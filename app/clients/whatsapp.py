"""WhatsApp Cloud API (Meta): enviar mensajes y plantillas y descargar archivos recibidos.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). Usa el cliente HTTP compartido y NUNCA reintenta un envío
(un reintento podría duplicar el mensaje al cliente). Las constantes se leen del entorno al importar, como antes.
"""
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.http import http_client


TOKEN_ACCESO       = get_settings().wa_access_token
ID_NUMERO_TELEFONO = get_settings().wa_phone_number_id
VERSION_GRAPH      = get_settings().wa_graph_version


async def descargar_media_whatsapp(media_id: str) -> tuple[bytes, str, str]:
    """
    Descarga un archivo multimedia de WhatsApp.
    Retorna (bytes, mime_type, nombre_archivo).
    """
    if not TOKEN_ACCESO:
        raise RuntimeError("Falta WA_ACCESS_TOKEN")

    headers = {"Authorization": f"Bearer {TOKEN_ACCESO}"}

    async with http_client(30) as client:
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
    async with http_client(20) as client:
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

    async with http_client(20) as client:
        r = await client.post(url, headers=headers, json=data)
        r.raise_for_status()
        return r.json()
