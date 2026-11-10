"""Repartir entre ejecutivos clientes que YA están cargados (por ejemplo, los miles que quedaron a una sola persona en una carga).

Solo administración. La pantalla manda los ids de los clientes elegidos (los que se ven en la Tabla con sus filtros) y el reparto ``[{usuario_id, cantidad}]``; los clientes
se asignan en bloques seguidos, en el orden de los ids, igual que en la carga. Cada bloque se guarda con una sola consulta por cada 100 clientes.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.clients.supabase import _supabase_request
from app.core.errors import AppError, mensaje_publico
from app.services.carga_clientes import RECOMENDADO_POR_PERSONA, Reparto

logger = logging.getLogger("wtsp_pita")

MAX_CLIENTES_REPARTO = 20_000     # por vez
_LOTE_IDS = 100                   # ids por consulta (el largo de la dirección tiene tope)
_CONSULTAS_A_LA_VEZ = 5


def ids_del_cuerpo(cuerpo: Any) -> List[int]:
    """Los ids de clientes del cuerpo de la petición, sin repetir y en el mismo orden. ``AppError`` 400 si no sirven."""
    ids = cuerpo.get("ids") if isinstance(cuerpo, dict) else None
    if not isinstance(ids, list) or not ids:
        raise AppError("Elige al menos un cliente para repartir.", 400)
    if len(ids) > MAX_CLIENTES_REPARTO:
        raise AppError(f"Se pueden repartir hasta {MAX_CLIENTES_REPARTO:,} clientes por vez; elegiste {len(ids):,}.".replace(",", "."), 400)
    salida: List[int] = []
    vistos: set = set()
    for i in ids:
        if isinstance(i, bool) or not isinstance(i, int) or i < 1:
            raise AppError("Uno de los clientes elegidos no es válido. Vuelve a elegirlos.", 400)
        if i not in vistos:
            vistos.add(i)
            salida.append(i)
    return salida


def _lotes(ids: List[int]) -> List[List[int]]:
    return [ids[i:i + _LOTE_IDS] for i in range(0, len(ids), _LOTE_IDS)]


async def encontrar_clientes(ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Esos clientes, por id, con su dueño actual y su proyecto. ``AppError`` 400 si alguno ya no existe."""
    cupo = asyncio.Semaphore(_CONSULTAS_A_LA_VEZ)

    async def uno(lote: List[int]):
        async with cupo:
            return await _supabase_request("GET", "/Cliente", params={"id": f"in.({','.join(str(i) for i in lote)})", "select": "id,usuario_id,proyecto_id"}) or []
    encontrados: Dict[int, Dict[str, Any]] = {}
    for filas in await asyncio.gather(*[uno(l) for l in _lotes(ids)]):
        for f in filas:
            encontrados[int(f["id"])] = f
    faltan = [i for i in ids if i not in encontrados]
    if faltan:
        raise AppError(f"{len(faltan)} de los clientes elegidos ya no existen. Actualiza la lista y vuelve a elegirlos.", 400)
    return encontrados


def exige_confirmacion_reparto(reparto: Reparto) -> Optional[str]:
    """Si el reparto deja a una persona por encima de lo recomendado, el mensaje que hay que confirmar; si no, ``None``."""
    mayor = max((c for u, c in reparto if u), default=0)
    if mayor > RECOMENDADO_POR_PERSONA:
        return (f"El reparto deja hasta {mayor} clientes en una sola persona (lo recomendado es hasta {RECOMENDADO_POR_PERSONA}). "
                "Repártelos entre más ejecutivos o confirma el reparto.")
    return None


def bloques(ids: List[int], reparto: Reparto) -> List[Dict[str, Any]]:
    """Parte los ids en bloques seguidos, uno por persona del reparto, en el orden dado. ``AppError`` 400 si el reparto no suma exactamente los clientes elegidos."""
    suma = sum(c for _, c in reparto)
    if suma != len(ids):
        raise AppError(f"El reparto suma {suma} clientes pero elegiste {len(ids)}: ajusta las cantidades para que calcen.", 400)
    salida, desde = [], 0
    for uid, cantidad in reparto:
        salida.append({"usuario_id": uid, "ids": ids[desde:desde + cantidad]})
        desde += cantidad
    return salida


async def reasignar(ids: List[int], reparto: Reparto) -> Dict[str, Any]:
    """Guarda el nuevo dueño de cada cliente. Un bloque que no se pueda guardar no frena a los demás: se cuenta como «fallidos» y se puede repetir (reasignar es repetible)."""
    cupo = asyncio.Semaphore(_CONSULTAS_A_LA_VEZ)
    trabajos = [(b["usuario_id"], lote) for b in bloques(ids, reparto) for lote in _lotes(b["ids"])]

    async def guardar(uid: Optional[str], lote: List[int]):
        async with cupo:
            try:
                await _supabase_request("PATCH", "/Cliente", params={"id": f"in.({','.join(str(i) for i in lote)})"}, json={"usuario_id": uid})
                return True, None
            except Exception as ex:
                return False, mensaje_publico(ex, "api_reparto_clientes")
    asignados: Dict[str, int] = {}
    fallidos, motivos = 0, []
    for (uid, lote), (ok, motivo) in zip(trabajos, await asyncio.gather(*[guardar(u, l) for u, l in trabajos])):
        if ok:
            asignados[str(uid or "")] = asignados.get(str(uid or ""), 0) + len(lote)
        else:
            fallidos += len(lote)
            if motivo not in motivos:
                motivos.append(motivo)
    return {"ok": True, "reasignados": sum(asignados.values()), "fallidos": fallidos, "asignados": asignados, "motivos": motivos[:3]}
