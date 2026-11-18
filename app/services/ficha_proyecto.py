"""Ficha pública de un proyecto (``/viviendas/{slug}``): el slug, qué datos se muestran y cómo se presentan.

El slug (la dirección de la ficha) se guarda en ``Proyecto.slug`` la primera vez que el proyecto sale en el sitio y desde entonces NO cambia,
aunque se renombre el proyecto o aparezca otro con el mismo nombre: así los enlaces ya compartidos (WhatsApp, Google, el bot) siguen
funcionando. La primera vez se calcula desde el nombre (igual que antes, de modo que las direcciones ya publicadas no cambian). Nuevos:
``nombre`` (p. ej. ``mirador-del-sol``). Semi-nuevos (una propiedad por publicación, con la dirección como nombre): ``nombre`` + un
identificador corto (el de la fuente, o el del proyecto). Si dos proyectos dan el mismo slug, ambos reciben un sufijo con el comienzo de
su id. Si la base aún no tiene la columna ``slug`` (falta correr el SQL), todo funciona como antes: el slug se calcula desde el nombre.

Solo se muestran proyectos activos. De cada tipología solo las que están activas y tienen stock; del stock nunca el número
exacto, solo «Disponible» o «Últimas unidades». Todo valor es referencial (aviso legal en la página).
"""
import asyncio
import logging
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.clients.supabase import _supabase_request
from app.core import interruptores
from app.core.cache_proyectos import _CACHE_TTL

logger = logging.getLogger("wtsp_pita")

SLUG_VALIDO = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 90
ULTIMAS_UNIDADES = 3          # hasta este stock total se dice «Últimas unidades»
ORIGENES = ("viviendas", "bot")

_RELACION_DUENO = "Inmobiliaria(nombre,Empresa(nombre))"      # para saber a qué inmobiliaria y empresa pertenece (ver oculto_en_sitio)
_SELECT_INDICE = f"id,nombre,slug,activo,condicion_propiedad,fuente_externo_id,{_RELACION_DUENO}"
_SELECT_INDICE_SIN_SLUG = f"id,nombre,activo,condicion_propiedad,fuente_externo_id,{_RELACION_DUENO}"      # si la base aún no tiene la columna slug
MAX_SLUGS_POR_VEZ = 100          # cuántos slugs nuevos se guardan en cada lectura del índice (el resto, en la siguiente)
_SELECT_FICHA = (
    "id,nombre,ubicacion,imagen_url,imagenes,activo,condicion_propiedad,fuente_externo_id,"
    "acepta_ds19,acepta_ds1_t23,ahorro_minimo_uf,valor_reserva_clp,valor_reserva_uf,tiene_piloto,"
    f"valor_estacionamiento_uf,estacionamiento_obligatorio,{_RELACION_DUENO},"
    "Tipologia(id,nombre,dormitorios,banos,superficie_util_m2,valor_uf,monto_subsidio,tipo_subsidio,estado,"
    "EtapaTipologia(stock,Etapa(nombre,estado,fecha_entrega,tipo_ahorro,num_cuotas)))"
)

_indice: Dict[str, Any] = {"fichas": None, "ts": 0.0, "por_id": {}}   # fichas: slug → {"id", "nuevo"} de cada proyecto público; por_id: id → slug; 5 min
_fichas: Dict[str, Tuple[float, Dict[str, Any]]] = {}              # id → (hora, fila de la base), 5 min
_MAX_FICHAS = 300

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ── proyectos que el dueño ocultó del sitio público ──────────────────────────

def _sin_tildes(texto: Any) -> str:
    return unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii").lower().strip()


def oculto_en_sitio(p: Dict[str, Any]) -> bool:
    """True si el nombre de la inmobiliaria o de la empresa del proyecto contiene alguno de interruptores.PROYECTOS_OCULTOS_DEL_SITIO."""
    textos = [_sin_tildes(t) for t in interruptores.PROYECTOS_OCULTOS_DEL_SITIO if _sin_tildes(t)]
    if not textos:
        return False
    inmobiliaria = p.get("Inmobiliaria") if isinstance(p.get("Inmobiliaria"), dict) else {}
    empresa = inmobiliaria.get("Empresa") if isinstance(inmobiliaria.get("Empresa"), dict) else {}
    nombres = [_sin_tildes(inmobiliaria.get("nombre")), _sin_tildes(empresa.get("nombre"))]
    return any(t in n for t in textos for n in nombres if n)


