"""Clientes del CRM (listar, crear, actualizar), inbox de prospectos que aún no son clientes (listar y registrar como
cliente) y conversaciones y contexto del bot de un cliente o prospecto.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import logging
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.autorizacion import exigir_cliente, exigir_prospecto
from app.core.errors import respuesta_error
from app.core.permisos import _solo_admin
from app.core.utilidades import _normalize_phone
from app.services.documentos import _CAMPOS_CALIFICACION
from app.services.prospectos import upsert_prospecto
from app.services.proyectos import obtener_proyecto_por_id
from fastapi import APIRouter, Request, Response
from typing import Any, Dict, List

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


@router.get("/api/clientes")
async def api_listar_clientes(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    ver_archivados  = request.query_params.get("archivado") == "true"
    params: Dict[str, str] = {
        "select": "*", "order": "id.desc",
        "archivado": "is.true" if ver_archivados else "not.is.true",
    }

    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    elif inmobiliaria_id:
        proyectos = await _supabase_request(
            "GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"},
        ) or []
        ids = ",".join(p["id"] for p in proyectos)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"
    elif empresa_id:
        inmobiliarias = await _supabase_request(
            "GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"},
        ) or []
        inm_ids = ",".join(str(i["id"]) for i in inmobiliarias)
        if not inm_ids:
            return []
        proyectos = await _supabase_request(
            "GET", "/Proyecto",
            params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"},
        ) or []
        ids = ",".join(p["id"] for p in proyectos)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"

    usuario_filtro = request.query_params.get("usuario_id")
    if not _solo_admin(perfil):
        params["usuario_id"] = f"eq.{perfil['id']}"
    elif usuario_filtro:
        params["usuario_id"] = f"eq.{usuario_filtro}"
    rows = await _supabase_request("GET", "/Cliente", params=params) or []
    # Merge datos de Prospecto para ordenar por actividad WA y mostrar estado
    if rows:
        telefonos = [r["Telefono"] for r in rows if r.get("Telefono")]
        prospectos: List[Dict] = []
        LOTE_TELEFONOS = 250
        for i in range(0, len(telefonos), LOTE_TELEFONOS):
            tel_list = ",".join(telefonos[i:i + LOTE_TELEFONOS])
            if tel_list:
                prospectos += await _supabase_request("GET", "/Prospecto",
                    params={"telefono_e164": f"in.({tel_list})",
                            "select": "telefono_e164,ultimo_entrante_en,pendiente_respuesta,estado,paso"}) or []
        if prospectos:
            prosp_map = {p["telefono_e164"]: p for p in prospectos}
            for r in rows:
                p = prosp_map.get(r.get("Telefono"), {})
                r["ultimo_entrante_en"]  = p.get("ultimo_entrante_en")
                r["pendiente_respuesta"] = p.get("pendiente_respuesta")
                r["prospecto_estado"]    = p.get("estado")
                r["prospecto_paso"]      = p.get("paso")
    return rows


@router.post("/api/clientes")
async def api_crear_cliente(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()

        nombre      = (body.get("Contacto") or "").strip()
        telefono    = _normalize_phone(body.get("Telefono") or "")
        proyecto_id = (body.get("proyecto_id") or "").strip()
        rut         = (body.get("Rut") or "").strip() or None
        correo      = (body.get("Correo") or "").strip() or None
        rango             = (body.get("Tramo de renta") or "").strip() or None
        num_integrantes   = body.get("numero_integrantes")
        num_integrantes   = int(num_integrantes) if num_integrantes else None
        primer_msg        = bool(body.get("primer mensaje", True))

        if not nombre:
            return Response(content="Falta Contacto", status_code=400)
        if not telefono:
            return Response(content="Falta Telefono", status_code=400)
        if not proyecto_id:
            return Response(content="Falta proyecto_id", status_code=400)

        proyecto = await obtener_proyecto_por_id(proyecto_id)
        if not proyecto:
            return Response(content="Proyecto no encontrado", status_code=400)

        # Evitar duplicados: mismo teléfono + mismo proyecto
        existente = await _supabase_request(
            "GET", "/Cliente",
            params={
                "Telefono":    f"eq.{telefono}",
                "proyecto_id": f"eq.{proyecto_id}",
                "select":      "id",
                "limit":       "1",
            },
        )
        if existente:
            return Response(
                content=f"Ya existe un cliente con ese teléfono en ese proyecto",
                status_code=409,
                media_type="text/plain",
            )

        from datetime import datetime, timezone
        fecha_hoy = datetime.now(timezone.utc).isoformat()

        cliente = await _supabase_request(
            "POST", "/Cliente",
            json={
                "proyecto_id":        proyecto_id,
                "Contacto":           nombre,
                "Rut":                rut or "",
                "Correo":             correo,
                "Telefono":           telefono,
                "Tramo de renta":     rango,
                "numero_integrantes": num_integrantes,
                "etapa_tipologia_id":  int(body["etapa_tipologia_id"]) if body.get("etapa_tipologia_id") else None,
                "ahorro_uf":          float(body["ahorro_uf"]) if body.get("ahorro_uf") is not None else None,
                "primer mensaje":     primer_msg,
                "Fecha Ult. Gestión": body.get("Fecha Ult. Gestión") or fecha_hoy,
                "usuario_id":         perfil["id"],
            },
            extra_headers={"Prefer": "return=representation"},
        )

        cliente_id_nuevo = None
        if isinstance(cliente, list) and cliente:
            cliente_id_nuevo = cliente[0].get("id")

        wa_result = None
        if primer_msg:
            await upsert_prospecto(
                telefono_e164=telefono,
                nombre=nombre,
                rut=rut,
                rango_sueldo=rango,
                proyecto_id=proyecto_id,
                estado="PLANTILLA_ENVIADA",
                paso="BIENVENIDA",
                cliente_id=cliente_id_nuevo,
            )
            logger.warning(f"Auto-envío omitido para proyecto {proyecto_id} — pendiente rediseño")

        return {"ok": True, "cliente": cliente, "wa": wa_result}

    except Exception as e:
        return respuesta_error(e, contexto="api_crear_cliente")


@router.patch("/api/clientes/{cliente_id}")
async def api_actualizar_cliente(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    _CAMPOS_PERMITIDOS = {"recordatorio_at", "Contacto", "Correo", "email", "Tramo de renta", "Rut", "es_nuevo", "numero_integrantes", "proyecto_id", "ahorro_uf", "etapa_tipologia_id", "estado_gestion"}
    if _solo_admin(perfil):
        _CAMPOS_PERMITIDOS = _CAMPOS_PERMITIDOS | {"usuario_id", "archivado"}
    try:
        body = await request.json()
        payload = {k: v for k, v in body.items() if k in _CAMPOS_PERMITIDOS}
        if not payload:
            return Response(content="Sin campos válidos", status_code=400)
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json=payload,
        )
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_actualizar_cliente")


_INBOX_SELECT = (
    "id,telefono_e164,nombre,rut,rango_sueldo,numero_integrantes,"
    "proyecto_id,Proyecto(nombre),"
    "paso,estado,ultimo_texto_entrante,ultimo_entrante_en,actualizado_en"
)


@router.get("/api/inbox")
async def api_inbox(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)

    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")

    params: Dict[str, str] = {
        "select":     _INBOX_SELECT,
        "cliente_id": "is.null",
        "opt_out":    "neq.true",
        "order":      "ultimo_entrante_en.desc.nullslast",
        "limit":      "200",
    }

    if proyecto_id:
        params["or"] = f"(proyecto_id.eq.{proyecto_id},proyecto_id.is.null)"
    elif inmobiliaria_id:
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"}) or []
        ids = ",".join(str(p["id"]) for p in prows)
        if ids:
            params["or"] = f"(proyecto_id.in.({ids}),proyecto_id.is.null)"
        # si no hay proyectos, igual devolvemos los sin proyecto
    elif empresa_id:
        inms = await _supabase_request("GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
        inm_ids = ",".join(str(i["id"]) for i in inms)
        if inm_ids:
            prows = await _supabase_request("GET", "/Proyecto",
                params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"}) or []
            ids = ",".join(str(p["id"]) for p in prows)
            if ids:
                params["or"] = f"(proyecto_id.in.({ids}),proyecto_id.is.null)"

    rows = await _supabase_request("GET", "/Prospecto", params=params) or []
    return rows


@router.post("/api/inbox/{prospecto_id}/registrar")
async def api_registrar_desde_inbox(prospecto_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        # Obtener el prospecto
        prospectos = await _supabase_request("GET", "/Prospecto",
            params={"id": f"eq.{prospecto_id}", "select": "*", "limit": "1"}) or []
        if not prospectos:
            return Response(content="Prospecto no encontrado", status_code=404)
        p = prospectos[0]
        if p.get("cliente_id"):
            return Response(content="Ya tiene un cliente vinculado", status_code=409)

        nombre = (body.get("nombre") or p.get("nombre") or "").strip()
        if not nombre:
            return Response(content="El nombre es obligatorio", status_code=400)
        telefono = p.get("telefono_e164") or ""
        if not telefono:
            return Response(content="El prospecto no tiene teléfono", status_code=400)

        cliente_payload: Dict[str, Any] = {
            "Contacto":           nombre,
            "Telefono":           telefono,
            "Rut":                body.get("rut")      or p.get("rut")      or None,
            "Correo":             body.get("correo")   or None,
            "Tramo de renta":     body.get("rango_sueldo") or p.get("rango_sueldo") or None,
            "numero_integrantes": body.get("numero_integrantes") or p.get("numero_integrantes") or None,
            "proyecto_id":        body.get("proyecto_id") or p.get("proyecto_id") or None,
            "usuario_id":         perfil["id"],
            "es_nuevo":           True,
            "primer mensaje":     False,
        }

        nuevo = await _supabase_request("POST", "/Cliente",
            json=cliente_payload,
            extra_headers={"Prefer": "return=representation"})
        cliente_id = nuevo[0]["id"] if isinstance(nuevo, list) and nuevo else None
        if not cliente_id:
            return Response(content="Error creando cliente", status_code=500)

        await _supabase_request("PATCH", "/Prospecto",
            params={"id": f"eq.{prospecto_id}"},
            json={"cliente_id": cliente_id},
            extra_headers={"Prefer": "return=minimal"})

        return {"ok": True, "cliente_id": cliente_id}
    except Exception as e:
        return respuesta_error(e, contexto="api_registrar_desde_inbox")


@router.get("/api/clientes/{cliente_id}/conversacion")
async def api_conversacion_cliente(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    prospectos = await _supabase_request(
        "GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": "id,paso,estado", "limit": "1"},
    )
    if not prospectos:
        return {"mensajes": [], "paso": None, "estado": None}
    p = prospectos[0]
    mensajes = await _supabase_request(
        "GET", "/Mensaje",
        params={
            "prospecto_id": f"eq.{p['id']}",
            "select":       "id,direccion,texto,creado_en",
            "order":        "creado_en.asc",
            "limit":        "200",
        },
    ) or []
    return {"mensajes": mensajes, "paso": p.get("paso"), "estado": p.get("estado")}


@router.get("/api/prospectos/{prospecto_id}/conversacion")
async def api_conversacion_prospecto(prospecto_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_prospecto(perfil, prospecto_id)
    if denegado:
        return denegado
    mensajes = await _supabase_request(
        "GET", "/Mensaje",
        params={
            "prospecto_id": f"eq.{prospecto_id}",
            "select":       "id,direccion,texto,creado_en",
            "order":        "creado_en.asc",
            "limit":        "200",
        },
    ) or []
    prospectos = await _supabase_request(
        "GET", "/Prospecto",
        params={"id": f"eq.{prospecto_id}", "select": "paso,estado", "limit": "1"},
    ) or []
    p = prospectos[0] if prospectos else {}
    return {"mensajes": mensajes, "paso": p.get("paso"), "estado": p.get("estado")}


@router.get("/api/clientes/{cliente_id}/contexto")
async def api_contexto_cliente(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    select_fields = ",".join([
        "paso", "estado", "datos",
        *_CAMPOS_CALIFICACION,
        "motivo_no_interesado", "motivo_no_califica",
        "quiere_contacto_ejecutivo", "intencion_regularizar",
        "fecha_tentativa_recontacto",
    ])
    prospectos = await _supabase_request(
        "GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": select_fields, "limit": "1"},
    ) or []
    if not prospectos:
        return {"tiene_prospecto": False}
    return {"tiene_prospecto": True, **prospectos[0]}
