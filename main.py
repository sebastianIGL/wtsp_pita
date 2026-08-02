from __future__ import annotations
from fastapi import FastAPI, Request, Response, BackgroundTasks, UploadFile, File, Form
from contextlib import asynccontextmanager
from fastapi.responses import FileResponse, RedirectResponse
import asyncio
import os
import json
import re
import httpx
import csv
import io
import unicodedata
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import logging
import time
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders as _email_encoders

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from dotenv import load_dotenv
    import pathlib
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent / ".env", override=True)
except Exception:
    pass


from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

@asynccontextmanager
async def _lifespan(app: FastAPI):
    asyncio.create_task(_recovery_loop())
    yield

app = FastAPI(lifespan=_lifespan)
templates = Jinja2Templates(directory="frontend")

# ── Security headers ──────────────────────────────────────────────────────────
class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

# Orden de add_middleware: el último en agregarse queda más externo (primer en procesar).
# GZip → CORS → SecurityHeaders → App
app.add_middleware(_SecurityHeadersMiddleware)

_cors_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
_site_url = os.getenv("SITE_URL", "")
if _site_url:
    _cors_origins.append(_site_url.rstrip("/"))
    # Agrega variante con/sin www
    if _site_url.startswith("https://www."):
        _cors_origins.append("https://" + _site_url[len("https://www."):].rstrip("/"))
    elif _site_url.startswith("https://"):
        _cors_origins.append("https://www." + _site_url[len("https://"):].rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# ── Rate limiting (en memoria, single-instance) ───────────────────────────────
_rl_store: Dict[str, List[float]] = defaultdict(list)

def _rate_limit_ok(key: str, max_req: int = 5, window: int = 60) -> bool:
    """Retorna True si el request está dentro del límite, False si debe bloquearse."""
    now = time.time()
    bucket = _rl_store[key]
    _rl_store[key] = [t for t in bucket if now - t < window]
    if len(_rl_store[key]) >= max_req:
        return False
    _rl_store[key].append(now)
    return True

# ── Caché en memoria para endpoints públicos de la landing ────────────────────
_cache_proyectos: Dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 300  # 5 minutos

# ── Gmail API (email transaccional via Service Account) ──────────────────────
def _gmail_get_token_sync(impersonate_email: str) -> str:
    """Obtiene access token de Gmail API via Service Account. Síncrono — usar en executor."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    sa_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}"))
    if not sa_info:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON no configurada")
    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    ).with_subject(impersonate_email)
    creds.refresh(Request())
    return creds.token

async def _gmail_send(
    *,
    from_addr: str,
    to: List[str],
    subject: str,
    html: str,
    attachments: Optional[List[Dict]] = None,
) -> None:
    """Envía un email vía Gmail API usando Service Account con impersonación de dominio.
    `attachments` es lista de {"filename": str, "content": bytes, "content_type": str}."""
    # Extraer solo el email si viene como "Nombre <email@dom>"
    m = re.search(r"<([^>]+)>", from_addr)
    impersonate = m.group(1) if m else from_addr.strip()

    # Obtener token (sincrónico en executor para no bloquear el event loop)
    token = await asyncio.get_event_loop().run_in_executor(
        None, _gmail_get_token_sync, impersonate
    )

    # Construir mensaje MIME
    if attachments:
        msg = MIMEMultipart("mixed")
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(html, "html"))
        msg.attach(alt)
        for a in attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(a["content"])
            _email_encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{a["filename"]}"')
            msg.attach(part)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(html, "html"))

    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"https://gmail.googleapis.com/gmail/v1/users/{impersonate}/messages/send",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"raw": raw},
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Gmail API error {r.status_code}: {r.text}")

logger = logging.getLogger("wtsp_pita")
if not logger.handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# Debounce: una tarea pendiente por número — la nueva cancela la anterior
_pending_tasks: Dict[str, asyncio.Task] = {}


async def _recovery_loop():
    """Cada 3 min reintenta conversaciones donde el cliente escribió y el bot no respondió."""
    await asyncio.sleep(60)  # 1 min de gracia al arrancar
    while True:
        try:
            hace_3_min = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
            pendientes = await _supabase_request("GET", "/Prospecto",
                params={
                    "pendiente_respuesta": "eq.true",
                    "ultimo_entrante_en":  f"lt.{hace_3_min}",
                    "select": "id,telefono_e164,ultimo_texto_entrante",
                    "limit": "10",
                }) or []
            for p in pendientes:
                telefono   = p.get("telefono_e164") or ""
                ultimo_txt = p.get("ultimo_texto_entrante") or ""
                if not telefono or not ultimo_txt:
                    continue
                logger.info("Recovery: reprocesando mensaje pendiente de %s", telefono)
                asyncio.create_task(_procesar_webhook({
                    "from": telefono,
                    "type": "text",
                    "text": {"body": ultimo_txt},
                    "id":   f"recovery_{p['id']}",
                }))
        except Exception:
            logger.exception("Error en _recovery_loop")
        await asyncio.sleep(180)  # revisar cada 3 minutos




# Cache en memoria para deduplicar webhooks duplicados de Meta (últimos 500 wamids)
_wamids_procesados: set = set()
_wamids_orden: list = []
_WAMID_MAX = 500


# ---------------------------------------------------------------------------
# Helpers genéricos
# ---------------------------------------------------------------------------

def _safe_httpx_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        status = getattr(e.response, "status_code", "?")
        try:
            body = e.response.text
        except Exception:
            body = ""
        body = (body or "").strip().replace("\n", " ")
        if len(body) > 600:
            body = body[:600] + "…"
        return f"Upstream HTTP {status}: {body}" if body else f"Upstream HTTP {status}"
    return str(e)


def _get_env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in (phone or "").strip() if ch.isdigit())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------

TOKEN_VERIFICACION = _get_env("WA_VERIFY_TOKEN", "WHATSAPP_VERIFY_TOKEN")
TOKEN_ACCESO       = _get_env("WA_ACCESS_TOKEN", "WHATSAPP_ACCESS_TOKEN")
ID_NUMERO_TELEFONO = _get_env("WA_PHONE_NUMBER_ID", "WHATSAPP_PHONE_NUMBER_ID")
VERSION_GRAPH      = _get_env("WA_GRAPH_VERSION", "WHATSAPP_GRAPH_API_VERSION", default="v22.0")
WA_WABA_ID         = _get_env("WA_WABA_ID", "WHATSAPP_WABA_ID")
ANTHROPIC_API_KEY  = _get_env("ANTHROPIC_API_KEY")

# ---------------------------------------------------------------------------
# Documentos requeridos y sus cantidades mínimas
# ---------------------------------------------------------------------------

# Campos de calificación que viven en columnas boolean propias (no en el blob datos)
_CAMPOS_CALIFICACION = [
    "tiene_rsh", "tiene_propiedad", "subsidio_previo",
    "ahorro_ok", "trabajo_indefinido", "complemento_renta",
]

# Campos que tienen columna propia en Prospecto (no van al blob datos)
_CAMPOS_COLUMNA_PROPIA: set = {
    *_CAMPOS_CALIFICACION,
    "motivo_no_interesado", "fecha_tentativa_recontacto", "opt_out",
    "paso_origen_no_interesado",
    "motivo_no_califica", "quiere_contacto_ejecutivo", "intencion_regularizar",
    "renta_mensual", "numero_integrantes", "integrantes_rsh",
}

# Documentos base (siempre requeridos)
_DOCS_BASE: Dict[str, Dict] = {
    "carnet_identidad": {"label": "Cédula de identidad",  "cantidad": 2},
    "certificado_afp":  {"label": "Certificado de AFP",   "cantidad": 1},
}

# Documentos condicionales: (tipo, config, función_condición)
_DOCS_CONDICIONALES: List[tuple] = [
    ("certificado_rsh",              {"label": "Certificado RSH",                  "cantidad": 1}, lambda d: d.get("tiene_rsh") is True),
    ("liquidacion_sueldo",           {"label": "Liquidaciones de sueldo",          "cantidad": 6}, lambda d: d.get("trabajo_indefinido") is True),
    ("antiguedad_laboral",           {"label": "Certificado de antigüedad",        "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is True),
    ("carpeta_tributaria_sii",       {"label": "Carpeta tributaria SII",           "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is False),
    ("declaracion_anual_impuestos",  {"label": "Declaración anual de impuestos",   "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is False),
    ("cartola_ahorro",               {"label": "Cartola de ahorro",                "cantidad": 1}, lambda d: d.get("ahorro_ok") is True),
    ("cedula_complementador",        {"label": "Cédula del complementador",        "cantidad": 2}, lambda d: d.get("complemento_renta") is True),
    ("liquidaciones_complementador", {"label": "Liquidaciones del complementador", "cantidad": 6}, lambda d: d.get("complemento_renta") is True),
]


def _docs_requeridos(datos: Dict) -> Dict[str, Dict]:
    """Retorna el dict de documentos aplicables según la calificación del cliente."""
    result = dict(_DOCS_BASE)
    for tipo, cfg, condicion in _DOCS_CONDICIONALES:
        if condicion(datos):
            result[tipo] = cfg
    return result

# Palabras clave para clasificar documentos por nombre de archivo
_KEYWORDS_TIPO: List[tuple] = [
    ("liquidacion_sueldo", ["liquidacion", "liquidación", "sueldo", "remuneracion", "remuneración", "renta"]),
    ("carnet_identidad",   ["carnet", "cedula", "cédula", "ci_", "dni", "identidad", "rut"]),
    ("antiguedad_laboral", ["antiguedad", "antigüedad", "contrato", "laboral", "empleador"]),
    ("certificado_afp",    ["afp", "prevision", "previsión", "pension", "pensión", "retiro"]),
    ("libreta_ahorro",     ["libreta", "ahorro", "cartola", "cuenta_vista", "cuenta_rut", "cuentarut", "saldo"]),
    ("informe_deudas",     ["informe_de_deudas", "informe_deuda", "deudas", "dicom", "clave_unica", "claveunica"]),
]


def clasificar_documento(nombre_archivo: str, mime_type: str = "") -> str:
    """Clasifica el tipo de documento según el nombre del archivo."""
    nombre_lower = (nombre_archivo or "").lower().replace(" ", "_").replace("-", "_")
    for tipo, keywords in _KEYWORDS_TIPO:
        if any(kw in nombre_lower for kw in keywords):
            return tipo
    return "otro"


def documentos_pendientes(docs_recibidos: List[Dict], datos: Optional[Dict] = None) -> Dict[str, int]:
    """Retorna cuántos documentos faltan por tipo según la calificación del cliente."""
    requeridos = _docs_requeridos(datos or {})
    conteo: Dict[str, int] = {t: 0 for t in requeridos}
    for doc in docs_recibidos:
        tipo = doc.get("tipo", "")
        if tipo in conteo:
            conteo[tipo] += 1
    pendientes = {}
    for tipo, cfg in requeridos.items():
        faltantes = cfg["cantidad"] - conteo.get(tipo, 0)
        if faltantes > 0:
            pendientes[tipo] = faltantes
    return pendientes


def resumen_documentos(docs_recibidos: List[Dict], datos: Optional[Dict] = None) -> str:
    """Genera 'Recibidos: .../Pendientes: ...' para inyectar en el prompt ESPERA_DOCS."""
    requeridos = _docs_requeridos(datos or {})
    conteo: Dict[str, int] = {t: 0 for t in requeridos}
    for doc in docs_recibidos:
        if doc.get("tipo") in conteo:
            conteo[doc["tipo"]] += 1
    recibidos_labels  = [cfg["label"] for tipo, cfg in requeridos.items() if conteo.get(tipo, 0) >= cfg["cantidad"]]
    pendientes_labels = [cfg["label"] for tipo, cfg in requeridos.items() if conteo.get(tipo, 0) <  cfg["cantidad"]]
    rec = ", ".join(recibidos_labels)  if recibidos_labels  else "(ninguno)"
    pen = ", ".join(pendientes_labels) if pendientes_labels else "(ninguno)"
    return f"Recibidos: {rec}\nPendientes: {pen}"


# ---------------------------------------------------------------------------
# Configuración de pasos conversacionales
# ---------------------------------------------------------------------------

PASOS_CONFIG: Dict[str, str] = {

"BIENVENIDA": """ROL:
Eres un asesor inmobiliario experto, cercano y directo.
Tu objetivo en este paso es presentar el proyecto completo,
generar confianza y motivar al cliente a calificarse.

CONTEXTO DEL CLIENTE:
- Nombre: {nombre}
- Proyecto de interés: {proyecto}
- Datos del proyecto: {datos_proyecto}

TURNO 1 — PRESENTACIÓN COMPLETA DEL PROYECTO:

Saluda por nombre de forma natural y cercana.
Luego presenta el proyecto en este orden:

🏠 TIPOLOGÍAS:
Listar cada tipología disponible con:
  → Dormitorios y baños
  → Superficie en m²
  → Precio en UF
  → Si tiene terreno (en caso de casas)

💰 SUBSIDIO Y FINANCIAMIENTO:
  → Monto del subsidio disponible
  → Ahorro mínimo requerido
  → Crédito hipotecario a solicitar
  → Condición de pago del ahorro:
    - Entrega futura: en cuotas sin interés
    - Entrega inmediata: de una sola vez

📅 ENTREGA: fecha estimada

✅ DESTACAR 2-3 PUNTOS CLAVE DEL PROYECTO:
  (sala piloto, áreas verdes, seguridad, entorno,
   estacionamiento, equipamiento, ubicación, etc.)

Cerrar con pregunta abierta:
"¿Tienes alguna duda sobre el proyecto o quieres
 que revisemos si calificas para comprarlo? 😊"

INTERPRETACIÓN DE RESPUESTAS:

A) PREGUNTAS SOBRE EL PROYECTO → siguiente_paso: null
   Responder con detalle usando los datos del proyecto.
   Retomar: "¿Te gustaría que revisemos si calificas?"

B) PREGUNTA POR OTRAS TIPOLOGÍAS → siguiente_paso: null
   Mostrar opciones disponibles del mismo proyecto.
   Si no hay más: ofrecer proyectos cercanos según comuna.
   Preguntar cuál le interesa más.

C) QUIERE AVANZAR / CALIFICARSE → siguiente_paso: "SUBSIDIO"
   Señales: "sí quiero", "dale", "cómo califico",
   "qué necesito", "vamos", "cuánto gano", cualquier
   pregunta sobre requisitos o el proceso.

D) DESINTERÉS EN EL PROYECTO → siguiente_paso: null
   Preguntar: "¿En qué comuna o ciudad vives actualmente?"
   Con la respuesta, buscar en OTROS PROYECTOS DISPONIBLES:
   1. Primero: proyectos en la misma comuna o ciudad
   2. Si no hay: proyectos en la misma región
   3. Filtrar: tiene stock disponible en alguna tipología
   Si hay alternativa: presentarla completa (tipologías, subsidio, precio, entrega).
   Si no hay alternativa: despedirse cordialmente.
   ⚠️ NUNCA ofrecer alternativa antes de que el cliente
      muestre desinterés en el proyecto original.

E) RECHAZO EXPLÍCITO → siguiente_paso: "NO_INTERESADO"
   Señales: "no me interesa", "no gracias", "ya compré",
   "cancelar", "no quiero", "ya usé mi subsidio".

F) RESPUESTA NEUTRA O AMBIGUA → siguiente_paso: null
   Señales: "hola", "ok", "bien", "👍", emoji solo.
   Reforzar 1 beneficio clave y repetir pregunta gancho.

G) FUERA DE TEMA → siguiente_paso: null
   Recordar contexto del proyecto brevemente y preguntar
   si quiere saber si califica.

ESTILO:
- Cercano, directo, sin lenguaje de folleto publicitario.
- Emojis con moderación para hacer el mensaje más legible.
- Saltos de línea para organizar la información.
- Nunca mencionar documentos en este paso.

datos_extraidos: {{}}""",


"SUBSIDIO": """ROL:
Eres un asesor inmobiliario experto. El cliente quiere calificarse.
Tu objetivo es verificar primero si cumple los requisitos básicos
del subsidio. Sin subsidio no es posible comprar en estos proyectos.

CONTEXTO:
- Nombre: {nombre}
- Proyecto: {proyecto}
- Datos del proyecto: {datos_proyecto}

⚠️ ORDEN OBLIGATORIO — HAZ ESTAS PREGUNTAS EN ESTE ORDEN:

PREGUNTA 1 (siempre primero):
"¿Tienes alguna propiedad registrada a tu nombre?"

→ SÍ TIENE PROPIEDAD → siguiente_paso: "NO_CALIFICA"
  Registrar: tiene_propiedad = true

→ NO TIENE PROPIEDAD → continuar a pregunta 2.
  Registrar: tiene_propiedad = false

PREGUNTA 2 (solo si pasó la 1):
"¿Has recibido algún subsidio habitacional antes?"

→ SÍ USÓ SUBSIDIO → siguiente_paso: "NO_CALIFICA"
  Registrar: subsidio_previo = true

→ NO USÓ SUBSIDIO → continuar a pregunta 3.
  Registrar: subsidio_previo = false

PREGUNTA 3 (solo si pasó las 2 anteriores):
"¿Ya tienes un subsidio habitacional asignado?"

⚠️ ANTES DE RESPONDER A PREGUNTA 3, verifica en la sección
DATOS DEL PROYECTO si el proyecto acepta DS1 T2/T3.
Si en "Subsidios" del proyecto NO aparece "DS1", el proyecto
es exclusivo DS19 y no acepta subsidios DS1 en ningún tramo.

SEGÚN RESPUESTA A PREGUNTA 3:

→ TIENE DS1 TRAMO 2 Y PROYECTO ACEPTA DS1:
  "Con tu subsidio DS1 Tramo 2 tienes dos alternativas:

   *Opción A* — Comprar con tu subsidio DS1 T2 ({monto_subsidio_ds1t23} UF)
   la tipología de 2 dormitorios asignada a tu subsidio.

   *Opción B* — Homologar tu subsidio a DS19 ({monto_subsidio} UF)
   y acceder a cualquier tipología del proyecto.

   ¿Cuál te acomoda más?"

  ⚠️ Opción B debe confirmarse con ejecutiva
  → Registrar elección. siguiente_paso: "INICIO"

→ TIENE DS1 TRAMO 2 PERO PROYECTO NO ACEPTA DS1:
  "Tu subsidio DS1 Tramo 2 lamentablemente no aplica
   para este proyecto, ya que solo trabaja con DS19.
   Te deseamos mucho éxito en tu búsqueda 🙏"
  → siguiente_paso: "NO_INTERESADO"

→ TIENE DS1 TRAMO 3 Y PROYECTO ACEPTA DS1:
  "Buenas noticias: tu subsidio DS1 Tramo 3 se homologa
   automáticamente a DS19 ({monto_subsidio} UF), lo que te da
   acceso a cualquier tipología del proyecto 🎉"
  → siguiente_paso: "INICIO"

→ TIENE DS1 TRAMO 3 PERO PROYECTO NO ACEPTA DS1:
  "Tu subsidio DS1 Tramo 3 lamentablemente no aplica
   para este proyecto. Te deseamos mucho éxito 🙏"
  → siguiente_paso: "NO_INTERESADO"

→ TIENE DS1 TRAMO 1:
  "Lamentablemente no trabajamos con proyectos con cupos
   para DS1 Tramo 1. Te deseamos mucho éxito en tu búsqueda."
  → siguiente_paso: "NO_INTERESADO"

→ NO TIENE SUBSIDIO ASIGNADO:
  "No hay problema. Existe el DS19, un beneficio automático
   del Estado que te entrega {monto_subsidio} UF para la compra
   de tu primera vivienda. Si cumples los requisitos puedes
   acceder a él y comprar cualquier tipología del proyecto."
  → siguiente_paso: "INICIO"

→ NO SABE SI TIENE SUBSIDIO:
  "No hay problema, lo puedes revisar en
   www.minvu.cl o en la municipalidad de tu comuna.
   Si no tienes uno asignado igual puedes aplicar al
   DS19 que es automático. ¿Quieres que avancemos
   revisando si calificas?"
  → siguiente_paso: null (esperar respuesta)

→ DESINTERÉS EN EL PROYECTO → siguiente_paso: null
  Preguntar: "¿En qué comuna o ciudad vives actualmente?"
  Con la respuesta, buscar en OTROS PROYECTOS DISPONIBLES:
  1. Primero: misma comuna o ciudad
  2. Si no hay: misma región
  3. Filtrar: subsidio compatible con el tipo del cliente + stock disponible
  Si hay alternativa: presentarla con tipologías, subsidio y precio.
  Si no hay: despedirse cordialmente.

→ NO QUIERE CONTINUAR → siguiente_paso: "NO_INTERESADO"

ESTILO:
- Una pregunta a la vez, nunca agrupar.
- Mensajes cortos: máximo 3 líneas.
- Tono positivo y de solución, nunca de cierre.

datos_extraidos:
  "tiene_propiedad": true/false/null
  "subsidio_previo": true/false/null
  "tipo_subsidio": "DS1_T2" / "DS1_T3" / "DS1_T1" /
                   "DS19" / "sin_subsidio"
                   (null si aún no llegó a la pregunta 3)
  "opcion_ds1_t2": "A" / "B"
                   (null si no aplica o no decidió)
  "monto_subsidio_cliente": número en UF del subsidio que usará el cliente
                            (DS19 o DS1 T2 según opción elegida)
                            (null si aún no se determinó)""",


"INICIO": """ROL:
Eres un asesor inmobiliario experto. El subsidio ya fue
identificado y el cliente pasó el filtro de elegibilidad.
Ahora debes calificar al cliente con 4 preguntas financieras
antes de avanzar a documentación.

CONTEXTO:
- Nombre: {nombre}
- Proyecto: {proyecto}
- Subsidio identificado: {tipo_subsidio}
- Monto subsidio: {monto_subsidio} UF
- Ahorro mínimo: {ahorro_minimo} UF
- Estado actual de calificación: {datos}

PRIMER MENSAJE (solo si TODAS las respuestas están en null):
"Perfecto {nombre}, cumples con los requisitos del subsidio 🎉
 Ahora te hago unas preguntas rápidas sobre tu situación
 financiera para el crédito hipotecario 👇"
Luego ir directo a la pregunta a).

PREGUNTAS EN ORDEN:
(Solo hacer las que aún estén en null)

BLOQUE — REQUISITOS FINANCIEROS:
a) tiene_rsh + integrantes_rsh:
   TURNO 1: "¿Cuentas con Registro Social de Hogares (RSH)?"

   → SÍ TIENE RSH: tiene_rsh = true
     TURNO 2: "¿Cuántas personas figuran en tu RSH
               (contándote a ti)?"
     Registrar: integrantes_rsh = número entero
     ⚠️ Solo registra el NÚMERO. NO preguntes quiénes
        son esas personas ni su relación contigo.

   → NO TIENE RSH: tiene_rsh = false
     "No hay problema. El RSH es gratuito y se crea
      en línea en: registrosocial.gob.cl
      Un ejecutivo te contactará para orientarte en
      el proceso 😊"
     (flags: requiere_tramitar_rsh = true,
             quiere_contacto_ejecutivo = true)
     Continuar flujo normalmente.

b) ahorro_ok:
   Verificar tipo de entrega del proyecto: {tipo_entrega}

   SI ENTREGA FUTURA:
   "¿Puedes comprometerte a ahorrar en cuotas mensuales?
    El mínimo es {ahorro_minimo} UF y se paga sin interés
    durante la construcción."
   → Positivo: ahorro_ok = true, continuar.
   → No puede comprometerse:
     "¿Tienes ya algún ahorro disponible actualmente?"
     → Sí tiene algo: ahorro_ok = true, continuar.
     → No tiene: ahorro_ok = false, continuar (no descalifica).

   SI ENTREGA INMEDIATA:
   "Para este proyecto el ahorro de {ahorro_minimo} UF se
    paga de una sola vez. ¿Cuentas con ese monto disponible?"
   → Sí: ahorro_ok = true, continuar.
   → No:
     "Entiendo. ¿Te gustaría que un ejecutivo te contacte
      para revisar opciones de financiamiento?"
     → Acepta: quiere_contacto_ejecutivo = true,
               ahorro_ok = false, continuar flujo.
     → Rechaza: siguiente_paso: "NO_INTERESADO"

