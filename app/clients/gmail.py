"""Envío de correo por la API de Gmail con una cuenta de servicio (delegación de dominio).

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). ``_gmail_get_token_sync`` es síncrono (google-auth usa
``requests``) y se ejecuta en un executor; el envío en sí usa el cliente HTTP compartido y nunca se reintenta.
"""
import asyncio
import base64
import json
import logging
import re
from email import encoders as _email_encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.http import http_client

logger = logging.getLogger("wtsp_pita")

_MAX_DETALLE_LOG = 600


def _prefijo(direccion: str) -> str:
    return f"No se pudo enviar el correo desde {direccion}: "


def explicar_error_de_token(direccion: str, e: BaseException) -> Optional[str]:
    """Texto claro para la persona usuaria si Google no entrega permiso para actuar como ``direccion`` (error al pedir el token de la cuenta de servicio); None si no se reconoce
    (entonces el error sigue el camino genérico). Nunca incluye el texto crudo de Google."""
    t = str(e).lower()
    if "invalid impersonation" in t or "invalid email or user id" in t or "not a valid email" in t or ("invalid_grant" in t and "account" not in t):
        return (_prefijo(direccion) + "Google no la reconoce como un usuario de Google Workspace. Revisa que el correo o el alias de esa persona en el CRM sea su dirección principal en "
                "Google Workspace (no un alias ni un correo personal) y que su cuenta esté activa.")
    if "unauthorized_client" in t or "not authorized for any of the scopes" in t:
        return (_prefijo(direccion) + "la cuenta de servicio no tiene permiso para enviar en su nombre. Un administrador de Google Workspace debe revisar la delegación de todo el dominio "
                "(permiso gmail.send para la cuenta de servicio).")
    if "invalid_grant" in t and any(w in t for w in ("deleted", "disabled", "suspended")):
        return _prefijo(direccion) + "esa cuenta está suspendida o eliminada en Google Workspace."
    return None


def explicar_respuesta_de_gmail(direccion: str, status: int, cuerpo: str) -> Optional[str]:
    """Lo mismo para una respuesta de error de la API de Gmail al enviar. Solo se explican los casos de configuración que alguien puede corregir; el resto (por ejemplo, Google caído) sigue
    el camino genérico y su detalle queda en el log."""
    try:
        err = (json.loads(cuerpo) or {}).get("error") or {}
        if not isinstance(err, dict):
            err = {}
    except Exception:
        err = {}
    estado = str(err.get("status") or "").upper()
    texto = (str(err.get("message") or "") + " " + " ".join(str(d.get("reason", "")) for d in (err.get("errors") or []) if isinstance(d, dict))).lower()
    if status == 413 or "too large" in texto or "exceeds the maximum" in texto:
        return _prefijo(direccion) + "los documentos adjuntos pesan demasiado para un solo correo. Envía menos documentos o más livianos."
    if status == 400 and (estado == "FAILED_PRECONDITION" or "mail service not enabled" in texto):
        return (_prefijo(direccion) + "esa cuenta no tiene Gmail habilitado. Revisa en Google Workspace que tenga una licencia asignada y que Gmail esté activo para su unidad organizativa.")
    if status == 403 and ("accessnotconfigured" in texto or "has not been used in project" in texto or "is disabled" in texto):
        return _prefijo(direccion) + "la API de Gmail no está habilitada en el proyecto de Google Cloud de la cuenta de servicio."
    if status == 429 or "ratelimitexceeded" in texto or "quota" in texto:
        return _prefijo(direccion) + "Google limitó los envíos de esa cuenta por ahora. Intenta de nuevo en unos minutos."
    if status == 403 or estado == "PERMISSION_DENIED":
        return (_prefijo(direccion) + "Google rechazó el permiso para enviar en su nombre. Un administrador de Google Workspace debe revisar la delegación de todo el dominio "
                "(permiso gmail.send) y que la cuenta esté activa.")
    if status == 400 and ("invalid to header" in texto or "invalid_to" in texto or "recipient" in texto):
        return _prefijo(direccion) + "Google rechazó un destinatario. Revisa que los correos de destino estén bien escritos."
    return None


# ── Gmail API (email transaccional via Service Account) ──────────────────────
def _gmail_get_token_sync(impersonate_email: str) -> str:
    """Obtiene access token de Gmail API via Service Account. Síncrono — usar en executor."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    sa_info = json.loads(get_settings().google_service_account_json or "{}")
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
    try:
        token = await asyncio.get_event_loop().run_in_executor(
            None, _gmail_get_token_sync, impersonate
        )
    except Exception as e:
        explicado = explicar_error_de_token(impersonate, e)
        if explicado:                                                 # error de configuración que alguien puede corregir: se le dice cuál es
            logger.warning("Gmail: Google no dio permiso para enviar desde %s (%s): %s", impersonate, type(e).__name__, str(e)[:_MAX_DETALLE_LOG])
            raise AppError(explicado, 502) from e
        raise

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
            part.add_header("Content-Disposition", "attachment", filename=a["filename"])
            msg.attach(part)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(html, "html"))

    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    async with http_client(60) as client:
        r = await client.post(
            f"https://gmail.googleapis.com/gmail/v1/users/{impersonate}/messages/send",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"raw": raw},
        )
        if r.status_code not in (200, 201):
            explicado = explicar_respuesta_de_gmail(impersonate, r.status_code, r.text)
            if explicado:
                logger.warning("Gmail: el envío desde %s falló (HTTP %s): %s", impersonate, r.status_code, r.text[:_MAX_DETALLE_LOG])
                raise AppError(explicado, 502)
            raise RuntimeError(f"Gmail API error {r.status_code}: {r.text}")