def es_publico(p: Dict[str, Any]) -> bool:
    """Un proyecto sale en el sitio público si tiene nombre, no está desactivado y no está entre los ocultos."""
    return bool(p.get("nombre")) and p.get("activo") is not False and not oculto_en_sitio(p)


# ── slug ─────────────────────────────────────────────────────────────────────

def slugify(texto: Any, maximo: int = 70) -> str:
    """Minúsculas, sin tildes ni símbolos, palabras unidas por guiones («Ñuñoa 2D» → «nunoa-2d»)."""
    ascii_ = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")[:maximo].strip("-")


def _id_corto(p: Dict[str, Any], largo: int) -> str:
    return str(p.get("id") or "").replace("-", "")[:largo]


def _slug_base(p: Dict[str, Any]) -> str:
    base = slugify(p.get("nombre")) or "proyecto"
    if (p.get("condicion_propiedad") or "nueva") != "nueva":              # semi-nuevo: la dirección no es única
        corto = slugify(p.get("fuente_externo_id"))[-8:].strip("-") or _id_corto(p, 8)
        return f"{base}-{corto}"[:SLUG_MAX]
    return base


def _slug_guardado(p: Dict[str, Any]) -> Optional[str]:
    g = p.get("slug")
    return g if isinstance(g, str) and len(g) <= SLUG_MAX and SLUG_VALIDO.match(g) else None


def asignar_slugs(proyectos: List[Dict[str, Any]]) -> Dict[str, str]:
    """id → slug de los proyectos públicos (con nombre y no desactivados). Un proyecto que ya tiene su slug guardado (``slug``) lo conserva
    siempre; los demás lo reciben calculado desde el nombre. Si dos calculados dan lo mismo, o uno coincide con un slug guardado (de cualquier
    proyecto, también de uno desactivado), los calculados llevan el comienzo de su id al final. El resultado no depende del orden de la lista."""
    reservados = {g for g in (_slug_guardado(p) for p in proyectos if p.get("id")) if g}
    slugs: Dict[str, str] = {}
    bases: Dict[str, List[Dict[str, Any]]] = {}
    for p in proyectos:
        if not es_publico(p) or not p.get("id"):
            continue
        guardado = _slug_guardado(p)
        if guardado:
            slugs[str(p["id"])] = guardado
        else:
            bases.setdefault(_slug_base(p), []).append(p)
    for base, grupo in bases.items():
        for p in grupo:
            slugs[str(p["id"])] = base if len(grupo) == 1 and base not in reservados else f"{base[:SLUG_MAX - 7]}-{_id_corto(p, 6)}"
    return slugs


async def _guardar_slugs(filas: List[Dict[str, Any]], slugs: Dict[str, str]) -> None:
    """Guarda el slug de los proyectos públicos que aún no lo tienen (solo si la base ya tiene la columna). Nunca pisa uno ya guardado
    (``slug=is.null``) y nunca rompe la lectura: si falla (p. ej. dos iguales), se reintenta en la próxima lectura del índice."""
    pendientes = [(str(p["id"]), slugs[str(p["id"])]) for p in filas if "slug" in p and not p.get("slug") and str(p.get("id")) in slugs][:MAX_SLUGS_POR_VEZ]

    async def uno(pid: str, slug: str) -> None:
        try:
            await _supabase_request("PATCH", "/Proyecto", params={"id": f"eq.{pid}", "slug": "is.null"}, json={"slug": slug})
        except Exception as e:
            logger.warning("Ficha de proyecto | no se pudo guardar el slug de un proyecto (%s)", type(e).__name__)
    for i in range(0, len(pendientes), 10):
        await asyncio.gather(*(uno(pid, slug) for pid, slug in pendientes[i:i + 10]))


# ── formato (es-CL) ──────────────────────────────────────────────────────────

def _num(n: float, decimales: int = 0) -> str:
    texto = f"{n:,.{decimales}f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return texto.rstrip("0").rstrip(",") if decimales and "," in texto else texto


