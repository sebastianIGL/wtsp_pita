from fastapi import FastAPI, Request, Response
import os
import httpx
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # Si python-dotenv no está instalado, igual se puede usar variables de entorno del sistema.
    pass


app = FastAPI()

def _get_env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


TOKEN_VERIFICACION = _get_env("WA_VERIFY_TOKEN", "WHATSAPP_VERIFY_TOKEN")
TOKEN_ACCESO = _get_env("WA_ACCESS_TOKEN", "WHATSAPP_ACCESS_TOKEN")
ID_NUMERO_TELEFONO = _get_env("WA_PHONE_NUMBER_ID", "WHATSAPP_PHONE_NUMBER_ID")
VERSION_GRAPH = _get_env("WA_GRAPH_VERSION", "WHATSAPP_GRAPH_API_VERSION", default="v22.0")


def _supabase_url() -> Optional[str]:
    return _get_env(
        "SUPABASE_URL",
        "SUPABASE_PROJECT_URL",
        "SUPABASE_REST_URL",
    )


def _supabase_service_role_key() -> Optional[str]:
    return _get_env(
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE",
    )


def _ingest_api_key() -> Optional[str]:
    return _get_env("INGEST_API_KEY")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in (phone or "").strip() if ch.isdigit())


def _supabase_rest_base() -> str:
    supabase_url = _supabase_url()
    if not supabase_url:
        raise RuntimeError("Falta SUPABASE_URL")
    return f"{supabase_url.rstrip('/')}/rest/v1"


def _supabase_headers() -> dict:
    supabase_key = _supabase_service_role_key()
    if not supabase_key:
        raise RuntimeError("Falta SUPABASE_SERVICE_ROLE_KEY")
    return {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
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
        # Supabase REST can return empty body on some operations
        if not r.content:
            return None
        return r.json()


async def upsert_prospecto(
    *,
    telefono_e164: str,
    nombre: Optional[str] = None,
    codigo_proyecto: Optional[str] = None,
    estado: Optional[str] = None,
    paso: Optional[str] = None,
    ultimo_texto_entrante: Optional[str] = None,
):
    row = {
        "telefono_e164": telefono_e164,
        "actualizado_en": _utc_now_iso(),
    }
    if nombre is not None:
        row["nombre"] = nombre
    if codigo_proyecto is not None:
        row["codigo_proyecto"] = codigo_proyecto
    if estado is not None:
        row["estado"] = estado
    if paso is not None:
        row["paso"] = paso
    if ultimo_texto_entrante is not None:
        row["ultimo_texto_entrante"] = ultimo_texto_entrante
        row["ultimo_entrante_en"] = _utc_now_iso()

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


async def insertar_mensaje(
    *,
    prospecto_id: str,
    direccion: str,
    text: Optional[str],
    raw: Optional[Dict[str, Any]] = None,
    wa_message_id: Optional[str] = None,
):
    row = {
        "prospecto_id": prospecto_id,
        "direccion": direccion,
        "texto": text,
        "crudo": raw,
        "wa_id_mensaje": wa_message_id,
    }
    await _supabase_request("POST", "/mensajes", json=row)


async def obtener_proyecto_por_codigo(codigo: str):
    rows = await _supabase_request(
        "GET",
        "/proyectos",
        params={
            "codigo": f"eq.{codigo}",
            "select": "codigo,nombre,ubicacion,nombre_plantilla,idioma_plantilla",
            "limit": "1",
        },
    )
    if not rows:
        return None
    return rows[0]

# 1) Endpoint GET para verificación del webhook
@app.get("/webhook")
async def verify_webhook(request: Request):
    if not TOKEN_VERIFICACION:
        return Response(content="WA_VERIFY_TOKEN no está configurado", status_code=500)

    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == TOKEN_VERIFICACION:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)

