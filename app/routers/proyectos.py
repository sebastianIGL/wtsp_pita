"""Catálogo (2/3): proyectos (listar según lo que cada persona puede ver, crear, editar y subir la imagen).
Editar un proyecto vacía el caché de la landing.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import time
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.clients.supabase_storage import StorageError, subir_imagen_proyecto
from app.core.cache_proyectos import _cache_proyectos
from app.core.errors import respuesta_error
from app.core.permisos import _solo_admin
from app.services.proyectos import _PROYECTO_SELECT_PANEL, _aplicar_campos_proyecto
from fastapi import APIRouter, File, Request, Response, UploadFile
from typing import Any, Dict, List

router = APIRouter()


@router.get("/api/proyectos")
async def api_listar_proyectos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _PROYECTO_SELECT_PANEL, "order": "nombre.asc"}
    if _solo_admin(perfil):
        if inmobiliaria_id:
            params["inmobiliaria_id"] = f"eq.{inmobiliaria_id}"
        elif empresa_id:
            inmobiliarias = await _supabase_request(
                "GET", "/Inmobiliaria",
                params={"empresa_id": f"eq.{empresa_id}", "select": "id"},
            ) or []
            inm_ids = ",".join(str(i["id"]) for i in inmobiliarias)
            if not inm_ids:
                return []
            params["inmobiliaria_id"] = f"in.({inm_ids})"
    else:
        inm_asig  = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        proy_asig = [str(p) for p in (perfil.get("proyecto_ids") or []) if p is not None]
        proy_de_inm: List[str] = []
        if inm_asig:
            inm_str = ",".join(str(i) for i in inm_asig)
            proy_de_inm = [str(p["id"]) for p in (await _supabase_request(
                "GET", "/Proyecto",
                params={"inmobiliaria_id": f"in.({inm_str})", "select": "id"},
            ) or []) if p.get("id")]
        visibles = set(proy_asig + proy_de_inm)
        if not visibles:
            return []
        params["id"] = f"in.({','.join(visibles)})"
    rows = await _supabase_request("GET", "/Proyecto", params=params)
    return rows or []


@router.post("/api/proyectos")
async def api_crear_proyecto(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    nombre          = (body.get("nombre") or "").strip()
    codigo          = (body.get("codigo") or "").strip()
    ubicacion       = (body.get("ubicacion") or "").strip() or None
    inmobiliaria_id = body.get("inmobiliaria_id")
    if not nombre or not codigo or not inmobiliaria_id:
        return Response(content="Faltan campos obligatorios", status_code=400)

    payload: Dict[str, Any] = {
        "nombre": nombre, "codigo": codigo,
        "ubicacion": ubicacion, "inmobiliaria_id": inmobiliaria_id,
    }
    _aplicar_campos_proyecto(body, payload, es_admin=_solo_admin(perfil))
    row = await _supabase_request("POST", "/Proyecto", json=payload,
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@router.patch("/api/proyectos/{proyecto_id}")
async def api_editar_proyecto(proyecto_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    payload: Dict[str, Any] = {}
    for f in ("nombre", "codigo", "ubicacion"):
        if body.get(f): payload[f] = body[f].strip()
    _aplicar_campos_proyecto(body, payload, es_admin=_solo_admin(perfil))
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    await _supabase_request("PATCH", "/Proyecto",
        params={"id": f"eq.{proyecto_id}"},
        json=payload, extra_headers={"Prefer": "return=minimal"})
    _cache_proyectos["data"] = None
    return {"ok": True}


@router.post("/api/proyectos/upload-imagen")
async def api_subir_imagen_proyecto(request: Request, file: UploadFile = File(...)):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)

    MAX_SIZE = 3 * 1024 * 1024
    contenido = await file.read()
    if len(contenido) > MAX_SIZE:
        return Response(content="La imagen supera los 3MB", status_code=400)

    mime = file.content_type or "image/jpeg"
    ext  = (file.filename or "imagen").rsplit(".", 1)[-1].lower() or "jpg"
    path = f"proyecto-{int(time.time())}.{ext}"

    try:
        public_url = await subir_imagen_proyecto(path, contenido, mime)
    except StorageError as e:
        return respuesta_error(e, contexto="api_subir_imagen_proyecto")
    return {"imagen_url": public_url}
