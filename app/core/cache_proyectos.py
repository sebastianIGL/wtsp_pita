"""Caché en memoria de los proyectos de la landing pública (5 minutos). Lo lee ``/api/proyectos-landing`` y lo invalida
``PATCH /api/proyectos/{id}``. Se modifica siempre en el lugar (nadie reasigna el objeto), por eso es seguro compartirlo.

Movido sin cambios desde main.py (Fase 6, tramo 3).
"""
from typing import Dict


# ── Caché en memoria para endpoints públicos de la landing ────────────────────
_cache_proyectos: Dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 300  # 5 minutos
