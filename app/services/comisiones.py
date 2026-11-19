"""Calculadora de comisiones: qué planes de comisión aplican a los proyectos que cada persona puede ver.

Los planes (ticket en UF, porcentaje, bonos) viven en la tabla ``PlanComision`` y a qué proyectos se aplican, en ``PlanComisionAsignacion`` (una fila por proyecto o por inmobiliaria entera).
Un proyecto SIN asignación usa el plan de «entrega futura» o «entrega inmediata» según el estado de sus etapas. El cálculo de la comisión lo hace la pantalla
(``frontend/js/calculos_comision.js``) para recalcular al instante; aquí solo se arma qué planes ve cada persona y a qué proyectos corresponde cada uno.

Cada plan trae además las ``propiedades`` a las que aplica (proyecto + tipología con su valor en UF) para que la calculadora pueda elegir la propiedad que se vende en vez de usar un solo valor
promedio; la pantalla también deja escribir el precio a mano.

Solo lectura. Si las tablas aún no existen, el servicio responde ``{"disponible": False}`` en vez de fallar.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.clients.supabase import _supabase_request
from app.core.permisos import _solo_admin

logger = logging.getLogger("wtsp_pita")

PLAN_ENTREGA_FUTURA = "subsidios_futura"
PLAN_ENTREGA_INMEDIATA = "subsidios_inmediata"
MAX_PROYECTOS_POR_PLAN = 12             # nombres que se listan por plan (si hay más, se cuenta cuántos más)
MAX_PROPIEDADES_POR_PLAN = 600          # propiedades que se ofrecen para elegir en cada plan
_PAGINA = 1000
_MAX_PROYECTOS = 5000


def _no_existe(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        try:
            cuerpo = e.response.json()
        except Exception:
            cuerpo = {}
        return e.response.status_code == 404 or str((cuerpo or {}).get("code")) in ("PGRST205", "42P01")
    return False


def proyectos_visibles(perfil: Dict[str, Any], proyectos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Los proyectos que esta persona puede usar: administración todos; un ejecutivo, los suyos y los de sus inmobiliarias (el mismo criterio de ``/api/proyectos``)."""
    if _solo_admin(perfil):
        return proyectos
    mios = {str(p) for p in (perfil.get("proyecto_ids") or []) if p is not None}
    inms = {str(i) for i in (perfil.get("inmobiliaria_ids") or []) if i is not None}
    return [p for p in proyectos if str(p.get("id")) in mios or str(p.get("inmobiliaria_id")) in inms]


def planes_de_proyecto(proyecto: Dict[str, Any], asignaciones: List[Dict[str, Any]], estados_etapa: List[str]) -> List[str]:
    """Las claves de los planes que aplican a un proyecto: primero las asignadas a ESE proyecto; si no hay, las de su inmobiliaria; si tampoco, según su estado de entrega
    (futura y/o inmediata, ambos si tiene etapas de los dos tipos). Sin etapas ni asignación: ninguno."""
    pid, iid = str(proyecto.get("id")), str(proyecto.get("inmobiliaria_id"))
    propias = [a["plan_clave"] for a in asignaciones if a.get("proyecto_id") is not None and str(a["proyecto_id"]) == pid]
    if propias:
        return list(dict.fromkeys(propias))
    de_inmobiliaria = [a["plan_clave"] for a in asignaciones if a.get("inmobiliaria_id") is not None and str(a["inmobiliaria_id"]) == iid]
    if de_inmobiliaria:
        return list(dict.fromkeys(de_inmobiliaria))
    claves = []
    if "entrega_futura" in estados_etapa:
        claves.append(PLAN_ENTREGA_FUTURA)
    if "entrega_inmediata" in estados_etapa:
        claves.append(PLAN_ENTREGA_INMEDIATA)
    return claves


