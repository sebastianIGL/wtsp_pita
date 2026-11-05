"""Guías y glosario (Fase 5): lee las guías y los términos publicados de la base, los valida y los deja listos para las plantillas.

Mismas reglas de protección que las fichas del directorio (``app/services/directorio.py``), que se aplican al **mostrar**:

* Una guía ``publicado`` se muestra solo si tiene título, resumen, texto y **al menos una fuente**, y todas sus fuentes existen y son un enlace https de un
  sitio oficial. Si no, responde 404 y deja un aviso en el log.
* Si la revisión de la guía tiene más de ``DIAS_REVISION`` días (o falta la fecha o el revisor), la guía se sigue mostrando con un aviso de «pendiente de
  revisión», con ``noindex``, y sale del sitemap.
* Un término del glosario se muestra solo si tiene definición y una fuente oficial que la respalde.

La lectura se guarda 5 minutos; si la base falla se usa la última copia buena. Si las tablas aún no existen se trata como «no hay guías».
"""
import logging
import re
import time
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional

import httpx

from app.clients.supabase import _supabase_request
from app.core.cache_proyectos import _CACHE_TTL
from app.services.directorio import DIAS_REVISION, SLUG_MAX, SLUG_VALIDO, TIPOS_FUENTE, _a_fecha, _ids, es_fuente_oficial, fecha_larga, partes_de_texto
from app.services.seo import SITIO_URL

logger = logging.getLogger("wtsp_pita")

_cache: Dict[str, Any] = {"datos": None, "ts": 0.0, "bueno": None}


class GuiasNoDisponibles(Exception):
    """La base no responde y no hay ninguna copia buena guardada."""


# ── validación ───────────────────────────────────────────────────────────────

def validar_guia(g: Dict[str, Any], fuentes: Dict[int, Dict[str, Any]], hoy: Optional[date] = None) -> Dict[str, List[str]]:
    """{"bloqueantes": [...], "avisos": [...]}. Con bloqueantes la guía no se muestra; con avisos se muestra con la advertencia de revisión."""
    hoy = hoy or date.today()
    bloq: List[str] = []
    avisos: List[str] = []
    slug = str(g.get("slug") or "")
    if not slug or len(slug) > SLUG_MAX or not SLUG_VALIDO.match(slug):
        bloq.append("el nombre en la dirección (slug) no es válido")
    for campo in ("titulo", "resumen", "cuerpo"):
        if not str(g.get(campo) or "").strip():
            bloq.append(f"falta «{campo}»")
    ids = _ids(g.get("fuentes"))
    if not ids:
        bloq.append("una guía necesita al menos una fuente oficial")
    for fid in ids:
        f = fuentes.get(fid)
        if f is None:
            bloq.append("la fuente no existe")
        elif not es_fuente_oficial(f.get("url")):
            bloq.append("la fuente no es un enlace https de un sitio oficial")
    rev = _a_fecha(g.get("revisado_en"))
    if not rev or (hoy - rev).days > DIAS_REVISION:
        avisos.append(f"la guía no tiene revisión de los últimos {DIAS_REVISION} días")
    if not str(g.get("revisado_por") or "").strip():
        avisos.append("la guía no indica quién la revisó")
    return {"bloqueantes": bloq, "avisos": avisos}


def validar_termino(t: Dict[str, Any], fuentes: Dict[int, Dict[str, Any]]) -> List[str]:
    """Lista de problemas graves (vacía = se puede mostrar)."""
    bloq: List[str] = []
    if not str(t.get("termino") or "").strip():
        bloq.append("falta el término")
    if not str(t.get("definicion") or "").strip():
        bloq.append("falta la definición")
    fid = t.get("fuente_id")
    f = fuentes.get(fid) if isinstance(fid, int) and not isinstance(fid, bool) else None
    if f is None:
        bloq.append("el término necesita una fuente que exista")
    elif not es_fuente_oficial(f.get("url")):
        bloq.append("la fuente no es un enlace https de un sitio oficial")
    return bloq


