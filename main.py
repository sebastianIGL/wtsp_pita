from fastapi import FastAPI, Request, Response
import os
import httpx
from typing import Optional

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


VERIFY_TOKEN = _get_env("WA_VERIFY_TOKEN", "WHATSAPP_VERIFY_TOKEN")
ACCESS_TOKEN = _get_env("WA_ACCESS_TOKEN", "WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = _get_env("WA_PHONE_NUMBER_ID", "WHATSAPP_PHONE_NUMBER_ID")
GRAPH_VERSION = _get_env("WA_GRAPH_VERSION", "WHATSAPP_GRAPH_API_VERSION", default="v22.0")

# 1) Endpoint GET para verificación del webhook
@app.get("/webhook")
async def verify_webhook(request: Request):
    if not VERIFY_TOKEN:
        return Response(content="WA_VERIFY_TOKEN no está configurado", status_code=500)

    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
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
        from_number = msg["from"]  # número del usuario
        text = msg.get("text", {}).get("body", "")

        # Respuesta simple (eco + saludo)
        reply_text = f"Hola 👋 Recibí: {text}"

        await send_whatsapp_message(to=from_number, text=reply_text)

    except Exception as e:
        # Para debug: ideal loguearlo
        return {"ok": False, "error": str(e)}

    return {"ok": True}

async def send_whatsapp_message(to: str, text: str):
    if not ACCESS_TOKEN:
        raise RuntimeError("Falta WA_ACCESS_TOKEN (token de acceso de Meta)")
    if not PHONE_NUMBER_ID:
        raise RuntimeError("Falta WA_PHONE_NUMBER_ID (Phone Number ID de WhatsApp Cloud API)")

    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )