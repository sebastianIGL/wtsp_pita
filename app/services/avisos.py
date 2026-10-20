"""Avisos al ejecutivo asignado por WhatsApp (plantilla ``recordatorio_wtsp``) y al admin (WhatsApp y correo).

Los avisos de ``_notificar_ejecutivo_wa`` (08:00–24:00) y ``_notificar_recordatorio_estado`` (09:00–19:00, hora de Chile)
esperan dentro de la propia función, con ``asyncio.sleep``, hasta que abre el horario. Esa espera vive solo en la memoria
del proceso: si el servidor se reinicia mientras un aviso duerme, el aviso se pierde (no se guarda nada). Es el comportamiento
actual y se conserva tal cual; los tests lo dejan fijado.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote C2).
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from app.clients.gmail import _gmail_send
from app.clients.supabase import _supabase_request
from app.clients.whatsapp import send_whatsapp_message, send_whatsapp_template
from app.core.config import get_settings
from app.core.utilidades import _normalize_phone
from app.services.horarios import (
    _dentro_horario_avisos,
    _dentro_horario_urgente,
    _segundos_hasta_horario_avisos,
    _segundos_hasta_horario_urgente,
)
from app.services.proyectos import obtener_proyecto_por_id

logger = logging.getLogger("wtsp_pita")


async def _notificar_ejecutivo_wa(cliente_id: int, nombre_cliente: str, accion: str) -> Dict[str, Any]:
    """Envía notificación WA al ejecutivo asignado al cliente (template recordatorio_wtsp).
    Retorna {"ok": True} si se envió, o {"ok": False, "motivo": "..."} si no se pudo."""
    if not _dentro_horario_urgente():
        # Horario ampliado (08:00–24:00 hora Chile) para no despertar al ejecutivo de madrugada,
        # pero sin retrasarlo tanto como los avisos rutinarios.
        await asyncio.sleep(_segundos_hasta_horario_urgente())
    try:
        clientes = await _supabase_request("GET", "/Cliente",
            params={"id": f"eq.{cliente_id}", "select": "usuario_id,Proyecto(nombre)", "limit": "1"}) or []
        if not clientes or not clientes[0].get("usuario_id"):
            return {"ok": False, "motivo": "sin ejecutivo asignado"}
        proyecto_nombre = (clientes[0].get("Proyecto") or {}).get("nombre") or "Sin proyecto"
        usuarios = await _supabase_request("GET", "/Usuario",
            params={"id": f"eq.{clientes[0]['usuario_id']}", "select": "nombre,celular", "limit": "1"}) or []
        if not usuarios:
            return {"ok": False, "motivo": "ejecutivo no encontrado"}
        celular = _normalize_phone(usuarios[0].get("celular") or "")
        if not celular:
            return {"ok": False, "motivo": f"ejecutivo '{usuarios[0].get('nombre') or ''}' sin celular configurado"}
        await send_whatsapp_template(
            to=celular,
            template_name="recordatorio_wtsp",
            language_code="es_CL",
            body_text_params=[nombre_cliente, accion, proyecto_nombre],
        )
        logger.info("WA notif ejecutivo → %s | cliente=%s | accion=%s | proyecto=%s",
                    celular, nombre_cliente, accion, proyecto_nombre)
        return {"ok": True}
    except Exception as e:
        logger.warning("No se pudo notificar ejecutivo WA: %s", e)
        return {"ok": False, "motivo": str(e)}


_TEXTOS_RECORDATORIO = {
    "primer_documento": "Recibió su primer documento",
    "docs_completos":   "Documentos completos, listo para llamar",
}


async def _notificar_recordatorio_estado(cliente_id: int, nombre_cliente: str, tipo: str) -> Dict[str, Any]:
    """Avisa al ejecutivo asignado cuando el bot mueve al cliente a un estado puntual
    ('primer_documento' | 'docs_completos'). No vuelve a enviar el mismo tipo de aviso
    hasta que el ejecutivo cambie estado_gestion (reconociendo que ya actuó)."""
    if not _dentro_horario_avisos():
        # No despertar al ejecutivo fuera de horario — esperar a que abra 9:00 hora Chile
        # (horario fijo de avisos, independiente del horario del bot).
        await asyncio.sleep(_segundos_hasta_horario_avisos())
    texto = _TEXTOS_RECORDATORIO.get(tipo)
    if not texto:
        return {"ok": False, "motivo": f"tipo '{tipo}' inválido"}
    try:
        clientes = await _supabase_request("GET", "/Cliente",
            params={"id": f"eq.{cliente_id}",
                    "select": "usuario_id,estado_gestion,ultimo_recordatorio_tipo,"
                              "ultimo_recordatorio_estado_gestion,Proyecto(nombre)",
                    "limit": "1"}) or []
        if not clientes:
            return {"ok": False, "motivo": "cliente no encontrado"}
        c = clientes[0]
        if not c.get("usuario_id"):
            return {"ok": False, "motivo": "sin ejecutivo asignado"}

        estado_gestion_actual = c.get("estado_gestion")
        if (c.get("ultimo_recordatorio_tipo") == tipo
                and c.get("ultimo_recordatorio_estado_gestion") == estado_gestion_actual):
            return {"ok": False, "motivo": "ya notificado — pendiente de que el ejecutivo cambie el estado de gestión"}

        proyecto_nombre = (c.get("Proyecto") or {}).get("nombre") or "Sin proyecto"
        usuarios = await _supabase_request("GET", "/Usuario",
            params={"id": f"eq.{c['usuario_id']}", "select": "nombre,celular", "limit": "1"}) or []
        if not usuarios:
            return {"ok": False, "motivo": "ejecutivo no encontrado"}
        celular = _normalize_phone(usuarios[0].get("celular") or "")
        if not celular:
            return {"ok": False, "motivo": f"ejecutivo '{usuarios[0].get('nombre') or ''}' sin celular configurado"}

        await send_whatsapp_template(
            to=celular,
            template_name="recordatorio_wtsp",
            language_code="es_CL",
            body_text_params=[nombre_cliente, texto, proyecto_nombre],
        )
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json={"ultimo_recordatorio_tipo": tipo, "ultimo_recordatorio_estado_gestion": estado_gestion_actual},
            extra_headers={"Prefer": "return=minimal"})
        logger.info("WA recordatorio → %s | cliente=%s | tipo=%s | proyecto=%s",
                    celular, nombre_cliente, tipo, proyecto_nombre)
        return {"ok": True}
    except Exception as e:
        logger.warning("No se pudo enviar recordatorio WA: %s", e)
        return {"ok": False, "motivo": str(e)}


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
        notify_phone = get_settings().admin_notify_phone
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
    notify_phone = get_settings().admin_notify_phone
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
            email_from = (get_settings().email_admin or "")
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
