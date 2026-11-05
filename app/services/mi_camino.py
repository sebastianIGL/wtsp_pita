"""«Mi Camino» (Fase 7): lo que la página necesita de las fichas y las guías para ajustar su lista de requisitos.

* **Subsidios elegibles:** las fichas completas, publicadas, verificadas y al día (las que ya pasaron ``directorio.validar``) que tienen un dato de ahorro mínimo o de tope del Registro Social de
  Hogares. De cada una solo viajan esos dos datos (con su equivalente en pesos aproximado y su llamado) y la fecha de revisión. Nunca borradores ni datos «por confirmar».
* **Guías:** los nombres de las guías publicadas y al día, para que la página enlace «Cómo hacerlo» solo a guías que existen.
Si la base falla o faltan las tablas, cada parte responde vacía: la página sigue mostrando su lista general de siempre.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services import directorio as dr
from app.services import guias as gs

logger = logging.getLogger("wtsp_pita")

CLAVES = {"ahorro": "ahorro_minimo", "rsh": "rsh_maximo"}


def _dato(f: Dict[str, Any], clave: str) -> Optional[Dict[str, Any]]:
    d = next((d for d in f["datos"] if d["clave"] == clave), None)
    return {"valor": d["valor"], "pesos": d["pesos"], "llamado": d["llamado"]} if d else None


async def _subsidios() -> List[Dict[str, Any]]:
    salida: List[Dict[str, Any]] = []
    for f in sorted((await dr.fichas_publicas()).values(), key=lambda x: (x["orden"], x["nombre"], x["slug"])):
        if f["vencida"] or f["alcance"] != "completa":
            continue
        ahorro, rsh = _dato(f, CLAVES["ahorro"]), _dato(f, CLAVES["rsh"])
        if not (ahorro or rsh):
            continue
        salida.append({"slug": f["slug"], "ruta": f["ruta"], "nombre": f["nombre"], "grupo": f["grupo_clave"], "revisado": f["revisado"], "uf": f["uf"], "ahorro": ahorro, "rsh": rsh})
    return salida


async def datos() -> Dict[str, Any]:
    subsidios: List[Dict[str, Any]] = []
    guias: List[str] = []
    try:
        subsidios = await _subsidios()
    except dr.DirectorioNoDisponible:
        logger.info("Mi Camino | directorio no disponible: lista general")
    except Exception:
        logger.exception("Mi Camino | no se pudieron armar los subsidios: lista general")
    try:
        guias = [g["slug"] for g in await gs.guias_publicas() if not g["vencida"]]
    except gs.GuiasNoDisponibles:
        logger.info("Mi Camino | guías no disponibles: sin enlaces")
    except Exception:
        logger.exception("Mi Camino | no se pudieron leer las guías: sin enlaces")
    presentes = {s["grupo"] for s in subsidios}
    return {"subsidios": subsidios, "guias": guias, "grupos": [{"clave": c, "nombre": n} for c, n in dr.GRUPOS.items() if c in presentes]}
