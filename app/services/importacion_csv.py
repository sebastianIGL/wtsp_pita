"""Importación de clientes desde un CSV: el generador que va emitiendo el progreso en vivo (eventos ``data: {...}``) mientras crea
los clientes, detecta duplicados y junta los errores por fila. Soporta el formato SERVIU y el formato general con columna de proyecto.

Extraído de ``api_importar_clientes`` (Fase 6, tramo 3, lote L8): el cuerpo del generador es el mismo; solo sale de la ruta y recibe
como parámetros lo que antes tomaba del entorno de la ruta.
"""
import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.clients.supabase import _supabase_request
from app.core.errors import mensaje_publico
from app.core.utilidades import _norm_row, _norm_row_keys, _normalize_phone
from app.services.carga_clientes import asignador
from app.services.proyectos import _buscar_id_proyecto

logger = logging.getLogger("wtsp_pita")

TAMANO_PAQUETE = 50      # clientes que se crean de una vez (una sola consulta a la base); si un paquete falla se reintenta fila por fila
_PREFER = {"Prefer": "return=representation"}


async def importar_clientes_stream(*, text, delimiter, is_serviu, empresa_id, proyecto_id, usuario_id, lote_carga, total, reparto=None, simular=False):
    """``reparto`` = [(usuario_id, cantidad)…]: los clientes creados se asignan en bloques seguidos, en el orden del archivo (sin reparto, todos a ``usuario_id``).
    ``simular=True`` hace todo menos escribir: es la revisión previa del archivo (cuántos se cargarían, a qué proyectos, cuántos duplicados y errores)."""
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
        siguiente_usuario = asignador(usuario_id, reparto)
        por_proyecto: Dict[str, int] = {}
        asignados: Dict[str, int] = {}
        pendientes: List[Dict[str, Any]] = []            # clientes válidos esperando su paquete (se crean de a ``TAMANO_PAQUETE``)
        tel_pendientes: set = set()

        def encolar(payload, fila, nombre, telefono, nombre_proyecto, asignar_a):
            pendientes.append({"payload": payload, "fila": fila, "nombre": nombre, "telefono": telefono, "proyecto": nombre_proyecto, "asignar_a": asignar_a})
            tel_pendientes.add(telefono)
            phones_existentes.add(telefono)

        async def ya_cargados(paquete) -> Optional[Dict[Any, Any]]:
            """Qué clientes del paquete SÍ quedaron en la base (por si la respuesta se perdió pero el paquete entró): {(teléfono, proyecto): id}. None si no se pudo saber."""
            try:
                filas = await _supabase_request("GET", "/Cliente", params={
                    "Telefono": f"in.({','.join(p['telefono'] for p in paquete)})", "origen_carga": f"eq.{lote_carga}", "select": "id,Telefono,proyecto_id"}) or []
            except Exception:
                return None
            return {(f.get("Telefono"), str(f.get("proyecto_id"))): f.get("id") for f in filas}

        async def vaciar():
            """Crea los clientes pendientes. Primero todos juntos (rápido); si la base rechaza el paquete —lo deshace entero—, uno por uno: solo falla el que trae el problema."""
            nonlocal creados
            if not pendientes:
                return
            paquete = list(pendientes)
            pendientes.clear()
            tel_pendientes.clear()
            resultados: List[Any] = []                   # (creado?, id o error) de cada cliente, en el orden del archivo
            if simular:
                resultados = [(True, None)] * len(paquete)
            else:
                try:
                    r = await _supabase_request("POST", "/Cliente", json=[p["payload"] for p in paquete], extra_headers=_PREFER)
                    devueltas = r if isinstance(r, list) and len(r) == len(paquete) else [None] * len(paquete)
                    resultados = [(True, x.get("id") if isinstance(x, dict) else None) for x in devueltas]
                except Exception as ex_paquete:
                    logger.warning("Importación | un paquete de %s clientes falló (%s): se reintenta uno por uno", len(paquete), type(ex_paquete).__name__)
                    ya = await ya_cargados(paquete)
                    for p in paquete:
                        if ya is None:                    # no se pudo confirmar qué entró: no se reintenta (podría duplicar clientes)
                            resultados.append((False, ex_paquete))
                            continue
                        clave = (p["telefono"], str(p["payload"]["proyecto_id"]))
                        if clave in ya:
                            resultados.append((True, ya[clave]))
                            continue
                        try:
                            r1 = await _supabase_request("POST", "/Cliente", json=p["payload"], extra_headers=_PREFER)
                            resultados.append((True, r1[0].get("id") if isinstance(r1, list) and r1 and isinstance(r1[0], dict) else None))
                        except Exception as ex_fila:
                            resultados.append((False, ex_fila))
            for p, (ok, dato) in zip(paquete, resultados):
                if ok:
                    if dato is not None:
                        ids_creados.append({"id": dato, "nombre": p["nombre"], "telefono": p["telefono"]})
                    creados += 1
                    por_proyecto[p["proyecto"]] = por_proyecto.get(p["proyecto"], 0) + 1
                    asignados[str(p["asignar_a"] or "")] = asignados.get(str(p["asignar_a"] or ""), 0) + 1
                else:
                    phones_existentes.discard(p["telefono"])      # no quedó en la base: un teléfono repetido más abajo ya no sería «duplicado» de este
                    errores.append({"fila": p["fila"], "motivo": mensaje_publico(dato, "api_importar_clientes (fila)")})

        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)

        n_filas = 0
        for i, row in enumerate(reader):
            fila = i + 2
            n_filas = i + 1
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
                    if telefono in tel_pendientes:
                        await vaciar()                    # un teléfono repetido en el mismo archivo: antes de decidir si es duplicado, se crean los pendientes

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
                        asignar_a = siguiente_usuario()
                        encolar({
                            "proyecto_id": proyecto_serviu["id"],
                            "Contacto": nombre, "Rut": rut, "Correo": correo,
                            "Telefono": telefono, "estado_crm": None,
                            "Tramo de renta": None,
                            "tiene_subsidio": None,
                            "tipo_subsidio": None,
                            "tiene_propiedad": None,
                            "primer mensaje": True, "wtsp_habilitado": True,
                            "usuario_id": asignar_a,
                            "Fecha Ult. Gestión": datetime.now(timezone.utc).date().isoformat(),
                            "origen_carga": lote_carga,
                            "fuente": "excel",
                        }, fila, nombre, telefono, proyecto_serviu.get("nombre") or "—", asignar_a)
                else:
                    nr              = _norm_row(row)
                    nombre_proyecto = nr.get("proyecto", "").strip()
                    nombre          = nr.get("contacto", "").strip()
                    rut             = nr.get("rut", "").strip()
                    correo          = nr.get("correo", "").strip() or None
                    tel_raw         = nr.get("telefono", "").strip()
                    telefono        = _normalize_phone(tel_raw)
                    if telefono in tel_pendientes:
                        await vaciar()
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
                            asignar_a = siguiente_usuario()
                            encolar({
                                "proyecto_id": proyecto_resuelto["id"],
                                "Contacto": nombre, "Rut": rut, "Correo": correo,
                                "Telefono": telefono, "estado_crm": estado_crm,
                                "Tramo de renta": tramo_renta,
                                "tiene_subsidio": tiene_subsidio,
                                "tipo_subsidio": tipo_subsidio,
                                "tiene_propiedad": tiene_propiedad,
                                "primer mensaje": True, "wtsp_habilitado": True,
                                "usuario_id": asignar_a,
                                "Fecha Ult. Gestión": datetime.now(timezone.utc).date().isoformat(),
                                "origen_carga": lote_carga,
                                "fuente": "excel",
                            }, fila, nombre, telefono, proyecto_resuelto.get("nombre") or "—", asignar_a)
            except Exception as ex:
                # mensaje de negocio tal cual; cualquier otro fallo → mensaje genérico con código (detalle en el log)
                errores.append({"fila": fila, "motivo": mensaje_publico(ex, "api_importar_clientes (fila)")})

            if len(pendientes) >= TAMANO_PAQUETE:
                await vaciar()
            yield f"data: {json.dumps({'t':'prog','n':i+1,'total':total,'creados':creados,'dups':len(duplicados),'errs':len(errores)})}\n\n"

        if pendientes:
            await vaciar()                               # el último paquete, aunque no esté completo
            yield f"data: {json.dumps({'t':'prog','n':n_filas,'total':total,'creados':creados,'dups':len(duplicados),'errs':len(errores)})}\n\n"
        errores.sort(key=lambda e: e["fila"])            # los de un paquete se anotan al cerrarlo: se dejan en el orden del archivo
        final = {'t': 'done', 'ok': True, 'creados': creados, 'duplicados': len(duplicados), 'errores': len(errores), 'detalle_errores': errores, 'detalle_duplicados': duplicados, 'ids_creados': ids_creados}
        if reparto:
            final['asignados'] = asignados                       # cuántos le tocaron a cada persona (clave vacía = sin asignar)
        if simular:
            final['por_proyecto'] = por_proyecto                 # a qué proyecto irían
        yield f"data: {json.dumps(final)}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'t':'error','msg':mensaje_publico(e, 'api_importar_clientes')})}\n\n"
