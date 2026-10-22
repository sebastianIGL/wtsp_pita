"""Integración con Movendo / Zonapropia: recibir un lead nuevo (con plantilla de bienvenida automática y aviso al
administrador) y actualizar el contacto con el resumen de la conversación. Se autentican con la clave Bearer de Movendo.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import asyncio
import logging
from app.clients.supabase import _supabase_request
from app.clients.whatsapp import _build_body_params, send_whatsapp_template
from app.core.config import get_settings
from app.core.errors import respuesta_error
from app.core.utilidades import _normalizar_telefono_cl, _utc_now_iso
from app.services.avisos import _notificar_nuevo_lead
from app.services.plantillas import _pool_plantilla
from app.services.prospectos import upsert_prospecto
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Response
from typing import Any, Dict, Optional

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


def _movendo_webhook_key() -> Optional[str]:
    return get_settings().movendo_webhook_key


def _verificar_movendo_auth(request: Request) -> bool:
    key = _movendo_webhook_key()
    if not key:
        return True  # Sin clave configurada → aceptar (desarrollo)
    auth = request.headers.get("Authorization", "")
    return auth.lower().startswith("bearer ") and auth[7:].strip() == key


@router.post("/api/movendo/nuevo-cliente")
async def api_movendo_nuevo_cliente(request: Request):
    """Recibe un nuevo lead de Movendo y lo registra como Cliente."""
    if not _verificar_movendo_auth(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()

        first     = (body.get("firstName") or "").strip()
        last      = (body.get("lastName") or "").strip()
        nombre    = f"{first} {last}".strip() or (body.get("phone") or "Sin nombre")
        phone_raw = (body.get("phone") or "").strip()
        crm_id      = str(body.get("crmId") or "").strip() or None
        proj_name   = (body.get("projectName") or "").strip()
        movendo_pid = body.get("projectId")  # ID numérico del proyecto en Movendo
        email       = (body.get("email") or "").strip() or None
        rut         = (body.get("rut") or "").strip() or None
        salary      = str(body.get("salary") or "").strip() or None
        source      = (body.get("source") or "").strip() or None

        if not phone_raw:
            return Response(content="phone es requerido", status_code=400)
        telefono = _normalizar_telefono_cl(phone_raw)
        if not telefono:
            return Response(content=f"Teléfono inválido: {phone_raw}", status_code=400)
        if not proj_name and movendo_pid is None:
            return Response(content="projectName o projectId es requerido", status_code=400)

        # Buscar proyecto — orden de prioridad:
        # 1) ID numérico de Movendo (movendo_proyecto_id)
        # 2) nombre ilike
        # 3) codigo ilike
        # 4) prefijo o alias en nombres_csv (maneja "Viñedos de Rengo II" → "Viñedos de Rengo")
        _sel_proy = "id,nombre,imagen_url,nombres_csv"
        proyectos = []

        if movendo_pid is not None:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"movendo_proyecto_id": f"eq.{movendo_pid}", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"nombre": f"ilike.%{proj_name}%", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"codigo": f"ilike.%{proj_name}%", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            todos = await _supabase_request("GET", "/Proyecto", params={"select": _sel_proy}) or []
            proj_lower = proj_name.lower().strip()
            todos_sorted = sorted(todos, key=lambda p: len(p.get("nombre") or ""), reverse=True)
            for p in todos_sorted:
                db_nombre = (p.get("nombre") or "").lower().strip()
                # nombres_csv puede ser lista (array PG) o string separado por comas
                raw_csv = p.get("nombres_csv") or []
                if isinstance(raw_csv, list):
                    aliases = [a.strip().lower() for a in raw_csv if a.strip()]
                else:
                    aliases = [a.strip().lower() for a in str(raw_csv).split(",") if a.strip()]
                if proj_lower in aliases:
                    proyectos = [p]; break
                if db_nombre and proj_lower.startswith(db_nombre):
                    proyectos = [p]; break

        if not proyectos:
            ref = f"projectId={movendo_pid}" if movendo_pid is not None else f"projectName={proj_name}"
            return Response(content=f"Proyecto no encontrado: {ref}", status_code=422,
                            media_type="text/plain")

        proyecto_row      = proyectos[0]
        proyecto_id       = proyecto_row["id"]
        proyecto_nombre   = proyecto_row["nombre"]
        template_auto     = proyecto_row.get("template_bienvenida") or None

        # Evitar duplicados (mismo teléfono + mismo proyecto)
        dup = await _supabase_request("GET", "/Cliente",
            params={"Telefono": f"eq.{telefono}", "proyecto_id": f"eq.{proyecto_id}",
                    "select": "id", "limit": "1"}) or []
        if dup:
            return {"ok": True, "duplicado": True, "cliente_id": dup[0]["id"],
                    "mensaje": "Cliente ya existe en este proyecto"}

        default_user = await _get_default_user()
        payload: Dict[str, Any] = {
            "proyecto_id":        proyecto_id,
            "Contacto":           nombre,
            "Telefono":           telefono,
            "Fecha Ult. Gestión": _utc_now_iso(),
            "primer mensaje":     False,
            "es_nuevo":           True,
        }
        if default_user: payload["usuario_id"]      = default_user["id"]
        if email:       payload["Correo"]           = email
        if rut:         payload["Rut"]              = rut
        if salary:      payload["Tramo de renta"]   = salary
        if crm_id:      payload["movendo_id"]       = crm_id
        if source:      payload["movendo_source"]   = source

        cliente = await _supabase_request("POST", "/Cliente", json=payload,
            extra_headers={"Prefer": "return=representation"})
        cliente_id = cliente[0]["id"] if isinstance(cliente, list) and cliente else None

        # Auto-enviar plantilla de bienvenida si el proyecto la tiene configurada
        wa_result = None
        if template_auto and cliente_id:
            try:
                # Construir parámetros usando PlantillaConfig (soporta named params)
                _wa_cfg = await _supabase_request("GET", "/PlantillaConfig",
                    params={"template_name": f"eq.{template_auto}",
                            "select": "variables,param_names", "limit": "1"}) or []
                _pool = await _pool_plantilla(nombre, proyecto_row)
                if _wa_cfg and _wa_cfg[0].get("variables"):
                    _btp = _build_body_params(_pool, _wa_cfg[0]["variables"], _wa_cfg[0].get("param_names"))
                else:
                    _btp = [nombre, proyecto_nombre]  # fallback básico
                wa_result = await send_whatsapp_template(
                    to=telefono,
                    template_name=template_auto,
                    language_code="es",
                    body_text_params=_btp,
                    image_url=proyecto_row.get("imagen_url") or None,
                )
                ahora_movendo = datetime.now(timezone.utc)
                patch_mov: Dict[str, Any] = {
                    "primer_wtsp_en": ahora_movendo.isoformat(),
                    "Fecha Ult. Gestión": ahora_movendo.isoformat(),
                }
                patch_mov["recordatorio_at"] = (ahora_movendo + timedelta(hours=24)).isoformat()
                await _supabase_request("PATCH", "/Cliente",
                    params={"id": f"eq.{cliente_id}"}, json=patch_mov)
                await upsert_prospecto(
                    telefono_e164=telefono,
                    nombre=nombre,
                    rut=rut,
                    rango_sueldo=salary,
                    proyecto_id=proyecto_id,
                    estado="PLANTILLA_ENVIADA",
                    paso="BIENVENIDA",
                    cliente_id=cliente_id,
                )
                logger.info(f"Movendo auto-template '{template_auto}' enviado a {telefono}")
            except Exception as wa_err:
                logger.warning(f"Movendo: cliente creado pero fallo envío template: {wa_err}")

        logger.info(f"Movendo nuevo cliente: {nombre} ({telefono}) → {proyecto_nombre}")
        asyncio.create_task(_notificar_nuevo_lead(
            nombre=nombre, telefono=telefono, proyecto_nombre=proyecto_nombre,
            correo_admin=default_user.get("correo") if default_user else None,
        ))
        return {"ok": True, "cliente_id": cliente_id}

    except Exception as e:
        return respuesta_error(e, contexto="api_movendo_nuevo_cliente")


@router.post("/api/aplicaciones/actualizar-contactos-quesubsidio")
async def api_movendo_actualizar_contacto(request: Request):
    """Recibe el resumen de conversación de Movendo y actualiza el Cliente."""
    if not _verificar_movendo_auth(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        lead_id = body.get("leadId")
        if not lead_id:
            return Response(content="leadId es requerido", status_code=400)
        lead_id_str = str(lead_id)

        # Buscar por movendo_id
        rows = await _supabase_request("GET", "/Cliente",
            params={"movendo_id": f"eq.{lead_id_str}", "select": "id", "limit": "1"}) or []

        # Si no encontró, intentar por teléfono
        if not rows and body.get("phone"):
            tel = _normalizar_telefono_cl(str(body["phone"]))
            if tel:
                rows = await _supabase_request("GET", "/Cliente",
                    params={"Telefono": f"eq.{tel}", "select": "id", "limit": "1"}) or []

        if not rows:
            return Response(content=f"Cliente no encontrado para leadId={lead_id}", status_code=404,
                            media_type="text/plain")

        cliente_id = rows[0]["id"]
        patch: Dict[str, Any] = {"movendo_id": lead_id_str}

        nombre_parts = [body.get("name") or "", body.get("lastName") or "", body.get("motherLastName") or ""]
        nombre = " ".join(p for p in nombre_parts if p).strip()
        if nombre:             patch["Contacto"]        = nombre
        if body.get("email"):  patch["Correo"]          = body["email"]
        if body.get("rut"):    patch["Rut"]             = body["rut"]
        if body.get("salary"): patch["Tramo de renta"]  = str(body["salary"])

        movendo_data: Dict[str, Any] = {}
        for key in ("summary", "chatLink", "score", "userNotes", "source",
                    "alertType", "alertMessage", "complementSalary", "projectId"):
            if body.get(key) is not None:
                movendo_data[key] = body[key]
        if movendo_data:
            patch["movendo_data"] = movendo_data

        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json=patch, extra_headers={"Prefer": "return=minimal"})

        logger.info(f"Movendo actualizó cliente_id={cliente_id} leadId={lead_id}")
        return {"ok": True, "cliente_id": cliente_id}

    except Exception as e:
        return respuesta_error(e, contexto="api_movendo_actualizar_contacto")


async def _get_default_user() -> Optional[Dict]:
    """Devuelve el usuario admin por defecto {id, correo, nombre}."""
    env_id = get_settings().movendo_default_user_id
    params = {"select": "id,correo,nombre", "limit": "1"}
    if env_id:
        params["id"] = f"eq.{env_id}"
    else:
        params["rol"]    = "in.(owner,administrador)"
        params["estado"] = "eq.1"
    rows = await _supabase_request("GET", "/Usuario", params=params) or []
    return rows[0] if rows else None
