"""Horarios (hora de Chile): cuándo responde el bot y cuándo se avisa a los ejecutivos.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). Tres ventanas independientes:
  * bot: HORARIO_BOT_INICIO / HORARIO_BOT_FIN (por defecto 0–24 = siempre), solo afecta a responder mensajes de texto;
  * avisos "primer documento" y "documentos completos": fijo 9–19;
  * avisos urgentes ("necesita ayuda"): 8–24.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.config import get_settings


# ── Horarios ────────────────────────────────────────────────────────────────
_TZ_CHILE = ZoneInfo("America/Santiago")


# 1) Horario en que el bot RESPONDE mensajes de texto: configurable (HORARIO_BOT_INICIO / HORARIO_BOT_FIN,
#    hora de Chile). Por defecto 0–24 = 24/7. Solo afecta a cuándo se responden mensajes entrantes.
def _horas_bot() -> tuple:
    s = get_settings()
    return s.horario_bot_inicio, s.horario_bot_fin


def _dentro_de(inicio: int, fin: int, ahora: Optional[datetime] = None) -> bool:
    hora_local = (ahora or datetime.now(timezone.utc)).astimezone(_TZ_CHILE)
    return inicio <= hora_local.hour < fin


def _segundos_hasta_apertura(inicio: int, fin: int) -> float:
    """Segundos que faltan hasta la próxima apertura de la ventana [inicio, fin) en hora de Chile."""
    ahora_cl = datetime.now(timezone.utc).astimezone(_TZ_CHILE)
    objetivo = ahora_cl.replace(hour=inicio, minute=0, second=0, microsecond=0)
    if ahora_cl.hour >= fin:
        objetivo += timedelta(days=1)
    elif ahora_cl.hour >= inicio:
        # Ya pasó el inicio de hoy pero seguimos dentro del horario — no debería llamarse en este caso.
        objetivo += timedelta(days=1)
    return max(0.0, (objetivo - ahora_cl).total_seconds())


def _dentro_horario_bot(ahora: Optional[datetime] = None) -> bool:
    """True si el bot puede responder ahora un mensaje de texto (por defecto siempre)."""
    return _dentro_de(*_horas_bot(), ahora)


def _segundos_hasta_horario_bot() -> float:
    """Segundos hasta que se abra el horario del bot (solo se usa si se configuró una ventana)."""
    return _segundos_hasta_apertura(*_horas_bot())


# 2) Horario de los AVISOS a ejecutivos "primer documento" y "documentos completos": fijo 9:00–19:00,
#    independiente del horario del bot (antes compartían la misma constante).
HORARIO_AVISOS_INICIO = 9   # 09:00 hora Chile


HORARIO_AVISOS_FIN    = 19  # 19:00 hora Chile (no incluido)


def _dentro_horario_avisos(ahora: Optional[datetime] = None) -> bool:
    return _dentro_de(HORARIO_AVISOS_INICIO, HORARIO_AVISOS_FIN, ahora)


def _segundos_hasta_horario_avisos() -> float:
    return _segundos_hasta_apertura(HORARIO_AVISOS_INICIO, HORARIO_AVISOS_FIN)


# Horario más amplio para avisos urgentes al ejecutivo ("necesita_ayuda"): 08:00–00:00 hora Chile.
HORARIO_URGENTE_INICIO = 8  # 08:00 hora Chile (sin límite superior: corre hasta medianoche)


def _dentro_horario_urgente(ahora: Optional[datetime] = None) -> bool:
    """True si estamos dentro del horario permitido para avisos urgentes (08:00–24:00, hora Chile)."""
    hora_local = (ahora or datetime.now(timezone.utc)).astimezone(_TZ_CHILE)
    return hora_local.hour >= HORARIO_URGENTE_INICIO


def _segundos_hasta_horario_urgente() -> float:
    """Segundos que faltan hasta que se abra el horario de avisos urgentes (8:00 hora Chile)."""
    ahora_cl = datetime.now(timezone.utc).astimezone(_TZ_CHILE)
    objetivo = ahora_cl.replace(hour=HORARIO_URGENTE_INICIO, minute=0, second=0, microsecond=0)
    if ahora_cl.hour >= HORARIO_URGENTE_INICIO:
        objetivo += timedelta(days=1)
    return max(0.0, (objetivo - ahora_cl).total_seconds())
