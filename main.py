from fastapi import FastAPI, Request, Response, BackgroundTasks
import asyncio
import os
import json
import httpx
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone
import logging

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("wtsp_pita")
if not logger.handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

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
ANTHROPIC_API_KEY  = _get_env("ANTHROPIC_API_KEY")

# ---------------------------------------------------------------------------
# Documentos requeridos y sus cantidades mínimas
# ---------------------------------------------------------------------------

DOCUMENTOS_REQUERIDOS = {
    "liquidacion_sueldo": {"label": "Últimas 6 liquidaciones de sueldo", "cantidad": 6},
    "carnet_identidad":   {"label": "Carnet de identidad (ambos lados)",  "cantidad": 2},
    "antiguedad_laboral": {"label": "Certificado de antigüedad laboral",  "cantidad": 1},
    "certificado_afp":    {"label": "Certificado de AFP",                 "cantidad": 1},
}

# Palabras clave para clasificar documentos por nombre de archivo
_KEYWORDS_TIPO: List[tuple] = [
    ("liquidacion_sueldo", ["liquidacion", "liquidación", "sueldo", "remuneracion", "remuneración", "renta"]),
    ("carnet_identidad",   ["carnet", "cedula", "cédula", "ci_", "dni", "identidad", "rut"]),
    ("antiguedad_laboral", ["antiguedad", "antigüedad", "contrato", "laboral", "empleador"]),
    ("certificado_afp",    ["afp", "prevision", "previsión", "pension", "pensión", "retiro"]),
]


def clasificar_documento(nombre_archivo: str, mime_type: str = "") -> str:
    """Clasifica el tipo de documento según el nombre del archivo."""
    nombre_lower = (nombre_archivo or "").lower().replace(" ", "_").replace("-", "_")
    for tipo, keywords in _KEYWORDS_TIPO:
        if any(kw in nombre_lower for kw in keywords):
            return tipo
    return "otro"


def documentos_pendientes(docs_recibidos: List[Dict]) -> Dict[str, int]:
    """
    Dado el listado de documentos ya recibidos, retorna cuántos faltan por tipo.
    Solo incluye tipos con pendientes > 0.
    """
    conteo: Dict[str, int] = {t: 0 for t in DOCUMENTOS_REQUERIDOS}
    for doc in docs_recibidos:
        tipo = doc.get("tipo", "")
        if tipo in conteo:
            conteo[tipo] += 1

    pendientes = {}
    for tipo, cfg in DOCUMENTOS_REQUERIDOS.items():
        faltantes = cfg["cantidad"] - conteo.get(tipo, 0)
        if faltantes > 0:
            pendientes[tipo] = faltantes
    return pendientes


def resumen_documentos(docs_recibidos: List[Dict]) -> str:
    """Genera un texto resumido del estado de documentos para usar en el prompt."""
    conteo: Dict[str, int] = {t: 0 for t in DOCUMENTOS_REQUERIDOS}
    for doc in docs_recibidos:
        if doc.get("tipo") in conteo:
            conteo[doc["tipo"]] += 1

    lineas = []
    for tipo, cfg in DOCUMENTOS_REQUERIDOS.items():
        recibidos = conteo.get(tipo, 0)
        requeridos = cfg["cantidad"]
        estado = "✅" if recibidos >= requeridos else f"⏳ ({recibidos}/{requeridos})"
        lineas.append(f"  {estado} {cfg['label']}")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Configuración de pasos conversacionales
# ---------------------------------------------------------------------------

