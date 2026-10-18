"""Envío de correo por la API de Gmail con una cuenta de servicio (delegación de dominio).

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). ``_gmail_get_token_sync`` es síncrono (google-auth usa
``requests``) y se ejecuta en un executor; el envío en sí usa el cliente HTTP compartido y nunca se reintenta.
"""
import asyncio
import base64
import json
import re
from email import encoders as _email_encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

from app.core.config import get_settings
from app.core.http import http_client


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
            raise RuntimeError(f"Gmail API error {r.status_code}: {r.text}")
