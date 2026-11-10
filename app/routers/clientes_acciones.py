"""Acciones masivas y de envío sobre clientes: importar clientes desde un CSV (con progreso en vivo) y enviar el primer
WhatsApp a un cliente eligiendo la plantilla.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import json
import re
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.autorizacion import exigir_cliente, exigir_proyecto
from app.clients.whatsapp import _build_body_params, send_whatsapp_template
from app.core.errors import AppError, respuesta_error
from app.core.permisos import _solo_admin
from app.core.utilidades import _normalize_phone
from app.services.mensajes import insertar_mensaje
from app.services.plantillas import TEMPLATE_VARS_MAP, _pool_plantilla
from app.services.prospectos import upsert_prospecto
from app.services.carga_clientes import RECOMENDADO_POR_PERSONA, contar_filas, ejecutivos_disponibles, exige_confirmacion, leer_archivo, recomendar, validar_asignado, validar_reparto
from app.services.importacion_csv import importar_clientes_stream
from app.services.reparto_existentes import bloques, encontrar_clientes, exige_confirmacion_reparto, ids_del_cuerpo, reasignar
from app.services.proyectos import obtener_proyecto_por_id
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from typing import Any, Dict, List, Optional

router = APIRouter()


_SIN_PROYECTO_SERVIU = "No hay un proyecto para asignar. Sube este archivo desde dentro de un proyecto."


@router.post("/api/clientes/importar/analizar")
async def api_analizar_importacion(request: Request, file: UploadFile = File(...)):
    """Revisa el archivo SIN cargar nada: cuántos clientes se cargarían, a qué proyectos, cuántos duplicados y errores hay, a quién se podrían repartir y qué se recomienda."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    empresa_id  = request.query_params.get("empresa_id")
    proyecto_id = request.query_params.get("proyecto_id")
    denegado = await exigir_proyecto(perfil, proyecto_id)
    if denegado:
        return denegado
    texto, separador, es_serviu = leer_archivo(await file.read())
    if es_serviu and not proyecto_id:
        return Response(content=_SIN_PROYECTO_SERVIU, status_code=400, media_type="text/plain")
    total = contar_filas(texto)
    final: Dict[str, Any] = {}
    async for pedazo in importar_clientes_stream(text=texto, delimiter=separador, is_serviu=es_serviu, empresa_id=empresa_id, proyecto_id=proyecto_id, usuario_id=perfil["id"],
                                                 lote_carga="", total=total, simular=True):
        if pedazo.startswith("data: "):
            evento = json.loads(pedazo[6:])
            if evento.get("t") in ("done", "error"):
                final = evento
    if final.get("t") != "done":
        return Response(content=final.get("msg") or "No se pudo revisar el archivo.", status_code=500, media_type="text/plain")
    se_cargan = int(final.get("creados") or 0)
    ejecutivos = await ejecutivos_disponibles(proyecto_id) if _solo_admin(perfil) else []
    return {
        "formato": "serviu" if es_serviu else "general", "total": total, "se_cargan": se_cargan, "duplicados": final["duplicados"], "con_errores": final["errores"],
        "errores": final["detalle_errores"][:20], "duplicados_ejemplo": final["detalle_duplicados"][:5], "por_proyecto": final.get("por_proyecto") or {},
        "limite_recomendado": RECOMENDADO_POR_PERSONA, "ejecutivos": ejecutivos,
        "recomendacion": recomendar(se_cargan, len([e for e in ejecutivos if e["rol"] == "ejecutivo"])),
    }


_SOLO_ADMIN_REPARTO = "Solo administradores pueden repartir clientes entre ejecutivos."