# 2) Endpoint POST donde llegan los eventos (mensajes)
@app.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()

    # Extrae mensajes entrantes (estructura típica de WhatsApp Cloud API)
    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        messages = value.get("messages", [])
        if not messages:
            return {"ok": True}  # puede ser un status update u otro evento

        msg = messages[0]
        from_number = _normalize_phone(msg["from"])  # número del usuario
        text = msg.get("text", {}).get("body", "")

        prospecto = None
        if _supabase_url() and _supabase_service_role_key():
            prospecto = await upsert_prospecto(
                telefono_e164=from_number,
                ultimo_texto_entrante=text,
                estado="RESPONDIO",
            )
            if prospecto and prospecto.get("id"):
                await insertar_mensaje(
                    prospecto_id=prospecto["id"],
                    direccion="entrante",
                    text=text,
                    raw=payload,
                    wa_message_id=msg.get("id"),
                )

        # Respuesta simple (eco + saludo)
        reply_text = f"Hola 👋 Recibí: {text}"

        await send_whatsapp_message(to=from_number, text=reply_text)

        if prospecto and prospecto.get("id") and _supabase_url() and _supabase_service_role_key():
            await insertar_mensaje(
                prospecto_id=prospecto["id"],
                direccion="saliente",
                text=reply_text,
                raw=None,
                wa_message_id=None,
            )

    except Exception as e:
        # Para debug: ideal loguearlo
        return {"ok": False, "error": str(e)}

    return {"ok": True}


@app.post("/prospectos/ingesta")
async def ingestar_prospecto(request: Request):
    """Crea/actualiza un lead y envía el primer mensaje template.

    Se usa cuando el lead llega antes de que el usuario escriba.
    Requiere header: X-API-Key == INGEST_API_KEY (si está configurada).

    Body JSON esperado:
      {
                "telefono_e164": "569XXXXXXXX",
                "nombre": "Javiera",
                "codigo_proyecto": "miraflores_chillan"
      }
    """
    ingest_api_key = _ingest_api_key()
    if ingest_api_key:
        provided = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
        if not provided or provided != ingest_api_key:
            return Response(content="Unauthorized", status_code=401)

    if not _supabase_url() or not _supabase_service_role_key():
        return Response(content="Supabase no está configurado", status_code=500)

    body = await request.json()
    phone = _normalize_phone(
        body.get("telefono_e164")
        or body.get("phone_e164")
        or body.get("telefono")
        or body.get("phone")
        or ""
    )
    nombre = (body.get("nombre") or body.get("first_name") or "").strip() or None
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
        codigo_proyecto=codigo_proyecto,
        estado="PLANTILLA_ENVIADA",
        paso="INICIO",
    )

    template_vars = [
        nombre or "",  # {{1}}
    ]

    wa_resp = await send_whatsapp_template(
        to=phone,
        template_name=proyecto["nombre_plantilla"],
        language_code=proyecto.get("idioma_plantilla") or "es",
        body_text_params=template_vars,
    )

    if prospecto and prospecto.get("id"):
        await insertar_mensaje(
            prospecto_id=prospecto["id"],
            direccion="saliente",
            text=None,
            raw={"plantilla": proyecto["nombre_plantilla"], "parametros": template_vars, "wa": wa_resp},
            wa_message_id=(wa_resp or {}).get("messages", [{}])[0].get("id") if isinstance(wa_resp, dict) else None,
        )

    return {"ok": True, "prospecto_id": (prospecto or {}).get("id")}

async def send_whatsapp_message(to: str, text: str):
    if not TOKEN_ACCESO:
        raise RuntimeError("Falta WA_ACCESS_TOKEN (token de acceso de Meta)")
    if not ID_NUMERO_TELEFONO:
        raise RuntimeError("Falta WA_PHONE_NUMBER_ID (Phone Number ID de WhatsApp Cloud API)")

    url = f"https://graph.facebook.com/{VERSION_GRAPH}/{ID_NUMERO_TELEFONO}/messages"
    headers = {
        "Authorization": f"Bearer {TOKEN_ACCESO}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
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
):
    if not TOKEN_ACCESO:
        raise RuntimeError("Falta WA_ACCESS_TOKEN (token de acceso de Meta)")
    if not ID_NUMERO_TELEFONO:
        raise RuntimeError("Falta WA_PHONE_NUMBER_ID (Phone Number ID de WhatsApp Cloud API)")

    url = f"https://graph.facebook.com/{VERSION_GRAPH}/{ID_NUMERO_TELEFONO}/messages"
    headers = {
        "Authorization": f"Bearer {TOKEN_ACCESO}",
        "Content-Type": "application/json",
    }

    parameters = [{"type": "text", "text": p} for p in body_text_params]
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": [
                {
                    "type": "body",
                    "parameters": parameters,
                }
            ],
        },
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=data)
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )