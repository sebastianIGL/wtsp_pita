"""Pestaña «Gasto» del panel de administración: cuánto consume cada cliente en IA (Anthropic) y en mensajes de WhatsApp (Meta), y las tarifas con que se calcula.
Solo administración. La lógica está en ``app/services/gasto.py``."""
from fastapi import APIRouter, Request, Response

from app.clients.supabase_auth import _get_usuario_actual
from app.core.errors import AppError, mensaje_publico
from app.core.permisos import _solo_admin
from app.services import gasto

router = APIRouter()


def _texto_error(e: Exception, ruta: str) -> Response:
    return Response(content=mensaje_publico(e, ruta), status_code=e.status_code if isinstance(e, AppError) else 500, media_type="text/plain")


@router.get("/api/gasto")
async def api_gasto(request: Request, desde: str = "", hasta: str = "", agrupar: str = "cliente"):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        return await gasto.resumen(desde or None, hasta or None, agrupar)
    except Exception as e:
        return _texto_error(e, "api_gasto")


@router.put("/api/gasto/tarifas")
async def api_gasto_guardar_tarifas(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        cuerpo = await request.json()
        await gasto.guardar_tarifas(gasto.validar_tarifas(cuerpo))
        return {"ok": True, "tarifas": await gasto.leer_tarifas()}
    except Exception as e:
        return _texto_error(e, "api_gasto_guardar_tarifas")
