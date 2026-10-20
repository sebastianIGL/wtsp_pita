"""Entrada del webhook de WhatsApp: resultado de la firma de Meta, deduplicación rápida en memoria de los mensajes ya vistos
(la definitiva es el índice único de la base) y lanzamiento de tareas en segundo plano reteniendo su referencia.

La memoria de mensajes vistos se pierde al reiniciar el servidor; es el comportamiento actual y lo cubre el índice único.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote D).
"""
import asyncio
import logging
from typing import Any

from app.core.config import get_settings
from app.core.webhook_signature import debe_rechazar

logger = logging.getLogger("wtsp_pita")


# Cache en memoria para deduplicar webhooks duplicados de Meta (últimos 500 wamids)
_wamids_procesados: set = set()
_wamids_orden: list = []
_WAMID_MAX = 500
# Referencias a las tareas en segundo plano: sin ellas el recolector de basura podría descartarlas a mitad de camino.
_tareas_fondo: set = set()


def _lanzar(coro) -> "asyncio.Task":
    tarea = asyncio.create_task(coro)
    _tareas_fondo.add(tarea)
    tarea.add_done_callback(_tareas_fondo.discard)
    return tarea


def _es_mensaje_nuevo(msg: Any) -> bool:
    """Deduplicación rápida en memoria (últimos 500 wamids). La definitiva es el índice único de la base."""
    if not isinstance(msg, dict):
        return False
    wamid = msg.get("id", "")
    if wamid and wamid in _wamids_procesados:
        logger.info("Webhook duplicado ignorado: %s", wamid)
        return False
    if wamid:
        _wamids_procesados.add(wamid)
        _wamids_orden.append(wamid)
        if len(_wamids_orden) > _WAMID_MAX:
            old = _wamids_orden.pop(0)
            _wamids_procesados.discard(old)
    return True


def _registrar_firma_webhook(firma: str) -> bool:
    """Registra el resultado de la firma y dice si hay que rechazar la petición (solo con ENFORCE y secreto)."""
    rechazar = debe_rechazar(firma, get_settings().wa_signature_enforce)
    if firma == "valida":
        logger.info("Webhook: firma válida", extra={"firma_webhook": firma})
    elif firma != "sin_secreto":
        logger.warning("Webhook: firma %s%s", firma, " → se rechaza" if rechazar else " (solo se registra)",
                       extra={"firma_webhook": firma, "se_rechaza": rechazar})
    return rechazar
