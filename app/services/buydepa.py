"""Scraping de buydepa: ayudantes que crean, actualizan y dan de baja los departamentos como proyectos (precio, tipología,
stock, imágenes, ofertas, log de cambios y log de ejecuciones).

Movido sin cambios desde main.py (Fase 6, tramo 2, lote B). El flujo completo (``_sincronizar_buydepa_stream``) se mueve a
este mismo módulo en el commit siguiente.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from app.clients.supabase import _supabase_request

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
