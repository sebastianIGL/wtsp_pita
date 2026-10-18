"""Valor de la UF en pesos chilenos (mindicador.cl), con caché de 24 h y un valor de respaldo si el servicio falla.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). ``_cache_uf`` es un diccionario que se modifica siempre
en el mismo objeto; main.py lo re-exporta (mismo objeto) para que los tests puedan reiniciarlo.
"""
import logging
import time
from typing import Dict

from app.core.http import http_client

logger = logging.getLogger("wtsp_pita")


# ── Valor UF (Chile) para poblar plantillas WhatsApp ──────────────────────────
_cache_uf: Dict = {"valor": None, "ts": 0.0}
_CACHE_UF_TTL = 86400  # 24 horas
_UF_VALOR_RESPALDO = 39000  # fallback si mindicador.cl no responde (ajustar periódicamente)


async def _obtener_valor_uf() -> float:
    """Valor de la UF en CLP, cacheado 24h. Usa mindicador.cl; ante cualquier falla retorna un respaldo fijo."""
    now = time.time()
    if _cache_uf["valor"] and now - _cache_uf["ts"] < _CACHE_UF_TTL:
        return _cache_uf["valor"]
    try:
        async with http_client(8) as client:
            resp = await client.get("https://mindicador.cl/api/uf")
            resp.raise_for_status()
            data = resp.json()
            valor = float(data["serie"][0]["valor"])
    except Exception as exc:
        logger.warning("No se pudo obtener valor UF de mindicador.cl, usando respaldo: %s", exc)
        valor = _UF_VALOR_RESPALDO
    _cache_uf["valor"] = valor
    _cache_uf["ts"]    = now
    return valor
