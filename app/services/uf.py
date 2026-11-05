"""Valor de la UF en pesos para mostrar equivalentes **aproximados** en el sitio público.

Fuente principal: la página oficial de valores de la UF del Servicio de Impuestos Internos (sii.cl). Respaldo: mindicador.cl (un servicio de terceros que a veces no responde).

A diferencia de ``app.clients.mindicador._obtener_valor_uf`` (que ante una falla devuelve un valor de respaldo fijo para las plantillas de WhatsApp), aquí
nunca se inventa un número: si el servicio no responde se devuelve ``None`` y quien llama simplemente no muestra pesos. Guarda el valor 6 horas junto con
la fecha que informa el servicio (la que se imprime como «UF del …»)."""
import logging
import re
import time
from datetime import date, datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from app.core.http import http_client

logger = logging.getLogger("wtsp_pita")

TTL_S = 6 * 3600
TTL_FALLO_S = 300
DIAS_MAXIMOS = 10
_cache = {"valor": None, "fecha": None, "hasta": 0.0}
_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
_UF_MIN, _UF_MAX = 10_000.0, 200_000.0         # un valor fuera de este rango es un error de lectura, no una UF
_ENCABEZADOS = {"User-Agent": "QueSubsidio/1.0 (+https://quesubsidio.cl)", "Accept": "text/html,application/json"}


def _hoy_chile() -> date:
    return datetime.now(ZoneInfo("America/Santiago")).date()


def leer_valor_sii(html: str, hoy: date) -> Optional[Tuple[float, date]]:
    """Lee de la página del SII el valor de la UF de ``hoy`` (o, si esa celda aún está vacía, del último día anterior con valor, hasta 3 días atrás). None si no se puede leer."""
    mes = _MESES[hoy.month - 1]
    m = re.search(rf"<h2>\s*{mes}\s*</h2>(.*?)(?=<h2>|\Z)", html, flags=re.S | re.I)
    if not m:
        return None
    celdas = {int(d): v.strip() for d, v in re.findall(r"<th[^>]*>\s*<strong>\s*(\d{1,2})\s*</strong>\s*</th>\s*<td[^>]*>\s*([\d.,]*)\s*</td>", m.group(1))}
    for atras in range(0, 4):
        dia = hoy.day - atras
        texto = celdas.get(dia, "") if dia >= 1 else ""
        if texto:
            try:
                valor = float(texto.replace(".", "").replace(",", "."))
            except ValueError:
                return None
            return (valor, hoy.replace(day=dia)) if _UF_MIN <= valor <= _UF_MAX else None
    return None


async def _desde_sii(c, hoy: date) -> Optional[Tuple[float, date]]:
    r = await c.get(f"https://www.sii.cl/valores_y_fechas/uf/uf{hoy.year}.htm", headers=_ENCABEZADOS)
    r.raise_for_status()
    return leer_valor_sii(r.text, hoy)


async def _desde_mindicador(c) -> Tuple[float, date]:
    r = await c.get("https://mindicador.cl/api/uf", headers=_ENCABEZADOS)
    r.raise_for_status()
    punto = r.json()["serie"][0]
    valor = float(punto["valor"])
    if not _UF_MIN <= valor <= _UF_MAX:
        raise ValueError("valor fuera de rango")
    return valor, (date.fromisoformat(str(punto["fecha"])[:10]) if punto.get("fecha") else date.today())


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
    obtenido: Optional[Tuple[float, date]] = None
    try:
        async with http_client(5) as c:
            try:
                obtenido = await _desde_sii(c, _hoy_chile())
            except Exception as e:                                     # el SII no respondió o cambió su página: se prueba el respaldo
                logger.info("UF: no se pudo leer el valor del SII (%s); se prueba mindicador.cl", type(e).__name__)
            if obtenido is None:
                obtenido = await _desde_mindicador(c)
    except Exception as e:
        logger.warning("UF: no se pudo obtener el valor para los equivalentes en pesos (%s)", type(e).__name__)
        _cache["hasta"] = ahora + TTL_FALLO_S
        return _vigente()                                  # el último valor bueno, si no es demasiado viejo
    valor, fecha = obtenido
    _cache.update(valor=valor, fecha=fecha, hasta=ahora + TTL_S)
    return valor, fecha


def formatear_pesos(clp: float) -> str:
    """12.153.400 → «$12,2 millones»; 450.000 → «$450.000». Siempre aproximado: se redondea."""
    if clp >= 1_000_000:
        return "$" + f"{clp / 1_000_000:,.1f}".replace(",", "§").replace(".", ",").replace("§", ".") + " millones"
    return "$" + f"{round(clp, -3):,.0f}".replace(",", ".")
