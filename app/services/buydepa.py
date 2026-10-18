"""Scraping de buydepa: ayudantes que crean, actualizan y dan de baja los departamentos como proyectos (precio, tipología,
stock, imágenes, ofertas, log de cambios y log de ejecuciones).

Movido sin cambios desde main.py (Fase 6, tramo 2, lote B). ``_sincronizar_buydepa_stream`` es el flujo completo: trae los
departamentos y va reportando el avance (SSE).
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.clients.mindicador import _obtener_valor_uf
from app.clients.supabase import _supabase_request
from app.core.http import http_client
from app.core.utilidades import _safe_httpx_error

logger = logging.getLogger("wtsp_pita")


BUYDEPA_API_URL = (
    "https://sales.core.buydepa.com/api/_web/apartments"
    "?page=1&pageSize=1000"
    "&north=-33.344582824537326&south=-33.55284011095616"
    "&east=-70.61119079589845&west=-70.72723388671876"
)


BUYDEPA_INMOBILIARIA_ID = 13
UF_UMBRAL_CLP = 1_000_000


def _precio_a_uf(precio: Any, uf_clp: float) -> Optional[float]:
    try:
        p = float(precio)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    return round(p if p < UF_UMBRAL_CLP else p / uf_clp, 2)


async def _registrar_cambio_depa(nombre: Optional[str], fuente_id: str, campo: str,
                                 anterior: Any, nuevo: Any) -> None:
    try:
        await _supabase_request("POST", "/log_depas", json={
            "direccion": nombre,
            "fuente_externo_id": fuente_id,
            "campo": campo,
            "valor_anterior": None if anterior is None else str(anterior),
            "valor_nuevo": None if nuevo is None else str(nuevo),
        }, extra_headers={"Prefer": "return=minimal"})
    except Exception:
        logger.exception("No se pudo registrar el cambio de %s en log_depas", fuente_id)


async def _registrar_oferta_depa(nombre: Optional[str], fuente_id: str, item: Dict[str, Any]) -> bool:
    """Registra en log_depas los cambios de descuento y etiquetas de oferta de buydepa.

    listPrice ya viene con el descuento aplicado (verificado contra avg_uf_m2_price), por lo
    que el precio se guarda tal cual y aquí solo se deja trazabilidad de la oferta.
    """
    try:
        pct = item.get("relativeDiscountPercentage")
        tags = sorted(t for t in (item.get("tags") or []) if isinstance(t, str))
        actual = {
            "descuento_pct": None if pct is None else str(pct),
            "ofertas": ", ".join(tags) or None,
        }
        filas = await _supabase_request("GET", "/log_depas", params={
            "fuente_externo_id": f"eq.{fuente_id}",
            "campo": "in.(descuento_pct,ofertas)",
            "select": "campo,valor_nuevo",
            "order": "detectado_en.desc",
            "limit": "20",
        }) or []
        ultimo: Dict[str, Any] = {}
        for f in filas:
            ultimo.setdefault(f["campo"], f.get("valor_nuevo"))
        hubo_cambio = False
        for campo, nuevo in actual.items():
            anterior = ultimo.get(campo)
            if anterior != nuevo:
                await _registrar_cambio_depa(nombre, fuente_id, campo, anterior, nuevo)
                hubo_cambio = True
        return hubo_cambio
    except Exception:
        logger.exception("No se pudo registrar la oferta de %s en log_depas", fuente_id)
        return False


def _payload_tipologia_buydepa(proyecto_id: Any, nombre: str, item: Dict[str, Any],
                               precio_uf: Optional[float], disponible: bool) -> Dict[str, Any]:
    return {
        "proyecto_id": proyecto_id,
        "nombre": nombre,
        # El default de la columna ('activa') viola tipologia_estado_check; los valores válidos son activo/agotado/inactivo
        "estado": "activo" if disponible else "agotado",
        "dormitorios": item.get("bedrooms"),
        "banos": item.get("bathrooms"),
        "superficie_util_m2": item.get("totalArea"),
        "valor_uf": precio_uf,
        "estacionamientos": item.get("parkings"),
        "bodegas": item.get("storages"),
    }


async def _normalizar_codigo_depa(existente: Dict[str, Any], fuente_id: str) -> bool:
    """Proyectos creados con el código viejo (dirección+precio) pasan a buydepa-<id>."""
    codigo = f"buydepa-{fuente_id}"
    if existente.get("codigo") == codigo:
        return False
    await _supabase_request("PATCH", "/Proyecto",
        params={"id": f"eq.{existente['id']}"}, json={"codigo": codigo},
        extra_headers={"Prefer": "return=minimal"})
    return True


async def _completar_tipologia_depa(existente: Dict[str, Any], item: Dict[str, Any],
                                    uf_clp: float, disponible: bool) -> bool:
    """Si un proyecto de buydepa quedó sin tipología (corridas fallidas anteriores), la crea."""
    tips = await _supabase_request("GET", "/Tipologia",
        params={"proyecto_id": f"eq.{existente['id']}", "select": "id", "limit": "1"}) or []
    if tips:
        return False
    etapas = await _supabase_request("GET", "/Etapa",
        params={"proyecto_id": f"eq.{existente['id']}", "select": "id", "order": "id.asc", "limit": "1"}) or []
    if etapas:
        etapa_id = etapas[0]["id"]
    else:
        etapa = await _supabase_request("POST", "/Etapa", json={
            "proyecto_id": existente["id"], "nombre": "Entrega inmediata", "estado": "entrega_inmediata",
        }, extra_headers={"Prefer": "return=representation"})
        etapa = etapa[0] if isinstance(etapa, list) and etapa else etapa
        if not etapa or not etapa.get("id"):
            raise RuntimeError("No se pudo crear la etapa")
        etapa_id = etapa["id"]
    nombre = existente.get("nombre") or (item.get("address") or "").strip() or f"Depto buydepa {item.get('id')}"
    tip = await _supabase_request("POST", "/Tipologia",
        json=_payload_tipologia_buydepa(existente["id"], nombre, item, _precio_a_uf(item.get("listPrice"), uf_clp), disponible),
        extra_headers={"Prefer": "return=representation"})
    tip = tip[0] if isinstance(tip, list) and tip else tip
    if not tip or not tip.get("id"):
        raise RuntimeError("No se pudo crear la tipología")
    try:
        await _supabase_request("POST", "/EtapaTipologia", json={
            "etapa_id": etapa_id, "tipologia_id": tip["id"], "stock": 1 if disponible else 0,
        }, extra_headers={"Prefer": "return=minimal"})
    except Exception:
        await _supabase_request("DELETE", "/Tipologia", params={"id": f"eq.{tip['id']}"})
        raise
    return True


async def _sincronizar_precio_depa(existente: Dict[str, Any], item: Dict[str, Any],
                                   uf_clp: float, fuente_id: str) -> bool:
    precio_uf = _precio_a_uf(item.get("listPrice"), uf_clp)
    if precio_uf is None:
        return False
    tips = await _supabase_request("GET", "/Tipologia",
        params={"proyecto_id": f"eq.{existente['id']}", "select": "id,valor_uf", "limit": "1"}) or []
    if not tips:
        return False
    tip = tips[0]
    anterior = float(tip["valor_uf"]) if tip.get("valor_uf") is not None else None
    if anterior is not None and abs(anterior - precio_uf) < 0.01:
        return False
    await _supabase_request("PATCH", "/Tipologia",
        params={"id": f"eq.{tip['id']}"}, json={"valor_uf": precio_uf},
        extra_headers={"Prefer": "return=minimal"})
    await _supabase_request("PATCH", "/Proyecto",
        params={"id": f"eq.{existente['id']}"},
        json={"ahorro_minimo_uf": round(precio_uf * 0.10, 2)},
        extra_headers={"Prefer": "return=minimal"})
    await _registrar_cambio_depa(existente.get("nombre"), fuente_id, "precio_uf", anterior, precio_uf)
    return True


async def _revertir_proyecto_huerfano(proyecto_id: Any) -> None:
    """Borra Proyecto/Etapa/Tipologia/EtapaTipologia creados a medias por un ítem que falló."""
    try:
        etapas = await _supabase_request("GET", "/Etapa",
            params={"proyecto_id": f"eq.{proyecto_id}", "select": "id"}) or []
        for e in etapas:
            await _supabase_request("DELETE", "/EtapaTipologia", params={"etapa_id": f"eq.{e['id']}"})
        await _supabase_request("DELETE", "/Tipologia", params={"proyecto_id": f"eq.{proyecto_id}"})
        await _supabase_request("DELETE", "/Etapa", params={"proyecto_id": f"eq.{proyecto_id}"})
        await _supabase_request("DELETE", "/Proyecto", params={"id": f"eq.{proyecto_id}"})
    except Exception:
        logger.exception("No se pudo revertir completamente el proyecto huérfano %s", proyecto_id)


_EXT_IMAGEN = re.compile(r"\.(jpe?g|png|webp|gif)(\?.*)?$", re.IGNORECASE)


def _urls_imagen(x: Any) -> List[str]:
    """URLs de imagen que hay dentro de x (lista/dict anidado), sin repetidas y en orden."""
    urls: List[str] = []

    def recorrer(v: Any) -> None:
        if isinstance(v, str):
            if v.startswith("http") and _EXT_IMAGEN.search(v) and v not in urls:
                urls.append(v)
        elif isinstance(v, list):
            for e in v:
                recorrer(e)
        elif isinstance(v, dict):
            for e in v.values():
                recorrer(e)

    recorrer(x)
    return urls


def _imagenes_buydepa(images: Any) -> List[str]:
    """Fotos a mostrar de un departamento: las de 'post-remodeling' (ya remodelado) y, si no
    tiene, las fotos normales de la publicación ('legacy' o lista plana). Se descartan
    'pre-remodeling' e 'inspection' porque son del estado anterior a la remodelación."""
    if isinstance(images, list):
        return _urls_imagen(images)
    if isinstance(images, dict):
        return _urls_imagen(images.get("post-remodeling")) or _urls_imagen(images.get("legacy"))
    return []


async def _sincronizar_imagenes_depa(existente: Dict[str, Any], imagenes: List[str]) -> bool:
    if not imagenes:
        return False
    if existente.get("imagenes") == imagenes and existente.get("imagen_url") == imagenes[0]:
        return False
    await _supabase_request("PATCH", "/Proyecto",
        params={"id": f"eq.{existente['id']}"},
        json={"imagen_url": imagenes[0], "imagenes": imagenes},
        extra_headers={"Prefer": "return=minimal"})
    return True


async def _actualizar_stock_proyecto(proyecto_id: Any, disponible: bool) -> None:
    """Refleja la disponibilidad en el stock de la (única) EtapaTipologia del proyecto."""
    etapas = await _supabase_request("GET", "/Etapa",
        params={"proyecto_id": f"eq.{proyecto_id}", "select": "id"}) or []
    for etapa_row in etapas:
        et_rows = await _supabase_request("GET", "/EtapaTipologia",
            params={"etapa_id": f"eq.{etapa_row['id']}", "select": "id"}) or []
        for et in et_rows:
            await _supabase_request("PATCH", "/EtapaTipologia",
                params={"id": f"eq.{et['id']}"},
                json={"stock": 1 if disponible else 0},
                extra_headers={"Prefer": "return=minimal"})


async def _registrar_log_scraping(fuente: str, usuario_id: Optional[str], creados: int,
                                   actualizados: int, desactivados: int, errores: int,
                                   detalle_errores: Optional[list], estado: str) -> None:
    try:
        await _supabase_request("POST", "/ScrapingLog", json={
            "fuente": fuente,
            "ejecutado_por": usuario_id,
            "creados": creados, "actualizados": actualizados,
            "desactivados": desactivados, "errores": errores,
            "detalle_errores": detalle_errores or [],
            "estado": estado,
        }, extra_headers={"Prefer": "return=minimal"})
    except Exception:
        logger.exception("No se pudo registrar el log de scraping")


async def _sincronizar_buydepa_stream(usuario_id: Optional[str] = None):
    """Generador SSE: trae los departamentos de buydepa y reporta avance ítem por ítem."""
    try:
        async with http_client(30) as client:
            r = await client.get(BUYDEPA_API_URL)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        motivo = f"Error al consultar la API de buydepa: {e}"
        await _registrar_log_scraping("buydepa", usuario_id, 0, 0, 0, 1, [{"motivo": motivo}], "error")
        yield f"data: {json.dumps({'t': 'error', 'msg': motivo})}\n\n"
        return

    items = data if isinstance(data, list) else (
        data.get("data") or data.get("items") or data.get("results") or data.get("apartments") or []
    )
    total = len(items)

    existentes = await _supabase_request("GET", "/Proyecto",
        params={"inmobiliaria_id": f"eq.{BUYDEPA_INMOBILIARIA_ID}",
                "fuente_externo_id": "not.is.null",
                "select": "id,nombre,codigo,imagen_url,imagenes,fuente_externo_id,activo"}) or []
    existentes_por_id = {e["fuente_externo_id"]: e for e in existentes}
    uf_clp = await _obtener_valor_uf()

    creados = actualizados = desactivados = 0
    errores: List[Dict[str, Any]] = []
    ids_vistos_hoy: set = set()

    for idx, item in enumerate(items, start=1):
        fuente_id = str(item.get("id") or "").strip()
        direccion_log = (item.get("address") or "").strip() or (fuente_id or "?")
        accion = "sin_cambios"
        if not fuente_id:
            accion = "omitido"
        else:
            ids_vistos_hoy.add(fuente_id)
            try:
                disponible = item.get("state") == "available"
                existente = existentes_por_id.get(fuente_id)

                if existente:
                    hubo_cambio = False
                    if bool(existente.get("activo")) != disponible:
                        await _supabase_request("PATCH", "/Proyecto",
                            params={"id": f"eq.{existente['id']}"},
                            json={"activo": disponible},
                            extra_headers={"Prefer": "return=minimal"})
                        await _actualizar_stock_proyecto(existente["id"], disponible)
                        await _registrar_cambio_depa(existente.get("nombre"), fuente_id, "disponible",
                                                     str(bool(existente.get("activo"))).lower(), str(disponible).lower())
                        hubo_cambio = True
                        if not disponible:
                            desactivados += 1
                    if await _completar_tipologia_depa(existente, item, uf_clp, disponible):
                        hubo_cambio = True
                    if await _normalizar_codigo_depa(existente, fuente_id):
                        hubo_cambio = True
                    if await _sincronizar_imagenes_depa(existente, _imagenes_buydepa(item.get("images"))):
                        hubo_cambio = True
                    if await _sincronizar_precio_depa(existente, item, uf_clp, fuente_id):
                        hubo_cambio = True
                    if await _registrar_oferta_depa(existente.get("nombre"), fuente_id, item):
                        hubo_cambio = True
                    if hubo_cambio:
                        actualizados += 1
                        accion = "actualizado"
                else:
                    # Nuevo departamento → crear Proyecto + Etapa + Tipologia + EtapaTipologia
                    direccion  = direccion_log or f"Depto buydepa {fuente_id}"
                    precio_uf  = _precio_a_uf(item.get("listPrice"), uf_clp)
                    ahorro_min = round(precio_uf * 0.10, 2) if precio_uf else None
                    imagenes   = _imagenes_buydepa(item.get("images"))
                    codigo     = f"buydepa-{fuente_id}"

                    proyecto = await _supabase_request("POST", "/Proyecto", json={
                        "nombre": direccion, "codigo": codigo, "ubicacion": direccion,
                        "inmobiliaria_id": BUYDEPA_INMOBILIARIA_ID,
                        "imagen_url": imagenes[0] if imagenes else None,
                        "imagenes": imagenes,
                        "ahorro_minimo_uf": ahorro_min,
                        "valor_reserva_clp": None, "valor_reserva_uf": None,
                        "tiene_piloto": True,
                        "acepta_ds19": False, "acepta_ds1_t23": False,
                        "condicion_propiedad": "semi-nueva",
                        "activo": disponible,
                        "fuente_externo_id": fuente_id,
                    }, extra_headers={"Prefer": "return=representation"})
                    proyecto = proyecto[0] if isinstance(proyecto, list) and proyecto else proyecto
                    if not proyecto or not proyecto.get("id"):
                        raise RuntimeError("No se pudo crear el proyecto")

                    try:
                        etapa = await _supabase_request("POST", "/Etapa", json={
                            "proyecto_id": proyecto["id"],
                            "nombre": "Entrega inmediata",
                            "estado": "entrega_inmediata",
                        }, extra_headers={"Prefer": "return=representation"})
                        etapa = etapa[0] if isinstance(etapa, list) and etapa else etapa
                        if not etapa or not etapa.get("id"):
                            raise RuntimeError("No se pudo crear la etapa")

                        tipologia = await _supabase_request("POST", "/Tipologia",
                            json=_payload_tipologia_buydepa(proyecto["id"], direccion, item, precio_uf, disponible),
                            extra_headers={"Prefer": "return=representation"})
                        tipologia = tipologia[0] if isinstance(tipologia, list) and tipologia else tipologia
                        if not tipologia or not tipologia.get("id"):
                            raise RuntimeError("No se pudo crear la tipología")

                        await _supabase_request("POST", "/EtapaTipologia", json={
                            "etapa_id": etapa["id"], "tipologia_id": tipologia["id"],
                            "stock": 1 if disponible else 0,
                        }, extra_headers={"Prefer": "return=minimal"})
                    except Exception:
                        # Quedó a medio crear (sin etapa, tipología o el vínculo entre ambas) →
                        # deshacer todo para que la próxima corrida lo reintente limpio en vez
                        # de dejar un proyecto huérfano sin tipología.
                        await _revertir_proyecto_huerfano(proyecto["id"])
                        raise

                    await _registrar_oferta_depa(direccion, fuente_id, item)
                    creados += 1
                    accion = "creado"
            except Exception as ex:
                errores.append({"fuente_externo_id": fuente_id, "direccion": direccion_log, "motivo": _safe_httpx_error(ex)})
                accion = "error"

        yield f"data: {json.dumps({'t':'prog','n':idx,'total':total,'accion':accion,'direccion':direccion_log,'creados':creados,'actualizados':actualizados,'desactivados':desactivados,'errores':len(errores)})}\n\n"

    # Departamentos que ya no aparecen en la respuesta de hoy → se dan de baja
    for fuente_id, existente in existentes_por_id.items():
        if fuente_id not in ids_vistos_hoy and existente.get("activo"):
            try:
                await _registrar_cambio_depa(existente.get("nombre"), fuente_id, "disponible", "true", "false")
                await _supabase_request("PATCH", "/Proyecto",
                    params={"id": f"eq.{existente['id']}"},
                    json={"activo": False},
                    extra_headers={"Prefer": "return=minimal"})
                await _actualizar_stock_proyecto(existente["id"], False)
                desactivados += 1
                actualizados += 1
            except Exception as ex:
                errores.append({"fuente_externo_id": fuente_id, "motivo": str(ex)})

    estado_log = "ok" if not errores else "parcial"
    await _registrar_log_scraping("buydepa", usuario_id, creados, actualizados, desactivados, len(errores), errores, estado_log)
    yield f"data: {json.dumps({'t':'done','creados':creados,'actualizados':actualizados,'desactivados':desactivados,'errores':len(errores),'detalle_errores':errores})}\n\n"
