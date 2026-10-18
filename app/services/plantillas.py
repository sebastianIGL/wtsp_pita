"""Plantillas de WhatsApp: qué variables lleva cada plantilla aprobada en Meta y el pool de valores (proyecto, subsidio, precio,
reserva, fecha de entrega) con el que se rellenan.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote B).
"""
from typing import Dict, List, Optional

from app.clients.mindicador import _obtener_valor_uf
from app.clients.supabase import _supabase_request


# ---------------------------------------------------------------------------
# Mapeo de variables por plantilla de WhatsApp
# Cada clave es el nombre exacto de la plantilla en Meta.
# El valor es la lista ordenada de claves del pool que corresponde a {{1}}, {{2}}, ...
# Para agregar una nueva plantilla: solo añadir una entrada aquí, sin tocar funciones.
# ---------------------------------------------------------------------------
TEMPLATE_VARS_MAP: Dict[str, List[str]] = {
    "ideal_para_mujeresfamilias": [
        "cliente_nombre", "proyecto_nombre", "proyecto_ubicacion",
        "subsidio_tipo", "monto_subsidio_uf",
    ],
    "enfoque_te_ayudamos": [
        "cliente_nombre", "proyecto_nombre",
        "monto_subsidio_uf", "valor_reserva_clp", "precio_desde_uf", "fecha_entrega",
    ],
    "cercana__consultiva": [
        # Esta plantilla ya trae "UF" y "$" escritos literalmente junto a las variables
        # en el texto aprobado en Meta, por eso usa las versiones _num (solo el número).
        "cliente_nombre", "proyecto_nombre", "proyecto_ubicacion",
        "subsidio_tipo", "monto_subsidio_uf_num",
        "monto_subsidio_clp_num", "precio_desde_uf_num", "fecha_entrega",
    ],
}


async def _pool_plantilla(nombre: str, proyecto: Optional[Dict], tipologia_id: Optional[int] = None) -> Dict[str, str]:
    """Construye el pool completo de valores disponibles para cualquier plantilla."""
    p    = proyecto or {}

    tipos: List[str] = []
    if p.get("acepta_ds19"):
        tipos.append("DS19")
    if p.get("acepta_ds1_t23"):
        tipos.append("DS1 T23")
    subsidio_tipo = " / ".join(tipos) if tipos else "DS19"

    # Precio desde UF: usa tipología específica si se indicó, si no busca el mínimo del proyecto
    tip_rows: List[Dict] = []
    if tipologia_id:
        tip_rows = await _supabase_request(
            "GET", "/Tipologia",
            params={"id": f"eq.{tipologia_id}", "select": "valor_uf,monto_subsidio"},
        ) or []
    elif p.get("id"):
        tip_rows = await _supabase_request(
            "GET", "/Tipologia",
            params={"proyecto_id": f"eq.{p['id']}", "select": "valor_uf,monto_subsidio", "order": "id.asc"},
        ) or []
    precios = [t.get("valor_uf") for t in tip_rows if t.get("valor_uf")]
    precio_min       = min(precios) if precios else None
    precio_desde     = f"{int(precio_min):,} UF".replace(",", ".") if precio_min else "a consultar"
    precio_desde_num = f"{int(precio_min):,}".replace(",", ".") if precio_min else "a consultar"

    # Monto subsidio desde tipología
    tip_monto = None
    if tip_rows:
        tip_monto = tip_rows[0].get("monto_subsidio")
    elif p.get("id"):
        _m_rows = await _supabase_request("GET", "/Tipologia",
            params={"proyecto_id": f"eq.{p['id']}", "select": "monto_subsidio", "limit": "1", "order": "id.asc"}) or []
        tip_monto = _m_rows[0].get("monto_subsidio") if _m_rows else None
    try:
        tip_monto = float(tip_monto) if tip_monto is not None else None
    except (TypeError, ValueError):
        tip_monto = None

    # Fecha entrega desde Etapa
    fecha_entrega_pool = "por confirmar"
    if p.get("id"):
        _e_rows = await _supabase_request("GET", "/Etapa",
            params={"proyecto_id": f"eq.{p['id']}", "select": "fecha_entrega", "limit": "1", "order": "id.asc"}) or []
        if _e_rows and _e_rows[0].get("fecha_entrega"):
            fecha_entrega_pool = _e_rows[0]["fecha_entrega"]

    # Valor reserva CLP, con fallback a UF si no hay CLP
    reserva_clp = p.get("valor_reserva_clp")
    if reserva_clp:
        reserva_fmt = f"${int(reserva_clp):,}".replace(",", ".")
    elif p.get("valor_reserva_uf"):
        reserva_fmt = f"{p['valor_reserva_uf']} UF"
    else:
        reserva_fmt = "a consultar"

    # Monto del subsidio en pesos: UF del subsidio × valor UF del día (cacheado 24h)
    monto_subsidio_uf_val = tip_monto or 700
    uf_valor              = await _obtener_valor_uf()
    monto_subsidio_clp    = round(monto_subsidio_uf_val * uf_valor)

    return {
        "cliente_nombre":     nombre                      or "cliente",
        "proyecto_nombre":    p.get("nombre")             or "nuestro proyecto",
        "proyecto_ubicacion": p.get("ubicacion")          or "Santiago",
        "subsidio_tipo":      subsidio_tipo,
        "monto_subsidio_uf":  f"{monto_subsidio_uf_val} UF",
        "valor_reserva_clp":  reserva_fmt,
        "precio_desde_uf":    precio_desde,
        "fecha_entrega":      fecha_entrega_pool,
        # Versiones "_num" sin símbolo de unidad: para plantillas que ya traen
        # "UF"/"$" escritos literalmente en el texto aprobado en Meta (evita duplicarlos).
        "monto_subsidio_uf_num": f"{monto_subsidio_uf_val:,.0f}".replace(",", "."),
        "monto_subsidio_clp_num": f"{monto_subsidio_clp:,}".replace(",", "."),
        "precio_desde_uf_num": precio_desde_num,
    }