def _a_numero(v: Any) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def fmt_uf(v: Any) -> Optional[str]:
    n = _a_numero(v)
    if n is None or n <= 0:
        return None
    return f"{_num(n, 0 if n == int(n) else 1)} UF"


def fmt_clp(v: Any) -> Optional[str]:
    n = _a_numero(v)
    return f"${_num(n)}" if n is not None and n > 0 else None


def _entrega(etapa: Dict[str, Any]) -> Optional[str]:
    if etapa.get("estado") == "entrega_inmediata":
        return "Entrega inmediata"
    fecha = str(etapa.get("fecha_entrega") or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-\d{2})?", fecha)
    if m and 1 <= int(m.group(2)) <= 12:
        fecha = f"{_MESES[int(m.group(2)) - 1]} {m.group(1)}"
    if fecha:
        return f"Entrega {fecha}"
    return "Entrega futura" if etapa.get("estado") == "entrega_futura" else None


def _imagenes(p: Dict[str, Any]) -> List[str]:
    """Solo URLs http(s): lo que venga de la base no puede colar otro tipo de enlace en la página."""
    urls = [u for u in (p.get("imagenes") or []) if isinstance(u, str)]
    if isinstance(p.get("imagen_url"), str) and p["imagen_url"] not in urls:
        urls.insert(0, p["imagen_url"])
    return [u for u in urls if u.startswith(("https://", "http://"))]


def _recortar(texto: str, largo: int) -> str:
    if len(texto) <= largo:
        return texto
    return texto[:largo].rsplit(" ", 1)[0].rstrip(",;: ") + "…"


# ── los datos de la página ───────────────────────────────────────────────────

def armar_ficha(p: Dict[str, Any], slug: str) -> Dict[str, Any]:
    nombre = str(p.get("nombre") or "Proyecto")
    ubicacion = str(p.get("ubicacion") or "")
    nuevo = (p.get("condicion_propiedad") or "nueva") == "nueva"

    tipologias: List[Dict[str, Any]] = []
    entregas: List[str] = []
    for t in p.get("Tipologia") or []:
        if (t.get("estado") or "activo") != "activo":
            continue
        con_stock = [e for e in (t.get("EtapaTipologia") or []) if (_a_numero(e.get("stock")) or 0) > 0]
        stock = sum(int(_a_numero(e["stock"]) or 0) for e in con_stock)
        if stock <= 0:
            continue
        propias = [x for x in dict.fromkeys(_entrega(e.get("Etapa") or {}) for e in con_stock) if x]
        entregas += [x for x in propias if x not in entregas]
        valor = _a_numero(t.get("valor_uf"))
        m2 = _a_numero(t.get("superficie_util_m2"))
        tipologias.append({
            "nombre": str(t.get("nombre") or "Tipología"),
            "dormitorios": t.get("dormitorios"), "banos": t.get("banos"),
            "m2": f"{_num(m2, 1)} m²" if m2 else None,
            "valor_uf": valor if valor and valor > 0 else None,
            "valor": fmt_uf(valor),
            "ultimas": stock <= ULTIMAS_UNIDADES,
            "disponibilidad": "Últimas unidades" if stock <= ULTIMAS_UNIDADES else "Disponible",
            "entregas": propias,
        })
    tipologias.sort(key=lambda t: (t["valor_uf"] is None, t["valor_uf"] or 0, t["nombre"]))
    desde = fmt_uf(min((t["valor_uf"] for t in tipologias if t["valor_uf"]), default=None))

    subsidios = (["DS19"] if p.get("acepta_ds19") else []) + (["DS1 T2/T3"] if p.get("acepta_ds1_t23") else [])
    reserva = fmt_clp(p.get("valor_reserva_clp")) or fmt_uf(p.get("valor_reserva_uf"))
    estac = fmt_uf(p.get("valor_estacionamiento_uf"))
    pago = [(k, v) for k, v in (
        ("Ahorro mínimo", fmt_uf(p.get("ahorro_minimo_uf"))),
        ("Reserva", reserva),
        ("Estacionamiento", f"{estac} ({'obligatorio' if p.get('estacionamiento_obligatorio') else 'opcional'})" if estac else None),
        ("Sala piloto", "Sí" if p.get("tiene_piloto") else None),
    ) if v]

    en = f" en {ubicacion}" if ubicacion and ubicacion.lower() != nombre.lower() else ""
    resumen = (f"{nombre}: {'proyecto nuevo' if nuevo else 'vivienda semi-nueva'}{en}"
               + (f", desde {desde}" if desde else "")
               + (f", con subsidio {' y '.join(subsidios)}" if subsidios else "")
               + ". Evalúa tu caso gratis con QueSubsidio.")
    imagenes = _imagenes(p)
    return {
        "slug": slug, "nombre": nombre, "ubicacion": ubicacion, "nuevo": nuevo,
        "inmobiliaria": str((p.get("Inmobiliaria") or {}).get("nombre") or ""),
        "imagenes": imagenes, "og_image": imagenes[0] if imagenes else None,
        "subsidios": subsidios, "entregas": entregas, "tipologias": tipologias, "desde": desde, "pago": pago,
        "titulo": f"{nombre}{en} — QueSubsidio",
        "descripcion": _recortar(resumen, 300),
        "ruta": f"/viviendas/{slug}",
    }


