"""Cifras públicas de la portada que salen de la base: cuántas personas hay registradas (filas de la tabla Cliente).

Se calcula con una sola consulta liviana (PostgREST devuelve el total en la cabecera Content-Range) y se guarda en memoria una hora, así que
las visitas a la portada casi nunca tocan la base. Si la consulta falla o tarda más de unos segundos se conserva el último valor conocido y,
si no hay ninguno, la portada simplemente no muestra esa cifra (nunca inventa un número ni se cae)."""
import logging
import time
from typing import Optional

from app.clients.supabase import _supabase_headers, _supabase_rest_base
from app.core.http import http_client

logger = logging.getLogger("wtsp_pita")

TTL_S = 3600           # el número se recalcula como mucho una vez por hora
TTL_FALLO_S = 300      # tras un fallo no se vuelve a consultar durante 5 minutos
TIMEOUT_S = 3          # la portada no espera más que esto a la base

_cache = {"valor": None, "hasta": 0.0}


def total_desde_content_range(valor: Optional[str]) -> Optional[int]:
    """``0-0/1234`` o ``*/0`` → 1234 o 0. Cualquier otra cosa (sin cabecera, ``0-0/*``, texto raro) → None."""
    if not valor or "/" not in valor:
        return None
    total = valor.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


def formatear_miles(n: int) -> str:
    """1234 → «1.234» (separador de miles de Chile)."""
    return f"{n:,}".replace(",", ".")


async def total_clientes() -> Optional[int]:
    ahora = time.monotonic()
    if ahora < _cache["hasta"]:
        return _cache["valor"]
    try:
        async with http_client(TIMEOUT_S) as c:
            r = await c.get(f"{_supabase_rest_base()}/Cliente", params={"select": "id"},
                            headers={**_supabase_headers(), "Prefer": "count=exact", "Range": "0-0"})
            r.raise_for_status()
        total = total_desde_content_range(r.headers.get("content-range"))
        if total is None:
            raise ValueError("la respuesta no trae el total")
    except Exception as e:
        logger.warning("Portada: no se pudo contar a las personas registradas (%s)", type(e).__name__)
        _cache["hasta"] = ahora + TTL_FALLO_S                      # se conserva el último valor conocido, si lo hay
        return _cache["valor"]
    _cache.update(valor=total, hasta=ahora + TTL_S)
    return total
