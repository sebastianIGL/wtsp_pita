"""Editor de guías y glosario (Fase 5): las operaciones que usan las secciones «Guías» y «Glosario» de la pestaña «Subsidios» del panel (solo el dueño).

Reglas (se suman a las de ``guias.validar_guia`` / ``validar_termino``, que además se aplican al mostrar):
  * Una guía o un término **nace en borrador**. Una guía solo se publica si ``validar_guia`` no da ni problemas graves ni avisos (texto completo, fuentes
    oficiales, revisada en los últimos 90 días); un término, si tiene definición y una fuente oficial.
  * **Cambiar el título, el resumen, el texto o las fuentes de una guía borra su revisión**: hay que volver a marcarla revisada.
  * Marcar una guía como revisada registra quién y cuándo (la persona autenticada).
  * La dirección (slug) de una guía publicada no se cambia; una guía o término publicado no se borra.
Cada escritura invalida la copia de lectura pública.
"""
from datetime import date
from typing import Any, Dict, List

from app.clients.supabase import _supabase_request
from app.services import directorio_admin as ed
from app.services import guias as g
from app.services.directorio import SLUG_MAX

ESTADOS = ed.ESTADOS
CAMPOS_QUE_REABREN_UNA_GUIA = ("titulo", "resumen", "cuerpo", "fuentes")


# ── guías ────────────────────────────────────────────────────────────────────

async def _guia(gid: int) -> Dict[str, Any]:
    return await ed._fila_de("Guia", gid, "esa guía")


