"""Directorio de subsidios (Fase 1): lee las fichas publicadas de la base, las valida y las deja listas para la plantilla ``subsidio.html``.

Reglas que protegen a las familias de información sin respaldo (se aplican al **mostrar**, no solo al guardar):

* Una ficha ``publicado`` se muestra solo si **todos** sus datos están ``verificado`` y cada dato y cada bloque citan una fuente existente
  cuyo enlace es https y de un dominio oficial (gob.cl, bcn.cl, minvu.cl, cmfchile.cl, sii.cl). Si no, responde 404 (nunca se muestra un número sin respaldo).
* Si la revisión de la ficha o de algún dato tiene más de ``DIAS_REVISION`` días (o falta la fecha o el revisor), la ficha **se sigue mostrando**,
  con un aviso visible («pendiente de revisión: confirma los datos en la fuente oficial») y ``noindex``.
* Los equivalentes en pesos son aproximados, con la UF del día que informa el servicio (``app.services.uf``); sin ese valor no se muestran.

La lectura de la base se guarda 5 minutos; si la base falla se usa la última copia buena. Si las tablas aún no existen se trata como «no hay fichas».
"""
import logging
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from app.clients.supabase import _supabase_request
from app.core.cache_proyectos import _CACHE_TTL
from app.services import uf as servicio_uf
from app.services.seo import SITIO_URL

logger = logging.getLogger("wtsp_pita")

DIAS_REVISION = 90
DOMINIOS_OFICIALES = ("gob.cl", "bcn.cl", "minvu.cl", "cmfchile.cl", "sii.cl")
SLUG_VALIDO = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 90
GRUPOS = {"comprar": "Comprar vivienda", "construir": "Construir vivienda", "arrendar": "Arrendar", "mejorar": "Mejorar vivienda y barrio",
          "rural": "Programa rural", "valech": "Informe Valech", "otro": "Otros subsidios"}
POSTULACION = {"por_llamados": "Se postula en llamados del MINVU: confirma las fechas vigentes en la página oficial antes de postular.",
               "todo_el_ano": "Se postula durante todo el año.",
               "otra": "Consulta cómo se postula en las fuentes oficiales."}
POSTULACION_ETIQUETA = {"por_llamados": "Postulación por llamados", "todo_el_ano": "Postulación todo el año", "otra": "Cómo postular: ver la ficha"}
# Fichas que /subsidios necesita publicadas y al día para mostrar el índice generado en vez de la página fija (ya revisada). Con una sola que falte
# o esté vencida, la página no pierde ningún subsidio: sigue viéndose la fija.
NUCLEO = ("ds49-compra", "ds1-tramo-1", "ds1-tramo-2", "ds1-tramo-3", "ds19-integracion-social")
CAT_POR_DECRETO = {"DS 19": "cat-red", "DS 1": "cat-blue", "DS 49": "cat-purple"}     # el color de la tarjeta sigue al programa; otro decreto: ver CAT_RESPALDO
CAT_RESPALDO = ("cat-blue", "cat-purple", "cat-red")
# Columnas de la tabla comparativa: (título, claves de dato que alimentan la columna, la primera que exista gana). Una columna sin ningún dato no se muestra.
COLUMNAS_COMPARACION = (("Registro Social de Hogares", ("rsh_maximo",)), ("Ahorro mínimo", ("ahorro_minimo",)),
                        ("Precio máximo de la vivienda", ("precio_maximo",)), ("Monto del subsidio", ("subsidio_base", "subsidio_promedio")))
DATOS_EN_TARJETA = 4
TIPOS_FUENTE = {"ficha": "Ficha oficial", "pagina_llamado": "Página del llamado", "folleto": "Folleto oficial", "decreto": "Decreto", "resolucion": "Resolución", "otro": "Documento oficial"}
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

_SELECT_SUBSIDIO = "*,SubsidioBloque(*),SubsidioDato(*)"

_cache: Dict[str, Any] = {"fichas": None, "ts": 0.0, "bueno": None}


class DirectorioNoDisponible(Exception):
    """La base no responde y no hay ninguna copia buena guardada."""


# ── utilidades ───────────────────────────────────────────────────────────────

def fecha_larga(d: Optional[date]) -> Optional[str]:
    return f"{d.day} de {_MESES[d.month - 1]} de {d.year}" if d else None


def _a_fecha(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def es_fuente_oficial(url: Any) -> bool:
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == d or host.endswith("." + d) for d in DOMINIOS_OFICIALES)