# ── lectura (con caché de 5 minutos) ─────────────────────────────────────────

async def _leer_proyectos_del_indice() -> List[Dict[str, Any]]:
    params = {"select": _SELECT_INDICE, "order": "nombre.asc", "limit": "1000"}
    try:
        return await _supabase_request("GET", "/Proyecto", params=params) or []
    except httpx.HTTPStatusError as e:
        if e.response is None or e.response.status_code not in (400, 404):
            raise
        logger.warning("Ficha de proyecto | la base aún no tiene la columna slug; se calcula desde el nombre")
        return await _supabase_request("GET", "/Proyecto", params={**params, "select": _SELECT_INDICE_SIN_SLUG}) or []


async def _indice_fichas() -> Dict[str, Dict[str, Any]]:
    ahora = time.time()
    if _indice["fichas"] is None or ahora - _indice["ts"] >= _CACHE_TTL:
        filas = await _leer_proyectos_del_indice()
        nuevo = {str(p.get("id")): (p.get("condicion_propiedad") or "nueva") == "nueva" for p in filas}
        por_id = asignar_slugs(filas)
        await _guardar_slugs(filas, por_id)
        _indice["fichas"] = {slug: {"id": pid, "nuevo": nuevo[pid]} for pid, slug in por_id.items()}
        _indice["por_id"] = por_id
        _indice["ts"] = ahora
    return _indice["fichas"]


async def slugs_por_id() -> Dict[str, str]:
    """id → slug de cada proyecto público (el mismo que abre la ficha), con la caché de 5 minutos del índice."""
    await _indice_fichas()
    return dict(_indice["por_id"])


async def slugs_de_proyectos_nuevos() -> List[str]:
    """Slug de la ficha de cada proyecto nuevo activo, ordenados (para el sitemap). Los semi-nuevos no se listan: su ficha lleva noindex."""
    return sorted(slug for slug, e in (await _indice_fichas()).items() if e["nuevo"])


async def _fila(proyecto_id: str) -> Optional[Dict[str, Any]]:
    ahora = time.time()
    guardada = _fichas.get(proyecto_id)
    if guardada and ahora - guardada[0] < _CACHE_TTL:
        return guardada[1]
    filas = await _supabase_request("GET", "/Proyecto", params={"id": f"eq.{proyecto_id}", "select": _SELECT_FICHA, "limit": "1"}) or []
    if not filas:
        return None
    if len(_fichas) >= _MAX_FICHAS:
        _fichas.clear()
    _fichas[proyecto_id] = (ahora, filas[0])
    return filas[0]


async def obtener_ficha(slug: str) -> Optional[Dict[str, Any]]:
    """Los datos de la página del proyecto, o None si el slug no existe, el proyecto no está activo o está entre los ocultos del sitio público."""
    if len(slug) > SLUG_MAX or not SLUG_VALIDO.match(slug):
        return None
    entrada = (await _indice_fichas()).get(slug)
    if not entrada:
        return None
    proyecto_id = entrada["id"]
    fila = await _fila(proyecto_id)
    if not fila or not es_publico(fila):
        return None
    return armar_ficha(fila, slug)
