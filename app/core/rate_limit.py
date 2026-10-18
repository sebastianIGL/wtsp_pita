"""Límite de peticiones por clave (IP + ruta) en una ventana deslizante, en memoria de este proceso.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). El estado (``_rl_store``) se modifica siempre en el mismo
objeto; main.py lo re-exporta (mismo objeto) para que los tests puedan reiniciarlo. Con una sola réplica es suficiente.
"""
import logging
import time
from collections import defaultdict
from typing import Dict, List

logger = logging.getLogger(__name__)


# ── Rate limiting (en memoria, single-instance) ───────────────────────────────
_rl_store: Dict[str, List[float]] = defaultdict(list)


def _rate_limit_ok(key: str, max_req: int = 5, window: int = 60) -> bool:
    """Retorna True si el request está dentro del límite, False si debe bloquearse."""
    now = time.time()
    bucket = _rl_store[key]
    _rl_store[key] = [t for t in bucket if now - t < window]
    if len(_rl_store[key]) >= max_req:
        return False
    _rl_store[key].append(now)
    return True


def ip_cliente(request) -> str:
    """IP real del visitante. Detrás del proxy de Railway ``request.client.host`` es la del proxy (no la del visitante),
    así que todos los visitantes compartían contador (o ninguno lo alcanzaba). Railway agrega la IP que vio al FINAL de
    X-Forwarded-For: se usa esa última entrada, porque las anteriores las puede inventar quien llama."""
    reenviada = request.headers.get("x-forwarded-for", "")
    if request.headers.get("x-diagnostico-ip"):          # TEMPORAL: ver qué IP entrega el proxy (solo si la petición lo pide)
        logger.info("Diagnóstico IP | x-forwarded-for=%r | x-real-ip=%r | conexión=%r",
                    reenviada, request.headers.get("x-real-ip"), request.client.host if request.client else None)
    if reenviada:
        ultima = reenviada.split(",")[-1].strip()
        if ultima:
            return ultima
    return request.client.host if request.client else "unknown"
