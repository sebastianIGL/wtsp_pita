"""Formulario de perfilamiento público (sin sesión): crear, leer y actualizar por token, y convertir un formulario completado
en cliente del CRM (esta última sí exige sesión).

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import logging
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.cuerpo_opcional import _cuerpo_opcional_ilegible
from app.core.errors import respuesta_error
from app.core.utilidades import _normalize_phone, _utc_now_iso
from app.services.prospectos import upsert_prospecto
from fastapi import APIRouter, Request, Response
from typing import Any, Dict

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


# ── Formulario de perfilamiento público (sin auth) ───────────────────────────
_FORMULARIO_CAMPOS = (
    "nombre", "email", "telefono", "rsh", "cmf", "tipo_ingreso",
    "antiguedad_meses", "tiene_liquidaciones", "tiene_cotizaciones_afp",
    "anos_declaracion_renta", "tiene_carpeta_tributaria",
    "tiene_boletas_honorarios", "tiene_cert_antiguedad",
    "tiene_propiedad", "numero_integrantes",
    "resultado", "paso_actual", "completado_at",
)


@router.post("/api/formulario")
async def api_crear_formulario(request: Request):
    body = await request.json()
    payload = {k: v for k, v in body.items() if k in _FORMULARIO_CAMPOS and v is not None}
    try:
        row = await _supabase_request(
            "POST", "/Formulario",
            json=payload,
            extra_headers={"Prefer": "return=representation"},
        )
        return row[0] if isinstance(row, list) and row else row
    except Exception as exc:
        logger.warning("Error creando Formulario: %s", exc)
        return Response(content="Error al crear formulario", status_code=500)


@router.get("/api/formulario/{token}")
async def api_obtener_formulario(token: str):
    try:
        rows = await _supabase_request(
            "GET", "/Formulario",
            params={"token": f"eq.{token}", "select": "*"},
        )
        if not rows:
            return Response(content="No encontrado", status_code=404)
        return rows[0]
    except Exception:
        return Response(content="Error", status_code=500)


@router.patch("/api/formulario/{token}")
async def api_actualizar_formulario(token: str, request: Request):
    body = await request.json()
    payload = {k: v for k, v in body.items() if k in _FORMULARIO_CAMPOS}
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    try:
        await _supabase_request(
            "PATCH", "/Formulario",
            params={"token": f"eq.{token}"},
            json=payload,
            extra_headers={"Prefer": "return=minimal"},
        )
        return {"ok": True}
    except Exception as exc:
        logger.warning("Error actualizando Formulario %s: %s", token, exc)
        return Response(content="Error al actualizar", status_code=500)


@router.post("/api/formulario/{token}/convertir")
async def api_convertir_formulario(token: str, request: Request):
    """Convierte un Formulario completado en un Cliente CRM."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        rows = await _supabase_request(
            "GET", "/Formulario",
            params={"token": f"eq.{token}", "select": "*"},
        )
        if not rows:
            return Response(content="Formulario no encontrado", status_code=404)
        f = rows[0]

        if f.get("resultado") == "convertido":
            return Response(content="Este formulario ya fue convertido a cliente", status_code=409)

        nombre   = (f.get("nombre") or "").strip()
        telefono = _normalize_phone(f.get("telefono") or "")
        if not nombre:
            return Response(content="El formulario no tiene nombre registrado", status_code=400)

        body: Dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception as e_body:
            _cuerpo_opcional_ilegible(request, "api_convertir_formulario", e_body)

        proyecto_id = body.get("proyecto_id") or f.get("proyecto_id")
        usuario_id  = body.get("usuario_id") or perfil.get("id")

        cliente_payload: Dict[str, Any] = {
            "Contacto":    nombre,
            "Telefono":    telefono or f.get("telefono"),
            "email":       f.get("email"),
            "rsh":         f.get("rsh"),
            "tipo_ingreso": f.get("tipo_ingreso"),
            "proyecto_id": proyecto_id,
            "usuario_id":  usuario_id,
            "primer mensaje": True,
            "wtsp_habilitado": bool(telefono),
        }
        cliente_payload = {k: v for k, v in cliente_payload.items() if v is not None}

        nuevo = await _supabase_request(
            "POST", "/Cliente",
            json=cliente_payload,
            extra_headers={"Prefer": "return=representation"},
        )
        if not nuevo:
            return Response(content="Error al crear cliente", status_code=500)
        cliente    = nuevo[0] if isinstance(nuevo, list) else nuevo
        cliente_id = cliente.get("id")

        if telefono and proyecto_id:
            await upsert_prospecto(
                telefono_e164=telefono,
                nombre=nombre,
                proyecto_id=str(proyecto_id),
                estado="NUEVO",
                paso="INICIO",
                cliente_id=cliente_id,
                numero_integrantes=f.get("numero_integrantes"),
            )

        await _supabase_request(
            "PATCH", "/Formulario",
            params={"token": f"eq.{token}"},
            json={"resultado": "convertido", "completado_at": _utc_now_iso()},
            extra_headers={"Prefer": "return=minimal"},
        )

        return {"ok": True, "cliente_id": cliente_id, "cliente": cliente}
    except Exception as e:
        return respuesta_error(e, contexto="api_convertir_formulario")
