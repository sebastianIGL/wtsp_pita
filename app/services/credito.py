"""«Sujeto de crédito» de cada cliente y el sueldo que necesitaría para la propiedad que le interesa. Todo es aproximado y de apoyo para el ejecutivo (no reemplaza la evaluación del banco).

Criterios (acordados con la persona dueña, 2026-11-16):
  · Trabajo estable: contrato indefinido, con 6 meses de antigüedad o, si tiene menos, sin lagunas previsionales (es lo que el bot confirma y guarda en ``trabajo_indefinido``).
    Si algún día se guarda la antigüedad exacta (``antiguedad_meses``), se exigen 6 meses.
  · Sueldo informado (``renta_mensual``).
  · Sueldo suficiente para la propiedad: se supone que la familia pone el 10 % del valor y el resto (90 %) lo entrega un crédito hipotecario, con la MISMA tasa y plazo de la calculadora de
    crédito de las herramientas (tasa anual efectiva 4,5 %, 30 años, sistema francés). El dividendo puede ser hasta el 30 % del sueldo para un banco y hasta el 25 % para una mutuaria, de modo
    que el sueldo requerido es dividendo ÷ 0,30 (banco) y dividendo ÷ 0,25 (mutuaria: pide más sueldo).

Estados: ``cumple`` (alcanza para banco y mutuaria), ``solo_banco`` (alcanza para banco pero no para mutuaria), ``no_cumple`` y ``faltan_datos``.
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("wtsp_pita")

TEA_PCT = 4.5                 # = el valor por defecto de la calculadora de crédito (frontend/herramientas.html)
PLAZO_ANIOS = 30
PIE_PCT = 10
RAZON_BANCO = 0.30
RAZON_MUTUARIA = 0.25
ANTIGUEDAD_MIN_MESES = 6


def dividendo_uf(valor_uf: float, pie_pct: float = PIE_PCT, tea_pct: float = TEA_PCT, plazo_anios: int = PLAZO_ANIOS) -> float:
    """Dividendo mensual (en UF) del crédito que queda después del pie: sistema francés, tasa efectiva anual."""
    credito = float(valor_uf) * (1 - pie_pct / 100)
    n = int(plazo_anios) * 12
    i = (1 + tea_pct / 100) ** (1 / 12) - 1
    return credito / n if i == 0 else credito * i / (1 - (1 + i) ** -n)


def sueldo_requerido(valor_uf: Any, uf_clp: Any, deuda_mensual: Any = 0) -> Optional[Dict[str, int]]:
    """Sueldo mensual (en pesos) que haría falta para el crédito de esa propiedad: ``{"dividendo", "banco", "mutuaria"}``; ``None`` si falta el valor o la UF.
    Si la persona ya paga deudas cada mes (``deuda_mensual``), esa cuota se suma al dividendo: el tope del 30 % / 25 % cubre todo lo que paga, no solo la casa."""
    try:
        v, u = float(valor_uf), float(uf_clp)
    except (TypeError, ValueError):
        return None
    if not (v > 0 and u > 0):
        return None
    d = dividendo_uf(v) * u
    try:
        deuda = max(0.0, float(deuda_mensual or 0))
    except (TypeError, ValueError):
        deuda = 0.0
    if deuda != deuda or deuda == float("inf"):
        deuda = 0.0
    return {"dividendo": round(d), "banco": round((d + deuda) / RAZON_BANCO), "mutuaria": round((d + deuda) / RAZON_MUTUARIA)}


def _numero(v: Any) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 and n == n else None


def evaluar(datos: Optional[Dict[str, Any]], valor_uf: Any = None, uf_clp: Any = None) -> Dict[str, Any]:
    """``datos``: lo que se sabe del cliente (``trabajo_indefinido``, ``renta_mensual``, ``antiguedad_meses``, ``complemento_renta``; todo opcional).
    Devuelve ``{"estado", "motivo", "sueldo", "requerido_banco", "requerido_mutuaria"}``. Función pura."""
    d = datos if isinstance(datos, dict) else {}
    deuda = _numero(d.get("deuda_mensual")) or 0
    req = sueldo_requerido(valor_uf, uf_clp, deuda)
    sueldo = _numero(d.get("renta_mensual"))
    tiene_deudas = d.get("tiene_deudas")
    deudas = "con_monto" if deuda > 0 else "monto_desconocido" if tiene_deudas is True else "sin" if tiene_deudas is False else None
    base = {"sueldo": int(sueldo) if sueldo else None, "requerido_banco": req["banco"] if req else None, "requerido_mutuaria": req["mutuaria"] if req else None, "deudas": deudas}
    problemas: List[str] = []
    faltan: List[str] = []
    estable = d.get("trabajo_indefinido")
    antig = _numero(d.get("antiguedad_meses"))
    if estable is False:
        problemas.append("no tiene trabajo estable (sin contrato indefinido o con lagunas previsionales)")
    elif estable is None:
        faltan.append("si tiene trabajo estable")
    if antig is not None and antig < ANTIGUEDAD_MIN_MESES and estable is not True:
        problemas.append(f"tiene {int(antig)} meses de antigüedad (mínimo {ANTIGUEDAD_MIN_MESES})")
    if not sueldo:
        faltan.append("su sueldo")
    if problemas:
        return {**base, "estado": "no_cumple", "motivo": "No cumple: " + "; ".join(problemas)}
    if faltan:
        return {**base, "estado": "faltan_datos", "motivo": "Faltan datos: " + " y ".join(faltan)}
    if req is None:
        return {**base, "estado": "faltan_datos", "motivo": "Faltan datos: el valor de la propiedad que le interesa (para comparar su sueldo)"}
    extra = " (con complementador de renta: revisar la suma de ambos sueldos)" if d.get("complemento_renta") else ""
    if deudas == "con_monto":
        extra += f" (el sueldo requerido ya incluye sus deudas de ${int(deuda):,} al mes)".replace(",", ".")
    elif deudas == "monto_desconocido":
        extra += " (tiene deudas y no sabemos el monto: el sueldo requerido puede ser mayor)"
    if sueldo >= req["mutuaria"]:
        return {**base, "estado": "cumple", "motivo": "Cumple: su sueldo alcanza para banco y mutuaria" + extra}
    if sueldo >= req["banco"]:
        return {**base, "estado": "solo_banco", "motivo": "Alcanza para banco (hasta 30 % de su sueldo) pero no para mutuaria (25 %)" + extra}
    return {**base, "estado": "no_cumple", "motivo": f"No cumple: su sueldo no alcanza (necesitaría al menos ${req['banco']:,} para banco)".replace(",", ".") + extra}


def agregar_a_filas(filas: Iterable[Dict[str, Any]], minimos_por_proyecto: Dict[Any, float], uf_clp: Any) -> None:
    """Agrega a cada fila de cliente ``credito_estado``, ``credito_motivo``, ``sueldo_requerido_banco`` y ``sueldo_requerido_mutuaria``. Usa la tipología del cliente y, si no tiene,
    la más barata de su proyecto. Nunca lanza por una fila rara."""
    for f in filas:
        try:
            valor = _numero(f.get("tipologia_valor_uf")) or minimos_por_proyecto.get(f.get("proyecto_id"))
            r = evaluar(f, valor, uf_clp)
            f["credito_estado"], f["credito_motivo"] = r["estado"], r["motivo"]
            f["sueldo_requerido_banco"], f["sueldo_requerido_mutuaria"] = r["requerido_banco"], r["requerido_mutuaria"]
            f["credito_deudas"] = r["deudas"]
        except Exception as e:
            logger.info("Crédito | no se pudo evaluar un cliente (%s)", type(e).__name__)
