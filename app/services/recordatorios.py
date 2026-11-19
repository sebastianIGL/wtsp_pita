"""Avisos programados por WhatsApp para un recordatorio con hora («llamar a las 14:00» → aviso a las 13:50).

El recordatorio del cliente guarda la fecha y la hora en ``Cliente.recordatorio_at`` (la hora tal como la escribió la persona, en hora de Chile). Si además pidió el aviso por WhatsApp, se guarda aquí una fila
(``RecordatorioAviso``) con el momento exacto en que hay que mandarlo; cada minuto un ciclo manda los que ya llegaron, a la plantilla de recordatorio y al celular de la persona que lo creó.

Cuidados: se manda **una sola vez** (se reclama la fila antes de enviar); si el servidor estuvo caído y el aviso se atrasó más de 3 horas, se descarta en vez de mandarlo fuera de hora; crear un aviso nuevo para el
mismo cliente cancela el anterior; un fallo con un aviso no detiene a los demás. Si la tabla aún no existe (falta el SQL), el ciclo lo registra y espera cada vez más.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.clients.supabase import _supabase_request
from app.clients.whatsapp import send_whatsapp_template
from app.core.config import get_settings
from app.core.utilidades import _normalize_phone

logger = logging.getLogger("wtsp_pita")

TZ = ZoneInfo("America/Santiago")
MINUTOS_ANTES = (0, 5, 10, 15, 30, 60)
MINUTOS_ANTES_POR_DEFECTO = 10
ATRASO_MAXIMO = timedelta(hours=3)
INTERVALO_S = 60
INICIO_S = 90
ESPERA_MAX_ERROR_S = 1800
TOPE_POR_CICLO = 30
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def momento_del_aviso(fecha: Any, hora: Any, minutos_antes: Any) -> Optional[datetime]:
    """El instante (hora de Chile) en que hay que avisar: fecha y hora del recordatorio menos los minutos pedidos. ``None`` si algo no es válido."""
    if not (isinstance(fecha, str) and _FECHA.match(fecha) and isinstance(hora, str) and _HORA.match(hora)):
        return None
    if minutos_antes is None:
        minutos_antes = MINUTOS_ANTES_POR_DEFECTO
    if isinstance(minutos_antes, bool) or minutos_antes not in MINUTOS_ANTES:
        return None
    try:
        t = datetime.fromisoformat(f"{fecha}T{hora}:00").replace(tzinfo=TZ)
    except ValueError:
        return None
    return t - timedelta(minutes=int(minutos_antes))


async def programar(cliente_id: int, usuario_id: str, enviar_en: datetime, motivo: str) -> Dict[str, Any]:
    """Guarda el aviso; el anterior pendiente de ese cliente y esa persona queda cancelado (solo cuenta el último)."""
    await cancelar(cliente_id, usuario_id)
    filas = await _supabase_request("POST", "/RecordatorioAviso", json={"cliente_id": cliente_id, "usuario_id": usuario_id, "enviar_en": enviar_en.astimezone(timezone.utc).isoformat(), "estado": "pendiente",
                                                                         "motivo": " ".join(str(motivo or "").split())[:150] or "Sin motivo especificado"},
                                    extra_headers={"Prefer": "return=representation"}) or []
    return filas[0] if filas else {}


async def cancelar(cliente_id: int, usuario_id: Optional[str] = None) -> None:
    params = {"cliente_id": f"eq.{cliente_id}", "estado": "eq.pendiente"}
    if usuario_id:
        params["usuario_id"] = f"eq.{usuario_id}"
    await _supabase_request("PATCH", "/RecordatorioAviso", params=params, json={"estado": "cancelado"})


async def _enviar(aviso: Dict[str, Any]) -> bool:
    """Manda la plantilla al celular de quien creó el aviso. True si salió."""
    usuarios = await _supabase_request("GET", "/Usuario", params={"id": f"eq.{aviso['usuario_id']}", "select": "celular,estado", "limit": "1"}) or []
    celular = _normalize_phone((usuarios[0].get("celular") if usuarios else "") or "")
    if not celular or (usuarios and usuarios[0].get("estado") == 0):
        return False
    clientes = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{aviso['cliente_id']}", "select": "Contacto,Proyecto(nombre)", "limit": "1"}) or []
    if not clientes:
        return False
    c = clientes[0]
    nombre = (c.get("Contacto") or "—").strip()
    proyecto = ((c.get("Proyecto") or {}).get("nombre") or "Sin proyecto").strip()
    motivo = " ".join(str(aviso.get("motivo") or "").split())[:150] or "Sin motivo especificado"
    await send_whatsapp_template(to=celular, template_name=get_settings().template_recordatorio_wa, language_code="es_CL", body_text_params=[nombre, motivo, proyecto])
    return True


async def ciclo(ahora: Optional[datetime] = None) -> Dict[str, int]:
    """Manda los avisos que ya llegaron. Devuelve cuántos salieron, se descartaron por atrasados y fallaron."""
    ahora = ahora or datetime.now(timezone.utc)
    resultado = {"enviados": 0, "vencidos": 0, "fallidos": 0}
    pendientes: List[Dict[str, Any]] = await _supabase_request("GET", "/RecordatorioAviso", params={"estado": "eq.pendiente", "enviar_en": f"lte.{ahora.isoformat()}", "select": "id,cliente_id,usuario_id,enviar_en,motivo",
                                                                                                   "order": "enviar_en.asc", "limit": str(TOPE_POR_CICLO)}) or []
    for a in pendientes:
        try:
            programado = datetime.fromisoformat(str(a["enviar_en"]).replace("Z", "+00:00"))
            programado = programado if programado.tzinfo else programado.replace(tzinfo=timezone.utc)
            nuevo = "vencido" if ahora - programado > ATRASO_MAXIMO else "enviado"
            reclamado = await _supabase_request("PATCH", "/RecordatorioAviso", params={"id": f"eq.{a['id']}", "estado": "eq.pendiente"},
                                                json={"estado": nuevo, "enviado_en": ahora.isoformat()}, extra_headers={"Prefer": "return=representation"}) or []
            if not reclamado:
                continue                                     # otro ciclo ya se lo llevó
            if nuevo == "vencido":
                resultado["vencidos"] += 1
                continue
            try:
                ok = await _enviar(a)
            except Exception as e:
                logger.warning("Recordatorio programado | no se pudo enviar el aviso %s (%s)", a.get("id"), type(e).__name__)
                ok = False
            if ok:
                resultado["enviados"] += 1
            else:
                resultado["fallidos"] += 1
                await _supabase_request("PATCH", "/RecordatorioAviso", params={"id": f"eq.{a['id']}"}, json={"estado": "error"})
        except Exception as e:
            logger.warning("Recordatorio programado | falló un aviso (%s)", type(e).__name__)
            resultado["fallidos"] += 1
    return resultado


async def bucle_recordatorios() -> None:
    """Cada minuto manda los avisos que llegaron. Si la base falla (por ejemplo, falta la tabla) lo registra y espera cada vez más."""
    await asyncio.sleep(INICIO_S)
    fallos = 0
    while True:
        espera = INTERVALO_S
        try:
            await ciclo()
            fallos = 0
        except Exception as e:
            fallos += 1
            espera = min(INTERVALO_S * 2 ** fallos, ESPERA_MAX_ERROR_S)
            logger.warning("Recordatorio programado | no se pudo hacer el ciclo (falla %d, se reintenta en %d s): %s", fallos, espera, type(e).__name__)
        await asyncio.sleep(espera)
