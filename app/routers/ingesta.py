"""Ingesta de prospectos desde sistemas externos (POST /prospectos/ingesta): exige la clave de API en X-API-Key cuando está
configurada, valida teléfono y proyecto y deja al prospecto registrado.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import logging
from app.clients.supabase import _supabase_service_role_key, _supabase_url
from app.core.config import get_settings
from app.core.errors import respuesta_error
from app.core.utilidades import _normalize_phone
from app.services.mensajes import insertar_mensaje
from app.services.prospectos import upsert_prospecto
from app.services.proyectos import obtener_proyecto_por_id
from fastapi import APIRouter, Request, Response
from typing import Optional

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


def _ingest_api_key() -> Optional[str]:
    return get_settings().ingest_api_key


@router.post("/prospectos/ingesta")
async def ingestar_prospecto(request: Request):
    try:
        ingest_api_key = _ingest_api_key()
        if ingest_api_key:
            provided = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
            if not provided or provided != ingest_api_key:
                return Response(content="Unauthorized", status_code=401)

        if not _supabase_url() or not _supabase_service_role_key():
            return Response(content="Supabase no está configurado", status_code=500)

        body = await request.json()
        phone = _normalize_phone(
            body.get("telefono_e164") or body.get("phone_e164")
            or body.get("telefono") or body.get("phone") or ""
        )
        nombre          = (body.get("nombre") or body.get("first_name") or "").strip() or None
        rut             = (body.get("rut") or "").strip() or None
        rango_sueldo    = (body.get("rango_sueldo") or "").strip() or None
        proyecto_id = (body.get("proyecto_id") or body.get("project_id") or "").strip() or None

        if not phone:
            return Response(content="Falta telefono_e164", status_code=400)
        if not proyecto_id:
            return Response(content="Falta proyecto_id", status_code=400)

        proyecto = await obtener_proyecto_por_id(proyecto_id)
        if not proyecto:
            return Response(content="proyecto_id no existe en proyectos", status_code=400)

        prospecto = await upsert_prospecto(
            telefono_e164=phone,
            nombre=nombre,
            rut=rut,
            rango_sueldo=rango_sueldo,
            proyecto_id=proyecto_id,
            estado="PLANTILLA_ENVIADA",
            paso="BIENVENIDA",
        )

        # Auto-envío deshabilitado: nombre_plantilla eliminado (rediseño pendiente)
        wa_resp = None
        logger.warning("Auto-envío de plantilla omitido — pendiente rediseño de flujo")

        if prospecto and prospecto.get("id"):
            await insertar_mensaje(
                prospecto_id=prospecto["id"],
                direccion="saliente",
                text="[PLANTILLA] pendiente selección manual",
                wa_message_id=(
                    (wa_resp or {}).get("messages", [{}])[0].get("id")
                    if isinstance(wa_resp, dict) else None
                ),
            )

        return {"ok": True, "prospecto_id": (prospecto or {}).get("id")}

    except Exception as e:
        return respuesta_error(e, contexto="ingestar_prospecto")
