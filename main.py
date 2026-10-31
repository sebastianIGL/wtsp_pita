from __future__ import annotations
from fastapi import FastAPI, Request, Response, BackgroundTasks
from contextlib import asynccontextmanager
import asyncio
from typing import Dict, List, Optional
import logging

_error_dotenv: Optional[Exception] = None   # se registra en cuanto existe el logger (más abajo)
try:
    from dotenv import load_dotenv
    import pathlib
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent / ".env", override=True)
except Exception as _e_dotenv:
    _error_dotenv = _e_dotenv

from app.core.config import get_settings, registrar_configuracion
from app.core.utilidades import _normalize_phone
from app.services.webhook_entrada import _es_mensaje_nuevo, _lanzar, _registrar_firma_webhook
from app.services.webhook_estados import _procesar_status
from app.services.webhook import DELAY_RESPUESTA_SEGUNDOS, _procesar_grupo, _procesar_webhook
from app.services.recovery import _recovery_loop
from app.routers.paginas import router as paginas_router
from app.routers.progreso import router as progreso_router
from app.routers.publico import router as publico_router
from app.routers.formulario import router as formulario_router
from app.routers.usuarios import router as usuarios_router
from app.routers.empresas import router as empresas_router
from app.routers.proyectos import router as proyectos_router
from app.routers.tipologias import router as tipologias_router
from app.routers.ejecutivos import router as ejecutivos_router
from app.routers.ingesta import router as ingesta_router
from app.routers.documentos import router as documentos_router
from app.routers.clientes import router as clientes_router
from app.routers.clientes_acciones import router as clientes_acciones_router
from app.routers.health import router as health_router
from app.core.logging_config import RequestContextMiddleware, configurar_logging
from app.core.user_cache import user_cache
from app.core.errors import registrar_error
from app.core.webhook_signature import verificar_firma
from app.core.llm import PRESUPUESTO_TOTAL_S, cerrar_anthropic, margen_frente_a_recovery
from app.core.http import cerrar_http, iniciar_http

from fastapi.middleware.cors import CORSMiddleware
from app.core.estaticos import FrontendEstatico
from app.core.security_headers import _SecurityHeadersMiddleware
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
