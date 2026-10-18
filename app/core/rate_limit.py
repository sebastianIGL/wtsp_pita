"""Límite de peticiones por clave (IP + ruta) en una ventana deslizante, en memoria de este proceso.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). El estado (``_rl_store``) se modifica siempre en el mismo
objeto; main.py lo re-exporta (mismo objeto) para que los tests puedan reiniciarlo. Con una sola réplica es suficiente.
"""
import time
from collections import defaultdict
from typing import Dict, List


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
    """IP real del visitante. Detrás del proxy de Railway ``request.client.host`` es una IP interna distinta en cada
    petición (100.64.0.x). El borde de Railway SOBRESCRIBE X-Forwarded-For y X-Real-IP (verificado en producción: lo que
    el visitante envíe en esas cabeceras no llega), y deja X-Forwarded-For como "IP del visitante, IP del servidor de
    borde": la primera entrada es el visitante y la última es un servidor de borde que cambia entre peticiones (por eso
    no sirve). Orden: primera entrada de X-Forwarded-For, luego X-Real-IP, luego la IP de la conexión (desarrollo local)."""
    primera = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if primera:
        return primera
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    return request.client.host if request.client else "unknown"
