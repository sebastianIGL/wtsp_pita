"""Seminuevos de Buydepa: descarga el contrato público documentado y crea, actualiza y da de baja los departamentos como proyectos (precio, tipología,
stock, imágenes, ofertas, log de cambios y log de ejecuciones).

``GET {BUYDEPA_API_BASE_URL}/_web/apartments?page=N&pageSize=1000`` (sin recorte geográfico), todas las páginas. La llave ``BUYDEPA_API_KEY`` es opcional y solo se envía
(cabecera ``X-Api-Key``) si tiene valor. La descarga solo es «íntegra» si lo acumulado coincide con ``pagination.total``: solo entonces se da de baja lo que ya no aparece, y nunca más
del 25 % de los activos de una vez. ``_sincronizar_buydepa_stream`` es el flujo completo (SSE, igual que siempre) y lo usan la corrida manual del panel y el bucle automático
(``bucle_buydepa``, cada ``BUYDEPA_SYNC_INTERVAL_HOURS``).
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.clients.mindicador import _obtener_valor_uf
from app.clients.supabase import _supabase_request
from app.core.config import MAX_BAJAS_PCT_DEFECTO, get_settings, leer_max_bajas_pct
from app.core.http import http_client
from app.core.utilidades import _safe_httpx_error

logger = logging.getLogger("wtsp_pita")


BUYDEPA_API_BASE_URL_DEFECTO = "https://sales.core.buydepa.com/api"
RUTA_DEPARTAMENTOS = "/_web/apartments"
TAMANO_PAGINA = 1000                     # Buydepa acepta más (probado hasta 100000); con 1000 hoy es una sola página
MAX_PAGINAS = 50                         # tope de seguridad contra un pageCount absurdo
MAX_REINTENTOS = 3                       # por página: 429, 5xx, tiempo agotado o error de conexión
ESPERA_MAX_429_S = 60.0                  # lo máximo que se obedece de Retry-After
ESPERA_429_SIN_CABECERA_S = 5.0

BUYDEPA_INMOBILIARIA_ID = 13
UF_UMBRAL_CLP = 1_000_000

REVISION_BUCLE_S = 600                   # el bucle automático mira cada 10 minutos si ya toca correr
INICIO_BUCLE_S = 120                     # tras arrancar espera un poco para no competir con el arranque
REINTENTO_TRAS_ERROR_S = 1800            # si la última corrida terminó en error, se reintenta a los 30 minutos
ESPERA_MAX_ERROR_BUCLE_S = 3600

_candado = asyncio.Lock()                # una sola sincronización a la vez en este proceso (manual o programada)


class DescargaBuydepa(Exception):
    """No se pudo leer una página de Buydepa (el texto nunca incluye la llave)."""


def _url_departamentos() -> str:
    base = (get_settings().buydepa_api_base_url or "").strip().rstrip("/") or BUYDEPA_API_BASE_URL_DEFECTO
    return base + RUTA_DEPARTAMENTOS


def _limite_bajas_pct() -> int:
    """Límite vigente del freno (``BUYDEPA_MAX_BAJAS_PCT``, entero de 1 a 100). Si la variable está puesta pero vacía o inválida, usa 25 y lo deja en el log."""
    crudo = os.environ.get("BUYDEPA_MAX_BAJAS_PCT")
    if crudo is not None and not leer_max_bajas_pct(crudo)[1]:
        logger.warning("Buydepa | BUYDEPA_MAX_BAJAS_PCT vacía o inválida (debe ser un entero de 1 a 100): se usa %d", MAX_BAJAS_PCT_DEFECTO)
    return get_settings().buydepa_max_bajas_pct


def _porcentaje(parte: int, total: int) -> str:
    x = round(100 * parte / total, 1)
    return f"{x:g}".replace(".", ",")


def _cabeceras() -> Dict[str, str]:
    """``X-Api-Key`` solo si hay llave: con una llave inválida Buydepa responde 401, así que sin llave no se manda nada."""
    llave = (get_settings().buydepa_api_key or "").strip()
    return {"X-Api-Key": llave} if llave else {}


async def _esperar(segundos: float) -> None:
    await asyncio.sleep(segundos)


def _espera_retry_after(r: httpx.Response) -> float:
    try:
        n = float((r.headers.get("Retry-After") or "").strip())
    except ValueError:
        n = ESPERA_429_SIN_CABECERA_S
    return max(1.0, min(n, ESPERA_MAX_429_S))


async def _pedir_pagina(client: Any, pagina: int) -> Any:
    """Una página, con reintentos acotados: 429 espera ``Retry-After``; 5xx, tiempo agotado y errores de conexión esperan 1, 2 y 4 s."""
    for intento in range(MAX_REINTENTOS + 1):
        ultimo = intento >= MAX_REINTENTOS
        try:
            r = await client.get(_url_departamentos(), params={"page": pagina, "pageSize": TAMANO_PAGINA}, headers=_cabeceras())
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if ultimo:
                raise DescargaBuydepa(f"sin respuesta de Buydepa ({type(e).__name__})") from None
            await _esperar(2 ** intento)
            continue
        if r.status_code == 429:
            if ultimo:
                raise DescargaBuydepa("Buydepa limitó las solicitudes (429) y no se levantó a tiempo")
            await _esperar(_espera_retry_after(r))
            continue
        if r.status_code >= 500:
            if ultimo:
                raise DescargaBuydepa(f"Buydepa respondió con error {r.status_code}")
            await _esperar(2 ** intento)
            continue
        if r.status_code == 401:
            raise DescargaBuydepa("Buydepa rechazó la llave (401): revisa BUYDEPA_API_KEY o déjala vacía")
        try:
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPStatusError, ValueError) as e:
            raise DescargaBuydepa(_safe_httpx_error(e)) from None
    raise DescargaBuydepa("sin respuesta de Buydepa")      # inalcanzable: el último intento siempre lanza o devuelve


def _leer_pagina(cuerpo: Any) -> Tuple[List[Dict[str, Any]], Optional[int], Optional[int]]:
    """``(departamentos, total, pageCount)``. Sin ``pagination`` válida, total y pageCount son None (no se puede comprobar que esté completa)."""
    if isinstance(cuerpo, list):
        return [x for x in cuerpo if isinstance(x, dict)], None, None
    if not isinstance(cuerpo, dict):
        return [], None, None
    items = cuerpo.get("results")
    if not isinstance(items, list):
        items = cuerpo.get("data") or cuerpo.get("items") or cuerpo.get("apartments") or []
    pag = cuerpo.get("pagination") if isinstance(cuerpo.get("pagination"), dict) else {}

    def _entero(v: Any) -> Optional[int]:
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None

    return [x for x in items if isinstance(x, dict)], _entero(pag.get("total")), _entero(pag.get("pageCount"))


async def _descargar_departamentos() -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    """Todos los departamentos, página a página. Devuelve ``(items, íntegra, motivo)``: ``íntegra`` solo si lo acumulado coincide con ``pagination.total`` (y no está vacío ni repite ids);
    si no, ``motivo`` dice por qué. Lanza ``DescargaBuydepa`` si ni la primera página se pudo leer."""
    acumulado: List[Dict[str, Any]] = []
    async with http_client(30) as client:
        items, total, paginas = _leer_pagina(await _pedir_pagina(client, 1))
        acumulado.extend(items)
        motivo: Optional[str] = None
        if total is None or paginas is None:
            motivo = "la respuesta de Buydepa no trae paginación: no se puede comprobar que esté completa"
        elif paginas > MAX_PAGINAS:
            motivo = f"Buydepa informa {paginas} páginas (más del máximo de {MAX_PAGINAS})"
        else:
            for pagina in range(2, paginas + 1):
                try:
                    items, _t, _p = _leer_pagina(await _pedir_pagina(client, pagina))
                except DescargaBuydepa as e:
                    motivo = f"la página {pagina} de {paginas} falló ({e})"
                    break
                if not items:
                    motivo = f"la página {pagina} de {paginas} llegó vacía"
                    break
                acumulado.extend(items)
    if motivo is None:
        ids = [str(x.get("id")) for x in acumulado if x.get("id") not in (None, "")]
        if not acumulado or total == 0:
            motivo = "Buydepa no devolvió ningún departamento"
        elif len(acumulado) != total:
            motivo = f"llegaron {len(acumulado)} de {total} departamentos"
        elif len(set(ids)) != len(ids):
            motivo = "Buydepa repitió departamentos entre páginas"
    return acumulado, motivo is None, motivo


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


def _fotos_de_etapa(etapa: Any) -> List[str]:
    """Fotos normales de una etapa: una lista (``legacy``) o ``{"standard": [...], "immersive": {...}}``. Las ``immersive`` (360°) se ignoran."""
    if isinstance(etapa, dict):
        etapa = etapa.get("standard")
    return _urls_imagen(etapa) if isinstance(etapa, list) else []


def _imagenes_buydepa(images: Any) -> List[str]:
    """Fotos a mostrar de un departamento. ``images`` llega en dos formas: un arreglo de URLs (se usa completo) o un objeto por etapa (``legacy``, ``pre-remodeling``,
    ``post-remodeling``, ``inspection``). Del objeto: las ``standard`` de 'post-remodeling' (ya remodelado) y, si no tiene, las de 'legacy'. Se descartan 'pre-remodeling' e
    'inspection' (son del estado anterior a la remodelación) y las ``immersive``."""
    if isinstance(images, list):
        return _urls_imagen(images)
    if isinstance(images, dict):
        return _fotos_de_etapa(images.get("post-remodeling")) or _fotos_de_etapa(images.get("legacy"))
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


async def _sincronizar_buydepa_stream(usuario_id: Optional[str] = None, origen: str = "manual"):
    """Generador SSE: trae los departamentos de buydepa y reporta avance ítem por ítem. Una sola sincronización a la vez (manual o programada): si ya hay una en curso, avisa y no hace nada."""
    if _candado.locked():
        logger.info("Buydepa | sincronización %s omitida: ya hay una en curso", origen)
        yield f"data: {json.dumps({'t': 'error', 'msg': 'Ya hay una sincronización de buydepa en curso. Espera a que termine.'})}\n\n"
        return
    await _candado.acquire()
    try:
        async for evento in _sincronizar_buydepa(usuario_id, origen):
            yield evento
    finally:
        _candado.release()


async def _sincronizar_buydepa(usuario_id: Optional[str], origen: str):
    marca_origen = {"motivo": f"Origen: {origen}"}
    try:
        items, integra, motivo_descarga = await _descargar_departamentos()
    except Exception as e:
        motivo = f"Error al consultar la API de buydepa: {e}"
        await _registrar_log_scraping("buydepa", usuario_id, 0, 0, 0, 1, [{"motivo": motivo}, marca_origen], "error")
        yield f"data: {json.dumps({'t': 'error', 'msg': motivo})}\n\n"
        return

    total = len(items)

    existentes = await _supabase_request("GET", "/Proyecto",
        params={"inmobiliaria_id": f"eq.{BUYDEPA_INMOBILIARIA_ID}",
                "fuente_externo_id": "not.is.null",
                "select": "id,nombre,codigo,imagen_url,imagenes,fuente_externo_id,activo"}) or []
    existentes_por_id = {e["fuente_externo_id"]: e for e in existentes}
    activos_al_inicio = sum(1 for e in existentes if e.get("activo"))
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

    # Departamentos que ya no aparecen en la respuesta de hoy → se dan de baja, solo si la descarga fue íntegra y la baja no es desproporcionada
    avisos: List[Dict[str, Any]] = []
    limite_bajas = _limite_bajas_pct()
    por_dar_de_baja = [(f, e) for f, e in existentes_por_id.items() if f not in ids_vistos_hoy and e.get("activo")]
    if por_dar_de_baja and not integra:
        avisos.append({"motivo": f"No se dio de baja ningún departamento: descarga incompleta ({motivo_descarga})"})
        por_dar_de_baja = []
    elif por_dar_de_baja and len(por_dar_de_baja) * 100 > limite_bajas * activos_al_inicio:       # justo en el límite sí se da de baja
        avisos.append({"motivo": f"Se frenó la baja de {len(por_dar_de_baja)} de {activos_al_inicio} activos ({_porcentaje(len(por_dar_de_baja), activos_al_inicio)} %, límite {limite_bajas} %). "
                                 "Revisa la fuente; si es legítimo, sube BUYDEPA_MAX_BAJAS_PCT y espera la próxima corrida."})
        por_dar_de_baja = []
    elif not integra:
        avisos.append({"motivo": f"Descarga incompleta ({motivo_descarga}): se actualizó lo que llegó y no se dio de baja nada"})
    for fuente_id, existente in por_dar_de_baja:
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

    estado_log = "ok" if not errores and not avisos and integra else "parcial"
    if not integra and not avisos:
        avisos.append({"motivo": f"Descarga incompleta ({motivo_descarga})"})
    await _registrar_log_scraping("buydepa", usuario_id, creados, actualizados, desactivados, len(errores), errores + avisos + [marca_origen], estado_log)
    yield f"data: {json.dumps({'t':'done','creados':creados,'actualizados':actualizados,'desactivados':desactivados,'errores':len(errores),'detalle_errores':errores + avisos})}\n\n"


# ── Corrida automática ───────────────────────────────────────────────────────

def _fecha(valor: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def _toca_correr(intervalo_h: float, ahora: Optional[datetime] = None) -> bool:
    """True si ya pasó el intervalo desde la última corrida de buydepa (manual o programada; ``ScrapingLog`` es la memoria compartida, así un reinicio no repite la corrida y otra
    instancia que ya corrió la hace innecesaria). Si la última terminó en error se reintenta a los 30 minutos."""
    ahora = ahora or datetime.now(timezone.utc)
    filas = await _supabase_request("GET", "/ScrapingLog", params={"fuente": "eq.buydepa", "select": "ejecutado_en,estado", "order": "ejecutado_en.desc", "limit": "1"}) or []
    ultima = _fecha(filas[0].get("ejecutado_en")) if filas else None
    if ultima is None:
        return True
    espera = REINTENTO_TRAS_ERROR_S if filas[0].get("estado") == "error" else intervalo_h * 3600
    return (ahora - ultima).total_seconds() >= min(espera, intervalo_h * 3600)


async def ciclo_buydepa(ahora: Optional[datetime] = None) -> str:
    """Un ciclo del bucle: ``"apagada"`` (intervalo 0), ``"no_toca"``, ``"ocupada"`` (ya hay una corrida en curso: se omite) o ``"corrio"``."""
    horas = get_settings().buydepa_sync_interval_hours
    if horas <= 0:
        return "apagada"
    if not await _toca_correr(horas, ahora):
        return "no_toca"
    if _candado.locked():
        logger.info("Buydepa | corrida programada omitida: ya hay una sincronización en curso")
        return "ocupada"
    async for _evento in _sincronizar_buydepa_stream(usuario_id=None, origen="programado"):
        pass
    return "corrio"


async def bucle_buydepa() -> None:
    """Cada ``BUYDEPA_SYNC_INTERVAL_HOURS`` sincroniza los seminuevos (0 = apagada). Mira cada 10 minutos si ya toca; si la base falla lo registra y espera cada vez más."""
    await asyncio.sleep(INICIO_BUCLE_S)
    fallos = 0
    while True:
        espera = REVISION_BUCLE_S
        try:
            await ciclo_buydepa()
            fallos = 0
        except Exception as e:
            fallos += 1
            espera = min(REVISION_BUCLE_S * 2 ** fallos, ESPERA_MAX_ERROR_BUCLE_S)
            logger.warning("Buydepa | no se pudo hacer el ciclo automático (falla %d, se reintenta en %d s): %s", fallos, espera, type(e).__name__)
        await asyncio.sleep(espera)