def partes_de_texto(cuerpo: Any) -> List[Dict[str, Any]]:
    """Párrafos (separados por línea en blanco) y listas (líneas que empiezan con «- »). El texto sale tal cual: la plantilla lo escapa."""
    partes: List[Dict[str, Any]] = []
    parrafo: List[str] = []
    items: List[str] = []

    def cerrar():
        if parrafo:
            partes.append({"tipo": "p", "texto": " ".join(parrafo)}); parrafo.clear()
        if items:
            partes.append({"tipo": "ul", "items": list(items)}); items.clear()

    for linea in str(cuerpo or "").replace("\r", "").split("\n"):
        s = linea.strip()
        if not s:
            cerrar()
        elif s.startswith("- "):
            if parrafo:
                partes.append({"tipo": "p", "texto": " ".join(parrafo)}); parrafo.clear()
            items.append(s[2:].strip())
        else:
            if items:
                partes.append({"tipo": "ul", "items": list(items)}); items.clear()
            parrafo.append(s)
    cerrar()
    return partes


def _ids(v: Any) -> List[int]:
    return [int(x) for x in v if isinstance(x, (int, str)) and str(x).isdigit()] if isinstance(v, list) else []


# ── validación ───────────────────────────────────────────────────────────────

def validar(s: Dict[str, Any], fuentes: Dict[int, Dict[str, Any]], hoy: Optional[date] = None) -> Dict[str, List[str]]:
    """{"bloqueantes": [...], "avisos": [...]}. Con bloqueantes la ficha no se muestra; con avisos se muestra con la advertencia de revisión."""
    hoy = hoy or date.today()
    bloq: List[str] = []
    avisos: List[str] = []
    slug = str(s.get("slug") or "")
    if not slug or len(slug) > SLUG_MAX or not SLUG_VALIDO.match(slug):
        bloq.append("el nombre en la dirección (slug) no es válido")
    for campo in ("nombre_corto", "nombre_oficial", "resumen"):
        if not str(s.get(campo) or "").strip():
            bloq.append(f"falta «{campo}»")
    datos = [d for d in (s.get("SubsidioDato") or []) if isinstance(d, dict)]
    if s.get("alcance", "completa") == "completa" and not datos:
        bloq.append("una ficha completa necesita al menos un dato con fuente")

    def revisar_fuente(fid: Any, donde: str):
        f = fuentes.get(fid) if isinstance(fid, int) else None
        if f is None:
            bloq.append(f"{donde}: la fuente no existe")
        elif not es_fuente_oficial(f.get("url")):
            bloq.append(f"{donde}: la fuente no es un enlace https de un sitio oficial")

    for d in datos:
        nombre = f"dato «{d.get('clave')}»"
        if d.get("estado") != "verificado":
            bloq.append(f"{nombre} no está verificado")
        revisar_fuente(d.get("fuente_id"), nombre)
        if not _a_fecha(d.get("revisado_en")) or (hoy - _a_fecha(d.get("revisado_en"))).days > DIAS_REVISION:
            avisos.append(f"{nombre} sin revisión de los últimos {DIAS_REVISION} días")
    for fid in _ids(s.get("fuentes")):
        revisar_fuente(fid, "la ficha")
    for b in (s.get("SubsidioBloque") or []):
        for fid in _ids(b.get("fuentes")):
            revisar_fuente(fid, f"el bloque «{b.get('clave')}»")
    rev = _a_fecha(s.get("revisado_en"))
    if not rev or (hoy - rev).days > DIAS_REVISION:
        avisos.append(f"la ficha no tiene revisión de los últimos {DIAS_REVISION} días")
    if not str(s.get("revisado_por") or "").strip():
        avisos.append("la ficha no indica quién la revisó")
    return {"bloqueantes": bloq, "avisos": avisos}


# ── armado de la ficha para la plantilla ─────────────────────────────────────

