"""Datos extra de cada cliente para las vistas «Tabla» y «Pipeline» del panel: la tipología que busca (con su etapa) y cuántas notas internas tiene.

Se piden en lotes de 250 clientes y en paralelo. Es de «mejor esfuerzo»: si una consulta falla (o la tabla de notas aún no existe), esa parte se omite y la lista de clientes
sale igual, como antes. Nunca se agrega nada que no sea el nombre de la tipología/etapa y un conteo: las notas en sí no viajan.
"""
import asyncio
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from app.clients.supabase import _supabase_request

logger = logging.getLogger("wtsp_pita")

LOTE = 250


NOTA_RESUMEN = 200             # de la última nota de cada cliente sale solo el comienzo (las tarjetas del Pipeline la muestran)
PAGINA_LECTURA = 1000          # filas por consulta: el tope por defecto de Supabase (si el proyecto lo bajara, la lista se cortaría antes)
MAX_CLIENTES_LISTA = 10_000    # la lista del CRM no pasa de esto; el CRM avisa cuando se llega (frontend/index.html: MAX_CLIENTES_LISTA)


async def leer_en_paginas(ruta: str, params: Dict[str, str], maximo: Optional[int] = None, pedir=None) -> List[Dict[str, Any]]:
    """Lee todas las filas de una consulta, de a ``PAGINA_LECTURA``, hasta ``maximo``. Sin esto la base corta la respuesta en silencio en
    su tope (1.000 filas) y los clientes más antiguos desaparecían de la lista. Quita repetidos por ``id`` (si entra un cliente nuevo
    mientras se lee, las páginas se corren y una misma fila podría salir dos veces)."""
    maximo = maximo or MAX_CLIENTES_LISTA
    pedir = pedir or _supabase_request           # quien llama puede pasar su propia función de consulta (así sus pruebas la siguen interceptando)
    filas: List[Dict[str, Any]] = []
    while len(filas) < maximo:
        pedido = min(PAGINA_LECTURA, maximo - len(filas))
        lote = await pedir("GET", ruta, params={**params, "limit": str(pedido), "offset": str(len(filas))}) or []
        filas.extend(lote)
        if len(lote) < pedido:
            break
    vistos, unicas = set(), []
    for f in filas:
        clave = f.get("id") if isinstance(f, dict) else None
        if clave is not None:
            if clave in vistos:
                continue
            vistos.add(clave)
        unicas.append(f)
    return unicas


def _lotes(valores: List[Any]) -> List[List[Any]]:
    return [valores[i:i + LOTE] for i in range(0, len(valores), LOTE)]


async def _tipologias(filas: List[Dict[str, Any]]) -> None:
    ids = sorted({int(f["etapa_tipologia_id"]) for f in filas if str(f.get("etapa_tipologia_id") or "").isdigit()})
    if not ids:
        return

    async def uno(lote: List[int]) -> List[Dict[str, Any]]:
        try:
            return await _supabase_request("GET", "/EtapaTipologia", params={
                "id": f"in.({','.join(str(i) for i in lote)})", "select": "id,Tipologia(nombre),Etapa(nombre)"}) or []
        except Exception as e:
            logger.warning("Clientes | no se pudieron leer las tipologías de un lote (%s)", type(e).__name__)
            return []

    por_id: Dict[int, Dict[str, Any]] = {}
    for lote in await asyncio.gather(*[uno(l) for l in _lotes(ids)]):
        for r in lote:
            if isinstance(r, dict) and r.get("id") is not None:
                por_id[int(r["id"])] = r
    for f in filas:
        r = por_id.get(int(f["etapa_tipologia_id"])) if str(f.get("etapa_tipologia_id") or "").isdigit() else None
        if r:
            f["tipologia_nombre"] = (r.get("Tipologia") or {}).get("nombre")
            f["etapa_nombre"] = (r.get("Etapa") or {}).get("nombre")


async def _notas(filas: List[Dict[str, Any]]) -> None:
    ids = sorted({int(f["id"]) for f in filas if f.get("id") is not None})
    if not ids:
        return

    async def uno(lote: List[int]):
        try:
            return await _supabase_request("GET", "/NotaCliente", params={"cliente_id": f"in.({','.join(str(i) for i in lote)})", "select": "cliente_id,texto,creado_en,autor_nombre", "order": "creado_en.desc", "limit": "10000"}) or []
        except Exception as e:
            logger.info("Clientes | no se pudieron contar las notas de un lote (%s)", type(e).__name__)
            return None

    cuenta: Counter = Counter()
    ultima: Dict[int, Dict[str, Any]] = {}
    hubo = False
    for lote in await asyncio.gather(*[uno(l) for l in _lotes(ids)]):
        if lote is None:
            continue
        hubo = True
        for n in lote:
            if isinstance(n, dict) and n.get("cliente_id") is not None:
                cid = int(n["cliente_id"])
                cuenta[cid] += 1
                if cid not in ultima:                                   # vienen de la más nueva a la más antigua: la primera de cada cliente es la última nota
                    ultima[cid] = {"texto": str(n.get("texto") or "").strip()[:NOTA_RESUMEN], "creado_en": n.get("creado_en"), "autor": n.get("autor_nombre")}
    if hubo:
        for f in filas:
            f["notas_n"] = cuenta.get(int(f["id"]), 0)
            if int(f["id"]) in ultima:
                f["nota_ultima"] = ultima[int(f["id"])]


async def enriquecer_clientes(filas: List[Dict[str, Any]]) -> None:
    """Agrega a cada fila ``tipologia_nombre``, ``etapa_nombre``, ``notas_n`` y ``nota_ultima`` (el comienzo de su última nota) cuando se pueden saber. Nunca lanza."""
    if not filas:
        return
    try:
        for r in await asyncio.gather(_tipologias(filas), _notas(filas), return_exceptions=True):      # que una parte falle no deja a la otra a medias
            if isinstance(r, Exception):
                logger.warning("Clientes | no se pudo agregar una parte de los datos extra (%s)", type(r).__name__)
    except Exception as e:
        logger.warning("Clientes | no se pudieron agregar los datos extra (%s)", type(e).__name__)