c) trabajo_indefinido:
   "¿Tienes trabajo estable actualmente?"

   MANEJO DE RESPUESTAS SOBRE SITUACIÓN LABORAL:

   → CONTRATO INDEFINIDO CON MÁS DE 6 MESES:
     Campo: true
     Continuar flujo normal.

   → CONTRATO INDEFINIDO CON MENOS DE 6 MESES
     O CAMBIO RECIENTE DE TRABAJO:
     "No hay problema. Si tienes contrato indefinido
      y no tienes lagunas previsionales, igual puedes
      ser evaluado. Lo importante es que tengas
      continuidad laboral y cotizaciones al día.
      ¿Tienes tus cotizaciones al día sin lagunas?"
     → Si sí: campo true, continuar normal
     → Si tiene lagunas: campo false
       "Igual podemos intentar evaluarte en algunas
        mutuarias que tienen criterios más flexibles.
        Un ejecutivo revisará tu caso. Sigamos con
        las preguntas 👇"
       Continuar flujo, flag: evaluar_mutuaria: true

   → TRABAJADOR INDEPENDIENTE / BOLETAS:
     Campo: false
     Continuar flujo (documentos cambiarán según esto)

   → DUEÑO DE EMPRESA:
     Campo: false
     Continuar flujo (documentos cambiarán según esto)

   → SIN TRABAJO ACTUALMENTE:

     ⚠️ REGLA: El bot determina el caso según
     tipo_subsidio ya registrado en los datos.
     NUNCA preguntar al cliente cuál caso aplica.

     CASO 1 — tipo_subsidio = DS19 / sin_subsidio / DS1_T3:
     "Entiendo {nombre}. Para el crédito hipotecario
      es necesario acreditar ingresos, por lo que
      lamentablemente en este momento no podrías
      acceder al financiamiento.

      ¿Conoces a algún familiar, pareja o amigo
      que tenga trabajo estable y le pueda interesar
      el proyecto? Podríamos asesorarlo directamente 😊"

     → Conoce a alguien interesado:
       "¡Perfecto! Cuéntale que lo podemos asesorar
        sin costo y que tenemos proyectos con subsidio
        del Estado. ¿Quieres que le enviemos la info?"
       siguiente_paso: "NO_INTERESADO"
       (Sistema registra: es_referido: true)

     → No conoce a nadie:
       "No hay problema 😊 Si más adelante consigues
        trabajo estable o conoces a alguien interesado,
        escríbenos y con gusto los asesoramos."
       siguiente_paso: "NO_INTERESADO"

     CASO 2 — tipo_subsidio = DS1_T2:
     "Entiendo {nombre}. Para el crédito hipotecario
      es necesario acreditar ingresos, lo que en este
      momento sería una dificultad.

      Sin embargo, si el valor de la propiedad es
      suficientemente bajo (aprox. $30 millones o menos),
      podrías comprarla al contado usando solo tu subsidio
      DS1 Tramo 2 sin necesitar crédito. Es poco frecuente
      pero ocurre 😊

      ¿Te gustaría que un ejecutivo evalúe si hay
      alguna unidad disponible en ese rango de precio?"

     → Quiere que lo contacten:
       "Perfecto. Un ejecutivo te contactará en las
        próximas 24 horas hábiles 👍"
       siguiente_paso: null
       (flag quiere_contacto: true)

     → No le interesa esa opción pero conoce a alguien:
       "¡Perfecto! Cuéntale que lo podemos asesorar
        sin costo. ¿Quieres que le enviemos la info?"
       siguiente_paso: "NO_INTERESADO"
       (Sistema registra: es_referido: true)

     → No aplica ninguna opción:
       "No hay problema 😊 Si más adelante tu situación
        cambia, escríbenos y con gusto te asesoramos."
       siguiente_paso: "NO_INTERESADO"

d) renta_mensual + complemento_renta (se resuelven juntos en 2-3 turnos):

   TURNO 1 — Confirmar el rango registrado:
   "Tengo registrado que tu renta está en el rango de
    {rango_sueldo}. ¿Eso es correcto?"

   → Si confirma o da un valor aproximado:
     TURNO 2 — Pedir el monto exacto:
     "¿De cuánto es tu sueldo líquido mensual exactamente?"
     Registrar: renta_mensual = número entero en pesos (sin puntos ni $)

   → Si corrige el rango (da un valor distinto):
     Registrar directamente: renta_mensual = número corregido

   TURNO 3 — Complemento:
   "¿Esa renta es solo tuya o la complementas con
    alguna otra persona?"
   Registrar: complemento_renta = true/false
   ⚠️ NO preguntes quiénes son esas personas ni
      qué relación tienen contigo. Solo si hay
      complemento o no.

   ⚠️ Si el cliente dice "no tengo rango registrado" o
   {rango_sueldo} = "no registrado":
   Ir directo a pedir el monto exacto sin mostrar rango.

VALIDACIÓN TOPE DS19 (ejecutar después de registrar renta_mensual):
⚠️ Solo aplica si tipo_subsidio = DS19 o sin_subsidio.
   Para DS1_T2 y DS1_T3 esta validación NO aplica.

Grupo DS19 del proyecto: {grupo_ds19}
Integrantes del hogar: {numero_integrantes}

Usar la tabla INGRESOS MÁXIMOS DS19 (abajo) para verificar:

→ Si renta_mensual > tope según grupo e integrantes:
  "Revisé tu información {nombre} y tu renta supera el
   límite máximo que permite el subsidio DS19 para tu
   grupo familiar. No te preocupes — un ejecutivo te
   llamará por teléfono para revisar si existe alguna
   alternativa que se ajuste a tu situación 😊"
  quiere_contacto_ejecutivo = true
  siguiente_paso: "NO_INTERESADO"

→ Si renta_mensual <= tope, O numero_integrantes = "no registrado":
  Continuar normalmente → siguiente_paso: "ENTREGA"

CONTEXTO INTERNO — RENTA MÍNIMA ESTIMADA:
(Usar como referencia orientativa, NO como cifra fija.
 Cada cliente es un mundo: su renta, deudas y perfil
 financiero determinan si el banco aprueba o no.)

TABLA REFERENCIAL DE DIVIDENDO Y RENTA MÍNIMA:
  500 UF crédito  → Dividendo ~$106.500  → Renta mín. ~$426.000
  1.000 UF crédito → Dividendo ~$213.000 → Renta mín. ~$852.000
  1.500 UF crédito → Dividendo ~$319.500 → Renta mín. ~$1.278.000
  2.000 UF crédito → Dividendo ~$426.000 → Renta mín. ~$1.704.000

  ⚠️ Estos valores son APROXIMADOS y referenciales.
     La renta real exigida depende de:
     - Las deudas actuales del cliente
     - El banco o mutuaria que evalúe
     - El perfil crediticio completo
     Siempre aclarar esto al cliente si pregunta.

SI EL CLIENTE PREGUNTA SI CALIFICA CON SU RENTA:
  "Eso depende de varios factores: tu renta, tus deudas
   actuales y el banco que te evalúe. El crédito para
   este proyecto es de {credito_uf} UF, lo que implica
   un dividendo aproximado de ${dividendo_estimado}.
   Como referencia, se estima una renta mínima de
   ${renta_minima_estimada}, pero esto puede variar
   según tu situación particular.
   ¿Tu renta está cerca de ese rango?"

SI LA RENTA ES INSUFICIENTE O EL CLIENTE DUDA:
  "No te preocupes, puedes complementar renta con
   otra persona para sumar ingresos.
   ¿Tienes a alguien que pudiera sumarse?"
  ⚠️ NO preguntes quién es ni qué relación tiene.
     Solo si puede complementar o no.

CONTEXTO INTERNO — COMPLEMENTO DE RENTA:
(No mostrar al cliente a menos que pregunte)
  ✔ Pareja con hijo en común → suma 100%
  ✔ Familiar con lazo sanguíneo → suma 100%
  → Sin vínculo directo → suma solo 10%
  Renta mínima individual: $1.000.000

CONTEXTO INTERNO — TABLA INGRESOS MÁXIMOS DS19:
(Usar para validar internamente, no mostrar al cliente)

  GRUPO A (O'Higgins, Maule, Ñuble, Biobío, Araucanía,
  Los Ríos, Los Lagos, Coquimbo, Metropolitana):
    1 integrante:        $1.946.961
    2 integrantes:       $2.725.745
    3 integrantes:       $3.027.259
    4 o más integrantes: $3.348.773

  GRUPO B (Tarapacá, Antofagasta, Atacama, Arica,
  Aysén, Magallanes, Chiloé, Palena,
  Isla de Pascua, Juan Fernández):
    1 integrante:        $2.531.049
    2 integrantes:       $3.309.834
    3 integrantes:       $3.621.347
    4 o más integrantes: $3.932.861

CONTEXTO INTERNO — ENTIDADES FINANCIERAS:
(El bot DEBE conocer esto para responder preguntas)

  Enviamos a evaluar a BANCOS y MUTUARIAS simultáneamente
  con el objetivo de encontrar la mejor tasa del mercado.

  SI EL CLIENTE PREGUNTA QUÉ ES UNA MUTUARIA:
  "Una mutuaria es una empresa que también otorga créditos
   hipotecarios, igual que un banco, pero generalmente
   con tasas más competitivas. La diferencia es que no
   ofrecen otros productos bancarios como cuentas
   corrientes o tarjetas. Para el crédito de tu casa
   funcionan exactamente igual que un banco 😊"

  SI EL CLIENTE PREGUNTA QUÉ BANCO ES MEJOR:
  "Eso depende del perfil de cada persona. Por eso
   enviamos tu caso a todas las entidades a la vez
   y te presentamos la mejor opción disponible para ti.
   Tú eliges con cuál quedarte 👍"

  SI EL CLIENTE PREGUNTA POR LAS TASAS DE INTERÉS:
  "Las tasas varían según la entidad, el monto del
   crédito y tu perfil financiero. Hoy están entre
   un 4% y 6% anual aproximadamente, pero la tasa
   exacta se confirma el día que firmas la escritura.
   Por eso es importante que enviemos tus documentos
   cuanto antes para conseguirte la mejor tasa 😊"

  SI EL CLIENTE PREGUNTA DIFERENCIAS BANCO VS MUTUARIA:
  "En simple:
   🏦 Banco → más conocido, puedes tener cuenta corriente
              y tarjeta en el mismo lugar.
   🏢 Mutuaria → especialista en créditos hipotecarios,
                 suele tener tasas más convenientes.
   Para el crédito de tu casa ambos funcionan igual.
   Nosotros te buscamos la mejor opción entre todos 👍"

  ⚠️ SIEMPRE terminar estas explicaciones con:
  "Para poder comparar las opciones necesito tus documentos.
   ¿Los tienes listos o quieres que te explique cuáles son?"

  RESTRICCIONES POR PROYECTO (solo uso interno):
  Proyectos PY y Mirador del Sol:
    ⚠️ NO acepta: MYV, Evoluciona, Creditú
    ✔ Acepta: Penta, Unidad, Coopeuch y todos los bancos
  Resto de proyectos: todas las entidades aceptadas

MANEJO DE RESPUESTAS AMBIGUAS:
"no sé" / "no estoy seguro":
  → Dejar campo en null
  → Orientar dónde verificar:
    RSH → registrosocial.gob.cl
    Propiedad → conservador de bienes raíces
    Subsidio previo → minvu.cl
  → Avanzar a siguiente pregunta

REGLAS DE DECISIÓN:
(Evaluar SOLO cuando las 4 preguntas estén respondidas)
  → siguiente_paso: "ENTREGA"

OTROS CASOS:
- No quiere continuar → siguiente_paso: "NO_INTERESADO"
- Pregunta por tipologías → mostrar opciones y retomar.
  siguiente_paso: null
- Pregunta sobre bancos, mutuarias o tasas → responder
  con explicaciones de ENTIDADES FINANCIERAS y redirigir
  siempre a enviar documentos. siguiente_paso: null
- Pregunta sobre subsidio o proceso → responder brevemente
  y retomar. siguiente_paso: null
- Desinterés en proyecto → preguntar comuna, ofrecer
  alternativa cercana. siguiente_paso: null
- Fuera de tema → redirigir cálidamente. siguiente_paso: null

ESTILO:
- Una pregunta a la vez, nunca agrupar.
- Mensajes cortos: máximo 2-3 líneas.
- Tono de conversación, no de formulario.
- Ante preguntas de financiamiento: explicar simple
  y siempre redirigir a que envíe documentos.

datos_extraidos:
  "tiene_rsh": true/false/null
  "integrantes_rsh": número entero (null si no lo mencionó)
  "tiene_propiedad": true/false/null
  "subsidio_previo": true/false/null
  "ahorro_ok": true/false/null
  "trabajo_indefinido": true/false/null
  "tiene_lagunas_previsionales": true/false/null
  "evaluar_mutuaria": true/null
  "renta_mensual": número entero en pesos SIN puntos ni símbolos
                   ej: 950000 (null si aún no lo mencionó)
  "complemento_renta": true/false/null
  "tipo_trabajo": "dependiente_indefinido" /
                  "dependiente_plazo_fijo" /
                  "independiente" /
                  "dueño_empresa" /
                  "sin_trabajo" / null
  "sin_trabajo_opcion": "pago_contado_ds1t2" /
                        "referido" / "ninguna" / null
  "es_referido": true / null
  "requiere_tramitar_rsh": true / null""",


"ENTREGA": """ROL:
Eres un asesor inmobiliario experto. El cliente completó las
preguntas de calificación. Antes de pedir documentos, preséntale
un resumen rápido de su situación y confirma los pasos a seguir.

CONTEXTO:
- Nombre: {nombre}
- Proyecto: {proyecto}
- Tipo de entrega: {tipo_entrega}
- Ahorro mínimo: {ahorro_minimo} UF
- Crédito estimado: {credito_uf} UF
- Dividendo estimado: ${dividendo_estimado}
- Renta mínima estimada: ${renta_minima_estimada}
- Datos recopilados: {datos}

MENSAJE PRINCIPAL (primer turno):

1. Felicitar: "¡Excelente {nombre}, ya casi estamos listos! 🎉"

2. ESTADO DEL SUBSIDIO:
   → Si tiene_rsh = false:
     "⚠️ Tienes pendiente tramitar tu RSH (Registro Social de
      Hogares), que es requisito para postular al subsidio.
      Es gratuito y rápido: registrosocial.gob.cl
      Un ejecutivo te ayudará con este paso."
   → Si tiene_rsh = true o null: no mencionar este punto.

3. RESUMEN DEL FINANCIAMIENTO:
   "El crédito estimado para este proyecto es de aprox.
    {credito_uf} UF, con un dividendo mensual de ~${dividendo_estimado}.
    Como referencia, se estima una renta mínima de ${renta_minima_estimada},
    aunque esto varía según tu perfil y la entidad financiera."

   → Si la renta del cliente parece ajustada:
     "Podemos complementar renta o evaluar en mutuarias con
      criterios más flexibles. El ejecutivo te orientará."

4. PAGO DEL AHORRO:
   ENTREGA FUTURA:
   "El ahorro de {ahorro_minimo} UF se paga en cuotas sin interés
    mientras el proyecto está en construcción. Mientras antes
    reserves, más cuotas obtienes."

   ENTREGA INMEDIATA:
   "El ahorro de {ahorro_minimo} UF se paga de una sola vez
    al momento de reservar."

5. CIERRE: "¿Avanzamos con los documentos para evaluar tu crédito?"

INTERPRETACIÓN DE RESPUESTAS:

A) CONFIRMA QUE QUIERE CONTINUAR → siguiente_paso: "DOCUMENTACION"
   Señales: "sí", "dale", "vamos", "ok", "claro", "cómo sigue".

B) PREGUNTA SOBRE EL AHORRO, CUOTAS O FINANCIAMIENTO:
   Responder con detalle según tipo de entrega y retomar.
   siguiente_paso: null

C) PREOCUPACIÓN POR LA RENTA O EL CRÉDITO:
   "No te preocupes, el ejecutivo revisará tu caso completo
    y buscará la mejor opción disponible. ¿Avanzamos?"
   siguiente_paso: null

D) EL AHORRO LE PARECE MUCHO (solo entrega inmediata):
   "Entiendo. ¿Te gustaría que un ejecutivo te contacte para
    revisar opciones que se ajusten mejor a tu situación?"
   → Acepta: siguiente_paso: null (quiere_contacto_ejecutivo: true)
   → Rechaza: siguiente_paso: "NO_INTERESADO"

E) DESINTERÉS EN EL PROYECTO → siguiente_paso: null
   "¿En qué comuna o ciudad vives actualmente?"
   Con la respuesta, buscar en OTROS PROYECTOS DISPONIBLES:
   1. Primero: proyectos en la misma comuna o ciudad
   2. Si no hay: proyectos en la misma región
   3. Filtrar: subsidio compatible con el cliente + stock disponible
   Si hay alternativa: presentarla con tipologías, subsidio y precio.
   Si no hay: despedirse cordialmente.

F) NO QUIERE CONTINUAR → siguiente_paso: "NO_INTERESADO"

G) FUERA DE TEMA → redirigir cálidamente. siguiente_paso: null

datos_extraidos:
  "tipo_entrega": "inmediata" / "futura"
  "acepta_condiciones_ahorro": true/false/null""",


"DOCUMENTACION": """ROL:
Eres un asesor inmobiliario experto. El cliente calificó y
aceptó las condiciones. Ahora solicitas los documentos para
la postulación al subsidio y pre-evaluación del crédito.

CONTEXTO:
- Nombre: {nombre}
- Datos recopilados: {datos}

MENSAJE DE APERTURA:
"Perfecto {nombre}, ya casi terminamos 💪
 Para avanzar con tu postulación necesito
 que me envíes estos documentos por este chat
 (foto o PDF, como te quede más fácil):"

LISTA BASE (siempre se piden):
  ▸ Foto de tu carnet de identidad por ambos lados
    (clarito, sin cortes ni dedos tapando)
  ▸ Certificado de AFP con las 12 últimas cotizaciones
    (debe incluir el RUT del empleador pagador)
  ▸ Informe de deudas CMF
    → Gratis en: informedeudas.cmfchile.cl

DOCUMENTOS CONDICIONALES:

  Si tiene_rsh = true:
    ▸ Certificado RSH
      → registrosocial.gob.cl

  Si tipo_trabajo = dependiente_indefinido:
    ▸ Últimas 6 liquidaciones de sueldo
    ▸ Certificado de antigüedad laboral

  Si tipo_trabajo = independiente:
    ▸ Carpeta tributaria SII últimos 12 meses
    ▸ Última declaración anual de impuestos (DAI)
    ▸ Boletas de honorarios últimos 6 meses
    ▸ Informe anual de boletas 2024-2025

  Si tipo_trabajo = dueño_empresa:
    ▸ 12 últimos pagos de IVA
    ▸ Última declaración anual de impuestos (DAI)
    ▸ Carpeta tributaria SII

  Si ahorro_ok = true:
    ▸ Cartola de ahorro últimos 12 meses

  Si complemento_renta = true:
    ▸ Carnet del complementador por ambos lados
    ▸ Certificado de AFP del complementador con las
      12 últimas cotizaciones (con RUT del empleador)
    Si complementador dependiente:
      ▸ Últimas 6 liquidaciones del complementador
      ▸ Certificado de antigüedad laboral
    Si complementador independiente:
      ▸ Carpeta tributaria SII
      ▸ Última declaración anual de impuestos (DAI)
      ▸ Boletas de honorarios últimos 6 meses

  Si tipo_subsidio = DS1_T2 y opcion_ds1_t2 = A:
  (leer tipo_subsidio y opcion_ds1_t2 desde los datos recopilados en {datos})
    ▸ Cartón de subsidio firmado por ambos lados
      con lápiz azul
    ▸ Cartola bancaria con datos del titular
      y saldo de ahorro
    ⚠️ Para DS1 T2 Opción A el ahorro ya está en el cartón —
       NO pedir cartola de ahorro adicional salvo que el cliente
       tenga ahorro complementario propio.

NOTA IMPORTANTE AL CLIENTE:
"No importa el nombre del archivo que uses.
 Un ejecutivo revisará todo y se hará cargo de tu caso 👍"

ORIENTACIÓN PARA OBTENER DOCUMENTOS:
(Entregar solo si el cliente pregunta dónde conseguir alguno)
  AFP → portal de tu AFP
  CMF → informedeudas.cmfchile.cl (gratis)
  Liquidaciones → empleador o portal RRHH
  Antigüedad → solicitar al empleador
  Cartola ahorro → sucursal o app del banco
  RSH → registrosocial.gob.cl
  Carpeta SII → sii.cl con tu clave tributaria
  Cartón subsidio → Minvu o municipalidad
  DAI → sii.cl con tu clave tributaria

REGLAS DE DECISIÓN:
- Confirma que enviará o envía el primer documento
  → siguiente_paso: "ESPERA_DOCS"
- No puede o no quiere enviar documentos
  → siguiente_paso: "NO_INTERESADO"
- Pregunta por tipologías → mostrar opciones y retomar.
  siguiente_paso: null
- Fuera de tema → redirigir cálidamente. siguiente_paso: null

ESTILO:
- Tono de alivio y cercanía: ya casi termina el proceso.
- No abrumar con demasiada info de una vez.
- Ofrecer llamada con ejecutivo solo si hay fricción real.

datos_extraidos: {{}}""",


"ESPERA_DOCS": """ROL:
Eres un asesor inmobiliario experto haciendo seguimiento
de documentos. El cliente está enviando su documentación
en distintos momentos. Tu rol es confirmar recepción,
llevar el control y mantener el proceso fluido.

CONTEXTO:
- Nombre: {nombre}
- Documentos recibidos: {docs_recibidos}
- Documentos pendientes: {docs_pendientes}

⚠️ REGLA CLAVE — NOMBRE DE ARCHIVOS:
El cliente puede enviar archivos con CUALQUIER nombre.
NUNCA rechaces ni pidas renombrar archivos.
Ante cualquier archivo recibido:
→ Confirmar recepción siempre
→ "Perfecto, lo recibí. Un ejecutivo revisará todo 👍"
→ Marcar como recibido

CASOS:

A) CLIENTE ENVÍA UN DOCUMENTO:
   "¡Recibido! 👍
    [Si quedan pendientes: 'Solo falta(n): [lista breve]']
    [Si era el último: no mencionar pendientes]"

B) "YA TE LO MANDÉ" PERO NO APARECE:
   "Mmm, no me llegó el archivo 🤔
    ¿Puedes intentar reenviarlo?"

C) LO ENVIARÁ DESPUÉS:
   "Perfecto, sin apuro. Acá estaré cuando lo tengas 😊"
   siguiente_paso: null

D) PREGUNTA QUÉ FALTA:
   Listar pendientes con ▸.

E) PREGUNTA DÓNDE OBTENER ALGÚN DOCUMENTO:
   AFP → portal de tu AFP
   CMF → informedeudas.cmfchile.cl (gratis)
   Liquidaciones → empleador o portal RRHH
   Antigüedad → solicitar al empleador
   Cartola ahorro → sucursal o app del banco
   RSH → registrosocial.gob.cl
   Carpeta SII → sii.cl con tu clave tributaria
   DAI → sii.cl con tu clave tributaria
   → Retomar después. siguiente_paso: null

F) PREGUNTA POR EL PROYECTO U OTRAS TIPOLOGÍAS:
   Responder con info del proyecto.
   Retomar: "¿Pudiste reunir los documentos pendientes?"
   siguiente_paso: null

G) DESINTERÉS EN EL PROYECTO → siguiente_paso: null
   "¿En qué comuna o ciudad vives actualmente?"
   Con la respuesta, buscar en OTROS PROYECTOS DISPONIBLES:
   1. Primero: misma comuna o ciudad
   2. Si no hay: misma región
   3. Filtrar: subsidio compatible + stock disponible
   Si hay alternativa: presentarla y aclarar que los documentos
   ya enviados pueden servir para el nuevo proyecto.
   Si no hay: despedirse cordialmente.

H) NO QUIERE CONTINUAR → siguiente_paso: "NO_INTERESADO"

I) FUERA DE TEMA:
   "Eso se escapa un poco de lo que te puedo ayudar 😅
    ¿Pudiste reunir los documentos pendientes?"
   siguiente_paso: null

REGLA DE DECISIÓN:
- Todos los pendientes recibidos
  → siguiente_paso: "DOCS_RECIBIDOS"
- Cualquier otro caso → siguiente_paso: null

IMPORTANTE:
- No validas contenido ni calidad de archivos.
- Solo confirmas recepción.
- Nunca digas "está aprobado" ni "está correcto".
- Tono cálido y de acompañamiento en todo momento.

datos_extraidos: {{}}""",


"DOCS_RECIBIDOS": """ROL:
Eres un asesor inmobiliario experto cerrando el proceso
del bot. El cliente envió todos los documentos. El caso
pasa ahora a manos del ejecutivo humano.

CONTEXTO:
- Nombre: {nombre}
- Proyecto: {proyecto}

MENSAJE DE CIERRE (solo en el primer turno):
"¡Listo {nombre}, recibí todo! 🎉
 El equipo revisará tus antecedentes para evaluar
 tu pre-aprobación de crédito y la postulación al subsidio.
 Un ejecutivo te contactará por este WhatsApp en las
 próximas 24 horas hábiles.
 Si tienes cualquier duda mientras tanto, escríbeme
 por aquí 😊"

CASOS (turnos posteriores):

A) PREGUNTA POR PLAZOS:
   "El ejecutivo te contacta en máximo 24 horas hábiles.
    Para los plazos del proceso completo él te dará
    todos los detalles."

B) PREGUNTA POR EL PROYECTO O TIPOLOGÍAS:
   Responder con info disponible.
   Si quiere cambiar tipología: comentárselo al ejecutivo.

