"""Clientes del CRM (listar, crear, actualizar), inbox de prospectos que aún no son clientes (listar y registrar como
cliente) y conversaciones y contexto del bot de un cliente o prospecto.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import asyncio
import logging
import httpx
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.autorizacion import exigir_cliente, exigir_prospecto, exigir_proyecto
from app.core.errors import respuesta_error
from app.core.permisos import _solo_admin
from app.core.utilidades import _normalize_phone
from app.services import clientes_datos as cd
from app.services.clientes_vistas import enriquecer_clientes, leer_en_paginas
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
    rows = await leer_en_paginas("/Cliente", params, pedir=_supabase_request)          # todas las filas, no solo las primeras 1.000
    # Merge datos de Prospecto para ordenar por actividad WA y mostrar estado
    if rows:
        telefonos = [r["Telefono"] for r in rows if r.get("Telefono")]
        prospectos: List[Dict] = []
        LOTE_TELEFONOS = 250
        cupo = asyncio.Semaphore(6)                              # de a pocas consultas a la vez: con miles de clientes son decenas de lotes

        async def _prospectos_de(tel_list: str):
            async with cupo:
                return await _supabase_request("GET", "/Prospecto",
                    params={"telefono_e164": f"in.({tel_list})",
                            "select": "telefono_e164,ultimo_entrante_en,pendiente_respuesta,estado,paso"}) or []
        lotes_tel = [",".join(telefonos[i:i + LOTE_TELEFONOS]) for i in range(0, len(telefonos), LOTE_TELEFONOS)]
        for lote in await asyncio.gather(*[_prospectos_de(t) for t in lotes_tel if t]):
            prospectos += lote
        if prospectos:
            prosp_map = {p["telefono_e164"]: p for p in prospectos}
            for r in rows:
                p = prosp_map.get(r.get("Telefono"), {})
                r["ultimo_entrante_en"]  = p.get("ultimo_entrante_en")
                r["pendiente_respuesta"] = p.get("pendiente_respuesta")
                r["prospecto_estado"]    = p.get("estado")
                r["prospecto_paso"]      = p.get("paso")
        await enriquecer_clientes(rows)           # tipología que busca y cantidad de notas (vistas «Tabla» y «Pipeline»)
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
        fuente_cliente = cd.fuente(body.get("fuente")) or "manual"      # de dónde llegó (si no se dice, lo creó una persona a mano)
        medio_cliente  = cd.medio(body.get("medio"))
        denegado = await exigir_proyecto(perfil, proyecto_id)          # solo en un proyecto que esta persona puede usar
        if denegado:
            return denegado

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
                "fuente":             fuente_cliente,
                "estado_gestion":     "sin_contactar",
                "medio":              medio_cliente,
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

    except cd.DatoInvalido as e:
        return Response(content=str(e), status_code=400)
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
    _CAMPOS_PERMITIDOS = {"recordatorio_at", "Contacto", "Correo", "email", "Tramo de renta", "Rut", "es_nuevo", "numero_integrantes", "proyecto_id", "ahorro_uf", "etapa_tipologia_id", "estado_gestion", "recordatorio_nota", "fuente", "medio"}
    if _solo_admin(perfil):
        _CAMPOS_PERMITIDOS = _CAMPOS_PERMITIDOS | {"usuario_id", "archivado"}
    try:
        body = await request.json()
        payload = {k: v for k, v in body.items() if k in _CAMPOS_PERMITIDOS}
        if not payload:
            return Response(content="Sin campos válidos", status_code=400)
        if "fuente" in payload:
            payload["fuente"] = cd.fuente(payload["fuente"])
        if "medio" in payload:
            payload["medio"] = cd.medio(payload["medio"])
        if "estado_gestion" in payload:
            payload["estado_gestion"] = cd.estado_gestion(payload["estado_gestion"])
        if "recordatorio_nota" in payload:
            payload["recordatorio_nota"] = cd.nota_recordatorio(payload["recordatorio_nota"])
        if "recordatorio_at" in payload and not payload["recordatorio_at"]:
            payload["recordatorio_nota"] = None                         # sin fecha no queda una nota suelta
        nuevo_proyecto = payload.get("proyecto_id")
        if nuevo_proyecto not in (None, "") and not _solo_admin(perfil):
            actual = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": "proyecto_id", "limit": "1"}) or []
            if not actual or str(actual[0].get("proyecto_id")) != str(nuevo_proyecto):         # dejarlo donde está no es un cambio: no se exige nada
                denegado = await exigir_proyecto(perfil, nuevo_proyecto)
                if denegado:
                    return denegado
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json=payload,
        )
        return {"ok": True}
    except cd.DatoInvalido as e:
        return Response(content=str(e), status_code=400)
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

        proyecto_id = body.get("proyecto_id") or p.get("proyecto_id") or None
        denegado = await exigir_proyecto(perfil, proyecto_id)          # el proyecto elegido, o el del prospecto, debe ser uno que esta persona puede usar
        if denegado:
            return denegado

        cliente_payload: Dict[str, Any] = {
            "Contacto":           nombre,
            "Telefono":           telefono,
            "Rut":                body.get("rut")      or p.get("rut")      or None,
            "Correo":             body.get("correo")   or None,
            "Tramo de renta":     body.get("rango_sueldo") or p.get("rango_sueldo") or None,
            "numero_integrantes": body.get("numero_integrantes") or p.get("numero_integrantes") or None,
            "proyecto_id":        proyecto_id,
            "usuario_id":         perfil["id"],
            "es_nuevo":           True,
            "primer mensaje":     False,
            "fuente":             "chatbot",                      # escribió al chatbot y estaba en el Inbox
            "medio":              "chatbot",
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


# ── Notas internas del equipo sobre un cliente ───────────────────────────────
# Las ve y las escribe quien puede usar al cliente (mismo criterio que la conversación y los documentos: administrador y owner,
# y el ejecutivo dueño). Solo son internas: no salen en ningún correo ni mensaje. Se guardan en la tabla NotaCliente.
# Editar y borrar: ver más abajo quién puede.
NOTA_MAX = 2000
_NOTA_SELECT = "id,texto,usuario_id,autor_nombre,creado_en"


def _nota_publica(fila: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": fila.get("id"), "texto": fila.get("texto") or "", "autor": fila.get("autor_nombre") or "",
            "usuario_id": fila.get("usuario_id"), "creado_en": fila.get("creado_en")}


async def _texto_de_nota(request: Request):
    """(texto, None) con el texto recortado, o (None, respuesta 422) si falta o es demasiado largo. Lo usan crear y editar."""
    try:
        body = await request.json()
    except Exception:
        body = None
    texto = body.get("texto") if isinstance(body, dict) else None
    texto = texto.strip() if isinstance(texto, str) else ""
    if not texto:
        return None, Response(content="La nota no puede estar vacía", status_code=422)
    if len(texto) > NOTA_MAX:
        return None, Response(content=f"La nota es demasiado larga (máximo {NOTA_MAX} caracteres)", status_code=422)
    return texto, None


@router.get("/api/clientes/{cliente_id}/notas")
async def api_listar_notas(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    try:
        filas = await _supabase_request(
            "GET", "/NotaCliente",
            params={"cliente_id": f"eq.{cliente_id}", "select": _NOTA_SELECT, "order": "creado_en.desc,id.desc", "limit": "200"},
        ) or []
        return [_nota_publica(f) for f in filas]
    except Exception as e:
        return respuesta_error(e, contexto="api_listar_notas")


@router.post("/api/clientes/{cliente_id}/notas")
async def api_crear_nota(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    texto, invalido = await _texto_de_nota(request)
    if invalido:
        return invalido
    try:
        # El autor y el cliente salen de la sesión y de la ruta, nunca del cuerpo de la petición.
        filas = await _supabase_request(
            "POST", "/NotaCliente",
            json={"cliente_id": cliente_id, "usuario_id": perfil.get("id"),
                  "autor_nombre": perfil.get("nombre") or perfil.get("correo") or "Usuario", "texto": texto},
            extra_headers={"Prefer": "return=representation"},
        ) or []
    except httpx.HTTPStatusError as e:
        if _es_cliente_inexistente(e):
            return Response(content="Cliente no encontrado", status_code=404)
        return respuesta_error(e, contexto="api_crear_nota")
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_nota")
    logger.info("Nota agregada | cliente=%s | usuario=%s", cliente_id, perfil.get("id"))      # nunca el texto de la nota
    return _nota_publica(filas[0] if filas else {})


async def _nota_del_cliente(cliente_id: int, nota_id: int):
    """La nota, solo si pertenece a ese cliente (una nota de otro cliente se trata como inexistente). ``None`` si no hay."""
    filas = await _supabase_request(
        "GET", "/NotaCliente",
        params={"id": f"eq.{nota_id}", "cliente_id": f"eq.{cliente_id}", "select": "id,usuario_id", "limit": "1"},
    ) or []
    return filas[0] if filas else None


def _es_autor(perfil: Dict[str, Any], nota: Dict[str, Any]) -> bool:
    return bool(nota.get("usuario_id")) and str(nota["usuario_id"]) == str(perfil.get("id"))


# Editar: solo quien la escribió (nadie más puede poner palabras en boca de otra persona). Borrar: quien la escribió, o el administrador y el
# owner. Ambos exigen además poder usar al cliente. Una nota cuyo autor ya no existe solo la puede borrar el administrador o el owner.
@router.patch("/api/clientes/{cliente_id}/notas/{nota_id}")
async def api_editar_nota(cliente_id: int, nota_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    texto, invalido = await _texto_de_nota(request)
    if invalido:
        return invalido
    try:
        nota = await _nota_del_cliente(cliente_id, nota_id)
        if not nota:
            return Response(content="Nota no encontrada", status_code=404)
        if not _es_autor(perfil, nota):
            return Response(content="Solo quien escribió la nota puede editarla", status_code=403)
        # El filtro por autor también va en la escritura: si la nota cambiara de manos entre la lectura y esta línea, no se toca.
        filas = await _supabase_request(
            "PATCH", "/NotaCliente",
            params={"id": f"eq.{nota_id}", "cliente_id": f"eq.{cliente_id}", "usuario_id": f"eq.{perfil.get('id')}"},
            json={"texto": texto},
            extra_headers={"Prefer": "return=representation"},
        ) or []
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_nota")
    if not filas:
        return Response(content="Nota no encontrada", status_code=404)
    logger.info("Nota editada | cliente=%s | nota=%s | usuario=%s", cliente_id, nota_id, perfil.get("id"))      # nunca el texto de la nota
    return _nota_publica(filas[0])


@router.delete("/api/clientes/{cliente_id}/notas/{nota_id}")
async def api_borrar_nota(cliente_id: int, nota_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    try:
        nota = await _nota_del_cliente(cliente_id, nota_id)
        if not nota:
            return Response(content="Nota no encontrada", status_code=404)
        if not (_solo_admin(perfil) or _es_autor(perfil, nota)):
            return Response(content="Solo quien escribió la nota o un administrador pueden borrarla", status_code=403)
        await _supabase_request("DELETE", "/NotaCliente", params={"id": f"eq.{nota_id}", "cliente_id": f"eq.{cliente_id}"},
                                extra_headers={"Prefer": "return=minimal"})
    except Exception as e:
        return respuesta_error(e, contexto="api_borrar_nota")
    logger.info("Nota borrada | cliente=%s | nota=%s | usuario=%s", cliente_id, nota_id, perfil.get("id"))
    return {"ok": True}


def _es_cliente_inexistente(e: "httpx.HTTPStatusError") -> bool:
    """PostgREST responde 409 con code 23503 cuando la clave foránea (el cliente) no existe."""
    if getattr(e.response, "status_code", None) != 409:
        return False
    try:
        return (e.response.json() or {}).get("code") == "23503"
    except Exception:
        return False


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
