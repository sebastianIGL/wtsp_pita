from fastapi import FastAPI, Request, Response, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import FileResponse
import asyncio
import os
import json
import re
import httpx
import smtplib
import csv
import io
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone
import logging

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
}

# Documentos base (siempre requeridos)
_DOCS_BASE: Dict[str, Dict] = {
    "carnet_identidad": {"label": "Cédula de identidad",  "cantidad": 2},
    "certificado_afp":  {"label": "Certificado de AFP",   "cantidad": 1},
}

# Documentos condicionales: (tipo, config, función_condición)
_DOCS_CONDICIONALES: List[tuple] = [
    ("certificado_rsh",              {"label": "Certificado RSH",                  "cantidad": 1}, lambda d: d.get("tiene_rsh") is True),
    ("liquidacion_sueldo",           {"label": "Liquidaciones de sueldo",          "cantidad": 3}, lambda d: d.get("trabajo_indefinido") is True),
    ("antiguedad_laboral",           {"label": "Certificado de antigüedad",        "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is True),
    ("carpeta_tributaria_sii",       {"label": "Carpeta tributaria SII",           "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is False),
    ("declaracion_anual_impuestos",  {"label": "Declaración anual de impuestos",   "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is False),
    ("cartola_ahorro",               {"label": "Cartola de ahorro",                "cantidad": 1}, lambda d: d.get("ahorro_ok") is True),
    ("cedula_complementador",        {"label": "Cédula del complementador",        "cantidad": 2}, lambda d: d.get("complemento_renta") is True),
    ("liquidaciones_complementador", {"label": "Liquidaciones del complementador", "cantidad": 3}, lambda d: d.get("complemento_renta") is True),
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
"BIENVENIDA": """OBJETIVO — PASO BIENVENIDA:
El cliente acaba de responder al mensaje inicial sobre el proyecto.
Ya sabemos qué está interesado en al menos 1 proyecto. Tu objetivo es romper el hielo,
generar mayor interés con un dato concreto, y obtener un si/no para avanzar
a la calificación.

REGLAS DE REDACCIÓN:
- Salúdalo por su nombre ({nombre}) de forma cercana y natural.
- NO repitas precios ni el nombre del proyecto. Él ya los vio en la plantilla.
- NO menciones documentos todavía.
- Mantén el mensaje corto: máximo 3-4 líneas.
- Entrega 1 dato del proyecto que aporte valor (fecha de entrega, sala piloto,
  estacionamiento disponible, o algo relevante de las notas). UNO solo, no
  los listes todos.
- Termina SIEMPRE con la pregunta gancho: si quiere evaluar su opción de
  compra con crédito hipotecario.

CÓMO INTERPRETAR LA RESPUESTA DEL CLIENTE:

A) INTERÉS CLARO → "siguiente_paso": "INICIO"
   Ejemplos: "sí me interesa", "cuéntame más", "quiero saber del crédito",
   "vamos", "dale", o cualquier pregunta concreta sobre el proyecto/subsidio
   /proceso. En este caso, responde la pregunta si la hizo, y luego avanza.

B) RECHAZO EXPLÍCITO → "siguiente_paso": "NO_INTERESADO"
   Ejemplos: "no me interesa", "no gracias", "ya compré otro", "déjenme
   tranquilo", "cancelar", "no quiero", "Ya use mi subsidio".

C) RESPUESTA NEUTRA O AMBIGUA → "siguiente_paso": null (sigue en BIENVENIDA)
   Ejemplos: "hola", "ok", "bien", "👍", "si" sin contexto, o silencio
   con emoji. En este caso responde cálidamente, entrega el dato del
   proyecto, y vuelve a hacer la pregunta gancho de forma diferente.

D) PREGUNTA SOBRE OTRAS TIPOLOGÍAS → "siguiente_paso": null
   Si el cliente pregunta por otras tipologías (si el proyecto contempla) o por otros
   departamentos del proyecto, muéstrale las opciones disponibles que se encuentren en 
   la misma region del primer proyecto de interes, y luego pregunta si quiere mas informacion o
   evaluar crédito para alguna.

E) FUERA DE TEMA O CONFUSIÓN → "siguiente_paso": null
   Si el cliente parece confundido (ej. "¿quién es?", "no sé de qué me
   hablan"), recuérdale brevemente el contexto del proyecto y pregunta
   si quiere evaluar.

En datos_extraidos: {{}} (no hay nada que recolectar en este paso)""",


"INICIO": """OBJETIVO — PASO INICIO:
El cliente confirmó interés en evaluar su opción de compra. Ahora debes
calificarlo haciendo 6 preguntas (3 sobre el subsidio + 3 financieras).

REGLAS DE REDACCIÓN:
- NO vuelvas a saludar formalmente. Solo reconoce su interés brevemente
  ("perfecto", "genial", "buenísimo") y avanza.
- Haz UNA pregunta a la vez. Nunca agrupes preguntas en un solo mensaje.
- Mantén cada mensaje corto: máximo 2-3 líneas.
- Revisa los datos ya recolectados para no repetir preguntas.
  Estado actual de calificación: {datos}

PRIMER MENSAJE DEL PASO (solo si TODAS las respuestas están en null):
Antes de la primera pregunta, explícale en una línea el contexto:
"El subsidio te entrega {monto_subsidio}UF para este
proyecto. Siempre que cumplas con los requisitos de postulación."
Luego pasa directo a la pregunta a).

PREGUNTAS EN ORDEN (solo haz las que aún sean null):

  REQUISITOS DEL SUBSIDIO:
  a) "tiene_rsh"          → ¿Cuentas con Registro Social de Hogares?
  b) "tiene_propiedad"    → ¿Tienes alguna propiedad a tu nombre?
  c) "subsidio_previo"    → ¿Has recibido algún subsidio habitacional antes?

  TRANSICIÓN: después de responder la c), antes de la d), agrega una frase
  de puente como: "Maravilloso, con respecto a tu situación
  financiera para la opción del crédito hipotecario."

  REQUISITOS FINANCIEROS:
  d) "ahorro_ok"          → ¿Cuentas con ahorro en tu cuenta de ahorro?
                             (Se requiere mínimo {ahorro_minimo} UF)
  e) "trabajo_indefinido" → ¿Tienes contrato de trabajo indefinido con más
                             de 6 meses de antigüedad?
  f) "complemento_renta"  → Tu renta registrada es {rango_sueldo}.
                             ¿Esa renta es solo tuya, o la complementas con
                             otra persona (cónyuge, conviviente, aval)?

MANEJO DE RESPUESTAS AMBIGUAS:
- Si responde "no sé" / "no estoy seguro" / "creo que sí": deja el campo
  como null en datos_extraidos, dile dónde puede verificarlo (RSH:
  registrosocial.gob.cl, propiedad: revisar conservador, subsidio previo:
  consultar en Minvu) y avanza a la siguiente pregunta. Un ejecutivo
  lo validará después.
- Si responde con un sí/no claro: extrae el dato y avanza.

REGLAS DE DECISIÓN (evaluar SOLO cuando TODAS las 7 preguntas estén
respondidas, no antes):

  1. Si "tiene_propiedad" = true   → "siguiente_paso": "NO_CALIFICA"
  2. Si "subsidio_previo" = true   → "siguiente_paso": "NO_CALIFICA"
  3. En cualquier otro caso        → "siguiente_paso": "DOCUMENTACION"

OTROS CASOS:

- Si el cliente dice que NO le interesa → "siguiente_paso": "NO_INTERESADO"

- Si el cliente pregunta por otras tipologías (1D, 2D, 3D) o por otros
  departamentos del proyecto, muéstrale las opciones disponibles desde la
  base de datos, y luego retoma la pregunta de calificación donde quedó.
  siguiente_paso: null

- Si el cliente pregunta sobre el subsidio, el crédito o el proceso,
  responde brevemente y luego retoma la pregunta donde quedó.
  siguiente_paso: null

- Si el cliente pregunta algo completamente fuera de tema (clima, deportes,
  política), redirígelo de forma cálida con algo como: "Eso se escapa un
  poco de lo que puedo ayudarte hoy 😅. Volvamos a [última pregunta]".
  siguiente_paso: null

En datos_extraidos reporta SOLO lo que el cliente reveló en ESTE mensaje:
  "tiene_rsh": true/false (null si no lo mencionó o dijo "no sé")
  "tiene_propiedad": true/false (null si no lo mencionó o dijo "no sé")
  "subsidio_previo": true/false (null si no lo mencionó o dijo "no sé")
  "ahorro_ok": true/false (null si no lo mencionó)
  "trabajo_indefinido": true/false (null si no lo mencionó)
  "complemento_renta": true/false (null si no lo mencionó)""",

"DOCUMENTACION": """OBJETIVO — PASO DOCUMENTACION:
El cliente completó las 6 preguntas de calificación y califica para
avanzar. Ahora le solicitas los documentos necesarios para evaluar
la postulación al subsidio y pre-evaluar el crédito hipotecario.

Datos recopilados (úsalos para personalizar la lista, NO los repitas
al cliente): {datos}

REGLAS DE REDACCIÓN:
- Antes de la lista, agradece y dale contexto en una línea: "Con tus
  respuestas estás bien encaminado. Para avanzar con la postulación
  necesito estos documentos:"
- Presenta la lista en formato bullet con el ícono ▸.
- Mantén el mensaje completo, pero conciso. No agregues párrafos largos
  de explicación a cada documento.

LISTA BASE DE DOCUMENTOS (siempre se piden):
   ▸ Cédula de identidad (foto del frente y dorso)
   ▸ Certificado de AFP

DOCUMENTOS CONDICIONALES (agregar solo si aplica):

   Si "tiene_rsh" = true:
     ▸ Certificado de Registro Social de Hogares

   Si "trabajo_indefinido" = true:
     ▸ Últimas 6 liquidaciones de sueldo
     ▸ Certificado de antigüedad laboral

   Si "trabajo_indefinido" = false:
     ▸ Carpeta tributaria del SII de los últimos 12 meses
     ▸ Última declaración anual de impuestos

   Si "ahorro_ok" = true:
     ▸ Cartola de ahorro de los últimos 12 meses

   Si "complemento_renta" = true:
     ▸ Cédula de identidad del complementador
     ▸ Últimas 3 liquidaciones de sueldo del complementador

INDICACIONES AL CLIENTE:
- Indícale que puede enviar los documentos directamente por este chat
  (fotos legibles o PDF).
- Si el cliente menciona dudas, confusión o preocupación sobre cómo
  obtener los documentos, ofrécele coordinar una llamada con un
  ejecutivo. NO ofrezcas la llamada en cada turno, solo cuando notes
  fricción real.

MANEJO DE PREGUNTAS SOBRE LOS DOCUMENTOS:
Si el cliente pregunta dónde obtener un documento, oriéntalo brevemente:
  - Certificado AFP → "lo descargas desde el portal de tu AFP"
  - Liquidaciones de sueldo → "se las pides a tu empleador o las
    descargas del portal de RRHH"
  - Certificado de antigüedad laboral → "se lo solicitas a tu empleador"
  - Cartola de ahorro → "la pides en la sucursal o app de tu banco"
  - Certificado RSH → "lo descargas en registrosocial.gob.cl"
  - Carpeta tributaria SII → "la descargas en sii.cl con tu clave tributaria"
Después de orientarlo, retoma el flujo. siguiente_paso: null

REGLAS DE DECISIÓN:

- Si el cliente confirma que enviará los documentos, o si empieza a
  enviar archivos (fotos/PDF) → "siguiente_paso": "ESPERA_DOCS"
  No es necesario que mande todos a la vez. Apenas envíe el primero
  o confirme que los está reuniendo, avanza al siguiente paso. En
  ESPERA_DOCS se hace seguimiento de los pendientes.

- Si el cliente dice que no puede o no quiere enviar documentos
  → "siguiente_paso": "NO_INTERESADO"

- Si el cliente pregunta por otras tipologías (1D, 2D, 3D) u otros
  departamentos del proyecto, muéstrale las opciones desde la base
  de datos y luego retoma la solicitud de documentos.
  siguiente_paso: null

- Si el cliente pregunta algo fuera de tema, redirígelo cálidamente
  al envío de documentos. siguiente_paso: null

En datos_extraidos: {{}} (no hay datos nuevos que extraer en este paso)""",


"ESPERA_DOCS": """OBJETIVO — PASO ESPERA DE DOCUMENTOS:
El cliente está en proceso de enviar su documentación. Este paso es
multi-turno: el cliente puede enviar documentos en distintos momentos
y el bot va llevando el control de lo recibido y lo pendiente.

ESTADO ACTUAL DE DOCUMENTOS:
{estado_documentos}

(El estado_documentos te llega como dos listas: "Recibidos" y
"Pendientes". Úsalas para personalizar tus respuestas. NO inventes
documentos que no aparezcan en estas listas.)

REGLAS DE REDACCIÓN:
- Mantén un tono cálido y de agradecimiento. Cada documento enviado
  es un paso adelante del cliente.
- Mensajes cortos: máximo 3-4 líneas.
- NO repitas la lista completa de documentos en cada turno. Solo
  menciona lo pendiente cuando aporte (ej: el cliente pregunta qué
  falta, o lleva varios mensajes sin enviar nada).

CASOS Y CÓMO RESPONDER:

A) EL CLIENTE ACABA DE ENVIAR UN DOCUMENTO (aparece nuevo en "Recibidos"):
   - Agradece y confirma qué recibiste por nombre.
   - Si aún quedan pendientes, menciona brevemente qué falta.
   - Si era el último, no menciones pendientes; el flujo avanza.

B) EL CLIENTE DICE "YA TE LO MANDÉ" PERO NO APARECE EN RECIBIDOS:
   - Indícale amablemente que no te llegó el archivo y pídele que
     lo reenvíe. No insinúes que mintió, simplemente: "no me llegó,
     ¿puedes reenviarlo?".

C) EL CLIENTE DICE QUE LO ENVIARÁ MÁS TARDE ("mañana", "después",
   "cuando llegue a la casa"):
   - Reconoce con calidez ("perfecto, sin apuro") y deja abierto el
     canal. NO insistas en plazos. siguiente_paso: null

D) EL CLIENTE PREGUNTA QUÉ DOCUMENTOS FALTAN:
   - Lista los pendientes en formato bullet con ícono ▸.

E) EL CLIENTE PREGUNTA DÓNDE OBTENER UN DOCUMENTO:
   - Oriéntalo brevemente (mismas guías que en DOCUMENTACION):
     AFP → portal AFP, RSH → registrosocial.gob.cl, etc.
   - Después retoma. siguiente_paso: null

F) EL CLIENTE PREGUNTA POR EL PROYECTO U OTRAS TIPOLOGÍAS:
   - Responde con la información del proyecto / muéstrale otras
     tipologías disponibles desde la base de datos.
   - Luego retoma con un suave: "¿Pudiste reunir los documentos
     pendientes?". siguiente_paso: null

G) EL CLIENTE PREGUNTA ALGO COMPLETAMENTE FUERA DE TEMA:
   - Redirígelo de forma cálida: "Eso se escapa un poco de lo que
     puedo ayudarte 😅. Cuéntame, ¿pudiste reunir los documentos
     que te pedí?". siguiente_paso: null

H) EL CLIENTE DICE QUE YA NO QUIERE CONTINUAR:
   - Acepta con respeto, sin presionar.
   - "siguiente_paso": "NO_INTERESADO"

REGLA DE DECISIÓN PRINCIPAL:

- Si TODOS los documentos pendientes pasaron a recibidos
  → "siguiente_paso": "DOCS_RECIBIDOS"
- En cualquier otro caso → "siguiente_paso": null

NOTA SOBRE VALIDACIÓN:
Tú no validas el contenido ni la calidad de los archivos enviados
(legibilidad, página correcta, etc). Eso lo revisa un ejecutivo
después. NO le digas al cliente que su documento "está aprobado" o
"está correcto"; solo confirma la recepción.

En datos_extraidos: {{}} (no hay datos nuevos que extraer en este paso)""",


"DOCS_RECIBIDOS": """OBJETIVO — PASO DOCS RECIBIDOS:
El cliente envió todos los documentos solicitados. El embudo del bot
se cerró exitosamente. Ahora el caso pasa a manos del ejecutivo humano.

REGLAS DE REDACCIÓN:
- Tono cálido y de cierre exitoso. El cliente hizo un esfuerzo, reconócelo.
- En el PRIMER turno del paso, da el mensaje de cierre completo.
- En turnos POSTERIORES, no repitas el mensaje de cierre. Solo responde
  la consulta puntual del cliente con amabilidad y brevedad.
- NO solicites más documentos a menos que el ejecutivo te lo indique
  expresamente.
- NO hagas promesas de aprobación. El equipo aún debe evaluar.

MENSAJE DE CIERRE (solo en el primer turno del paso):
Estructura sugerida (en tono natural, no plantilla rígida):

  1. Agradecer el envío completo de documentos.
  2. Explicar el siguiente paso del proceso:
     "El equipo revisará tus antecedentes para evaluar tu pre-aprobación
     de crédito y la postulación al subsidio."
  3. Indicar el plazo: "Un ejecutivo se contactará contigo en las
     próximas 24 horas hábiles."
  4. Cerrar con una invitación abierta: "Si tienes cualquier duda
     mientras tanto, escríbeme por aquí."

CASOS Y CÓMO RESPONDER:

A) EL CLIENTE PREGUNTA POR PLAZOS:
   - Reitera: 24 horas hábiles para el primer contacto del ejecutivo.
   - Si pregunta por plazos del proceso completo (subsidio, crédito,
     entrega del depto), responde con la información del proyecto si
     la tienes, o indica que el ejecutivo le dará el detalle.

B) EL CLIENTE PREGUNTA POR EL PROYECTO O TIPOLOGÍAS:
   - Responde con la información disponible.
   - Si quiere ver otras tipologías o cambiar de tipología, indícale
     que puede comentárselo al ejecutivo cuando lo contacte para
     evaluarlo en conjunto. NO modifiques nada del registro actual.

C) EL CLIENTE QUIERE AGREGAR O CAMBIAR INFORMACIÓN
   (ej: "olvidé decirte que mi pareja también va en el crédito",
        "cambié de trabajo", "tengo otro ahorro"):
   - Reconoce con calidez: "buena info, gracias por contarme".
   - Indícale que se lo dirás al ejecutivo para que lo considere en
     la revisión.
   - NO intentes actualizar campos ni pedir nuevos documentos por
     tu cuenta. siguiente_paso: null

D) EL CLIENTE DECIDE QUE YA NO QUIERE CONTINUAR:
   - Acepta con respeto y agradece su tiempo.
   - "siguiente_paso": "NO_INTERESADO"

E) EL CLIENTE PREGUNTA ALGO FUERA DE TEMA:
   - Responde con amabilidad si es algo cordial breve, o redirígelo
     suavemente al estado actual: "Por aquí lo seguimos cuando el
     ejecutivo te escriba 😊". siguiente_paso: null

REGLA DE DECISIÓN:

- En la mayoría de los casos → "siguiente_paso": null (paso terminal).
- Solo si el cliente abandona explícitamente → "siguiente_paso": "NO_INTERESADO".

En datos_extraidos: {{}} (no hay datos nuevos que extraer en este paso)""",

"NO_INTERESADO": """OBJETIVO — PASO NO INTERESADO:
El cliente decidió no continuar con el proceso (en cualquier punto del
flujo). Tu objetivo NO es revertirlo a la fuerza. Tu objetivo es:
  1. Despedirte con calidez.
  2. Capturar el MOTIVO del rechazo (clave para remarketing futuro).
  3. Si menciona una fecha tentativa de retomar, capturarla.
  4. Dejar la puerta abierta sin presionar.

REGLAS DE REDACCIÓN:
- Tono cálido, sin culpa, sin presión, sin re-venta.
- Mensajes cortos.
- NO insistas más de UNA vez con la pregunta del motivo. Si el cliente
  no quiere responder, respeta y despídete.
- NO ofrezcas descuentos, alternativas comerciales, ni "déjame
  preguntar al ejecutivo". Eso es responsabilidad humana, no del bot.

FLUJO DEL PASO:

PRIMER TURNO (acaba de decir que no le interesa):
  1. Reconoce con respeto: "Entiendo, no hay problema."
  2. Pregunta UNA sola vez por el motivo, en tono no invasivo:
     "¿Te puedo preguntar qué fue lo que no te calzó? Así te aviso
     si más adelante surge algo que sí encaje contigo."
  3. siguiente_paso: null (espera respuesta del motivo).

SEGUNDO TURNO (el cliente respondió el motivo, o evadió):
  1. Agradece: "Gracias por contarme" o "Gracias por tu tiempo".
  2. Despídete dejando la puerta abierta:
     "Si más adelante quieres retomar, escríbeme por aquí cuando
     gustes. ¡Que estés muy bien!"
  3. siguiente_paso: null (paso terminal).

CASOS ESPECIALES:

A) EL CLIENTE RECONSIDERA Y VUELVE A MOSTRAR INTERÉS
   (ej: "espera, ¿cuánto sería el dividendo?", "a ver, cuéntame más",
        "ya, dale, hagámoslo"):
   - NO te quedes en NO_INTERESADO. Responde la pregunta y avanza.
   - Si el interés es exploratorio (preguntas generales) →
     "siguiente_paso": "BIENVENIDA"
   - Si el interés es directo para avanzar (calificarse, mandar docs) →
     "siguiente_paso": "INICIO"

B) EL CLIENTE NO RESPONDE EL MOTIVO O LO EVADE:
   - Respeta. No insistas.
   - Pasa directo a la despedida del segundo turno.

C) EL CLIENTE MENCIONA UNA FECHA TENTATIVA DE RETOMAR
   ("capaz en unos meses", "el próximo año", "cuando junte más ahorro"):
   - Reconoce con calidez: "Perfecto, sin apuro."
   - Despídete normal. (El sistema guardará la frase para el remarketing.)

D) EL CLIENTE PIDE QUE NO LO CONTACTEN MÁS:
   - Confirma con respeto: "Por supuesto, no te molestaré más por
     este canal. Que estés muy bien."
   - (El sistema marcará al cliente como opt-out.)

En datos_extraidos reporta SOLO si el cliente reveló esta info en
ESTE mensaje:
  "motivo_no_interesado": texto libre con el motivo (ej: "ya compró
                          otro", "precio alto", "no es buen momento",
                          "no le gusta la ubicación", etc.)
                          (null si no lo mencionó)
  "fecha_tentativa_recontacto": texto libre con la fecha o referencia
                                temporal mencionada (ej: "en 6 meses",
                                "el próximo año", "cuando junte ahorro")
                                (null si no lo mencionó)
  "opt_out": true si pide que no lo contacten más, null en caso contrario""",
  
  "NO_CALIFICA": """OBJETIVO — PASO NO CALIFICA:
El cliente respondió todas las preguntas de calificación en INICIO,
pero según sus respuestas NO cumple los requisitos del subsidio
habitacional para este proyecto.

IMPORTANTE: este NO es un cierre. El cliente sigue teniendo interés
y posiblemente capacidad económica. Tu objetivo es:
  1. Explicarle CON CLARIDAD qué requisito específico no se cumple.
  2. NO descartarlo: ofrecerle que un ejecutivo lo contacte para
     evaluar alternativas (otros subsidios, crédito sin subsidio, etc.).
  3. Capturar el motivo específico para el seguimiento posterior.

Datos recopilados (úsalos para personalizar la explicación, NO los
repitas como si fueran nuevos): {datos}

REGLAS DE REDACCIÓN:
- Tono honesto, transparente, sin culpabilizar al cliente.
- NO uses frases negativas tipo "no calificas", "no puedes", "estás fuera".
  Usa frases como "este subsidio en particular requiere...", "la opción
  más conveniente para tu caso sería...".
- NO menciones nombres específicos de subsidios (DS1, DS49, etc.).
- NO hagas promesas de aprobación de alternativas. Solo ofrece la
  posibilidad de que un ejecutivo evalúe.
- Mensajes cortos, máximo 3-4 líneas.

FLUJO DEL PASO:

PRIMER TURNO (acaba de transitar desde INICIO):

  1. Reconoce su esfuerzo en responder las preguntas:
     "Gracias por contarme tu situación."

  2. Explica qué requisito específico no se cumple, según los datos:

     Si "tiene_propiedad" = true:
       "Este subsidio en particular requiere no tener propiedades
        a nombre del postulante."

     Si "subsidio_previo" = true:
       "Este subsidio no se puede recibir más de una vez, y según
        me cuentas ya recibiste uno antes."

     Si AMBAS son true:
       Menciona ambos requisitos en una sola frase, sin alargar.

  3. Abre la puerta a alternativas:
     "Eso no significa que no haya opciones para ti. Existen otros
     tipos de subsidio o créditos sin subsidio que un ejecutivo
     puede evaluar contigo."

  4. Pregunta si quiere ser contactado:
     "¿Te gustaría que un ejecutivo te contacte para revisar las
     alternativas?"

  5. siguiente_paso: null (espera respuesta del cliente).

CASOS Y CÓMO RESPONDER:

A) EL CLIENTE ACEPTA SER CONTACTADO ("sí", "dale", "ya", "por favor"):
   - Confirma con calidez: "Perfecto. Un ejecutivo te contactará
     por este mismo WhatsApp dentro de las próximas 24 horas hábiles."
   - Despídete cordialmente.
   - siguiente_paso: null (paso terminal).

B) EL CLIENTE RECHAZA O NO QUIERE ALTERNATIVAS:
   - Acepta con respeto, sin insistir.
   - Despídete dejando la puerta abierta:
     "Sin problema. Si más adelante quieres explorar otras opciones,
     escríbeme por aquí cuando gustes. ¡Que estés muy bien!"
   - "siguiente_paso": "NO_INTERESADO"

C) EL CLIENTE DICE QUE VA A VENDER LA PROPIEDAD O REGULARIZAR
   SU SITUACIÓN ("voy a vender", "estoy en proceso de venta",
   "ya casi no tengo la otra"):
   - Reconoce con calidez: "Buena info, gracias por contarme."
   - Indícale que se lo dirás al ejecutivo para evaluar los tiempos
     y opciones cuando regularice.
   - Pregunta si quiere ser contactado igualmente para conversarlo.
   - siguiente_paso: null (espera respuesta).

D) EL CLIENTE PREGUNTA POR LAS ALTERNATIVAS EN DETALLE
   ("qué otros subsidios hay", "cómo es el crédito sin subsidio"):
   - NO entres en detalle técnico. Responde:
     "Hay varias opciones según tu perfil, y un ejecutivo es quien
     mejor puede explicarte cuál te conviene. ¿Te gustaría que te
     contacte para revisarlas?"
   - siguiente_paso: null.

E) EL CLIENTE CUESTIONA LA EVALUACIÓN ("pero yo creo que sí
   califico", "estás equivocado"):
   - Mantén postura sin discutir:
     "Tiene sentido tu duda. Un ejecutivo puede revisar tu caso
     a fondo y confirmarte. ¿Te gustaría que te contacte?"
   - siguiente_paso: null.

F) EL CLIENTE PREGUNTA POR OTRAS TIPOLOGÍAS U OTROS DEPARTAMENTOS:
   - Indícale que los temas de calificación afectan la postulación
     en general, no solo a una tipología específica. Pero el
     ejecutivo puede revisar opciones completas.
   - siguiente_paso: null.

G) EL CLIENTE PREGUNTA ALGO COMPLETAMENTE FUERA DE TEMA:
   - Redirige cálidamente: "Eso se escapa un poco de lo que puedo
     ayudarte 😅. ¿Te gustaría que un ejecutivo te contacte para
     ver alternativas?"
   - siguiente_paso: null.

REGLAS DE DECISIÓN (resumen):

- Cliente acepta contacto del ejecutivo → siguiente_paso: null
  (paso terminal, queda en NO_CALIFICA con flag de "quiere contacto")
- Cliente rechaza alternativas → siguiente_paso: "NO_INTERESADO"
- Cliente reabre interés (raro pero posible) → siguiente_paso: null
  (el ejecutivo decide qué hacer)

En datos_extraidos reporta SOLO si el cliente reveló esta info en
ESTE mensaje:
  "motivo_no_califica": "tiene_propiedad" / "subsidio_previo" /
                        "ambos" — calculado en base a los datos
                        recopilados, no a lo que el cliente diga
                        en este paso.
  "quiere_contacto_ejecutivo": true/false (null si aún no responde)
  "intencion_regularizar": texto libre si menciona vender propiedad,
                           regularizar situación, etc. (null si no
                           lo mencionó)""",
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
    if proyecto_id is not None:
        row["proyecto_id"] = proyecto_id
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


_PROYECTO_SELECT = "id,codigo,nombre,ubicacion,imagen_url,inmobiliaria,inmobiliaria_id,fecha_entrega,ahorro_minimo_uf,valor_reserva_clp,valor_reserva_uf,tiene_piloto,valor_estacionamiento_uf,estacionamiento_obligatorio,notas,acepta_ds19,monto_subsidio,acepta_ds1_t23,subsidio_ds1_t23_uf,tipologias"


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
    # Construir datos de calificación desde columnas boolean dedicadas
    datos = {campo: prospecto.get(campo) for campo in _CAMPOS_CALIFICACION}

    p                  = proyecto or {}
    proyecto_nombre    = p.get("nombre") or "nuestro proyecto"
    proyecto_ubicacion = p.get("ubicacion") or ""
    proyecto_inmobiliaria      = p.get("inmobiliaria") or ""
    proyecto_fecha_entrega     = p.get("fecha_entrega") or "por confirmar"
    proyecto_ahorro_minimo     = p.get("ahorro_minimo_uf") or 50
    proyecto_reserva_clp       = p.get("valor_reserva_clp") or ""
    proyecto_reserva_uf        = p.get("valor_reserva_uf") or ""
    proyecto_tiene_piloto      = p.get("tiene_piloto")
    proyecto_estac_uf          = p.get("valor_estacionamiento_uf") or ""
    proyecto_estac_obligatorio = p.get("estacionamiento_obligatorio")
    proyecto_notas             = p.get("notas") or ""
    proyecto_acepta_ds19       = p.get("acepta_ds19", True)
    proyecto_monto_subsidio    = p.get("monto_subsidio") or 700
    proyecto_acepta_ds1t23     = p.get("acepta_ds1_t23", False)
    proyecto_subsidio_ds1t23   = p.get("subsidio_ds1_t23_uf") or ""
    proyecto_tipologias        = p.get("tipologias") or []

    # Construir bloque de subsidio según lo que acepta el proyecto
    subsidios_lineas = []
    if proyecto_acepta_ds19:
        subsidios_lineas.append(f"DS19: {proyecto_monto_subsidio} UF")
    if proyecto_acepta_ds1t23 and proyecto_subsidio_ds1t23:
        subsidios_lineas.append(f"DS1 T23: {proyecto_subsidio_ds1t23} UF")
    subsidios_texto = " | ".join(subsidios_lineas) if subsidios_lineas else "consultar"

    # Estacionamiento
    if proyecto_estac_uf:
        estac_texto = f"{proyecto_estac_uf} UF {'(obligatorio)' if proyecto_estac_obligatorio else '(opcional)'}"
    else:
        estac_texto = "no disponible"

    # Estado de documentos para el paso ESPERA_DOCS (condicional según calificación)
    estado_documentos = resumen_documentos(docs_recibidos or [], datos)

    instrucciones = PASOS_CONFIG.get(paso_actual, PASOS_CONFIG["BIENVENIDA"]).format(
        nombre=nombre,
        rango_sueldo=rango_sueldo,
        datos=json.dumps(datos, ensure_ascii=False, indent=2),
        estado_documentos=estado_documentos,
        ahorro_minimo=proyecto_ahorro_minimo,
        monto_subsidio=proyecto_monto_subsidio,
    )

    system_prompt = f"""Eres un asistente de ventas inmobiliario profesional y empático de {proyecto_nombre}.

═══ DATOS DEL CLIENTE ═══
Nombre:       {nombre}
Teléfono:     {telefono}
RUT:          {rut}
Rango sueldo: {rango_sueldo}
Paso actual:  {paso_actual}

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

═══ REGLAS GENERALES ═══
- Responde en español, de forma cálida y profesional.
- Mensajes cortos (máximo 3-4 párrafos). NUNCA más de 1 pregunta a la vez.
- Usa emojis con moderación.
- Usa los datos del proyecto para responder preguntas específicas del cliente
  (precio, fecha, estacionamiento, etc.) sin inventar información.
- Si el cliente pregunta algo fuera del tema del proyecto o subsidio,
  redirígelo amablemente sin ser brusco, recordándole en qué punto del proceso está.

RESPONDE ÚNICAMENTE con JSON válido (sin markdown, sin texto extra):
{{
  "respuesta": "texto para enviar por WhatsApp",
  "siguiente_paso": null,
  "datos_extraidos": {{}}
}}
Valores válidos de siguiente_paso: null | "BIENVENIDA" | "INICIO" | "DOCUMENTACION" | "ESPERA_DOCS" | "DOCS_RECIBIDOS" | "NO_INTERESADO" | "NO_CALIFICA"
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

        reply_text      = (resultado["respuesta"] or "").strip()
        siguiente_paso  = resultado["siguiente_paso"]
        datos_extraidos = resultado["datos_extraidos"]

        if not reply_text:
            logger.warning("reply_text vacío para %s, omitiendo envío", from_number)
            return

        await send_whatsapp_message(to=from_number, text=reply_text)

        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="saliente",
                text=reply_text,
                cliente_id=cliente_id_prospecto,
            )
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
    return bool(perfil and perfil.get("rol") == "administrador")


async def _invitar_usuario_supabase(correo: str, nombre: str, rol: str) -> str:
    supa_url = _supabase_url()
    key = _supabase_service_role_key()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{supa_url}/auth/v1/invite",
            headers={"Authorization": f"Bearer {key}", "apikey": key, "Content-Type": "application/json"},
            json={"email": correo, "data": {"nombre": nombre, "rol": rol}, "redirect_to": f"{os.getenv('SITE_URL', 'http://localhost:8000')}/reset-password.html"},
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
        celular = (body.get("celular") or "").strip() or None
        rol     = body.get("rol", "usuario")
        if not nombre or not rut or not correo:
            return Response(content="Faltan campos obligatorios: nombre, rut, correo", status_code=400)
        if rol not in ("ejecutivo", "administrador"):
            return Response(content="rol debe ser 'ejecutivo' o 'administrador'", status_code=400)
        user_id = await _invitar_usuario_supabase(correo, nombre, rol)
        await _supabase_request("POST", "/Usuario", json={
            "id": user_id, "nombre": nombre, "rut": rut, "correo": correo,
            "celular": celular, "rol": rol, "password_provisional": False,
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
        update = {k: body[k] for k in ("nombre", "celular", "rol", "estado") if k in body}
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
# API — Importar clientes desde CSV
# ---------------------------------------------------------------------------

@app.post("/api/clientes/importar")
async def api_importar_clientes(request: Request, file: UploadFile = File(...)):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    usuario_id = perfil["id"]
    try:
        content = await file.read()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        todos_proyectos = await _supabase_request(
            "GET", "/Proyecto",
            params={"select": "id,nombre,nombres_csv,inmobiliaria_id"},
        ) or []
        todos_inmobiliarias = await _supabase_request(
            "GET", "/Inmobiliaria",
            params={"select": "id,empresa_id"},
        ) or []
        inmobiliaria_map = {i["id"]: i["empresa_id"] for i in todos_inmobiliarias}
        creados, duplicados, errores = 0, [], []
        for i, row in enumerate(reader):
            fila = i + 2
            try:
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

                if not nombre or not telefono:
                    errores.append({"fila": fila, "motivo": "Contacto o Teléfono vacío"})
                    continue
                if not nombre_proyecto:
                    errores.append({"fila": fila, "nombre": nombre, "motivo": "Proyecto vacío"})
                    continue
                proyecto = await _buscar_id_proyecto(nombre_proyecto, todos_proyectos)
                if not proyecto:
                    errores.append({"fila": fila, "nombre": nombre, "motivo": f"Proyecto '{nombre_proyecto}' sin mapeo"})
                    continue
                proyecto_id     = proyecto["id"]
                inmobiliaria_id = proyecto.get("inmobiliaria_id")
                empresa_id      = inmobiliaria_map.get(inmobiliaria_id) if inmobiliaria_id else None
                existente = await _supabase_request(
                    "GET", "/Cliente",
                    params={"Telefono": f"eq.{telefono}", "select": "id,usuario_id", "limit": "1"},
                )
                if existente:
                    owner_id = existente[0].get("usuario_id")
                    owner_nombre = "otro usuario"
                    if owner_id:
                        op = await _supabase_request("GET", "/Usuario",
                            params={"id": f"eq.{owner_id}", "select": "nombre", "limit": "1"})
                        if op:
                            owner_nombre = op[0].get("nombre", "otro usuario")
                    duplicados.append({"fila": fila, "nombre": nombre, "telefono": telefono, "propietario": owner_nombre})
                    continue
                await _supabase_request("POST", "/Cliente",
                    json={
                        "proyecto_id": proyecto_id, "inmobiliaria_id": inmobiliaria_id,
                        "empresa_id": empresa_id,
                        "Contacto": nombre, "Rut": rut, "Correo": correo, "Telefono": telefono,
                        "estado_crm": estado_crm, "Tramo de renta": tramo_renta,
                        "tiene_subsidio": tiene_subsidio, "tipo_subsidio": tipo_subsidio,
                        "tiene_propiedad": tiene_propiedad, "primer mensaje": True,
                        "wtsp_habilitado": True, "usuario_id": usuario_id,
                        "Fecha Ult. Gestión": datetime.now(timezone.utc).date().isoformat(),
                    },
                    extra_headers={"Prefer": "return=minimal"})
                creados += 1
            except Exception as ex:
                errores.append({"fila": fila, "motivo": str(ex)})
        return {
            "ok": True, "creados": creados,
            "duplicados": len(duplicados), "errores": len(errores),
            "detalle_duplicados": duplicados, "detalle_errores": errores,
        }
    except Exception as e:
        logger.exception("Error importando clientes")
        return Response(content=_safe_httpx_error(e), status_code=500, media_type="text/plain")


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

        # Detectar cuántos params espera el body y si header es IMAGE
        body_comp = next((cm for cm in components_meta if cm.get("type") == "BODY"), None)
        header_comp = next((cm for cm in components_meta if cm.get("type") == "HEADER"), None)
        needs_image = header_comp and header_comp.get("format") == "IMAGE"

        # Pool de valores para rellenar los params en orden
        pool = [
            nombre,
            (proyecto.get("nombre") or "") if proyecto else "",
            (proyecto.get("ubicacion") or "") if proyecto else "",
        ]

        # Extraer nombres de variables del body: {{name}}, {{1}}, etc.
        var_names = re.findall(r'\{\{([^}]+)\}\}', body_comp.get("text", "")) if body_comp else []
        body_text_params: List[Any] = []
        for i, var_name in enumerate(var_names):
            value = pool[i] if i < len(pool) else ""
            if var_name.isdigit():
                body_text_params.append(value)  # posicional: solo texto
            else:
                body_text_params.append({"parameter_name": var_name, "text": value})  # con nombre
        if not body_text_params:
            body_text_params = [nombre]

        image_url = (proyecto.get("imagen_url") or None) if (needs_image and proyecto) else None

        wa = await send_whatsapp_template(
            to=telefono, template_name=template_name, language_code=language_code,
            body_text_params=body_text_params, image_url=image_url,
        )
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json={"primer mensaje": False, "wtsp_habilitado": False})
        await upsert_prospecto(
            telefono_e164=telefono, nombre=nombre, rut=c.get("Rut"),
            rango_sueldo=c.get("Tramo de renta"), proyecto_id=proyecto_id,
            estado="PLANTILLA_ENVIADA", paso="BIENVENIDA", cliente_id=cliente_id,
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
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request(
        "GET", "/Empresa",
        params={"estado": "eq.activa", "select": "id,nombre,slug,logo_url,color_marca", "order": "nombre.asc"},
    )
    return rows or []


@app.get("/api/inmobiliarias")
async def api_listar_inmobiliarias(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    empresa_id = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": "id,nombre,empresa_id", "order": "nombre.asc"}
    if empresa_id:
        params["empresa_id"] = f"eq.{empresa_id}"
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
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    params: Dict[str, str] = {"select": "id,codigo,nombre,ubicacion,inmobiliaria_id", "order": "nombre.asc"}
    if inmobiliaria_id:
        params["inmobiliaria_id"] = f"eq.{inmobiliaria_id}"
    rows = await _supabase_request("GET", "/Proyecto", params=params)
    return rows or []


@app.post("/api/proyectos")
async def api_crear_proyecto(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    nombre          = (body.get("nombre") or "").strip()
    codigo          = (body.get("codigo") or "").strip()
    ubicacion       = (body.get("ubicacion") or "").strip() or None
    inmobiliaria_id = body.get("inmobiliaria_id")
    if not nombre or not codigo or not inmobiliaria_id:
        return Response(content="Faltan campos obligatorios", status_code=400)
    row = await _supabase_request("POST", "/Proyecto",
        json={"nombre": nombre, "codigo": codigo, "ubicacion": ubicacion, "inmobiliaria_id": inmobiliaria_id},
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@app.patch("/api/proyectos/{proyecto_id}")
async def api_editar_proyecto(proyecto_id: str, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    payload: Dict[str, Any] = {}
    if body.get("nombre"):    payload["nombre"]    = body["nombre"].strip()
    if body.get("codigo"):    payload["codigo"]    = body["codigo"].strip()
    if body.get("ubicacion"): payload["ubicacion"] = body["ubicacion"].strip()
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    await _supabase_request("PATCH", "/Proyecto",
        params={"id": f"eq.{proyecto_id}"},
        json=payload,
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
        params["inmobiliaria_id"] = f"eq.{inmobiliaria_id}"
    elif empresa_id:
        params["empresa_id"] = f"eq.{empresa_id}"
    if perfil.get("rol") != "administrador":
        params["usuario_id"] = f"eq.{perfil['id']}"
    rows = await _supabase_request("GET", "/Cliente", params=params)
    return rows or []


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
        rango       = (body.get("Tramo de renta") or "").strip() or None
        primer_msg  = bool(body.get("primer mensaje", True))

        if not nombre:
            return Response(content="Falta Contacto", status_code=400)
        if not telefono:
            return Response(content="Falta Telefono", status_code=400)
        if not proyecto_id:
            return Response(content="Falta proyecto_id", status_code=400)

        proyecto = await obtener_proyecto_por_id(proyecto_id)
        if not proyecto:
            return Response(content="Proyecto no encontrado", status_code=400)
        inmobiliaria_id = proyecto.get("inmobiliaria_id")
        empresa_id_row: Optional[str] = None
        if inmobiliaria_id:
            inm = await _supabase_request("GET", "/Inmobiliaria",
                params={"id": f"eq.{inmobiliaria_id}", "select": "empresa_id", "limit": "1"})
            if inm:
                empresa_id_row = inm[0].get("empresa_id")

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
                "inmobiliaria_id":    inmobiliaria_id,
                "empresa_id":         empresa_id_row,
                "Contacto":           nombre,
                "Rut":                rut or "",
                "Correo":             correo,
                "Telefono":           telefono,
                "Tramo de renta":     rango,
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


@app.post("/api/clientes/{cliente_id}/enviar-plantilla")
async def api_enviar_plantilla(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        rows = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
        if not rows:
            return Response(content="Cliente no encontrado", status_code=404)
        c           = rows[0]
        telefono    = _normalize_phone(c.get("Telefono") or "")
        nombre      = (c.get("Contacto") or "").strip()
        proyecto_id = (c.get("proyecto_id") or "").strip()

        if not telefono:
            return Response(content="Cliente sin teléfono", status_code=400)
        if not proyecto_id:
            return Response(content="Cliente sin proyecto_id — actualiza la BD", status_code=400)

        proyecto = await obtener_proyecto_por_id(proyecto_id)
        if not proyecto or not proyecto.get("nombre_plantilla"):
            return Response(content="Proyecto sin plantilla configurada", status_code=400)

        wa = await send_whatsapp_template(
            to=telefono,
            template_name=proyecto["nombre_plantilla"],
            language_code="es_CL",
            body_text_params=[nombre],
            image_url=proyecto.get("imagen_url"),
        )
        await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente_id}"}, json={"primer mensaje": False})
        await upsert_prospecto(
            telefono_e164=telefono, nombre=nombre, rut=c.get("Rut"),
            rango_sueldo=c.get("Tramo de renta"), proyecto_id=proyecto_id,
            estado="PLANTILLA_ENVIADA", paso="BIENVENIDA",
            cliente_id=cliente_id,
        )
        return {"ok": True, "wa": wa}
    except Exception as e:
        logger.exception("Error en enviar-plantilla")
        return Response(content=_safe_httpx_error(e), status_code=500, media_type="text/plain")


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


async def _descargar_documento_storage(url_storage: str) -> bytes:
    key = _supabase_service_role_key()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url_storage, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
    return r.content


async def _enviar_email_evaluacion(cliente_id: int) -> dict:
    email_remitente = os.getenv("EMAIL_REMITENTE")
    email_password  = os.getenv("EMAIL_PASSWORD")
    if not email_remitente or not email_password:
        raise RuntimeError("EMAIL_REMITENTE o EMAIL_PASSWORD no configurados")

    clientes = await _supabase_request("GET", "/Cliente",
        params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
    if not clientes:
        raise ValueError(f"Cliente {cliente_id} no encontrado")
    c = clientes[0]

    ejecutivos = await _supabase_request("GET", "/Ejecutivo",
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
    proyecto = c.get("proyecto_id") or "No registrado"
    renta    = c.get("Tramo de renta") or "No registrado"

    NOMBRES_TIPO = {
        "liquidacion_sueldo": "Liquidación de sueldo",
        "certificado_afp": "Certificado AFP",
        "carnet_identidad": "Cédula de identidad",
        "libreta_ahorro": "Libreta de ahorro",
        "informe_deudas": "Informe de deudas",
        "antiguedad_laboral": "Antigüedad laboral",
        "otro": "Otro documento",
    }

    filas_docs = "".join(
        f"<tr><td style='padding:4px 12px;color:#555;'>▸ {NOMBRES_TIPO.get(d.get('tipo',''), d.get('tipo',''))}</td>"
        f"<td style='padding:4px 12px;color:#333;'>{d.get('nombre_archivo','')}</td></tr>"
        for d in docs
    )

    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;">
      <h2 style="color:#1e3a5f;border-bottom:2px solid #1e3a5f;padding-bottom:8px;">
        📋 Solicitud de Evaluación de Crédito
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
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Teléfono</td>
          <td style="padding:8px 12px;">{telefono}</td>
        </tr>
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Proyecto</td>
          <td style="padding:8px 12px;">{proyecto}</td>
        </tr>
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Tramo de renta</td>
          <td style="padding:8px 12px;">{renta}</td>
        </tr>
      </table>
      <h3 style="color:#1e3a5f;margin-top:24px;">Documentos adjuntos ({len(docs)})</h3>
      <table style="border-collapse:collapse;font-size:13px;">
        {filas_docs}
      </table>
      <p style="font-size:12px;color:#aaa;margin-top:24px;">
        Generado automáticamente por el CRM WhatsApp.
      </p>
    </div>
    """

    msg = MIMEMultipart()
    msg["From"]    = email_remitente
    msg["To"]      = ", ".join(destinatarios)
    msg["Subject"] = f"📋 Evaluación de crédito — {nombre}"
    msg.attach(MIMEText(body_html, "html"))

    for doc in docs:
        url = doc.get("url_storage")
        nombre_archivo = doc.get("nombre_archivo") or doc.get("tipo") or "documento"
        if not url:
            continue
        try:
            file_bytes = await _descargar_documento_storage(url)
            mime_type  = doc.get("mime_type") or "application/octet-stream"
            main_type, sub_type = (mime_type.split("/", 1) if "/" in mime_type else ("application", "octet-stream"))
            part = MIMEBase(main_type, sub_type)
            part.set_payload(file_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=nombre_archivo)
            msg.attach(part)
        except Exception as e:
            logger.warning("No se pudo adjuntar '%s': %s", nombre_archivo, e)

    def _smtp_send():
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(email_remitente, email_password)
            server.sendmail(email_remitente, destinatarios, msg.as_string())

    await asyncio.get_event_loop().run_in_executor(None, _smtp_send)
    return {"enviado_a": destinatarios, "documentos_adjuntos": len(docs)}


@app.post("/api/clientes/{cliente_id}/enviar-evaluacion")
async def api_enviar_evaluacion(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        result = await _enviar_email_evaluacion(cliente_id)
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("Error enviando evaluación cliente %s", cliente_id)
        return Response(content=str(e), status_code=500, media_type="text/plain")


# ── Rutas de páginas (URLs limpias sin .html) ────────────────────────────────
@app.get("/empresas")
async def page_empresas():
    return FileResponse("frontend/empresas.html")

@app.get("/login")
async def page_login():
    return FileResponse("frontend/login.html")

@app.get("/inmobiliaria")
async def page_inmobiliaria():
    return FileResponse("frontend/inmobiliaria.html")

@app.get("/proyecto")
async def page_proyecto():
    return FileResponse("frontend/proyecto.html")


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
