"""Documentos de un cliente (listar, reclasificar, subir, descargar, eliminar), vista previa y envío de la evaluación
hipotecaria por correo, y recordatorio por WhatsApp al celular del ejecutivo.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import asyncio
import re
import time
import logging
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.autorizacion import exigir_cliente
from app.clients.supabase_storage import _descargar_documento_storage, _eliminar_documento_storage, subir_a_storage
from app.clients.whatsapp import send_whatsapp_template
from app.core.config import get_settings
from app.core.cuerpo_opcional import _cuerpo_opcional_ilegible
from app.core.errors import respuesta_error
from app.core.permisos import _solo_admin
from app.core.utilidades import _normalize_phone
from app.services.avisos import _notificar_recordatorio_estado
from app.services.documentos import _CAMPOS_CALIFICACION, documentos_pendientes
from app.services.documentos_prospecto import insertar_documento, obtener_documentos_prospecto
from app.services.evaluacion_email import CONDICION_LABEL, _enviar_email_evaluacion
from app.services.prospectos import actualizar_datos_prospecto, upsert_prospecto
from datetime import datetime, timezone
from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from typing import Dict, Optional

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


async def _obtener_o_crear_prospecto(cliente_id: int) -> Optional[Dict]:
    """Retorna el prospecto del cliente. Si no existe, lo crea con los datos del Cliente."""
    prospectos = await _supabase_request(
        "GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": "*", "limit": "1"},
    )
    if prospectos:
        return prospectos[0]

    # No existe — obtener datos del Cliente para crear el prospecto
    clientes = await _supabase_request(
        "GET", "/Cliente",
        params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"},
    )
    if not clientes:
        return None
    c = clientes[0]

    telefono = _normalize_phone(c.get("Telefono") or "")
    if not telefono:
        return None

    prospecto = await upsert_prospecto(
        telefono_e164=telefono,
        nombre=(c.get("Contacto") or "").strip() or None,
        rut=(c.get("Rut") or "").strip() or None,
        rango_sueldo=c.get("Tramo de renta") or None,
        proyecto_id=c.get("proyecto_id") or None,
        estado="SIN_CONTACTAR",
        cliente_id=cliente_id,
    )
    return prospecto


@router.get("/api/clientes/{cliente_id}/documentos")
async def api_documentos_cliente(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    try:
        prospectos = await _supabase_request(
            "GET", "/Prospecto",
            params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"},
        )
        if not prospectos:
            return []
        prospecto_id = prospectos[0]["id"]
        docs = await _supabase_request(
            "GET", "/Documento",
            params={
                "prospecto_id": f"eq.{prospecto_id}",
                "select": "id,tipo,nombre_archivo,url_storage,verificado,creado_en",
                "order": "creado_en.desc",
            },
        )
        return docs or []
    except Exception as e:
        return respuesta_error(e, contexto="api_documentos_cliente")


TIPOS_VALIDOS = {
    "liquidacion_sueldo", "certificado_afp", "carnet_identidad",
    "antiguedad_laboral", "libreta_ahorro", "informe_deudas", "otro",
    "cedula_complementador", "liquidaciones_complementador",
    "certificado_afp_complementador", "antiguedad_laboral_complementador",
}


# Tipos que un ejecutivo puede asignar manualmente a un documento ya recibido
# (incluye todos los que el clasificador automático puede requerir, más "otro"
# para revertir una reclasificación por error).
TIPOS_DOCUMENTO_RECLASIFICABLES: Dict[str, str] = {
    "carnet_identidad":             "Cédula de identidad",
    "certificado_afp":              "Certificado de AFP",
    "certificado_rsh":              "Certificado RSH",
    "liquidacion_sueldo":           "Liquidación de sueldo",
    "antiguedad_laboral":           "Antigüedad laboral",
    "carpeta_tributaria_sii":       "Carpeta tributaria SII",
    "declaracion_anual_impuestos":  "Declaración anual de impuestos",
    "libreta_ahorro":               "Cartola / libreta de ahorro",
    "cedula_complementador":               "Cédula del complementador",
    "liquidaciones_complementador":        "Liquidaciones del complementador",
    "certificado_afp_complementador":      "Certificado de AFP del complementador",
    "antiguedad_laboral_complementador":   "Antigüedad laboral del complementador",
    "informe_deudas":               "Informe de deudas",
    "otro":                         "Sin clasificar",
}


@router.patch("/api/clientes/{cliente_id}/documentos/{doc_id}/tipo")
async def api_reclasificar_documento(cliente_id: int, doc_id: str, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        nuevo_tipo = (body.get("tipo") or "").strip()
        if nuevo_tipo not in TIPOS_DOCUMENTO_RECLASIFICABLES:
            return Response(content=f"Tipo inválido: {nuevo_tipo}", status_code=400)

        prospectos = await _supabase_request(
            "GET", "/Prospecto",
            params={
                "cliente_id": f"eq.{cliente_id}",
                "select": "id,paso,nombre," + ",".join(_CAMPOS_CALIFICACION),
                "limit": "1",
            },
        ) or []
        if not prospectos:
            return Response(content="Cliente sin conversación asociada", status_code=404)
        prospecto    = prospectos[0]
        prospecto_id = prospecto["id"]

        doc_existente = await _supabase_request(
            "GET", "/Documento",
            params={"id": f"eq.{doc_id}", "prospecto_id": f"eq.{prospecto_id}", "select": "id", "limit": "1"},
        ) or []
        if not doc_existente:
            return Response(content="Documento no encontrado", status_code=404)

        await _supabase_request(
            "PATCH", f"/Documento?id=eq.{doc_id}",
            json={"tipo": nuevo_tipo},
            extra_headers={"Prefer": "return=minimal"},
        )

        docs_recibidos     = await obtener_documentos_prospecto(prospecto_id)
        datos_calificacion = {campo: prospecto.get(campo) for campo in _CAMPOS_CALIFICACION}
        pendientes          = documentos_pendientes(docs_recibidos, datos_calificacion)

        if not pendientes:
            if prospecto.get("paso") != "DOCS_RECIBIDOS":
                await actualizar_datos_prospecto(prospecto_id, {}, "DOCS_RECIBIDOS")
            asyncio.create_task(_notificar_recordatorio_estado(
                cliente_id=cliente_id,
                nombre_cliente=prospecto.get("nombre") or "Cliente",
                tipo="docs_completos",
            ))

        return {"ok": True, "tipo": nuevo_tipo, "pendientes": pendientes}
    except Exception as e:
        return respuesta_error(e, contexto="api_reclasificar_documento")


@router.post("/api/clientes/{cliente_id}/documentos/upload")
async def api_upload_documento(
    cliente_id: int,
    request: Request,
    file: UploadFile = File(...),
    tipo: str = Form(...),
):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    if tipo not in TIPOS_VALIDOS:
        return Response(content=f"Tipo inválido: {tipo}", status_code=400)

    try:
        prospecto = await _obtener_o_crear_prospecto(cliente_id)
        if not prospecto:
            return Response(content="Cliente no encontrado o sin teléfono", status_code=404)

        prospecto_id = prospecto["id"]
        file_bytes   = await file.read()
        mime_type    = file.content_type or "application/octet-stream"

        # Nombre único para evitar colisiones
        ts            = int(datetime.now(timezone.utc).timestamp() * 1000)
        nombre_seguro = file.filename.replace(" ", "_") if file.filename else f"doc_{ts}"
        nombre_final  = f"{tipo}_{ts}_{nombre_seguro}"

        url_storage = await subir_a_storage(
            file_bytes=file_bytes,
            nombre_archivo=nombre_final,
            prospecto_id=prospecto_id,
            mime_type=mime_type,
        )

        # Reemplazar si ya existe un doc del mismo tipo para este prospecto
        existente = await _supabase_request("GET", "/Documento",
            params={"prospecto_id": f"eq.{prospecto_id}", "tipo": f"eq.{tipo}", "limit": "1"}) or []
        if existente:
            doc_id = existente[0]["id"]
            doc = await _supabase_request("PATCH", f"/Documento?id=eq.{doc_id}",
                json={"nombre_archivo": file.filename or nombre_final,
                      "url_storage": url_storage, "mime_type": mime_type},
                extra_headers={"Prefer": "return=representation"})
            doc = doc[0] if isinstance(doc, list) and doc else {}
        else:
            doc = await insertar_documento(
                prospecto_id=prospecto_id,
                tipo=tipo,
                nombre_archivo=file.filename or nombre_final,
                url_storage=url_storage,
                mime_type=mime_type,
            )

        return {"ok": True, "documento": doc, "prospecto_id": prospecto_id}

    except Exception as e:
        return respuesta_error(e, contexto="api_upload_documento")


@router.get("/api/clientes/{cliente_id}/documentos/{doc_id}/descargar")
async def api_descargar_documento(cliente_id: int, doc_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado
    try:
        # Verificar que el doc pertenece al cliente correcto
        prospectos = await _supabase_request("GET", "/Prospecto",
            params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"})
        prospecto_id = prospectos[0]["id"] if prospectos else None
        if not prospecto_id:
            return Response(content="Documento no encontrado", status_code=404)
        docs = await _supabase_request("GET", "/Documento",
            params={
                "id": f"eq.{doc_id}",
                "prospecto_id": f"eq.{prospecto_id}",
                "select": "id,nombre_archivo,url_storage,mime_type",
                "limit": "1",
            })
        if not docs:
            return Response(content="Documento no encontrado", status_code=404)
        doc = docs[0]
        file_bytes = await _descargar_documento_storage(doc["url_storage"])
        mime = doc.get("mime_type") or "application/octet-stream"
        nombre = re.sub(r'[^\w\-_\. ]', '_', doc.get("nombre_archivo") or "documento")
        return Response(
            content=file_bytes,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
        )
    except Exception as e:
        return respuesta_error(e, contexto="api_descargar_documento")


@router.delete("/api/clientes/{cliente_id}/documentos/{doc_id}")
async def api_eliminar_documento(cliente_id: int, doc_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        prospectos = await _supabase_request("GET", "/Prospecto",
            params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"}) or []
        prospecto_id = prospectos[0]["id"] if prospectos else None

        docs = await _supabase_request("GET", "/Documento",
            params={
                "id": f"eq.{doc_id}",
                **({"prospecto_id": f"eq.{prospecto_id}"} if prospecto_id else {}),
                "select": "id,url_storage",
                "limit": "1",
            }) or []
        if not docs:
            return Response(content="Documento no encontrado", status_code=404)
        doc = docs[0]

        if doc.get("url_storage"):
            try:
                await _eliminar_documento_storage(doc["url_storage"])
            except Exception:
                logger.warning("No se pudo eliminar archivo de storage para documento %s", doc_id)

        await _supabase_request("DELETE", "/Documento",
            params={"id": f"eq.{doc_id}"}, extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_eliminar_documento")


@router.get("/api/clientes/{cliente_id}/preview-evaluacion")
async def api_preview_evaluacion(cliente_id: int, request: Request):
    """Devuelve los datos que se incluirán en el correo de evaluación, sin enviarlo."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return denegado

    clientes = await _supabase_request("GET", "/Cliente",
        params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
    if not clientes:
        return Response(content="Cliente no encontrado", status_code=404)
    c = clientes[0]

    proyecto_id = c.get("proyecto_id")
    # EjecutivoBancario.proyecto: si coincide con el proyecto del cliente, se filtra a ese
    # proyecto; si está vacío, el ejecutivo es global (aparece en todos los proyectos).
    # ProyectoEjecutivo: proyectos ADICIONALES para un ejecutivo que ya tiene su
    # proyecto principal en EjecutivoBancario.proyecto (o es global).
    todos_raw = await _supabase_request("GET", "/EjecutivoBancario",
        params={"disponible": "eq.true", "select": "id,email,ejecutivo,proyecto"}) or []
    ejs_proyecto = [
        {"id": e["id"], "email": e["email"], "nombre": e.get("ejecutivo") or e["email"]}
        for e in todos_raw
        if e.get("email") and proyecto_id and str(e.get("proyecto")) == str(proyecto_id)
    ]
    ejs_globales = [
        {"id": e["id"], "email": e["email"], "nombre": e.get("ejecutivo") or e["email"]}
        for e in todos_raw
        if e.get("email") and not e.get("proyecto")
    ]
    ejs_adicionales = []
    if proyecto_id:
        pe_rows = await _supabase_request("GET", "/ProyectoEjecutivo",
            params={"proyecto_id": f"eq.{proyecto_id}", "select": "ejecutivo_id"}) or []
        ids_adicionales = {r["ejecutivo_id"] for r in pe_rows}
        ejs_adicionales = [
            {"id": e["id"], "email": e["email"], "nombre": e.get("ejecutivo") or e["email"]}
            for e in todos_raw
            if e.get("email") and e["id"] in ids_adicionales
        ]
    # Combinar sin duplicados
    ids_vistos: set = set()
    ejecutivos = []
    for e in ejs_proyecto + ejs_globales + ejs_adicionales:
        if e["id"] not in ids_vistos:
            ids_vistos.add(e["id"])
            ejecutivos.append(e)

    prospectos = await _supabase_request("GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"}) or []
    docs = []
    if prospectos:
        docs = await _supabase_request("GET", "/Documento",
            params={"prospecto_id": f"eq.{prospectos[0]['id']}",
                    "select": "tipo,nombre_archivo"}) or []

    proyecto_nombre     = "No registrado"
    proyecto_ubicacion  = "No registrada"
    inmobiliaria_nombre = "No registrada"
    valor_uf: float | None = None
    monto_subsidio: float | None = None
    entrega_label = "Futura"
    condicion_propiedad = "nueva"

    if c.get("proyecto_id"):
        proy = await _supabase_request("GET", "/Proyecto",
            params={"id": f"eq.{c['proyecto_id']}",
                    "select": "nombre,ubicacion,condicion_propiedad,Inmobiliaria(nombre)",
                    "limit": "1"})
        if proy:
            p = proy[0]
            proyecto_nombre    = p.get("nombre") or "No registrado"
            proyecto_ubicacion = p.get("ubicacion") or "No registrada"
            condicion_propiedad = p.get("condicion_propiedad") or "nueva"
            inm = (p.get("Inmobiliaria") or {})
            inmobiliaria_nombre = inm.get("nombre") or "No registrada"

        # Usar la tipología + etapa específica asignada al cliente
        if c.get("etapa_tipologia_id"):
            et_rows = await _supabase_request("GET", "/EtapaTipologia",
                params={
                    "id": f"eq.{c['etapa_tipologia_id']}",
                    "select": "Tipologia(valor_uf,monto_subsidio),Etapa(estado)",
                    "limit": "1",
                })
            if et_rows:
                tip   = et_rows[0].get("Tipologia") or {}
                etapa = et_rows[0].get("Etapa") or {}
                if tip.get("valor_uf") is not None:
                    valor_uf = float(tip["valor_uf"])
                if tip.get("monto_subsidio") is not None:
                    monto_subsidio = float(tip["monto_subsidio"])
                if etapa.get("estado"):
                    entrega_label = "Inmediata" if etapa["estado"] == "entrega_inmediata" else "Futura"

    ahorro_uf = c.get("ahorro_uf")
    ahorro_f  = float(ahorro_uf) if ahorro_uf is not None else None
    monto_credito = (
        round(valor_uf - (monto_subsidio or 0) - ahorro_f, 2)
        if (valor_uf is not None and ahorro_f is not None) else None
    )

    NOMBRES_TIPO = {
        "liquidacion_sueldo": "Liquidación de sueldo",
        "certificado_afp": "Certificado AFP",
        "carnet_identidad": "Cédula de identidad",
        "libreta_ahorro": "Libreta de ahorro",
        "informe_deudas": "Informe de deudas",
        "antiguedad_laboral": "Antigüedad laboral",
        "otro": "Otro documento",
    }
    from collections import Counter
    conteo = Counter(d.get("tipo", "otro") for d in docs)
    docs_resumen = [
        {"tipo": NOMBRES_TIPO.get(t, t), "cantidad": n}
        for t, n in conteo.items()
    ]

    alias = perfil.get("email_alias") or perfil.get("correo") or (get_settings().email_admin or "")
    rut = c.get("Rut") or "—"
    return {
        "remitente": alias,
        "ejecutivos": ejecutivos,
        "asunto": f"Evaluación Hipotecaria {rut} – Proyecto {proyecto_nombre} Entrega {entrega_label}",
        "ahorro_requerido": ahorro_uf is None,
        "tipologia_requerida": not bool(c.get("etapa_tipologia_id")),
        "cliente": {
            "nombre":        (c.get("Contacto") or "Sin nombre").strip(),
            "rut":           rut,
            "telefono":      c.get("Telefono") or "—",
            "correo":        (c.get("Correo") or "").strip() or None,
            "condicion":     CONDICION_LABEL.get(condicion_propiedad, condicion_propiedad),
            "renta":         c.get("Tramo de renta") or "—",
            "proyecto":      proyecto_nombre,
            "direccion":     proyecto_ubicacion,
            "inmobiliaria":  inmobiliaria_nombre,
            "entrega":       entrega_label,
            "valor_uf":      valor_uf,
            "monto_subsidio": monto_subsidio,
            "ahorro_uf":     ahorro_f,
            "monto_credito": monto_credito,
        },
        "documentos": docs_resumen,
        "total_docs": len(docs),
    }