def _campos_de_guia(body: Dict[str, Any], parcial: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    reglas = {
        "slug": lambda: ed._slug(body, "slug", "Dirección (slug)"),
        "titulo": lambda: ed._texto(body, "titulo", 150, "Título"),
        "resumen": lambda: ed._texto(body, "resumen", 400, "Resumen (en una frase)"),
        "cuerpo": lambda: ed._texto(body, "cuerpo", 20000, "Texto"),
        "orden": lambda: ed._entero(body, "orden", "Orden"),
    }
    for campo, regla in reglas.items():
        if parcial and campo not in body:
            continue
        if not parcial and campo == "orden" and campo not in body:
            out[campo] = 100
            continue
        out[campo] = regla()
    return out


async def listar_guias() -> List[Dict[str, Any]]:
    filas = await ed._filas("Guia", {"order": "orden.asc,titulo.asc"})
    fuentes = await ed._fuentes_por_id()
    salida = []
    for f in filas:
        p = g.validar_guia(f, fuentes)
        salida.append({k: f.get(k) for k in ("id", "slug", "titulo", "estado", "orden", "revisado_en", "revisado_por")} | {"bloqueantes": p["bloqueantes"], "avisos": p["avisos"]})
    return salida


async def obtener_guia(gid: int) -> Dict[str, Any]:
    f = await _guia(gid)
    p = g.validar_guia(f, await ed._fuentes_por_id())
    return {"guia": f, "bloqueantes": p["bloqueantes"], "avisos": p["avisos"]}


async def crear_guia(body: Dict[str, Any], perfil: Dict[str, Any]) -> Dict[str, Any]:
    datos = _campos_de_guia(body, parcial=False)
    await _slug_libre(datos["slug"])
    datos["fuentes"] = await ed._ids_de_fuentes(body.get("fuentes"), "Fuentes de la guía")
    datos["estado"] = "borrador"
    fila = await _supabase_request("POST", "/Guia", json=datos, extra_headers={"Prefer": "return=representation"})
    g.invalidar_cache()
    return fila[0] if isinstance(fila, list) and fila else fila


async def _slug_libre(slug: str, excepto: Any = None) -> None:
    for f in await ed._filas("Guia", {"slug": f"eq.{slug}", "select": "id"}):
        if f["id"] != excepto:
            raise ed._error(f"Ya hay una guía con la dirección «{slug}».", 409)


async def editar_guia(gid: int, body: Dict[str, Any]) -> Dict[str, Any]:
    actual = await _guia(gid)
    cambios = _campos_de_guia(body, parcial=True)
    if "slug" in cambios and cambios["slug"] != actual["slug"]:
        if actual.get("estado") == "publicado":
            raise ed._error("Oculta la guía antes de cambiar su dirección: los enlaces publicados dejarían de funcionar.", 409)
        await _slug_libre(cambios["slug"], excepto=gid)
    if "fuentes" in body:
        cambios["fuentes"] = await ed._ids_de_fuentes(body.get("fuentes"), "Fuentes de la guía")
    if not cambios:
        raise ed._error("No hay nada que actualizar.", 400)
    reabierta = any(c in cambios and cambios[c] != actual.get(c) for c in CAMPOS_QUE_REABREN_UNA_GUIA)
    if reabierta:
        cambios.update(revisado_en=None, revisado_por=None)
    cambios["actualizada_en"] = ed._ahora()
    fila = await _supabase_request("PATCH", "/Guia", params={"id": f"eq.{gid}"}, json=cambios, extra_headers={"Prefer": "return=representation"})
    g.invalidar_cache()
    return {**fila[0], "revision_borrada": reabierta and bool(actual.get("revisado_en"))}


async def marcar_revisada_guia(gid: int, perfil: Dict[str, Any]) -> Dict[str, Any]:
    await _guia(gid)
    fila = await _supabase_request("PATCH", "/Guia", params={"id": f"eq.{gid}"}, json={"revisado_en": date.today().isoformat(), "revisado_por": ed._revisor(perfil)},
                                   extra_headers={"Prefer": "return=representation"})
    g.invalidar_cache()
    return fila[0]


async def cambiar_estado_guia(gid: int, nuevo: Any) -> Dict[str, Any]:
    if nuevo not in ESTADOS:
        raise ed._error(f"El estado debe ser uno de: {', '.join(ESTADOS)}.")
    detalle = await obtener_guia(gid)
    if nuevo == "publicado":
        pendientes = detalle["bloqueantes"] + detalle["avisos"]
        if pendientes:
            raise ed._error("No se puede publicar todavía:\n- " + "\n- ".join(pendientes), 422)
    fila = await _supabase_request("PATCH", "/Guia", params={"id": f"eq.{gid}"}, json={"estado": nuevo}, extra_headers={"Prefer": "return=representation"})
    g.invalidar_cache()
    return fila[0]


async def eliminar_guia(gid: int) -> None:
    f = await _guia(gid)
    if f.get("estado") != "borrador":
        raise ed._error("Solo se puede borrar una guía en borrador. Ocúltala o archívala primero.", 409)
    await _supabase_request("DELETE", "/Guia", params={"id": f"eq.{gid}"})
    g.invalidar_cache()


async def vista_previa_guia(gid: int) -> Dict[str, Any]:
    """La guía tal como se vería publicada (aunque esté en borrador o tenga problemas), con la lista de lo que falta para publicarla."""
    detalle = await obtener_guia(gid)
    f = detalle["guia"]
    problemas = {"bloqueantes": detalle["bloqueantes"], "avisos": detalle["avisos"]}
    base = {**f, "titulo": f.get("titulo") or "(sin título)", "resumen": f.get("resumen") or "", "slug": f.get("slug") or "sin-direccion"}
    guia = g.armar_guia(base, await ed._fuentes_por_id(), problemas)
    guia["robots"] = "noindex, nofollow"
    return {"guia": guia, "pendientes": problemas["bloqueantes"] + problemas["avisos"], "json_ld": g.json_ld_guia(guia)}


# ── glosario ─────────────────────────────────────────────────────────────────

async def _termino(tid: int) -> Dict[str, Any]:
    return await ed._fila_de("GlosarioTermino", tid, "ese término")


async def _campos_de_termino(body: Dict[str, Any], parcial: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not parcial or "termino" in body:
        out["termino"] = ed._texto(body, "termino", 80, "Término")
    if not parcial or "definicion" in body:
        out["definicion"] = ed._texto(body, "definicion", 700, "Definición")
    if not parcial or "orden" in body:
        out["orden"] = ed._entero(body, "orden", "Orden", defecto=100)
    if not parcial or "fuente_id" in body:
        out["fuente_id"] = await ed._fuente_existente(body.get("fuente_id"))
    return out


async def listar_terminos() -> List[Dict[str, Any]]:
    filas = await ed._filas("GlosarioTermino", {"order": "termino.asc"})
    fuentes = await ed._fuentes_por_id()
    return [{k: t.get(k) for k in ("id", "termino", "definicion", "fuente_id", "estado", "orden")} | {"bloqueantes": g.validar_termino(t, fuentes)} for t in filas]


async def _termino_libre(termino: str, excepto: Any = None) -> None:
    for t in await ed._filas("GlosarioTermino", {"select": "id,termino"}):
        if g.clave_orden(t.get("termino")) == g.clave_orden(termino) and t["id"] != excepto:
            raise ed._error(f"Ya existe el término «{t.get('termino')}».", 409)


async def crear_termino(body: Dict[str, Any]) -> Dict[str, Any]:
    datos = await _campos_de_termino(body, parcial=False)
    await _termino_libre(datos["termino"])
    datos["estado"] = "borrador"
    fila = await _supabase_request("POST", "/GlosarioTermino", json=datos, extra_headers={"Prefer": "return=representation"})
    g.invalidar_cache()
    return fila[0] if isinstance(fila, list) and fila else fila


async def editar_termino(tid: int, body: Dict[str, Any]) -> Dict[str, Any]:
    await _termino(tid)
    cambios = await _campos_de_termino(body, parcial=True)
    if not cambios:
        raise ed._error("No hay nada que actualizar.", 400)
    if "termino" in cambios:
        await _termino_libre(cambios["termino"], excepto=tid)
    fila = await _supabase_request("PATCH", "/GlosarioTermino", params={"id": f"eq.{tid}"}, json=cambios, extra_headers={"Prefer": "return=representation"})
    g.invalidar_cache()
    return fila[0]


async def cambiar_estado_termino(tid: int, nuevo: Any) -> Dict[str, Any]:
    if nuevo not in ESTADOS:
        raise ed._error(f"El estado debe ser uno de: {', '.join(ESTADOS)}.")
    t = await _termino(tid)
    if nuevo == "publicado":
        problemas = g.validar_termino(t, await ed._fuentes_por_id())
        if problemas:
            raise ed._error("No se puede publicar todavía:\n- " + "\n- ".join(problemas), 422)
    fila = await _supabase_request("PATCH", "/GlosarioTermino", params={"id": f"eq.{tid}"}, json={"estado": nuevo}, extra_headers={"Prefer": "return=representation"})
    g.invalidar_cache()
    return fila[0]


async def eliminar_termino(tid: int) -> None:
    t = await _termino(tid)
    if t.get("estado") != "borrador":
        raise ed._error("Solo se puede borrar un término en borrador. Ocúltalo o archívalo primero.", 409)
    await _supabase_request("DELETE", "/GlosarioTermino", params={"id": f"eq.{tid}"})
    g.invalidar_cache()