async def _cuerpo_json(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError:
        raise AppError("No se pudo leer la solicitud. Vuelve a intentarlo.", 400)


@router.post("/api/clientes/repartir/analizar")
async def api_analizar_reparto(request: Request):
    """Antes de repartir clientes ya cargados: cuántos son, a quién pertenecen hoy, a quién se podrían repartir y qué se recomienda."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content=_SOLO_ADMIN_REPARTO, status_code=403, media_type="text/plain")
    ids = ids_del_cuerpo(await _cuerpo_json(request))
    filas = await encontrar_clientes(ids)
    proyectos = {str(f.get("proyecto_id")) for f in filas.values() if f.get("proyecto_id")}
    ejecutivos = await ejecutivos_disponibles(next(iter(proyectos)) if len(proyectos) == 1 else None)
    nombres = {e["id"]: e["nombre"] for e in ejecutivos}
    hoy: Dict[str, int] = {}
    for f in filas.values():
        hoy[str(f.get("usuario_id") or "")] = hoy.get(str(f.get("usuario_id") or ""), 0) + 1
    return {
        "total": len(ids), "se_cargan": len(ids), "duplicados": 0, "con_errores": 0, "errores": [], "por_proyecto": {}, "limite_recomendado": RECOMENDADO_POR_PERSONA,
        "actuales": [{"usuario_id": u or None, "nombre": nombres.get(u) or ("Sin asignar" if not u else "Otra persona"), "cantidad": n} for u, n in sorted(hoy.items(), key=lambda x: -x[1])],
        "ejecutivos": ejecutivos, "recomendacion": recomendar(len(ids), len([e for e in ejecutivos if e["rol"] == "ejecutivo"]), verbo="repartirían"),
    }


@router.post("/api/clientes/repartir")
async def api_repartir_clientes(request: Request):
    """Reparte clientes ya cargados entre ejecutivos: ``{ids: [...], reparto: [{usuario_id, cantidad}], confirmo?}``. Solo administración."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content=_SOLO_ADMIN_REPARTO, status_code=403, media_type="text/plain")
    cuerpo = await _cuerpo_json(request)
    ids = ids_del_cuerpo(cuerpo)
    reparto = await validar_reparto(cuerpo.get("reparto"))
    bloques(ids, reparto)                                         # que el reparto sume exactamente los clientes elegidos, antes de tocar nada
    aviso = None if (request.query_params.get("confirmo") == "1" or cuerpo.get("confirmo") is True) else exige_confirmacion_reparto(reparto)
    if aviso:
        return Response(content=aviso, status_code=409, media_type="text/plain")
    await encontrar_clientes(ids)
    return await reasignar(ids, reparto)


@router.post("/api/clientes/importar")
async def api_importar_clientes(request: Request, file: UploadFile = File(...), reparto: Optional[str] = Form(None)):
    from fastapi.responses import StreamingResponse as _SR
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    usuario_id = perfil["id"]
    empresa_id  = request.query_params.get("empresa_id")
    proyecto_id = request.query_params.get("proyecto_id")
    denegado = await exigir_proyecto(perfil, proyecto_id)          # el proyecto de destino (formato SERVIU) debe ser uno que esta persona puede usar
    if denegado:
        return denegado
    # Identificador del lote de carga: permite luego filtrar/borrar solo lo importado en esta corrida
    lote_carga = f"{(file.filename or 'import').strip()} · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    asignar_usuario_id = request.query_params.get("asignar_usuario_id")
    reparto_validado = None
    if reparto and reparto.strip():
        if not _solo_admin(perfil):
            return Response(content="Solo administradores pueden repartir los clientes entre varios ejecutivos.", status_code=403, media_type="text/plain")
        reparto_validado = await validar_reparto(reparto)
    elif asignar_usuario_id and _solo_admin(perfil):
        await validar_asignado(asignar_usuario_id)
        usuario_id = asignar_usuario_id

    text, delimiter, is_serviu = leer_archivo(await file.read())

    if is_serviu and not proyecto_id:
        return Response(content=_SIN_PROYECTO_SERVIU, status_code=400, media_type="text/plain")

    total = contar_filas(text)

    # Una carga que dejaría a una sola persona por encima de lo recomendado hay que confirmarla (la pantalla ya lo avisó; esto vale también si alguien llama a la ruta directo)
    aviso = None if request.query_params.get("confirmo") == "1" else exige_confirmacion(total, reparto_validado)
    if aviso:
        return Response(content=aviso, status_code=409, media_type="text/plain")

    return _SR(
        importar_clientes_stream(
            text=text, delimiter=delimiter, is_serviu=is_serviu, empresa_id=empresa_id, proyecto_id=proyecto_id,
            usuario_id=usuario_id, lote_carga=lote_carga, total=total, reparto=reparto_validado),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/clientes/{cliente_id}/enviar-wtsp")
async def api_enviar_primer_wtsp(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    try:
        body          = await request.json()
        template_name = (body.get("template_name") or "").strip()
        language_code = (body.get("language_code") or "es_CL").strip()
        components_meta = body.get("components") or []
        if not template_name:
            return Response(content="Falta template_name", status_code=400)
        rows = await _supabase_request("GET", "/Cliente",
            params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
        if not rows:
            return Response(content="Cliente no encontrado", status_code=404)
        c          = rows[0]
        telefono   = _normalize_phone(c.get("Telefono") or "")
        nombre     = (c.get("Contacto") or "").strip()
        proyecto_id = c.get("proyecto_id") or ""
        if not telefono:
            return Response(content="Cliente sin teléfono", status_code=400)

        proyecto = None
        if proyecto_id:
            proyecto = await obtener_proyecto_por_id(proyecto_id)

        header_comp = next((cm for cm in components_meta if cm.get("type") == "HEADER"), None)
        needs_image = header_comp and header_comp.get("format") == "IMAGE"
        image_url   = (proyecto.get("imagen_url") or None) if (needs_image and proyecto) else None

        tipologia_id = body.get("tipologia_id") or None
        if tipologia_id:
            try:
                tipologia_id = int(tipologia_id)
            except (ValueError, TypeError):
                tipologia_id = None

        pool_vals = await _pool_plantilla(nombre, proyecto, tipologia_id=tipologia_id)

        # Prioridad: 1) config en DB  2) mapa hardcoded  3) fallback posicional
        db_cfg = await _supabase_request(
            "GET", "/PlantillaConfig",
            params={"template_name": f"eq.{template_name}", "select": "variables,param_names", "limit": "1"},
        ) or []
        if db_cfg and db_cfg[0].get("variables"):
            cfg_vars   = db_cfg[0]["variables"]
            cfg_params = db_cfg[0].get("param_names") or None
            body_text_params: List[Any] = _build_body_params(pool_vals, cfg_vars, cfg_params)
        elif template_name in TEMPLATE_VARS_MAP:
            body_text_params = [pool_vals.get(k, "") for k in TEMPLATE_VARS_MAP[template_name]]
        else:
            fallback_order = [
                "cliente_nombre", "proyecto_nombre", "proyecto_ubicacion",
                "subsidio_tipo", "monto_subsidio_uf", "valor_reserva_clp",
                "precio_desde_uf", "fecha_entrega",
            ]
            body_comp = next((cm for cm in components_meta if cm.get("type") == "BODY"), None)
            body_text_params = []
            if body_comp:
                # Named params: Meta incluye example.body_text_named_params con los nombres reales
                named_meta = (body_comp.get("example") or {}).get("body_text_named_params") or []
                if named_meta:
                    for i, p in enumerate(named_meta):
                        pname    = p.get("parameter_name", "")
                        pool_key = fallback_order[i] if i < len(fallback_order) else "cliente_nombre"
                        body_text_params.append({"parameter_name": pname, "text": pool_vals.get(pool_key, "")})
                else:
                    # Posicionales {{1}}, {{2}} — solo dígitos
                    positional = re.findall(r'\{\{\d+\}\}', body_comp.get("text", ""))
                    var_count  = max((int(m.strip("{}")) for m in positional), default=0)
                    body_text_params = [pool_vals.get(fallback_order[i], "") for i in range(min(var_count, len(fallback_order)))]
            # Si la plantilla no tiene variables → no enviar parámetros (evita el error 400)

        wa = await send_whatsapp_template(
            to=telefono, template_name=template_name, language_code=language_code,
            body_text_params=body_text_params, image_url=image_url,
        )
        ahora_wtsp = datetime.now(timezone.utc)
        wamid_enviado = (wa.get("messages") or [{}])[0].get("id") if isinstance(wa, dict) else None
        patch_cliente: Dict[str, Any] = {
            "primer mensaje": False,
            "wtsp_habilitado": False,
            "primer_wtsp_en": ahora_wtsp.isoformat(),
            "Fecha Ult. Gestión": ahora_wtsp.isoformat(),
            "wamid_plantilla":  wamid_enviado,
            "estado_plantilla": "enviado",
        }
        # Auto-agendar recordatorio 24h después si no hay uno ya programado
        if not c.get("recordatorio_at"):
            patch_cliente["recordatorio_at"] = (ahora_wtsp + timedelta(hours=24)).isoformat()
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"}, json=patch_cliente)
        prospecto = await upsert_prospecto(
            telefono_e164=telefono, nombre=nombre, rut=c.get("Rut"),
            rango_sueldo=c.get("Tramo de renta"), proyecto_id=proyecto_id,
            estado="PLANTILLA_ENVIADA", paso="BIENVENIDA", cliente_id=cliente_id,
        )
        # Registrar plantilla enviada como mensaje saliente en la conversación
        if prospecto and prospecto.get("id"):
            body_comp = next((cm for cm in components_meta if cm.get("type") == "BODY"), None)
            texto_plantilla = f"[Plantilla: {template_name}]"
            if body_comp and body_comp.get("text"):
                # Rellenar variables con los valores reales para mostrar en conversación
                raw = body_comp["text"]
                if isinstance(body_text_params, list):
                    for i, p in enumerate(body_text_params):
                        val = p.get("text", "") if isinstance(p, dict) else str(p)
                        raw = re.sub(r'\{\{[^}]+\}\}', val, raw, count=1)
                texto_plantilla = raw
            await insertar_mensaje(
                prospecto_id=prospecto["id"],
                direccion="saliente",
                text=texto_plantilla,
                wa_message_id=(wa.get("messages", [{}])[0].get("id") if isinstance(wa, dict) else None),
                cliente_id=cliente_id,
            )
        return {"ok": True, "wa": wa}
    except Exception as e:
        return respuesta_error(e, contexto="api_enviar_primer_wtsp")
