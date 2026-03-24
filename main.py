from fastapi import FastAPI, Request, Response
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


app = FastAPI()

logger = logging.getLogger("wtsp_pita")
if not logger.handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


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
# Configuración de pasos conversacionales
# ---------------------------------------------------------------------------

# Cada valor puede usar: {nombre}, {rango_sueldo}, {datos}
PASOS_CONFIG: Dict[str, str] = {
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

En datos_extraidos reporta SOLO lo que el cliente reveló en ESTE mensaje:
  "ahorro_ok": true/false  (null si no lo mencionó)
  "trabajo_indefinido": true/false  (null si no lo mencionó)
  "complemento_renta": true/false  (null si no lo mencionó)""",

    "DOCUMENTACION": """OBJETIVO — PASO DOCUMENTACION:
El cliente completó las preguntas de calificación.
Datos recopilados: {datos}

1. Explícale qué documentos debe enviarte según su situación:

   Todos deben enviar:
   ▸ Cédula de identidad (ambos lados)
   ▸ Últimas 3 liquidaciones de sueldo
   ▸ Certificado de antigüedad laboral

   Si ahorro_ok es true, agregar:
   ▸ Cartola de ahorro de los últimos 12 meses

   Si complemento_renta es true, agregar:
   ▸ CI y últimas 3 liquidaciones del co-deudor

2. Ofrécele una llamada telefónica si tiene dudas o necesita orientación.
3. Queda a la espera de que envíe los documentos por este mismo chat.
4. Si confirma que enviará o ya envió documentos → "siguiente_paso": "ESPERA_DOCS".""",

    "ESPERA_DOCS": """OBJETIVO — PASO ESPERA DE DOCUMENTOS:
El cliente está en proceso de enviar su documentación.
- Responde sus consultas con amabilidad y paciencia.
- Si confirma el envío, agradece y confirma recepción.
- Responde preguntas sobre el proyecto con la información disponible.
- Si confirma envío → "siguiente_paso": "DOCS_RECIBIDOS".""",

    "DOCS_RECIBIDOS": """Los documentos fueron recibidos.
Informa al cliente que el equipo los revisará y se pondrá en contacto pronto.
Responde cualquier consulta con amabilidad.""",

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
    """Hace merge de nuevos_datos en el JSONB 'datos' y opcionalmente avanza el paso."""
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
    raw: Optional[Dict[str, Any]] = None,
    wa_message_id: Optional[str] = None,
):
    await _supabase_request(
        "POST",
        "/mensajes",
        json={
            "prospecto_id": prospecto_id,
            "direccion": direccion,
            "texto": text,
            "crudo": raw,
            "wa_id_mensaje": wa_message_id,
        },
    )


async def obtener_proyecto_por_codigo(codigo: str):
    rows = await _supabase_request(
        "GET",
        "/proyectos",
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
# IA — respuesta con contexto de paso
# ---------------------------------------------------------------------------

async def generar_respuesta_ia(
    *,
    prospecto: Dict,
    proyecto: Optional[Dict],
    historial: List[Dict],
    mensaje_actual: str,
) -> Dict[str, Any]:
    """
    Retorna:
      {
        "respuesta": str,
        "siguiente_paso": Optional[str],
        "datos_extraidos": Dict
      }
    """
    if not ANTHROPIC_API_KEY or anthropic is None:
        logger.warning("ANTHROPIC_API_KEY no configurada — usando eco")
        return {"respuesta": f"Hola 👋 Recibí: {mensaje_actual}", "siguiente_paso": None, "datos_extraidos": {}}

    nombre        = (prospecto.get("nombre") or "").strip() or "amigo/a"
    telefono      = prospecto.get("telefono_e164") or ""
    rut           = prospecto.get("rut") or "no registrado"
    rango_sueldo  = prospecto.get("rango_sueldo") or "no registrado"
    paso_actual   = prospecto.get("paso") or "INICIO"
    datos         = prospecto.get("datos") or {}

    proyecto_nombre   = (proyecto or {}).get("nombre") or "nuestro proyecto"
    proyecto_ubicacion = (proyecto or {}).get("ubicacion") or ""

    instrucciones = PASOS_CONFIG.get(paso_actual, PASOS_CONFIG["INICIO"]).format(
        nombre=nombre,
        rango_sueldo=rango_sueldo,
        datos=json.dumps(datos, ensure_ascii=False, indent=2),
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

RESPONDE ÚNICAMENTE con JSON válido (sin markdown, sin texto extra):
{{
  "respuesta": "texto para enviar por WhatsApp",
  "siguiente_paso": null,
  "datos_extraidos": {{}}
}}
Valores válidos de siguiente_paso: null | "DOCUMENTACION" | "ESPERA_DOCS" | "DOCS_RECIBIDOS" | "NO_INTERESADO"
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

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        system=system_prompt,
        messages=messages,
    )

    raw = response.content[0].text.strip()
    # Quitar markdown si Claude lo incluye
    if raw.startswith("```"):
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


@app.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()
    try:
        entry   = payload["entry"][0]
        changes = entry["changes"][0]
        value   = changes["value"]

        messages = value.get("messages", [])
        if not messages:
            return {"ok": True}

        msg         = messages[0]
        from_number = _normalize_phone(msg["from"])
        text        = (msg.get("text") or {}).get("body", "")

        if not text:
            return {"ok": True}  # imagen, audio, etc. — ignorar por ahora

        prospecto = None
        proyecto  = None

        if _supabase_url() and _supabase_service_role_key():
            prospecto = await upsert_prospecto(
                telefono_e164=from_number,
                ultimo_texto_entrante=text,
                estado="RESPONDIO",
            )
            if prospecto and prospecto.get("codigo_proyecto"):
                proyecto = await obtener_proyecto_por_codigo(prospecto["codigo_proyecto"])

        historial = []
        if prospecto and prospecto.get("id"):
            await insertar_mensaje(
                prospecto_id=prospecto["id"],
                direccion="entrante",
                text=text,
                raw=payload,
                wa_message_id=msg.get("id"),
            )
            try:
                historial = await obtener_historial_mensajes(prospecto["id"])
            except Exception:
                pass

        resultado = await generar_respuesta_ia(
            prospecto=prospecto or {},
            proyecto=proyecto,
            historial=historial,
            mensaje_actual=text,
        )

        reply_text     = resultado["respuesta"]
        siguiente_paso = resultado["siguiente_paso"]
        datos_extraidos = resultado["datos_extraidos"]

        await send_whatsapp_message(to=from_number, text=reply_text)

        if prospecto and prospecto.get("id") and _supabase_url() and _supabase_service_role_key():
            await insertar_mensaje(
                prospecto_id=prospecto["id"],
                direccion="saliente",
                text=reply_text,
            )
            if datos_extraidos or siguiente_paso:
                await actualizar_datos_prospecto(
                    prospecto["id"],
                    datos_extraidos,
                    siguiente_paso,
                )

    except Exception as e:
        logger.exception("Error en /webhook")
        return {"ok": False, "error": _safe_httpx_error(e)}

    return {"ok": True}


# ---------------------------------------------------------------------------
# Ingesta de prospectos (primer mensaje)
# ---------------------------------------------------------------------------

@app.post("/prospectos/ingesta")
async def ingestar_prospecto(request: Request):
    """
    Crea/actualiza un lead y envía el primer mensaje template.
    Requiere header X-API-Key == INGEST_API_KEY (si está configurada).

    Body JSON:
      {
        "telefono_e164":  "569XXXXXXXX",
        "nombre":         "Javiera",
        "rut":            "12.345.678-9",   (opcional)
        "rango_sueldo":   "$800.000",        (opcional)
        "codigo_proyecto": "ds19_hacienda_lo_errazuriz"
      }
    """
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
        nombre        = (body.get("nombre") or body.get("first_name") or "").strip() or None
        rut           = (body.get("rut") or "").strip() or None
        rango_sueldo  = (body.get("rango_sueldo") or "").strip() or None
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
            paso="INICIO",
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
                text=None,
                raw={
                    "plantilla":   proyecto["nombre_plantilla"],
                    "parametros":  [nombre],
                    "wa":          wa_resp,
                },
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
    """Envía la plantilla hello_world a un número de prueba."""
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
