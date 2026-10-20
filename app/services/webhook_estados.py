"""Estados de entrega de WhatsApp (enviado, entregado, leído, fallido): se anota en el cliente el estado de la plantilla que le
mandó el CRM, solo hacia adelante (salvo los fallidos), y se registran los envíos fallidos. Los demás envíos (respuestas del
bot, avisos) no tienen cliente asociado y es lo normal.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote D).
"""
import logging
from typing import Dict

from app.clients.supabase import _supabase_request

logger = logging.getLogger("wtsp_pita")


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
        # Solo la plantilla manual del CRM guarda su wamid; los demás envíos (respuestas del bot, avisos) no tienen
        # cliente asociado y es lo normal. Se avisa como advertencia únicamente si el envío falló.
        logger.log(logging.WARNING if estado_crm == "fallido" else logging.INFO,
                   "WA status '%s' sin cliente para wamid=%s", meta_estado, wamid)
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

    if estado_crm == "fallido":
        await _supabase_request("POST", "/LogWhatsapp", json={
            "cliente_id":    c["id"],
            "wamid":         wamid,
            "error_code":    str(errors[0].get("code")) if errors else None,
            "error_message": errors[0].get("message") if errors else None,
        })
