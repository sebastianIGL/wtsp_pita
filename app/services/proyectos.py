"""Proyectos: consulta de un proyecto con su empresa e industria, columnas que se piden según la vista, copia de los campos
opcionales que llegan al crear o editar y búsqueda de un proyecto por el nombre que trae el CSV.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote B).
"""
import logging
from typing import Dict, List, Optional

from app.clients.supabase import _supabase_request
from app.core.utilidades import _normalizar_nombre

logger = logging.getLogger("wtsp_pita")


_PROYECTO_SELECT = "id,codigo,nombre,ubicacion,imagen_url,inmobiliaria_id,Inmobiliaria(nombre,empresa_id,Empresa(nombre,industria_id,Industria(nombre))),ahorro_minimo_uf,valor_reserva_clp,valor_reserva_uf,tiene_piloto,valor_estacionamiento_uf,estacionamiento_obligatorio,notas,acepta_ds19,acepta_ds1_t23"
# Variante liviana para el listado del panel (frontend/panel.html): solo necesita el nombre
# de la Inmobiliaria (para la vista "Todos los proyectos"), no la cadena Empresa/Industria.
_PROYECTO_SELECT_PANEL = "id,codigo,nombre,ubicacion,imagen_url,inmobiliaria_id,Inmobiliaria(nombre),ahorro_minimo_uf,valor_reserva_clp,valor_reserva_uf,tiene_piloto,valor_estacionamiento_uf,estacionamiento_obligatorio,notas,acepta_ds19,acepta_ds1_t23"


async def obtener_proyecto_por_id(proyecto_id: str):
    rows = await _supabase_request(
        "GET", "/Proyecto",
        params={"id": f"eq.{proyecto_id}", "select": _PROYECTO_SELECT, "limit": "1"},
    )
    return rows[0] if rows else None


_PROYECTO_SELECT_LIGHT = (
    "id,nombre,ubicacion,acepta_ds19,"
    "acepta_ds1_t23,"
    "ahorro_minimo_uf,valor_reserva_clp,valor_reserva_uf,"
    "tiene_piloto,valor_estacionamiento_uf,notas"
)


async def _buscar_id_proyecto(nombre_csv: str, proyectos_cache: List[Dict]) -> Optional[Dict]:
    nombre_norm = _normalizar_nombre(nombre_csv)
    if not nombre_norm:
        return None   # un nombre vacío no identifica a ningún proyecto (antes calzaba con uno sin nombre)
    for p in proyectos_cache:
        if _normalizar_nombre(p.get("nombre", "")) == nombre_norm:
            return p
        for alias in (p.get("nombres_csv") or []):
            if _normalizar_nombre(alias) == nombre_norm:
                return p
    return None


def _aplicar_campos_proyecto(body: Dict, payload: Dict, *, es_admin: bool) -> None:
    """Copia los campos opcionales del proyecto desde body → payload."""
    if es_admin and body.get("imagen_url") is not None:
        payload["imagen_url"] = body["imagen_url"] or None
    for campo in ("notas",):
        if campo in body:
            payload[campo] = (body[campo] or "").strip() or None
    for campo in ("ahorro_minimo_uf", "valor_reserva_clp", "valor_reserva_uf",
                  "valor_estacionamiento_uf"):
        if campo in body:
            try:
                payload[campo] = float(body[campo]) if body[campo] not in (None, "") else None
            except (TypeError, ValueError):
                # se ignora el valor no numérico (como antes), pero ya no en silencio
                logger.warning("Proyecto: el campo %s no es numérico y se ignora", campo, extra={"contexto": "_aplicar_campos_proyecto"})
    for campo in ("tiene_piloto", "estacionamiento_obligatorio", "acepta_ds19", "acepta_ds1_t23"):
        if campo in body:
            payload[campo] = bool(body[campo])