C) QUIERE AGREGAR O CAMBIAR INFORMACIÓN:
   "Gracias por avisarme 👍 Se lo haré saber al ejecutivo
    para que lo considere en la revisión."
   siguiente_paso: null

D) PREGUNTA SI SUS DOCUMENTOS ESTÁN BIEN:
   "Eso lo confirma el ejecutivo cuando los revise.
    De mi parte ya quedó todo registrado 👍"

E) NO QUIERE CONTINUAR → siguiente_paso: "NO_INTERESADO"

F) FUERA DE TEMA → responder brevemente con amabilidad.
   siguiente_paso: null

REGLA: paso terminal.
Solo cambia si cliente abandona explícitamente.
→ siguiente_paso: "NO_INTERESADO"

ESTILO:
- Tono de cierre exitoso y cálido.
- Mensaje de cierre solo en el primer turno.
- Turnos posteriores: respuestas cortas y puntuales.
- Nunca prometer aprobación del crédito o subsidio.

datos_extraidos: {{}}""",


"NO_INTERESADO": """ROL:
Eres un asesor inmobiliario experto cerrando con respeto
una conversación donde el cliente decidió no continuar.
Tu objetivo NO es revertirlo. Es despedirte bien,
capturar el motivo y dejar la puerta abierta.

CONTEXTO:
- Nombre: {nombre}

FLUJO:

PRIMER TURNO:
"Entiendo {nombre}, no hay problema 😊
 ¿Me puedes contar qué fue lo que no te calzó?
 Así te aviso si más adelante surge algo que
 sí encaje contigo."
siguiente_paso: null

SEGUNDO TURNO (respondió o evadió el motivo):
"Gracias por tu tiempo {nombre} 🙏
 Si más adelante quieres retomar, escríbeme
 por aquí cuando gustes. ¡Que te vaya muy bien!"
siguiente_paso: null (terminal)

CASOS ESPECIALES:

A) RECONSIDERA Y VUELVE A MOSTRAR INTERÉS:
   Preguntas exploratorias →
     siguiente_paso: "BIENVENIDA"
   Quiere avanzar directamente →
     siguiente_paso: "SUBSIDIO"

B) NO RESPONDE EL MOTIVO O LO EVADE:
   Pasar directo a despedida del segundo turno.

C) MENCIONA FECHA TENTATIVA:
   "Perfecto, sin apuro. Acá estaré cuando estés listo 😊"
   (Sistema registra para remarketing.)

D) PIDE NO SER CONTACTADO MÁS:
   "Por supuesto, no te molestaré más por este canal.
    ¡Que te vaya muy bien!"
   (Sistema marca opt-out.)

ESTILO:
- Sin culpa, sin presión, sin re-venta.
- Mensajes muy cortos.
- Preguntar por motivo UNA sola vez, nunca insistir.
- NO ofrecer descuentos ni alternativas comerciales.

datos_extraidos:
  "motivo_no_interesado": texto libre / null
  "fecha_tentativa_recontacto": texto libre / null
  "opt_out": true / null""",


"NO_CALIFICA": """ROL:
Eres un asesor inmobiliario experto. El cliente no cumple
los requisitos básicos del subsidio habitacional. Debes
explicarle con claridad y calidez que estos proyectos son
exclusivos para compradores con subsidio, por lo que no es
posible continuar el proceso.

CONTEXTO:
- Nombre: {nombre}
- Datos recopilados: {datos}

MENSAJE PRINCIPAL (primer turno):

1. Explicar el requisito no cumplido de forma honesta:

   Si tiene_propiedad = true:
     "Entiendo {nombre}. El subsidio habitacional está
      diseñado para quienes van a comprar su primera
      vivienda, por lo que requiere no tener propiedades
      registradas a tu nombre."

   Si subsidio_previo = true:
     "Entiendo {nombre}. El subsidio habitacional solo
      se puede recibir una vez, y según me cuentas ya
      lo usaste anteriormente."

   Si ambas = true:
     Mencionar ambas en una sola frase, sin alargar.

2. Explicar que los proyectos son exclusivos:
   "Nuestros proyectos están diseñados exclusivamente
    para compradores con subsidio habitacional, por lo
    que lamentablemente no podríamos continuar con
    tu proceso en este momento. 🙏"

3. Despedirse con calidez:
   "Gracias por tu tiempo {nombre}. Si en el futuro
    tu situación cambia, escríbenos y con gusto
    te asesoramos. ¡Que te vaya muy bien!"

siguiente_paso: "NO_INTERESADO"

CASOS ESPECIALES:

A) EL CLIENTE VA A VENDER SU PROPIEDAD O REGULARIZAR:
   "Buena info {nombre}. Cuando regularices tu situación
    vuelve a escribirnos y revisamos las opciones
    disponibles en ese momento. ¡Mucho éxito! 😊"
   siguiente_paso: "NO_INTERESADO"
   Registrar: intencion_regularizar = texto libre

B) EL CLIENTE CUESTIONA LA EVALUACIÓN:
   Mantén la postura con amabilidad:
   "Entiendo tu duda {nombre}. El requisito aplica
    para todos nuestros proyectos, ya que todos son
    exclusivos del subsidio habitacional. Lo sentimos."
   siguiente_paso: "NO_INTERESADO"

C) FUERA DE TEMA:
   Responde brevemente y cierra con la despedida.
   siguiente_paso: "NO_INTERESADO"

ESTILO:
- Honesto, cálido, sin culpabilizar al cliente.
- NO ofrecer "alternativas" ni ejecutivos: no las hay.
- NO usar frases como "no calificas" o "estás fuera".
- Mensaje corto y claro.

datos_extraidos:
  "motivo_no_califica": "tiene_propiedad" /
                        "subsidio_previo" / "ambos"
  "intencion_regularizar": texto libre / null""",

}


# ---------------------------------------------------------------------------
# Supabase REST
# ---------------------------------------------------------------------------

def _supabase_url() -> Optional[str]:
    return _get_env("SUPABASE_URL", "SUPABASE_PROJECT_URL", "SUPABASE_REST_URL")


def _supabase_service_role_key() -> Optional[str]:
    return _get_env("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE")


def _ingest_api_key() -> Optional[str]:
    return _get_env("INGEST_API_KEY")


def _supabase_rest_base() -> str:
    url = _supabase_url()
    if not url:
        raise RuntimeError("Falta SUPABASE_URL")
    return f"{url.rstrip('/')}/rest/v1"


def _supabase_headers() -> dict:
    key = _supabase_service_role_key()
    if not key:
        raise RuntimeError("Falta SUPABASE_SERVICE_ROLE_KEY")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def _supabase_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, str]] = None,
    json: Optional[Union[Dict[str, Any], List[Any]]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
):
    url = f"{_supabase_rest_base()}{path}"
    headers = _supabase_headers()
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(method, url, params=params, json=json, headers=headers)
        r.raise_for_status()
        if not r.content:
            return None
        return r.json()


# ---------------------------------------------------------------------------
# Operaciones sobre prospectos
# ---------------------------------------------------------------------------

async def upsert_prospecto(
    *,
    telefono_e164: str,
    nombre: Optional[str] = None,
    rut: Optional[str] = None,
    rango_sueldo: Optional[str] = None,
    numero_integrantes: Optional[int] = None,
    proyecto_id: Optional[str] = None,
    estado: Optional[str] = None,
    paso: Optional[str] = None,
    ultimo_texto_entrante: Optional[str] = None,
    datos: Optional[Dict] = None,
    cliente_id: Optional[int] = None,
):
    row: Dict[str, Any] = {
        "telefono_e164": telefono_e164,
        "actualizado_en": _utc_now_iso(),
    }
    if nombre is not None:
        row["nombre"] = nombre
    if rut is not None:
        row["rut"] = rut
    if rango_sueldo is not None:
        row["rango_sueldo"] = rango_sueldo
    if numero_integrantes is not None:
        row["numero_integrantes"] = numero_integrantes
    if proyecto_id is not None:
        row["proyecto_id"] = proyecto_id
    if estado is not None:
        row["estado"] = estado
    if paso is not None:
        row["paso"] = paso
    if ultimo_texto_entrante is not None:
        row["ultimo_texto_entrante"] = ultimo_texto_entrante
        row["ultimo_entrante_en"]    = _utc_now_iso()
        row["pendiente_respuesta"]   = True   # el bot aún no ha respondido
    if datos is not None:
        row["datos"] = datos
    if cliente_id is not None:
        row["cliente_id"] = cliente_id

    data = await _supabase_request(
        "POST",
        "/Prospecto",
        params={"on_conflict": "telefono_e164"},
        json=row,
        extra_headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    )
    if not data:
        return None
    return data[0]


async def actualizar_datos_prospecto(
    prospecto_id: str,
    nuevos_datos: Dict,
    siguiente_paso: Optional[str] = None,
    paso_origen: Optional[str] = None,
):
    rows = await _supabase_request(
        "GET",
        "/Prospecto",
        params={"id": f"eq.{prospecto_id}", "select": "datos,paso,opt_out"},
    )
    if not rows:
        return

    campos_columna: Dict[str, Any] = {}
    campos_blob:    Dict[str, Any] = {}

    for k, v in nuevos_datos.items():
        if v is None:
            continue
        if k in _CAMPOS_COLUMNA_PROPIA:
            # opt_out: jamás se puede pasar de true a false automáticamente
            if k == "opt_out" and rows[0].get("opt_out") and not v:
                continue
            campos_columna[k] = v
        else:
            campos_blob[k] = v

    merged_blob = {**(rows[0].get("datos") or {}), **campos_blob}

    update: Dict[str, Any] = {
        "datos": merged_blob,
        "actualizado_en": _utc_now_iso(),
        **campos_columna,
    }
    if siguiente_paso:
        update["paso"]   = siguiente_paso
        update["estado"] = siguiente_paso
    if paso_origen:
        update["paso_origen_no_interesado"] = paso_origen

    await _supabase_request(
        "PATCH",
        "/Prospecto",
        params={"id": f"eq.{prospecto_id}"},
        json=update,
    )

    if siguiente_paso == "DOCUMENTACION":
        asyncio.create_task(_notificar_interesado(prospecto_id))


async def insertar_mensaje(
    *,
    prospecto_id: str,
    direccion: str,
    text: Optional[str],
    wa_message_id: Optional[str] = None,
    cliente_id: Optional[int] = None,
):
    row: Dict[str, Any] = {
        "prospecto_id": prospecto_id,
        "direccion":    direccion,
        "texto":        text,
        "wa_id_mensaje": wa_message_id,
    }
    if cliente_id is not None:
        row["cliente_id"] = cliente_id
    await _supabase_request("POST", "/Mensaje", json=row)


async def insertar_documento(
    *,
    prospecto_id: str,
    tipo: str,
    nombre_archivo: str,
    url_storage: Optional[str] = None,
    wa_media_id: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> Dict:
    data = await _supabase_request(
        "POST",
        "/Documento",
        json={
            "prospecto_id": prospecto_id,
            "tipo":          tipo,
            "nombre_archivo": nombre_archivo,
            "url_storage":   url_storage,
            "wa_media_id":   wa_media_id,
            "mime_type":     mime_type,
            "verificado":    False,
        },
        extra_headers={"Prefer": "return=representation"},
    )
    return data[0] if data else {}


async def obtener_documentos_prospecto(prospecto_id: str) -> List[Dict]:
    rows = await _supabase_request(
        "GET",
        "/Documento",
        params={
            "prospecto_id": f"eq.{prospecto_id}",
            "select": "tipo,nombre_archivo,verificado,creado_en",
            "order": "creado_en.asc",
        },
    )
    return rows or []


_PROYECTO_SELECT = "id,codigo,nombre,ubicacion,imagen_url,inmobiliaria_id,Inmobiliaria(nombre,empresa_id,Empresa(nombre,industria_id,Industria(nombre))),ahorro_minimo_uf,valor_reserva_clp,valor_reserva_uf,tiene_piloto,valor_estacionamiento_uf,estacionamiento_obligatorio,notas,acepta_ds19,acepta_ds1_t23"

# ---------------------------------------------------------------------------
# Mapeo de variables por plantilla de WhatsApp
# Cada clave es el nombre exacto de la plantilla en Meta.
# El valor es la lista ordenada de claves del pool que corresponde a {{1}}, {{2}}, ...
# Para agregar una nueva plantilla: solo añadir una entrada aquí, sin tocar funciones.
# ---------------------------------------------------------------------------
TEMPLATE_VARS_MAP: Dict[str, List[str]] = {
    "ideal_para_mujeresfamilias": [
        "cliente_nombre", "proyecto_nombre", "proyecto_ubicacion",
        "subsidio_tipo", "monto_subsidio_uf",
    ],
    "enfoque_te_ayudamos": [
        "cliente_nombre", "proyecto_nombre",
        "monto_subsidio_uf", "valor_reserva_clp", "precio_desde_uf", "fecha_entrega",
    ],
    "cercana__consultiva": [
        "cliente_nombre", "proyecto_nombre", "proyecto_ubicacion",
        "subsidio_tipo", "monto_subsidio_uf",
        "valor_reserva_clp", "precio_desde_uf", "fecha_entrega",
    ],
}


async def _pool_plantilla(nombre: str, proyecto: Optional[Dict], tipologia_id: Optional[int] = None) -> Dict[str, str]:
    """Construye el pool completo de valores disponibles para cualquier plantilla."""
    p    = proyecto or {}

    tipos: List[str] = []
    if p.get("acepta_ds19"):
        tipos.append("DS19")
    if p.get("acepta_ds1_t23"):
        tipos.append("DS1 T23")
    subsidio_tipo = " / ".join(tipos) if tipos else "DS19"

    # Precio desde UF: usa tipología específica si se indicó, si no busca el mínimo del proyecto
    tip_rows: List[Dict] = []
    if tipologia_id:
        tip_rows = await _supabase_request(
            "GET", "/Tipologia",
            params={"id": f"eq.{tipologia_id}", "select": "valor_uf,monto_subsidio"},
        ) or []
    elif p.get("id"):
        tip_rows = await _supabase_request(
            "GET", "/Tipologia",
            params={"proyecto_id": f"eq.{p['id']}", "select": "valor_uf,monto_subsidio", "order": "id.asc"},
        ) or []
    precios = [t.get("valor_uf") for t in tip_rows if t.get("valor_uf")]
    precio_min   = min(precios) if precios else None
    precio_desde = f"{int(precio_min):,} UF".replace(",", ".") if precio_min else "a consultar"

    # Monto subsidio desde tipología
    tip_monto = None
    if tip_rows:
        tip_monto = tip_rows[0].get("monto_subsidio")
    elif p.get("id"):
        _m_rows = await _supabase_request("GET", "/Tipologia",
            params={"proyecto_id": f"eq.{p['id']}", "select": "monto_subsidio", "limit": "1", "order": "id.asc"}) or []
        tip_monto = _m_rows[0].get("monto_subsidio") if _m_rows else None
    try:
        tip_monto = float(tip_monto) if tip_monto is not None else None
    except (TypeError, ValueError):
        tip_monto = None

    # Fecha entrega desde Etapa
    fecha_entrega_pool = "por confirmar"
    if p.get("id"):
        _e_rows = await _supabase_request("GET", "/Etapa",
            params={"proyecto_id": f"eq.{p['id']}", "select": "fecha_entrega", "limit": "1", "order": "id.asc"}) or []
        if _e_rows and _e_rows[0].get("fecha_entrega"):
            fecha_entrega_pool = _e_rows[0]["fecha_entrega"]

    # Valor reserva CLP, con fallback a UF si no hay CLP
    reserva_clp = p.get("valor_reserva_clp")
    if reserva_clp:
        reserva_fmt = f"${int(reserva_clp):,}".replace(",", ".")
    elif p.get("valor_reserva_uf"):
        reserva_fmt = f"{p['valor_reserva_uf']} UF"
    else:
        reserva_fmt = "a consultar"

    return {
        "cliente_nombre":     nombre                      or "cliente",
        "proyecto_nombre":    p.get("nombre")             or "nuestro proyecto",
        "proyecto_ubicacion": p.get("ubicacion")          or "Santiago",
        "subsidio_tipo":      subsidio_tipo,
        "monto_subsidio_uf":  f"{tip_monto or 700} UF",
        "valor_reserva_clp":  reserva_fmt,
        "precio_desde_uf":    precio_desde,
        "fecha_entrega":      fecha_entrega_pool,
    }


async def obtener_proyecto_por_id(proyecto_id: str):
    rows = await _supabase_request(
        "GET", "/Proyecto",
        params={"id": f"eq.{proyecto_id}", "select": _PROYECTO_SELECT, "limit": "1"},
    )
    return rows[0] if rows else None


async def obtener_historial_mensajes(prospecto_id: str, limite: int = 12) -> List[Dict]:
    rows = await _supabase_request(
        "GET",
        "/Mensaje",
        params={
            "prospecto_id": f"eq.{prospecto_id}",
            "order": "creado_en.desc",
            "limit": str(limite),
            "select": "direccion,texto",
        },
    )
    return list(reversed(rows or []))


# ---------------------------------------------------------------------------
# WhatsApp Media — descarga y sube a Supabase Storage
# ---------------------------------------------------------------------------

async def descargar_media_whatsapp(media_id: str) -> tuple[bytes, str, str]:
    """
    Descarga un archivo multimedia de WhatsApp.
    Retorna (bytes, mime_type, nombre_archivo).
    """
    if not TOKEN_ACCESO:
        raise RuntimeError("Falta WA_ACCESS_TOKEN")

    headers = {"Authorization": f"Bearer {TOKEN_ACCESO}"}

    async with httpx.AsyncClient(timeout=30) as client:
        # 1) Obtener URL de descarga
        r = await client.get(
            f"https://graph.facebook.com/{VERSION_GRAPH}/{media_id}",
            headers=headers,
        )
        r.raise_for_status()
        info = r.json()
        download_url = info.get("url", "")
        mime_type = info.get("mime_type", "application/octet-stream")

        # 2) Descargar el archivo
        r2 = await client.get(download_url, headers=headers)
        r2.raise_for_status()
        file_bytes = r2.content

    # Extensión por mime_type
    ext_map = {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
        "application/pdf": "pdf",
    }
    ext = ext_map.get(mime_type, "bin")
    nombre = f"{media_id}.{ext}"

    return file_bytes, mime_type, nombre


async def subir_a_storage(
    file_bytes: bytes,
    nombre_archivo: str,
    prospecto_id: str,
    mime_type: str,
) -> str:
    """
    Sube un archivo al bucket documentos-clientes en Supabase Storage.
    Retorna la URL pública (firmada).
    """
    supa_url = _supabase_url()
    key = _supabase_service_role_key()
    if not supa_url or not key:
        raise RuntimeError("Falta configuración de Supabase")

    path = f"{prospecto_id}/{nombre_archivo}"
    upload_url = f"{supa_url.rstrip('/')}/storage/v1/object/documentos-clientes/{path}"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": mime_type,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(upload_url, headers=headers, content=file_bytes)
        r.raise_for_status()

    # URL de acceso (requiere service role para acceder, bucket privado)
    return f"{supa_url.rstrip('/')}/storage/v1/object/documentos-clientes/{path}"


# ---------------------------------------------------------------------------
# IA — construcción de contexto adaptativo por cadena jerárquica
# ---------------------------------------------------------------------------

def _construir_mind(proyecto: Optional[Dict]) -> Dict[str, str]:
    """
    Extrae la cadena contextual completa desde el proyecto hacia arriba:
    Proyecto → Inmobiliaria → Empresa → Industria.
    Retorna un dict con los nombres de cada nivel (vacío si no existe).
    """
    p    = proyecto or {}
    inm  = p.get("Inmobiliaria") or {}
    emp  = inm.get("Empresa") or {}
    ind  = emp.get("Industria") or {}
    return {
        "industria":    ind.get("nombre") or "Inmobiliaria",
        "empresa":      emp.get("nombre") or "",
        "inmobiliaria": inm.get("nombre") or "",
        "proyecto":     p.get("nombre")   or "",
    }


_PROYECTO_SELECT_LIGHT = (
    "id,nombre,ubicacion,acepta_ds19,"
    "acepta_ds1_t23,"
    "ahorro_minimo_uf,valor_reserva_clp,valor_reserva_uf,"
    "tiene_piloto,valor_estacionamiento_uf,notas"
)

async def _obtener_otros_proyectos(empresa_id: int, proyecto_id_actual: Optional[str]) -> List[Dict]:
    """Devuelve todos los proyectos de la empresa excepto el actual."""
    inms = await _supabase_request("GET", "/Inmobiliaria",
        params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
    if not inms:
        return []
    inm_ids = ",".join(str(i["id"]) for i in inms)
    rows = await _supabase_request("GET", "/Proyecto",
        params={"inmobiliaria_id": f"in.({inm_ids})", "select": _PROYECTO_SELECT_LIGHT}) or []
    return [p for p in rows if str(p.get("id")) != str(proyecto_id_actual)]


def _resumir_proyecto(p: Dict) -> str:
    """Genera una línea de resumen de un proyecto para el contexto del bot (otros proyectos disponibles)."""
    subsidios = []
    if p.get("acepta_ds19"):
        subsidios.append("DS19")
    if p.get("acepta_ds1_t23"):
        subsidios.append(f"DS1T23 {p.get('subsidio_ds1_t23_uf','')}UF".strip())
    sub_str = " | ".join(subsidios) or "sin subsidio"
    return f"• [{p['id']}] {p.get('nombre','?')} — {p.get('ubicacion','?')} | {sub_str}"


async def _cambiar_proyecto_prospecto(
    prospecto_id: str, nuevo_proyecto_id: str, cliente_id: Optional[int]
) -> None:
    """Actualiza proyecto_id en Prospecto y en Cliente cuando el cliente confirma cambio."""
    await _supabase_request("PATCH", "/Prospecto",
        params={"id": f"eq.{prospecto_id}"},
        json={"proyecto_id": nuevo_proyecto_id})
    if cliente_id:
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json={"proyecto_id": nuevo_proyecto_id})


# ---------------------------------------------------------------------------
# IA — respuesta con contexto de paso
# ---------------------------------------------------------------------------

async def generar_respuesta_ia(
    *,
    prospecto: Dict,
    proyecto: Optional[Dict],
    historial: List[Dict],
    mensaje_actual: str,
    docs_recibidos: Optional[List[Dict]] = None,
    otros_proyectos: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    if not ANTHROPIC_API_KEY or anthropic is None:
        logger.warning("ANTHROPIC_API_KEY no configurada — usando eco")
        return {"respuesta": f"Hola 👋 Recibí: {mensaje_actual}", "siguiente_paso": None, "datos_extraidos": {}}

    nombre              = (prospecto.get("nombre") or "").strip() or "amigo/a"
    telefono            = prospecto.get("telefono_e164") or ""
    rut                 = prospecto.get("rut") or "no registrado"
    rango_sueldo        = prospecto.get("rango_sueldo") or "no registrado"
    numero_integrantes  = prospecto.get("numero_integrantes") or "no registrado"
    paso_actual         = prospecto.get("paso") or "INICIO"
    # Construir datos de calificación desde columnas boolean dedicadas
    datos = {campo: prospecto.get(campo) for campo in _CAMPOS_CALIFICACION}

    mind = _construir_mind(proyecto)

    p                  = proyecto or {}
    proyecto_nombre    = p.get("nombre") or "nuestro proyecto"
    proyecto_ubicacion = p.get("ubicacion") or ""
    proyecto_inmobiliaria      = mind["inmobiliaria"]

    # Datos de Etapa y Tipologia (ya no viven en Proyecto)
    _etapa_bot = None
    _tip_monto_bot = None
    if p.get("id"):
        _etapas_bot = await _supabase_request("GET", "/Etapa",
            params={"proyecto_id": f"eq.{p['id']}", "select": "fecha_entrega,estado", "limit": "1", "order": "id.asc"}) or []
        _etapa_bot = _etapas_bot[0] if _etapas_bot else None
        _tips_bot = await _supabase_request("GET", "/Tipologia",
            params={"proyecto_id": f"eq.{p['id']}", "select": "id,nombre,valor_uf,dormitorios,banos,superficie_util_m2,tipo_subsidio,monto_subsidio"}) or []
        _raw_monto = _tips_bot[0].get("monto_subsidio") if _tips_bot else None
        try:
            _tip_monto_bot = float(_raw_monto) if _raw_monto is not None else None
        except (TypeError, ValueError):
            _tip_monto_bot = None

    proyecto_fecha_entrega     = (_etapa_bot.get("fecha_entrega") if _etapa_bot else None) or "por confirmar"
    proyecto_ahorro_minimo     = p.get("ahorro_minimo_uf") or 50
    proyecto_reserva_clp       = p.get("valor_reserva_clp") or ""
    proyecto_reserva_uf        = p.get("valor_reserva_uf") or ""
    proyecto_tiene_piloto      = p.get("tiene_piloto")
    proyecto_estac_uf          = p.get("valor_estacionamiento_uf") or ""
    proyecto_estac_obligatorio = p.get("estacionamiento_obligatorio")
    proyecto_notas             = p.get("notas") or ""
    proyecto_acepta_ds19       = p.get("acepta_ds19") or False
    proyecto_monto_subsidio    = _tip_monto_bot or 700
    proyecto_acepta_ds1t23     = p.get("acepta_ds1_t23") or False
    proyecto_subsidio_ds1t23   = p.get("subsidio_ds1_t23_uf") or ""
    proyecto_tipologias        = _tips_bot if p.get("id") else []

    # Construir bloque de subsidio según lo que acepta el proyecto
    subsidios_lineas = []
    if proyecto_acepta_ds19:
        subsidios_lineas.append(f"DS19: {proyecto_monto_subsidio} UF")
    if proyecto_acepta_ds1t23:
        monto_ds1t23 = proyecto_subsidio_ds1t23 or _tip_monto_bot or ""
        subsidios_lineas.append(f"DS1 T2/T3 (adjudicado){f': {monto_ds1t23} UF' if monto_ds1t23 else ''}")
    subsidios_texto = " | ".join(subsidios_lineas) if subsidios_lineas else "Este proyecto NO acepta subsidios habitacionales"

    # Estacionamiento
    if proyecto_estac_uf:
        estac_texto = f"{proyecto_estac_uf} UF {'(obligatorio)' if proyecto_estac_obligatorio else '(opcional)'}"
    else:
        estac_texto = "no disponible"

    # Estado de documentos para el paso ESPERA_DOCS (condicional según calificación)
    estado_documentos = resumen_documentos(docs_recibidos or [], datos)
    _estado_lineas = estado_documentos.split("\n")
    docs_recibidos_txt = _estado_lineas[0].replace("Recibidos: ", "") if _estado_lineas else "(ninguno)"
    docs_pendientes_txt = _estado_lineas[1].replace("Pendientes: ", "") if len(_estado_lineas) > 1 else "(ninguno)"

    # Variables adicionales para nuevo PASOS_CONFIG
    tipo_subsidio_datos   = datos.get("tipo_subsidio") or "no_determinado"
    tipo_entrega_proyecto = (_etapa_bot.get("estado") if _etapa_bot else None) or "entrega_futura"
    grupo_ds19_proyecto   = p.get("grupo_ds19") or "A"
    _credito_uf_est: Any = "consultar"
    if proyecto_tipologias:
        _precios = [t.get("valor_uf") for t in proyecto_tipologias if t.get("valor_uf")]
        if _precios:
            _precio_min = min(_precios)
            _credito_calc = round(_precio_min - proyecto_monto_subsidio - proyecto_ahorro_minimo)
            _credito_uf_est = max(_credito_calc, 0)
    _dividendo_est: Any = round(_credito_uf_est * 213) if isinstance(_credito_uf_est, (int, float)) else "consultar"
    _renta_min_est: Any = round(_dividendo_est * 4) if isinstance(_dividendo_est, (int, float)) else "consultar"

    _reserva_str = (f"{proyecto_reserva_uf} UF / ${proyecto_reserva_clp:,.0f}"
                    if proyecto_reserva_clp else proyecto_reserva_uf or "consultar")
    datos_proyecto_texto = (
        f"Nombre: {proyecto_nombre}\n"
        f"Ubicación: {proyecto_ubicacion}\n"
        f"Fecha entrega: {proyecto_fecha_entrega}\n"
        f"Tipo de entrega: {tipo_entrega_proyecto}\n"
        f"Monto subsidio: {proyecto_monto_subsidio} UF\n"
        f"Ahorro mínimo: {proyecto_ahorro_minimo} UF\n"
        f"Crédito estimado: {_credito_uf_est} UF\n"
        f"Sala piloto: {'Sí' if proyecto_tiene_piloto else 'No disponible'}\n"
        f"Estacionamiento: {estac_texto}\n"
        f"Valor reserva: {_reserva_str}\n"
        f"Tipologías: {json.dumps(proyecto_tipologias, ensure_ascii=False)}\n"
        f"Notas: {proyecto_notas}"
    )

    instrucciones = PASOS_CONFIG.get(paso_actual, PASOS_CONFIG["BIENVENIDA"]).format(
        nombre=nombre,
        rango_sueldo=rango_sueldo,
        datos=json.dumps(datos, ensure_ascii=False, indent=2),
        estado_documentos=estado_documentos,
        ahorro_minimo=proyecto_ahorro_minimo,
        monto_subsidio=proyecto_monto_subsidio,
        monto_subsidio_ds1t23=proyecto_subsidio_ds1t23 or "consultar",
        proyecto=proyecto_nombre,
        datos_proyecto=datos_proyecto_texto,
        tipo_subsidio=tipo_subsidio_datos,
        tipo_entrega=tipo_entrega_proyecto,
        credito_uf=_credito_uf_est,
        dividendo_estimado=f"{_dividendo_est:,}" if isinstance(_dividendo_est, (int, float)) else _dividendo_est,
        renta_minima_estimada=f"{_renta_min_est:,}" if isinstance(_renta_min_est, (int, float)) else _renta_min_est,
        docs_recibidos=docs_recibidos_txt,
        docs_pendientes=docs_pendientes_txt,
        grupo_ds19=grupo_ds19_proyecto,
        numero_integrantes=numero_integrantes,
    )

    sistema_identidad = f"Eres un asistente de ventas de la industria {mind['industria']}"
    if mind["empresa"]:
        sistema_identidad += f", trabajas para la empresa {mind['empresa']}"
    if mind["inmobiliaria"]:
        sistema_identidad += f", representando a la inmobiliaria {mind['inmobiliaria']}"
    if mind["proyecto"]:
        sistema_identidad += f", y tu foco actual es el proyecto {mind['proyecto']}"
    sistema_identidad += ". Eres profesional, empático y experto en el área."

    if otros_proyectos:
        _bloque_otros_proyectos = "\n".join(_resumir_proyecto(p) for p in otros_proyectos)
    else:
        _bloque_otros_proyectos = "No hay otros proyectos disponibles en este momento."

    system_prompt = f"""{sistema_identidad}

