from __future__ import annotations
from fastapi import FastAPI, Request, Response, BackgroundTasks, UploadFile, File, Form
from contextlib import asynccontextmanager
from fastapi.responses import FileResponse, RedirectResponse
import asyncio
import os
import json
import random
import re
import httpx
import csv
import io
import unicodedata
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import logging
import time
import base64
from html import escape as html_escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders as _email_encoders

try:
    import anthropic
except ImportError:
    anthropic = None

_error_dotenv: Optional[Exception] = None   # se registra en cuanto existe el logger (más abajo)
try:
    from dotenv import load_dotenv
    import pathlib
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent / ".env", override=True)
except Exception as _e_dotenv:
    _error_dotenv = _e_dotenv

from app.core.config import get_settings, registrar_configuracion
from app.clients.supabase import (  # noqa: F401  (se re-exportan: main.py y los tests los usan con estos nombres)
    _supabase_headers,
    _supabase_request,
    _supabase_rest_base,
    _supabase_service_role_key,
    _supabase_url,
)
from app.clients.supabase_storage import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _descargar_documento_storage,
    StorageError,
    _eliminar_documento_storage,
    subir_a_storage,
    subir_imagen_proyecto,
)
from app.clients.supabase_auth import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _get_usuario_actual,
    _invitar_usuario_supabase,
    _uid_desde_jwt,
    cambiar_password_supabase,
)
from app.core.utilidades import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _CSV_COL_MAP,
    _es_violacion_unica,
    _horas_desde,
    _norm_col,
    _norm_row,
    _norm_row_keys,
    _normalizar_nombre,
    _normalizar_telefono_cl,
    _normalizar_wamid,
    _normalize_phone,
    _safe_httpx_error,
    _slugify,
    _utc_now_iso,
)
from app.services.horarios import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _TZ_CHILE,
    HORARIO_AVISOS_FIN,
    HORARIO_AVISOS_INICIO,
    HORARIO_URGENTE_INICIO,
    _dentro_de,
    _dentro_horario_avisos,
    _dentro_horario_bot,
    _dentro_horario_urgente,
    _horas_bot,
    _segundos_hasta_apertura,
    _segundos_hasta_horario_avisos,
    _segundos_hasta_horario_bot,
    _segundos_hasta_horario_urgente,
)
from app.services.documentos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _CAMPOS_CALIFICACION,
    _DOCS_BASE,
    _DOCS_CONDICIONALES,
    _KEYWORDS_TIPO,
    _TIPO_ALIAS,
    _docs_requeridos,
    _lista_documentos_requeridos_texto,
    clasificar_documento,
    clasificar_texto_documento,
    documentos_pendientes,
    resumen_documentos,
)
from app.clients.mindicador import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _CACHE_UF_TTL,
    _UF_VALOR_RESPALDO,
    _cache_uf,
    _obtener_valor_uf,
)
from app.clients.gmail import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _gmail_get_token_sync,
    _gmail_send,
)
from app.clients.whatsapp import (  # noqa: F401  (se re-exportan con los mismos nombres)
    ID_NUMERO_TELEFONO,
    TOKEN_ACCESO,
    VERSION_GRAPH,
    _build_body_params,
    descargar_media_whatsapp,
    send_whatsapp_message,
    send_whatsapp_template,
)
from app.core.permisos import _solo_admin, _solo_owner  # noqa: F401  (se re-exportan con los mismos nombres)
from app.core.rate_limit import _rate_limit_ok, _rl_store, ip_cliente  # noqa: F401  (se re-exportan con los mismos nombres)
from app.services.evaluacion_email import (  # noqa: F401  (se re-exportan con los mismos nombres)
    CONDICION_LABEL,
    _enviar_email_evaluacion,
)
from app.services.buydepa import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _EXT_IMAGEN,
    BUYDEPA_API_URL,
    BUYDEPA_INMOBILIARIA_ID,
    UF_UMBRAL_CLP,
    _actualizar_stock_proyecto,
    _completar_tipologia_depa,
    _imagenes_buydepa,
    _normalizar_codigo_depa,
    _payload_tipologia_buydepa,
    _precio_a_uf,
    _registrar_cambio_depa,
    _registrar_log_scraping,
    _registrar_oferta_depa,
    _revertir_proyecto_huerfano,
    _sincronizar_buydepa_stream,
    _sincronizar_imagenes_depa,
    _sincronizar_precio_depa,
    _urls_imagen,
)
from app.services.plantillas import (  # noqa: F401  (se re-exportan con los mismos nombres)
    TEMPLATE_VARS_MAP,
    _pool_plantilla,
)
from app.services.proyectos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _PROYECTO_SELECT,
    _PROYECTO_SELECT_LIGHT,
    _PROYECTO_SELECT_PANEL,
    _aplicar_campos_proyecto,
    _buscar_id_proyecto,
    obtener_proyecto_por_id,
)
from app.services.bot_prompts import PASOS_CONFIG  # noqa: F401  (se re-exporta con el mismo nombre)
from app.services.bot import (  # noqa: F401  (se re-exportan con los mismos nombres)
    ANTHROPIC_API_KEY,
    _construir_mind,
    _obtener_otros_proyectos,
    _resumir_proyecto,
    generar_respuesta_ia,
)
from app.services.avisos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _TEXTOS_RECORDATORIO,
    _notificar_ejecutivo_wa,
    _notificar_interesado,
    _notificar_nuevo_lead,
    _notificar_recordatorio_estado,
)
from app.services.mensajes import (  # noqa: F401  (se re-exportan con los mismos nombres)
    insertar_mensaje,
    obtener_historial_mensajes,
)
from app.services.documentos_prospecto import (  # noqa: F401  (se re-exportan con los mismos nombres)
    TOLERANCIA_RELOJ_S,
    VENTANA_TIPO_POR_TEXTO_MIN,
    _tipo_segun_textos,
    etiquetar_documentos_sin_tipo,
    insertar_documento,
    obtener_documentos_prospecto,
    tipo_por_texto_desde_envio,
)
from app.services.prospectos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _CAMPOS_COLUMNA_PROPIA,
    _cambiar_proyecto_prospecto,
    actualizar_datos_prospecto,
    obtener_prospecto_por_id,
    upsert_prospecto,
)
from app.services.webhook_entrada import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _WAMID_MAX,
    _es_mensaje_nuevo,
    _lanzar,
    _registrar_firma_webhook,
    _tareas_fondo,
    _wamids_orden,
    _wamids_procesados,
)
from app.services.webhook_estados import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _META_ESTADO_MAP,
    _ORDEN_ESTADO_PLANTILLA,
    _procesar_status,
)
from app.services.webhook_media import _procesar_media  # noqa: F401  (se re-exporta con el mismo nombre)
from app.services.webhook import (  # noqa: F401  (se re-exportan con los mismos nombres)
    DELAY_RESPUESTA_SEGUNDOS,
    _procesar_grupo,
    _procesar_webhook,
)
from app.services.recovery import (  # noqa: F401  (se re-exportan con los mismos nombres)
    _RECOVERY_CICLOS_ENTRE_AVISOS,
    _recovery_loop,
    _registrar_pendientes_vencidos,
)
from app.routers.paginas import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as paginas_router,
    page_blog,
    page_como_funciona,
    page_empresas,
    page_empresas_html,
    page_home,
    page_inmobiliaria,
    page_inmobiliaria_html,
    page_login,
    page_panel,
    page_perfil,
    page_proyecto,
    page_proyecto_html,
    page_reset_password,
    page_subsidios,
    page_viviendas,
    templates,
)
from app.routers.progreso import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as progreso_router,
    api_get_progreso,
    api_set_progreso,
)
from app.core.cache_proyectos import _CACHE_TTL, _cache_proyectos  # noqa: F401  (se re-exportan con los mismos nombres)
from app.core.cuerpo_opcional import _cuerpo_opcional_ilegible  # noqa: F401  (se re-exporta con el mismo nombre)
from app.routers.publico import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as publico_router,
    api_contacto,
    api_newsletter,
    api_proyectos_landing,
)
from app.routers.formulario import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as formulario_router,
    _FORMULARIO_CAMPOS,
    api_actualizar_formulario,
    api_convertir_formulario,
    api_crear_formulario,
    api_obtener_formulario,
)
from app.routers.usuarios import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as usuarios_router,
    WA_WABA_ID,
    _log_correo,
    api_actualizar_usuario,
    api_cambiar_password,
    api_crear_usuario,
    api_listar_plantillas_config,
    api_listar_templates,
    api_listar_usuarios,
    api_log_correos,
    api_log_whatsapp,
    api_upsert_plantilla_config,
    api_usuario_actual,
)
from app.routers.empresas import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as empresas_router,
    api_crear_empresa,
    api_crear_inmobiliaria,
    api_editar_empresa,
    api_editar_inmobiliaria,
    api_listar_empresas,
    api_listar_industrias,
    api_listar_inmobiliarias,
)
from app.routers.proyectos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as proyectos_router,
    api_crear_proyecto,
    api_editar_proyecto,
    api_listar_proyectos,
    api_subir_imagen_proyecto,
)
from app.routers.tipologias import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as tipologias_router,
    _CAMPOS_ETAPA,
    _CAMPOS_TIPOLOGIA,
    _ETAPA_SELECT,
    _ETAPA_TIP_SELECT,
    _TIPOLOGIA_SELECT,
    api_crear_etapa,
    api_crear_etapa_tipologia,
    api_crear_tipologia,
    api_editar_etapa,
    api_editar_etapa_tipologia,
    api_editar_tipologia,
    api_eliminar_etapa,
    api_eliminar_etapa_tipologia,
    api_eliminar_tipologia,
    api_find_or_create_etapa_tipologia,
    api_get_etapa_tipologia,
    api_listar_etapa_tipologia,
    api_listar_etapas,
    api_listar_tipologias,
)
from app.routers.ejecutivos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as ejecutivos_router,
    api_asignar_ejecutivo,
    api_crear_ejecutivo,
    api_editar_ejecutivo,
    api_ejecutar_scraping_buydepa,
    api_eliminar_ejecutivo,
    api_listar_ejecutivos,
    api_listar_logs_scraping,
    api_listar_proyecto_ejecutivos,
    api_quitar_ejecutivo,
)
from app.routers.ingesta import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as ingesta_router,
    _ingest_api_key,
    ingestar_prospecto,
)
from app.routers.movendo import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as movendo_router,
    _get_default_user,
    _movendo_webhook_key,
    _verificar_movendo_auth,
    api_movendo_actualizar_contacto,
    api_movendo_nuevo_cliente,
)
from app.routers.documentos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as documentos_router,
    TEMPLATE_RECORDATORIO_WA,
    TIPOS_DOCUMENTO_RECLASIFICABLES,
    TIPOS_VALIDOS,
    _EVAL_LOCK_TIMEOUT_SEG,
    _evaluaciones_en_curso,
    _obtener_o_crear_prospecto,
    api_descargar_documento,
    api_documentos_cliente,
    api_eliminar_documento,
    api_enviar_evaluacion,
    api_preview_evaluacion,
    api_reclasificar_documento,
    api_recordatorio_wa,
    api_upload_documento,
)
from app.routers.health import router as health_router
from app.core.logging_config import RequestContextMiddleware, configurar_logging, enmascarar
from app.core.user_cache import reducir_perfil, user_cache
from app.core.errors import AppError, detalle_error, mensaje_publico, registrar_error, respuesta_error
from app.core.webhook_signature import debe_rechazar, verificar_firma
from app.core.llm import PRESUPUESTO_TOTAL_S, cerrar_anthropic, cliente_anthropic, margen_frente_a_recovery
from app.core.http import cerrar_http, http_client, iniciar_http, request_con_reintentos_get

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

