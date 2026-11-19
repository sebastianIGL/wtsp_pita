"""Aviso por WhatsApp a cada persona a la que le cargaron o repartieron clientes nuevos: «tienes 12 leads nuevos que puedes contactar».

Usa la plantilla de avisos que ya existe (``recordatorio_wtsp``: {{1}} a quién, {{2}} qué hacer, {{3}} proyecto), así que no hace falta una plantilla nueva en Meta. Un solo mensaje por persona y por carga o reparto
(no uno por cliente). No se avisa a quien se asignó los clientes a sí misma, ni a quien no tiene celular. De noche espera al horario de los avisos (08:00–24:00, hora de Chile) en vez de despertar a nadie.
Nunca lanza: un fallo con una persona se registra y no detiene a las demás.
"""
import asyncio
import logging
from typing import Dict, Optional

from app.clients.supabase import _supabase_request
from app.clients.whatsapp import send_whatsapp_template
from app.core.config import get_settings
from app.core.utilidades import _normalize_phone
from app.services.horarios import _dentro_horario_urgente, _segundos_hasta_horario_urgente

logger = logging.getLogger("wtsp_pita")

MAX_PERSONAS = 60
PAUSA_ENTRE_ENVIOS_S = 0.3


def textos(cantidad: int) -> Dict[str, str]:
    """Lo que dice el aviso: «3 leads nuevos» / «Tienes 3 leads nuevos que puedes contactar» (en singular si es uno)."""
    nuevos = "1 lead nuevo" if cantidad == 1 else f"{cantidad:,} leads nuevos".replace(",", ".")
    return {"quien": nuevos, "accion": f"Tienes {nuevos} que puedes contactar", "proyecto": "tu CRM"}


async def avisar(asignados: Dict[str, int], actor_id: Optional[str]) -> Dict[str, int]:
    """``asignados``: ``{usuario_id: cantidad}`` (la clave vacía = sin asignar, no se avisa). ``actor_id``: quien hizo la carga o el reparto (a esa persona no se le avisa de lo que se asignó a sí misma)."""
    resultado = {"avisados": 0, "omitidos": 0, "fallidos": 0}
    try:
        if not _dentro_horario_urgente():
            await asyncio.sleep(_segundos_hasta_horario_urgente())
        for uid, cantidad in list((asignados or {}).items())[:MAX_PERSONAS]:
            if not uid or not isinstance(cantidad, int) or cantidad <= 0 or str(uid) == str(actor_id or ""):
                resultado["omitidos"] += 1
                continue
            try:
                usuarios = await _supabase_request("GET", "/Usuario", params={"id": f"eq.{uid}", "select": "celular,estado", "limit": "1"}) or []
                celular = _normalize_phone((usuarios[0].get("celular") if usuarios else "") or "")
                if not celular or usuarios[0].get("estado") == 0:
                    resultado["omitidos"] += 1
                    continue
                t = textos(cantidad)
                await send_whatsapp_template(to=celular, template_name=get_settings().template_recordatorio_wa, language_code="es_CL", body_text_params=[t["quien"], t["accion"], t["proyecto"]])
                resultado["avisados"] += 1
                await asyncio.sleep(PAUSA_ENTRE_ENVIOS_S)
            except Exception as e:
                resultado["fallidos"] += 1
                logger.warning("Leads nuevos | no se pudo avisar a una persona (%s)", type(e).__name__)
    except Exception as e:
        logger.warning("Leads nuevos | no se pudieron enviar los avisos (%s)", type(e).__name__)
    return resultado