PASOS_CONFIG: Dict[str, str] = {
    "BIENVENIDA": """OBJETIVO — PASO BIENVENIDA:
El cliente acaba de responder al mensaje inicial. No sabemos aún qué tan interesado está.

1. Salúdalo por su nombre ({nombre}), pregúntale cómo está, de forma cercana y natural.
2. Muéstrate disponible para ayudarlo y pregúntale si le gustaría evaluar su opción
   de compra a través de un crédito hipotecario.
   - NO repitas el nombre del proyecto ni los precios. Él ya los vio en el mensaje anterior.
   - NO menciones documentos todavía.
3. Si el cliente muestra interés o hace preguntas sobre el proyecto/subsidio/proceso:
   respóndelas brevemente y luego → "siguiente_paso": "INICIO"
4. Si el cliente dice explícitamente que NO le interesa → "siguiente_paso": "NO_INTERESADO"
5. Si el cliente saluda o responde de forma neutra (hola, ok, bien, etc.):
   responde cálidamente y pregunta si quiere evaluar su opción. siguiente_paso: null

En datos_extraidos: {{}} (no hay nada que recolectar en este paso)""",

    "INICIO": """OBJETIVO — PASO INICIO:
El cliente acaba de responder al mensaje inicial sobre el proyecto.

1. Salúdalo por su nombre ({nombre}) y confirma su interés.
2. Si confirma interés, explícale el subsidio DS19 brevemente:
   el Estado entrega 700 UF (≈ $27.889.000) como subsidio habitacional.
3. Luego hazle las siguientes preguntas de calificación UNA A LA VEZ.
   Revisa los datos ya recolectados para no repetir preguntas:
   Estado actual de calificación: {datos}

   Preguntas en orden (solo haz las que aún sean null):
   a) "ahorro_ok"          → ¿Cuenta con ahorro en libreta o cuenta de ahorro?
                              (Se requiere mínimo 50 UF ≈ $2.000.000)
   b) "trabajo_indefinido" → ¿Tiene contrato de trabajo indefinido con más de
                              6 meses de antigüedad?
   c) "complemento_renta"  → Su renta registrada es {rango_sueldo}.
                              ¿Esto incluye complemento de renta de un co-deudor?

4. Cuando las 3 preguntas estén respondidas → "siguiente_paso": "DOCUMENTACION"
5. Si el cliente dice que NO le interesa    → "siguiente_paso": "NO_INTERESADO"
6. Si el cliente pregunta algo fuera del tema (clima, otros temas), redirígelo
   gentilmente al proceso indicando que necesitas esa información para avanzar.

En datos_extraidos reporta SOLO lo que el cliente reveló en ESTE mensaje:
  "ahorro_ok": true/false  (null si no lo mencionó)
  "trabajo_indefinido": true/false  (null si no lo mencionó)
  "complemento_renta": true/false  (null si no lo mencionó)""",

    "DOCUMENTACION": """OBJETIVO — PASO DOCUMENTACION:
El cliente completó las preguntas de calificación.
Datos recopilados: {datos}

1. Explícale que para continuar necesitas los siguientes documentos:
   ▸ Carnet de identidad (foto del frente y dorso)
   ▸ Últimas 6 liquidaciones de sueldo
   ▸ Certificado de antigüedad laboral
   ▸ Certificado de AFP

   Si ahorro_ok es true, agregar:
   ▸ Cartola de ahorro de los últimos 12 meses

   Si complemento_renta es true, agregar:
   ▸ CI y últimas 3 liquidaciones del co-deudor

2. Indícale que puede enviar los documentos directamente por este chat (fotos o PDF).
3. Ofrécele una llamada telefónica si tiene dudas o necesita orientación.
4. Si confirma que enviará o ya envió documentos → "siguiente_paso": "ESPERA_DOCS".""",

    "ESPERA_DOCS": """OBJETIVO — PASO ESPERA DE DOCUMENTOS:
El cliente está enviando su documentación.

Estado actual de documentos recibidos:
{estado_documentos}

1. Si el cliente acaba de enviar un documento, agradece y confirma qué recibiste.
2. Si aún faltan documentos, indícale amablemente cuáles quedan pendientes.
3. Si YA están todos los documentos completos → "siguiente_paso": "DOCS_RECIBIDOS".
4. Si el cliente pregunta algo fuera del tema, recuérdale qué documentos faltan
   y que sin ellos no se puede avanzar en el proceso.
5. Responde consultas sobre el proyecto con amabilidad.""",

    "DOCS_RECIBIDOS": """Los documentos fueron recibidos completos.
Informa al cliente que el equipo los revisará y se pondrá en contacto pronto.
Responde cualquier consulta con amabilidad.
No solicites más documentos a menos que el ejecutivo lo indique.""",

    "NO_INTERESADO": """El cliente no está interesado actualmente.
Responde con amabilidad, deja la puerta abierta para el futuro y despídete.""",
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
    codigo_proyecto: Optional[str] = None,
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
    if codigo_proyecto is not None:
        row["codigo_proyecto"] = codigo_proyecto
    if estado is not None:
        row["estado"] = estado
    if paso is not None:
        row["paso"] = paso
    if ultimo_texto_entrante is not None:
        row["ultimo_texto_entrante"] = ultimo_texto_entrante
        row["ultimo_entrante_en"] = _utc_now_iso()
    if datos is not None:
        row["datos"] = datos
    if cliente_id is not None:
        row["cliente_id"] = cliente_id

    data = await _supabase_request(
        "POST",
        "/prospectos",
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
):
    rows = await _supabase_request(
        "GET",
        "/prospectos",
        params={"id": f"eq.{prospecto_id}", "select": "datos,paso"},
    )
    if not rows:
        return
    merged = {**(rows[0].get("datos") or {}), **{k: v for k, v in nuevos_datos.items() if v is not None}}

    update: Dict[str, Any] = {"datos": merged, "actualizado_en": _utc_now_iso()}
    if siguiente_paso:
        update["paso"] = siguiente_paso
        update["estado"] = siguiente_paso

    await _supabase_request(
        "PATCH",
        "/prospectos",
        params={"id": f"eq.{prospecto_id}"},
        json=update,
    )


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
    await _supabase_request("POST", "/mensajes", json=row)


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
        "/documentos",
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
        "/documentos",
        params={
            "prospecto_id": f"eq.{prospecto_id}",
            "select": "tipo,nombre_archivo,verificado,creado_en",
            "order": "creado_en.asc",
        },
    )
    return rows or []


