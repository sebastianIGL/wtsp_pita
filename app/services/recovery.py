"""Recuperación de mensajes sin responder: cada 3 minutos retoma las conversaciones donde el cliente escribió y el bot no respondió
(por ejemplo porque el servidor se reinició durante la espera). Solo toma pendientes con más de 3 minutos y menos de
RECOVERY_MAX_ANTIGUEDAD_HORAS (24 por defecto, la ventana de WhatsApp para mensajes libres), los más antiguos primero, y deja
registrados en el log los que ya vencieron. Fuera del horario del bot duerme hasta que abra.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote D).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.clients.supabase import _supabase_request
from app.core.config import get_settings
from app.core.logging_config import enmascarar
from app.core.utilidades import _horas_desde
from app.services.horarios import _dentro_horario_bot, _segundos_hasta_horario_bot
from app.services.webhook import _procesar_webhook

logger = logging.getLogger("wtsp_pita")


_RECOVERY_CICLOS_ENTRE_AVISOS = 20   # un aviso de pendientes vencidos al arrancar y luego cada ~1 h (20 × 3 min)


async def _registrar_pendientes_vencidos(limite_iso: str, max_horas: int) -> None:
    """Deja en el log los pendientes más antiguos que el tope (no se reprocesan). Solo lectura."""
    try:
        vencidos = await _supabase_request("GET", "/Prospecto", params=[
            ("pendiente_respuesta", "eq.true"),
            ("ultimo_entrante_en", f"lt.{limite_iso}"),
            ("select", "id,telefono_e164,ultimo_entrante_en"),
            ("order", "ultimo_entrante_en.asc"),
            ("limit", "50"),
        ]) or []
        if not vencidos:
            return
        detalle = "; ".join(
            f"{v.get('id')}:{enmascarar(v.get('telefono_e164') or '')}:{(_horas_desde(v.get('ultimo_entrante_en')) or 0):.0f}h"
            for v in vencidos[:20])
        logger.warning(
            "Recovery: %d%s pendiente(s) con más de %d h ignorados (fuera de la ventana de 24 h de WhatsApp, "
            "no se les puede responder con un mensaje libre): %s",
            len(vencidos), "+" if len(vencidos) == 50 else "", max_horas, detalle)
    except Exception:
        logger.exception("Recovery: no se pudo listar los pendientes vencidos")


async def _recovery_loop():
    """Cada 3 min reintenta conversaciones donde el cliente escribió y el bot no respondió.

    Solo toma pendientes con más de 3 min y menos de RECOVERY_MAX_ANTIGUEDAD_HORAS (24 por defecto, la
    ventana de WhatsApp para mensajes libres), los más antiguos primero. Los demás se ignoran y se dejan
    registrados en el log."""
    await asyncio.sleep(60)  # 1 min de gracia al arrancar
    ciclo = 0
    while True:
        try:
            if not _dentro_horario_bot():
                # Solo ocurre si se configuró una ventana horaria (por defecto el bot es 24/7).
                # Fuera de la ventana el webhook guarda el mensaje y no responde; si el loop siguiera
                # corriendo, cada 3 min volvería a recoger los mismos pendientes y a registrar
                # "respuesta pospuesta". Se retoma solo al abrir el horario.
                await asyncio.sleep(_segundos_hasta_horario_bot())
                continue
            ahora = datetime.now(timezone.utc)
            hace_3_min = (ahora - timedelta(minutes=3)).isoformat()
            max_horas = get_settings().recovery_max_antiguedad_horas
            filtros = [
                ("pendiente_respuesta", "eq.true"),
                ("ultimo_entrante_en", f"lt.{hace_3_min}"),
                ("select", "id,telefono_e164,ultimo_texto_entrante"),
                ("order", "ultimo_entrante_en.asc"),   # primero los más antiguos (los que antes vencen)
                ("limit", "10"),
            ]
            limite_antiguedad = None
            if max_horas > 0:
                limite_antiguedad = (ahora - timedelta(hours=max_horas)).isoformat()
                filtros.append(("ultimo_entrante_en", f"gte.{limite_antiguedad}"))
            pendientes = await _supabase_request("GET", "/Prospecto", params=filtros) or []
            for p in pendientes:
                telefono   = p.get("telefono_e164") or ""
                ultimo_txt = p.get("ultimo_texto_entrante") or ""
                if not telefono or not ultimo_txt:
                    continue
                logger.info("Recovery: reprocesando mensaje pendiente de %s", telefono)
                asyncio.create_task(_procesar_webhook({
                    "from": telefono,
                    "type": "text",
                    "text": {"body": ultimo_txt},
                    "id":   f"recovery_{p['id']}",
                }, es_reintento=True))
            if limite_antiguedad and ciclo % _RECOVERY_CICLOS_ENTRE_AVISOS == 0:
                await _registrar_pendientes_vencidos(limite_antiguedad, max_horas)
            ciclo += 1
        except Exception:
            logger.exception("Error en _recovery_loop")
        await asyncio.sleep(180)  # revisar cada 3 minutos
