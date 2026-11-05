"""Valor de la UF en pesos para mostrar equivalentes **aproximados** en el sitio público.

A diferencia de ``app.clients.mindicador._obtener_valor_uf`` (que ante una falla devuelve un valor de respaldo fijo para las plantillas de WhatsApp), aquí
nunca se inventa un número: si el servicio no responde se devuelve ``None`` y quien llama simplemente no muestra pesos. Guarda el valor 6 horas junto con
la fecha que informa el servicio (la que se imprime como «UF del …»)."""
import logging
import time
from datetime import date
from typing import Optional, Tuple

from app.core.http import http_client

logger = logging.getLogger("wtsp_pita")

TTL_S = 6 * 3600
TTL_FALLO_S = 300
DIAS_MAXIMOS = 10
_cache = {"valor": None, "fecha": None, "hasta": 0.0}


def _vigente() -> Optional[Tuple[float, date]]:
    """El valor guardado, solo si su fecha tiene como mucho DIAS_MAXIMOS días: un valor viejo no se presenta como «aproximado de hoy»."""
    if not _cache["valor"] or (date.today() - _cache["fecha"]).days > DIAS_MAXIMOS:
        return None
    return _cache["valor"], _cache["fecha"]


async def uf_del_dia() -> Optional[Tuple[float, date]]:
    """(valor en pesos, fecha de ese valor) o None si no se pudo saber."""
    ahora = time.monotonic()
    if ahora < _cache["hasta"]:
        return _vigente()
    try:
        async with http_client(5) as c:
            r = await c.get("https://mindicador.cl/api/uf")
            r.raise_for_status()
            punto = r.json()["serie"][0]
            valor = float(punto["valor"])
            fecha = date.fromisoformat(str(punto["fecha"])[:10]) if punto.get("fecha") else date.today()
        if valor <= 0:
            raise ValueError("valor no positivo")
    except Exception as e:
        logger.warning("UF: no se pudo obtener el valor para los equivalentes en pesos (%s)", type(e).__name__)
        _cache["hasta"] = ahora + TTL_FALLO_S
        return _vigente()                                  # el último valor bueno, si no es demasiado viejo
    _cache.update(valor=valor, fecha=fecha, hasta=ahora + TTL_S)
    return valor, fecha


def formatear_pesos(clp: float) -> str:
    """12.153.400 → «$12,2 millones»; 450.000 → «$450.000». Siempre aproximado: se redondea."""
    if clp >= 1_000_000:
        return "$" + f"{clp / 1_000_000:,.1f}".replace(",", "§").replace(".", ",").replace("§", ".") + " millones"
    return "$" + f"{round(clp, -3):,.0f}".replace(",", ".")