═══ DATOS DEL CLIENTE ═══
Nombre:              {nombre}
Teléfono:            {telefono}
RUT:                 {rut}
Rango sueldo:        {rango_sueldo}
Integrantes hogar:   {numero_integrantes}
Paso actual:         {paso_actual}

═══ DATOS DEL PROYECTO ═══
Nombre:              {proyecto_nombre}
Inmobiliaria:        {proyecto_inmobiliaria}
Ubicación:           {proyecto_ubicacion}
Fecha entrega:       {proyecto_fecha_entrega}
Subsidios:           {subsidios_texto}
Ahorro mínimo:       {proyecto_ahorro_minimo} UF
Estacionamiento:     {estac_texto}
Sala piloto:         {'Sí' if proyecto_tiene_piloto else 'No disponible'}
Valor reserva:       {f'{proyecto_reserva_uf} UF / ${proyecto_reserva_clp:,.0f}' if proyecto_reserva_clp else proyecto_reserva_uf or 'consultar'}
Tipologías:          {json.dumps(proyecto_tipologias, ensure_ascii=False)}
Notas del proyecto:  {proyecto_notas}

═══ INSTRUCCIONES DE ESTE PASO ═══
{instrucciones}

═══ OTROS PROYECTOS DISPONIBLES ═══
{_bloque_otros_proyectos}

═══ REGLAS GENERALES ═══
- Responde en español, de forma cálida y profesional.
- Mensajes cortos (máximo 3-4 párrafos). NUNCA más de 1 pregunta a la vez.
- Usa emojis con moderación.
- Usa los datos del proyecto para responder preguntas específicas del cliente
  (precio, fecha, estacionamiento, etc.) sin inventar información.
- NUNCA menciones nombres de empresas, constructoras o inmobiliarias que no estén
  explícitamente en los datos del sistema. Si no tienes la información, di que la
  coordinarás con el equipo a cargo, sin inventar nombres.
- Si el proyecto NO acepta subsidios, NO hables de subsidios ni consultes si el
  cliente tiene uno. Ignora completamente ese tema.
- Si el cliente pregunta algo fuera del tema del proyecto o subsidio,
  redirígelo amablemente sin ser brusco, recordándole en qué punto del proceso está.
- Si el cliente pide ver otros proyectos o mostrar alternativas, preséntale
  los proyectos de la sección "OTROS PROYECTOS DISPONIBLES" de forma breve.
- Cuando el cliente confirme explícitamente su interés en un proyecto distinto
  al actual, incluye "nuevo_proyecto_id" en datos_extraidos con el id exacto
  del proyecto confirmado. La calificación y documentos ya recopilados se
  conservan; NO reinicies el flujo salvo que el nuevo proyecto exija subsidio
  distinto y el cliente no haya calificado aún.

RESPONDE ÚNICAMENTE con JSON válido (sin markdown, sin texto extra):
{{
  "respuesta": "texto para enviar por WhatsApp",
  "siguiente_paso": null,
  "datos_extraidos": {{}}
}}
Valores válidos de siguiente_paso: null | "BIENVENIDA" | "INICIO" | "DOCUMENTACION" | "ESPERA_DOCS" | "DOCS_RECIBIDOS" | "NO_INTERESADO" | "NO_CALIFICA"
En datos_extraidos puedes incluir además: "nuevo_proyecto_id": "<uuid>" cuando el cliente confirme cambio de proyecto.
"""

    messages: List[Dict[str, str]] = []
    for msg in historial:
        texto = (msg.get("texto") or "").strip()
        if not texto:
            continue
        role = "user" if msg["direccion"] == "entrante" else "assistant"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += f"\n{texto}"
        else:
            messages.append({"role": role, "content": texto})
    messages.append({"role": "user", "content": mensaje_actual})
    # Prefill: fuerza a Claude a comenzar directamente con JSON
    messages.append({"role": "assistant", "content": "{"})

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        system=system_prompt,
        messages=messages,
    )

    raw = "{" + response.content[0].text.strip()
    # Limpiar si viene con markdown igual
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        result = json.loads(raw)
        return {
            "respuesta":       str(result.get("respuesta") or raw),
            "siguiente_paso":  result.get("siguiente_paso") or None,
            "datos_extraidos": result.get("datos_extraidos") or {},
        }
    except Exception:
        logger.warning("Claude no devolvió JSON válido, usando texto crudo")
        return {"respuesta": raw, "siguiente_paso": None, "datos_extraidos": {}}


# ---------------------------------------------------------------------------
# Webhook WhatsApp
# ---------------------------------------------------------------------------

@app.get("/webhook")
async def verify_webhook(request: Request):
    if not TOKEN_VERIFICACION:
        return Response(content="WA_VERIFY_TOKEN no está configurado", status_code=500)
    params = request.query_params
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == TOKEN_VERIFICACION:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)


# Segundos de espera antes de que el bot responda (para sensación más humana)
DELAY_RESPUESTA_SEGUNDOS = int(os.getenv("BOT_REPLY_DELAY", "30"))


async def _procesar_webhook(msg: Dict):
    """Procesa un mensaje de WhatsApp en background (después de devolver 200 a Meta)."""
    try:
        from_number = _normalize_phone(msg["from"])
        msg_type    = msg.get("type", "text")

        texto_preview = ""
        if msg_type == "text":
            texto_preview = (msg.get("text", {}).get("body") or "")[:80]
        elif msg_type in ("image", "audio", "video", "document"):
            texto_preview = f"[{msg_type}]"
        logger.info("📨 Mensaje entrante | %s | tipo: %s | %s", from_number, msg_type, texto_preview)

        # ── Obtener o crear prospecto de inmediato ─────────────────────────
        prospecto = None
        proyecto  = None

        if _supabase_url() and _supabase_service_role_key():
            prospecto = await upsert_prospecto(
                telefono_e164=from_number,
                estado="RESPONDIO",
            )
            if prospecto and prospecto.get("proyecto_id"):
                proyecto = await obtener_proyecto_por_id(prospecto["proyecto_id"])

        prospecto_id = (prospecto or {}).get("id")
        cliente_id_prospecto: Optional[int] = (prospecto or {}).get("cliente_id")

        # ── Manejo de DOCUMENTOS (imagen o archivo) ───────────────────────
        if msg_type in ("image", "document"):
            await _procesar_media(msg, msg_type, from_number, prospecto, prospecto_id)
            return

        # ── Manejo de TEXTO ───────────────────────────────────────────────
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            return

        # Guardar mensaje entrante ANTES del debounce (queda en historial aunque llegue otro)
        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="entrante",
                text=text,
                wa_message_id=msg.get("id"),
                cliente_id=cliente_id_prospecto,
            )
            await upsert_prospecto(
                telefono_e164=from_number,
                ultimo_texto_entrante=text,
            )

        # Debounce: esperar a que el cliente termine de escribir
        await asyncio.sleep(DELAY_RESPUESTA_SEGUNDOS)

        historial = []
        docs_recibidos = []

        if prospecto_id:
            try:
                historial = await obtener_historial_mensajes(prospecto_id)
                docs_recibidos = await obtener_documentos_prospecto(prospecto_id)
            except Exception:
                pass

        # Cargar otros proyectos de la misma empresa para contexto del bot
        otros_proyectos: List[Dict] = []
        if proyecto:
            empresa_id_ctx = ((proyecto.get("Inmobiliaria") or {}).get("empresa_id"))
            if empresa_id_ctx:
                try:
                    otros_proyectos = await _obtener_otros_proyectos(empresa_id_ctx, proyecto.get("id"))
                except Exception:
                    pass

        resultado = await generar_respuesta_ia(
            prospecto=prospecto or {},
            proyecto=proyecto,
            historial=historial,
            mensaje_actual=text,
            docs_recibidos=docs_recibidos,
            otros_proyectos=otros_proyectos,
        )

        reply_text      = (resultado["respuesta"] or "").strip()
        siguiente_paso  = resultado["siguiente_paso"]
        datos_extraidos = resultado["datos_extraidos"]

        if not reply_text:
            logger.warning("reply_text vacío para %s, omitiendo envío", from_number)
            return

        await send_whatsapp_message(to=from_number, text=reply_text)

        # Cambio de proyecto confirmado por el cliente
        nuevo_proyecto_id = datos_extraidos.pop("nuevo_proyecto_id", None)
        if nuevo_proyecto_id and prospecto_id:
            try:
                await _cambiar_proyecto_prospecto(prospecto_id, nuevo_proyecto_id, cliente_id_prospecto)
                logger.info("Proyecto actualizado → %s para prospecto %s", nuevo_proyecto_id, prospecto_id)
            except Exception:
                logger.exception("Error al cambiar proyecto del prospecto %s", prospecto_id)

        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="saliente",
                text=reply_text,
                cliente_id=cliente_id_prospecto,
            )
            # Bot respondió exitosamente → limpiar flag
            await _supabase_request("PATCH", "/Prospecto",
                params={"id": f"eq.{prospecto_id}"},
                json={"pendiente_respuesta": False})
            if datos_extraidos or siguiente_paso:
                paso_actual_prospecto = (prospecto or {}).get("paso") or "BIENVENIDA"

                # FASE 5.1 — guardar el paso de origen cuando el cliente se marca NO_INTERESADO
                paso_origen = paso_actual_prospecto if siguiente_paso == "NO_INTERESADO" else None

                # FASE 5.2 — calcular motivo_no_califica cuando transiciona a NO_CALIFICA
                if siguiente_paso == "NO_CALIFICA":
                    tiene_prop = datos_extraidos.get("tiene_propiedad") or (prospecto or {}).get("tiene_propiedad")
                    sub_prev   = datos_extraidos.get("subsidio_previo")  or (prospecto or {}).get("subsidio_previo")
                    if tiene_prop and sub_prev:
                        datos_extraidos["motivo_no_califica"] = "ambos"
                    elif tiene_prop:
                        datos_extraidos["motivo_no_califica"] = "tiene_propiedad"
                    elif sub_prev:
                        datos_extraidos["motivo_no_califica"] = "subsidio_previo"

                await actualizar_datos_prospecto(
                    prospecto_id,
                    datos_extraidos,
                    siguiente_paso,
                    paso_origen=paso_origen,
                )

    except Exception as e:
        logger.exception("Error procesando webhook: %s", _safe_httpx_error(e))


_ORDEN_ESTADO_PLANTILLA = {"enviado": 1, "entregado": 2, "leido": 3, "fallido": 0}
_META_ESTADO_MAP        = {"sent": "enviado", "delivered": "entregado", "read": "leido", "failed": "fallido"}


async def _procesar_status(status: Dict) -> None:
    wamid       = status.get("id")
    meta_estado = status.get("status")
    timestamp   = status.get("timestamp", "")
    errors      = status.get("errors") or []

    if not wamid or not meta_estado:
        return

    # Log siempre el status que llega desde Meta
    if errors:
        err_code = errors[0].get("code") if errors else ""
        err_msg  = errors[0].get("message") if errors else ""
        logger.warning("WA status FAILED wamid=%s code=%s msg=%s", wamid, err_code, err_msg)
    else:
        logger.info("WA status recibido: %s | wamid=%s | ts=%s", meta_estado, wamid, timestamp)

    estado_crm = _META_ESTADO_MAP.get(meta_estado)
    if not estado_crm:
        logger.warning("WA status desconocido '%s' ignorado wamid=%s", meta_estado, wamid)
        return

    rows = await _supabase_request("GET", "/Cliente",
        params={"wamid_plantilla": f"eq.{wamid}", "select": "id,estado_plantilla", "limit": "1"}) or []
    if not rows:
        logger.warning("WA status '%s' sin cliente para wamid=%s", meta_estado, wamid)
        return
    c = rows[0]
    actual = c.get("estado_plantilla") or ""
    # "fallido" siempre se aplica (ignora el orden); el resto solo avanza
    debe_actualizar = (
        estado_crm == "fallido"
        or _ORDEN_ESTADO_PLANTILLA.get(estado_crm, 0) > _ORDEN_ESTADO_PLANTILLA.get(actual, 0)
    )
    if debe_actualizar:
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{c['id']}"},
            json={"estado_plantilla": estado_crm},
            extra_headers={"Prefer": "return=minimal"})
        logger.info("estado_plantilla %s → %s (cliente=%s wamid=%s)", actual or "—", estado_crm, c["id"], wamid)


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    try:
        entry   = payload["entry"][0]
        changes = entry["changes"][0]
        value   = changes["value"]

        # Procesar actualizaciones de estado (entregado, leído, fallido)
        statuses_list = value.get("statuses", [])
        if statuses_list:
            asyncio.create_task(_procesar_status(statuses_list[0]))

        messages_list = value.get("messages", [])
        if not messages_list:
            return {"ok": True}

        msg   = messages_list[0]
        wamid = msg.get("id", "")

        # Deduplicar: Meta puede reenviar el mismo evento varias veces
        if wamid and wamid in _wamids_procesados:
            logger.info("Webhook duplicado ignorado: %s", wamid)
            return {"ok": True}
        if wamid:
            _wamids_procesados.add(wamid)
            _wamids_orden.append(wamid)
            if len(_wamids_orden) > _WAMID_MAX:
                old = _wamids_orden.pop(0)
                _wamids_procesados.discard(old)

        # Devuelve 200 a Meta de inmediato — debounce: cancela tarea anterior del mismo número
        from_number = _normalize_phone(msg.get("from", ""))
        existing = _pending_tasks.get(from_number)
        if existing and not existing.done():
            existing.cancel()
        _pending_tasks[from_number] = asyncio.create_task(_procesar_webhook(msg))

    except Exception:
        logger.exception("Error en /webhook")

    return {"ok": True}


async def _procesar_media(
    msg: Dict,
    msg_type: str,
    from_number: str,
    prospecto: Optional[Dict],
    prospecto_id: Optional[str],
):
    """Descarga el media de WhatsApp, lo clasifica y lo sube a Supabase Storage."""
    try:
        media_info = msg.get(msg_type, {})
        media_id   = media_info.get("id", "")
        filename   = media_info.get("filename") or media_info.get("caption") or ""

        if not media_id:
            logger.warning("Mensaje de media sin media_id")
            return

        # Descargar de WhatsApp
        file_bytes, mime_type, nombre_generado = await descargar_media_whatsapp(media_id)
        nombre_archivo = filename or nombre_generado

        # Clasificar tipo de documento
        tipo = clasificar_documento(nombre_archivo, mime_type)

        url_storage = None
        if prospecto_id:
            try:
                url_storage = await subir_a_storage(
                    file_bytes=file_bytes,
                    nombre_archivo=nombre_archivo,
                    prospecto_id=prospecto_id,
                    mime_type=mime_type,
                )
            except Exception as e:
                logger.warning(f"No se pudo subir a Storage: {e}")

            await insertar_documento(
                prospecto_id=prospecto_id,
                tipo=tipo,
                nombre_archivo=nombre_archivo,
                url_storage=url_storage,
                wa_media_id=media_id,
                mime_type=mime_type,
            )

            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="entrante",
                text=f"[{msg_type.upper()}] {nombre_archivo} → tipo: {tipo}",
                wa_message_id=msg.get("id"),
            )

        # Confirmar recepción y notificar pendientes
        docs_recibidos = []
        if prospecto_id:
            docs_recibidos = await obtener_documentos_prospecto(prospecto_id)

        datos_calificacion = {campo: (prospecto or {}).get(campo) for campo in _CAMPOS_CALIFICACION}
        requeridos_dict    = _docs_requeridos(datos_calificacion)
        pendientes         = documentos_pendientes(docs_recibidos, datos_calificacion)
        tipo_label         = requeridos_dict.get(tipo, {}).get("label") or nombre_archivo

        if not pendientes:
            confirmacion = (
                f"✅ ¡Recibí tu documento ({tipo_label})!\n\n"
                f"🎉 ¡Perfecto! Ya tenemos todos los documentos necesarios. "
                f"El equipo los revisará y se pondrá en contacto contigo pronto."
            )
            if prospecto_id:
                await actualizar_datos_prospecto(prospecto_id, {}, "DOCS_RECIBIDOS")
        else:
            pendientes_texto = "\n".join(
                f"  ▸ {requeridos_dict[t]['label']} ({falta} archivo{'s' if falta > 1 else ''} más)"
                for t, falta in pendientes.items()
                if t in requeridos_dict
            )
            confirmacion = (
                f"✅ ¡Recibí tu documento ({tipo_label})!\n\n"
                f"Aún me faltan los siguientes documentos:\n{pendientes_texto}\n\n"
                f"Puedes enviarlos directamente por este chat 📎"
            )
            if prospecto_id:
                paso_actual = (prospecto or {}).get("paso", "INICIO")
                if paso_actual not in ("ESPERA_DOCS", "DOCS_RECIBIDOS"):
                    await actualizar_datos_prospecto(prospecto_id, {}, "ESPERA_DOCS")

        await send_whatsapp_message(to=from_number, text=confirmacion)

        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="saliente",
                text=confirmacion,
            )

    except Exception as e:
        logger.exception(f"Error procesando media: {e}")
        try:
            await send_whatsapp_message(
                to=from_number,
                text="Recibí tu archivo, pero tuve un problema al procesarlo. Por favor intenta enviarlo nuevamente 🙏",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Ingesta de prospectos (primer mensaje)
# ---------------------------------------------------------------------------

@app.post("/prospectos/ingesta")
async def ingestar_prospecto(request: Request):
    try:
        ingest_api_key = _ingest_api_key()
        if ingest_api_key:
            provided = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
            if not provided or provided != ingest_api_key:
                return Response(content="Unauthorized", status_code=401)

        if not _supabase_url() or not _supabase_service_role_key():
            return Response(content="Supabase no está configurado", status_code=500)

        body = await request.json()
        phone = _normalize_phone(
            body.get("telefono_e164") or body.get("phone_e164")
            or body.get("telefono") or body.get("phone") or ""
        )
        nombre          = (body.get("nombre") or body.get("first_name") or "").strip() or None
        rut             = (body.get("rut") or "").strip() or None
        rango_sueldo    = (body.get("rango_sueldo") or "").strip() or None
        proyecto_id = (body.get("proyecto_id") or body.get("project_id") or "").strip() or None

        if not phone:
            return Response(content="Falta telefono_e164", status_code=400)
        if not proyecto_id:
            return Response(content="Falta proyecto_id", status_code=400)

        proyecto = await obtener_proyecto_por_id(proyecto_id)
        if not proyecto:
            return Response(content="proyecto_id no existe en proyectos", status_code=400)

        prospecto = await upsert_prospecto(
            telefono_e164=phone,
            nombre=nombre,
            rut=rut,
            rango_sueldo=rango_sueldo,
            proyecto_id=proyecto_id,
            estado="PLANTILLA_ENVIADA",
            paso="BIENVENIDA",
        )

        # Auto-envío deshabilitado: nombre_plantilla eliminado (rediseño pendiente)
        wa_resp = None
        logger.warning("Auto-envío de plantilla omitido — pendiente rediseño de flujo")

        if prospecto and prospecto.get("id"):
            await insertar_mensaje(
                prospecto_id=prospecto["id"],
                direccion="saliente",
                text="[PLANTILLA] pendiente selección manual",
                wa_message_id=(
                    (wa_resp or {}).get("messages", [{}])[0].get("id")
                    if isinstance(wa_resp, dict) else None
                ),
            )

        return {"ok": True, "prospecto_id": (prospecto or {}).get("id")}

    except Exception as e:
        logger.exception("Error en /prospectos/ingesta")
        return Response(
            content=_safe_httpx_error(e) or "Internal Server Error",
            status_code=500,
            media_type="text/plain",
        )


# ---------------------------------------------------------------------------
# WhatsApp Cloud API
# ---------------------------------------------------------------------------

async def send_whatsapp_message(to: str, text: str):
    if not TOKEN_ACCESO:
        raise RuntimeError("Falta WA_ACCESS_TOKEN")
    if not ID_NUMERO_TELEFONO:
        raise RuntimeError("Falta WA_PHONE_NUMBER_ID")
    url = f"https://graph.facebook.com/{VERSION_GRAPH}/{ID_NUMERO_TELEFONO}/messages"
    headers = {"Authorization": f"Bearer {TOKEN_ACCESO}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=data)
        r.raise_for_status()
        return r.json()


def _build_body_params(pool_vals: Dict[str, str], variables: List[str], param_names: Optional[List[str]] = None) -> List[Any]:
    """Construye body_text_params para send_whatsapp_template.
    Con param_names (plantilla de parámetros nombrados): retorna dicts con parameter_name.
    Sin param_names (posicional {{1}}): retorna lista de strings.
    """
    if param_names:
        return [
            {"parameter_name": param_names[i], "text": pool_vals.get(variables[i], "") if i < len(variables) else ""}
            for i in range(len(param_names))
        ]
    return [pool_vals.get(k, "") for k in variables]


async def send_whatsapp_template(
    *,
    to: str,
    template_name: str,
    language_code: str,
    body_text_params: List[Any],
    image_url: Optional[str] = None,
):
    if not TOKEN_ACCESO:
        raise RuntimeError("Falta WA_ACCESS_TOKEN")
    if not ID_NUMERO_TELEFONO:
        raise RuntimeError("Falta WA_PHONE_NUMBER_ID")

    url = f"https://graph.facebook.com/{VERSION_GRAPH}/{ID_NUMERO_TELEFONO}/messages"
    headers = {"Authorization": f"Bearer {TOKEN_ACCESO}", "Content-Type": "application/json"}

    components = []
    if image_url:
        components.append({
            "type": "header",
            "parameters": [{"type": "image", "image": {"link": image_url}}],
        })
    if body_text_params:
        components.append({
            "type": "body",
            "parameters": [
                {"type": "text", **p} if isinstance(p, dict) else {"type": "text", "text": p}
                for p in body_text_params
            ],
        })

    template_payload: Dict[str, Any] = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template_payload["components"] = components

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template_payload,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=data)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Endpoint de prueba
# ---------------------------------------------------------------------------

@app.post("/test/hello-world")
async def test_hello_world(request: Request):
    try:
        body = await request.json()
        phone = _normalize_phone(
            body.get("telefono") or body.get("telefono_e164") or body.get("phone") or ""
        )
        if not phone:
            return Response(content="Falta telefono", status_code=400)
        wa_resp = await send_whatsapp_template(
            to=phone,
            template_name="hello_world",
            language_code="en_US",
            body_text_params=[],
        )
        return {"ok": True, "wa": wa_resp}
    except Exception as e:
        logger.exception("Error en /test/hello-world")
        return Response(
            content=_safe_httpx_error(e) or "Internal Server Error",
            status_code=500,
            media_type="text/plain",
        )


# ---------------------------------------------------------------------------
# Auth JWT (Supabase)
# ---------------------------------------------------------------------------

async def _get_usuario_actual(request: Request) -> Optional[Dict]:
    """Verifica el JWT de Supabase y retorna el perfil del usuario, o None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    supa_url = _supabase_url()
    key = _supabase_service_role_key()
    if not supa_url or not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{supa_url}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": key},
            )
            if r.status_code != 200:
                return None
            user_id = r.json().get("id")
            if not user_id:
                return None
        perfiles = await _supabase_request(
            "GET", "/Usuario",
            params={"id": f"eq.{user_id}", "select": "*", "limit": "1"},
        )
        return perfiles[0] if perfiles else None
    except Exception:
        return None