_evaluaciones_en_curso: Dict[int, float] = {}


_EVAL_LOCK_TIMEOUT_SEG = 30  # válvula de seguridad si algo deja el lock colgado


@router.post("/api/clientes/{cliente_id}/enviar-evaluacion")
async def api_enviar_evaluacion(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)

    ahora = time.monotonic()
    en_curso_desde = _evaluaciones_en_curso.get(cliente_id)
    if en_curso_desde is not None and (ahora - en_curso_desde) < _EVAL_LOCK_TIMEOUT_SEG:
        return Response(
            content="Ya se está enviando una evaluación para este cliente, espera unos segundos.",
            status_code=409, media_type="text/plain")
    _evaluaciones_en_curso[cliente_id] = ahora
    try:
        body: dict = {}
        try:
            body = await request.json()
        except Exception as e_body:
            _cuerpo_opcional_ilegible(request, "api_enviar_evaluacion", e_body)
        ejecutivos_emails = body.get("ejecutivos") or None
        nota = (body.get("nota") or "").strip()[:500] or None
        result = await _enviar_email_evaluacion(
            cliente_id, usuario=perfil, ejecutivos_emails=ejecutivos_emails, nota=nota
        )
        return {"ok": True, **result}
    except Exception as e:
        return respuesta_error(e, contexto="api_enviar_evaluacion")
    finally:
        _evaluaciones_en_curso.pop(cliente_id, None)


