"""Scraping de buydepa (lanzar la sincronización y ver sus registros), ejecutivos bancarios (solo administradores) y
asignación de ejecutivos a proyectos.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.permisos import _solo_admin
from app.services.buydepa import _sincronizar_buydepa_stream
from app.services.proyectos import obtener_proyecto_por_id
from fastapi import APIRouter, Request, Response
from typing import Any, Dict, List

router = APIRouter()


@router.post("/api/scraping/buydepa/ejecutar")
async def api_ejecutar_scraping_buydepa(request: Request):
    from fastapi.responses import StreamingResponse as _SR
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    return _SR(
        _sincronizar_buydepa_stream(usuario_id=perfil.get("id")),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/scraping/logs")
async def api_listar_logs_scraping(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request("GET", "/ScrapingLog",
        params={"select": "*,Usuario(nombre)", "order": "ejecutado_en.desc", "limit": "100"})
    return rows or []


@router.get("/api/ejecutivos")
async def api_listar_ejecutivos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request("GET", "/EjecutivoBancario",
        params={"select": "id,ejecutivo,entidad,email,telefono,disponible,proyecto", "order": "ejecutivo.asc"}) or []

    pe_rows = await _supabase_request("GET", "/ProyectoEjecutivo",
        params={"select": "ejecutivo_id,proyecto_id"}) or []
    adicionales_por_ejec: Dict[Any, List[str]] = {}
    for r in pe_rows:
        adicionales_por_ejec.setdefault(r["ejecutivo_id"], []).append(r["proyecto_id"])

    for e in rows:
        proyecto_ids = list(adicionales_por_ejec.get(e["id"], []))
        # El campo legado "proyecto" (proyecto único) se funde en la misma lista
        # para que el front muestre siempre una sola vista unificada.
        if e.get("proyecto") and e["proyecto"] not in proyecto_ids:
            proyecto_ids.append(e["proyecto"])
        e["proyecto_ids"] = proyecto_ids
    return rows


@router.post("/api/ejecutivos")
async def api_crear_ejecutivo(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    payload = {
        "ejecutivo": (body.get("ejecutivo") or "").strip(),
        "entidad":   (body.get("entidad") or "").strip(),
        "email":     (body.get("email") or "").strip().lower(),
        "disponible": bool(body.get("disponible", True)),
    }
    if body.get("telefono"):
        payload["telefono"] = body["telefono"]
    if not payload["ejecutivo"] or not payload["entidad"] or not payload["email"]:
        return Response(content="Nombre, entidad y email son obligatorios", status_code=400)
    row = await _supabase_request("POST", "/EjecutivoBancario", json=payload)
    creado = row[0] if isinstance(row, list) else row

    proyecto_ids = body.get("proyecto_ids") or []
    if proyecto_ids and creado and creado.get("id"):
        await _supabase_request("POST", "/ProyectoEjecutivo",
            json=[{"ejecutivo_id": creado["id"], "proyecto_id": pid} for pid in proyecto_ids],
            extra_headers={"Prefer": "return=minimal"})
    return creado


@router.patch("/api/ejecutivos/{ejecutivo_id}")
async def api_editar_ejecutivo(ejecutivo_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    allowed = {"ejecutivo", "entidad", "email", "telefono", "disponible"}
    payload = {k: v for k, v in body.items() if k in allowed}
    if "email" in payload:
        payload["email"] = payload["email"].strip().lower()
    if "ejecutivo" in payload:
        payload["ejecutivo"] = payload["ejecutivo"].strip()
    if "entidad" in payload:
        payload["entidad"] = payload["entidad"].strip()

    proyecto_ids = body.get("proyecto_ids")
    if proyecto_ids is not None:
        # La lista de proyectos permitidos ahora vive completa en ProyectoEjecutivo;
        # el campo legado "proyecto" se limpia para no duplicar la fuente de verdad.
        payload["proyecto"] = None
        await _supabase_request("DELETE", "/ProyectoEjecutivo",
            params={"ejecutivo_id": f"eq.{ejecutivo_id}"}, extra_headers={"Prefer": "return=minimal"})
        if proyecto_ids:
            await _supabase_request("POST", "/ProyectoEjecutivo",
                json=[{"ejecutivo_id": ejecutivo_id, "proyecto_id": pid} for pid in proyecto_ids],
                extra_headers={"Prefer": "return=minimal"})

    if payload:
        await _supabase_request("PATCH", f"/EjecutivoBancario?id=eq.{ejecutivo_id}", json=payload)
    return {"ok": True}


@router.delete("/api/ejecutivos/{ejecutivo_id}")
async def api_eliminar_ejecutivo(ejecutivo_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", f"/EjecutivoBancario?id=eq.{ejecutivo_id}")
    return {"ok": True}


@router.get("/api/proyecto-ejecutivos")
async def api_listar_proyecto_ejecutivos(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id = request.query_params.get("proyecto_id")
    params: Dict[str, str] = {
        "select": "proyecto_id,ejecutivo_id,EjecutivoBancario(id,ejecutivo,email)",
    }
    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    rows = await _supabase_request("GET", "/ProyectoEjecutivo", params=params)
    return rows or []


@router.post("/api/proyecto-ejecutivos")
async def api_asignar_ejecutivo(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    proyecto_id  = body.get("proyecto_id")
    ejecutivo_id = body.get("ejecutivo_id")
    if not proyecto_id or not ejecutivo_id:
        return Response(content="Faltan campos obligatorios", status_code=400)
    proyecto = await obtener_proyecto_por_id(proyecto_id)
    if not proyecto:
        return Response(content="Proyecto no encontrado", status_code=404)
    row = await _supabase_request("POST", "/ProyectoEjecutivo",
        json={"proyecto_id": proyecto_id, "ejecutivo_id": ejecutivo_id},
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@router.delete("/api/proyecto-ejecutivos")
async def api_quitar_ejecutivo(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id  = request.query_params.get("proyecto_id")
    ejecutivo_id = request.query_params.get("ejecutivo_id")
    if not proyecto_id or not ejecutivo_id:
        return Response(content="Faltan parámetros", status_code=400)
    await _supabase_request("DELETE", "/ProyectoEjecutivo",
        params={"proyecto_id": f"eq.{proyecto_id}", "ejecutivo_id": f"eq.{ejecutivo_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}
