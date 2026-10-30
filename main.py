from __future__ import annotations
from fastapi import FastAPI, Request, Response, BackgroundTasks
from contextlib import asynccontextmanager
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
import logging
import time

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
from app.core.rate_limit import _rate_limit_ok, _rl_store  # noqa: F401  (se re-exportan con los mismos nombres)
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
from app.services.bot.prompts import PASOS_CONFIG  # noqa: F401  (se re-exporta con el mismo nombre)
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
)
from app.routers.progreso import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as progreso_router,
)
from app.core.cache_proyectos import _cache_proyectos  # noqa: F401  (se re-exportan con los mismos nombres)
from app.core.cuerpo_opcional import _cuerpo_opcional_ilegible  # noqa: F401  (se re-exporta con el mismo nombre)
from app.routers.publico import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as publico_router,
)
from app.routers.formulario import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as formulario_router,
)
from app.routers.usuarios import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as usuarios_router,
    WA_WABA_ID,
)
from app.routers.empresas import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as empresas_router,
)
from app.routers.proyectos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as proyectos_router,
)
from app.routers.tipologias import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as tipologias_router,
    _ETAPA_SELECT,
    _ETAPA_TIP_SELECT,
    _TIPOLOGIA_SELECT,
)
from app.routers.ejecutivos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as ejecutivos_router,
)
from app.routers.ingesta import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as ingesta_router,
)
from app.routers.documentos import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as documentos_router,
    TIPOS_DOCUMENTO_RECLASIFICABLES,
    TIPOS_VALIDOS,
    _evaluaciones_en_curso,
)
from app.routers.clientes import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as clientes_router,
    _INBOX_SELECT,
)
from app.routers.clientes_acciones import (  # noqa: F401  (se re-exportan con los mismos nombres)
    router as clientes_acciones_router,
)
from app.routers.health import router as health_router
from app.core.logging_config import RequestContextMiddleware, configurar_logging
from app.core.user_cache import user_cache
from app.core.errors import registrar_error
from app.core.webhook_signature import verificar_firma
from app.core.llm import PRESUPUESTO_TOTAL_S, cerrar_anthropic, margen_frente_a_recovery
from app.core.http import cerrar_http, iniciar_http

from fastapi.middleware.cors import CORSMiddleware
from app.core.estaticos import FrontendEstatico
from app.core.security_headers import _SecurityHeadersMiddleware  # noqa: F401  (se re-exporta con el mismo nombre)
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
# Variables de entorno
# ---------------------------------------------------------------------------

TOKEN_VERIFICACION = get_settings().wa_verify_token


# Acceso a Supabase: movido a app/clients/supabase.py (se importa arriba con los mismos nombres).


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
# API — Usuarios
# ---------------------------------------------------------------------------


app.include_router(usuarios_router)


# ---------------------------------------------------------------------------
# API — Importar clientes desde CSV
# ---------------------------------------------------------------------------


app.include_router(clientes_acciones_router)


# ---------------------------------------------------------------------------
# API para el frontend
# ---------------------------------------------------------------------------


app.include_router(empresas_router)


app.include_router(proyectos_router)


# ── Tipologia ─────────────────────────────────────────────────────────────────


app.include_router(tipologias_router)


# ── Scraping: buydepa ───────────────────────────────────────────────────────


app.include_router(ejecutivos_router)


app.include_router(clientes_router)


app.include_router(documentos_router)


app.include_router(paginas_router)


app.include_router(publico_router)


app.include_router(formulario_router)


# ── API Progreso usuario (requiere JWT de Supabase en Authorization header) ──


app.include_router(progreso_router)


app.mount("/", FrontendEstatico(directory="frontend", html=True), name="frontend")   # sin las plantillas internas (app/core/estaticos.py)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=get_settings().host,
        port=get_settings().port,
        reload=True,
    )