TEMPLATE_RECORDATORIO_WA = get_settings().template_recordatorio_wa


@router.post("/api/clientes/{cliente_id}/recordatorio-wa")
async def api_recordatorio_wa(cliente_id: int, request: Request):
    """Envía una plantilla WA al número personal (celular) del ejecutivo
    recordándole contactar a un cliente específico."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    celular = _normalize_phone(perfil.get("celular") or "")
    if not celular:
        return Response(content="Tu perfil no tiene número celular configurado", status_code=400)
    try:
        body   = await request.json()
        motivo = (body.get("motivo") or "Sin motivo especificado").strip()
        rows   = await _supabase_request("GET", "/Cliente",
            params={"id": f"eq.{cliente_id}", "select": "Contacto,Telefono", "limit": "1"})
        if not rows:
            return Response(content="Cliente no encontrado", status_code=404)
        c       = rows[0]
        nombre  = (c.get("Contacto") or "—").strip()
        telefono_cliente = _normalize_phone(c.get("Telefono") or "") or (c.get("Telefono") or "—")
        await send_whatsapp_template(
            to=celular,
            template_name=TEMPLATE_RECORDATORIO_WA,
            language_code="es_CL",
            body_text_params=[nombre, telefono_cliente, motivo],
        )
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_recordatorio_wa")
