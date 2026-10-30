"""Contexto que se le da al modelo del bot: cadena Proyecto → Inmobiliaria → Empresa → Industria, otros proyectos de la misma
empresa y la línea de resumen de cada uno.

Movido sin cambios desde app/services/bot (Fase 8).
"""
from typing import Dict, List, Optional

from app.clients.supabase import _supabase_request
from app.services.proyectos import _PROYECTO_SELECT_LIGHT


def _construir_mind(proyecto: Optional[Dict]) -> Dict[str, str]:
    """
    Extrae la cadena contextual completa desde el proyecto hacia arriba:
    Proyecto → Inmobiliaria → Empresa → Industria.
    Retorna un dict con los nombres de cada nivel (vacío si no existe).
    """
    p    = proyecto or {}
    inm  = p.get("Inmobiliaria") or {}
    emp  = inm.get("Empresa") or {}
    ind  = emp.get("Industria") or {}
    return {
        "industria":    ind.get("nombre") or "Inmobiliaria",
        "empresa":      emp.get("nombre") or "",
        "inmobiliaria": inm.get("nombre") or "",
        "proyecto":     p.get("nombre")   or "",
    }


async def _obtener_otros_proyectos(empresa_id: int, proyecto_id_actual: Optional[str]) -> List[Dict]:
    """Devuelve todos los proyectos de la empresa excepto el actual."""
    inms = await _supabase_request("GET", "/Inmobiliaria",
        params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
    if not inms:
        return []
    inm_ids = ",".join(str(i["id"]) for i in inms)
    rows = await _supabase_request("GET", "/Proyecto",
        params={"inmobiliaria_id": f"in.({inm_ids})", "activo": "eq.true", "select": _PROYECTO_SELECT_LIGHT}) or []
    return [p for p in rows if str(p.get("id")) != str(proyecto_id_actual)]


def _resumir_proyecto(p: Dict) -> str:
    """Genera una línea de resumen de un proyecto para el contexto del bot (otros proyectos disponibles)."""
    subsidios = []
    if p.get("acepta_ds19"):
        subsidios.append("DS19")
    if p.get("acepta_ds1_t23"):
        subsidios.append(f"DS1T23 {p.get('subsidio_ds1_t23_uf','')}UF".strip())
    sub_str = " | ".join(subsidios) or "sin subsidio"
    return f"• [{p['id']}] {p.get('nombre','?')} — {p.get('ubicacion','?')} | {sub_str}"
