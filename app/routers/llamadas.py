"""Registro de llamadas a un cliente (botón «Registré una llamada»): quién llamó, cómo resultó y una nota corta.

Una llamada cuenta como contacto: un cliente «Sin contactar» pasa a «Primer llamado», y la temperatura se recalcula al instante (una llamada contestada la sube; varias sin respuesta la bajan).
El autor y el cliente salen de la sesión y de la ruta, nunca del cuerpo. Quien no puede usar a ese cliente recibe 404, igual que con las notas.
"""
import logging
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Request, Response

from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.autorizacion import exigir_cliente
from app.core.errors import respuesta_error
from app.services import temperatura as temp

logger = logging.getLogger("wtsp_pita")
router = APIRouter()

NOTA_LLAMADA_MAX = 500
_SELECT = "id,usuario_id,usuario_nombre,resultado,nota,creado_en"


def _publica(f: Dict[str, Any]) -> Dict[str, Any]:
    return {k: f.get(k) for k in ("id", "usuario_id", "usuario_nombre", "resultado", "nota", "creado_en")}


@router.get("/api/clientes/{cliente_id}/llamadas")
async def api_listar_llamadas(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    try:
        filas = await _supabase_request("GET", "/LlamadaCliente", params={"cliente_id": f"eq.{cliente_id}", "select": _SELECT, "order": "creado_en.desc,id.desc", "limit": "100"}) or []
        return [_publica(f) for f in filas]
    except Exception as e:
        return respuesta_error(e, contexto="api_listar_llamadas")


@router.post("/api/clientes/{cliente_id}/llamadas")
async def api_registrar_llamada(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    try:
        body = await request.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    resultado = body.get("resultado")
    if resultado not in temp.RESULTADOS_LLAMADA:
        return Response(content="Elige cómo resultó la llamada: contestó, no contestó, buzón o número incorrecto", status_code=422)
    nota = body.get("nota")
    nota = nota.strip() if isinstance(nota, str) else ""
    if len(nota) > NOTA_LLAMADA_MAX:
        return Response(content=f"La nota es demasiado larga (máximo {NOTA_LLAMADA_MAX} caracteres)", status_code=422)
    try:
        filas = await _supabase_request(
            "POST", "/LlamadaCliente",
            json={"cliente_id": cliente_id, "usuario_id": perfil.get("id"), "usuario_nombre": perfil.get("nombre") or perfil.get("correo") or "Usuario",
                  "resultado": resultado, "nota": nota or None},
            extra_headers={"Prefer": "return=representation"},
        ) or []
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code in (400, 409):          # el cliente no existe (clave foránea)
            return Response(content="Cliente no encontrado", status_code=404)
        return respuesta_error(e, contexto="api_registrar_llamada")
    except Exception as e:
        return respuesta_error(e, contexto="api_registrar_llamada")
    logger.info("Llamada registrada | cliente=%s | usuario=%s | resultado=%s", cliente_id, perfil.get("id"), resultado)      # nunca la nota
    # La llamada cuenta como contacto: «Sin contactar» → «Primer llamado». Lo demás no se toca.
    try:
        c = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": "id,estado_gestion", "limit": "1"}) or []
        if c and (c[0].get("estado_gestion") or "sin_contactar") == "sin_contactar":
            await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente_id}"}, json={"estado_gestion": "primer_llamado"})
    except Exception as e:
        logger.warning("Llamada: no se pudo pasar el cliente %s a «Primer llamado»: %s", cliente_id, e)
    nuevo = await temp.recalcular_cliente(cliente_id)
    return {"llamada": _publica(filas[0] if filas else {}), "temperatura": (nuevo or {}).get("temperatura"), "motivo": (nuevo or {}).get("motivo")}
