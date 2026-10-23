"""Acciones masivas y de envío sobre clientes: importar clientes desde un CSV (con progreso en vivo) y enviar el primer
WhatsApp a un cliente eligiendo la plantilla.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import csv
import io
import json
import re
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.clients.whatsapp import _build_body_params, send_whatsapp_template
from app.core.errors import mensaje_publico, respuesta_error
from app.core.permisos import _solo_admin
from app.core.utilidades import _norm_col, _norm_row, _norm_row_keys, _normalize_phone
from app.services.mensajes import insertar_mensaje
from app.services.plantillas import TEMPLATE_VARS_MAP, _pool_plantilla
from app.services.prospectos import upsert_prospecto
from app.services.proyectos import _buscar_id_proyecto, obtener_proyecto_por_id
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, File, Request, Response, UploadFile
from typing import Any, Dict, List

router = APIRouter()


@router.post("/api/clientes/importar")
async def api_importar_clientes(request: Request, file: UploadFile = File(...)):
    from fastapi.responses import StreamingResponse as _SR
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    usuario_id = perfil["id"]
    empresa_id  = request.query_params.get("empresa_id")
    proyecto_id = request.query_params.get("proyecto_id")
    # Identificador del lote de carga: permite luego filtrar/borrar solo lo importado en esta corrida
    lote_carga = f"{(file.filename or 'import').strip()} · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    asignar_usuario_id = request.query_params.get("asignar_usuario_id")
    if asignar_usuario_id and _solo_admin(perfil):
        usuario_id = asignar_usuario_id

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    # Detectar delimitador: exportaciones desde Excel en configuración regional
    # es-CL suelen usar ";" (porque la coma se usa como separador decimal).
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","

    # Detectar formato: SERVIU tiene columna "Dv" o "Primer Apellido" (case/tilde-insensible)
    _peek = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    _headers = _peek.fieldnames or []
    _headers_norm = {_norm_col(h) for h in _headers}
    is_serviu = "dv" in _headers_norm or "primerapellido" in _headers_norm

    if is_serviu and not proyecto_id:
        return Response(
            content="No hay un proyecto para asignar. Sube este archivo desde dentro de un proyecto.",
            status_code=400, media_type="text/plain"
        )

    total = max(0, sum(1 for l in text.splitlines() if l.strip()) - 1)

    async def _stream():
        try:
            proyecto_params: Dict[str, str] = {"select": "id,nombre,nombres_csv"}
            if empresa_id:
                inmobiliarias = await _supabase_request(
                    "GET", "/Inmobiliaria",
                    params={"empresa_id": f"eq.{empresa_id}", "select": "id"},
                ) or []
                inm_ids = ",".join(str(i["id"]) for i in inmobiliarias)
                if inm_ids:
                    proyecto_params["inmobiliaria_id"] = f"in.({inm_ids})"
            todos_proyectos = await _supabase_request(
                "GET", "/Proyecto", params=proyecto_params,
            ) or []

            # Extraer teléfonos del CSV (primera pasada, sin I/O)
            phones_csv: set = set()
            for row in csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter):
                if is_serviu:
                    nrow = _norm_row_keys(row)
                    tel_raw = (nrow.get("movil") or nrow.get("fonodomicilio") or
                               nrow.get("fonotrabajo") or "").strip()
                else:
                    tel_raw = (_norm_row(row).get("telefono") or "").strip()
                tel = _normalize_phone(tel_raw)
                if tel:
                    phones_csv.add(tel)

            # Solo los teléfonos que vienen en el archivo, en lotes para no exceder
            # el largo máximo de URL que acepta el gateway de Supabase
            phones_existentes: set = set()
            if phones_csv:
                phones_list = list(phones_csv)
                LOTE_TELEFONOS = 250
                for i in range(0, len(phones_list), LOTE_TELEFONOS):
                    lote = phones_list[i:i + LOTE_TELEFONOS]
                    phones_in = ",".join(lote)
                    batch = await _supabase_request("GET", "/Cliente",
                        params={"Telefono": f"in.({phones_in})", "select": "Telefono"},
                    ) or []
                    phones_existentes.update(r["Telefono"] for r in batch if r.get("Telefono"))

            # Resolver proyecto fijo (para SERVIU y para CSVs sin columna Proyecto)
            proyecto_fijo = None
            if proyecto_id:
                _proy_rows = await _supabase_request("GET", "/Proyecto",
                    params={"id": f"eq.{proyecto_id}", "select": "id,nombre", "limit": "1"}) or []
                proyecto_fijo = _proy_rows[0] if _proy_rows else None
            proyecto_serviu = proyecto_fijo  # alias para bloque SERVIU

            creados, duplicados, errores, ids_creados = 0, [], [], []
            reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)

            for i, row in enumerate(reader):
                fila = i + 2
                try:
                    if is_serviu:
                        nrow    = _norm_row_keys(row)
                        rut_num = (nrow.get("rut") or "").strip()
                        dv      = (nrow.get("dv") or "").strip()
                        if rut_num and dv:
                            rut = f"{rut_num}-{dv}"
                        elif rut_num:
                            rut = rut_num  # ya viene combinado, ej "17511100-K"
                        else:
                            rut = None
                        nombre  = " ".join(filter(None, [
                            (nrow.get("nombre") or "").strip(),
                            (nrow.get("primerapellido") or "").strip(),
                            (nrow.get("segundoapellido") or "").strip(),
                        ]))
                        tel_raw  = (nrow.get("movil") or nrow.get("fonodomicilio") or
                                    nrow.get("fonotrabajo") or "").strip()
                        correo   = (nrow.get("email") or "").strip() or None
                        telefono = _normalize_phone(tel_raw)

                        datos_raw = {"Nombre": nombre, "Rut": rut or "",
                                     "Teléfono": tel_raw, "E-mail": correo or ""}

                        if not nombre or not telefono:
                            errores.append({"fila": fila, "motivo": "Nombre o Teléfono vacío", "datos": datos_raw})
                        elif not rut:
                            errores.append({"fila": fila, "nombre": nombre, "motivo": "Rut vacío", "datos": datos_raw})
                        elif not proyecto_serviu:
                            errores.append({"fila": fila, "nombre": nombre,
                                            "motivo": "Proyecto no encontrado", "datos": datos_raw})
                        elif telefono in phones_existentes:
                            duplicados.append({"fila": fila, "nombre": nombre,
                                               "telefono": telefono, "datos": datos_raw})
                        else:
                            _r = await _supabase_request("POST", "/Cliente",
                                json={
                                    "proyecto_id": proyecto_serviu["id"],
                                    "Contacto": nombre, "Rut": rut, "Correo": correo,
                                    "Telefono": telefono, "estado_crm": None,
                                    "Tramo de renta": None,
                                    "tiene_subsidio": None,
                                    "tipo_subsidio": None,
                                    "tiene_propiedad": None,
                                    "primer mensaje": True, "wtsp_habilitado": True,
                                    "usuario_id": usuario_id,
                                    "Fecha Ult. Gestión": datetime.now(timezone.utc).date().isoformat(),
                                    "origen_carga": lote_carga,
                                },
                                extra_headers={"Prefer": "return=representation"})
                            phones_existentes.add(telefono)
                            if isinstance(_r, list) and _r:
                                ids_creados.append({"id": _r[0]["id"], "nombre": nombre, "telefono": telefono})
                            creados += 1
                    else:
                        nr              = _norm_row(row)
                        nombre_proyecto = nr.get("proyecto", "").strip()
                        nombre          = nr.get("contacto", "").strip()
                        rut             = nr.get("rut", "").strip()
                        correo          = nr.get("correo", "").strip() or None
                        tel_raw         = nr.get("telefono", "").strip()
                        telefono        = _normalize_phone(tel_raw)
                        estado_crm      = nr.get("estado", "").strip() or None
                        tramo_renta     = nr.get("tramo_renta", "").strip() or None
                        sub_raw         = nr.get("tiene_subsidio", "").strip().lower()
                        tiene_subsidio  = True if sub_raw in ("si","sí","yes","1") else (False if sub_raw in ("no","0") else None)
                        tipo_subsidio   = nr.get("tipo_subsidio", "").strip() or None
                        prop_raw        = nr.get("tiene_propiedad", "").strip().lower()
                        tiene_propiedad = True if prop_raw in ("si","sí","yes","1") else (False if prop_raw in ("no","0") else None)

                        datos_raw = {"Contacto": nombre, "Rut": rut, "Correo": correo or "",
                                     "Teléfono": tel_raw, "Proyecto": nombre_proyecto,
                                     "Estado": estado_crm or "", "Tramo de renta": tramo_renta or "",
                                     "Tiene subsidio": sub_raw, "Tipo subsidio": tipo_subsidio or "",
                                     "Tiene propiedad": prop_raw}

                        # Si el CSV no trae Proyecto pero hay uno seleccionado, usarlo
                        proyecto_resuelto = proyecto_fijo if not nombre_proyecto and proyecto_fijo else None

                        if not nombre or not telefono:
                            errores.append({"fila": fila, "motivo": "Contacto o Teléfono vacío", "datos": datos_raw})
                        elif not rut:
                            errores.append({"fila": fila, "nombre": nombre, "motivo": "Rut vacío", "datos": datos_raw})
                        elif not nombre_proyecto and not proyecto_fijo:
                            errores.append({"fila": fila, "nombre": nombre, "motivo": "Proyecto vacío (agrega columna Proyecto o importa desde dentro de un proyecto)", "datos": datos_raw})
                        elif telefono in phones_existentes:
                            duplicados.append({"fila": fila, "nombre": nombre,
                                               "telefono": telefono, "datos": datos_raw})
                        else:
                            if not proyecto_resuelto:
                                proyecto_resuelto = await _buscar_id_proyecto(nombre_proyecto, todos_proyectos)
                            if not proyecto_resuelto:
                                errores.append({"fila": fila, "nombre": nombre,
                                                "motivo": f"Proyecto '{nombre_proyecto}' no encontrado — agrega el alias en nombres_csv",
                                                "datos": datos_raw})
                            else:
                                _r = await _supabase_request("POST", "/Cliente",
                                    json={
                                        "proyecto_id": proyecto_resuelto["id"],
                                        "Contacto": nombre, "Rut": rut, "Correo": correo,
                                        "Telefono": telefono, "estado_crm": estado_crm,
                                        "Tramo de renta": tramo_renta,
                                        "tiene_subsidio": tiene_subsidio,
                                        "tipo_subsidio": tipo_subsidio,
                                        "tiene_propiedad": tiene_propiedad,
                                        "primer mensaje": True, "wtsp_habilitado": True,
                                        "usuario_id": usuario_id,
                                        "Fecha Ult. Gestión": datetime.now(timezone.utc).date().isoformat(),
                                        "origen_carga": lote_carga,
                                    },
                                    extra_headers={"Prefer": "return=representation"})
                                phones_existentes.add(telefono)
                                if isinstance(_r, list) and _r:
                                    ids_creados.append({"id": _r[0]["id"], "nombre": nombre, "telefono": telefono})
                                creados += 1
                except Exception as ex:
                    # mensaje de negocio tal cual; cualquier otro fallo → mensaje genérico con código (detalle en el log)
                    errores.append({"fila": fila, "motivo": mensaje_publico(ex, "api_importar_clientes (fila)")})

                yield f"data: {json.dumps({'t':'prog','n':i+1,'total':total,'creados':creados,'dups':len(duplicados),'errs':len(errores)})}\n\n"

            yield f"data: {json.dumps({'t':'done','ok':True,'creados':creados,'duplicados':len(duplicados),'errores':len(errores),'detalle_errores':errores,'detalle_duplicados':duplicados,'ids_creados':ids_creados})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'t':'error','msg':mensaje_publico(e, 'api_importar_clientes')})}\n\n"

    return _SR(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/clientes/{cliente_id}/enviar-wtsp")
async def api_enviar_primer_wtsp(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
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
