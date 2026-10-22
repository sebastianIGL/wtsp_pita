"""Usuarios del CRM (perfil propio, listar, invitar, actualizar), cambio de contraseña, registros de correos y de WhatsApp
enviados, listado de plantillas de WhatsApp aprobadas por Meta y su configuración de variables.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import asyncio
import logging
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual, _invitar_usuario_supabase, cambiar_password_supabase
from app.clients.whatsapp import TOKEN_ACCESO, VERSION_GRAPH, send_whatsapp_template
from app.core.config import get_settings
from app.core.errors import respuesta_error
from app.core.http import http_client
from app.core.permisos import _solo_admin, _solo_owner
from app.core.user_cache import user_cache
from app.core.utilidades import _normalize_phone
from app.services.plantillas import TEMPLATE_VARS_MAP
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Response
from typing import Any, Dict

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


WA_WABA_ID         = get_settings().wa_waba_id


async def _log_correo(tipo: str, destinatario: str, asunto: str, estado: str, detalle_error: str = None, usuario_id: str = None):
    try:
        await _supabase_request("POST", "/log_correos", json={
            "tipo": tipo, "destinatario": destinatario, "asunto": asunto,
            "estado": estado, "detalle_error": detalle_error, "usuario_id": usuario_id,
        })
    except Exception as e:
        logger.warning("No se pudo guardar log de correo: %s", e)


@router.get("/api/usuarios/me")
async def api_usuario_actual(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    return perfil


@router.get("/api/usuarios")
async def api_listar_usuarios(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request("GET", "/Usuario", params={"select": "*", "order": "created_at.desc"})
    return rows or []


@router.post("/api/usuarios")
async def api_crear_usuario(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body    = await request.json()
        nombre  = (body.get("nombre") or "").strip()
        rut     = (body.get("rut") or "").strip()
        correo  = (body.get("correo") or "").strip()
        celular      = (body.get("celular") or "").strip() or None
        rol          = body.get("rol", "usuario")
        email_alias  = (body.get("email_alias") or "").strip() or correo
        if not nombre or not rut or not correo:
            return Response(content="Faltan campos obligatorios: nombre, rut, correo", status_code=400)
        roles_permitidos = ("ejecutivo", "administrador") if _solo_owner(perfil) else ("ejecutivo",)
        if rol not in roles_permitidos:
            return Response(content=f"rol debe ser uno de: {', '.join(roles_permitidos)}", status_code=400)
        inmobiliaria_ids = body.get("inmobiliaria_ids") or []
        proyecto_ids     = body.get("proyecto_ids") or []
        user_id = await _invitar_usuario_supabase(correo, nombre, rol)
        await _supabase_request("POST", "/Usuario", json={
            "id": user_id, "nombre": nombre, "rut": rut, "correo": correo,
            "celular": celular, "rol": rol, "password_provisional": False,
            "email_alias": email_alias,
            "inmobiliaria_ids": inmobiliaria_ids,
            "proyecto_ids": proyecto_ids,
        })
        asyncio.create_task(_log_correo("invitacion", correo, "Invitación al CRM", "enviado", usuario_id=user_id))
        if celular:
            cel_norm = _normalize_phone(celular)
            if cel_norm:
                asyncio.create_task(send_whatsapp_template(
                    to=cel_norm,
                    template_name="recordatorio_wtsp",
                    language_code="es_CL",
                    body_text_params=[nombre, "Bienvenida al CRM", "QueSubsidio CRM"],
                ))
        return {"ok": True, "usuario_id": user_id}
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_usuario")


@router.get("/api/log-correos")
async def api_log_correos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request(
        "GET", "/log_correos",
        params={"select": "*", "order": "created_at.desc", "limit": "100"},
    )
    return rows or []


@router.get("/api/log-whatsapp")
async def api_log_whatsapp(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request(
        "GET", "/LogWhatsapp",
        params={"select": "*,Cliente(Contacto,Telefono)", "order": "created_at.desc", "limit": "100"},
    )
    return rows or []


@router.patch("/api/usuarios/{usuario_id}")
async def api_actualizar_usuario(usuario_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body   = await request.json()
        update = {k: body[k] for k in ("nombre", "celular", "rol", "estado", "email_alias", "inmobiliaria_ids", "proyecto_ids") if k in body}
        if not update:
            return Response(content="Nada que actualizar", status_code=400)

        # El rol "owner" es intocable: no se puede quitar a un owner ni asignárselo a otro por acá
        if "rol" in update:
            actuales = await _supabase_request("GET", "/Usuario",
                params={"id": f"eq.{usuario_id}", "select": "rol", "limit": "1"}) or []
            rol_actual = actuales[0].get("rol") if actuales else None
            if rol_actual == "owner" or update["rol"] == "owner":
                update.pop("rol")
                if not update:
                    return Response(content="El rol Owner no se puede modificar", status_code=400)

        try:
            await _supabase_request("PATCH", "/Usuario", params={"id": f"eq.{usuario_id}"}, json=update)
        finally:
            # aunque el PATCH falle a medias, no se deja un perfil anterior en caché
            user_cache.invalidar_usuario(usuario_id)
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_actualizar_usuario")


@router.post("/api/auth/cambiar-password")
async def api_cambiar_password(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        body  = await request.json()
        nueva = (body.get("nueva_password") or "").strip()
        if len(nueva) < 8:
            return Response(content="La contraseña debe tener al menos 8 caracteres", status_code=400)
        token    = request.headers.get("Authorization", "")[7:]
        await cambiar_password_supabase(token, nueva)
        try:
            await _supabase_request("PATCH", "/Usuario",
                params={"id": f"eq.{perfil['id']}"},
                json={"password_provisional": False})
        finally:
            user_cache.invalidar_usuario(perfil["id"])
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_cambiar_password")


@router.get("/api/templates")
async def api_listar_templates(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not WA_WABA_ID:
        return Response(content="WA_WABA_ID no configurado en el servidor", status_code=500)
    try:
        async with http_client(20) as client:
            r = await client.get(
                f"https://graph.facebook.com/{VERSION_GRAPH}/{WA_WABA_ID}/message_templates",
                params={"status": "APPROVED", "limit": "100"},
                headers={"Authorization": f"Bearer {TOKEN_ACCESO}"},
            )
            r.raise_for_status()
        return {
            "templates": [
                {"name": t["name"], "language": t.get("language", "es"), "category": t.get("category", ""), "components": t.get("components", [])}
                for t in r.json().get("data", [])
            ]
        }
    except Exception as e:
        return respuesta_error(e, contexto="api_listar_templates")


@router.get("/api/plantillas-config")
async def api_listar_plantillas_config(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Forbidden", status_code=403)
    rows = await _supabase_request("GET", "/PlantillaConfig", params={"select": "*"}) or []
    # Combinar hardcoded (fallback) con lo que hay en DB; DB tiene prioridad
    merged: Dict[str, Dict] = {
        name: {"template_name": name, "variables": vars_list, "source": "default"}
        for name, vars_list in TEMPLATE_VARS_MAP.items()
    }
    for row in rows:
        merged[row["template_name"]] = {**row, "source": "db"}
    return list(merged.values())


@router.put("/api/plantillas-config/{template_name}")
async def api_upsert_plantilla_config(template_name: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Forbidden", status_code=403)
    body = await request.json()
    variables   = body.get("variables", [])
    param_names = body.get("param_names") or None  # nombres de parámetros nombrados (ej: ["nombre_cliente"])
    if not isinstance(variables, list):
        return Response(content="variables debe ser una lista", status_code=400)
    now_iso = datetime.now(timezone.utc).isoformat()
    patch_data: Dict[str, Any] = {"variables": variables, "updated_at": now_iso}
    if param_names is not None:
        patch_data["param_names"] = param_names
    existing = await _supabase_request(
        "GET", "/PlantillaConfig",
        params={"template_name": f"eq.{template_name}", "select": "id", "limit": "1"},
    ) or []
    if existing:
        await _supabase_request("PATCH", "/PlantillaConfig",
            params={"template_name": f"eq.{template_name}"}, json=patch_data)
    else:
        await _supabase_request("POST", "/PlantillaConfig",
            json={"template_name": template_name, **patch_data})
    return {"ok": True}
