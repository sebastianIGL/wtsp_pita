"""Editor del directorio de subsidios (Fase 2): las operaciones que usa la pestaña «Subsidios» del panel de administración (solo el dueño).

Reglas del editor (se suman a las de ``directorio.validar``, que además se aplican al mostrar):
  * Una ficha **nace en borrador** y solo se publica si ``validar`` no da ni problemas graves ni avisos: todos sus datos verificados con una fuente oficial
    y la ficha y sus datos revisados en los últimos 90 días.
  * **Cambiar un dato (valor, cifra, unidad, llamado o fuente) lo devuelve a «por confirmar»**: hay que volver a verificarlo. Así una cifra editada nunca queda
    marcada como verificada sin que alguien la haya releído en la fuente.
  * Verificar un dato y marcar la ficha como revisada registran quién y cuándo (la persona autenticada, no un texto libre).
  * Las fuentes solo se crean con un enlace https de un sitio oficial; su enlace no se edita (se crea otra), para que nadie cambie en silencio lo que respalda un dato.
  * La dirección (slug) de una ficha publicada no se cambia (rompería los enlaces); una ficha publicada no se borra.
Cada escritura invalida la copia de lectura pública, para que el cambio se vea en la siguiente visita.
"""
import math
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.clients.supabase import _supabase_request
from app.core.errors import AppError
from app.services import directorio as dr
from app.services import uf as servicio_uf

CLAVES_BLOQUE = ("para_quien", "que_entrega", "antes_de_postular", "como_se_postula", "despues_de_ganarlo", "compatibilidades", "errores_frecuentes", "otro")
UNIDADES = ("UF", "CLP", "porcentaje", "meses", "anios", "texto")
ALCANCES = ("completa", "ligera")
ESTADOS = ("borrador", "publicado", "archivado")
CAMPOS_QUE_REABREN_UN_DATO = ("valor_texto", "valor_numero", "unidad", "llamado", "fuente_id")
CLAVE_DATO = re.compile(r"^[a-z0-9_]{2,40}$")


# ── validación de entradas ───────────────────────────────────────────────────

def _error(mensaje: str, estado: int = 422) -> AppError:
    return AppError(mensaje, estado)


def _texto(body: Dict[str, Any], campo: str, maximo: int, etiqueta: str, obligatorio: bool = True) -> Optional[str]:
    v = body.get(campo)
    if v is None or (isinstance(v, str) and not v.strip()):
        if obligatorio:
            raise _error(f"Falta «{etiqueta}».")
        return None
    if not isinstance(v, str):
        raise _error(f"«{etiqueta}» debe ser texto.")
    v = v.strip()
    if len(v) > maximo:
        raise _error(f"«{etiqueta}» es demasiado largo (máximo {maximo} caracteres).")
    return v


def _opcion(body: Dict[str, Any], campo: str, validas, etiqueta: str) -> str:
    v = body.get(campo)
    if v not in validas:
        raise _error(f"«{etiqueta}» no es válido. Opciones: {', '.join(validas)}.")
    return v


def _entero(body: Dict[str, Any], campo: str, etiqueta: str, minimo: int = 0, maximo: int = 10000, defecto: Optional[int] = None) -> int:
    v = body.get(campo, defecto)
    if v is None:
        raise _error(f"Falta «{etiqueta}».")
    if isinstance(v, bool) or not isinstance(v, int) or not minimo <= v <= maximo:
        raise _error(f"«{etiqueta}» debe ser un número entero entre {minimo} y {maximo}.")
    return v


def _numero_o_nulo(body: Dict[str, Any], campo: str, etiqueta: str) -> Optional[float]:
    v = body.get(campo)
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e12:
        raise _error(f"«{etiqueta}» debe ser un número.")
    return v


def _fecha_o_nula(body: Dict[str, Any], campo: str, etiqueta: str) -> Optional[str]:
    v = body.get(campo)
    if v is None or v == "":
        return None
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        raise _error(f"«{etiqueta}» debe ser una fecha (AAAA-MM-DD).")
    if d > date.today():
        raise _error(f"«{etiqueta}» no puede ser una fecha futura.")
    return d.isoformat()


def _slug(body: Dict[str, Any], campo: str, etiqueta: str) -> str:
    v = _texto(body, campo, dr.SLUG_MAX, etiqueta)
    if not dr.SLUG_VALIDO.match(v):
        raise _error(f"«{etiqueta}» solo admite minúsculas, números y guiones (por ejemplo ds49-compra).")
    return v


# ── lectura ──────────────────────────────────────────────────────────────────