def armar_ficha(s: Dict[str, Any], fuentes: Dict[int, Dict[str, Any]], problemas: Dict[str, List[str]], uf: Optional[Tuple[float, date]]) -> Dict[str, Any]:
    numeradas: Dict[int, int] = {}                       # id de Fuente → número que se ve en la página (por orden de aparición)

    def num(fid: int) -> Optional[int]:
        if fid not in fuentes:                           # en la vista previa puede haber una fuente aún inexistente: simplemente no se numera
            return None
        return numeradas.setdefault(fid, len(numeradas) + 1)

    for fid in _ids(s.get("fuentes")):
        num(fid)
    bloques = []
    for b in sorted([b for b in (s.get("SubsidioBloque") or []) if isinstance(b, dict)], key=lambda x: (x.get("orden") or 0, x.get("id") or 0)):
        bloques.append({"clave": b.get("clave"), "titulo": str(b.get("titulo") or ""), "partes": partes_de_texto(b.get("cuerpo")),
                        "fuentes": sorted({num(f) for f in _ids(b.get("fuentes")) if num(f)})})
    datos = []
    for d in sorted([d for d in (s.get("SubsidioDato") or []) if isinstance(d, dict)], key=lambda x: (x.get("orden") or 0, x.get("id") or 0)):
        pesos = None
        try:
            n = float(d["valor_numero"]) if d.get("valor_numero") is not None else None
        except (TypeError, ValueError):
            n = None
        if uf and d.get("unidad") == "UF" and n and n > 0:
            pesos = "≈ " + servicio_uf.formatear_pesos(n * uf[0])
        datos.append({"clave": str(d.get("clave") or ""), "etiqueta": str(d.get("etiqueta") or ""), "valor": str(d.get("valor_texto") or ""), "pesos": pesos, "llamado": d.get("llamado"),
                      "fuentes": [n for n in [num(d["fuente_id"])] if n] if isinstance(d.get("fuente_id"), int) else []})
    lista_fuentes = []
    for fid, n in sorted(numeradas.items(), key=lambda kv: kv[1]):
        f = fuentes[fid]
        lista_fuentes.append({"n": n, "titulo": str(f.get("titulo") or ""), "url": f.get("url"), "organismo": str(f.get("organismo") or ""),
                              "tipo": TIPOS_FUENTE.get(f.get("tipo"), "Documento oficial"), "leida": fecha_larga(_a_fecha(f.get("leida_en"))),
                              "publicada": fecha_larga(_a_fecha(f.get("publicada_en")))})
    rev = _a_fecha(s.get("revisado_en"))
    vencida = bool(problemas["avisos"])
    nombre, oficial, resumen = str(s["nombre_corto"]), str(s["nombre_oficial"]), str(s["resumen"])
    return {
        "slug": s["slug"], "ruta": f"/subsidios/{s['slug']}", "nombre": nombre, "nombre_oficial": oficial, "decreto": s.get("decreto"),
        "grupo": GRUPOS.get(s.get("grupo"), "Subsidios"), "grupo_clave": s.get("grupo") if s.get("grupo") in GRUPOS else "otro",
        "alcance": s.get("alcance") or "completa", "etiqueta_postulacion": POSTULACION_ETIQUETA.get(s.get("postulacion"), POSTULACION_ETIQUETA["otra"]), "resumen": resumen, "postulacion": POSTULACION.get(s.get("postulacion"), POSTULACION["otra"]),
        "bloques": bloques, "datos": datos, "fuentes": lista_fuentes,
        "revisado": fecha_larga(rev), "revisado_iso": rev.isoformat() if rev else None, "revisado_por": str(s.get("revisado_por") or "").strip() or None,
        "vencida": vencida, "avisos": problemas["avisos"],
        "uf": {"valor": f"{uf[0]:,.0f}".replace(",", "."), "fecha": fecha_larga(uf[1])} if uf and any(d["pesos"] for d in datos) else None,
        "titulo": f"{nombre}: {oficial} — QueSubsidio",
        "descripcion": resumen if len(resumen) <= 300 else resumen[:300].rsplit(" ", 1)[0].rstrip(",;: ") + "…",
        "robots": "noindex, follow" if vencida else "index, follow",
        "orden": s.get("orden") or 100,
    }


def json_ld(ficha: Dict[str, Any]) -> Dict[str, Any]:
    pagina = {"@type": "WebPage", "name": ficha["titulo"], "description": ficha["descripcion"], "url": f"{SITIO_URL}{ficha['ruta']}", "inLanguage": "es-CL"}
    if ficha["revisado_iso"]:
        pagina["dateModified"] = ficha["revisado_iso"]
    return {"@context": "https://schema.org", "@graph": [
        pagina,
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Inicio", "item": f"{SITIO_URL}/"},
            {"@type": "ListItem", "position": 2, "name": "Subsidios", "item": f"{SITIO_URL}/subsidios"},
            {"@type": "ListItem", "position": 3, "name": ficha["nombre"], "item": f"{SITIO_URL}{ficha['ruta']}"}]}]}


# ── lectura (con copia de 5 minutos y última copia buena) ────────────────────

async def _leer() -> Dict[str, Dict[str, Any]]:
    try:
        fuentes_filas = await _supabase_request("GET", "/Fuente", params={"select": "*", "limit": "2000"}) or []
        filas = await _supabase_request("GET", "/Subsidio", params={"estado": "eq.publicado", "select": _SELECT_SUBSIDIO,
                                                                    "order": "orden.asc,nombre_corto.asc", "limit": "500"}) or []
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:                      # las tablas todavía no existen: no hay fichas
            logger.info("Directorio: las tablas todavía no existen (sin fichas)")
            return {}
        raise
    fuentes = {int(f["id"]): f for f in fuentes_filas if isinstance(f, dict) and str(f.get("id", "")).isdigit()}
    uf = await servicio_uf.uf_del_dia() if any(d.get("unidad") == "UF" for s in filas for d in (s.get("SubsidioDato") or []) if isinstance(d, dict)) else None
    fichas: Dict[str, Dict[str, Any]] = {}
    for s in filas:
        if not isinstance(s, dict):
            continue
        problemas = validar(s, fuentes)
        if problemas["bloqueantes"]:
            logger.warning("Directorio: la ficha «%s» está publicada pero no se muestra: %s", str(s.get("slug"))[:60], "; ".join(problemas["bloqueantes"])[:400])
            continue
        fichas[s["slug"]] = armar_ficha(s, fuentes, problemas, uf)
    return fichas


