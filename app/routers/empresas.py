"""Catálogo (1/3): empresas, industrias e inmobiliarias. Todos exigen sesión; crear y editar, solo administradores.
Quien no es administrador ve solo lo que cuelga de sus inmobiliarias.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.errors import respuesta_error
from app.core.permisos import _solo_admin
from app.core.utilidades import _slugify
from fastapi import APIRouter, Request, Response
from typing import Any, Dict

router = APIRouter()


@router.get("/api/empresas")
async def api_listar_empresas(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    params: Dict[str, str] = {"estado": "eq.activa", "select": "id,nombre,slug,logo_url,color_marca", "order": "nombre.asc"}
    if not _solo_admin(perfil):
        inm_ids = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        if not inm_ids:
            return []
        inm_str = ",".join(str(i) for i in inm_ids)
        inmobiliarias = await _supabase_request(
            "GET", "/Inmobiliaria",
            params={"id": f"in.({inm_str})", "select": "empresa_id"},
        ) or []
        empresa_ids = list({str(i["empresa_id"]) for i in inmobiliarias if i.get("empresa_id")})
        if not empresa_ids:
            return []
        params["id"] = f"in.({','.join(empresa_ids)})"
    rows = await _supabase_request("GET", "/Empresa", params=params)
    return rows or []


@router.get("/api/industrias")
async def api_listar_industrias(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request("GET", "/Industria",
        params={"select": "id,nombre", "order": "nombre.asc"})
    return rows or []


@router.post("/api/empresas")
async def api_crear_empresa(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    nombre = (body.get("nombre") or "").strip()
    if not nombre:
        return Response(content="Falta el nombre", status_code=400)
    slug = (body.get("slug") or "").strip() or _slugify(nombre)
    payload: Dict[str, Any] = {
        "nombre": nombre, "slug": slug, "estado": "activa",
        "logo_url": (body.get("logo_url") or "").strip() or None,
        "color_marca": (body.get("color_marca") or "").strip() or None,
    }
    if body.get("industria_id"):
        payload["industria_id"] = body["industria_id"]
    try:
        row = await _supabase_request("POST", "/Empresa", json=payload,
            extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_empresa")


@router.patch("/api/empresas/{empresa_id}")
async def api_editar_empresa(empresa_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    payload: Dict[str, Any] = {}
    if "nombre" in body:
        payload["nombre"] = (body["nombre"] or "").strip()
    if "slug" in body:
        payload["slug"] = (body["slug"] or "").strip()
    if "logo_url" in body:
        payload["logo_url"] = (body["logo_url"] or "").strip() or None
    if "color_marca" in body:
        payload["color_marca"] = (body["color_marca"] or "").strip() or None
    if "industria_id" in body:
        payload["industria_id"] = body["industria_id"] or None
    if "estado" in body:
        payload["estado"] = body["estado"]
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    try:
        await _supabase_request("PATCH", "/Empresa",
            params={"id": f"eq.{empresa_id}"}, json=payload,
            extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_empresa")


@router.get("/api/inmobiliarias")
async def api_listar_inmobiliarias(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    empresa_id = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": "id,nombre,empresa_id,Empresa(nombre)", "order": "nombre.asc"}
    if empresa_id:
        params["empresa_id"] = f"eq.{empresa_id}"
    if not _solo_admin(perfil):
        inm_ids = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        if not inm_ids:
            return []
        params["id"] = f"in.({','.join(str(i) for i in inm_ids)})"
    rows = await _supabase_request("GET", "/Inmobiliaria", params=params)
    return rows or []


@router.post("/api/inmobiliarias")
async def api_crear_inmobiliaria(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    nombre     = (body.get("nombre") or "").strip()
    empresa_id = body.get("empresa_id")
    if not nombre or not empresa_id:
        return Response(content="Faltan campos obligatorios", status_code=400)
    row = await _supabase_request("POST", "/Inmobiliaria",
        json={"nombre": nombre, "empresa_id": empresa_id},
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@router.patch("/api/inmobiliarias/{inm_id}")
async def api_editar_inmobiliaria(inm_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    nombre = (body.get("nombre") or "").strip()
    if not nombre:
        return Response(content="Falta nombre", status_code=400)
    await _supabase_request("PATCH", "/Inmobiliaria",
        params={"id": f"eq.{inm_id}"},
        json={"nombre": nombre},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}
