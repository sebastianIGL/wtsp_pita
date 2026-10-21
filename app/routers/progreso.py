"""Progreso del usuario en «Mi camino» (/api/progreso): qué requisitos completó y guardar un avance. Usa el JWT del usuario
(no el perfil del CRM): sin sesión devuelve 401.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _uid_desde_jwt
from fastapi import APIRouter, Request, Response
from typing import Dict, List

router = APIRouter()


@router.get("/api/progreso")
async def api_get_progreso(request: Request):
    uid = await _uid_desde_jwt(request)
    if not uid:
        return Response(content="no autorizado", status_code=401)
    rows = await _supabase_request("GET", "/progreso_usuario",
        params={"user_id": f"eq.{uid}", "select": "mundo,req_id,completado"},
    ) or []
    resultado: Dict[str, List[str]] = {"s": [], "h": []}
    for row in rows:
        if row.get("completado"):
            m = row.get("mundo", "")
            if m in resultado:
                resultado[m].append(row.get("req_id", ""))
    return resultado


@router.post("/api/progreso")
async def api_set_progreso(request: Request):
    uid = await _uid_desde_jwt(request)
    if not uid:
        return Response(content="no autorizado", status_code=401)
    body = await request.json()
    mundo  = (body.get("mundo")  or "").strip()
    req_id = (body.get("req_id") or "").strip()
    completado = bool(body.get("completado", True))
    if not mundo or not req_id:
        return Response(content="mundo y req_id son requeridos", status_code=422)
    await _supabase_request(
        "POST", "/progreso_usuario",
        json={"user_id": uid, "mundo": mundo, "req_id": req_id, "completado": completado},
        extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    return {"ok": True}