@asynccontextmanager
async def _lifespan(app: FastAPI):
    registrar_configuracion(logger)   # falla solo si falta lo obligatorio; registra nombres, nunca valores
    if margen_frente_a_recovery(DELAY_RESPUESTA_SEGUNDOS) < 0:
        logger.warning("BOT_REPLY_DELAY=%s + presupuesto del modelo %ss supera los 180 s del _recovery_loop: "
                       "una respuesta lenta podría enviarse dos veces", DELAY_RESPUESTA_SEGUNDOS, PRESUPUESTO_TOTAL_S)
    await iniciar_http()              # cliente httpx compartido (se cierra al apagar)
    asyncio.create_task(_recovery_loop())
    try:
        yield
    finally:
        await cerrar_http()
        await cerrar_anthropic()
        user_cache.vaciar()

# Documentación automática (/docs, /redoc, /openapi.json): cerrada por defecto porque
# publica el mapa completo de la API. En desarrollo se activa con ENABLE_DOCS=true.
_docs_enabled = get_settings().enable_docs

app = FastAPI(
    lifespan=_lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
app.include_router(health_router)   # /health: solo el proceso, sin Supabase ni terceros


# ── Security headers ──────────────────────────────────────────────────────────
class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

# Orden de add_middleware: el último en agregarse queda más externo (primer en procesar).
# RequestContext → GZip → CORS → SecurityHeaders → App
app.add_middleware(_SecurityHeadersMiddleware)

_cors_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
_site_url = get_settings().site_url or ""
if _site_url:
    _cors_origins.append(_site_url.rstrip("/"))
    # Agrega variante con/sin www
    if _site_url.startswith("https://www."):
        _cors_origins.append("https://" + _site_url[len("https://www."):].rstrip("/"))
    elif _site_url.startswith("https://"):
        _cors_origins.append("https://www." + _site_url[len("https://"):].rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(RequestContextMiddleware)   # el más externo: request_id, X-Request-ID y log de acceso


logger = logging.getLogger("wtsp_pita")
if not logger.handlers:
    configurar_logging(get_settings().log_level)
if _error_dotenv is not None:
    registrar_error(_error_dotenv, "carga de .env al importar main", mensaje="No se pudo cargar el .env (se sigue con el entorno)")

# Debounce: una tarea pendiente por número — la nueva cancela la anterior
_pending_tasks: Dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Helpers genéricos
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------

TOKEN_VERIFICACION = get_settings().wa_verify_token


# ---------------------------------------------------------------------------
# Documentos requeridos y sus cantidades mínimas
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Supabase REST
# ---------------------------------------------------------------------------


# Acceso a Supabase: movido a app/clients/supabase.py (se importa arriba con los mismos nombres).


# ---------------------------------------------------------------------------
# Operaciones sobre prospectos
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# WhatsApp Media — descarga y sube a Supabase Storage
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# IA — construcción de contexto adaptativo por cadena jerárquica
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# IA — respuesta con contexto de paso
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Webhook WhatsApp
# ---------------------------------------------------------------------------

@app.get("/webhook")
async def verify_webhook(request: Request):
    if not TOKEN_VERIFICACION:
        return Response(content="WA_VERIFY_TOKEN no está configurado", status_code=500)
    params = request.query_params
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == TOKEN_VERIFICACION:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    # Firma de Meta (X-Hub-Signature-256) sobre el cuerpo EXACTO. Por defecto solo se registra; con
    # WA_SIGNATURE_ENFORCE=true se rechaza lo que no venga firmado por Meta.
    cuerpo_crudo = await request.body()
    firma = verificar_firma(cuerpo_crudo, request.headers.get("X-Hub-Signature-256"), get_settings().wa_app_secret)
    if _registrar_firma_webhook(firma):
        return Response(content="Forbidden", status_code=403, media_type="text/plain")
    payload = await request.json()
    try:
        mensajes_nuevos: List[Dict] = []
        for entry in (payload.get("entry") or []) if isinstance(payload, dict) else []:
            for change in (entry.get("changes") or []) if isinstance(entry, dict) else []:
                value = (change.get("value") or {}) if isinstance(change, dict) else {}
                # Actualizaciones de estado (entregado, leído, fallido): todas las del lote
                for estado in value.get("statuses") or []:
                    _lanzar(_procesar_status(estado))
                for msg in value.get("messages") or []:
                    if _es_mensaje_nuevo(msg):
                        mensajes_nuevos.append(msg)

        # Los mensajes de un mismo número se atienden en orden y responde solo el último (debounce por número);
        # los de números distintos son independientes.
        por_numero: Dict[str, List[Dict]] = {}
        for msg in mensajes_nuevos:
            por_numero.setdefault(_normalize_phone(msg.get("from", "")), []).append(msg)

        # Devuelve 200 a Meta de inmediato — debounce: cancela tarea anterior del mismo número
        for from_number, grupo in por_numero.items():
            existing = _pending_tasks.get(from_number)
            if existing and not existing.done():
                existing.cancel()
            tarea = _procesar_webhook(grupo[0]) if len(grupo) == 1 else _procesar_grupo(grupo)
            _pending_tasks[from_number] = _lanzar(tarea)

    except Exception:
        logger.exception("Error en /webhook")

    return {"ok": True}


# ---------------------------------------------------------------------------
# Ingesta de prospectos (primer mensaje)
# ---------------------------------------------------------------------------


app.include_router(ingesta_router)


# ---------------------------------------------------------------------------
# WhatsApp Cloud API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Auth JWT (Supabase)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# API — Usuarios
# ---------------------------------------------------------------------------


app.include_router(usuarios_router)


# ---------------------------------------------------------------------------
# API — Templates WhatsApp (dinámico desde Meta)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# API — Configuración de plantillas WhatsApp
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# API — Importar clientes desde CSV
# ---------------------------------------------------------------------------

@app.post("/api/clientes/importar")
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


# ---------------------------------------------------------------------------
# API — Enviar primer WhatsApp seleccionando template
# ---------------------------------------------------------------------------

@app.post("/api/clientes/{cliente_id}/enviar-wtsp")
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


# ---------------------------------------------------------------------------
# API para el frontend
# ---------------------------------------------------------------------------


app.include_router(empresas_router)


app.include_router(proyectos_router)


# ── Tipologia ─────────────────────────────────────────────────────────────────


app.include_router(tipologias_router)


# ── Etapa ─────────────────────────────────────────────────────────────────────


# ── EtapaTipologia ────────────────────────────────────────────────────────────


# ── Scraping: buydepa ───────────────────────────────────────────────────────


app.include_router(ejecutivos_router)


# ── Ejecutivos bancarios ──────────────────────────────────────────────────────


# ── ProyectoEjecutivo ─────────────────────────────────────────────────────────


@app.get("/api/clientes")
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


@app.post("/api/clientes")
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


@app.patch("/api/clientes/{cliente_id}")
async def api_actualizar_cliente(cliente_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
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


# ── Inbox (prospectos sin cliente) ────────────────────────────────────────────

_INBOX_SELECT = (
    "id,telefono_e164,nombre,rut,rango_sueldo,numero_integrantes,"
    "proyecto_id,Proyecto(nombre),"
    "paso,estado,ultimo_texto_entrante,ultimo_entrante_en,actualizado_en"
)


@app.get("/api/inbox")
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


@app.post("/api/inbox/{prospecto_id}/registrar")
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


@app.get("/api/clientes/{cliente_id}/conversacion")
async def api_conversacion_cliente(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
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


@app.get("/api/prospectos/{prospecto_id}/conversacion")
async def api_conversacion_prospecto(prospecto_id: str, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
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


@app.get("/api/clientes/{cliente_id}/contexto")
async def api_contexto_cliente(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
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


app.include_router(documentos_router)


# ── Recordatorio WA al ejecutivo ─────────────────────────────────────────────


# ── Movendo / Zonapropia Integration ─────────────────────────────────────────


app.include_router(movendo_router)


# ── Recordatorios puntuales por avance del bot (event-driven) ─────────────────


app.include_router(paginas_router)


app.include_router(publico_router)


app.include_router(formulario_router)


# ── API Progreso usuario (requiere JWT de Supabase en Authorization header) ──


app.include_router(progreso_router)


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=get_settings().host,
        port=get_settings().port,
        reload=True,
    )
