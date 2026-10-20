"""Mensajes de la conversación con el prospecto: guardar un mensaje (entrante, saliente o registro de un archivo) y leer los
últimos para dárselos de contexto al bot. Un ``wa_id_mensaje`` repetido lo rechaza el índice único de la base y se trata como
mensaje duplicado de Meta, no como error.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote C2).
"""
from typing import Any, Dict, List, Optional

import httpx

from app.clients.supabase import _supabase_request
from app.core.utilidades import _es_violacion_unica, _normalizar_wamid


async def insertar_mensaje(
    *,
    prospecto_id: str,
    direccion: str,
    text: Optional[str],
    wa_message_id: Optional[str] = None,
    cliente_id: Optional[int] = None,
):
    row: Dict[str, Any] = {
        "prospecto_id": prospecto_id,
        "direccion":    direccion,
        "texto":        text,
        "wa_id_mensaje": _normalizar_wamid(wa_message_id),
    }
    if cliente_id is not None:
        row["cliente_id"] = cliente_id
    try:
        await _supabase_request("POST", "/Mensaje", json=row)
    except httpx.HTTPStatusError as e:
        # Con el índice único sobre Mensaje.wa_id_mensaje, un wamid repetido es un mensaje duplicado de Meta:
        # no es un error. Cualquier otro fallo (incluida una violación distinta, como una clave foránea) sigue lanzándose.
        if _es_violacion_unica(e):
            return False
        raise
    return True


async def obtener_historial_mensajes(prospecto_id: str, limite: int = 12) -> List[Dict]:
    rows = await _supabase_request(
        "GET",
        "/Mensaje",
        params={
            "prospecto_id": f"eq.{prospecto_id}",
            "order": "creado_en.desc",
            "limit": str(limite),
            "select": "direccion,texto",
        },
    )
    return list(reversed(rows or []))