async def fichas_publicas() -> Dict[str, Dict[str, Any]]:
    """slug → ficha lista para mostrar, solo las que pasan la validación. Lanza ``DirectorioNoDisponible`` si la base falla y no hay copia buena."""
    ahora = time.time()
    if _cache["fichas"] is not None and ahora - _cache["ts"] < _CACHE_TTL:
        return _cache["fichas"]
    try:
        fichas = await _leer()
    except Exception as e:
        logger.warning("Directorio: no se pudieron leer las fichas (%s)", type(e).__name__)
        if _cache["bueno"] is not None:
            return _cache["bueno"]
        raise DirectorioNoDisponible() from e
    _cache.update(fichas=fichas, ts=ahora, bueno=fichas)
    return fichas


def invalidar_cache() -> None:
    """Tras guardar algo en el editor: la próxima visita vuelve a leer la base (se conserva la última copia buena por si falla)."""
    _cache.update(fichas=None, ts=0.0)


async def obtener_subsidio(slug: str) -> Optional[Dict[str, Any]]:
    if len(slug) > SLUG_MAX or not SLUG_VALIDO.match(slug):
        return None
    return (await fichas_publicas()).get(slug)


async def slugs_de_subsidios() -> List[str]:
    """Para el sitemap: las fichas que se muestran y se pueden indexar (las que tienen la revisión vencida llevan noindex y no van)."""
    return sorted(s for s, f in (await fichas_publicas()).items() if not f["vencida"])


# ── índice y comparación de /subsidios ───────────────────────────────────────

def _comparacion(fichas: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Tabla comparativa de un grupo, hecha con los datos de sus fichas completas. None si hay menos de dos o ninguna columna con datos."""
    filas_base = [f for f in fichas if f["alcance"] == "completa" and f["datos"]]
    if len(filas_base) < 2:
        return None
    columnas, matriz = [], [[] for _ in filas_base]
    for titulo, claves in COLUMNAS_COMPARACION:
        celdas = []
        for f in filas_base:
            d = next((d for k in claves for d in f["datos"] if d["clave"] == k), None)
            celdas.append({"valor": d["valor"], "pesos": d["pesos"], "llamado": d["llamado"]} if d else None)
        if any(celdas):
            columnas.append(titulo)
            for fila, c in zip(matriz, celdas):
                fila.append(c)
    if not columnas:
        return None
    llamados = sorted({c["llamado"] for fila in matriz for c in fila if c and c["llamado"]})
    return {"columnas": columnas, "filas": [{"ficha": f, "celdas": celdas} for f, celdas in zip(filas_base, matriz)], "llamados": llamados}


async def indice_publico() -> Optional[Dict[str, Any]]:
    """Lo que necesita /subsidios para armar las tarjetas, el selector por grupo y la tabla comparativa desde las fichas publicadas, o None si no
    corresponde usarlo (falta o está vencida alguna ficha del NUCLEO): entonces la página muestra su versión fija, que tiene los mismos subsidios.
    Las fichas con la revisión vencida no entran al índice (siguen visibles en su propia página, con el aviso)."""
    vigentes = {slug: f for slug, f in (await fichas_publicas()).items() if not f["vencida"]}
    if any(slug not in vigentes for slug in NUCLEO):
        return None
    ordenadas = sorted(vigentes.values(), key=lambda f: (f["orden"], f["nombre"], f["slug"]))
    por_grupo: Dict[str, List[Dict[str, Any]]] = {}
    for i, f in enumerate(ordenadas):                    # copias: las fichas de la memoria rápida se comparten con las páginas de cada ficha
        tarjeta = {**f, "cat": CAT_POR_DECRETO.get(f["decreto"]) or CAT_RESPALDO[i % len(CAT_RESPALDO)],
                   "resumen_datos": f["datos"][:DATOS_EN_TARJETA] if f["alcance"] == "completa" else []}
        por_grupo.setdefault(f["grupo_clave"], []).append(tarjeta)
    grupos = [{"clave": c, "nombre": GRUPOS[c], "fichas": por_grupo[c], "comparacion": _comparacion(por_grupo[c])} for c in GRUPOS if c in por_grupo]
    uf = next((f["uf"] for f in ordenadas if f["uf"]), None)
    return {"grupos": grupos, "varios_grupos": len(grupos) > 1, "uf": uf}