async def _leer(ruta: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
    filas: List[Dict[str, Any]] = []
    while len(filas) < _MAX_PROYECTOS:
        lote = await _supabase_request("GET", ruta, params={**params, "limit": str(_PAGINA), "offset": str(len(filas))}) or []
        filas.extend(lote)
        if len(lote) < _PAGINA:
            break
    return filas


def _numero(v: Any) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n else None


async def _propiedades_por_proyecto(ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Tipologías con valor en UF de cada proyecto (activas): ``{proyecto_id: [{id, tipologia, valor_uf}]}``. Si la lectura falla, la calculadora sigue funcionando sin la lista (se escribe el precio)."""
    salida: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for i in range(0, len(ids), 100):
            filas = await _leer("/Tipologia", {"proyecto_id": f"in.({','.join(ids[i:i + 100])})", "select": "id,proyecto_id,nombre,valor_uf,estado", "order": "id.asc"})
            for t in filas:
                valor = _numero(t.get("valor_uf"))
                if valor is None or valor <= 0 or t.get("estado") in ("agotado", "inactivo"):
                    continue
                salida.setdefault(str(t.get("proyecto_id")), []).append({"id": t.get("id"), "tipologia": str(t.get("nombre") or "").strip(), "valor_uf": valor})
    except Exception as e:
        logger.warning("Comisiones | no se pudieron leer las tipologías para elegir propiedad (%s)", type(e).__name__)
        return {}
    return salida


async def planes_para(perfil: Dict[str, Any]) -> Dict[str, Any]:
    """Los planes que ve esta persona, cada uno con los proyectos a los que aplica, y (solo administración) los proyectos que quedaron sin plan."""
    try:
        planes = await _leer("/PlanComision", {"select": "clave,nombre,ticket_uf,porcentaje,bono_2_clp,bono_3_clp,condicion,orden,activo", "order": "orden.asc,clave.asc"})
    except Exception as e:
        if _no_existe(e):
            return {"disponible": False}
        raise
    try:
        asignaciones = await _leer("/PlanComisionAsignacion", {"select": "plan_clave,proyecto_id,inmobiliaria_id", "order": "id.asc"})
    except Exception as e:
        if not _no_existe(e):
            raise
        asignaciones = []
    proyectos = proyectos_visibles(perfil, await _leer("/Proyecto", {"select": "id,nombre,inmobiliaria_id", "order": "nombre.asc"}))
    estados: Dict[str, List[str]] = {}
    ids = [str(p["id"]) for p in proyectos]
    for i in range(0, len(ids), 100):
        for e in await _supabase_request("GET", "/Etapa", params={"proyecto_id": f"in.({','.join(ids[i:i + 100])})", "select": "proyecto_id,estado"}) or []:
            estados.setdefault(str(e.get("proyecto_id")), []).append(str(e.get("estado") or ""))

    activos = {p["clave"]: p for p in planes if p.get("activo") is not False}
    tipologias = await _propiedades_por_proyecto(ids)
    por_plan: Dict[str, List[str]] = {}
    propiedades_plan: Dict[str, List[Dict[str, Any]]] = {}
    sin_plan: List[str] = []
    for pr in proyectos:
        claves = [c for c in planes_de_proyecto(pr, asignaciones, estados.get(str(pr["id"]), [])) if c in activos]
        if not claves:
            sin_plan.append(pr.get("nombre") or "Proyecto sin nombre")
        for c in claves:
            por_plan.setdefault(c, []).append(pr.get("nombre") or "Proyecto sin nombre")
            for t in tipologias.get(str(pr["id"]), []):
                propiedades_plan.setdefault(c, []).append({"id": t["id"], "proyecto": pr.get("nombre") or "Proyecto sin nombre", "tipologia": t["tipologia"], "valor_uf": t["valor_uf"]})
    salida = []
    for p in planes:
        nombres = por_plan.get(p["clave"], [])
        if p["clave"] not in activos or not nombres:
            continue
        salida.append({"clave": p["clave"], "nombre": p["nombre"], "ticket_uf": _numero(p.get("ticket_uf")), "porcentaje": _numero(p.get("porcentaje")),
                       "bono_2_clp": _numero(p.get("bono_2_clp")), "bono_3_clp": _numero(p.get("bono_3_clp")), "condicion": p.get("condicion"),
                       "proyectos": nombres[:MAX_PROYECTOS_POR_PLAN], "proyectos_total": len(nombres),
                       "propiedades": sorted(propiedades_plan.get(p["clave"], []), key=lambda x: (x["proyecto"].lower(), x["tipologia"].lower(), str(x["id"])))[:MAX_PROPIEDADES_POR_PLAN]})
    return {"disponible": True, "planes": salida, "sin_plan": sorted(set(sin_plan))[:50] if _solo_admin(perfil) else []}