async def _filas(tabla: str, params: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    p = {"select": "*", "limit": "5000"}
    p.update(params or {})
    return await _supabase_request("GET", f"/{tabla}", params=p) or []


async def _fuentes_por_id() -> Dict[int, Dict[str, Any]]:
    return {int(f["id"]): f for f in await _filas("Fuente", {"order": "id.asc"}) if str(f.get("id", "")).isdigit()}


def _ensamblar(s: Dict[str, Any], bloques: List[Dict[str, Any]], datos: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {**s, "SubsidioBloque": [b for b in bloques if b.get("subsidio_id") == s["id"]], "SubsidioDato": [d for d in datos if d.get("subsidio_id") == s["id"]]}


async def listar() -> List[Dict[str, Any]]:
    subs = await _filas("Subsidio", {"order": "orden.asc,nombre_corto.asc"})
    bloques, datos, fuentes = await _filas("SubsidioBloque"), await _filas("SubsidioDato"), await _fuentes_por_id()
    salida = []
    for s in subs:
        completo = _ensamblar(s, bloques, datos)
        p = dr.validar(completo, fuentes)
        salida.append({k: s.get(k) for k in ("id", "slug", "nombre_corto", "nombre_oficial", "grupo", "alcance", "estado", "orden", "revisado_en", "revisado_por", "publicado_en")}
                      | {"bloqueantes": p["bloqueantes"], "avisos": p["avisos"], "n_datos": len(completo["SubsidioDato"]),
                         "n_verificados": sum(1 for d in completo["SubsidioDato"] if d.get("estado") == "verificado")})
    return salida


async def _subsidio(sid: int) -> Dict[str, Any]:
    filas = await _filas("Subsidio", {"id": f"eq.{sid}"})
    if not filas:
        raise _error("No existe esa ficha.", 404)
    return filas[0]


async def obtener(sid: int) -> Dict[str, Any]:
    s = await _subsidio(sid)
    bloques = sorted(await _filas("SubsidioBloque", {"subsidio_id": f"eq.{sid}"}), key=lambda b: (b.get("orden") or 0, b.get("id") or 0))
    datos = sorted(await _filas("SubsidioDato", {"subsidio_id": f"eq.{sid}"}), key=lambda d: (d.get("orden") or 0, d.get("id") or 0))
    problemas = dr.validar({**s, "SubsidioBloque": bloques, "SubsidioDato": datos}, await _fuentes_por_id())
    return {"subsidio": s, "bloques": bloques, "datos": datos, "bloqueantes": problemas["bloqueantes"], "avisos": problemas["avisos"]}


# ── fichas ───────────────────────────────────────────────────────────────────

def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _revisor(perfil: Dict[str, Any]) -> str:
    return str(perfil.get("nombre") or perfil.get("correo") or "").strip() or "Dueño del sitio"


async def _ids_de_fuentes(valor: Any, etiqueta: str) -> List[int]:
    if valor is None:
        return []
    if not isinstance(valor, list) or len(valor) > 30 or any(isinstance(x, bool) or not isinstance(x, int) for x in valor):
        raise _error(f"«{etiqueta}» debe ser una lista de fuentes.")
    existentes = await _fuentes_por_id()
    faltan = [x for x in dict.fromkeys(valor) if x not in existentes]
    if faltan:
        raise _error(f"«{etiqueta}»: la fuente {faltan[0]} no existe.")
    return list(dict.fromkeys(valor))


async def _slug_libre(slug: str, excepto: Optional[int] = None) -> None:
    for f in await _filas("Subsidio", {"slug": f"eq.{slug}", "select": "id"}):
        if f["id"] != excepto:
            raise _error(f"Ya hay una ficha con la dirección «{slug}».", 409)


def _campos_de_ficha(body: Dict[str, Any], parcial: bool) -> Dict[str, Any]:
    """Valida los campos que vengan (todos si es una creación)."""
    out: Dict[str, Any] = {}
    reglas = {
        "slug": lambda: _slug(body, "slug", "Dirección (slug)"),
        "nombre_corto": lambda: _texto(body, "nombre_corto", 60, "Nombre corto"),
        "nombre_oficial": lambda: _texto(body, "nombre_oficial", 200, "Nombre oficial"),
        "decreto": lambda: _texto(body, "decreto", 60, "Decreto", obligatorio=False),
        "grupo": lambda: _opcion(body, "grupo", tuple(dr.GRUPOS), "Grupo"),
        "alcance": lambda: _opcion(body, "alcance", ALCANCES, "Alcance"),
        "postulacion": lambda: _opcion(body, "postulacion", tuple(dr.POSTULACION), "Cómo se postula"),
        "orden": lambda: _entero(body, "orden", "Orden"),
        "resumen": lambda: _texto(body, "resumen", 400, "Resumen (en una frase)"),
    }
    for campo, regla in reglas.items():
        if parcial and campo not in body:
            continue
        if not parcial and campo in ("decreto", "alcance", "postulacion", "orden") and campo not in body:
            out[campo] = {"decreto": None, "alcance": "completa", "postulacion": "por_llamados", "orden": 100}[campo]
            continue
        out[campo] = regla()
    return out


async def crear(body: Dict[str, Any], perfil: Dict[str, Any]) -> Dict[str, Any]:
    datos = _campos_de_ficha(body, parcial=False)
    await _slug_libre(datos["slug"])
    datos["fuentes"] = await _ids_de_fuentes(body.get("fuentes"), "Fuentes de la ficha")
    datos["estado"] = "borrador"
    fila = await _supabase_request("POST", "/Subsidio", json=datos, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0] if isinstance(fila, list) and fila else fila


async def editar(sid: int, body: Dict[str, Any]) -> Dict[str, Any]:
    actual = await _subsidio(sid)
    cambios = _campos_de_ficha(body, parcial=True)
    if "slug" in cambios and cambios["slug"] != actual["slug"]:
        if actual.get("estado") == "publicado":
            raise _error("Oculta la ficha antes de cambiar su dirección: los enlaces publicados dejarían de funcionar.", 409)
        await _slug_libre(cambios["slug"], excepto=sid)
    if "fuentes" in body:
        cambios["fuentes"] = await _ids_de_fuentes(body.get("fuentes"), "Fuentes de la ficha")
    if not cambios:
        raise _error("No hay nada que actualizar.", 400)
    cambios["actualizado_en"] = _ahora()
    fila = await _supabase_request("PATCH", "/Subsidio", params={"id": f"eq.{sid}"}, json=cambios, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0] if isinstance(fila, list) and fila else await _subsidio(sid)


async def marcar_revisada(sid: int, perfil: Dict[str, Any]) -> Dict[str, Any]:
    await _subsidio(sid)
    fila = await _supabase_request("PATCH", "/Subsidio", params={"id": f"eq.{sid}"},
                                   json={"revisado_en": date.today().isoformat(), "revisado_por": _revisor(perfil)}, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0]


async def cambiar_estado(sid: int, nuevo: Any) -> Dict[str, Any]:
    if nuevo not in ESTADOS:
        raise _error(f"El estado debe ser uno de: {', '.join(ESTADOS)}.")
    detalle = await obtener(sid)
    cambios: Dict[str, Any] = {"estado": nuevo}
    if nuevo == "publicado":
        pendientes = detalle["bloqueantes"] + detalle["avisos"]
        if pendientes:
            raise _error("No se puede publicar todavía:\n- " + "\n- ".join(pendientes), 422)
        cambios["publicado_en"] = _ahora()
    fila = await _supabase_request("PATCH", "/Subsidio", params={"id": f"eq.{sid}"}, json=cambios, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0]


async def eliminar(sid: int) -> None:
    s = await _subsidio(sid)
    if s.get("estado") != "borrador":
        raise _error("Solo se puede borrar una ficha en borrador. Ocúltala o archívala primero.", 409)
    await _supabase_request("DELETE", "/Subsidio", params={"id": f"eq.{sid}"})
    dr.invalidar_cache()


# ── bloques ──────────────────────────────────────────────────────────────────

async def _campos_de_bloque(body: Dict[str, Any], parcial: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not parcial or "clave" in body:
        out["clave"] = _opcion(body, "clave", CLAVES_BLOQUE, "Tipo de bloque")
    if not parcial or "titulo" in body:
        out["titulo"] = _texto(body, "titulo", 120, "Título")
    if not parcial or "cuerpo" in body:
        out["cuerpo"] = _texto(body, "cuerpo", 8000, "Texto")
    if not parcial or "orden" in body:
        out["orden"] = _entero(body, "orden", "Orden", defecto=100)
    if "fuentes" in body:
        out["fuentes"] = await _ids_de_fuentes(body.get("fuentes"), "Fuentes del bloque")
    return out


async def crear_bloque(sid: int, body: Dict[str, Any]) -> Dict[str, Any]:
    await _subsidio(sid)
    datos = await _campos_de_bloque(body, parcial=False)
    datos.setdefault("fuentes", [])
    datos["subsidio_id"] = sid
    fila = await _supabase_request("POST", "/SubsidioBloque", json=datos, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0]


async def _fila_de(tabla: str, fid: int, nombre: str) -> Dict[str, Any]:
    filas = await _filas(tabla, {"id": f"eq.{fid}"})
    if not filas:
        raise _error(f"No existe {nombre}.", 404)
    return filas[0]


async def editar_bloque(bid: int, body: Dict[str, Any]) -> Dict[str, Any]:
    await _fila_de("SubsidioBloque", bid, "ese bloque")
    cambios = await _campos_de_bloque(body, parcial=True)
    if not cambios:
        raise _error("No hay nada que actualizar.", 400)
    fila = await _supabase_request("PATCH", "/SubsidioBloque", params={"id": f"eq.{bid}"}, json=cambios, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0]


async def eliminar_bloque(bid: int) -> None:
    await _fila_de("SubsidioBloque", bid, "ese bloque")
    await _supabase_request("DELETE", "/SubsidioBloque", params={"id": f"eq.{bid}"})
    dr.invalidar_cache()


# ── datos ────────────────────────────────────────────────────────────────────

async def _fuente_existente(fid: Any) -> int:
    if isinstance(fid, bool) or not isinstance(fid, int):
        raise _error("Elige la fuente del dato.")
    if fid not in await _fuentes_por_id():
        raise _error(f"La fuente {fid} no existe.")
    return fid


async def _campos_de_dato(body: Dict[str, Any], parcial: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not parcial or "clave" in body:
        out["clave"] = _texto(body, "clave", 40, "Clave")
        if not CLAVE_DATO.match(out["clave"]):
            raise _error("«Clave» solo admite minúsculas, números y guion bajo (por ejemplo ahorro_minimo).")
    if not parcial or "etiqueta" in body:
        out["etiqueta"] = _texto(body, "etiqueta", 120, "Dato")
    if not parcial or "valor_texto" in body:
        out["valor_texto"] = _texto(body, "valor_texto", 200, "Valor")
    if not parcial or "valor_numero" in body:
        out["valor_numero"] = _numero_o_nulo(body, "valor_numero", "Valor numérico")
    if not parcial or "unidad" in body:
        out["unidad"] = _opcion(body, "unidad", UNIDADES, "Unidad")
    if "llamado" in body:
        out["llamado"] = _texto(body, "llamado", 120, "Llamado", obligatorio=False)
    if "nota" in body:
        out["nota"] = _texto(body, "nota", 500, "Nota", obligatorio=False)
    if not parcial or "orden" in body:
        out["orden"] = _entero(body, "orden", "Orden", defecto=100)
    if not parcial or "fuente_id" in body:
        out["fuente_id"] = await _fuente_existente(body.get("fuente_id"))
    return out


async def crear_dato(sid: int, body: Dict[str, Any]) -> Dict[str, Any]:
    await _subsidio(sid)
    datos = await _campos_de_dato({**body, "unidad": body.get("unidad", "texto")}, parcial=False)
    for d in await _filas("SubsidioDato", {"subsidio_id": f"eq.{sid}", "clave": f"eq.{datos['clave']}", "select": "id"}):
        raise _error(f"Ya hay un dato con la clave «{datos['clave']}» en esta ficha.", 409)
    datos.update(subsidio_id=sid, estado="por_confirmar", revisado_en=None, revisado_por=None)
    fila = await _supabase_request("POST", "/SubsidioDato", json=datos, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0]


async def editar_dato(did: int, body: Dict[str, Any]) -> Dict[str, Any]:
    actual = await _fila_de("SubsidioDato", did, "ese dato")
    cambios = await _campos_de_dato(body, parcial=True)
    if not cambios:
        raise _error("No hay nada que actualizar.", 400)
    if "clave" in cambios and cambios["clave"] != actual["clave"]:
        for d in await _filas("SubsidioDato", {"subsidio_id": f"eq.{actual['subsidio_id']}", "clave": f"eq.{cambios['clave']}", "select": "id"}):
            raise _error(f"Ya hay un dato con la clave «{cambios['clave']}» en esta ficha.", 409)
    reabierto = any(c in cambios and cambios[c] != actual.get(c) for c in CAMPOS_QUE_REABREN_UN_DATO)
    if reabierto:
        cambios.update(estado="por_confirmar", revisado_en=None, revisado_por=None)
    fila = await _supabase_request("PATCH", "/SubsidioDato", params={"id": f"eq.{did}"}, json=cambios, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return {**fila[0], "reabierto": reabierto and actual.get("estado") == "verificado"}


async def verificar_dato(did: int, perfil: Dict[str, Any]) -> Dict[str, Any]:
    dato = await _fila_de("SubsidioDato", did, "ese dato")
    fuente = (await _fuentes_por_id()).get(dato.get("fuente_id"))
    if not fuente or not dr.es_fuente_oficial(fuente.get("url")):
        raise _error("Para verificar un dato necesita una fuente oficial (enlace https de un sitio del Estado).", 422)
    fila = await _supabase_request("PATCH", "/SubsidioDato", params={"id": f"eq.{did}"},
                                   json={"estado": "verificado", "revisado_en": date.today().isoformat(), "revisado_por": _revisor(perfil)},
                                   extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0]


async def eliminar_dato(did: int) -> None:
    await _fila_de("SubsidioDato", did, "ese dato")
    await _supabase_request("DELETE", "/SubsidioDato", params={"id": f"eq.{did}"})
    dr.invalidar_cache()


# ── fuentes ──────────────────────────────────────────────────────────────────

TIPOS_FUENTE = tuple(dr.TIPOS_FUENTE)


async def listar_fuentes() -> List[Dict[str, Any]]:
    return list((await _fuentes_por_id()).values())


async def crear_fuente(body: Dict[str, Any]) -> Dict[str, Any]:
    url = _texto(body, "url", 500, "Enlace")
    if not dr.es_fuente_oficial(url):
        raise _error("El enlace debe ser https y de un sitio oficial del Estado (gob.cl, bcn.cl, minvu.cl, cmfchile.cl o sii.cl).")
    datos = {"titulo": _texto(body, "titulo", 250, "Título"), "url": url, "organismo": _texto(body, "organismo", 80, "Organismo"),
             "tipo": _opcion(body, "tipo", TIPOS_FUENTE, "Tipo"), "publicada_en": _fecha_o_nula(body, "publicada_en", "Fecha de la fuente"),
             "leida_en": _fecha_o_nula(body, "leida_en", "Fecha en que la leíste") or date.today().isoformat()}
    for f in await _filas("Fuente", {"url": f"eq.{url}", "select": "id"}):
        raise _error(f"Esa fuente ya existe (número {f['id']}).", 409)
    fila = await _supabase_request("POST", "/Fuente", json=datos, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0]


async def editar_fuente(fid: int, body: Dict[str, Any]) -> Dict[str, Any]:
    await _fila_de("Fuente", fid, "esa fuente")
    if "url" in body:
        raise _error("El enlace de una fuente no se edita: crea otra fuente y cámbiala en los datos que la usan.", 422)
    cambios: Dict[str, Any] = {}
    if "titulo" in body:
        cambios["titulo"] = _texto(body, "titulo", 250, "Título")
    if "organismo" in body:
        cambios["organismo"] = _texto(body, "organismo", 80, "Organismo")
    if "tipo" in body:
        cambios["tipo"] = _opcion(body, "tipo", TIPOS_FUENTE, "Tipo")
    if "publicada_en" in body:
        cambios["publicada_en"] = _fecha_o_nula(body, "publicada_en", "Fecha de la fuente")
    if "leida_en" in body:
        cambios["leida_en"] = _fecha_o_nula(body, "leida_en", "Fecha en que la leíste") or date.today().isoformat()
    if not cambios:
        raise _error("No hay nada que actualizar.", 400)
    fila = await _supabase_request("PATCH", "/Fuente", params={"id": f"eq.{fid}"}, json=cambios, extra_headers={"Prefer": "return=representation"})
    dr.invalidar_cache()
    return fila[0]


# ── vista previa ─────────────────────────────────────────────────────────────

async def vista_previa(sid: int) -> Dict[str, Any]:
    """La ficha tal como se vería publicada (aunque esté en borrador o tenga problemas), con la lista de lo que falta para poder publicarla."""
    detalle = await obtener(sid)
    fuentes = await _fuentes_por_id()
    s = {**detalle["subsidio"], "SubsidioBloque": detalle["bloques"], "SubsidioDato": detalle["datos"]}
    problemas = {"bloqueantes": detalle["bloqueantes"], "avisos": detalle["avisos"]}
    uf = await servicio_uf.uf_del_dia() if any(d.get("unidad") == "UF" for d in detalle["datos"]) else None
    ficha = dr.armar_ficha({**s, "nombre_corto": s.get("nombre_corto") or "(sin nombre)", "nombre_oficial": s.get("nombre_oficial") or "", "resumen": s.get("resumen") or ""}, fuentes, problemas, uf)
    ficha["robots"] = "noindex, nofollow"
    return {"ficha": ficha, "pendientes": problemas["bloqueantes"] + problemas["avisos"], "json_ld": dr.json_ld(ficha)}