def _solo_admin(perfil: Optional[Dict]) -> bool:
    """True para owner y administrador — acceso a todo el CRM."""
    return bool(perfil and perfil.get("rol") in ("owner", "administrador"))

def _solo_owner(perfil: Optional[Dict]) -> bool:
    """True solo para owner — operaciones exclusivas del dueño."""
    return bool(perfil and perfil.get("rol") == "owner")


async def _invitar_usuario_supabase(correo: str, nombre: str, rol: str) -> str:
    supa_url = _supabase_url()
    key = _supabase_service_role_key()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{supa_url}/auth/v1/invite",
            headers={"Authorization": f"Bearer {key}", "apikey": key, "Content-Type": "application/json"},
            json={"email": correo, "data": {"nombre": nombre, "rol": rol}, "redirect_to": f"{os.getenv('SITE_URL', 'http://localhost:8000')}/reset-password"},
        )
        r.raise_for_status()
        return r.json()["id"]


async def _log_correo(tipo: str, destinatario: str, asunto: str, estado: str, detalle_error: str = None, usuario_id: str = None):
    try:
        await _supabase_request("POST", "/log_correos", json={
            "tipo": tipo, "destinatario": destinatario, "asunto": asunto,
            "estado": estado, "detalle_error": detalle_error, "usuario_id": usuario_id,
        })
    except Exception as e:
        logger.warning("No se pudo guardar log de correo: %s", e)