# ── armado para las plantillas ───────────────────────────────────────────────

def _sin_tildes(txt: Any) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(txt or "")) if unicodedata.category(c) != "Mn")


def clave_orden(termino: Any) -> str:
    return _sin_tildes(termino).casefold().strip()


def ancla(termino: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", clave_orden(termino)).strip("-")[:60] or "termino"


def agrupar_por_letra(terminos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Los términos (ya ordenados) agrupados por su letra inicial sin tilde; los que no empiezan con letra van al final bajo «#»."""
    grupos: Dict[str, List[Dict[str, Any]]] = {}
    for t in terminos:
        letra = clave_orden(t["termino"])[:1].upper()
        grupos.setdefault(letra if letra.isalpha() else "#", []).append(t)
    return [{"letra": l, "id": "num" if l == "#" else l.lower(), "terminos": grupos[l]} for l in sorted(grupos, key=lambda x: (x == "#", x))]


def _fuente_visible(f: Dict[str, Any]) -> Dict[str, Any]:
    return {"titulo": str(f.get("titulo") or ""), "url": f.get("url"), "organismo": str(f.get("organismo") or ""),
            "tipo": TIPOS_FUENTE.get(f.get("tipo"), "Documento oficial"), "leida": fecha_larga(_a_fecha(f.get("leida_en")))}


def armar_guia(g: Dict[str, Any], fuentes: Dict[int, Dict[str, Any]], problemas: Dict[str, List[str]]) -> Dict[str, Any]:
    lista = [dict(_fuente_visible(fuentes[i]), n=n) for n, i in enumerate((i for i in _ids(g.get("fuentes")) if i in fuentes), start=1)]
    rev = _a_fecha(g.get("revisado_en"))
    vencida = bool(problemas["avisos"])
    titulo, resumen = str(g["titulo"]).strip(), str(g["resumen"]).strip()
    return {
        "slug": g["slug"], "ruta": f"/guias/{g['slug']}", "titulo": titulo, "resumen": resumen, "partes": partes_de_texto(g.get("cuerpo")), "fuentes": lista,
        "revisado": fecha_larga(rev), "revisado_iso": rev.isoformat() if rev else None, "revisado_por": str(g.get("revisado_por") or "").strip() or None,
        "vencida": vencida, "avisos": problemas["avisos"], "orden": g.get("orden") or 100,
        "titulo_pagina": f"{titulo} — QueSubsidio",
        "descripcion": resumen if len(resumen) <= 300 else resumen[:300].rsplit(" ", 1)[0].rstrip(",;: ") + "…",
        "robots": "noindex, follow" if vencida else "index, follow",
    }


def armar_termino(t: Dict[str, Any], fuentes: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    f = fuentes.get(t.get("fuente_id")) or {}
    return {"termino": str(t["termino"]).strip(), "definicion": str(t["definicion"]).strip(), "ancla": ancla(t["termino"]), "fuente": _fuente_visible(f), "orden": t.get("orden") or 100}


def json_ld_guia(g: Dict[str, Any]) -> Dict[str, Any]:
    pagina = {"@type": "WebPage", "name": g["titulo_pagina"], "description": g["descripcion"], "url": f"{SITIO_URL}{g['ruta']}", "inLanguage": "es-CL"}
    if g["revisado_iso"]:
        pagina["dateModified"] = g["revisado_iso"]
    return {"@context": "https://schema.org", "@graph": [pagina, {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Inicio", "item": f"{SITIO_URL}/"},
        {"@type": "ListItem", "position": 2, "name": "Guías", "item": f"{SITIO_URL}/guias"},
        {"@type": "ListItem", "position": 3, "name": g["titulo"], "item": f"{SITIO_URL}{g['ruta']}"}]}]}


def json_ld_glosario(terminos: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"@context": "https://schema.org", "@type": "DefinedTermSet", "name": "Glosario de subsidios habitacionales", "url": f"{SITIO_URL}/glosario", "inLanguage": "es-CL",
            "hasDefinedTerm": [{"@type": "DefinedTerm", "name": t["termino"], "description": t["definicion"], "url": f"{SITIO_URL}/glosario#{t['ancla']}"} for t in terminos]}


# ── lectura (con copia de 5 minutos y última copia buena) ────────────────────

async def _leer() -> Dict[str, Any]:
    try:
        fuentes_filas = await _supabase_request("GET", "/Fuente", params={"select": "*", "limit": "2000"}) or []
        guias_filas = await _supabase_request("GET", "/Guia", params={"estado": "eq.publicado", "select": "*", "order": "orden.asc,titulo.asc", "limit": "500"}) or []
        terminos_filas = await _supabase_request("GET", "/GlosarioTermino", params={"estado": "eq.publicado", "select": "*", "limit": "2000"}) or []
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:                      # las tablas todavía no existen: no hay guías
            logger.info("Guías: las tablas todavía no existen (sin guías)")
            return {"guias": {}, "terminos": []}
        raise
    fuentes = {int(f["id"]): f for f in fuentes_filas if isinstance(f, dict) and str(f.get("id", "")).isdigit()}
    guias: Dict[str, Dict[str, Any]] = {}
    for g in guias_filas:
        if not isinstance(g, dict):
            continue
        problemas = validar_guia(g, fuentes)
        if problemas["bloqueantes"]:
            logger.warning("Guías: la guía «%s» está publicada pero no se muestra: %s", str(g.get("slug"))[:60], "; ".join(problemas["bloqueantes"])[:400])
            continue
        guias[g["slug"]] = armar_guia(g, fuentes, problemas)
    terminos: List[Dict[str, Any]] = []
    vistos = set()
    for t in terminos_filas:
        if not isinstance(t, dict):
            continue
        problemas = validar_termino(t, fuentes)
        if problemas:
            logger.warning("Glosario: el término «%s» está publicado pero no se muestra: %s", str(t.get("termino"))[:60], "; ".join(problemas)[:300])
            continue
        armado = armar_termino(t, fuentes)
        if armado["ancla"] in vistos:                           # dos términos que dan la misma ancla: se muestra el primero
            continue
        vistos.add(armado["ancla"])
        terminos.append(armado)
    terminos.sort(key=lambda x: clave_orden(x["termino"]))
    return {"guias": guias, "terminos": terminos}


async def _datos() -> Dict[str, Any]:
    ahora = time.time()
    if _cache["datos"] is not None and ahora - _cache["ts"] < _CACHE_TTL:
        return _cache["datos"]
    try:
        datos = await _leer()
    except Exception as e:
        logger.warning("Guías: no se pudieron leer (%s)", type(e).__name__)
        if _cache["bueno"] is not None:
            return _cache["bueno"]
        raise GuiasNoDisponibles() from e
    _cache.update(datos=datos, ts=ahora, bueno=datos)
    return datos


def invalidar_cache() -> None:
    """Tras guardar algo en el editor: la próxima visita vuelve a leer la base (se conserva la última copia buena por si falla)."""
    _cache.update(datos=None, ts=0.0)


async def guias_publicas() -> List[Dict[str, Any]]:
    """Las guías que se muestran, en el orden del editor."""
    return sorted((await _datos())["guias"].values(), key=lambda g: (g["orden"], g["titulo"]))


async def obtener_guia(slug: str) -> Optional[Dict[str, Any]]:
    if len(slug) > SLUG_MAX or not SLUG_VALIDO.match(slug):
        return None
    return (await _datos())["guias"].get(slug)


async def terminos_publicos() -> List[Dict[str, Any]]:
    return list((await _datos())["terminos"])


async def slugs_de_guias() -> List[str]:
    """Para el sitemap: las guías que se muestran y se pueden indexar (las vencidas llevan noindex y no van)."""
    return sorted(s for s, g in (await _datos())["guias"].items() if not g["vencida"])
