"""API del editor del directorio de subsidios (pestaña «Subsidios» del panel de administración). Todas las rutas exigen sesión y son **solo del dueño**.
La lógica y sus reglas están en ``app/services/directorio_admin.py``."""
import logging
from typing import Any, Awaitable, Callable, Dict, Tuple, Union

from app.clients.supabase_auth import _get_usuario_actual
from app.core.errors import respuesta_error
from app.core.permisos import _solo_owner
from app.routers.paginas import templates
from app.services import directorio_admin as ed
from fastapi import APIRouter, Request, Response

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


async def _dueno(request: Request) -> Union[Dict[str, Any], Response]:
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_owner(perfil):
        return Response(content="Solo el dueño", status_code=403)
    return perfil


async def _cuerpo(request: Request) -> Union[Dict[str, Any], Response]:
    try:
        body = await request.json()
    except Exception:
        return Response(content="El cuerpo debe ser JSON", status_code=400)
    if not isinstance(body, dict):
        return Response(content="El cuerpo debe ser un objeto JSON", status_code=400)
    return body


async def _ejecutar(contexto: str, accion: Callable[[], Awaitable[Any]]):
    try:
        return await accion()
    except Exception as e:
        return respuesta_error(e, contexto=contexto)


# ── fichas ───────────────────────────────────────────────────────────────────

@router.get("/api/directorio/subsidios")
async def api_directorio_listar(request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    return await _ejecutar("api_directorio_listar", ed.listar)


@router.post("/api/directorio/subsidios")
async def api_directorio_crear(request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_crear", lambda: ed.crear(body, perfil))


@router.get("/api/directorio/subsidios/{sid}")
async def api_directorio_obtener(sid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    return await _ejecutar("api_directorio_obtener", lambda: ed.obtener(sid))


@router.patch("/api/directorio/subsidios/{sid}")
async def api_directorio_editar(sid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_editar", lambda: ed.editar(sid, body))


@router.delete("/api/directorio/subsidios/{sid}")
async def api_directorio_borrar(sid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil

    async def borrar():
        await ed.eliminar(sid)
        return {"ok": True}
    return await _ejecutar("api_directorio_borrar", borrar)


@router.post("/api/directorio/subsidios/{sid}/estado")
async def api_directorio_estado(sid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_estado", lambda: ed.cambiar_estado(sid, body.get("estado")))


@router.post("/api/directorio/subsidios/{sid}/revisada")
async def api_directorio_revisada(sid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    return await _ejecutar("api_directorio_revisada", lambda: ed.marcar_revisada(sid, perfil))


@router.get("/api/directorio/subsidios/{sid}/vista-previa")
async def api_directorio_vista_previa(sid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    try:
        prev = await ed.vista_previa(sid)
    except Exception as e:
        return respuesta_error(e, contexto="api_directorio_vista_previa")
    ficha = prev["ficha"]
    r = templates.TemplateResponse(request, "subsidio.html", {
        "active": "subsidios", "subsidio": ficha, "json_ld": prev["json_ld"], "robots": "noindex, nofollow",
        "page_title": ficha["titulo"], "page_description": ficha["descripcion"], "page_path": ficha["ruta"],
        "vista_previa": True, "pendientes": prev["pendientes"],
    })
    r.headers["Cache-Control"] = "no-store"
    r.headers["X-Robots-Tag"] = "noindex, nofollow"
    return r


# ── bloques ──────────────────────────────────────────────────────────────────

@router.post("/api/directorio/subsidios/{sid}/bloques")
async def api_directorio_bloque_crear(sid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_bloque_crear", lambda: ed.crear_bloque(sid, body))


@router.patch("/api/directorio/bloques/{bid}")
async def api_directorio_bloque_editar(bid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_bloque_editar", lambda: ed.editar_bloque(bid, body))


@router.delete("/api/directorio/bloques/{bid}")
async def api_directorio_bloque_borrar(bid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil

    async def borrar():
        await ed.eliminar_bloque(bid)
        return {"ok": True}
    return await _ejecutar("api_directorio_bloque_borrar", borrar)


# ── datos ────────────────────────────────────────────────────────────────────

@router.post("/api/directorio/subsidios/{sid}/datos")
async def api_directorio_dato_crear(sid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_dato_crear", lambda: ed.crear_dato(sid, body))


@router.patch("/api/directorio/datos/{did}")
async def api_directorio_dato_editar(did: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_dato_editar", lambda: ed.editar_dato(did, body))


@router.post("/api/directorio/datos/{did}/verificar")
async def api_directorio_dato_verificar(did: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    return await _ejecutar("api_directorio_dato_verificar", lambda: ed.verificar_dato(did, perfil))


@router.delete("/api/directorio/datos/{did}")
async def api_directorio_dato_borrar(did: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil

    async def borrar():
        await ed.eliminar_dato(did)
        return {"ok": True}
    return await _ejecutar("api_directorio_dato_borrar", borrar)


# ── fuentes ──────────────────────────────────────────────────────────────────

@router.get("/api/directorio/fuentes")
async def api_directorio_fuentes(request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    return await _ejecutar("api_directorio_fuentes", ed.listar_fuentes)


@router.post("/api/directorio/fuentes")
async def api_directorio_fuente_crear(request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_fuente_crear", lambda: ed.crear_fuente(body))


@router.patch("/api/directorio/fuentes/{fid}")
async def api_directorio_fuente_editar(fid: int, request: Request):
    perfil = await _dueno(request)
    if isinstance(perfil, Response):
        return perfil
    body = await _cuerpo(request)
    if isinstance(body, Response):
        return body
    return await _ejecutar("api_directorio_fuente_editar", lambda: ed.editar_fuente(fid, body))