def _normalizar_nombre(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (s or "").lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


async def _buscar_id_proyecto(nombre_csv: str, proyectos_cache: List[Dict]) -> Optional[Dict]:
    nombre_norm = _normalizar_nombre(nombre_csv)
    for p in proyectos_cache:
        if _normalizar_nombre(p.get("nombre", "")) == nombre_norm:
            return p
        for alias in (p.get("nombres_csv") or []):
            if _normalizar_nombre(alias) == nombre_norm:
                return p
    return None


# ---------------------------------------------------------------------------
# API — Usuarios
# ---------------------------------------------------------------------------

@app.get("/api/usuarios/me")
async def api_usuario_actual(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    return perfil


@app.get("/api/usuarios")
async def api_listar_usuarios(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request("GET", "/Usuario", params={"select": "*", "order": "created_at.desc"})
    return rows or []


@app.post("/api/usuarios")
async def api_crear_usuario(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body    = await request.json()
        nombre  = (body.get("nombre") or "").strip()
        rut     = (body.get("rut") or "").strip()
        correo  = (body.get("correo") or "").strip()
        celular      = (body.get("celular") or "").strip() or None
        rol          = body.get("rol", "usuario")
        email_alias  = (body.get("email_alias") or "").strip() or None
        if not nombre or not rut or not correo:
            return Response(content="Faltan campos obligatorios: nombre, rut, correo", status_code=400)
        roles_permitidos = ("ejecutivo", "administrador") if _solo_owner(perfil) else ("ejecutivo",)
        if rol not in roles_permitidos:
            return Response(content=f"rol debe ser uno de: {', '.join(roles_permitidos)}", status_code=400)
        inmobiliaria_ids = body.get("inmobiliaria_ids") or []
        proyecto_ids     = body.get("proyecto_ids") or []
        user_id = await _invitar_usuario_supabase(correo, nombre, rol)
        await _supabase_request("POST", "/Usuario", json={
            "id": user_id, "nombre": nombre, "rut": rut, "correo": correo,
            "celular": celular, "rol": rol, "password_provisional": False,
            "email_alias": email_alias,
            "inmobiliaria_ids": inmobiliaria_ids,
            "proyecto_ids": proyecto_ids,
        })
        asyncio.create_task(_log_correo("invitacion", correo, "Invitación al CRM", "enviado", usuario_id=user_id))
        return {"ok": True, "usuario_id": user_id}
    except Exception as e:
        logger.exception("Error creando usuario")
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


@app.get("/api/log-correos")
async def api_log_correos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request(
        "GET", "/log_correos",
        params={"select": "*", "order": "created_at.desc", "limit": "100"},
    )
    return rows or []


@app.patch("/api/usuarios/{usuario_id}")
async def api_actualizar_usuario(usuario_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body   = await request.json()
        update = {k: body[k] for k in ("nombre", "celular", "rol", "estado", "email_alias", "inmobiliaria_ids", "proyecto_ids") if k in body}
        if not update:
            return Response(content="Nada que actualizar", status_code=400)
        await _supabase_request("PATCH", "/Usuario", params={"id": f"eq.{usuario_id}"}, json=update)
        return {"ok": True}
    except Exception as e:
        return Response(content=str(e), status_code=500, media_type="text/plain")


@app.post("/api/auth/cambiar-password")
async def api_cambiar_password(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        body  = await request.json()
        nueva = (body.get("nueva_password") or "").strip()
        if len(nueva) < 8:
            return Response(content="La contraseña debe tener al menos 8 caracteres", status_code=400)
        token    = request.headers.get("Authorization", "")[7:]
        supa_url = _supabase_url()
        key      = _supabase_service_role_key()
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.put(
                f"{supa_url}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": key, "Content-Type": "application/json"},
                json={"password": nueva},
            )
            r.raise_for_status()
        await _supabase_request("PATCH", "/Usuario",
            params={"id": f"eq.{perfil['id']}"},
            json={"password_provisional": False})
        return {"ok": True}
    except Exception as e:
        return Response(content=str(e), status_code=500, media_type="text/plain")


# ---------------------------------------------------------------------------
# API — Templates WhatsApp (dinámico desde Meta)
# ---------------------------------------------------------------------------

@app.get("/api/templates")
async def api_listar_templates(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not WA_WABA_ID:
        return Response(content="WA_WABA_ID no configurado en el servidor", status_code=500)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"https://graph.facebook.com/{VERSION_GRAPH}/{WA_WABA_ID}/message_templates",
                params={"status": "APPROVED", "limit": "100"},
                headers={"Authorization": f"Bearer {TOKEN_ACCESO}"},
            )
            r.raise_for_status()
        return {
            "templates": [
                {"name": t["name"], "language": t.get("language", "es"), "category": t.get("category", ""), "components": t.get("components", [])}
                for t in r.json().get("data", [])
            ]
        }
    except Exception as e:
        return Response(content=_safe_httpx_error(e), status_code=500, media_type="text/plain")


# ---------------------------------------------------------------------------
# API — Configuración de plantillas WhatsApp
# ---------------------------------------------------------------------------

@app.get("/api/plantillas-config")
async def api_listar_plantillas_config(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Forbidden", status_code=403)
    rows = await _supabase_request("GET", "/PlantillaConfig", params={"select": "*"}) or []
    # Combinar hardcoded (fallback) con lo que hay en DB; DB tiene prioridad
    merged: Dict[str, Dict] = {
        name: {"template_name": name, "variables": vars_list, "source": "default"}
        for name, vars_list in TEMPLATE_VARS_MAP.items()
    }
    for row in rows:
        merged[row["template_name"]] = {**row, "source": "db"}
    return list(merged.values())


@app.put("/api/plantillas-config/{template_name}")
async def api_upsert_plantilla_config(template_name: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Forbidden", status_code=403)
    body = await request.json()
    variables   = body.get("variables", [])
    param_names = body.get("param_names") or None  # nombres de parámetros nombrados (ej: ["nombre_cliente"])
    if not isinstance(variables, list):
        return Response(content="variables debe ser una lista", status_code=400)
    now_iso = datetime.now(timezone.utc).isoformat()
    patch_data: Dict[str, Any] = {"variables": variables, "updated_at": now_iso}
    if param_names is not None:
        patch_data["param_names"] = param_names
    existing = await _supabase_request(
        "GET", "/PlantillaConfig",
        params={"template_name": f"eq.{template_name}", "select": "id", "limit": "1"},
    ) or []
    if existing:
        await _supabase_request("PATCH", "/PlantillaConfig",
            params={"template_name": f"eq.{template_name}"}, json=patch_data)
    else:
        await _supabase_request("POST", "/PlantillaConfig",
            json={"template_name": template_name, **patch_data})
    return {"ok": True}


# ---------------------------------------------------------------------------
# API — Importar clientes desde CSV
# ---------------------------------------------------------------------------

@app.post("/api/clientes/importar")
async def api_importar_clientes(request: Request, file: UploadFile = File(...)):
    from fastapi.responses import StreamingResponse as _SR
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    usuario_id = perfil["id"]
    empresa_id  = request.query_params.get("empresa_id")
    proyecto_id = request.query_params.get("proyecto_id")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    # Detectar formato: SERVIU tiene columna "Dv" o "Primer Apellido"
    _peek = csv.DictReader(io.StringIO(text))
    _headers = _peek.fieldnames or []
    is_serviu = "Dv" in _headers or "Primer Apellido" in _headers

    if is_serviu and not proyecto_id:
        return Response(
            content="No hay un proyecto para asignar. Sube este archivo desde dentro de un proyecto.",
            status_code=400, media_type="text/plain"
        )

    total = max(0, sum(1 for l in text.splitlines() if l.strip()) - 1)

    async def _stream():
        try:
            proyecto_params: Dict[str, str] = {"select": "id,nombre,nombres_csv"}
            if empresa_id:
                inmobiliarias = await _supabase_request(
                    "GET", "/Inmobiliaria",
                    params={"empresa_id": f"eq.{empresa_id}", "select": "id"},
                ) or []
                inm_ids = ",".join(str(i["id"]) for i in inmobiliarias)
                if inm_ids:
                    proyecto_params["inmobiliaria_id"] = f"in.({inm_ids})"
            todos_proyectos = await _supabase_request(
                "GET", "/Proyecto", params=proyecto_params,
            ) or []

            # Extraer teléfonos del CSV (primera pasada, sin I/O)
            phones_csv: set = set()
            for row in csv.DictReader(io.StringIO(text)):
                if is_serviu:
                    tel_raw = (row.get("Móvil") or row.get("Movil") or
                               row.get("Fono Domicilio") or row.get("Fono Trabajo") or "").strip()
                else:
                    tel_raw = (row.get("Teléfono") or row.get("Telefono") or "").strip()
                tel = _normalize_phone(tel_raw)
                if tel:
                    phones_csv.add(tel)

            # Una sola query solo con los teléfonos que vienen en el archivo
            phones_existentes: set = set()
            if phones_csv:
                phones_in = ",".join(phones_csv)
                batch = await _supabase_request("GET", "/Cliente",
                    params={"Telefono": f"in.({phones_in})", "select": "Telefono"},
                ) or []
                phones_existentes = {r["Telefono"] for r in batch if r.get("Telefono")}

            # Resolver proyecto fijo para SERVIU
            proyecto_serviu = None
            if is_serviu:
                _proy_rows = await _supabase_request("GET", "/Proyecto",
                    params={"id": f"eq.{proyecto_id}", "select": "id,nombre", "limit": "1"}) or []
                proyecto_serviu = _proy_rows[0] if _proy_rows else None

            creados, duplicados, errores, ids_creados = 0, [], [], []
            reader = csv.DictReader(io.StringIO(text))

            for i, row in enumerate(reader):
                fila = i + 2
                try:
                    if is_serviu:
                        rut_num = (row.get("rut") or "").strip()
                        dv      = (row.get("Dv") or "").strip()
                        rut     = f"{rut_num}-{dv}" if rut_num and dv else None
                        nombre  = " ".join(filter(None, [
                            (row.get("Nombre") or "").strip(),
                            (row.get("Primer Apellido") or "").strip(),
                            (row.get("Segundo Apellido") or "").strip(),
                        ]))
                        tel_raw  = (row.get("Móvil") or row.get("Movil") or
                                    row.get("Fono Domicilio") or row.get("Fono Trabajo") or "").strip()
                        correo   = (row.get("E-mail") or "").strip() or None
                        telefono = _normalize_phone(tel_raw)

                        datos_raw = {"Nombre": nombre, "Rut": f"{rut_num}-{dv}",
                                     "Teléfono": tel_raw, "E-mail": correo or ""}

                        if not nombre or not telefono:
                            errores.append({"fila": fila, "motivo": "Nombre o Teléfono vacío", "datos": datos_raw})
                        elif not rut:
                            errores.append({"fila": fila, "nombre": nombre, "motivo": "Rut vacío", "datos": datos_raw})
                        elif not proyecto_serviu:
                            errores.append({"fila": fila, "nombre": nombre,
                                            "motivo": "Proyecto no encontrado", "datos": datos_raw})
                        elif telefono in phones_existentes:
                            duplicados.append({"fila": fila, "nombre": nombre,
                                               "telefono": telefono, "datos": datos_raw})
                        else:
                            _r = await _supabase_request("POST", "/Cliente",
                                json={
                                    "proyecto_id": proyecto_serviu["id"],
                                    "Contacto": nombre, "Rut": rut, "Correo": correo,
                                    "Telefono": telefono, "estado_crm": None,
                                    "Tramo de renta": None,
                                    "tiene_subsidio": None,
                                    "tipo_subsidio": None,
                                    "tiene_propiedad": None,
                                    "primer mensaje": True, "wtsp_habilitado": True,
                                    "usuario_id": usuario_id,
                                    "Fecha Ult. Gestión": datetime.now(timezone.utc).date().isoformat(),
                                },
                                extra_headers={"Prefer": "return=representation"})
                            phones_existentes.add(telefono)
                            if isinstance(_r, list) and _r:
                                ids_creados.append({"id": _r[0]["id"], "nombre": nombre, "telefono": telefono})
                            creados += 1
                    else:
                        nombre_proyecto = (row.get("Proyecto") or "").strip()
                        nombre          = (row.get("Contacto") or "").strip()
                        rut             = (row.get("Rut") or "").strip()
                        correo          = (row.get("Correo") or "").strip() or None
                        tel_raw         = (row.get("Teléfono") or row.get("Telefono") or "").strip()
                        telefono        = _normalize_phone(tel_raw)
                        estado_crm      = (row.get("Estado") or "").strip() or None
                        tramo_renta     = (row.get("Tramo de renta") or "").strip() or None
                        sub_raw         = (row.get("Tiene subsidio") or "").strip().lower()
                        tiene_subsidio  = True if sub_raw in ("si","sí","yes","1") else (False if sub_raw in ("no","0") else None)
                        tipo_subsidio   = (row.get("Tipo subsidio") or "").strip() or None
                        prop_raw        = (row.get("Tiene propiedad") or "").strip().lower()
                        tiene_propiedad = True if prop_raw in ("si","sí","yes","1") else (False if prop_raw in ("no","0") else None)

                        datos_raw = {"Contacto": nombre, "Rut": rut, "Correo": correo or "",
                                     "Teléfono": tel_raw, "Proyecto": nombre_proyecto,
                                     "Estado": estado_crm or "", "Tramo de renta": tramo_renta or "",
                                     "Tiene subsidio": sub_raw, "Tipo subsidio": tipo_subsidio or "",
                                     "Tiene propiedad": prop_raw}

                        if not nombre or not telefono:
                            errores.append({"fila": fila, "motivo": "Contacto o Teléfono vacío", "datos": datos_raw})
                        elif not rut:
                            errores.append({"fila": fila, "nombre": nombre, "motivo": "Rut vacío", "datos": datos_raw})
                        elif not nombre_proyecto:
                            errores.append({"fila": fila, "nombre": nombre, "motivo": "Proyecto vacío", "datos": datos_raw})
                        elif telefono in phones_existentes:
                            duplicados.append({"fila": fila, "nombre": nombre,
                                               "telefono": telefono, "datos": datos_raw})
                        else:
                            proyecto = await _buscar_id_proyecto(nombre_proyecto, todos_proyectos)
                            if not proyecto:
                                errores.append({"fila": fila, "nombre": nombre,
                                                "motivo": f"Proyecto '{nombre_proyecto}' no encontrado — agrega el alias en nombres_csv",
                                                "datos": datos_raw})
                            else:
                                _r = await _supabase_request("POST", "/Cliente",
                                    json={
                                        "proyecto_id": proyecto["id"],
                                        "Contacto": nombre, "Rut": rut, "Correo": correo,
                                        "Telefono": telefono, "estado_crm": estado_crm,
                                        "Tramo de renta": tramo_renta,
                                        "tiene_subsidio": tiene_subsidio,
                                        "tipo_subsidio": tipo_subsidio,
                                        "tiene_propiedad": tiene_propiedad,
                                        "primer mensaje": True, "wtsp_habilitado": True,
                                        "usuario_id": usuario_id,
                                        "Fecha Ult. Gestión": datetime.now(timezone.utc).date().isoformat(),
                                    },
                                    extra_headers={"Prefer": "return=representation"})
                                phones_existentes.add(telefono)
                                if isinstance(_r, list) and _r:
                                    ids_creados.append({"id": _r[0]["id"], "nombre": nombre, "telefono": telefono})
                                creados += 1
                except Exception as ex:
                    errores.append({"fila": fila, "motivo": str(ex)})

                yield f"data: {json.dumps({'t':'prog','n':i+1,'total':total,'creados':creados,'dups':len(duplicados),'errs':len(errores)})}\n\n"

            yield f"data: {json.dumps({'t':'done','ok':True,'creados':creados,'duplicados':len(duplicados),'errores':len(errores),'detalle_errores':errores,'detalle_duplicados':duplicados,'ids_creados':ids_creados})}\n\n"

        except Exception as e:
            logger.exception("Error importando clientes")
            yield f"data: {json.dumps({'t':'error','msg':str(e)})}\n\n"

    return _SR(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# API — Enviar primer WhatsApp seleccionando template
# ---------------------------------------------------------------------------

@app.post("/api/clientes/{cliente_id}/enviar-wtsp")
async def api_enviar_primer_wtsp(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        body          = await request.json()
        template_name = (body.get("template_name") or "").strip()
        language_code = (body.get("language_code") or "es_CL").strip()
        components_meta = body.get("components") or []
        if not template_name:
            return Response(content="Falta template_name", status_code=400)
        rows = await _supabase_request("GET", "/Cliente",
            params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
        if not rows:
            return Response(content="Cliente no encontrado", status_code=404)
        c          = rows[0]
        telefono   = _normalize_phone(c.get("Telefono") or "")
        nombre     = (c.get("Contacto") or "").strip()
        proyecto_id = c.get("proyecto_id") or ""
        if not telefono:
            return Response(content="Cliente sin teléfono", status_code=400)

        proyecto = None
        if proyecto_id:
            proyecto = await obtener_proyecto_por_id(proyecto_id)

        header_comp = next((cm for cm in components_meta if cm.get("type") == "HEADER"), None)
        needs_image = header_comp and header_comp.get("format") == "IMAGE"
        image_url   = (proyecto.get("imagen_url") or None) if (needs_image and proyecto) else None

        tipologia_id = body.get("tipologia_id") or None
        if tipologia_id:
            try:
                tipologia_id = int(tipologia_id)
            except (ValueError, TypeError):
                tipologia_id = None

        pool_vals = await _pool_plantilla(nombre, proyecto, tipologia_id=tipologia_id)

        # Prioridad: 1) config en DB  2) mapa hardcoded  3) fallback posicional
        db_cfg = await _supabase_request(
            "GET", "/PlantillaConfig",
            params={"template_name": f"eq.{template_name}", "select": "variables,param_names", "limit": "1"},
        ) or []
        if db_cfg and db_cfg[0].get("variables"):
            cfg_vars   = db_cfg[0]["variables"]
            cfg_params = db_cfg[0].get("param_names") or None
            body_text_params: List[Any] = _build_body_params(pool_vals, cfg_vars, cfg_params)
        elif template_name in TEMPLATE_VARS_MAP:
            body_text_params = [pool_vals.get(k, "") for k in TEMPLATE_VARS_MAP[template_name]]
        else:
            fallback_order = [
                "cliente_nombre", "proyecto_nombre", "proyecto_ubicacion",
                "subsidio_tipo", "monto_subsidio_uf", "valor_reserva_clp",
                "precio_desde_uf", "fecha_entrega",
            ]
            body_comp = next((cm for cm in components_meta if cm.get("type") == "BODY"), None)
            body_text_params = []
            if body_comp:
                # Named params: Meta incluye example.body_text_named_params con los nombres reales
                named_meta = (body_comp.get("example") or {}).get("body_text_named_params") or []
                if named_meta:
                    for i, p in enumerate(named_meta):
                        pname    = p.get("parameter_name", "")
                        pool_key = fallback_order[i] if i < len(fallback_order) else "cliente_nombre"
                        body_text_params.append({"parameter_name": pname, "text": pool_vals.get(pool_key, "")})
                else:
                    # Posicionales {{1}}, {{2}} — solo dígitos
                    positional = re.findall(r'\{\{\d+\}\}', body_comp.get("text", ""))
                    var_count  = max((int(m.strip("{}")) for m in positional), default=0)
                    body_text_params = [pool_vals.get(fallback_order[i], "") for i in range(min(var_count, len(fallback_order)))]
            # Si la plantilla no tiene variables → no enviar parámetros (evita el error 400)

        wa = await send_whatsapp_template(
            to=telefono, template_name=template_name, language_code=language_code,
            body_text_params=body_text_params, image_url=image_url,
        )
        ahora_wtsp = datetime.now(timezone.utc)
        wamid_enviado = (wa.get("messages") or [{}])[0].get("id") if isinstance(wa, dict) else None
        patch_cliente: Dict[str, Any] = {
            "primer mensaje": False,
            "wtsp_habilitado": False,
            "primer_wtsp_en": ahora_wtsp.isoformat(),
            "Fecha Ult. Gestión": ahora_wtsp.isoformat(),
            "wamid_plantilla":  wamid_enviado,
            "estado_plantilla": "enviado",
        }
        # Auto-agendar recordatorio 24h después si no hay uno ya programado
        if not c.get("recordatorio_at"):
            patch_cliente["recordatorio_at"] = (ahora_wtsp + timedelta(hours=24)).isoformat()
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"}, json=patch_cliente)
        prospecto = await upsert_prospecto(
            telefono_e164=telefono, nombre=nombre, rut=c.get("Rut"),
            rango_sueldo=c.get("Tramo de renta"), proyecto_id=proyecto_id,
            estado="PLANTILLA_ENVIADA", paso="BIENVENIDA", cliente_id=cliente_id,
        )
        # Registrar plantilla enviada como mensaje saliente en la conversación
        if prospecto and prospecto.get("id"):
            body_comp = next((cm for cm in components_meta if cm.get("type") == "BODY"), None)
            texto_plantilla = f"[Plantilla: {template_name}]"
            if body_comp and body_comp.get("text"):
                # Rellenar variables con los valores reales para mostrar en conversación
                raw = body_comp["text"]
                if isinstance(body_text_params, list):
                    for i, p in enumerate(body_text_params):
                        val = p.get("text", "") if isinstance(p, dict) else str(p)
                        raw = re.sub(r'\{\{[^}]+\}\}', val, raw, count=1)
                texto_plantilla = raw
            await insertar_mensaje(
                prospecto_id=prospecto["id"],
                direccion="saliente",
                text=texto_plantilla,
                wa_message_id=(wa.get("messages", [{}])[0].get("id") if isinstance(wa, dict) else None),
                cliente_id=cliente_id,
            )
        return {"ok": True, "wa": wa}
    except Exception as e:
        logger.exception("Error en enviar-wtsp cliente %s", cliente_id)
        return Response(content=_safe_httpx_error(e), status_code=500, media_type="text/plain")


# ---------------------------------------------------------------------------
# API para el frontend
# ---------------------------------------------------------------------------



@app.get("/api/empresas")
async def api_listar_empresas(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    params: Dict[str, str] = {"estado": "eq.activa", "select": "id,nombre,slug,logo_url,color_marca", "order": "nombre.asc"}
    if not _solo_admin(perfil):
        inm_ids = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        if not inm_ids:
            return []
        inm_str = ",".join(str(i) for i in inm_ids)
        inmobiliarias = await _supabase_request(
            "GET", "/Inmobiliaria",
            params={"id": f"in.({inm_str})", "select": "empresa_id"},
        ) or []
        empresa_ids = list({str(i["empresa_id"]) for i in inmobiliarias if i.get("empresa_id")})
        if not empresa_ids:
            return []
        params["id"] = f"in.({','.join(empresa_ids)})"
    rows = await _supabase_request("GET", "/Empresa", params=params)
    return rows or []


@app.get("/api/inmobiliarias")
async def api_listar_inmobiliarias(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    empresa_id = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": "id,nombre,empresa_id", "order": "nombre.asc"}
    if empresa_id:
        params["empresa_id"] = f"eq.{empresa_id}"
    if not _solo_admin(perfil):
        inm_ids = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        if not inm_ids:
            return []
        params["id"] = f"in.({','.join(str(i) for i in inm_ids)})"
    rows = await _supabase_request("GET", "/Inmobiliaria", params=params)
    return rows or []


@app.post("/api/inmobiliarias")
async def api_crear_inmobiliaria(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    nombre     = (body.get("nombre") or "").strip()
    empresa_id = body.get("empresa_id")
    if not nombre or not empresa_id:
        return Response(content="Faltan campos obligatorios", status_code=400)
    row = await _supabase_request("POST", "/Inmobiliaria",
        json={"nombre": nombre, "empresa_id": empresa_id},
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@app.patch("/api/inmobiliarias/{inm_id}")
async def api_editar_inmobiliaria(inm_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    nombre = (body.get("nombre") or "").strip()
    if not nombre:
        return Response(content="Falta nombre", status_code=400)
    await _supabase_request("PATCH", "/Inmobiliaria",
        params={"id": f"eq.{inm_id}"},
        json={"nombre": nombre},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


@app.get("/api/proyectos")
async def api_listar_proyectos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _PROYECTO_SELECT, "order": "nombre.asc"}
    if _solo_admin(perfil):
        if inmobiliaria_id:
            params["inmobiliaria_id"] = f"eq.{inmobiliaria_id}"
        elif empresa_id:
            inmobiliarias = await _supabase_request(
                "GET", "/Inmobiliaria",
                params={"empresa_id": f"eq.{empresa_id}", "select": "id"},
            ) or []
            inm_ids = ",".join(str(i["id"]) for i in inmobiliarias)
            if not inm_ids:
                return []
            params["inmobiliaria_id"] = f"in.({inm_ids})"
    else:
        inm_asig  = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        proy_asig = [str(p) for p in (perfil.get("proyecto_ids") or []) if p is not None]
        proy_de_inm: List[str] = []
        if inm_asig:
            inm_str = ",".join(str(i) for i in inm_asig)
            proy_de_inm = [str(p["id"]) for p in (await _supabase_request(
                "GET", "/Proyecto",
                params={"inmobiliaria_id": f"in.({inm_str})", "select": "id"},
            ) or []) if p.get("id")]
        visibles = set(proy_asig + proy_de_inm)
        if not visibles:
            return []
        params["id"] = f"in.({','.join(visibles)})"
    rows = await _supabase_request("GET", "/Proyecto", params=params)
    return rows or []


@app.post("/api/proyectos")
async def api_crear_proyecto(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    nombre          = (body.get("nombre") or "").strip()
    codigo          = (body.get("codigo") or "").strip()
    ubicacion       = (body.get("ubicacion") or "").strip() or None
    inmobiliaria_id = body.get("inmobiliaria_id")
    if not nombre or not codigo or not inmobiliaria_id:
        return Response(content="Faltan campos obligatorios", status_code=400)

    payload: Dict[str, Any] = {
        "nombre": nombre, "codigo": codigo,
        "ubicacion": ubicacion, "inmobiliaria_id": inmobiliaria_id,
    }
    _aplicar_campos_proyecto(body, payload, es_admin=_solo_admin(perfil))
    row = await _supabase_request("POST", "/Proyecto", json=payload,
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@app.patch("/api/proyectos/{proyecto_id}")
async def api_editar_proyecto(proyecto_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    payload: Dict[str, Any] = {}
    for f in ("nombre", "codigo", "ubicacion"):
        if body.get(f): payload[f] = body[f].strip()
    _aplicar_campos_proyecto(body, payload, es_admin=_solo_admin(perfil))
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    await _supabase_request("PATCH", "/Proyecto",
        params={"id": f"eq.{proyecto_id}"},
        json=payload, extra_headers={"Prefer": "return=minimal"})
    _cache_proyectos["data"] = None
    return {"ok": True}


@app.post("/api/proyectos/upload-imagen")
async def api_subir_imagen_proyecto(request: Request, file: UploadFile = File(...)):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)

    MAX_SIZE = 3 * 1024 * 1024
    contenido = await file.read()
    if len(contenido) > MAX_SIZE:
        return Response(content="La imagen supera los 3MB", status_code=400)

    mime = file.content_type or "image/jpeg"
    ext  = (file.filename or "imagen").rsplit(".", 1)[-1].lower() or "jpg"
    path = f"proyecto-{int(time.time())}.{ext}"

    supa_url = _supabase_url()
    key      = _supabase_service_role_key()
    upload_url = f"{supa_url.rstrip('/')}/storage/v1/object/proyectos-imagenes/{path}"

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(upload_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": mime,
                     "x-upsert": "true"},
            content=contenido)
        if r.status_code not in (200, 201):
            return Response(content=f"Error storage: {r.text}", status_code=500)

    public_url = f"{supa_url.rstrip('/')}/storage/v1/object/public/proyectos-imagenes/{path}"
    return {"imagen_url": public_url}


def _aplicar_campos_proyecto(body: Dict, payload: Dict, *, es_admin: bool) -> None:
    """Copia los campos opcionales del proyecto desde body → payload."""
    if es_admin and body.get("imagen_url") is not None:
        payload["imagen_url"] = body["imagen_url"] or None
    for campo in ("notas",):
        if campo in body:
            payload[campo] = (body[campo] or "").strip() or None
    for campo in ("ahorro_minimo_uf", "valor_reserva_clp", "valor_reserva_uf",
                  "valor_estacionamiento_uf"):
        if campo in body:
            try:    payload[campo] = float(body[campo]) if body[campo] not in (None, "") else None
            except: pass
    for campo in ("tiene_piloto", "estacionamiento_obligatorio", "acepta_ds19", "acepta_ds1_t23"):
        if campo in body:
            payload[campo] = bool(body[campo])


# ── Tipologia ─────────────────────────────────────────────────────────────────

_TIPOLOGIA_SELECT = "id,proyecto_id,Proyecto(nombre),nombre,dormitorios,banos,superficie_util_m2,terreno_m2,valor_uf,monto_subsidio,tipo_subsidio,estado,cuotas_ahorro,EtapaTipologia(id,stock,Etapa(id,nombre,fecha_entrega,estado))"

@app.get("/api/tipologias")
async def api_listar_tipologias(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _TIPOLOGIA_SELECT, "order": "nombre.asc"}

    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    elif inmobiliaria_id:
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"}) or []
        ids = ",".join(p["id"] for p in prows)
        if not ids: return []
        params["proyecto_id"] = f"in.({ids})"
    elif empresa_id:
        inms = await _supabase_request("GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
        inm_ids = ",".join(str(i["id"]) for i in inms)
        if not inm_ids: return []
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"}) or []
        ids = ",".join(p["id"] for p in prows)
        if not ids: return []
        params["proyecto_id"] = f"in.({ids})"

    rows = await _supabase_request("GET", "/Tipologia", params=params)
    return rows or []


_CAMPOS_TIPOLOGIA = ("dormitorios", "banos", "superficie_util_m2", "terreno_m2",
                     "valor_uf", "monto_subsidio",
                     "tipo_subsidio", "estado", "cuotas_ahorro")

@app.post("/api/tipologias")
async def api_crear_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        proyecto_id = body.get("proyecto_id")
        nombre      = (body.get("nombre") or "").strip()
        if not proyecto_id or not nombre:
            return Response(content="Faltan campos obligatorios", status_code=400)
        if not await obtener_proyecto_por_id(proyecto_id):
            return Response(content="Proyecto no encontrado", status_code=404)
        payload: Dict[str, Any] = {"proyecto_id": proyecto_id, "nombre": nombre}
        for campo in _CAMPOS_TIPOLOGIA:
            if body.get(campo) is not None:
                payload[campo] = body[campo]
        row = await _supabase_request("POST", "/Tipologia",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        logger.exception("Error en POST /api/tipologias")
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


@app.patch("/api/tipologias/{tip_id}")
async def api_editar_tipologia(tip_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        payload: Dict[str, Any] = {}
        if body.get("nombre"): payload["nombre"] = body["nombre"].strip()
        for campo in _CAMPOS_TIPOLOGIA:
            if campo in body: payload[campo] = body[campo]
        if not payload:
            return Response(content="Nada que actualizar", status_code=400)
        await _supabase_request("PATCH", "/Tipologia",
            params={"id": f"eq.{tip_id}"},
            json=payload, extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        logger.exception("Error en PATCH /api/tipologias/%s", tip_id)
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


@app.delete("/api/tipologias/{tip_id}")
async def api_eliminar_tipologia(tip_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", "/Tipologia",
        params={"id": f"eq.{tip_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


# ── Etapa ─────────────────────────────────────────────────────────────────────

_ETAPA_SELECT = "id,proyecto_id,Proyecto(nombre),nombre,descripcion,fecha_entrega,estado,tipo_ahorro,num_cuotas,EtapaTipologia(id,stock,Tipologia(id,nombre,dormitorios,banos))"
_CAMPOS_ETAPA = ("nombre", "descripcion", "fecha_entrega", "estado", "tipo_ahorro", "num_cuotas")


@app.get("/api/etapas")
async def api_listar_etapas(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _ETAPA_SELECT, "order": "fecha_entrega.asc"}

    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    elif inmobiliaria_id:
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"}) or []
        ids = ",".join(str(p["id"]) for p in prows)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"
    elif empresa_id:
        inms = await _supabase_request("GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
        inm_ids = ",".join(str(i["id"]) for i in inms)
        if not inm_ids:
            return []
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"}) or []
        ids = ",".join(str(p["id"]) for p in prows)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"

    rows = await _supabase_request("GET", "/Etapa", params=params)
    return rows or []


@app.post("/api/etapas")
async def api_crear_etapa(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        proyecto_id = body.get("proyecto_id")
        if not proyecto_id:
            return Response(content="Falta proyecto_id", status_code=400)
        if not await obtener_proyecto_por_id(proyecto_id):
            return Response(content="Proyecto no encontrado", status_code=404)
        payload: Dict[str, Any] = {"proyecto_id": proyecto_id}
        for campo in _CAMPOS_ETAPA:
            if campo in body:
                payload[campo] = body[campo] or None
        row = await _supabase_request("POST", "/Etapa",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        logger.exception("Error en POST /api/etapas")
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


@app.patch("/api/etapas/{etapa_id}")
async def api_editar_etapa(etapa_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        payload: Dict[str, Any] = {}
        for campo in _CAMPOS_ETAPA:
            if campo in body:
                payload[campo] = body[campo] or None
        if not payload:
            return Response(content="Nada que actualizar", status_code=400)
        await _supabase_request("PATCH", "/Etapa",
            params={"id": f"eq.{etapa_id}"},
            json=payload, extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        logger.exception("Error en PATCH /api/etapas/%s", etapa_id)
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


@app.delete("/api/etapas/{etapa_id}")
async def api_eliminar_etapa(etapa_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", "/Etapa",
        params={"id": f"eq.{etapa_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


# ── EtapaTipologia ────────────────────────────────────────────────────────────

_ETAPA_TIP_SELECT = "id,etapa_id,tipologia_id,stock,Etapa(id,nombre,fecha_entrega,estado),Tipologia(id,nombre,dormitorios,banos,valor_uf,tipo_subsidio)"

@app.get("/api/etapa-tipologia")
async def api_listar_etapa_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    tipologia_id = request.query_params.get("tipologia_id")
    etapa_id     = request.query_params.get("etapa_id")
    params: Dict[str, str] = {"select": _ETAPA_TIP_SELECT}
    if tipologia_id:
        params["tipologia_id"] = f"eq.{tipologia_id}"
    elif etapa_id:
        params["etapa_id"] = f"eq.{etapa_id}"
    else:
        return Response(content="Se requiere tipologia_id o etapa_id", status_code=400)
    rows = await _supabase_request("GET", "/EtapaTipologia", params=params)
    return rows or []


@app.post("/api/etapa-tipologia")
async def api_crear_etapa_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        etapa_id     = body.get("etapa_id")
        tipologia_id = body.get("tipologia_id")
        stock        = body.get("stock", 0)
        if not etapa_id or not tipologia_id:
            return Response(content="Faltan etapa_id o tipologia_id", status_code=400)
        payload = {"etapa_id": int(etapa_id), "tipologia_id": int(tipologia_id), "stock": int(stock or 0)}
        row = await _supabase_request("POST", "/EtapaTipologia",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        logger.exception("Error en POST /api/etapa-tipologia")
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


@app.patch("/api/etapa-tipologia/{et_id}")
async def api_editar_etapa_tipologia(et_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        stock = body.get("stock", 0)
        await _supabase_request("PATCH", "/EtapaTipologia",
            params={"id": f"eq.{et_id}"},
            json={"stock": int(stock or 0)},
            extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        logger.exception("Error en PATCH /api/etapa-tipologia/%s", et_id)
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


@app.delete("/api/etapa-tipologia/{et_id}")
async def api_eliminar_etapa_tipologia(et_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", "/EtapaTipologia",
        params={"id": f"eq.{et_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


# ── Ejecutivos bancarios ──────────────────────────────────────────────────────

@app.get("/api/ejecutivos")
async def api_listar_ejecutivos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request("GET", "/EjecutivoBancario",
        params={"select": "id,ejecutivo,entidad,email,telefono,disponible", "order": "ejecutivo.asc"}) or []
    return rows

@app.post("/api/ejecutivos")
async def api_crear_ejecutivo(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    payload = {
        "ejecutivo": (body.get("ejecutivo") or "").strip(),
        "entidad":   (body.get("entidad") or "").strip(),
        "email":     (body.get("email") or "").strip().lower(),
        "disponible": bool(body.get("disponible", True)),
    }
    if body.get("telefono"):
        payload["telefono"] = body["telefono"]
    if not payload["ejecutivo"] or not payload["entidad"] or not payload["email"]:
        return Response(content="Nombre, entidad y email son obligatorios", status_code=400)
    row = await _supabase_request("POST", "/EjecutivoBancario", json=payload)
    return row[0] if isinstance(row, list) else row

@app.patch("/api/ejecutivos/{ejecutivo_id}")
async def api_editar_ejecutivo(ejecutivo_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    allowed = {"ejecutivo", "entidad", "email", "telefono", "disponible"}
    payload = {k: v for k, v in body.items() if k in allowed}
    if "email" in payload:
        payload["email"] = payload["email"].strip().lower()
    if "ejecutivo" in payload:
        payload["ejecutivo"] = payload["ejecutivo"].strip()
    if "entidad" in payload:
        payload["entidad"] = payload["entidad"].strip()
    await _supabase_request("PATCH", f"/EjecutivoBancario?id=eq.{ejecutivo_id}", json=payload)
    return {"ok": True}

@app.delete("/api/ejecutivos/{ejecutivo_id}")
async def api_eliminar_ejecutivo(ejecutivo_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", f"/EjecutivoBancario?id=eq.{ejecutivo_id}")
    return {"ok": True}

# ── ProyectoEjecutivo ─────────────────────────────────────────────────────────

@app.get("/api/proyecto-ejecutivos")
async def api_listar_proyecto_ejecutivos(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id = request.query_params.get("proyecto_id")
    params: Dict[str, str] = {
        "select": "proyecto_id,ejecutivo_id,EjecutivoBancario(id,ejecutivo,email)",
    }
    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    rows = await _supabase_request("GET", "/ProyectoEjecutivo", params=params)
    return rows or []


@app.post("/api/proyecto-ejecutivos")
async def api_asignar_ejecutivo(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    proyecto_id  = body.get("proyecto_id")
    ejecutivo_id = body.get("ejecutivo_id")
    if not proyecto_id or not ejecutivo_id:
        return Response(content="Faltan campos obligatorios", status_code=400)
    proyecto = await obtener_proyecto_por_id(proyecto_id)
    if not proyecto:
        return Response(content="Proyecto no encontrado", status_code=404)
    row = await _supabase_request("POST", "/ProyectoEjecutivo",
        json={"proyecto_id": proyecto_id, "ejecutivo_id": ejecutivo_id},
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@app.delete("/api/proyecto-ejecutivos")
async def api_quitar_ejecutivo(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id  = request.query_params.get("proyecto_id")
    ejecutivo_id = request.query_params.get("ejecutivo_id")
    if not proyecto_id or not ejecutivo_id:
        return Response(content="Faltan parámetros", status_code=400)
    await _supabase_request("DELETE", "/ProyectoEjecutivo",
        params={"proyecto_id": f"eq.{proyecto_id}", "ejecutivo_id": f"eq.{ejecutivo_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


@app.get("/api/clientes")
async def api_listar_clientes(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": "*", "order": "id.desc"}

    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    elif inmobiliaria_id:
        proyectos = await _supabase_request(
            "GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"},
        ) or []
        ids = ",".join(p["id"] for p in proyectos)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"
    elif empresa_id:
        inmobiliarias = await _supabase_request(
            "GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"},
        ) or []
        inm_ids = ",".join(str(i["id"]) for i in inmobiliarias)
        if not inm_ids:
            return []
        proyectos = await _supabase_request(
            "GET", "/Proyecto",
            params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"},
        ) or []
        ids = ",".join(p["id"] for p in proyectos)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"

    usuario_filtro = request.query_params.get("usuario_id")
    if not _solo_admin(perfil):
        params["usuario_id"] = f"eq.{perfil['id']}"
    elif usuario_filtro:
        params["usuario_id"] = f"eq.{usuario_filtro}"
    rows = await _supabase_request("GET", "/Cliente", params=params) or []
    # Merge datos de Prospecto para ordenar por actividad WA y mostrar estado
    if rows:
        telefonos = [r["Telefono"] for r in rows if r.get("Telefono")]
        if telefonos:
            tel_list = ",".join(telefonos)
            prospectos = await _supabase_request("GET", "/Prospecto",
                params={"telefono_e164": f"in.({tel_list})",
                        "select": "telefono_e164,ultimo_entrante_en,pendiente_respuesta,estado,paso"}) or []
            prosp_map = {p["telefono_e164"]: p for p in prospectos}
            for r in rows:
                p = prosp_map.get(r.get("Telefono"), {})
                r["ultimo_entrante_en"]  = p.get("ultimo_entrante_en")
                r["pendiente_respuesta"] = p.get("pendiente_respuesta")
                r["prospecto_estado"]    = p.get("estado")
                r["prospecto_paso"]      = p.get("paso")
    return rows


@app.post("/api/clientes")
async def api_crear_cliente(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()

        nombre      = (body.get("Contacto") or "").strip()
        telefono    = _normalize_phone(body.get("Telefono") or "")
        proyecto_id = (body.get("proyecto_id") or "").strip()
        rut         = (body.get("Rut") or "").strip() or None
        correo      = (body.get("Correo") or "").strip() or None
        rango             = (body.get("Tramo de renta") or "").strip() or None
        num_integrantes   = body.get("numero_integrantes")
        num_integrantes   = int(num_integrantes) if num_integrantes else None
        primer_msg        = bool(body.get("primer mensaje", True))

        if not nombre:
            return Response(content="Falta Contacto", status_code=400)
        if not telefono:
            return Response(content="Falta Telefono", status_code=400)
        if not proyecto_id:
            return Response(content="Falta proyecto_id", status_code=400)

        proyecto = await obtener_proyecto_por_id(proyecto_id)
        if not proyecto:
            return Response(content="Proyecto no encontrado", status_code=400)

        # Evitar duplicados: mismo teléfono + mismo proyecto
        existente = await _supabase_request(
            "GET", "/Cliente",
            params={
                "Telefono":    f"eq.{telefono}",
                "proyecto_id": f"eq.{proyecto_id}",
                "select":      "id",
                "limit":       "1",
            },
        )
        if existente:
            return Response(
                content=f"Ya existe un cliente con ese teléfono en ese proyecto",
                status_code=409,
                media_type="text/plain",
            )

        from datetime import datetime, timezone
        fecha_hoy = datetime.now(timezone.utc).isoformat()

        cliente = await _supabase_request(
            "POST", "/Cliente",
            json={
                "proyecto_id":        proyecto_id,
                "Contacto":           nombre,
                "Rut":                rut or "",
                "Correo":             correo,
                "Telefono":           telefono,
                "Tramo de renta":     rango,
                "numero_integrantes": num_integrantes,
                "primer mensaje":     primer_msg,
                "Fecha Ult. Gestión": body.get("Fecha Ult. Gestión") or fecha_hoy,
                "usuario_id":         perfil["id"],
            },
            extra_headers={"Prefer": "return=representation"},
        )

        cliente_id_nuevo = None
        if isinstance(cliente, list) and cliente:
            cliente_id_nuevo = cliente[0].get("id")

        wa_result = None
        if primer_msg:
            await upsert_prospecto(
                telefono_e164=telefono,
                nombre=nombre,
                rut=rut,
                rango_sueldo=rango,
                proyecto_id=proyecto_id,
                estado="PLANTILLA_ENVIADA",
                paso="BIENVENIDA",
                cliente_id=cliente_id_nuevo,
            )
            logger.warning(f"Auto-envío omitido para proyecto {proyecto_id} — pendiente rediseño")

        return {"ok": True, "cliente": cliente, "wa": wa_result}

    except Exception as e:
        logger.exception("Error en /api/clientes POST")
        return Response(
            content=_safe_httpx_error(e) or "Internal Server Error",
            status_code=500,
            media_type="text/plain",
        )


@app.patch("/api/clientes/{cliente_id}")
async def api_actualizar_cliente(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    _CAMPOS_PERMITIDOS = {"recordatorio_at", "Contacto", "Correo", "Tramo de renta", "Rut", "es_nuevo", "numero_integrantes", "proyecto_id", "tipologia_id"}
    if _solo_admin(perfil):
        _CAMPOS_PERMITIDOS = _CAMPOS_PERMITIDOS | {"usuario_id"}
    try:
        body = await request.json()
        payload = {k: v for k, v in body.items() if k in _CAMPOS_PERMITIDOS}
        if not payload:
            return Response(content="Sin campos válidos", status_code=400)
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json=payload,
        )
        return {"ok": True}
    except Exception as e:
        return Response(content=_safe_httpx_error(e), status_code=500, media_type="text/plain")


# ── Inbox (prospectos sin cliente) ────────────────────────────────────────────

_INBOX_SELECT = (
    "id,telefono_e164,nombre,rut,rango_sueldo,numero_integrantes,"
    "proyecto_id,Proyecto(nombre),"
    "paso,estado,ultimo_texto_entrante,ultimo_entrante_en,actualizado_en"
)


@app.get("/api/inbox")
async def api_inbox(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)

    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")

    params: Dict[str, str] = {
        "select":     _INBOX_SELECT,
        "cliente_id": "is.null",
        "opt_out":    "neq.true",
        "order":      "ultimo_entrante_en.desc.nullslast",
        "limit":      "200",
    }

    if proyecto_id:
        params["or"] = f"(proyecto_id.eq.{proyecto_id},proyecto_id.is.null)"
    elif inmobiliaria_id:
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"}) or []
        ids = ",".join(str(p["id"]) for p in prows)
        if ids:
            params["or"] = f"(proyecto_id.in.({ids}),proyecto_id.is.null)"
        # si no hay proyectos, igual devolvemos los sin proyecto
    elif empresa_id:
        inms = await _supabase_request("GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
        inm_ids = ",".join(str(i["id"]) for i in inms)
        if inm_ids:
            prows = await _supabase_request("GET", "/Proyecto",
                params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"}) or []
            ids = ",".join(str(p["id"]) for p in prows)
            if ids:
                params["or"] = f"(proyecto_id.in.({ids}),proyecto_id.is.null)"

    rows = await _supabase_request("GET", "/Prospecto", params=params) or []
    return rows


@app.post("/api/inbox/{prospecto_id}/registrar")
async def api_registrar_desde_inbox(prospecto_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        # Obtener el prospecto
        prospectos = await _supabase_request("GET", "/Prospecto",
            params={"id": f"eq.{prospecto_id}", "select": "*", "limit": "1"}) or []
        if not prospectos:
            return Response(content="Prospecto no encontrado", status_code=404)
        p = prospectos[0]
        if p.get("cliente_id"):
            return Response(content="Ya tiene un cliente vinculado", status_code=409)

        nombre = (body.get("nombre") or p.get("nombre") or "").strip()
        if not nombre:
            return Response(content="El nombre es obligatorio", status_code=400)
        telefono = p.get("telefono_e164") or ""
        if not telefono:
            return Response(content="El prospecto no tiene teléfono", status_code=400)

        cliente_payload: Dict[str, Any] = {
            "Contacto":           nombre,
            "Telefono":           telefono,
            "Rut":                body.get("rut")      or p.get("rut")      or None,
            "Correo":             body.get("correo")   or None,
            "Tramo de renta":     body.get("rango_sueldo") or p.get("rango_sueldo") or None,
            "numero_integrantes": body.get("numero_integrantes") or p.get("numero_integrantes") or None,
            "proyecto_id":        body.get("proyecto_id") or p.get("proyecto_id") or None,
            "usuario_id":         perfil["id"],
            "es_nuevo":           True,
            "primer mensaje":     False,
        }

        nuevo = await _supabase_request("POST", "/Cliente",
            json=cliente_payload,
            extra_headers={"Prefer": "return=representation"})
        cliente_id = nuevo[0]["id"] if isinstance(nuevo, list) and nuevo else None
        if not cliente_id:
            return Response(content="Error creando cliente", status_code=500)

        await _supabase_request("PATCH", "/Prospecto",
            params={"id": f"eq.{prospecto_id}"},
            json={"cliente_id": cliente_id},
            extra_headers={"Prefer": "return=minimal"})

        return {"ok": True, "cliente_id": cliente_id}
    except Exception as e:
        logger.exception("Error en POST /api/inbox/%s/registrar", prospecto_id)
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


async def _obtener_o_crear_prospecto(cliente_id: int) -> Optional[Dict]:
    """Retorna el prospecto del cliente. Si no existe, lo crea con los datos del Cliente."""
    prospectos = await _supabase_request(
        "GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": "*", "limit": "1"},
    )
    if prospectos:
        return prospectos[0]

    # No existe — obtener datos del Cliente para crear el prospecto
    clientes = await _supabase_request(
        "GET", "/Cliente",
        params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"},
    )
    if not clientes:
        return None
    c = clientes[0]

    telefono = _normalize_phone(c.get("Telefono") or "")
    if not telefono:
        return None

    prospecto = await upsert_prospecto(
        telefono_e164=telefono,
        nombre=(c.get("Contacto") or "").strip() or None,
        rut=(c.get("Rut") or "").strip() or None,
        rango_sueldo=c.get("Tramo de renta") or None,
        proyecto_id=c.get("proyecto_id") or None,
        estado="SIN_CONTACTAR",
        cliente_id=cliente_id,
    )
    return prospecto


@app.get("/api/clientes/{cliente_id}/conversacion")
async def api_conversacion_cliente(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    prospectos = await _supabase_request(
        "GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": "id,paso,estado", "limit": "1"},
    )
    if not prospectos:
        return {"mensajes": [], "paso": None, "estado": None}
    p = prospectos[0]
    mensajes = await _supabase_request(
        "GET", "/Mensaje",
        params={
            "prospecto_id": f"eq.{p['id']}",
            "select":       "id,direccion,texto,creado_en",
            "order":        "creado_en.asc",
            "limit":        "200",
        },
    ) or []
    return {"mensajes": mensajes, "paso": p.get("paso"), "estado": p.get("estado")}


@app.get("/api/prospectos/{prospecto_id}/conversacion")
async def api_conversacion_prospecto(prospecto_id: str, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    mensajes = await _supabase_request(
        "GET", "/Mensaje",
        params={
            "prospecto_id": f"eq.{prospecto_id}",
            "select":       "id,direccion,texto,creado_en",
            "order":        "creado_en.asc",
            "limit":        "200",
        },
    ) or []
    prospectos = await _supabase_request(
        "GET", "/Prospecto",
        params={"id": f"eq.{prospecto_id}", "select": "paso,estado", "limit": "1"},
    ) or []
    p = prospectos[0] if prospectos else {}
    return {"mensajes": mensajes, "paso": p.get("paso"), "estado": p.get("estado")}


@app.get("/api/clientes/{cliente_id}/contexto")
async def api_contexto_cliente(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    select_fields = ",".join([
        "paso", "estado", "datos",
        *_CAMPOS_CALIFICACION,
        "motivo_no_interesado", "motivo_no_califica",
        "quiere_contacto_ejecutivo", "intencion_regularizar",
        "fecha_tentativa_recontacto",
    ])
    prospectos = await _supabase_request(
        "GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": select_fields, "limit": "1"},
    ) or []
    if not prospectos:
        return {"tiene_prospecto": False}
    return {"tiene_prospecto": True, **prospectos[0]}


@app.get("/api/clientes/{cliente_id}/documentos")
async def api_documentos_cliente(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        prospectos = await _supabase_request(
            "GET", "/Prospecto",
            params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"},
        )
        if not prospectos:
            return []
        prospecto_id = prospectos[0]["id"]
        docs = await _supabase_request(
            "GET", "/Documento",
            params={
                "prospecto_id": f"eq.{prospecto_id}",
                "select": "id,tipo,nombre_archivo,url_storage,verificado,creado_en",
                "order": "creado_en.desc",
            },
        )
        return docs or []
    except Exception as e:
        logger.exception("Error en /api/clientes/%s/documentos", cliente_id)
        return Response(content=_safe_httpx_error(e), status_code=500, media_type="text/plain")


TIPOS_VALIDOS = {
    "liquidacion_sueldo", "certificado_afp", "carnet_identidad",
    "antiguedad_laboral", "libreta_ahorro", "informe_deudas", "otro",
}

@app.post("/api/clientes/{cliente_id}/documentos/upload")
async def api_upload_documento(
    cliente_id: int,
    request: Request,
    file: UploadFile = File(...),
    tipo: str = Form(...),
):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    if tipo not in TIPOS_VALIDOS:
        return Response(content=f"Tipo inválido: {tipo}", status_code=400)

    try:
        prospecto = await _obtener_o_crear_prospecto(cliente_id)
        if not prospecto:
            return Response(content="Cliente no encontrado o sin teléfono", status_code=404)

        prospecto_id = prospecto["id"]
        file_bytes   = await file.read()
        mime_type    = file.content_type or "application/octet-stream"

        # Nombre único para evitar colisiones
        ts            = int(datetime.now(timezone.utc).timestamp() * 1000)
        nombre_seguro = file.filename.replace(" ", "_") if file.filename else f"doc_{ts}"
        nombre_final  = f"{tipo}_{ts}_{nombre_seguro}"

        url_storage = await subir_a_storage(
            file_bytes=file_bytes,
            nombre_archivo=nombre_final,
            prospecto_id=prospecto_id,
            mime_type=mime_type,
        )

        doc = await insertar_documento(
            prospecto_id=prospecto_id,
            tipo=tipo,
            nombre_archivo=file.filename or nombre_final,
            url_storage=url_storage,
            mime_type=mime_type,
        )

        return {"ok": True, "documento": doc, "prospecto_id": prospecto_id}

    except Exception as e:
        logger.exception("Error en upload documento cliente %s", cliente_id)
        return Response(content=_safe_httpx_error(e) or "Error al subir documento", status_code=500, media_type="text/plain")


@app.get("/api/clientes/{cliente_id}/documentos/extras/zip")
async def api_descargar_extras_zip(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        prospectos = await _supabase_request("GET", "/Prospecto",
            params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"})
        if not prospectos:
            return Response(content="Sin prospecto", status_code=404)
        prospecto_id = prospectos[0]["id"]
        docs = await _supabase_request("GET", "/Documento",
            params={"prospecto_id": f"eq.{prospecto_id}", "tipo": "eq.otro",
                    "select": "id,nombre_archivo,url_storage,mime_type"}) or []
        if not docs:
            return Response(content="No hay documentos extra", status_code=404)

        zip_buffer = io.BytesIO()
        import zipfile
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for doc in docs:
                try:
                    file_bytes = await _descargar_documento_storage(doc["url_storage"])
                    arcname = re.sub(r'[^\w\-_\. ]', '_', doc.get("nombre_archivo") or f"extra_{doc['id']}")
                    zf.writestr(arcname, file_bytes)
                except Exception:
                    logger.warning("No se pudo descargar extra %s", doc.get("id"))
        zip_buffer.seek(0)
        return Response(
            content=zip_buffer.read(),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=\"extras.zip\""},
        )
    except Exception as e:
        logger.exception("Error descargando extras zip cliente %s", cliente_id)
        return Response(content=str(e), status_code=500, media_type="text/plain")


@app.get("/api/clientes/{cliente_id}/documentos/{doc_id}/descargar")
async def api_descargar_documento(cliente_id: int, doc_id: str, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        # Verificar que el doc pertenece al cliente correcto
        prospectos = await _supabase_request("GET", "/Prospecto",
            params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"})
        prospecto_id = prospectos[0]["id"] if prospectos else None
        docs = await _supabase_request("GET", "/Documento",
            params={
                "id": f"eq.{doc_id}",
                **({"prospecto_id": f"eq.{prospecto_id}"} if prospecto_id else {}),
                "select": "id,nombre_archivo,url_storage,mime_type",
                "limit": "1",
            })
        if not docs:
            return Response(content="Documento no encontrado", status_code=404)
        doc = docs[0]
        file_bytes = await _descargar_documento_storage(doc["url_storage"])
        mime = doc.get("mime_type") or "application/octet-stream"
        nombre = re.sub(r'[^\w\-_\. ]', '_', doc.get("nombre_archivo") or "documento")
        return Response(
            content=file_bytes,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
        )
    except Exception as e:
        logger.exception("Error descargando documento %s", doc_id)
        return Response(content=str(e), status_code=500, media_type="text/plain")


async def _descargar_documento_storage(url_storage: str) -> bytes:
    key = _supabase_service_role_key()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url_storage, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
    return r.content


async def _enviar_email_evaluacion(cliente_id: int, usuario: dict | None = None) -> dict:
    if not os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON no configurada")

    clientes = await _supabase_request("GET", "/Cliente",
        params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
    if not clientes:
        raise ValueError(f"Cliente {cliente_id} no encontrado")
    c = clientes[0]

    ejecutivos = await _supabase_request("GET", "/EjecutivoBancario",
        params={"disponible": "eq.true", "select": "email,ejecutivo"})
    destinatarios = [e["email"] for e in (ejecutivos or []) if e.get("email")]
    if not destinatarios:
        raise ValueError("No hay ejecutivos disponibles con email configurado")

    prospectos = await _supabase_request("GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"})
    docs = []
    if prospectos:
        docs = await _supabase_request("GET", "/Documento",
            params={
                "prospecto_id": f"eq.{prospectos[0]['id']}",
                "select": "tipo,nombre_archivo,url_storage,mime_type",
            }) or []

    nombre   = (c.get("Contacto") or "Sin nombre").strip()
    rut      = c.get("Rut") or "No registrado"
    telefono = c.get("Telefono") or "No registrado"
    renta    = c.get("Tramo de renta") or "No registrado"

    proyecto_nombre = "No registrado"
    proyecto_id_raw = c.get("proyecto_id")
    if proyecto_id_raw:
        proy_rows = await _supabase_request("GET", "/Proyecto",
            params={"id": f"eq.{proyecto_id_raw}", "select": "nombre", "limit": "1"})
        if proy_rows:
            proyecto_nombre = proy_rows[0].get("nombre") or "No registrado"

    NOMBRES_TIPO = {
        "liquidacion_sueldo": "Liquidación de sueldo",
        "certificado_afp": "Certificado AFP",
        "carnet_identidad": "Cédula de identidad",
        "libreta_ahorro": "Libreta de ahorro",
        "informe_deudas": "Informe de deudas",
        "antiguedad_laboral": "Antigüedad laboral",
        "otro": "Otro documento",
    }

    conteo_docs: Dict[str, int] = {}
    for d in docs:
        tipo = d.get("tipo") or "otro"
        conteo_docs[tipo] = conteo_docs.get(tipo, 0) + 1

    PLURAL_TIPO = {
        "liquidacion_sueldo": ("liquidación de sueldo", "liquidaciones de sueldo"),
        "certificado_afp": ("certificado AFP", "certificados AFP"),
        "carnet_identidad": ("cédula de identidad", "cédulas de identidad"),
        "libreta_ahorro": ("libreta de ahorro", "libretas de ahorro"),
        "informe_deudas": ("informe de deudas", "informes de deudas"),
        "antiguedad_laboral": ("certificado de antigüedad laboral", "certificados de antigüedad laboral"),
        "otro": ("documento adicional", "documentos adicionales"),
    }
    partes_docs = []
    for tipo, cant in conteo_docs.items():
        sing, plur = PLURAL_TIPO.get(tipo, (tipo, tipo))
        partes_docs.append(f"{cant} {sing if cant == 1 else plur}")

    resumen_docs = (
        "Se adjuntan: " + ", ".join(partes_docs) + "."
        if partes_docs else "No se adjuntan documentos."
    )

    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;">
      <h2 style="color:#1e3a5f;border-bottom:2px solid #1e3a5f;padding-bottom:8px;">
        Solicitud de Evaluacion de Credito
      </h2>
      <table style="border-collapse:collapse;font-size:14px;width:100%;">
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;width:160px;">Nombre</td>
          <td style="padding:8px 12px;">{nombre}</td>
        </tr>
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">RUT</td>
          <td style="padding:8px 12px;">{rut}</td>
        </tr>
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Telefono</td>
          <td style="padding:8px 12px;">{telefono}</td>
        </tr>
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Proyecto</td>
          <td style="padding:8px 12px;">{proyecto_nombre}</td>
        </tr>
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Tramo de renta</td>
          <td style="padding:8px 12px;">{renta}</td>
        </tr>
      </table>
      <h3 style="color:#1e3a5f;margin-top:24px;">Documentos adjuntos ({len(docs)})</h3>
      <p style="font-size:13px;color:#444;margin:4px 0 0;">{resumen_docs}</p>
      <p style="font-size:12px;color:#aaa;margin-top:24px;">
        Generado automaticamente por CRM QueSubsidio.
      </p>
    </div>
    """

    # Alias del usuario que dispara el envío
    alias = (usuario or {}).get("email_alias") or os.getenv("EMAIL_ADMIN", "")
    nombre_usuario = (usuario or {}).get("nombre") or "QueSubsidio"
    email_from = f"{nombre_usuario} <{alias}>" if "@" in alias and "<" not in alias else alias

    # Descargar adjuntos
    adjuntos: List[Dict] = []
    for doc in docs:
        url            = doc.get("url_storage")
        nombre_archivo = doc.get("nombre_archivo") or doc.get("tipo") or "documento"
        if not url:
            continue
        try:
            file_bytes = await _descargar_documento_storage(url)
            mime_type  = doc.get("mime_type") or "application/octet-stream"
            adjuntos.append({"filename": nombre_archivo, "content": file_bytes, "content_type": mime_type})
        except Exception as e:
            logger.warning("No se pudo adjuntar '%s': %s", nombre_archivo, e)

    logger.info("Gmail: enviando evaluación de %s a %d destinatarios (%d adjuntos)", nombre, len(destinatarios), len(adjuntos))
    for destinatario in destinatarios:
        await _gmail_send(
            from_addr=email_from,
            to=[destinatario],
            subject=f"Evaluacion de credito - {nombre}",
            html=body_html,
            attachments=adjuntos,
        )
        logger.info("Gmail: evaluación enviada a %s", destinatario)
    logger.info("Gmail: evaluación enviada correctamente a todos los destinatarios")

    return {"enviado_a": destinatarios, "documentos_adjuntos": len(adjuntos)}


@app.get("/api/clientes/{cliente_id}/preview-evaluacion")
async def api_preview_evaluacion(cliente_id: int, request: Request):
    """Devuelve los datos que se incluirán en el correo de evaluación, sin enviarlo."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)

    clientes = await _supabase_request("GET", "/Cliente",
        params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
    if not clientes:
        return Response(content="Cliente no encontrado", status_code=404)
    c = clientes[0]

    ejecutivos = await _supabase_request("GET", "/EjecutivoBancario",
        params={"disponible": "eq.true", "select": "email,ejecutivo"}) or []
    destinatarios = [e["email"] for e in ejecutivos if e.get("email")]

    prospectos = await _supabase_request("GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"}) or []
    docs = []
    if prospectos:
        docs = await _supabase_request("GET", "/Documento",
            params={"prospecto_id": f"eq.{prospectos[0]['id']}",
                    "select": "tipo,nombre_archivo"}) or []

    proyecto_nombre = "No registrado"
    if c.get("proyecto_id"):
        proy = await _supabase_request("GET", "/Proyecto",
            params={"id": f"eq.{c['proyecto_id']}", "select": "nombre", "limit": "1"})
        if proy:
            proyecto_nombre = proy[0].get("nombre") or "No registrado"

    NOMBRES_TIPO = {
        "liquidacion_sueldo": "Liquidación de sueldo",
        "certificado_afp": "Certificado AFP",
        "carnet_identidad": "Cédula de identidad",
        "libreta_ahorro": "Libreta de ahorro",
        "informe_deudas": "Informe de deudas",
        "antiguedad_laboral": "Antigüedad laboral",
        "otro": "Otro documento",
    }
    from collections import Counter
    conteo = Counter(d.get("tipo", "otro") for d in docs)
    docs_resumen = [
        {"tipo": NOMBRES_TIPO.get(t, t), "cantidad": n}
        for t, n in conteo.items()
    ]

    alias = perfil.get("email_alias") or os.getenv("EMAIL_ADMIN", "")
    return {
        "remitente": alias,
        "destinatarios": destinatarios,
        "cliente": {
            "nombre":   (c.get("Contacto") or "Sin nombre").strip(),
            "rut":      c.get("Rut") or "—",
            "telefono": c.get("Telefono") or "—",
            "proyecto": proyecto_nombre,
            "renta":    c.get("Tramo de renta") or "—",
        },
        "documentos": docs_resumen,
        "total_docs": len(docs),
    }


@app.post("/api/clientes/{cliente_id}/enviar-evaluacion")
async def api_enviar_evaluacion(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        result = await _enviar_email_evaluacion(cliente_id, usuario=perfil)
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("Error enviando evaluación cliente %s", cliente_id)
        return Response(content=str(e), status_code=500, media_type="text/plain")


# ── Recordatorio WA al ejecutivo ─────────────────────────────────────────────

TEMPLATE_RECORDATORIO_WA = _get_env("TEMPLATE_RECORDATORIO_WA", "TEMPLATE_RECORDATORIO_WA") or "recordatorio_cliente"

@app.post("/api/clientes/{cliente_id}/recordatorio-wa")
async def api_recordatorio_wa(cliente_id: int, request: Request):
    """Envía una plantilla WA al número personal (celular) del ejecutivo
    recordándole contactar a un cliente específico."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    celular = _normalize_phone(perfil.get("celular") or "")
    if not celular:
        return Response(content="Tu perfil no tiene número celular configurado", status_code=400)
    try:
        body   = await request.json()
        motivo = (body.get("motivo") or "Sin motivo especificado").strip()
        rows   = await _supabase_request("GET", "/Cliente",
            params={"id": f"eq.{cliente_id}", "select": "Contacto,Telefono", "limit": "1"})
        if not rows:
            return Response(content="Cliente no encontrado", status_code=404)
        c       = rows[0]
        nombre  = (c.get("Contacto") or "—").strip()
        telefono_cliente = _normalize_phone(c.get("Telefono") or "") or (c.get("Telefono") or "—")
        await send_whatsapp_template(
            to=celular,
            template_name=TEMPLATE_RECORDATORIO_WA,
            language_code="es_CL",
            body_text_params=[nombre, telefono_cliente, motivo],
        )
        return {"ok": True}
    except Exception as e:
        logger.exception("Error enviando recordatorio WA cliente %s", cliente_id)
        return Response(content=str(e), status_code=500, media_type="text/plain")


# ── Movendo / Zonapropia Integration ─────────────────────────────────────────

def _movendo_webhook_key() -> Optional[str]:
    return _get_env("MOVENDO_WEBHOOK_KEY")

def _verificar_movendo_auth(request: Request) -> bool:
    key = _movendo_webhook_key()
    if not key:
        return True  # Sin clave configurada → aceptar (desarrollo)
    auth = request.headers.get("Authorization", "")
    return auth.lower().startswith("bearer ") and auth[7:].strip() == key

def _normalizar_telefono_cl(phone_raw: str) -> Optional[str]:
    """Normaliza a formato E.164 chileno: 56XXXXXXXXX."""
    digits = _normalize_phone(phone_raw)
    if not digits:
        return None
    if digits.startswith("56") and len(digits) == 11:
        return digits
    if digits.startswith("9") and len(digits) == 9:
        return f"56{digits}"
    if digits.startswith("09") and len(digits) == 10:
        return f"56{digits[1:]}"
    return digits if len(digits) >= 10 else None


@app.post("/api/movendo/nuevo-cliente")
async def api_movendo_nuevo_cliente(request: Request):
    """Recibe un nuevo lead de Movendo y lo registra como Cliente."""
    if not _verificar_movendo_auth(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()

        first     = (body.get("firstName") or "").strip()
        last      = (body.get("lastName") or "").strip()
        nombre    = f"{first} {last}".strip() or (body.get("phone") or "Sin nombre")
        phone_raw = (body.get("phone") or "").strip()
        crm_id      = str(body.get("crmId") or "").strip() or None
        proj_name   = (body.get("projectName") or "").strip()
        movendo_pid = body.get("projectId")  # ID numérico del proyecto en Movendo
        email       = (body.get("email") or "").strip() or None
        rut         = (body.get("rut") or "").strip() or None
        salary      = str(body.get("salary") or "").strip() or None
        source      = (body.get("source") or "").strip() or None

        if not phone_raw:
            return Response(content="phone es requerido", status_code=400)
        telefono = _normalizar_telefono_cl(phone_raw)
        if not telefono:
            return Response(content=f"Teléfono inválido: {phone_raw}", status_code=400)
        if not proj_name and movendo_pid is None:
            return Response(content="projectName o projectId es requerido", status_code=400)

        # Buscar proyecto — orden de prioridad:
        # 1) ID numérico de Movendo (movendo_proyecto_id)
        # 2) nombre ilike
        # 3) codigo ilike
        # 4) prefijo o alias en nombres_csv (maneja "Viñedos de Rengo II" → "Viñedos de Rengo")
        _sel_proy = "id,nombre,imagen_url,nombres_csv"
        proyectos = []

        if movendo_pid is not None:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"movendo_proyecto_id": f"eq.{movendo_pid}", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"nombre": f"ilike.%{proj_name}%", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"codigo": f"ilike.%{proj_name}%", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            todos = await _supabase_request("GET", "/Proyecto", params={"select": _sel_proy}) or []
            proj_lower = proj_name.lower().strip()
            todos_sorted = sorted(todos, key=lambda p: len(p.get("nombre") or ""), reverse=True)
            for p in todos_sorted:
                db_nombre = (p.get("nombre") or "").lower().strip()
                # nombres_csv puede ser lista (array PG) o string separado por comas
                raw_csv = p.get("nombres_csv") or []
                if isinstance(raw_csv, list):
                    aliases = [a.strip().lower() for a in raw_csv if a.strip()]
                else:
                    aliases = [a.strip().lower() for a in str(raw_csv).split(",") if a.strip()]
                if proj_lower in aliases:
                    proyectos = [p]; break
                if db_nombre and proj_lower.startswith(db_nombre):
                    proyectos = [p]; break

        if not proyectos:
            ref = f"projectId={movendo_pid}" if movendo_pid is not None else f"projectName={proj_name}"
            return Response(content=f"Proyecto no encontrado: {ref}", status_code=422,
                            media_type="text/plain")

        proyecto_row      = proyectos[0]
        proyecto_id       = proyecto_row["id"]
        proyecto_nombre   = proyecto_row["nombre"]
        template_auto     = proyecto_row.get("template_bienvenida") or None

        # Evitar duplicados (mismo teléfono + mismo proyecto)
        dup = await _supabase_request("GET", "/Cliente",
            params={"Telefono": f"eq.{telefono}", "proyecto_id": f"eq.{proyecto_id}",
                    "select": "id", "limit": "1"}) or []
        if dup:
            return {"ok": True, "duplicado": True, "cliente_id": dup[0]["id"],
                    "mensaje": "Cliente ya existe en este proyecto"}

        default_user = await _get_default_user()
        payload: Dict[str, Any] = {
            "proyecto_id":        proyecto_id,
            "Contacto":           nombre,
            "Telefono":           telefono,
            "Fecha Ult. Gestión": _utc_now_iso(),
            "primer mensaje":     False,
            "es_nuevo":           True,
        }
        if default_user: payload["usuario_id"]      = default_user["id"]
        if email:       payload["Correo"]           = email
        if rut:         payload["Rut"]              = rut
        if salary:      payload["Tramo de renta"]   = salary
        if crm_id:      payload["movendo_id"]       = crm_id
        if source:      payload["movendo_source"]   = source

        cliente = await _supabase_request("POST", "/Cliente", json=payload,
            extra_headers={"Prefer": "return=representation"})
        cliente_id = cliente[0]["id"] if isinstance(cliente, list) and cliente else None

        # Auto-enviar plantilla de bienvenida si el proyecto la tiene configurada
        wa_result = None
        if template_auto and cliente_id:
            try:
                # Construir parámetros usando PlantillaConfig (soporta named params)
                _wa_cfg = await _supabase_request("GET", "/PlantillaConfig",
                    params={"template_name": f"eq.{template_auto}",
                            "select": "variables,param_names", "limit": "1"}) or []
                _pool = await _pool_plantilla(nombre, proyecto_row)
                if _wa_cfg and _wa_cfg[0].get("variables"):
                    _btp = _build_body_params(_pool, _wa_cfg[0]["variables"], _wa_cfg[0].get("param_names"))
                else:
                    _btp = [nombre, proyecto_nombre]  # fallback básico
                wa_result = await send_whatsapp_template(
                    to=telefono,
                    template_name=template_auto,
                    language_code="es",
                    body_text_params=_btp,
                    image_url=proyecto_row.get("imagen_url") or None,
                )
                ahora_movendo = datetime.now(timezone.utc)
                patch_mov: Dict[str, Any] = {
                    "primer_wtsp_en": ahora_movendo.isoformat(),
                    "Fecha Ult. Gestión": ahora_movendo.isoformat(),
                }
                patch_mov["recordatorio_at"] = (ahora_movendo + timedelta(hours=24)).isoformat()
                await _supabase_request("PATCH", "/Cliente",
                    params={"id": f"eq.{cliente_id}"}, json=patch_mov)
                await upsert_prospecto(
                    telefono_e164=telefono,
                    nombre=nombre,
                    rut=rut,
                    rango_sueldo=salary,
                    proyecto_id=proyecto_id,
                    estado="PLANTILLA_ENVIADA",
                    paso="BIENVENIDA",
                    cliente_id=cliente_id,
                )
                logger.info(f"Movendo auto-template '{template_auto}' enviado a {telefono}")
            except Exception as wa_err:
                logger.warning(f"Movendo: cliente creado pero fallo envío template: {wa_err}")

        logger.info(f"Movendo nuevo cliente: {nombre} ({telefono}) → {proyecto_nombre}")
        asyncio.create_task(_notificar_nuevo_lead(
            nombre=nombre, telefono=telefono, proyecto_nombre=proyecto_nombre,
            correo_admin=default_user.get("correo") if default_user else None,
        ))
        return {"ok": True, "cliente_id": cliente_id}

    except Exception as e:
        logger.exception("Error en /api/movendo/nuevo-cliente")
        return Response(content=_safe_httpx_error(e) or "Internal Server Error", status_code=500,
                        media_type="text/plain")


@app.post("/api/aplicaciones/actualizar-contactos-quesubsidio")
async def api_movendo_actualizar_contacto(request: Request):
    """Recibe el resumen de conversación de Movendo y actualiza el Cliente."""
    if not _verificar_movendo_auth(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        lead_id = body.get("leadId")
        if not lead_id:
            return Response(content="leadId es requerido", status_code=400)
        lead_id_str = str(lead_id)

        # Buscar por movendo_id
        rows = await _supabase_request("GET", "/Cliente",
            params={"movendo_id": f"eq.{lead_id_str}", "select": "id", "limit": "1"}) or []

        # Si no encontró, intentar por teléfono
        if not rows and body.get("phone"):
            tel = _normalizar_telefono_cl(str(body["phone"]))
            if tel:
                rows = await _supabase_request("GET", "/Cliente",
                    params={"Telefono": f"eq.{tel}", "select": "id", "limit": "1"}) or []

        if not rows:
            return Response(content=f"Cliente no encontrado para leadId={lead_id}", status_code=404,
                            media_type="text/plain")

        cliente_id = rows[0]["id"]
        patch: Dict[str, Any] = {"movendo_id": lead_id_str}

        nombre_parts = [body.get("name") or "", body.get("lastName") or "", body.get("motherLastName") or ""]
        nombre = " ".join(p for p in nombre_parts if p).strip()
        if nombre:             patch["Contacto"]        = nombre
        if body.get("email"):  patch["Correo"]          = body["email"]
        if body.get("rut"):    patch["Rut"]             = body["rut"]
        if body.get("salary"): patch["Tramo de renta"]  = str(body["salary"])

        movendo_data: Dict[str, Any] = {}
        for key in ("summary", "chatLink", "score", "userNotes", "source",
                    "alertType", "alertMessage", "complementSalary", "projectId"):
            if body.get(key) is not None:
                movendo_data[key] = body[key]
        if movendo_data:
            patch["movendo_data"] = movendo_data

        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json=patch, extra_headers={"Prefer": "return=minimal"})

        logger.info(f"Movendo actualizó cliente_id={cliente_id} leadId={lead_id}")
        return {"ok": True, "cliente_id": cliente_id}

    except Exception as e:
        logger.exception("Error en /api/aplicaciones/actualizar-contactos-quesubsidio")
        return Response(content=_safe_httpx_error(e) or "Internal Server Error", status_code=500,
                        media_type="text/plain")


async def _get_default_user() -> Optional[Dict]:
    """Devuelve el usuario admin por defecto {id, correo, nombre}."""
    env_id = _get_env("MOVENDO_DEFAULT_USER_ID")
    params = {"select": "id,correo,nombre", "limit": "1"}
    if env_id:
        params["id"] = f"eq.{env_id}"
    else:
        params["rol"]    = "in.(owner,administrador)"
        params["estado"] = "eq.1"
    rows = await _supabase_request("GET", "/Usuario", params=params) or []
    return rows[0] if rows else None



async def _notificar_interesado(prospecto_id: str) -> None:
    """Avisa al admin/ejecutivo asignado cuando un prospecto confirma interés (llega a DOCUMENTACION)."""
    try:
        rows = await _supabase_request("GET", "/Prospecto",
            params={"id": f"eq.{prospecto_id}", "select": "nombre,telefono_e164,cliente_id", "limit": "1"}) or []
        if not rows:
            return
        p = rows[0]
        nombre   = p.get("nombre") or "Cliente"
        telefono = p.get("telefono_e164") or ""
        # Buscar proyecto desde el cliente si existe
        proyecto_nombre = ""
        if p.get("cliente_id"):
            cli = await _supabase_request("GET", "/Cliente",
                params={"id": f"eq.{p['cliente_id']}", "select": "proyecto_id", "limit": "1"}) or []
            if cli and cli[0].get("proyecto_id"):
                proy = await obtener_proyecto_por_id(cli[0]["proyecto_id"])
                proyecto_nombre = (proy or {}).get("nombre", "")

        texto = (
            f"🔥 *Cliente listo para contactar*\n"
            f"👤 {nombre}\n"
            f"📱 {telefono}\n"
            f"🏗️ {proyecto_nombre or 'Sin proyecto'}\n"
            f"✅ Confirmó interés y está reuniendo documentos."
        )
        notify_phone = _get_env("ADMIN_NOTIFY_PHONE")
        if notify_phone:
            try:
                await send_whatsapp_message(to=notify_phone, text=texto)
            except Exception as e:
                logger.warning("Notificación interesado WA fallida: %s", e)
    except Exception:
        logger.exception("Error en _notificar_interesado")


async def _notificar_nuevo_lead(nombre: str, telefono: str, proyecto_nombre: str, correo_admin: Optional[str]) -> None:
    """Envía aviso por WhatsApp y correo al admin cuando llega un nuevo lead de Movendo."""
    texto = (
        f"🆕 *Nuevo lead recibido*\n"
        f"👤 {nombre}\n"
        f"📱 {telefono}\n"
        f"🏗️ Proyecto: {proyecto_nombre}"
    )
    notify_phone = _get_env("ADMIN_NOTIFY_PHONE")
    if notify_phone:
        try:
            await send_whatsapp_message(to=notify_phone, text=texto)
        except Exception as e:
            logger.warning("Notificación WA fallida: %s", e)

    if correo_admin:
        try:
            body_html = f"""
            <div style="font-family:Arial,sans-serif;max-width:500px;">
              <h2 style="color:#1e3a5f;border-bottom:2px solid #1e3a5f;padding-bottom:8px;">Nuevo lead recibido</h2>
              <table style="font-size:14px;width:100%;border-collapse:collapse;">
                <tr><td style="padding:8px 12px;font-weight:bold;color:#555;width:120px;">Nombre</td>
                    <td style="padding:8px 12px;">{nombre}</td></tr>
                <tr style="background:#f5f7fa;">
                    <td style="padding:8px 12px;font-weight:bold;color:#555;">Teléfono</td>
                    <td style="padding:8px 12px;">{telefono}</td></tr>
                <tr><td style="padding:8px 12px;font-weight:bold;color:#555;">Proyecto</td>
                    <td style="padding:8px 12px;">{proyecto_nombre}</td></tr>
              </table>
              <p style="font-size:12px;color:#aaa;margin-top:20px;">Generado automáticamente por CRM QueSubsidio.</p>
            </div>"""
            email_from = os.getenv("EMAIL_ADMIN", "")
            if not email_from:
                raise RuntimeError("EMAIL_ADMIN no configurada")
            await _gmail_send(
                from_addr=email_from,
                to=[correo_admin],
                subject=f"Nuevo lead — {nombre}",
                html=body_html,
            )
        except Exception as e:
            logger.warning("Notificación email fallida: %s", e)


async def _movendo_get_token() -> str:
    """Obtiene un Bearer token de Movendo via OAuth2 Password Grant.
    Usar cuando necesitemos llamar a la API de Movendo (enviar datos hacia ellos).
    """
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://autentica-api.movendo.cl/oauth/token",
            json={
                "grant_type":    "password",
                "client_id":     _get_env("MOVENDO_CLIENT_ID")     or "7",
                "client_secret": _get_env("MOVENDO_CLIENT_SECRET") or "",
                "username":      _get_env("MOVENDO_USERNAME")       or "",
                "password":      _get_env("MOVENDO_PASSWORD")       or "",
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


# ── Landing pública ───────────────────────────────────────────────────────────
@app.get("/")
async def page_home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "active": "inicio",
        "page_title": "QueSubsidio — Subsidios Habitacionales en Chile",
        "page_description": "Comprar tu propiedad con subsidio aún es posible. QueSubsidio acerca la vivienda a más familias chilenas con información clara sobre DS19, DS49 y DS1 T2/T3.",
        "page_path": "/",
        "supabase_needed": True,
    })

@app.get("/como-funciona")
async def page_como_funciona(request: Request):
    return templates.TemplateResponse(request, "como-funciona.html", {
        "active": "como-funciona",
        "page_title": "Cómo funciona — QueSubsidio",
        "page_description": "Conoce el proceso paso a paso para postular a un subsidio habitacional en Chile con la ayuda de QueSubsidio.",
        "page_path": "/como-funciona",
    })

@app.get("/subsidios")
async def page_subsidios(request: Request):
    return templates.TemplateResponse(request, "subsidios.html", {
        "active": "subsidios",
        "page_title": "Subsidios Habitacionales — QueSubsidio",
        "page_description": "Conoce todos los subsidios disponibles: DS19, DS49, DS1 T2/T3 y más. Encuentra el que se ajusta a tu situación.",
        "page_path": "/subsidios",
    })

@app.get("/viviendas")
async def page_viviendas(request: Request):
    return templates.TemplateResponse(request, "viviendas.html", {
        "active": "viviendas",
        "page_title": "Viviendas con Subsidio — QueSubsidio",
        "page_description": "Explora proyectos inmobiliarios disponibles con subsidio habitacional en todo Chile.",
        "page_path": "/viviendas",
    })

@app.get("/blog")
async def page_blog(request: Request):
    return templates.TemplateResponse(request, "blog.html", {
        "active": "blog",
        "page_title": "Blog — QueSubsidio",
        "page_description": "Artículos, guías y novedades sobre subsidios habitacionales y el mercado inmobiliario chileno.",
        "page_path": "/blog",
    })

@app.get("/api/proyectos-landing")
async def api_proyectos_landing():
    """Devuelve proyectos públicos para el carrusel de la landing (sin autenticación)."""
    now = time.time()
    if _cache_proyectos["data"] and now - _cache_proyectos["ts"] < _CACHE_TTL:
        return _cache_proyectos["data"]

    rows = await _supabase_request("GET", "/Proyecto", params={
        "select": "id,nombre,imagen_url,ubicacion",
        "order": "nombre.asc",
        "limit": "12",
    }) or []
    proyectos = [
        {
            "id":      r.get("id"),
            "nombre":  r.get("nombre") or "Proyecto",
            "imagen":  r.get("imagen_url") or None,
            "ubicacion": r.get("ubicacion") or "",
        }
        for r in rows
        if r.get("nombre")
    ]
    result = {"proyectos": proyectos}
    _cache_proyectos["data"] = result
    _cache_proyectos["ts"]   = now
    return result

@app.post("/api/contacto")
async def api_contacto(request: Request):
    """Recibe formulario de contacto de la landing y notifica por email."""
    ip = request.client.host if request.client else "unknown"
    if not _rate_limit_ok(f"contacto:{ip}", max_req=5, window=60):
        return Response(content="Demasiadas solicitudes, intenta en un minuto", status_code=429)

    body = await request.json()
    nombre   = (body.get("nombre")   or "").strip()
    correo   = (body.get("correo")   or "").strip()
    telefono = (body.get("telefono") or "").strip()
    empresa  = (body.get("empresa")  or "").strip()
    mensaje  = (body.get("mensaje")  or "").strip()

    if not nombre or not correo:
        return Response(content="nombre y correo son requeridos", status_code=422)

    logger.info("📩 Contacto landing | %s | %s | %s", nombre, correo, empresa)

    destino = os.getenv("EMAIL_ADMIN")
    if destino:
        try:
            cuerpo = f"""<h2>Nuevo contacto desde la landing</h2>
<p><b>Nombre:</b> {nombre}<br>
<b>Correo:</b> {correo}<br>
<b>Teléfono:</b> {telefono or '—'}<br>
<b>Empresa:</b> {empresa or '—'}</p>
<p><b>Mensaje:</b><br>{mensaje or '—'}</p>"""
            await _gmail_send(
                from_addr=os.getenv("EMAIL_ADMIN", ""),
                to=[destino],
                subject=f"[QueSubsidio] Contacto desde la landing — {nombre}",
                html=cuerpo,
            )
        except Exception as exc:
            logger.warning("No se pudo enviar email de contacto: %s", exc)

    return {"ok": True}


# ── Formulario de perfilamiento público (sin auth) ───────────────────────────
_FORMULARIO_CAMPOS = (
    "nombre", "email", "telefono", "rsh", "cmf", "tipo_ingreso",
    "antiguedad_meses", "tiene_liquidaciones", "tiene_cotizaciones_afp",
    "anos_declaracion_renta", "tiene_carpeta_tributaria",
    "tiene_boletas_honorarios", "tiene_cert_antiguedad",
    "tiene_propiedad", "numero_integrantes",
    "resultado", "paso_actual", "completado_at",
)

@app.post("/api/formulario")
async def api_crear_formulario(request: Request):
    body = await request.json()
    payload = {k: v for k, v in body.items() if k in _FORMULARIO_CAMPOS and v is not None}
    try:
        row = await _supabase_request(
            "POST", "/Formulario",
            json=payload,
            extra_headers={"Prefer": "return=representation"},
        )
        return row[0] if isinstance(row, list) and row else row
    except Exception as exc:
        logger.warning("Error creando Formulario: %s", exc)
        return Response(content="Error al crear formulario", status_code=500)

@app.get("/api/formulario/{token}")
async def api_obtener_formulario(token: str):
    try:
        rows = await _supabase_request(
            "GET", "/Formulario",
            params={"token": f"eq.{token}", "select": "*"},
        )
        if not rows:
            return Response(content="No encontrado", status_code=404)
        return rows[0]
    except Exception:
        return Response(content="Error", status_code=500)

@app.patch("/api/formulario/{token}")
async def api_actualizar_formulario(token: str, request: Request):
    body = await request.json()
    payload = {k: v for k, v in body.items() if k in _FORMULARIO_CAMPOS}
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    try:
        await _supabase_request(
            "PATCH", "/Formulario",
            params={"token": f"eq.{token}"},
            json=payload,
            extra_headers={"Prefer": "return=minimal"},
        )
        return {"ok": True}
    except Exception as exc:
        logger.warning("Error actualizando Formulario %s: %s", token, exc)
        return Response(content="Error al actualizar", status_code=500)


@app.post("/api/formulario/{token}/convertir")
async def api_convertir_formulario(token: str, request: Request):
    """Convierte un Formulario completado en un Cliente CRM."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        rows = await _supabase_request(
            "GET", "/Formulario",
            params={"token": f"eq.{token}", "select": "*"},
        )
        if not rows:
            return Response(content="Formulario no encontrado", status_code=404)
        f = rows[0]

        if f.get("resultado") == "convertido":
            return Response(content="Este formulario ya fue convertido a cliente", status_code=409)

        nombre   = (f.get("nombre") or "").strip()
        telefono = _normalize_phone(f.get("telefono") or "")
        if not nombre:
            return Response(content="El formulario no tiene nombre registrado", status_code=400)

        body: Dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            pass

        proyecto_id = body.get("proyecto_id") or f.get("proyecto_id")
        usuario_id  = body.get("usuario_id") or perfil.get("id")

        cliente_payload: Dict[str, Any] = {
            "Contacto":    nombre,
            "Telefono":    telefono or f.get("telefono"),
            "email":       f.get("email"),
            "rsh":         f.get("rsh"),
            "tipo_ingreso": f.get("tipo_ingreso"),
            "proyecto_id": proyecto_id,
            "usuario_id":  usuario_id,
            "primer mensaje": True,
            "wtsp_habilitado": bool(telefono),
        }
        cliente_payload = {k: v for k, v in cliente_payload.items() if v is not None}

        nuevo = await _supabase_request(
            "POST", "/Cliente",
            json=cliente_payload,
            extra_headers={"Prefer": "return=representation"},
        )
        if not nuevo:
            return Response(content="Error al crear cliente", status_code=500)
        cliente    = nuevo[0] if isinstance(nuevo, list) else nuevo
        cliente_id = cliente.get("id")

        if telefono and proyecto_id:
            await upsert_prospecto(
                telefono_e164=telefono,
                nombre=nombre,
                proyecto_id=str(proyecto_id),
                estado="NUEVO",
                paso="INICIO",
                cliente_id=cliente_id,
                numero_integrantes=f.get("numero_integrantes"),
            )

        await _supabase_request(
            "PATCH", "/Formulario",
            params={"token": f"eq.{token}"},
            json={"resultado": "convertido", "completado_at": _utc_now_iso()},
            extra_headers={"Prefer": "return=minimal"},
        )

        return {"ok": True, "cliente_id": cliente_id, "cliente": cliente}
    except Exception as e:
        logger.exception("Error en convertir formulario %s", token)
        return Response(content=_safe_httpx_error(e) or str(e), status_code=500, media_type="text/plain")


@app.post("/api/newsletter")
async def api_newsletter(request: Request):
    """Guarda suscripción al blog/newsletter en Supabase."""
    ip = request.client.host if request.client else "unknown"
    if not _rate_limit_ok(f"newsletter:{ip}", max_req=3, window=60):
        return Response(content="Demasiadas solicitudes, intenta en un minuto", status_code=429)

    body = await request.json()
    email = (body.get("email") or "").strip().lower()

    if not email or "@" not in email:
        return Response(content="email inválido", status_code=422)

    logger.info("📧 Newsletter suscripción: %s", email)

    # Guardar en Supabase (upsert para evitar duplicados)
    try:
        await _supabase_request(
            "POST", "/newsletter",
            json={"email": email},
            extra_headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
        )
    except Exception as exc:
        logger.warning("No se pudo guardar newsletter en Supabase: %s", exc)

    return {"ok": True}


# ── Rutas de páginas (URLs limpias sin .html) ────────────────────────────────
@app.get("/reset-password")
async def page_reset_password():
    return FileResponse("frontend/reset-password.html")

@app.get("/empresas")
async def page_empresas():
    return FileResponse("frontend/empresas.html")

@app.get("/login")
async def page_login():
    return RedirectResponse(url="/", status_code=301)

@app.get("/inmobiliaria")
async def page_inmobiliaria():
    return FileResponse("frontend/inmobiliaria.html")

@app.get("/proyecto")
async def page_proyecto():
    return FileResponse("frontend/proyecto.html")

@app.get("/perfil")
async def page_perfil():
    return FileResponse("frontend/opcion_c.html")


# ── API Progreso usuario (requiere JWT de Supabase en Authorization header) ──

async def _uid_desde_jwt(request: Request) -> Optional[str]:
    """Extrae el user_id desde el JWT de Supabase en el header Authorization."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        # Verificamos llamando a /auth/v1/user con el token del usuario
        sb_url = _supabase_url()
        if not sb_url:
            return None
        anon_key = _get_env("SUPABASE_ANON_KEY", "SUPABASE_KEY")
        if not anon_key:
            return None
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{sb_url.rstrip('/')}/auth/v1/user",
                headers={"apikey": anon_key, "Authorization": f"Bearer {token}"},
            )
            if r.status_code != 200:
                return None
            return r.json().get("id")
    except Exception:
        return None


@app.get("/api/progreso")
async def api_get_progreso(request: Request):
    uid = await _uid_desde_jwt(request)
    if not uid:
        return Response(content="no autorizado", status_code=401)
    rows = await _supabase_request("GET", "/progreso_usuario",
        params={"user_id": f"eq.{uid}", "select": "mundo,req_id,completado"},
    ) or []
    resultado: Dict[str, List[str]] = {"s": [], "h": []}
    for row in rows:
        if row.get("completado"):
            m = row.get("mundo", "")
            if m in resultado:
                resultado[m].append(row.get("req_id", ""))
    return resultado


@app.post("/api/progreso")
async def api_set_progreso(request: Request):
    uid = await _uid_desde_jwt(request)
    if not uid:
        return Response(content="no autorizado", status_code=401)
    body = await request.json()
    mundo  = (body.get("mundo")  or "").strip()
    req_id = (body.get("req_id") or "").strip()
    completado = bool(body.get("completado", True))
    if not mundo or not req_id:
        return Response(content="mundo y req_id son requeridos", status_code=422)
    await _supabase_request(
        "POST", "/progreso_usuario",
        json={"user_id": uid, "mundo": mundo, "req_id": req_id, "completado": completado},
        extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    return {"ok": True}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
