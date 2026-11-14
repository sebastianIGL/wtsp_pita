"""Estimador de comisiones: ``GET /api/comisiones/planes`` entrega los planes de comisión (ticket, porcentaje, bonos) que aplican a los proyectos que la persona puede ver.
Cualquier persona con sesión; un ejecutivo solo ve los planes de sus proyectos. La lógica está en ``app/services/comisiones.py``."""
from fastapi import APIRouter, Request, Response

from app.clients.supabase_auth import _get_usuario_actual
from app.core.errors import mensaje_publico
from app.services import comisiones

router = APIRouter()


@router.get("/api/comisiones/planes")
async def api_planes_comision(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        return await comisiones.planes_para(perfil)
    except Exception as e:
        return Response(content=mensaje_publico(e, "api_planes_comision"), status_code=500, media_type="text/plain")
