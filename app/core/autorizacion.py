"""Autorización por recurso (Fase 7, grupo 4): quién puede tocar los datos de un cliente.

Regla: administrador y owner ven todo; el ejecutivo solo los clientes cuyo ``usuario_id`` es el suyo; un cliente sin dueño solo lo ven
administrador y owner. Mensajes, documentos, evaluación y conversación heredan el dueño del cliente. Lo denegado responde 404
«Cliente no encontrado» (igual que si no existiera: no revela que existe).

Proyectos: ``exigir_proyecto`` aplica el mismo criterio que la lista de ``GET /api/proyectos``: administrador y owner pueden usar cualquiera; el ejecutivo solo los que
tiene asignados o los de una inmobiliaria que tiene asignada. Un proyecto que no puede usar responde 403 (los proyectos no son secretos: aquí se decide un permiso).
"""
import re
from typing import Dict, Optional

from fastapi import Response

from app.clients.supabase import _supabase_request
from app.core.permisos import _solo_admin


def _no_encontrado() -> Response:
    return Response(content="Cliente no encontrado", status_code=404)


_ID_PROYECTO = re.compile(r"[A-Za-z0-9_-]{1,64}")           # los ids reales son UUID; cualquier otra cosa no es un proyecto y nunca llega a la base


async def exigir_proyecto(perfil: Dict, proyecto_id) -> Optional[Response]:
    """``None`` si el usuario puede usar este proyecto (o si no se indicó ninguno); si no, la respuesta 403 que la ruta debe devolver.
    Administrador y owner no hacen ninguna consulta; el ejecutivo, ninguna si el proyecto está entre los suyos y una si solo tiene inmobiliarias asignadas."""
    if _solo_admin(perfil) or proyecto_id is None or proyecto_id == "":
        return None
    pid = str(proyecto_id)
    if isinstance(proyecto_id, (str, int)) and not isinstance(proyecto_id, bool) and _ID_PROYECTO.fullmatch(pid):
        if pid in {str(p) for p in (perfil.get("proyecto_ids") or []) if p is not None}:
            return None
        inmobiliarias = [str(i) for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        if inmobiliarias:
            filas = await _supabase_request(
                "GET", "/Proyecto",
                params={"id": f"eq.{pid}", "inmobiliaria_id": f"in.({','.join(inmobiliarias)})", "select": "id", "limit": "1"},
            ) or []
            if filas:
                return None
    return Response(content="No tienes acceso a ese proyecto", status_code=403)


async def exigir_cliente(perfil: Dict, cliente_id) -> Optional[Response]:
    """``None`` si el usuario puede usar este cliente; si no, la respuesta 404 que la ruta debe devolver.
    Administrador y owner no hacen ninguna consulta; el ejecutivo, una."""
    if _solo_admin(perfil):
        return None
    filas = await _supabase_request(
        "GET", "/Cliente",
        params={"id": f"eq.{cliente_id}", "select": "id,usuario_id", "limit": "1"},
    ) or []
    dueno = filas[0].get("usuario_id") if filas else None
    if dueno and str(dueno) == str(perfil.get("id")):
        return None
    return _no_encontrado()


async def exigir_prospecto(perfil: Dict, prospecto_id) -> Optional[Response]:
    """Conversación de un prospecto. Si está vinculado a un cliente, hereda su dueño. Un prospecto sin cliente (la bandeja de entrada)
    sigue visible para quien tenga sesión, como ya lo es el inbox; uno que no existe responde como antes."""
    if _solo_admin(perfil):
        return None
    filas = await _supabase_request(
        "GET", "/Prospecto",
        params={"id": f"eq.{prospecto_id}", "select": "id,cliente_id", "limit": "1"},
    ) or []
    cliente_id = filas[0].get("cliente_id") if filas else None
    if not cliente_id:
        return None
    return await exigir_cliente(perfil, cliente_id)