async def obtener_proyecto_por_codigo(codigo: str):
    rows = await _supabase_request(
        "GET",
        "/Proyecto",
        params={
            "codigo": f"eq.{codigo}",
            "select": "codigo,nombre,ubicacion,nombre_plantilla,idioma_plantilla,imagen_url",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def obtener_historial_mensajes(prospecto_id: str, limite: int = 12) -> List[Dict]:
    rows = await _supabase_request(
        "GET",
        "/mensajes",
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
# IA — respuesta con contexto de paso
# ---------------------------------------------------------------------------

async def generar_respuesta_ia(
    *,
    prospecto: Dict,
    proyecto: Optional[Dict],
    historial: List[Dict],
    mensaje_actual: str,
    docs_recibidos: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    if not ANTHROPIC_API_KEY or anthropic is None:
        logger.warning("ANTHROPIC_API_KEY no configurada — usando eco")
        return {"respuesta": f"Hola 👋 Recibí: {mensaje_actual}", "siguiente_paso": None, "datos_extraidos": {}}

    nombre        = (prospecto.get("nombre") or "").strip() or "amigo/a"
    telefono      = prospecto.get("telefono_e164") or ""
    rut           = prospecto.get("rut") or "no registrado"
    rango_sueldo  = prospecto.get("rango_sueldo") or "no registrado"
    paso_actual   = prospecto.get("paso") or "INICIO"
    datos         = prospecto.get("datos") or {}

    proyecto_nombre    = (proyecto or {}).get("nombre") or "nuestro proyecto"
    proyecto_ubicacion = (proyecto or {}).get("ubicacion") or ""

    # Estado de documentos para el paso ESPERA_DOCS
    estado_documentos = resumen_documentos(docs_recibidos or [])

    instrucciones = PASOS_CONFIG.get(paso_actual, PASOS_CONFIG["BIENVENIDA"]).format(
        nombre=nombre,
        rango_sueldo=rango_sueldo,
        datos=json.dumps(datos, ensure_ascii=False, indent=2),
        estado_documentos=estado_documentos,
    )

    system_prompt = f"""Eres un asistente de ventas inmobiliario profesional y empático de {proyecto_nombre}.

═══ DATOS DEL CLIENTE ═══
Nombre:       {nombre}
Teléfono:     {telefono}
RUT:          {rut}
Rango sueldo: {rango_sueldo}
Proyecto:     {proyecto_nombre} — {proyecto_ubicacion}
Paso actual:  {paso_actual}

═══ INSTRUCCIONES DE ESTE PASO ═══
{instrucciones}

═══ REGLAS GENERALES ═══
- Responde en español, de forma cálida y profesional.
- Mensajes cortos (máximo 3-4 párrafos). NUNCA más de 1 pregunta a la vez.
- Usa emojis con moderación.
- Si el cliente pregunta algo fuera del tema del proyecto o subsidio DS19,
  redirígelo amablemente sin ser brusco, recordándole en qué punto del proceso está.

RESPONDE ÚNICAMENTE con JSON válido (sin markdown, sin texto extra):
{{
  "respuesta": "texto para enviar por WhatsApp",
  "siguiente_paso": null,
  "datos_extraidos": {{}}
}}
Valores válidos de siguiente_paso: null | "BIENVENIDA" | "INICIO" | "DOCUMENTACION" | "ESPERA_DOCS" | "DOCS_RECIBIDOS" | "NO_INTERESADO"
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
DELAY_RESPUESTA_SEGUNDOS = int(os.getenv("BOT_REPLY_DELAY", "3"))


async def _procesar_webhook(msg: Dict):
    """Procesa un mensaje de WhatsApp en background (después de devolver 200 a Meta)."""
    try:
        from_number = _normalize_phone(msg["from"])
        msg_type    = msg.get("type", "text")

        # Delay antes de responder — sensación humana y evita race conditions
        await asyncio.sleep(DELAY_RESPUESTA_SEGUNDOS)

        # ── Obtener o crear prospecto ──────────────────────────────────────
        prospecto = None
        proyecto  = None

        if _supabase_url() and _supabase_service_role_key():
            prospecto = await upsert_prospecto(
                telefono_e164=from_number,
                estado="RESPONDIO",
            )
            if prospecto and prospecto.get("codigo_proyecto"):
                proyecto = await obtener_proyecto_por_codigo(prospecto["codigo_proyecto"])

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

        historial = []
        docs_recibidos = []

        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="entrante",
                text=text,
                wa_message_id=msg.get("id"),
                cliente_id=cliente_id_prospecto,
            )
            try:
                historial = await obtener_historial_mensajes(prospecto_id)
                docs_recibidos = await obtener_documentos_prospecto(prospecto_id)
            except Exception:
                pass

        if prospecto_id:
            await upsert_prospecto(
                telefono_e164=from_number,
                ultimo_texto_entrante=text,
            )

        resultado = await generar_respuesta_ia(
            prospecto=prospecto or {},
            proyecto=proyecto,
            historial=historial,
            mensaje_actual=text,
            docs_recibidos=docs_recibidos,
        )

        reply_text      = resultado["respuesta"]
        siguiente_paso  = resultado["siguiente_paso"]
        datos_extraidos = resultado["datos_extraidos"]

        await send_whatsapp_message(to=from_number, text=reply_text)

        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="saliente",
                text=reply_text,
                cliente_id=cliente_id_prospecto,
            )
            if datos_extraidos or siguiente_paso:
                await actualizar_datos_prospecto(
                    prospecto_id,
                    datos_extraidos,
                    siguiente_paso,
                )

    except Exception as e:
        logger.exception("Error procesando webhook: %s", _safe_httpx_error(e))


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    try:
        entry   = payload["entry"][0]
        changes = entry["changes"][0]
        value   = changes["value"]

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

        # Devuelve 200 a Meta de inmediato y procesa en background
        background_tasks.add_task(_procesar_webhook, msg)

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

        pendientes = documentos_pendientes(docs_recibidos)
        tipo_label = DOCUMENTOS_REQUERIDOS.get(tipo, {}).get("label") or nombre_archivo

        if not pendientes:
            # Todos los documentos recibidos
            confirmacion = (
                f"✅ ¡Recibí tu documento ({tipo_label})!\n\n"
                f"🎉 ¡Perfecto! Ya tenemos todos los documentos necesarios. "
                f"El equipo los revisará y se pondrá en contacto contigo pronto."
            )
            if prospecto_id:
                await actualizar_datos_prospecto(prospecto_id, {}, "DOCS_RECIBIDOS")
        else:
            pendientes_texto = "\n".join(
                f"  ▸ {DOCUMENTOS_REQUERIDOS[t]['label']} ({falta} archivo{'s' if falta > 1 else ''} más)"
                for t, falta in pendientes.items()
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
        codigo_proyecto = (body.get("codigo_proyecto") or body.get("project_code") or "").strip() or None

        if not phone:
            return Response(content="Falta telefono_e164", status_code=400)
        if not codigo_proyecto:
            return Response(content="Falta codigo_proyecto", status_code=400)

        proyecto = await obtener_proyecto_por_codigo(codigo_proyecto)
        if not proyecto:
            return Response(content="codigo_proyecto no existe en proyectos", status_code=400)

        prospecto = await upsert_prospecto(
            telefono_e164=phone,
            nombre=nombre,
            rut=rut,
            rango_sueldo=rango_sueldo,
            codigo_proyecto=codigo_proyecto,
            estado="PLANTILLA_ENVIADA",
            paso="BIENVENIDA",
        )

        wa_resp = await send_whatsapp_template(
            to=phone,
            template_name=proyecto["nombre_plantilla"],
            language_code=proyecto.get("idioma_plantilla") or "es",
            body_text_params=[nombre or ""],
            image_url=proyecto.get("imagen_url"),
        )

        if prospecto and prospecto.get("id"):
            await insertar_mensaje(
                prospecto_id=prospecto["id"],
                direccion="saliente",
                text=f"[PLANTILLA] {proyecto['nombre_plantilla']}",
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


async def send_whatsapp_template(
    *,
    to: str,
    template_name: str,
    language_code: str,
    body_text_params: List[str],
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
            "parameters": [{"type": "text", "text": p} for p in body_text_params],
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
# API para el frontend
# ---------------------------------------------------------------------------

def _check_api_key(request: Request) -> bool:
    key = _ingest_api_key()
    if not key:
        return True
    provided = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    return provided == key


@app.get("/api/proyectos")
async def api_listar_proyectos(request: Request):
    if not _check_api_key(request):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request(
        "GET", "/Proyecto",
        params={"select": "codigo,nombre,ubicacion", "order": "nombre.asc"},
    )
    return rows or []


@app.get("/api/clientes")
async def api_listar_clientes(request: Request):
    if not _check_api_key(request):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request(
        "GET", "/Cliente",
        params={"select": "*", "order": "id.desc"},
    )
    return rows or []


@app.post("/api/clientes")
async def api_crear_cliente(request: Request):
    if not _check_api_key(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()

        nombre          = (body.get("Contacto") or "").strip()
        telefono        = _normalize_phone(body.get("Telefono") or "")
        proyecto_codigo = (body.get("Proyecto") or "").strip()
        rut             = (body.get("Rut") or "").strip() or None
        correo          = (body.get("Correo") or "").strip() or None
        rango           = (body.get("Tramo de renta") or "").strip() or None
        primer_msg      = bool(body.get("primer mensaje", True))

        if not nombre:
            return Response(content="Falta Contacto", status_code=400)
        if not telefono:
            return Response(content="Falta Telefono", status_code=400)
        if not proyecto_codigo:
            return Response(content="Falta Proyecto", status_code=400)

        cliente = await _supabase_request(
            "POST", "/Cliente",
            json={
                "Proyecto":       proyecto_codigo,
                "Contacto":       nombre,
                "Rut":            rut or "",
                "Correo":         correo,
                "Telefono":       telefono,
                "Tramo de renta": rango,
                "primer mensaje": primer_msg,
            },
            extra_headers={"Prefer": "return=representation"},
        )

        wa_result = None
        if primer_msg:
            await upsert_prospecto(
                telefono_e164=telefono,
                nombre=nombre,
                rut=rut,
                rango_sueldo=rango,
                codigo_proyecto=proyecto_codigo,
                estado="PLANTILLA_ENVIADA",
                paso="BIENVENIDA",
            )
            proyecto = await obtener_proyecto_por_codigo(proyecto_codigo)
            if proyecto and proyecto.get("nombre_plantilla"):
                wa_result = await send_whatsapp_template(
                    to=telefono,
                    template_name=proyecto["nombre_plantilla"],
                    language_code=proyecto.get("idioma_plantilla") or "es",
                    body_text_params=[nombre],
                    image_url=proyecto.get("imagen_url"),
                )
            else:
                logger.warning(f"Proyecto {proyecto_codigo} sin plantilla — no se envió template")

        return {"ok": True, "cliente": cliente, "wa": wa_result}

    except Exception as e:
        logger.exception("Error en /api/clientes POST")
        return Response(
            content=_safe_httpx_error(e) or "Internal Server Error",
            status_code=500,
            media_type="text/plain",
        )


@app.post("/api/clientes/{cliente_id}/enviar-plantilla")
async def api_enviar_plantilla(cliente_id: int, request: Request):
    if not _check_api_key(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        rows = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
        if not rows:
            return Response(content="Cliente no encontrado", status_code=404)
        c = rows[0]
        telefono        = _normalize_phone(c.get("Telefono") or "")
        nombre          = (c.get("Contacto") or "").strip()
        codigo_proyecto = (c.get("codigo_proyecto") or "").strip()

        if not telefono:
            return Response(content="Cliente sin teléfono", status_code=400)
        if not codigo_proyecto:
            return Response(content="Cliente sin codigo_proyecto — actualiza la BD", status_code=400)

        proyecto = await obtener_proyecto_por_codigo(codigo_proyecto)
        if not proyecto or not proyecto.get("nombre_plantilla"):
            return Response(content=f"Proyecto '{codigo_proyecto}' sin plantilla configurada", status_code=400)

        wa = await send_whatsapp_template(
            to=telefono,
            template_name=proyecto["nombre_plantilla"],
            language_code=proyecto.get("idioma_plantilla") or "es_CL",
            body_text_params=[nombre],
            image_url=proyecto.get("imagen_url"),
        )
        await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente_id}"}, json={"primer mensaje": False})
        await upsert_prospecto(
            telefono_e164=telefono, nombre=nombre, rut=c.get("Rut"),
            rango_sueldo=c.get("Tramo de renta"), codigo_proyecto=codigo_proyecto,
            estado="PLANTILLA_ENVIADA", paso="BIENVENIDA",
            cliente_id=cliente_id,
        )
        return {"ok": True, "wa": wa}
    except Exception as e:
        logger.exception("Error en enviar-plantilla")
        return Response(content=_safe_httpx_error(e), status_code=500, media_type="text/plain")


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
