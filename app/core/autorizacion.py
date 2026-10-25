"""Autorización por recurso (Fase 7, grupo 4): quién puede tocar los datos de un cliente.

Regla: administrador y owner ven todo; el ejecutivo solo los clientes cuyo ``usuario_id`` es el suyo; un cliente sin dueño solo lo ven
administrador y owner. Mensajes, documentos, evaluación y conversación heredan el dueño del cliente. Lo denegado responde 404
«Cliente no encontrado» (igual que si no existiera: no revela que existe).
"""
from typing import Dict, Optional

from fastapi import Response

from app.clients.supabase import _supabase_request
from app.core.permisos import _solo_admin


def _no_encontrado() -> Response:
    return Response(content="Cliente no encontrado", status_code=404)


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
