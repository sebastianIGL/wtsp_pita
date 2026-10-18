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
templates = Jinja2Templates(directory="frontend")

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


# ── Caché en memoria para endpoints públicos de la landing ────────────────────
_cache_proyectos: Dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 300  # 5 minutos


logger = logging.getLogger("wtsp_pita")
if not logger.handlers:
    configurar_logging(get_settings().log_level)
if _error_dotenv is not None:
    registrar_error(_error_dotenv, "carga de .env al importar main", mensaje="No se pudo cargar el .env (se sigue con el entorno)")

# Debounce: una tarea pendiente por número — la nueva cancela la anterior
_pending_tasks: Dict[str, asyncio.Task] = {}


_RECOVERY_CICLOS_ENTRE_AVISOS = 20   # un aviso de pendientes vencidos al arrancar y luego cada ~1 h (20 × 3 min)


async def _registrar_pendientes_vencidos(limite_iso: str, max_horas: int) -> None:
    """Deja en el log los pendientes más antiguos que el tope (no se reprocesan). Solo lectura."""
    try:
        vencidos = await _supabase_request("GET", "/Prospecto", params=[
            ("pendiente_respuesta", "eq.true"),
            ("ultimo_entrante_en", f"lt.{limite_iso}"),
            ("select", "id,telefono_e164,ultimo_entrante_en"),
            ("order", "ultimo_entrante_en.asc"),
            ("limit", "50"),
        ]) or []
        if not vencidos:
            return
        detalle = "; ".join(
            f"{v.get('id')}:{enmascarar(v.get('telefono_e164') or '')}:{(_horas_desde(v.get('ultimo_entrante_en')) or 0):.0f}h"
            for v in vencidos[:20])
        logger.warning(
            "Recovery: %d%s pendiente(s) con más de %d h ignorados (fuera de la ventana de 24 h de WhatsApp, "
            "no se les puede responder con un mensaje libre): %s",
            len(vencidos), "+" if len(vencidos) == 50 else "", max_horas, detalle)
    except Exception:
        logger.exception("Recovery: no se pudo listar los pendientes vencidos")


async def _recovery_loop():
    """Cada 3 min reintenta conversaciones donde el cliente escribió y el bot no respondió.

    Solo toma pendientes con más de 3 min y menos de RECOVERY_MAX_ANTIGUEDAD_HORAS (24 por defecto, la
    ventana de WhatsApp para mensajes libres), los más antiguos primero. Los demás se ignoran y se dejan
    registrados en el log."""
    await asyncio.sleep(60)  # 1 min de gracia al arrancar
    ciclo = 0
    while True:
        try:
            if not _dentro_horario_bot():
                # Solo ocurre si se configuró una ventana horaria (por defecto el bot es 24/7).
                # Fuera de la ventana el webhook guarda el mensaje y no responde; si el loop siguiera
                # corriendo, cada 3 min volvería a recoger los mismos pendientes y a registrar
                # "respuesta pospuesta". Se retoma solo al abrir el horario.
                await asyncio.sleep(_segundos_hasta_horario_bot())
                continue
            ahora = datetime.now(timezone.utc)
            hace_3_min = (ahora - timedelta(minutes=3)).isoformat()
            max_horas = get_settings().recovery_max_antiguedad_horas
            filtros = [
                ("pendiente_respuesta", "eq.true"),
                ("ultimo_entrante_en", f"lt.{hace_3_min}"),
                ("select", "id,telefono_e164,ultimo_texto_entrante"),
                ("order", "ultimo_entrante_en.asc"),   # primero los más antiguos (los que antes vencen)
                ("limit", "10"),
            ]
            limite_antiguedad = None
            if max_horas > 0:
                limite_antiguedad = (ahora - timedelta(hours=max_horas)).isoformat()
                filtros.append(("ultimo_entrante_en", f"gte.{limite_antiguedad}"))
            pendientes = await _supabase_request("GET", "/Prospecto", params=filtros) or []
            for p in pendientes:
                telefono   = p.get("telefono_e164") or ""
                ultimo_txt = p.get("ultimo_texto_entrante") or ""
                if not telefono or not ultimo_txt:
                    continue
                logger.info("Recovery: reprocesando mensaje pendiente de %s", telefono)
                asyncio.create_task(_procesar_webhook({
                    "from": telefono,
                    "type": "text",
                    "text": {"body": ultimo_txt},
                    "id":   f"recovery_{p['id']}",
                }, es_reintento=True))
            if limite_antiguedad and ciclo % _RECOVERY_CICLOS_ENTRE_AVISOS == 0:
                await _registrar_pendientes_vencidos(limite_antiguedad, max_horas)
            ciclo += 1
        except Exception:
            logger.exception("Error en _recovery_loop")
        await asyncio.sleep(180)  # revisar cada 3 minutos




# Cache en memoria para deduplicar webhooks duplicados de Meta (últimos 500 wamids)
_wamids_procesados: set = set()
_wamids_orden: list = []
_WAMID_MAX = 500


# ---------------------------------------------------------------------------
# Helpers genéricos
# ---------------------------------------------------------------------------

def _cuerpo_opcional_ilegible(request: Request, contexto: str, e: Exception) -> None:
    """El cuerpo JSON de estos endpoints es opcional: sin cuerpo no es un error. Si vino un cuerpo y no se
    pudo leer, se avisa en el log (y se sigue sin él, como siempre)."""
    if request.headers.get("content-length") not in (None, "0"):
        logger.warning("%s: el cuerpo no es JSON válido y se ignora (%s)", contexto, type(e).__name__,
                       extra={"contexto": contexto})


# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------

TOKEN_VERIFICACION = get_settings().wa_verify_token


WA_WABA_ID         = get_settings().wa_waba_id
ANTHROPIC_API_KEY  = get_settings().anthropic_api_key

# ---------------------------------------------------------------------------
# Documentos requeridos y sus cantidades mínimas
# ---------------------------------------------------------------------------


# Campos que tienen columna propia en Prospecto (no van al blob datos)
_CAMPOS_COLUMNA_PROPIA: set = {
    *_CAMPOS_CALIFICACION,
    "motivo_no_interesado", "fecha_tentativa_recontacto", "opt_out",
    "paso_origen_no_interesado",
    "motivo_no_califica", "quiere_contacto_ejecutivo", "intencion_regularizar",
    "renta_mensual", "numero_integrantes", "integrantes_rsh",
}


# ---------------------------------------------------------------------------
# Supabase REST
# ---------------------------------------------------------------------------

def _ingest_api_key() -> Optional[str]:
    return get_settings().ingest_api_key


# Acceso a Supabase: movido a app/clients/supabase.py (se importa arriba con los mismos nombres).


# ---------------------------------------------------------------------------
# Operaciones sobre prospectos
# ---------------------------------------------------------------------------

async def upsert_prospecto(
    *,
    telefono_e164: str,
    nombre: Optional[str] = None,
    rut: Optional[str] = None,
    rango_sueldo: Optional[str] = None,
    numero_integrantes: Optional[int] = None,
    proyecto_id: Optional[str] = None,
    estado: Optional[str] = None,
    paso: Optional[str] = None,
    ultimo_texto_entrante: Optional[str] = None,
    datos: Optional[Dict] = None,
    cliente_id: Optional[int] = None,
):
    row: Dict[str, Any] = {
        "telefono_e164": telefono_e164,
        "actualizado_en": _utc_now_iso(),
    }
    if nombre is not None:
        row["nombre"] = nombre
    if rut is not None:
        row["rut"] = rut
    if rango_sueldo is not None:
        row["rango_sueldo"] = rango_sueldo
    if numero_integrantes is not None:
        row["numero_integrantes"] = numero_integrantes
    if proyecto_id is not None:
        row["proyecto_id"] = proyecto_id
    if estado is not None:
        row["estado"] = estado
    if paso is not None:
        row["paso"] = paso
    if ultimo_texto_entrante is not None:
        row["ultimo_texto_entrante"] = ultimo_texto_entrante
        row["ultimo_entrante_en"]    = _utc_now_iso()
        row["pendiente_respuesta"]   = True   # el bot aún no ha respondido
    if datos is not None:
        row["datos"] = datos
    if cliente_id is not None:
        row["cliente_id"] = cliente_id

    data = await _supabase_request(
        "POST",
        "/Prospecto",
        params={"on_conflict": "telefono_e164"},
        json=row,
        extra_headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    )
    if not data:
        return None
    return data[0]


async def actualizar_datos_prospecto(
    prospecto_id: str,
    nuevos_datos: Dict,
    siguiente_paso: Optional[str] = None,
    paso_origen: Optional[str] = None,
):
    rows = await _supabase_request(
        "GET",
        "/Prospecto",
        params={"id": f"eq.{prospecto_id}", "select": "datos,paso,opt_out"},
    )
    if not rows:
        return

    campos_columna: Dict[str, Any] = {}
    campos_blob:    Dict[str, Any] = {}

    for k, v in nuevos_datos.items():
        if v is None:
            continue
        if k in _CAMPOS_COLUMNA_PROPIA:
            # opt_out: jamás se puede pasar de true a false automáticamente
            if k == "opt_out" and rows[0].get("opt_out") and not v:
                continue
            campos_columna[k] = v
        else:
            campos_blob[k] = v

    merged_blob = {**(rows[0].get("datos") or {}), **campos_blob}

    update: Dict[str, Any] = {
        "datos": merged_blob,
        "actualizado_en": _utc_now_iso(),
        **campos_columna,
    }
    if siguiente_paso:
        update["paso"]   = siguiente_paso
        update["estado"] = siguiente_paso
    if paso_origen:
        update["paso_origen_no_interesado"] = paso_origen

    await _supabase_request(
        "PATCH",
        "/Prospecto",
        params={"id": f"eq.{prospecto_id}"},
        json=update,
    )

    if siguiente_paso == "DOCUMENTACION":
        asyncio.create_task(_notificar_interesado(prospecto_id))


async def insertar_mensaje(
    *,
    prospecto_id: str,
    direccion: str,
    text: Optional[str],
    wa_message_id: Optional[str] = None,
    cliente_id: Optional[int] = None,
):
    row: Dict[str, Any] = {
        "prospecto_id": prospecto_id,
        "direccion":    direccion,
        "texto":        text,
        "wa_id_mensaje": _normalizar_wamid(wa_message_id),
    }
    if cliente_id is not None:
        row["cliente_id"] = cliente_id
    try:
        await _supabase_request("POST", "/Mensaje", json=row)
    except httpx.HTTPStatusError as e:
        # Con el índice único sobre Mensaje.wa_id_mensaje, un wamid repetido es un mensaje duplicado de Meta:
        # no es un error. Cualquier otro fallo (incluida una violación distinta, como una clave foránea) sigue lanzándose.
        if _es_violacion_unica(e):
            return False
        raise
    return True


async def insertar_documento(
    *,
    prospecto_id: str,
    tipo: str,
    nombre_archivo: str,
    url_storage: Optional[str] = None,
    wa_media_id: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> Dict:
    data = await _supabase_request(
        "POST",
        "/Documento",
        json={
            "prospecto_id": prospecto_id,
            "tipo":          tipo,
            "nombre_archivo": nombre_archivo,
            "url_storage":   url_storage,
            "wa_media_id":   wa_media_id,
            "mime_type":     mime_type,
            "verificado":    False,
        },
        extra_headers={"Prefer": "return=representation"},
    )
    return data[0] if data else {}


# Minutos hasta los que un archivo sin tipo ("otro") puede recibir tipo por lo que el cliente escriba después.
VENTANA_TIPO_POR_TEXTO_MIN = 15
# Los avisos de Meta pueden llegar en otro orden y su reloj no es el de este servidor: un texto recibido unos segundos
# "antes" de la hora de envío del archivo igual cuenta como escrito junto con él.
TOLERANCIA_RELOJ_S = 5


async def _tipo_segun_textos(prospecto_id: str, operador: str, instante: str) -> Optional[str]:
    """Tipo de documento que nombra el primer mensaje del cliente recibido desde ``instante`` (operador "gt" o "gte")."""
    textos = await _supabase_request("GET", "/Mensaje", params={
        "prospecto_id": f"eq.{prospecto_id}", "direccion": "eq.entrante", "creado_en": f"{operador}.{instante}",
        "select": "texto", "order": "creado_en.asc", "limit": "20",
    }) or []
    for fila in textos:
        texto = (fila.get("texto") or "").strip()
        tipo = None if texto.startswith("[") else clasificar_texto_documento(texto)   # "[IMAGE] …" es el registro de un archivo
        if tipo:
            return tipo
    return None


async def etiquetar_documentos_sin_tipo(prospecto_id: str) -> int:
    """Una foto de WhatsApp casi nunca trae nombre, así que llega como tipo "otro". Si después el cliente escribe qué
    era ("mi libreta de ahorro"), ese tipo se le pone al archivo. Devuelve cuántos archivos se etiquetaron."""
    desde = (datetime.now(timezone.utc) - timedelta(minutes=VENTANA_TIPO_POR_TEXTO_MIN)).isoformat()
    sin_tipo = await _supabase_request("GET", "/Documento", params={
        "prospecto_id": f"eq.{prospecto_id}", "tipo": "eq.otro", "wa_media_id": "not.is.null",
        "creado_en": f"gte.{desde}", "select": "id,creado_en", "order": "creado_en.asc", "limit": "10",
    }) or []
    etiquetados = 0
    for doc in sin_tipo:
        tipo = await _tipo_segun_textos(prospecto_id, "gt", doc["creado_en"])
        if tipo:
            await _supabase_request("PATCH", "/Documento", params={"id": f"eq.{doc['id']}"}, json={"tipo": tipo})
            etiquetados += 1
    return etiquetados


async def tipo_por_texto_desde_envio(prospecto_id: str, timestamp_envio: Any) -> Optional[str]:
    """Al registrar un archivo: lo que el cliente escribió desde que lo ENVIÓ (la hora de WhatsApp), aunque el archivo
    haya tardado en procesarse y el texto ya estuviera guardado."""
    try:
        envio = datetime.fromtimestamp(int(timestamp_envio), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    desde = max(envio - timedelta(seconds=TOLERANCIA_RELOJ_S),
                datetime.now(timezone.utc) - timedelta(minutes=VENTANA_TIPO_POR_TEXTO_MIN))
    return await _tipo_segun_textos(prospecto_id, "gte", desde.isoformat())


async def obtener_prospecto_por_id(prospecto_id: str) -> Optional[Dict]:
    filas = await _supabase_request("GET", "/Prospecto", params={"id": f"eq.{prospecto_id}", "select": "*", "limit": "1"})
    return filas[0] if filas else None


async def obtener_documentos_prospecto(prospecto_id: str) -> List[Dict]:
    rows = await _supabase_request(
        "GET",
        "/Documento",
        params={
            "prospecto_id": f"eq.{prospecto_id}",
            "select": "tipo,nombre_archivo,verificado,creado_en",
            "order": "creado_en.asc",
        },
    )
    return rows or []


async def obtener_historial_mensajes(prospecto_id: str, limite: int = 12) -> List[Dict]:
    rows = await _supabase_request(
        "GET",
        "/Mensaje",
        params={
            "prospecto_id": f"eq.{prospecto_id}",
            "order": "creado_en.desc",
            "limit": str(limite),
            "select": "direccion,texto",
        },
    )
    return list(reversed(rows or []))


# ---------------------------------------------------------------------------
# WhatsApp Media — descarga y sube a Supabase Storage
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# IA — construcción de contexto adaptativo por cadena jerárquica
# ---------------------------------------------------------------------------

def _construir_mind(proyecto: Optional[Dict]) -> Dict[str, str]:
    """
    Extrae la cadena contextual completa desde el proyecto hacia arriba:
    Proyecto → Inmobiliaria → Empresa → Industria.
    Retorna un dict con los nombres de cada nivel (vacío si no existe).
    """
    p    = proyecto or {}
    inm  = p.get("Inmobiliaria") or {}
    emp  = inm.get("Empresa") or {}
    ind  = emp.get("Industria") or {}
    return {
        "industria":    ind.get("nombre") or "Inmobiliaria",
        "empresa":      emp.get("nombre") or "",
        "inmobiliaria": inm.get("nombre") or "",
        "proyecto":     p.get("nombre")   or "",
    }


async def _obtener_otros_proyectos(empresa_id: int, proyecto_id_actual: Optional[str]) -> List[Dict]:
    """Devuelve todos los proyectos de la empresa excepto el actual."""
    inms = await _supabase_request("GET", "/Inmobiliaria",
        params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
    if not inms:
        return []
    inm_ids = ",".join(str(i["id"]) for i in inms)
    rows = await _supabase_request("GET", "/Proyecto",
        params={"inmobiliaria_id": f"in.({inm_ids})", "activo": "eq.true", "select": _PROYECTO_SELECT_LIGHT}) or []
    return [p for p in rows if str(p.get("id")) != str(proyecto_id_actual)]


def _resumir_proyecto(p: Dict) -> str:
    """Genera una línea de resumen de un proyecto para el contexto del bot (otros proyectos disponibles)."""
    subsidios = []
    if p.get("acepta_ds19"):
        subsidios.append("DS19")
    if p.get("acepta_ds1_t23"):
        subsidios.append(f"DS1T23 {p.get('subsidio_ds1_t23_uf','')}UF".strip())
    sub_str = " | ".join(subsidios) or "sin subsidio"
    return f"• [{p['id']}] {p.get('nombre','?')} — {p.get('ubicacion','?')} | {sub_str}"


async def _cambiar_proyecto_prospecto(
    prospecto_id: str, nuevo_proyecto_id: str, cliente_id: Optional[int]
) -> None:
    """Actualiza proyecto_id en Prospecto y en Cliente cuando el cliente confirma cambio."""
    await _supabase_request("PATCH", "/Prospecto",
        params={"id": f"eq.{prospecto_id}"},
        json={"proyecto_id": nuevo_proyecto_id})
    if cliente_id:
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json={"proyecto_id": nuevo_proyecto_id})


# ---------------------------------------------------------------------------
# IA — respuesta con contexto de paso
# ---------------------------------------------------------------------------

async def generar_respuesta_ia(
    *,
    prospecto: Dict,
    proyecto: Optional[Dict],
    historial: List[Dict],
    mensaje_actual: str,
    docs_recibidos: Optional[List[Dict]] = None,
    otros_proyectos: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    if not ANTHROPIC_API_KEY or anthropic is None:
        logger.warning("ANTHROPIC_API_KEY no configurada — usando eco")
        return {"respuesta": f"Hola 👋 Recibí: {mensaje_actual}", "siguiente_paso": None, "datos_extraidos": {}}

    nombre              = (prospecto.get("nombre") or "").strip() or "amigo/a"
    telefono            = prospecto.get("telefono_e164") or ""
    rut                 = prospecto.get("rut") or "no registrado"
    rango_sueldo        = prospecto.get("rango_sueldo") or "no registrado"
    numero_integrantes  = prospecto.get("numero_integrantes") or "no registrado"
    paso_actual         = prospecto.get("paso") or "INICIO"
    # Construir datos de calificación desde columnas boolean dedicadas
    datos = {campo: prospecto.get(campo) for campo in _CAMPOS_CALIFICACION}

    mind = _construir_mind(proyecto)

    p                  = proyecto or {}
    proyecto_nombre    = p.get("nombre") or "nuestro proyecto"
    proyecto_ubicacion = p.get("ubicacion") or ""
    proyecto_inmobiliaria      = mind["inmobiliaria"]

    # Datos de Etapa y Tipologia (ya no viven en Proyecto)
    _etapa_bot = None
    _tip_monto_bot = None
    if p.get("id"):
        _etapas_bot = await _supabase_request("GET", "/Etapa",
            params={"proyecto_id": f"eq.{p['id']}", "select": "fecha_entrega,estado,num_cuotas", "limit": "1", "order": "id.asc"}) or []
        _etapa_bot = _etapas_bot[0] if _etapas_bot else None
        _tips_bot = await _supabase_request("GET", "/Tipologia",
            params={"proyecto_id": f"eq.{p['id']}", "select": "id,nombre,valor_uf,dormitorios,banos,superficie_util_m2,tipo_subsidio,monto_subsidio"}) or []
        _raw_monto = _tips_bot[0].get("monto_subsidio") if _tips_bot else None
        try:
            _tip_monto_bot = float(_raw_monto) if _raw_monto is not None else None
        except (TypeError, ValueError):
            _tip_monto_bot = None

    proyecto_fecha_entrega     = (_etapa_bot.get("fecha_entrega") if _etapa_bot else None) or "por confirmar"
    proyecto_ahorro_minimo     = p.get("ahorro_minimo_uf") or 50
    proyecto_reserva_clp       = p.get("valor_reserva_clp") or ""
    proyecto_reserva_uf        = p.get("valor_reserva_uf") or ""
    proyecto_tiene_piloto      = p.get("tiene_piloto")
    proyecto_estac_uf          = p.get("valor_estacionamiento_uf") or ""
    proyecto_estac_obligatorio = p.get("estacionamiento_obligatorio")
    proyecto_notas             = p.get("notas") or ""
    proyecto_acepta_ds19       = p.get("acepta_ds19") or False
    proyecto_monto_subsidio    = _tip_monto_bot or 700
    proyecto_acepta_ds1t23     = p.get("acepta_ds1_t23") or False
    proyecto_subsidio_ds1t23   = p.get("subsidio_ds1_t23_uf") or ""
    proyecto_tipologias        = _tips_bot if p.get("id") else []

    # Construir bloque de subsidio según lo que acepta el proyecto
    subsidios_lineas = []
    if proyecto_acepta_ds19:
        subsidios_lineas.append(f"DS19: {proyecto_monto_subsidio} UF")
    if proyecto_acepta_ds1t23:
        monto_ds1t23 = proyecto_subsidio_ds1t23 or _tip_monto_bot or ""
        subsidios_lineas.append(f"DS1 T2/T3 (adjudicado){f': {monto_ds1t23} UF' if monto_ds1t23 else ''}")
    subsidios_texto = " | ".join(subsidios_lineas) if subsidios_lineas else "Este proyecto NO acepta subsidios habitacionales"

    # Estacionamiento
    if proyecto_estac_uf:
        estac_texto = f"{proyecto_estac_uf} UF {'(obligatorio)' if proyecto_estac_obligatorio else '(opcional)'}"
    else:
        estac_texto = "no disponible"

    # Lista estática de documentos requeridos (mismos nombres/cantidades que valida el backend)
    lista_documentos_requeridos = _lista_documentos_requeridos_texto(datos)

    # Estado de documentos para el paso ESPERA_DOCS (condicional según calificación)
    estado_documentos = resumen_documentos(docs_recibidos or [], datos)
    _estado_lineas = estado_documentos.split("\n")
    docs_recibidos_txt = _estado_lineas[0].replace("Recibidos: ", "") if _estado_lineas else "(ninguno)"
    docs_pendientes_txt = _estado_lineas[1].replace("Pendientes: ", "") if len(_estado_lineas) > 1 else "(ninguno)"

    # Variables adicionales para nuevo PASOS_CONFIG
    tipo_subsidio_datos   = datos.get("tipo_subsidio") or "no_determinado"
    tipo_entrega_proyecto = (_etapa_bot.get("estado") if _etapa_bot else None) or "entrega_futura"
    grupo_ds19_proyecto   = p.get("grupo_ds19") or "A"

    # Condición de pago del ahorro: calculada en código a partir de Etapa.estado/num_cuotas
    # (única fuente de verdad) — se inyecta como frase fija en DATOS DEL PROYECTO para que el
    # bot no tenga que inferir/inventar si es "al contado" o "en cuotas durante la construcción".
    if tipo_entrega_proyecto == "entrega_inmediata":
        condicion_pago_ahorro = (
            f"El ahorro de {proyecto_ahorro_minimo} UF se paga DE UNA SOLA VEZ "
            f"(proyecto de entrega inmediata — NO se paga en cuotas ni durante construcción)."
        )
    else:
        _num_cuotas_bot = _etapa_bot.get("num_cuotas") if _etapa_bot else None
        _cuotas_txt = f"en {_num_cuotas_bot} cuotas mensuales" if _num_cuotas_bot else "en cuotas mensuales"
        condicion_pago_ahorro = (
            f"El ahorro de {proyecto_ahorro_minimo} UF se paga {_cuotas_txt} SIN INTERÉS "
            f"durante la construcción, hasta la fecha de entrega estimada "
            f"({(_etapa_bot.get('fecha_entrega') if _etapa_bot else None) or 'por confirmar'})."
        )
    _credito_uf_est: Any = "consultar"
    if proyecto_tipologias:
        _precios = [t.get("valor_uf") for t in proyecto_tipologias if t.get("valor_uf")]
        if _precios:
            _precio_min = min(_precios)
            _credito_calc = round(_precio_min - proyecto_monto_subsidio - proyecto_ahorro_minimo)
            _credito_uf_est = max(_credito_calc, 0)
    _dividendo_est: Any = round(_credito_uf_est * 213) if isinstance(_credito_uf_est, (int, float)) else "consultar"
    _renta_min_est: Any = round(_dividendo_est * 4) if isinstance(_dividendo_est, (int, float)) else "consultar"

    _reserva_str = (f"{proyecto_reserva_uf} UF / ${proyecto_reserva_clp:,.0f}"
                    if proyecto_reserva_clp else proyecto_reserva_uf or "consultar")
    datos_proyecto_texto = (
        f"Nombre: {proyecto_nombre}\n"
        f"Ubicación: {proyecto_ubicacion}\n"
        f"Fecha entrega: {proyecto_fecha_entrega}\n"
        f"Tipo de entrega: {tipo_entrega_proyecto}\n"
        f"Monto subsidio: {proyecto_monto_subsidio} UF\n"
        f"Ahorro mínimo: {proyecto_ahorro_minimo} UF\n"
        f"Condición de pago del ahorro: {condicion_pago_ahorro}\n"
        f"Crédito estimado: {_credito_uf_est} UF\n"
        f"Sala piloto: {'Sí' if proyecto_tiene_piloto else 'No disponible'}\n"
        f"Estacionamiento: {estac_texto}\n"
        f"Valor reserva: {_reserva_str}\n"
        f"Tipologías: {json.dumps(proyecto_tipologias, ensure_ascii=False)}\n"
        f"Notas: {proyecto_notas}"
    )

    instrucciones = PASOS_CONFIG.get(paso_actual, PASOS_CONFIG["BIENVENIDA"]).format(
        nombre=nombre,
        rango_sueldo=rango_sueldo,
        datos=json.dumps(datos, ensure_ascii=False, indent=2),
        estado_documentos=estado_documentos,
        ahorro_minimo=proyecto_ahorro_minimo,
        monto_subsidio=proyecto_monto_subsidio,
        monto_subsidio_ds1t23=proyecto_subsidio_ds1t23 or "consultar",
        proyecto=proyecto_nombre,
        datos_proyecto=datos_proyecto_texto,
        tipo_subsidio=tipo_subsidio_datos,
        tipo_entrega=tipo_entrega_proyecto,
        credito_uf=_credito_uf_est,
        dividendo_estimado=f"{_dividendo_est:,}" if isinstance(_dividendo_est, (int, float)) else _dividendo_est,
        renta_minima_estimada=f"{_renta_min_est:,}" if isinstance(_renta_min_est, (int, float)) else _renta_min_est,
        docs_recibidos=docs_recibidos_txt,
        docs_pendientes=docs_pendientes_txt,
        grupo_ds19=grupo_ds19_proyecto,
        numero_integrantes=numero_integrantes,
        lista_documentos_requeridos=lista_documentos_requeridos,
        condicion_pago_ahorro=condicion_pago_ahorro,
    )

    sistema_identidad = f"Eres un asistente de ventas de la industria {mind['industria']}"
    if mind["empresa"]:
        sistema_identidad += f", trabajas para la empresa {mind['empresa']}"
    if mind["inmobiliaria"]:
        sistema_identidad += f", representando a la inmobiliaria {mind['inmobiliaria']}"
    if mind["proyecto"]:
        sistema_identidad += f", y tu foco actual es el proyecto {mind['proyecto']}"
    sistema_identidad += ". Eres profesional, empático y experto en el área."

    if otros_proyectos:
        _bloque_otros_proyectos = "\n".join(_resumir_proyecto(p) for p in otros_proyectos)
    else:
        _bloque_otros_proyectos = "No hay otros proyectos disponibles en este momento."

    system_prompt = f"""{sistema_identidad}

═══ DATOS DEL CLIENTE ═══
Nombre:              {nombre}
Teléfono:            {telefono}
RUT:                 {rut}
Rango sueldo:        {rango_sueldo}
Integrantes hogar:   {numero_integrantes}
Paso actual:         {paso_actual}

═══ DATOS DEL PROYECTO ═══
Nombre:              {proyecto_nombre}
Inmobiliaria:        {proyecto_inmobiliaria}
Ubicación:           {proyecto_ubicacion}
Fecha entrega:       {proyecto_fecha_entrega}
Subsidios:           {subsidios_texto}
Ahorro mínimo:       {proyecto_ahorro_minimo} UF
Cómo se paga el ahorro: {condicion_pago_ahorro}
Estacionamiento:     {estac_texto}
Sala piloto:         {'Sí' if proyecto_tiene_piloto else 'No disponible'}
Valor reserva:       {f'{proyecto_reserva_uf} UF / ${proyecto_reserva_clp:,.0f}' if proyecto_reserva_clp else proyecto_reserva_uf or 'consultar'}
Tipologías:          {json.dumps(proyecto_tipologias, ensure_ascii=False)}
Notas del proyecto:  {proyecto_notas}

═══ INSTRUCCIONES DE ESTE PASO ═══
{instrucciones}

═══ OTROS PROYECTOS DISPONIBLES ═══
{_bloque_otros_proyectos}

═══ REGLAS GENERALES ═══
- Responde en español, de forma cálida y profesional.
- Mensajes cortos (máximo 3-4 párrafos). NUNCA más de 1 pregunta a la vez.
- Usa emojis con moderación.
- Usa los datos del proyecto para responder preguntas específicas del cliente
  (precio, fecha, estacionamiento, etc.) sin inventar información.
- Sobre cómo se paga el ahorro (al contado vs en cuotas durante construcción):
  usa EXCLUSIVAMENTE lo que dice "Cómo se paga el ahorro" arriba. NUNCA asumas
  que es "en cuotas durante la construcción" por defecto — varios proyectos son
  de entrega inmediata y el ahorro se paga de una sola vez. No contradigas ese dato.
- NUNCA menciones nombres de empresas, constructoras o inmobiliarias que no estén
  explícitamente en los datos del sistema. Si no tienes la información, di que la
  coordinarás con el equipo a cargo, sin inventar nombres.
- Si el proyecto NO acepta subsidios, NO hables de subsidios ni consultes si el
  cliente tiene uno. Ignora completamente ese tema.
- Si el cliente pregunta algo fuera del tema del proyecto o subsidio,
  redirígelo amablemente sin ser brusco, recordándole en qué punto del proceso está.
- Si el cliente pide ver otros proyectos o mostrar alternativas, preséntale
  los proyectos de la sección "OTROS PROYECTOS DISPONIBLES" de forma breve.
- Cuando el cliente confirme explícitamente su interés en un proyecto distinto
  al actual, incluye "nuevo_proyecto_id" en datos_extraidos con el id exacto
  del proyecto confirmado. La calificación y documentos ya recopilados se
  conservan; NO reinicies el flujo salvo que el nuevo proyecto exija subsidio
  distinto y el cliente no haya calificado aún.

RESPONDE ÚNICAMENTE con JSON válido (sin markdown, sin texto extra):
{{
  "respuesta": "texto para enviar por WhatsApp",
  "siguiente_paso": null,
  "datos_extraidos": {{}}
}}
Valores válidos de siguiente_paso: null | "BIENVENIDA" | "INICIO" | "DOCUMENTACION" | "ESPERA_DOCS" | "DOCS_RECIBIDOS" | "NO_INTERESADO" | "NO_CALIFICA"
En datos_extraidos puedes incluir además: "nuevo_proyecto_id": "<uuid>" cuando el cliente confirme cambio de proyecto.
"""

    messages: List[Dict[str, str]] = []
    for msg in historial:
        texto = (msg.get("texto") or "").strip()
        if not texto:
            continue
        role = "user" if msg["direccion"] == "entrante" else "assistant"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += f"\n{texto}"
        else:
            messages.append({"role": role, "content": texto})
    messages.append({"role": "user", "content": mensaje_actual})
    # Prefill: fuerza a Claude a comenzar directamente con JSON
    messages.append({"role": "assistant", "content": "{"})

    client = cliente_anthropic(ANTHROPIC_API_KEY)
    # El presupuesto total incluye los reintentos del SDK y cualquier Retry-After (ver app/core/llm.py)
    _modelo = "claude-haiku-4-5-20251001"
    _t0 = time.perf_counter()
    try:
        response = await asyncio.wait_for(client.messages.create(
            model=_modelo,
            max_tokens=700,
            system=system_prompt,
            messages=messages,
        ), timeout=PRESUPUESTO_TOTAL_S)
    except (asyncio.TimeoutError, anthropic.APITimeoutError) as _e:
        # request_id lo añade el formateador JSON (contextvar de la petición de origen)
        logger.warning(
            "Tiempo agotado llamando al modelo (presupuesto %.0f s, reintentos incluidos): "
            "el mensaje queda pendiente para _recovery_loop",
            PRESUPUESTO_TOTAL_S,
            extra={"modelo": _modelo, "motivo": type(_e).__name__,
                   "duration_ms": round((time.perf_counter() - _t0) * 1000, 1)},
        )
        raise
    logger.info("Respuesta del modelo", extra={"modelo": _modelo,
                                               "duration_ms": round((time.perf_counter() - _t0) * 1000, 1)})

    raw = "{" + response.content[0].text.strip()
    # Limpiar si viene con markdown igual
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        result = json.loads(raw)
        respuesta_final = str(result.get("respuesta") or raw)
        # Reemplazo estático: garantiza que la lista de documentos que llega al
        # cliente sea exactamente la que valida el backend, sin que el modelo
        # pueda parafrasear nombres, cambiar cantidades o inventar documentos.
        if "<<LISTA_DOCUMENTOS>>" in respuesta_final:
            respuesta_final = respuesta_final.replace("<<LISTA_DOCUMENTOS>>", lista_documentos_requeridos)
        return {
            "respuesta":       respuesta_final,
            "siguiente_paso":  result.get("siguiente_paso") or None,
            "datos_extraidos": result.get("datos_extraidos") or {},
        }
    except Exception:
        logger.warning("Claude no devolvió JSON válido, usando texto crudo")
        if "<<LISTA_DOCUMENTOS>>" in raw:
            raw = raw.replace("<<LISTA_DOCUMENTOS>>", lista_documentos_requeridos)
        return {"respuesta": raw, "siguiente_paso": None, "datos_extraidos": {}}


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


# Segundos de espera antes de que el bot responda (para sensación más humana)
DELAY_RESPUESTA_SEGUNDOS = get_settings().bot_reply_delay


async def _procesar_webhook(msg: Dict, es_reintento: bool = False, responder: bool = True):
    """Procesa un mensaje de WhatsApp en background (después de devolver 200 a Meta).

    es_reintento=True cuando lo invoca _recovery_loop: el mensaje entrante ya
    quedó guardado en el primer intento, así que NO debe volver a insertarse
    (evita duplicar el mensaje del cliente en el historial en cada reintento)."""
    try:
        from_number = _normalize_phone(msg["from"])
        msg_type    = msg.get("type", "text")

        texto_preview = ""
        if msg_type == "text":
            texto_preview = (msg.get("text", {}).get("body") or "")[:80]
        elif msg_type in ("image", "audio", "video", "document"):
            texto_preview = f"[{msg_type}]"
        logger.info("📨 Mensaje entrante | %s | tipo: %s | %s", from_number, msg_type, texto_preview)

        # ── Filtrar números de ejecutivos CRM ────────────────────────────
        _usr_crm = await _supabase_request("GET", "/Usuario",
            params={"celular": f"eq.{from_number}", "select": "id", "limit": "1"}) or []
        if _usr_crm:
            await send_whatsapp_message(
                to=from_number,
                text="Este número es exclusivo para notificaciones del CRM QueSubsidio. Gestiona tus clientes desde la plataforma en línea.",
            )
            return

        # ── Obtener o crear prospecto de inmediato ─────────────────────────
        prospecto = None
        proyecto  = None

        if _supabase_url() and _supabase_service_role_key():
            prospecto = await upsert_prospecto(
                telefono_e164=from_number,
                estado="RESPONDIO",
            )
            if prospecto and prospecto.get("proyecto_id"):
                proyecto = await obtener_proyecto_por_id(prospecto["proyecto_id"])

        prospecto_id = (prospecto or {}).get("id")
        cliente_id_prospecto: Optional[int] = (prospecto or {}).get("cliente_id")

        # ── Manejo de DOCUMENTOS (imagen o archivo) ───────────────────────
        if msg_type in ("image", "document"):
            # En su propia tarea y protegida: si el mismo número escribe otro mensaje mientras se descarga, el debounce
            # cancela esta tarea y la foto/documento se perdería a medias. Así la descarga termina igual.
            await asyncio.shield(_lanzar(_procesar_media(msg, msg_type, from_number, prospecto, prospecto_id)))
            return

        # ── Manejo de TEXTO ───────────────────────────────────────────────
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            return

        # Guardar mensaje entrante ANTES del debounce (queda en historial aunque llegue otro)
        # — se omite en reintentos del recovery loop: el mensaje ya se guardó la primera vez.
        if prospecto_id and not es_reintento:
            nuevo = await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="entrante",
                text=text,
                wa_message_id=msg.get("id"),
                cliente_id=cliente_id_prospecto,
            )
            if not nuevo:
                # Duplicado detectado por la base (índice único): sobrevive a reinicios y a varias réplicas.
                logger.info("Mensaje duplicado ignorado (ya estaba guardado): %s", msg.get("id"),
                            extra={"wamid": msg.get("id")})
                return
            await upsert_prospecto(
                telefono_e164=from_number,
                ultimo_texto_entrante=text,
            )

        # Mensajes anteriores de un lote del mismo número: solo se guardan; responde el último del lote.
        if not responder:
            return

        # Fuera del horario del bot (HORARIO_BOT_INICIO/FIN; por defecto 24/7, así que normalmente no frena):
        # no responder ahora. El mensaje ya quedó guardado con pendiente_respuesta=true;
        # _recovery_loop lo retoma solo cuando se abra el horario.
        if not _dentro_horario_bot():
            logger.info("Fuera de horario hábil, respuesta pospuesta para %s", from_number)
            return

        # Debounce: esperar a que el cliente termine de escribir
        await asyncio.sleep(DELAY_RESPUESTA_SEGUNDOS)

        historial = []
        docs_recibidos = []

        if prospecto_id:
            # Durante la espera pudo cambiar el estado (p. ej. se registró un archivo y el paso avanzó): se responde con
            # el estado de ahora, no con el que se leyó al llegar el mensaje.
            try:
                prospecto = await obtener_prospecto_por_id(prospecto_id) or prospecto
            except Exception as e_fresco:
                registrar_error(e_fresco, "_procesar_webhook: releer el prospecto después de la espera",
                                mensaje="No se pudo releer el prospecto; el bot responde con el estado de cuando llegó el mensaje",
                                nivel=logging.WARNING)
            try:
                await etiquetar_documentos_sin_tipo(prospecto_id)
            except Exception as e_etiq:
                registrar_error(e_etiq, "_procesar_webhook: tipo de archivos según lo que escribe el cliente",
                                mensaje="No se pudo asignar tipo a los archivos sin clasificar; el bot sigue sin ese dato",
                                nivel=logging.WARNING)
            try:
                historial = await obtener_historial_mensajes(prospecto_id)
                docs_recibidos = await obtener_documentos_prospecto(prospecto_id)
            except Exception as e_ctx:
                registrar_error(e_ctx, "_procesar_webhook: historial y documentos",
                                mensaje="No se pudo cargar el historial/documentos; el bot responde sin ese contexto",
                                nivel=logging.WARNING)

        # Cargar otros proyectos de la misma empresa para contexto del bot
        otros_proyectos: List[Dict] = []
        if proyecto:
            empresa_id_ctx = ((proyecto.get("Inmobiliaria") or {}).get("empresa_id"))
            if empresa_id_ctx:
                try:
                    otros_proyectos = await _obtener_otros_proyectos(empresa_id_ctx, proyecto.get("id"))
                except Exception as e_ctx:
                    registrar_error(e_ctx, "_procesar_webhook: otros proyectos de la empresa",
                                    mensaje="No se pudo cargar otros proyectos; el bot responde sin ese contexto",
                                    nivel=logging.WARNING)

        resultado = await generar_respuesta_ia(
            prospecto=prospecto or {},
            proyecto=proyecto,
            historial=historial,
            mensaje_actual=text,
            docs_recibidos=docs_recibidos,
            otros_proyectos=otros_proyectos,
        )

        reply_text      = (resultado["respuesta"] or "").strip()
        siguiente_paso  = resultado["siguiente_paso"]
        datos_extraidos = resultado["datos_extraidos"]

        if not reply_text:
            logger.warning("reply_text vacío para %s, omitiendo envío", from_number)
            return

        await send_whatsapp_message(to=from_number, text=reply_text)

        # Cambio de proyecto confirmado por el cliente
        nuevo_proyecto_id = datos_extraidos.pop("nuevo_proyecto_id", None)
        if nuevo_proyecto_id and prospecto_id:
            try:
                await _cambiar_proyecto_prospecto(prospecto_id, nuevo_proyecto_id, cliente_id_prospecto)
                logger.info("Proyecto actualizado → %s para prospecto %s", nuevo_proyecto_id, prospecto_id)
            except Exception:
                logger.exception("Error al cambiar proyecto del prospecto %s", prospecto_id)

        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="saliente",
                text=reply_text,
                cliente_id=cliente_id_prospecto,
            )
            # Bot respondió exitosamente → limpiar flag
            await _supabase_request("PATCH", "/Prospecto",
                params={"id": f"eq.{prospecto_id}"},
                json={"pendiente_respuesta": False})
            if datos_extraidos or siguiente_paso:
                paso_actual_prospecto = (prospecto or {}).get("paso") or "BIENVENIDA"

                # FASE 5.1 — guardar el paso de origen cuando el cliente se marca NO_INTERESADO
                paso_origen = paso_actual_prospecto if siguiente_paso == "NO_INTERESADO" else None

                # FASE 5.2 — calcular motivo_no_califica cuando transiciona a NO_CALIFICA
                if siguiente_paso == "NO_CALIFICA":
                    tiene_prop = datos_extraidos.get("tiene_propiedad") or (prospecto or {}).get("tiene_propiedad")
                    sub_prev   = datos_extraidos.get("subsidio_previo")  or (prospecto or {}).get("subsidio_previo")
                    if tiene_prop and sub_prev:
                        datos_extraidos["motivo_no_califica"] = "ambos"
                    elif tiene_prop:
                        datos_extraidos["motivo_no_califica"] = "tiene_propiedad"
                    elif sub_prev:
                        datos_extraidos["motivo_no_califica"] = "subsidio_previo"

                await actualizar_datos_prospecto(
                    prospecto_id,
                    datos_extraidos,
                    siguiente_paso,
                    paso_origen=paso_origen,
                )

                # Notificar ejecutivo si el cliente pide contacto humano
                if datos_extraidos.get("quiere_contacto_ejecutivo") and cliente_id_prospecto:
                    nombre_p = (prospecto or {}).get("nombre") or from_number
                    asyncio.create_task(_notificar_ejecutivo_wa(
                        cliente_id=cliente_id_prospecto,
                        nombre_cliente=nombre_p,
                        accion="necesita_ayuda",
                    ))

    except Exception as e:
        logger.exception("Error procesando webhook: %s", _safe_httpx_error(e))


_ORDEN_ESTADO_PLANTILLA = {"enviado": 1, "entregado": 2, "leido": 3, "fallido": 0}
_META_ESTADO_MAP        = {"sent": "enviado", "delivered": "entregado", "read": "leido", "failed": "fallido"}


async def _procesar_status(status: Dict) -> None:
    wamid       = status.get("id")
    meta_estado = status.get("status")
    timestamp   = status.get("timestamp", "")
    errors      = status.get("errors") or []

    if not wamid or not meta_estado:
        return

    # Log siempre el status que llega desde Meta
    if errors:
        err_code = errors[0].get("code") if errors else ""
        err_msg  = errors[0].get("message") if errors else ""
        logger.warning("WA status FAILED wamid=%s code=%s msg=%s", wamid, err_code, err_msg)
    else:
        logger.info("WA status recibido: %s | wamid=%s | ts=%s", meta_estado, wamid, timestamp)

    estado_crm = _META_ESTADO_MAP.get(meta_estado)
    if not estado_crm:
        logger.warning("WA status desconocido '%s' ignorado wamid=%s", meta_estado, wamid)
        return

    rows = await _supabase_request("GET", "/Cliente",
        params={"wamid_plantilla": f"eq.{wamid}", "select": "id,estado_plantilla", "limit": "1"}) or []
    if not rows:
        # Solo la plantilla manual del CRM guarda su wamid; los demás envíos (respuestas del bot, avisos) no tienen
        # cliente asociado y es lo normal. Se avisa como advertencia únicamente si el envío falló.
        logger.log(logging.WARNING if estado_crm == "fallido" else logging.INFO,
                   "WA status '%s' sin cliente para wamid=%s", meta_estado, wamid)
        return
    c = rows[0]
    actual = c.get("estado_plantilla") or ""
    # "fallido" siempre se aplica (ignora el orden); el resto solo avanza
    debe_actualizar = (
        estado_crm == "fallido"
        or _ORDEN_ESTADO_PLANTILLA.get(estado_crm, 0) > _ORDEN_ESTADO_PLANTILLA.get(actual, 0)
    )
    if debe_actualizar:
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{c['id']}"},
            json={"estado_plantilla": estado_crm},
            extra_headers={"Prefer": "return=minimal"})
        logger.info("estado_plantilla %s → %s (cliente=%s wamid=%s)", actual or "—", estado_crm, c["id"], wamid)

    if estado_crm == "fallido":
        await _supabase_request("POST", "/LogWhatsapp", json={
            "cliente_id":    c["id"],
            "wamid":         wamid,
            "error_code":    str(errors[0].get("code")) if errors else None,
            "error_message": errors[0].get("message") if errors else None,
        })


# Referencias a las tareas en segundo plano: sin ellas el recolector de basura podría descartarlas a mitad de camino.
_tareas_fondo: set = set()


def _lanzar(coro) -> "asyncio.Task":
    tarea = asyncio.create_task(coro)
    _tareas_fondo.add(tarea)
    tarea.add_done_callback(_tareas_fondo.discard)
    return tarea


def _es_mensaje_nuevo(msg: Any) -> bool:
    """Deduplicación rápida en memoria (últimos 500 wamids). La definitiva es el índice único de la base."""
    if not isinstance(msg, dict):
        return False
    wamid = msg.get("id", "")
    if wamid and wamid in _wamids_procesados:
        logger.info("Webhook duplicado ignorado: %s", wamid)
        return False
    if wamid:
        _wamids_procesados.add(wamid)
        _wamids_orden.append(wamid)
        if len(_wamids_orden) > _WAMID_MAX:
            old = _wamids_orden.pop(0)
            _wamids_procesados.discard(old)
    return True


async def _procesar_grupo(mensajes: List[Dict]) -> None:
    """Varios mensajes del mismo número en un mismo lote: se guardan todos en orden y responde el último."""
    for msg in mensajes[:-1]:
        await _procesar_webhook(msg, responder=False)
    await _procesar_webhook(mensajes[-1])


def _registrar_firma_webhook(firma: str) -> bool:
    """Registra el resultado de la firma y dice si hay que rechazar la petición (solo con ENFORCE y secreto)."""
    rechazar = debe_rechazar(firma, get_settings().wa_signature_enforce)
    if firma == "valida":
        logger.info("Webhook: firma válida", extra={"firma_webhook": firma})
    elif firma != "sin_secreto":
        logger.warning("Webhook: firma %s%s", firma, " → se rechaza" if rechazar else " (solo se registra)",
                       extra={"firma_webhook": firma, "se_rechaza": rechazar})
    return rechazar


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


async def _procesar_media(
    msg: Dict,
    msg_type: str,
    from_number: str,
    prospecto: Optional[Dict],
    prospecto_id: Optional[str],
):
    """Descarga el media de WhatsApp, lo clasifica y lo sube a Supabase Storage."""
    try:
        media_info = msg.get(msg_type, {})
        media_id   = media_info.get("id", "")
        filename   = media_info.get("filename") or media_info.get("caption") or ""

        if not media_id:
            logger.warning("Mensaje de media sin media_id")
            return

        # Duplicado durable: si ya hay un documento con ese media_id de WhatsApp, este envío ya se procesó.
        if prospecto_id:
            ya = await _supabase_request("GET", "/Documento",
                params={"wa_media_id": f"eq.{media_id}", "select": "id", "limit": "1"}) or []
            if ya:
                logger.info("Archivo duplicado ignorado (media_id ya procesado): %s", media_id,
                            extra={"wa_media_id": media_id})
                return

        # Descargar de WhatsApp
        file_bytes, mime_type, nombre_generado = await descargar_media_whatsapp(media_id)
        nombre_archivo = filename or nombre_generado

        # Clasificar tipo de documento
        tipo = clasificar_documento(nombre_archivo, mime_type)
        tipo_por_nombre = tipo
        if tipo == "otro" and prospecto_id:
            try:
                tipo = await tipo_por_texto_desde_envio(prospecto_id, msg.get("timestamp")) or tipo
            except Exception as e_tipo:
                registrar_error(e_tipo, "_procesar_media: tipo según lo que el cliente escribió antes de que llegara el archivo",
                                mensaje="No se pudo leer lo que escribió el cliente; el archivo queda sin clasificar",
                                nivel=logging.WARNING)
        logger.info("Archivo recibido | tipo por nombre: %s | tipo final: %s | enviado (hora WhatsApp): %s",
                    tipo_por_nombre, tipo, msg.get("timestamp"), extra={"wa_media_id": media_id})

        url_storage = None
        if prospecto_id:
            try:
                url_storage = await subir_a_storage(
                    file_bytes=file_bytes,
                    nombre_archivo=nombre_archivo,
                    prospecto_id=prospecto_id,
                    mime_type=mime_type,
                )
            except Exception as e:
                logger.warning(f"No se pudo subir a Storage: {e}")

            await insertar_documento(
                prospecto_id=prospecto_id,
                tipo=tipo,
                nombre_archivo=nombre_archivo,
                url_storage=url_storage,
                wa_media_id=media_id,
                mime_type=mime_type,
            )

            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="entrante",
                text=f"[{msg_type.upper()}] {nombre_archivo} → tipo: {tipo}",
                wa_message_id=msg.get("id"),
            )

        # Confirmar recepción y notificar pendientes
        docs_recibidos = []
        if prospecto_id:
            docs_recibidos = await obtener_documentos_prospecto(prospecto_id)
            # Notificar ejecutivo cuando el cliente envía su primer documento
            cliente_id_media: Optional[int] = (prospecto or {}).get("cliente_id")
            nombre_p = (prospecto or {}).get("nombre") or from_number
            if len(docs_recibidos) == 1 and cliente_id_media:
                asyncio.create_task(_notificar_recordatorio_estado(
                    cliente_id=cliente_id_media,
                    nombre_cliente=nombre_p,
                    tipo="primer_documento",
                ))

        datos_calificacion = {campo: (prospecto or {}).get(campo) for campo in _CAMPOS_CALIFICACION}
        requeridos_dict    = _docs_requeridos(datos_calificacion)
        pendientes         = documentos_pendientes(docs_recibidos, datos_calificacion)
        tipo_label         = requeridos_dict.get(tipo, {}).get("label") or nombre_archivo

        if not pendientes:
            confirmacion = (
                f"✅ ¡Recibí tu documento ({tipo_label})!\n\n"
                f"🎉 ¡Perfecto! Ya tenemos todos los documentos necesarios. "
                f"El equipo los revisará y se pondrá en contacto contigo pronto."
            )
            if prospecto_id:
                await actualizar_datos_prospecto(prospecto_id, {}, "DOCS_RECIBIDOS")
                if cliente_id_media:
                    asyncio.create_task(_notificar_recordatorio_estado(
                        cliente_id=cliente_id_media,
                        nombre_cliente=nombre_p,
                        tipo="docs_completos",
                    ))
        else:
            pendientes_texto = "\n".join(
                f"  ▸ {requeridos_dict[t]['label']} ({falta} archivo{'s' if falta > 1 else ''} más)"
                for t, falta in pendientes.items()
                if t in requeridos_dict
            )
            confirmacion = (
                f"✅ ¡Recibí tu documento ({tipo_label})!\n\n"
                f"Aún me faltan los siguientes documentos:\n{pendientes_texto}\n\n"
                f"Puedes enviarlos directamente por este chat 📎"
            )
            if prospecto_id:
                paso_actual = (prospecto or {}).get("paso", "INICIO")
                if paso_actual not in ("ESPERA_DOCS", "DOCS_RECIBIDOS"):
                    await actualizar_datos_prospecto(prospecto_id, {}, "ESPERA_DOCS")

        await send_whatsapp_message(to=from_number, text=confirmacion)

        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="saliente",
                text=confirmacion,
            )

    except Exception as e:
        logger.exception(f"Error procesando media: {e}")
        try:
            await send_whatsapp_message(
                to=from_number,
                text="Recibí tu archivo, pero tuve un problema al procesarlo. Por favor intenta enviarlo nuevamente 🙏",
            )
        except Exception as e_aviso:
            registrar_error(e_aviso, "_procesar_media: aviso de fallo al cliente",
                            mensaje="Tampoco se pudo avisar al cliente del problema con su archivo", nivel=logging.WARNING)


# ---------------------------------------------------------------------------
# Ingesta de prospectos (primer mensaje)
# ---------------------------------------------------------------------------

@app.post("/prospectos/ingesta")
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


# ---------------------------------------------------------------------------
# WhatsApp Cloud API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Auth JWT (Supabase)
# ---------------------------------------------------------------------------


async def _log_correo(tipo: str, destinatario: str, asunto: str, estado: str, detalle_error: str = None, usuario_id: str = None):
    try:
        await _supabase_request("POST", "/log_correos", json={
            "tipo": tipo, "destinatario": destinatario, "asunto": asunto,
            "estado": estado, "detalle_error": detalle_error, "usuario_id": usuario_id,
        })
    except Exception as e:
        logger.warning("No se pudo guardar log de correo: %s", e)


# ---------------------------------------------------------------------------
# API — Usuarios
# ---------------------------------------------------------------------------

@app.get("/api/usuarios/me")
async def api_usuario_actual(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    return perfil


@app.get("/api/usuarios")
async def api_listar_usuarios(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request("GET", "/Usuario", params={"select": "*", "order": "created_at.desc"})
    return rows or []


@app.post("/api/usuarios")
async def api_crear_usuario(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body    = await request.json()
        nombre  = (body.get("nombre") or "").strip()
        rut     = (body.get("rut") or "").strip()
        correo  = (body.get("correo") or "").strip()
        celular      = (body.get("celular") or "").strip() or None
        rol          = body.get("rol", "usuario")
        email_alias  = (body.get("email_alias") or "").strip() or correo
        if not nombre or not rut or not correo:
            return Response(content="Faltan campos obligatorios: nombre, rut, correo", status_code=400)
        roles_permitidos = ("ejecutivo", "administrador") if _solo_owner(perfil) else ("ejecutivo",)
        if rol not in roles_permitidos:
            return Response(content=f"rol debe ser uno de: {', '.join(roles_permitidos)}", status_code=400)
        inmobiliaria_ids = body.get("inmobiliaria_ids") or []
        proyecto_ids     = body.get("proyecto_ids") or []
        user_id = await _invitar_usuario_supabase(correo, nombre, rol)
        await _supabase_request("POST", "/Usuario", json={
            "id": user_id, "nombre": nombre, "rut": rut, "correo": correo,
            "celular": celular, "rol": rol, "password_provisional": False,
            "email_alias": email_alias,
            "inmobiliaria_ids": inmobiliaria_ids,
            "proyecto_ids": proyecto_ids,
        })
        asyncio.create_task(_log_correo("invitacion", correo, "Invitación al CRM", "enviado", usuario_id=user_id))
        if celular:
            cel_norm = _normalize_phone(celular)
            if cel_norm:
                asyncio.create_task(send_whatsapp_template(
                    to=cel_norm,
                    template_name="recordatorio_wtsp",
                    language_code="es_CL",
                    body_text_params=[nombre, "Bienvenida al CRM", "QueSubsidio CRM"],
                ))
        return {"ok": True, "usuario_id": user_id}
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_usuario")


@app.get("/api/log-correos")
async def api_log_correos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request(
        "GET", "/log_correos",
        params={"select": "*", "order": "created_at.desc", "limit": "100"},
    )
    return rows or []


@app.get("/api/log-whatsapp")
async def api_log_whatsapp(request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request(
        "GET", "/LogWhatsapp",
        params={"select": "*,Cliente(Contacto,Telefono)", "order": "created_at.desc", "limit": "100"},
    )
    return rows or []


@app.patch("/api/usuarios/{usuario_id}")
async def api_actualizar_usuario(usuario_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body   = await request.json()
        update = {k: body[k] for k in ("nombre", "celular", "rol", "estado", "email_alias", "inmobiliaria_ids", "proyecto_ids") if k in body}
        if not update:
            return Response(content="Nada que actualizar", status_code=400)

        # El rol "owner" es intocable: no se puede quitar a un owner ni asignárselo a otro por acá
        if "rol" in update:
            actuales = await _supabase_request("GET", "/Usuario",
                params={"id": f"eq.{usuario_id}", "select": "rol", "limit": "1"}) or []
            rol_actual = actuales[0].get("rol") if actuales else None
            if rol_actual == "owner" or update["rol"] == "owner":
                update.pop("rol")
                if not update:
                    return Response(content="El rol Owner no se puede modificar", status_code=400)

        try:
            await _supabase_request("PATCH", "/Usuario", params={"id": f"eq.{usuario_id}"}, json=update)
        finally:
            # aunque el PATCH falle a medias, no se deja un perfil anterior en caché
            user_cache.invalidar_usuario(usuario_id)
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_actualizar_usuario")


@app.post("/api/auth/cambiar-password")
async def api_cambiar_password(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        body  = await request.json()
        nueva = (body.get("nueva_password") or "").strip()
        if len(nueva) < 8:
            return Response(content="La contraseña debe tener al menos 8 caracteres", status_code=400)
        token    = request.headers.get("Authorization", "")[7:]
        await cambiar_password_supabase(token, nueva)
        try:
            await _supabase_request("PATCH", "/Usuario",
                params={"id": f"eq.{perfil['id']}"},
                json={"password_provisional": False})
        finally:
            user_cache.invalidar_usuario(perfil["id"])
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_cambiar_password")


# ---------------------------------------------------------------------------
# API — Templates WhatsApp (dinámico desde Meta)
# ---------------------------------------------------------------------------

@app.get("/api/templates")
async def api_listar_templates(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not WA_WABA_ID:
        return Response(content="WA_WABA_ID no configurado en el servidor", status_code=500)
    try:
        async with http_client(20) as client:
            r = await client.get(
                f"https://graph.facebook.com/{VERSION_GRAPH}/{WA_WABA_ID}/message_templates",
                params={"status": "APPROVED", "limit": "100"},
                headers={"Authorization": f"Bearer {TOKEN_ACCESO}"},
            )
            r.raise_for_status()
        return {
            "templates": [
                {"name": t["name"], "language": t.get("language", "es"), "category": t.get("category", ""), "components": t.get("components", [])}
                for t in r.json().get("data", [])
            ]
        }
    except Exception as e:
        return respuesta_error(e, contexto="api_listar_templates")


# ---------------------------------------------------------------------------
# API — Configuración de plantillas WhatsApp
# ---------------------------------------------------------------------------

@app.get("/api/plantillas-config")
async def api_listar_plantillas_config(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Forbidden", status_code=403)
    rows = await _supabase_request("GET", "/PlantillaConfig", params={"select": "*"}) or []
    # Combinar hardcoded (fallback) con lo que hay en DB; DB tiene prioridad
    merged: Dict[str, Dict] = {
        name: {"template_name": name, "variables": vars_list, "source": "default"}
        for name, vars_list in TEMPLATE_VARS_MAP.items()
    }
    for row in rows:
        merged[row["template_name"]] = {**row, "source": "db"}
    return list(merged.values())


@app.put("/api/plantillas-config/{template_name}")
async def api_upsert_plantilla_config(template_name: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Forbidden", status_code=403)
    body = await request.json()
    variables   = body.get("variables", [])
    param_names = body.get("param_names") or None  # nombres de parámetros nombrados (ej: ["nombre_cliente"])
    if not isinstance(variables, list):
        return Response(content="variables debe ser una lista", status_code=400)
    now_iso = datetime.now(timezone.utc).isoformat()
    patch_data: Dict[str, Any] = {"variables": variables, "updated_at": now_iso}
    if param_names is not None:
        patch_data["param_names"] = param_names
    existing = await _supabase_request(
        "GET", "/PlantillaConfig",
        params={"template_name": f"eq.{template_name}", "select": "id", "limit": "1"},
    ) or []
    if existing:
        await _supabase_request("PATCH", "/PlantillaConfig",
            params={"template_name": f"eq.{template_name}"}, json=patch_data)
    else:
        await _supabase_request("POST", "/PlantillaConfig",
            json={"template_name": template_name, **patch_data})
    return {"ok": True}


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



@app.get("/api/empresas")
async def api_listar_empresas(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    params: Dict[str, str] = {"estado": "eq.activa", "select": "id,nombre,slug,logo_url,color_marca", "order": "nombre.asc"}
    if not _solo_admin(perfil):
        inm_ids = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        if not inm_ids:
            return []
        inm_str = ",".join(str(i) for i in inm_ids)
        inmobiliarias = await _supabase_request(
            "GET", "/Inmobiliaria",
            params={"id": f"in.({inm_str})", "select": "empresa_id"},
        ) or []
        empresa_ids = list({str(i["empresa_id"]) for i in inmobiliarias if i.get("empresa_id")})
        if not empresa_ids:
            return []
        params["id"] = f"in.({','.join(empresa_ids)})"
    rows = await _supabase_request("GET", "/Empresa", params=params)
    return rows or []


@app.get("/api/industrias")
async def api_listar_industrias(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request("GET", "/Industria",
        params={"select": "id,nombre", "order": "nombre.asc"})
    return rows or []


@app.post("/api/empresas")
async def api_crear_empresa(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    nombre = (body.get("nombre") or "").strip()
    if not nombre:
        return Response(content="Falta el nombre", status_code=400)
    slug = (body.get("slug") or "").strip() or _slugify(nombre)
    payload: Dict[str, Any] = {
        "nombre": nombre, "slug": slug, "estado": "activa",
        "logo_url": (body.get("logo_url") or "").strip() or None,
        "color_marca": (body.get("color_marca") or "").strip() or None,
    }
    if body.get("industria_id"):
        payload["industria_id"] = body["industria_id"]
    try:
        row = await _supabase_request("POST", "/Empresa", json=payload,
            extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_empresa")


@app.patch("/api/empresas/{empresa_id}")
async def api_editar_empresa(empresa_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    payload: Dict[str, Any] = {}
    if "nombre" in body:
        payload["nombre"] = (body["nombre"] or "").strip()
    if "slug" in body:
        payload["slug"] = (body["slug"] or "").strip()
    if "logo_url" in body:
        payload["logo_url"] = (body["logo_url"] or "").strip() or None
    if "color_marca" in body:
        payload["color_marca"] = (body["color_marca"] or "").strip() or None
    if "industria_id" in body:
        payload["industria_id"] = body["industria_id"] or None
    if "estado" in body:
        payload["estado"] = body["estado"]
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    try:
        await _supabase_request("PATCH", "/Empresa",
            params={"id": f"eq.{empresa_id}"}, json=payload,
            extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_empresa")


@app.get("/api/inmobiliarias")
async def api_listar_inmobiliarias(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    empresa_id = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": "id,nombre,empresa_id,Empresa(nombre)", "order": "nombre.asc"}
    if empresa_id:
        params["empresa_id"] = f"eq.{empresa_id}"
    if not _solo_admin(perfil):
        inm_ids = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        if not inm_ids:
            return []
        params["id"] = f"in.({','.join(str(i) for i in inm_ids)})"
    rows = await _supabase_request("GET", "/Inmobiliaria", params=params)
    return rows or []


@app.post("/api/inmobiliarias")
async def api_crear_inmobiliaria(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    nombre     = (body.get("nombre") or "").strip()
    empresa_id = body.get("empresa_id")
    if not nombre or not empresa_id:
        return Response(content="Faltan campos obligatorios", status_code=400)
    row = await _supabase_request("POST", "/Inmobiliaria",
        json={"nombre": nombre, "empresa_id": empresa_id},
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@app.patch("/api/inmobiliarias/{inm_id}")
async def api_editar_inmobiliaria(inm_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    nombre = (body.get("nombre") or "").strip()
    if not nombre:
        return Response(content="Falta nombre", status_code=400)
    await _supabase_request("PATCH", "/Inmobiliaria",
        params={"id": f"eq.{inm_id}"},
        json={"nombre": nombre},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


@app.get("/api/proyectos")
async def api_listar_proyectos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _PROYECTO_SELECT_PANEL, "order": "nombre.asc"}
    if _solo_admin(perfil):
        if inmobiliaria_id:
            params["inmobiliaria_id"] = f"eq.{inmobiliaria_id}"
        elif empresa_id:
            inmobiliarias = await _supabase_request(
                "GET", "/Inmobiliaria",
                params={"empresa_id": f"eq.{empresa_id}", "select": "id"},
            ) or []
            inm_ids = ",".join(str(i["id"]) for i in inmobiliarias)
            if not inm_ids:
                return []
            params["inmobiliaria_id"] = f"in.({inm_ids})"
    else:
        inm_asig  = [i for i in (perfil.get("inmobiliaria_ids") or []) if i is not None]
        proy_asig = [str(p) for p in (perfil.get("proyecto_ids") or []) if p is not None]
        proy_de_inm: List[str] = []
        if inm_asig:
            inm_str = ",".join(str(i) for i in inm_asig)
            proy_de_inm = [str(p["id"]) for p in (await _supabase_request(
                "GET", "/Proyecto",
                params={"inmobiliaria_id": f"in.({inm_str})", "select": "id"},
            ) or []) if p.get("id")]
        visibles = set(proy_asig + proy_de_inm)
        if not visibles:
            return []
        params["id"] = f"in.({','.join(visibles)})"
    rows = await _supabase_request("GET", "/Proyecto", params=params)
    return rows or []


@app.post("/api/proyectos")
async def api_crear_proyecto(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    body = await request.json()
    nombre          = (body.get("nombre") or "").strip()
    codigo          = (body.get("codigo") or "").strip()
    ubicacion       = (body.get("ubicacion") or "").strip() or None
    inmobiliaria_id = body.get("inmobiliaria_id")
    if not nombre or not codigo or not inmobiliaria_id:
        return Response(content="Faltan campos obligatorios", status_code=400)

    payload: Dict[str, Any] = {
        "nombre": nombre, "codigo": codigo,
        "ubicacion": ubicacion, "inmobiliaria_id": inmobiliaria_id,
    }
    _aplicar_campos_proyecto(body, payload, es_admin=_solo_admin(perfil))
    row = await _supabase_request("POST", "/Proyecto", json=payload,
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@app.patch("/api/proyectos/{proyecto_id}")
async def api_editar_proyecto(proyecto_id: str, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    payload: Dict[str, Any] = {}
    for f in ("nombre", "codigo", "ubicacion"):
        if body.get(f): payload[f] = body[f].strip()
    _aplicar_campos_proyecto(body, payload, es_admin=_solo_admin(perfil))
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    await _supabase_request("PATCH", "/Proyecto",
        params={"id": f"eq.{proyecto_id}"},
        json=payload, extra_headers={"Prefer": "return=minimal"})
    _cache_proyectos["data"] = None
    return {"ok": True}


@app.post("/api/proyectos/upload-imagen")
async def api_subir_imagen_proyecto(request: Request, file: UploadFile = File(...)):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)

    MAX_SIZE = 3 * 1024 * 1024
    contenido = await file.read()
    if len(contenido) > MAX_SIZE:
        return Response(content="La imagen supera los 3MB", status_code=400)

    mime = file.content_type or "image/jpeg"
    ext  = (file.filename or "imagen").rsplit(".", 1)[-1].lower() or "jpg"
    path = f"proyecto-{int(time.time())}.{ext}"

    try:
        public_url = await subir_imagen_proyecto(path, contenido, mime)
    except StorageError as e:
        return respuesta_error(e, contexto="api_subir_imagen_proyecto")
    return {"imagen_url": public_url}


# ── Tipologia ─────────────────────────────────────────────────────────────────

_TIPOLOGIA_SELECT = "id,proyecto_id,Proyecto(nombre),nombre,dormitorios,banos,superficie_util_m2,terreno_m2,valor_uf,monto_subsidio,tipo_subsidio,estado,cuotas_ahorro,estacionamientos,bodegas,EtapaTipologia(id,stock,Etapa(id,nombre,fecha_entrega,estado))"

@app.get("/api/tipologias")
async def api_listar_tipologias(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _TIPOLOGIA_SELECT, "order": "nombre.asc"}

    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    elif inmobiliaria_id:
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"}) or []
        ids = ",".join(p["id"] for p in prows)
        if not ids: return []
        params["proyecto_id"] = f"in.({ids})"
    elif empresa_id:
        inms = await _supabase_request("GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
        inm_ids = ",".join(str(i["id"]) for i in inms)
        if not inm_ids: return []
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"}) or []
        ids = ",".join(p["id"] for p in prows)
        if not ids: return []
        params["proyecto_id"] = f"in.({ids})"

    rows = await _supabase_request("GET", "/Tipologia", params=params)
    return rows or []


_CAMPOS_TIPOLOGIA = ("dormitorios", "banos", "superficie_util_m2", "terreno_m2",
                     "valor_uf", "monto_subsidio",
                     "tipo_subsidio", "estado", "cuotas_ahorro",
                     "estacionamientos", "bodegas")

@app.post("/api/tipologias")
async def api_crear_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        proyecto_id = body.get("proyecto_id")
        nombre      = (body.get("nombre") or "").strip()
        if not proyecto_id or not nombre:
            return Response(content="Faltan campos obligatorios", status_code=400)
        if not await obtener_proyecto_por_id(proyecto_id):
            return Response(content="Proyecto no encontrado", status_code=404)
        payload: Dict[str, Any] = {"proyecto_id": proyecto_id, "nombre": nombre}
        for campo in _CAMPOS_TIPOLOGIA:
            if body.get(campo) is not None:
                payload[campo] = body[campo]
        row = await _supabase_request("POST", "/Tipologia",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_tipologia")


@app.patch("/api/tipologias/{tip_id}")
async def api_editar_tipologia(tip_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        payload: Dict[str, Any] = {}
        if body.get("nombre"): payload["nombre"] = body["nombre"].strip()
        for campo in _CAMPOS_TIPOLOGIA:
            if campo in body: payload[campo] = body[campo]
        if not payload:
            return Response(content="Nada que actualizar", status_code=400)
        await _supabase_request("PATCH", "/Tipologia",
            params={"id": f"eq.{tip_id}"},
            json=payload, extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_tipologia")


@app.delete("/api/tipologias/{tip_id}")
async def api_eliminar_tipologia(tip_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", "/Tipologia",
        params={"id": f"eq.{tip_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


# ── Etapa ─────────────────────────────────────────────────────────────────────

_ETAPA_SELECT = "id,proyecto_id,Proyecto(nombre),nombre,descripcion,fecha_entrega,estado,tipo_ahorro,num_cuotas,EtapaTipologia(id,stock,Tipologia(id,nombre,dormitorios,banos))"
_CAMPOS_ETAPA = ("nombre", "descripcion", "fecha_entrega", "estado", "tipo_ahorro", "num_cuotas")


@app.get("/api/etapas")
async def api_listar_etapas(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _ETAPA_SELECT, "order": "fecha_entrega.asc"}

    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    elif inmobiliaria_id:
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"}) or []
        ids = ",".join(str(p["id"]) for p in prows)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"
    elif empresa_id:
        inms = await _supabase_request("GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
        inm_ids = ",".join(str(i["id"]) for i in inms)
        if not inm_ids:
            return []
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"}) or []
        ids = ",".join(str(p["id"]) for p in prows)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"

    rows = await _supabase_request("GET", "/Etapa", params=params)
    return rows or []


@app.post("/api/etapas")
async def api_crear_etapa(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        proyecto_id = body.get("proyecto_id")
        if not proyecto_id:
            return Response(content="Falta proyecto_id", status_code=400)
        if not await obtener_proyecto_por_id(proyecto_id):
            return Response(content="Proyecto no encontrado", status_code=404)
        payload: Dict[str, Any] = {"proyecto_id": proyecto_id}
        for campo in _CAMPOS_ETAPA:
            if campo in body:
                payload[campo] = body[campo] or None
        row = await _supabase_request("POST", "/Etapa",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_etapa")


@app.patch("/api/etapas/{etapa_id}")
async def api_editar_etapa(etapa_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        payload: Dict[str, Any] = {}
        for campo in _CAMPOS_ETAPA:
            if campo in body:
                payload[campo] = body[campo] or None
        if not payload:
            return Response(content="Nada que actualizar", status_code=400)
        await _supabase_request("PATCH", "/Etapa",
            params={"id": f"eq.{etapa_id}"},
            json=payload, extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_etapa")


@app.delete("/api/etapas/{etapa_id}")
async def api_eliminar_etapa(etapa_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", "/Etapa",
        params={"id": f"eq.{etapa_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


# ── EtapaTipologia ────────────────────────────────────────────────────────────

_ETAPA_TIP_SELECT = "id,etapa_id,tipologia_id,stock,Etapa(id,nombre,fecha_entrega,estado),Tipologia(id,nombre,dormitorios,banos,valor_uf,tipo_subsidio)"

@app.get("/api/etapa-tipologia")
async def api_listar_etapa_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    tipologia_id = request.query_params.get("tipologia_id")
    etapa_id     = request.query_params.get("etapa_id")
    params: Dict[str, str] = {"select": _ETAPA_TIP_SELECT}
    if tipologia_id:
        params["tipologia_id"] = f"eq.{tipologia_id}"
    elif etapa_id:
        params["etapa_id"] = f"eq.{etapa_id}"
    else:
        return Response(content="Se requiere tipologia_id o etapa_id", status_code=400)
    rows = await _supabase_request("GET", "/EtapaTipologia", params=params)
    return rows or []


@app.get("/api/etapa-tipologia/{et_id}")
async def api_get_etapa_tipologia(et_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request("GET", "/EtapaTipologia",
        params={"id": f"eq.{et_id}", "select": "id,tipologia_id,etapa_id", "limit": "1"}) or []
    return rows[0] if rows else {}


@app.post("/api/etapa-tipologia/find-or-create")
async def api_find_or_create_etapa_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        tipologia_id = body.get("tipologia_id")
        etapa_id     = body.get("etapa_id")
        if not tipologia_id or not etapa_id:
            return Response(content="Faltan tipologia_id o etapa_id", status_code=400)
        existing = await _supabase_request("GET", "/EtapaTipologia",
            params={"tipologia_id": f"eq.{tipologia_id}", "etapa_id": f"eq.{etapa_id}",
                    "select": "id", "limit": "1"}) or []
        if existing:
            return {"id": existing[0]["id"]}
        row = await _supabase_request("POST", "/EtapaTipologia",
            json={"etapa_id": int(etapa_id), "tipologia_id": int(tipologia_id), "stock": 1},
            extra_headers={"Prefer": "return=representation"})
        return {"id": (row[0]["id"] if isinstance(row, list) and row else row["id"])}
    except Exception as e:
        return respuesta_error(e, contexto="api_find_or_create_etapa_tipologia")


@app.post("/api/etapa-tipologia")
async def api_crear_etapa_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        etapa_id     = body.get("etapa_id")
        tipologia_id = body.get("tipologia_id")
        stock        = body.get("stock", 0)
        if not etapa_id or not tipologia_id:
            return Response(content="Faltan etapa_id o tipologia_id", status_code=400)
        payload = {"etapa_id": int(etapa_id), "tipologia_id": int(tipologia_id), "stock": int(stock or 0)}
        row = await _supabase_request("POST", "/EtapaTipologia",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_etapa_tipologia")


@app.patch("/api/etapa-tipologia/{et_id}")
async def api_editar_etapa_tipologia(et_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        stock = body.get("stock", 0)
        await _supabase_request("PATCH", "/EtapaTipologia",
            params={"id": f"eq.{et_id}"},
            json={"stock": int(stock or 0)},
            extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_etapa_tipologia")


@app.delete("/api/etapa-tipologia/{et_id}")
async def api_eliminar_etapa_tipologia(et_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", "/EtapaTipologia",
        params={"id": f"eq.{et_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


# ── Scraping: buydepa ───────────────────────────────────────────────────────


@app.post("/api/scraping/buydepa/ejecutar")
async def api_ejecutar_scraping_buydepa(request: Request):
    from fastapi.responses import StreamingResponse as _SR
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    return _SR(
        _sincronizar_buydepa_stream(usuario_id=perfil.get("id")),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/scraping/logs")
async def api_listar_logs_scraping(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    rows = await _supabase_request("GET", "/ScrapingLog",
        params={"select": "*,Usuario(nombre)", "order": "ejecutado_en.desc", "limit": "100"})
    return rows or []


# ── Ejecutivos bancarios ──────────────────────────────────────────────────────

@app.get("/api/ejecutivos")
async def api_listar_ejecutivos(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request("GET", "/EjecutivoBancario",
        params={"select": "id,ejecutivo,entidad,email,telefono,disponible,proyecto", "order": "ejecutivo.asc"}) or []

    pe_rows = await _supabase_request("GET", "/ProyectoEjecutivo",
        params={"select": "ejecutivo_id,proyecto_id"}) or []
    adicionales_por_ejec: Dict[Any, List[str]] = {}
    for r in pe_rows:
        adicionales_por_ejec.setdefault(r["ejecutivo_id"], []).append(r["proyecto_id"])

    for e in rows:
        proyecto_ids = list(adicionales_por_ejec.get(e["id"], []))
        # El campo legado "proyecto" (proyecto único) se funde en la misma lista
        # para que el front muestre siempre una sola vista unificada.
        if e.get("proyecto") and e["proyecto"] not in proyecto_ids:
            proyecto_ids.append(e["proyecto"])
        e["proyecto_ids"] = proyecto_ids
    return rows

@app.post("/api/ejecutivos")
async def api_crear_ejecutivo(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    payload = {
        "ejecutivo": (body.get("ejecutivo") or "").strip(),
        "entidad":   (body.get("entidad") or "").strip(),
        "email":     (body.get("email") or "").strip().lower(),
        "disponible": bool(body.get("disponible", True)),
    }
    if body.get("telefono"):
        payload["telefono"] = body["telefono"]
    if not payload["ejecutivo"] or not payload["entidad"] or not payload["email"]:
        return Response(content="Nombre, entidad y email son obligatorios", status_code=400)
    row = await _supabase_request("POST", "/EjecutivoBancario", json=payload)
    creado = row[0] if isinstance(row, list) else row

    proyecto_ids = body.get("proyecto_ids") or []
    if proyecto_ids and creado and creado.get("id"):
        await _supabase_request("POST", "/ProyectoEjecutivo",
            json=[{"ejecutivo_id": creado["id"], "proyecto_id": pid} for pid in proyecto_ids],
            extra_headers={"Prefer": "return=minimal"})
    return creado

@app.patch("/api/ejecutivos/{ejecutivo_id}")
async def api_editar_ejecutivo(ejecutivo_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    allowed = {"ejecutivo", "entidad", "email", "telefono", "disponible"}
    payload = {k: v for k, v in body.items() if k in allowed}
    if "email" in payload:
        payload["email"] = payload["email"].strip().lower()
    if "ejecutivo" in payload:
        payload["ejecutivo"] = payload["ejecutivo"].strip()
    if "entidad" in payload:
        payload["entidad"] = payload["entidad"].strip()

    proyecto_ids = body.get("proyecto_ids")
    if proyecto_ids is not None:
        # La lista de proyectos permitidos ahora vive completa en ProyectoEjecutivo;
        # el campo legado "proyecto" se limpia para no duplicar la fuente de verdad.
        payload["proyecto"] = None
        await _supabase_request("DELETE", "/ProyectoEjecutivo",
            params={"ejecutivo_id": f"eq.{ejecutivo_id}"}, extra_headers={"Prefer": "return=minimal"})
        if proyecto_ids:
            await _supabase_request("POST", "/ProyectoEjecutivo",
                json=[{"ejecutivo_id": ejecutivo_id, "proyecto_id": pid} for pid in proyecto_ids],
                extra_headers={"Prefer": "return=minimal"})

    if payload:
        await _supabase_request("PATCH", f"/EjecutivoBancario?id=eq.{ejecutivo_id}", json=payload)
    return {"ok": True}

@app.delete("/api/ejecutivos/{ejecutivo_id}")
async def api_eliminar_ejecutivo(ejecutivo_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil or not _solo_admin(perfil):
        return Response(content="Unauthorized", status_code=401)
    await _supabase_request("DELETE", f"/EjecutivoBancario?id=eq.{ejecutivo_id}")
    return {"ok": True}

# ── ProyectoEjecutivo ─────────────────────────────────────────────────────────

@app.get("/api/proyecto-ejecutivos")
async def api_listar_proyecto_ejecutivos(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id = request.query_params.get("proyecto_id")
    params: Dict[str, str] = {
        "select": "proyecto_id,ejecutivo_id,EjecutivoBancario(id,ejecutivo,email)",
    }
    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    rows = await _supabase_request("GET", "/ProyectoEjecutivo", params=params)
    return rows or []


@app.post("/api/proyecto-ejecutivos")
async def api_asignar_ejecutivo(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    body = await request.json()
    proyecto_id  = body.get("proyecto_id")
    ejecutivo_id = body.get("ejecutivo_id")
    if not proyecto_id or not ejecutivo_id:
        return Response(content="Faltan campos obligatorios", status_code=400)
    proyecto = await obtener_proyecto_por_id(proyecto_id)
    if not proyecto:
        return Response(content="Proyecto no encontrado", status_code=404)
    row = await _supabase_request("POST", "/ProyectoEjecutivo",
        json={"proyecto_id": proyecto_id, "ejecutivo_id": ejecutivo_id},
        extra_headers={"Prefer": "return=representation"})
    return row[0] if isinstance(row, list) and row else row


@app.delete("/api/proyecto-ejecutivos")
async def api_quitar_ejecutivo(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id  = request.query_params.get("proyecto_id")
    ejecutivo_id = request.query_params.get("ejecutivo_id")
    if not proyecto_id or not ejecutivo_id:
        return Response(content="Faltan parámetros", status_code=400)
    await _supabase_request("DELETE", "/ProyectoEjecutivo",
        params={"proyecto_id": f"eq.{proyecto_id}", "ejecutivo_id": f"eq.{ejecutivo_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


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


@app.get("/api/clientes/{cliente_id}/documentos")
async def api_documentos_cliente(cliente_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
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


@app.patch("/api/clientes/{cliente_id}/documentos/{doc_id}/tipo")
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

@app.post("/api/clientes/{cliente_id}/documentos/upload")
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


@app.get("/api/clientes/{cliente_id}/documentos/{doc_id}/descargar")
async def api_descargar_documento(cliente_id: int, doc_id: str, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        # Verificar que el doc pertenece al cliente correcto
        prospectos = await _supabase_request("GET", "/Prospecto",
            params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"})
        prospecto_id = prospectos[0]["id"] if prospectos else None
        docs = await _supabase_request("GET", "/Documento",
            params={
                "id": f"eq.{doc_id}",
                **({"prospecto_id": f"eq.{prospecto_id}"} if prospecto_id else {}),
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


@app.delete("/api/clientes/{cliente_id}/documentos/{doc_id}")
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


@app.get("/api/clientes/{cliente_id}/preview-evaluacion")
async def api_preview_evaluacion(cliente_id: int, request: Request):
    """Devuelve los datos que se incluirán en el correo de evaluación, sin enviarlo."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)

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


@app.post("/api/clientes/{cliente_id}/enviar-evaluacion")
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


# ── Recordatorio WA al ejecutivo ─────────────────────────────────────────────

TEMPLATE_RECORDATORIO_WA = get_settings().template_recordatorio_wa

@app.post("/api/clientes/{cliente_id}/recordatorio-wa")
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


# ── Movendo / Zonapropia Integration ─────────────────────────────────────────

def _movendo_webhook_key() -> Optional[str]:
    return get_settings().movendo_webhook_key

def _verificar_movendo_auth(request: Request) -> bool:
    key = _movendo_webhook_key()
    if not key:
        return True  # Sin clave configurada → aceptar (desarrollo)
    auth = request.headers.get("Authorization", "")
    return auth.lower().startswith("bearer ") and auth[7:].strip() == key


@app.post("/api/movendo/nuevo-cliente")
async def api_movendo_nuevo_cliente(request: Request):
    """Recibe un nuevo lead de Movendo y lo registra como Cliente."""
    if not _verificar_movendo_auth(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()

        first     = (body.get("firstName") or "").strip()
        last      = (body.get("lastName") or "").strip()
        nombre    = f"{first} {last}".strip() or (body.get("phone") or "Sin nombre")
        phone_raw = (body.get("phone") or "").strip()
        crm_id      = str(body.get("crmId") or "").strip() or None
        proj_name   = (body.get("projectName") or "").strip()
        movendo_pid = body.get("projectId")  # ID numérico del proyecto en Movendo
        email       = (body.get("email") or "").strip() or None
        rut         = (body.get("rut") or "").strip() or None
        salary      = str(body.get("salary") or "").strip() or None
        source      = (body.get("source") or "").strip() or None

        if not phone_raw:
            return Response(content="phone es requerido", status_code=400)
        telefono = _normalizar_telefono_cl(phone_raw)
        if not telefono:
            return Response(content=f"Teléfono inválido: {phone_raw}", status_code=400)
        if not proj_name and movendo_pid is None:
            return Response(content="projectName o projectId es requerido", status_code=400)

        # Buscar proyecto — orden de prioridad:
        # 1) ID numérico de Movendo (movendo_proyecto_id)
        # 2) nombre ilike
        # 3) codigo ilike
        # 4) prefijo o alias en nombres_csv (maneja "Viñedos de Rengo II" → "Viñedos de Rengo")
        _sel_proy = "id,nombre,imagen_url,nombres_csv"
        proyectos = []

        if movendo_pid is not None:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"movendo_proyecto_id": f"eq.{movendo_pid}", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"nombre": f"ilike.%{proj_name}%", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            proyectos = await _supabase_request("GET", "/Proyecto",
                params={"codigo": f"ilike.%{proj_name}%", "select": _sel_proy, "limit": "1"}) or []

        if not proyectos and proj_name:
            todos = await _supabase_request("GET", "/Proyecto", params={"select": _sel_proy}) or []
            proj_lower = proj_name.lower().strip()
            todos_sorted = sorted(todos, key=lambda p: len(p.get("nombre") or ""), reverse=True)
            for p in todos_sorted:
                db_nombre = (p.get("nombre") or "").lower().strip()
                # nombres_csv puede ser lista (array PG) o string separado por comas
                raw_csv = p.get("nombres_csv") or []
                if isinstance(raw_csv, list):
                    aliases = [a.strip().lower() for a in raw_csv if a.strip()]
                else:
                    aliases = [a.strip().lower() for a in str(raw_csv).split(",") if a.strip()]
                if proj_lower in aliases:
                    proyectos = [p]; break
                if db_nombre and proj_lower.startswith(db_nombre):
                    proyectos = [p]; break

        if not proyectos:
            ref = f"projectId={movendo_pid}" if movendo_pid is not None else f"projectName={proj_name}"
            return Response(content=f"Proyecto no encontrado: {ref}", status_code=422,
                            media_type="text/plain")

        proyecto_row      = proyectos[0]
        proyecto_id       = proyecto_row["id"]
        proyecto_nombre   = proyecto_row["nombre"]
        template_auto     = proyecto_row.get("template_bienvenida") or None

        # Evitar duplicados (mismo teléfono + mismo proyecto)
        dup = await _supabase_request("GET", "/Cliente",
            params={"Telefono": f"eq.{telefono}", "proyecto_id": f"eq.{proyecto_id}",
                    "select": "id", "limit": "1"}) or []
        if dup:
            return {"ok": True, "duplicado": True, "cliente_id": dup[0]["id"],
                    "mensaje": "Cliente ya existe en este proyecto"}

        default_user = await _get_default_user()
        payload: Dict[str, Any] = {
            "proyecto_id":        proyecto_id,
            "Contacto":           nombre,
            "Telefono":           telefono,
            "Fecha Ult. Gestión": _utc_now_iso(),
            "primer mensaje":     False,
            "es_nuevo":           True,
        }
        if default_user: payload["usuario_id"]      = default_user["id"]
        if email:       payload["Correo"]           = email
        if rut:         payload["Rut"]              = rut
        if salary:      payload["Tramo de renta"]   = salary
        if crm_id:      payload["movendo_id"]       = crm_id
        if source:      payload["movendo_source"]   = source

        cliente = await _supabase_request("POST", "/Cliente", json=payload,
            extra_headers={"Prefer": "return=representation"})
        cliente_id = cliente[0]["id"] if isinstance(cliente, list) and cliente else None

        # Auto-enviar plantilla de bienvenida si el proyecto la tiene configurada
        wa_result = None
        if template_auto and cliente_id:
            try:
                # Construir parámetros usando PlantillaConfig (soporta named params)
                _wa_cfg = await _supabase_request("GET", "/PlantillaConfig",
                    params={"template_name": f"eq.{template_auto}",
                            "select": "variables,param_names", "limit": "1"}) or []
                _pool = await _pool_plantilla(nombre, proyecto_row)
                if _wa_cfg and _wa_cfg[0].get("variables"):
                    _btp = _build_body_params(_pool, _wa_cfg[0]["variables"], _wa_cfg[0].get("param_names"))
                else:
                    _btp = [nombre, proyecto_nombre]  # fallback básico
                wa_result = await send_whatsapp_template(
                    to=telefono,
                    template_name=template_auto,
                    language_code="es",
                    body_text_params=_btp,
                    image_url=proyecto_row.get("imagen_url") or None,
                )
                ahora_movendo = datetime.now(timezone.utc)
                patch_mov: Dict[str, Any] = {
                    "primer_wtsp_en": ahora_movendo.isoformat(),
                    "Fecha Ult. Gestión": ahora_movendo.isoformat(),
                }
                patch_mov["recordatorio_at"] = (ahora_movendo + timedelta(hours=24)).isoformat()
                await _supabase_request("PATCH", "/Cliente",
                    params={"id": f"eq.{cliente_id}"}, json=patch_mov)
                await upsert_prospecto(
                    telefono_e164=telefono,
                    nombre=nombre,
                    rut=rut,
                    rango_sueldo=salary,
                    proyecto_id=proyecto_id,
                    estado="PLANTILLA_ENVIADA",
                    paso="BIENVENIDA",
                    cliente_id=cliente_id,
                )
                logger.info(f"Movendo auto-template '{template_auto}' enviado a {telefono}")
            except Exception as wa_err:
                logger.warning(f"Movendo: cliente creado pero fallo envío template: {wa_err}")

        logger.info(f"Movendo nuevo cliente: {nombre} ({telefono}) → {proyecto_nombre}")
        asyncio.create_task(_notificar_nuevo_lead(
            nombre=nombre, telefono=telefono, proyecto_nombre=proyecto_nombre,
            correo_admin=default_user.get("correo") if default_user else None,
        ))
        return {"ok": True, "cliente_id": cliente_id}

    except Exception as e:
        return respuesta_error(e, contexto="api_movendo_nuevo_cliente")


@app.post("/api/aplicaciones/actualizar-contactos-quesubsidio")
async def api_movendo_actualizar_contacto(request: Request):
    """Recibe el resumen de conversación de Movendo y actualiza el Cliente."""
    if not _verificar_movendo_auth(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        lead_id = body.get("leadId")
        if not lead_id:
            return Response(content="leadId es requerido", status_code=400)
        lead_id_str = str(lead_id)

        # Buscar por movendo_id
        rows = await _supabase_request("GET", "/Cliente",
            params={"movendo_id": f"eq.{lead_id_str}", "select": "id", "limit": "1"}) or []

        # Si no encontró, intentar por teléfono
        if not rows and body.get("phone"):
            tel = _normalizar_telefono_cl(str(body["phone"]))
            if tel:
                rows = await _supabase_request("GET", "/Cliente",
                    params={"Telefono": f"eq.{tel}", "select": "id", "limit": "1"}) or []

        if not rows:
            return Response(content=f"Cliente no encontrado para leadId={lead_id}", status_code=404,
                            media_type="text/plain")

        cliente_id = rows[0]["id"]
        patch: Dict[str, Any] = {"movendo_id": lead_id_str}

        nombre_parts = [body.get("name") or "", body.get("lastName") or "", body.get("motherLastName") or ""]
        nombre = " ".join(p for p in nombre_parts if p).strip()
        if nombre:             patch["Contacto"]        = nombre
        if body.get("email"):  patch["Correo"]          = body["email"]
        if body.get("rut"):    patch["Rut"]             = body["rut"]
        if body.get("salary"): patch["Tramo de renta"]  = str(body["salary"])

        movendo_data: Dict[str, Any] = {}
        for key in ("summary", "chatLink", "score", "userNotes", "source",
                    "alertType", "alertMessage", "complementSalary", "projectId"):
            if body.get(key) is not None:
                movendo_data[key] = body[key]
        if movendo_data:
            patch["movendo_data"] = movendo_data

        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json=patch, extra_headers={"Prefer": "return=minimal"})

        logger.info(f"Movendo actualizó cliente_id={cliente_id} leadId={lead_id}")
        return {"ok": True, "cliente_id": cliente_id}

    except Exception as e:
        return respuesta_error(e, contexto="api_movendo_actualizar_contacto")


async def _get_default_user() -> Optional[Dict]:
    """Devuelve el usuario admin por defecto {id, correo, nombre}."""
    env_id = get_settings().movendo_default_user_id
    params = {"select": "id,correo,nombre", "limit": "1"}
    if env_id:
        params["id"] = f"eq.{env_id}"
    else:
        params["rol"]    = "in.(owner,administrador)"
        params["estado"] = "eq.1"
    rows = await _supabase_request("GET", "/Usuario", params=params) or []
    return rows[0] if rows else None



async def _notificar_ejecutivo_wa(cliente_id: int, nombre_cliente: str, accion: str) -> Dict[str, Any]:
    """Envía notificación WA al ejecutivo asignado al cliente (template recordatorio_wtsp).
    Retorna {"ok": True} si se envió, o {"ok": False, "motivo": "..."} si no se pudo."""
    if not _dentro_horario_urgente():
        # Horario ampliado (08:00–24:00 hora Chile) para no despertar al ejecutivo de madrugada,
        # pero sin retrasarlo tanto como los avisos rutinarios.
        await asyncio.sleep(_segundos_hasta_horario_urgente())
    try:
        clientes = await _supabase_request("GET", "/Cliente",
            params={"id": f"eq.{cliente_id}", "select": "usuario_id,Proyecto(nombre)", "limit": "1"}) or []
        if not clientes or not clientes[0].get("usuario_id"):
            return {"ok": False, "motivo": "sin ejecutivo asignado"}
        proyecto_nombre = (clientes[0].get("Proyecto") or {}).get("nombre") or "Sin proyecto"
        usuarios = await _supabase_request("GET", "/Usuario",
            params={"id": f"eq.{clientes[0]['usuario_id']}", "select": "nombre,celular", "limit": "1"}) or []
        if not usuarios:
            return {"ok": False, "motivo": "ejecutivo no encontrado"}
        celular = _normalize_phone(usuarios[0].get("celular") or "")
        if not celular:
            return {"ok": False, "motivo": f"ejecutivo '{usuarios[0].get('nombre') or ''}' sin celular configurado"}
        await send_whatsapp_template(
            to=celular,
            template_name="recordatorio_wtsp",
            language_code="es_CL",
            body_text_params=[nombre_cliente, accion, proyecto_nombre],
        )
        logger.info("WA notif ejecutivo → %s | cliente=%s | accion=%s | proyecto=%s",
                    celular, nombre_cliente, accion, proyecto_nombre)
        return {"ok": True}
    except Exception as e:
        logger.warning("No se pudo notificar ejecutivo WA: %s", e)
        return {"ok": False, "motivo": str(e)}


# ── Recordatorios puntuales por avance del bot (event-driven) ─────────────────

_TEXTOS_RECORDATORIO = {
    "primer_documento": "Recibió su primer documento",
    "docs_completos":   "Documentos completos, listo para llamar",
}


async def _notificar_recordatorio_estado(cliente_id: int, nombre_cliente: str, tipo: str) -> Dict[str, Any]:
    """Avisa al ejecutivo asignado cuando el bot mueve al cliente a un estado puntual
    ('primer_documento' | 'docs_completos'). No vuelve a enviar el mismo tipo de aviso
    hasta que el ejecutivo cambie estado_gestion (reconociendo que ya actuó)."""
    if not _dentro_horario_avisos():
        # No despertar al ejecutivo fuera de horario — esperar a que abra 9:00 hora Chile
        # (horario fijo de avisos, independiente del horario del bot).
        await asyncio.sleep(_segundos_hasta_horario_avisos())
    texto = _TEXTOS_RECORDATORIO.get(tipo)
    if not texto:
        return {"ok": False, "motivo": f"tipo '{tipo}' inválido"}
    try:
        clientes = await _supabase_request("GET", "/Cliente",
            params={"id": f"eq.{cliente_id}",
                    "select": "usuario_id,estado_gestion,ultimo_recordatorio_tipo,"
                              "ultimo_recordatorio_estado_gestion,Proyecto(nombre)",
                    "limit": "1"}) or []
        if not clientes:
            return {"ok": False, "motivo": "cliente no encontrado"}
        c = clientes[0]
        if not c.get("usuario_id"):
            return {"ok": False, "motivo": "sin ejecutivo asignado"}

        estado_gestion_actual = c.get("estado_gestion")
        if (c.get("ultimo_recordatorio_tipo") == tipo
                and c.get("ultimo_recordatorio_estado_gestion") == estado_gestion_actual):
            return {"ok": False, "motivo": "ya notificado — pendiente de que el ejecutivo cambie el estado de gestión"}

        proyecto_nombre = (c.get("Proyecto") or {}).get("nombre") or "Sin proyecto"
        usuarios = await _supabase_request("GET", "/Usuario",
            params={"id": f"eq.{c['usuario_id']}", "select": "nombre,celular", "limit": "1"}) or []
        if not usuarios:
            return {"ok": False, "motivo": "ejecutivo no encontrado"}
        celular = _normalize_phone(usuarios[0].get("celular") or "")
        if not celular:
            return {"ok": False, "motivo": f"ejecutivo '{usuarios[0].get('nombre') or ''}' sin celular configurado"}

        await send_whatsapp_template(
            to=celular,
            template_name="recordatorio_wtsp",
            language_code="es_CL",
            body_text_params=[nombre_cliente, texto, proyecto_nombre],
        )
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json={"ultimo_recordatorio_tipo": tipo, "ultimo_recordatorio_estado_gestion": estado_gestion_actual},
            extra_headers={"Prefer": "return=minimal"})
        logger.info("WA recordatorio → %s | cliente=%s | tipo=%s | proyecto=%s",
                    celular, nombre_cliente, tipo, proyecto_nombre)
        return {"ok": True}
    except Exception as e:
        logger.warning("No se pudo enviar recordatorio WA: %s", e)
        return {"ok": False, "motivo": str(e)}


async def _notificar_interesado(prospecto_id: str) -> None:
    """Avisa al admin/ejecutivo asignado cuando un prospecto confirma interés (llega a DOCUMENTACION)."""
    try:
        rows = await _supabase_request("GET", "/Prospecto",
            params={"id": f"eq.{prospecto_id}", "select": "nombre,telefono_e164,cliente_id", "limit": "1"}) or []
        if not rows:
            return
        p = rows[0]
        nombre   = p.get("nombre") or "Cliente"
        telefono = p.get("telefono_e164") or ""
        # Buscar proyecto desde el cliente si existe
        proyecto_nombre = ""
        if p.get("cliente_id"):
            cli = await _supabase_request("GET", "/Cliente",
                params={"id": f"eq.{p['cliente_id']}", "select": "proyecto_id", "limit": "1"}) or []
            if cli and cli[0].get("proyecto_id"):
                proy = await obtener_proyecto_por_id(cli[0]["proyecto_id"])
                proyecto_nombre = (proy or {}).get("nombre", "")

        texto = (
            f"🔥 *Cliente listo para contactar*\n"
            f"👤 {nombre}\n"
            f"📱 {telefono}\n"
            f"🏗️ {proyecto_nombre or 'Sin proyecto'}\n"
            f"✅ Confirmó interés y está reuniendo documentos."
        )
        notify_phone = get_settings().admin_notify_phone
        if notify_phone:
            try:
                await send_whatsapp_message(to=notify_phone, text=texto)
            except Exception as e:
                logger.warning("Notificación interesado WA fallida: %s", e)
    except Exception:
        logger.exception("Error en _notificar_interesado")


async def _notificar_nuevo_lead(nombre: str, telefono: str, proyecto_nombre: str, correo_admin: Optional[str]) -> None:
    """Envía aviso por WhatsApp y correo al admin cuando llega un nuevo lead de Movendo."""
    texto = (
        f"🆕 *Nuevo lead recibido*\n"
        f"👤 {nombre}\n"
        f"📱 {telefono}\n"
        f"🏗️ Proyecto: {proyecto_nombre}"
    )
    notify_phone = get_settings().admin_notify_phone
    if notify_phone:
        try:
            await send_whatsapp_message(to=notify_phone, text=texto)
        except Exception as e:
            logger.warning("Notificación WA fallida: %s", e)

    if correo_admin:
        try:
            body_html = f"""
            <div style="font-family:Arial,sans-serif;max-width:500px;">
              <h2 style="color:#1e3a5f;border-bottom:2px solid #1e3a5f;padding-bottom:8px;">Nuevo lead recibido</h2>
              <table style="font-size:14px;width:100%;border-collapse:collapse;">
                <tr><td style="padding:8px 12px;font-weight:bold;color:#555;width:120px;">Nombre</td>
                    <td style="padding:8px 12px;">{nombre}</td></tr>
                <tr style="background:#f5f7fa;">
                    <td style="padding:8px 12px;font-weight:bold;color:#555;">Teléfono</td>
                    <td style="padding:8px 12px;">{telefono}</td></tr>
                <tr><td style="padding:8px 12px;font-weight:bold;color:#555;">Proyecto</td>
                    <td style="padding:8px 12px;">{proyecto_nombre}</td></tr>
              </table>
              <p style="font-size:12px;color:#aaa;margin-top:20px;">Generado automáticamente por CRM QueSubsidio.</p>
            </div>"""
            email_from = (get_settings().email_admin or "")
            if not email_from:
                raise RuntimeError("EMAIL_ADMIN no configurada")
            await _gmail_send(
                from_addr=email_from,
                to=[correo_admin],
                subject=f"Nuevo lead — {nombre}",
                html=body_html,
            )
        except Exception as e:
            logger.warning("Notificación email fallida: %s", e)


# ── Landing pública ───────────────────────────────────────────────────────────
@app.get("/")
async def page_home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "active": "inicio",
        "page_title": "QueSubsidio — Subsidios Habitacionales en Chile",
        "page_description": "Comprar tu propiedad con subsidio aún es posible. QueSubsidio acerca la vivienda a más familias chilenas con información clara sobre DS19, DS49 y DS1 T2/T3.",
        "page_path": "/",
        "supabase_needed": True,
    })

@app.get("/como-funciona")
async def page_como_funciona(request: Request):
    return templates.TemplateResponse(request, "como-funciona.html", {
        "active": "como-funciona",
        "page_title": "Cómo funciona — QueSubsidio",
        "page_description": "Conoce el proceso paso a paso para postular a un subsidio habitacional en Chile con la ayuda de QueSubsidio.",
        "page_path": "/como-funciona",
    })

@app.get("/subsidios")
async def page_subsidios(request: Request):
    return templates.TemplateResponse(request, "subsidios.html", {
        "active": "subsidios",
        "page_title": "Subsidios Habitacionales — QueSubsidio",
        "page_description": "Conoce todos los subsidios disponibles: DS19, DS49, DS1 T2/T3 y más. Encuentra el que se ajusta a tu situación.",
        "page_path": "/subsidios",
    })

@app.get("/viviendas")
async def page_viviendas(request: Request):
    return templates.TemplateResponse(request, "viviendas.html", {
        "active": "viviendas",
        "page_title": "Viviendas con Subsidio — QueSubsidio",
        "page_description": "Explora proyectos inmobiliarios disponibles con subsidio habitacional en todo Chile.",
        "page_path": "/viviendas",
    })

@app.get("/blog")
async def page_blog(request: Request):
    return templates.TemplateResponse(request, "blog.html", {
        "active": "blog",
        "page_title": "Blog — QueSubsidio",
        "page_description": "Artículos, guías y novedades sobre subsidios habitacionales y el mercado inmobiliario chileno.",
        "page_path": "/blog",
    })

@app.get("/api/proyectos-landing")
async def api_proyectos_landing(limite: int = 12):
    """Proyectos públicos para la vitrina (sin autenticación): activos, nuevos primero y luego
    semi-nuevos (solo con fotos). Con límite menor al total, los semi-nuevos se muestrean al azar."""
    limite = max(1, min(int(limite), 150))
    now = time.time()
    if not (_cache_proyectos["data"] and now - _cache_proyectos["ts"] < _CACHE_TTL):
        rows = await _supabase_request("GET", "/Proyecto", params={
            "select": "id,nombre,imagen_url,imagenes,ubicacion,activo,condicion_propiedad,"
                      "Tipologia(valor_uf,dormitorios,banos,superficie_util_m2)",
            "order": "nombre.asc",
            "limit": "1000",
        }) or []
        nuevos: List[Dict[str, Any]] = []
        semi: List[Dict[str, Any]] = []
        for r in rows:
            if not r.get("nombre") or r.get("activo") is False:
                continue
            condicion = r.get("condicion_propiedad") or "nueva"
            imagenes = [u for u in (r.get("imagenes") or []) if isinstance(u, str)]
            if r.get("imagen_url") and r["imagen_url"] not in imagenes:
                imagenes.insert(0, r["imagen_url"])
            item = {
                "id":        r.get("id"),
                "nombre":    r.get("nombre") or "Proyecto",
                "imagen":    imagenes[0] if imagenes else None,
                "imagenes":  imagenes,
                "ubicacion": r.get("ubicacion") or "",
                "condicion": condicion,
            }
            if condicion == "nueva":
                nuevos.append(item)
            elif imagenes:
                tip = (r.get("Tipologia") or [None])[0] or {}
                item.update({
                    "precio_uf":   tip.get("valor_uf"),
                    "dormitorios": tip.get("dormitorios"),
                    "banos":       tip.get("banos"),
                    "m2":          tip.get("superficie_util_m2"),
                })
                semi.append(item)
        _cache_proyectos["data"] = {"nuevos": nuevos, "semi": semi}
        _cache_proyectos["ts"] = now

    nuevos = _cache_proyectos["data"]["nuevos"]
    semi = _cache_proyectos["data"]["semi"]
    cupo = max(0, limite - len(nuevos))
    elegidos_semi = semi if cupo >= len(semi) else random.sample(semi, cupo)
    return {"proyectos": (nuevos + elegidos_semi)[:limite], "total": len(nuevos) + len(semi)}

@app.post("/api/contacto")
async def api_contacto(request: Request):
    """Recibe formulario de contacto de la landing y notifica por email."""
    ip = ip_cliente(request)
    if not _rate_limit_ok(f"contacto:{ip}", max_req=5, window=60):
        return Response(content="Demasiadas solicitudes, intenta en un minuto", status_code=429)

    body = await request.json()
    nombre   = (body.get("nombre")   or "").strip()
    correo   = (body.get("correo")   or "").strip()
    telefono = (body.get("telefono") or "").strip()
    empresa  = (body.get("empresa")  or "").strip()
    mensaje  = (body.get("mensaje")  or "").strip()

    if not nombre or not correo:
        return Response(content="nombre y correo son requeridos", status_code=422)

    logger.info("📩 Contacto landing | %s | %s | %s", nombre, correo, empresa)

    destino = get_settings().email_admin
    if destino:
        try:
            cuerpo = f"""<h2>Nuevo contacto desde la landing</h2>
<p><b>Nombre:</b> {nombre}<br>
<b>Correo:</b> {correo}<br>
<b>Teléfono:</b> {telefono or '—'}<br>
<b>Empresa:</b> {empresa or '—'}</p>
<p><b>Mensaje:</b><br>{mensaje or '—'}</p>"""
            await _gmail_send(
                from_addr=(get_settings().email_admin or ""),
                to=[destino],
                subject=f"[QueSubsidio] Contacto desde la landing — {nombre}",
                html=cuerpo,
            )
        except Exception as exc:
            logger.warning("No se pudo enviar email de contacto: %s", exc)

    return {"ok": True}


# ── Formulario de perfilamiento público (sin auth) ───────────────────────────
_FORMULARIO_CAMPOS = (
    "nombre", "email", "telefono", "rsh", "cmf", "tipo_ingreso",
    "antiguedad_meses", "tiene_liquidaciones", "tiene_cotizaciones_afp",
    "anos_declaracion_renta", "tiene_carpeta_tributaria",
    "tiene_boletas_honorarios", "tiene_cert_antiguedad",
    "tiene_propiedad", "numero_integrantes",
    "resultado", "paso_actual", "completado_at",
)

@app.post("/api/formulario")
async def api_crear_formulario(request: Request):
    body = await request.json()
    payload = {k: v for k, v in body.items() if k in _FORMULARIO_CAMPOS and v is not None}
    try:
        row = await _supabase_request(
            "POST", "/Formulario",
            json=payload,
            extra_headers={"Prefer": "return=representation"},
        )
        return row[0] if isinstance(row, list) and row else row
    except Exception as exc:
        logger.warning("Error creando Formulario: %s", exc)
        return Response(content="Error al crear formulario", status_code=500)

@app.get("/api/formulario/{token}")
async def api_obtener_formulario(token: str):
    try:
        rows = await _supabase_request(
            "GET", "/Formulario",
            params={"token": f"eq.{token}", "select": "*"},
        )
        if not rows:
            return Response(content="No encontrado", status_code=404)
        return rows[0]
    except Exception:
        return Response(content="Error", status_code=500)

@app.patch("/api/formulario/{token}")
async def api_actualizar_formulario(token: str, request: Request):
    body = await request.json()
    payload = {k: v for k, v in body.items() if k in _FORMULARIO_CAMPOS}
    if not payload:
        return Response(content="Nada que actualizar", status_code=400)
    try:
        await _supabase_request(
            "PATCH", "/Formulario",
            params={"token": f"eq.{token}"},
            json=payload,
            extra_headers={"Prefer": "return=minimal"},
        )
        return {"ok": True}
    except Exception as exc:
        logger.warning("Error actualizando Formulario %s: %s", token, exc)
        return Response(content="Error al actualizar", status_code=500)


@app.post("/api/formulario/{token}/convertir")
async def api_convertir_formulario(token: str, request: Request):
    """Convierte un Formulario completado en un Cliente CRM."""
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    try:
        rows = await _supabase_request(
            "GET", "/Formulario",
            params={"token": f"eq.{token}", "select": "*"},
        )
        if not rows:
            return Response(content="Formulario no encontrado", status_code=404)
        f = rows[0]

        if f.get("resultado") == "convertido":
            return Response(content="Este formulario ya fue convertido a cliente", status_code=409)

        nombre   = (f.get("nombre") or "").strip()
        telefono = _normalize_phone(f.get("telefono") or "")
        if not nombre:
            return Response(content="El formulario no tiene nombre registrado", status_code=400)

        body: Dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception as e_body:
            _cuerpo_opcional_ilegible(request, "api_convertir_formulario", e_body)

        proyecto_id = body.get("proyecto_id") or f.get("proyecto_id")
        usuario_id  = body.get("usuario_id") or perfil.get("id")

        cliente_payload: Dict[str, Any] = {
            "Contacto":    nombre,
            "Telefono":    telefono or f.get("telefono"),
            "email":       f.get("email"),
            "rsh":         f.get("rsh"),
            "tipo_ingreso": f.get("tipo_ingreso"),
            "proyecto_id": proyecto_id,
            "usuario_id":  usuario_id,
            "primer mensaje": True,
            "wtsp_habilitado": bool(telefono),
        }
        cliente_payload = {k: v for k, v in cliente_payload.items() if v is not None}

        nuevo = await _supabase_request(
            "POST", "/Cliente",
            json=cliente_payload,
            extra_headers={"Prefer": "return=representation"},
        )
        if not nuevo:
            return Response(content="Error al crear cliente", status_code=500)
        cliente    = nuevo[0] if isinstance(nuevo, list) else nuevo
        cliente_id = cliente.get("id")

        if telefono and proyecto_id:
            await upsert_prospecto(
                telefono_e164=telefono,
                nombre=nombre,
                proyecto_id=str(proyecto_id),
                estado="NUEVO",
                paso="INICIO",
                cliente_id=cliente_id,
                numero_integrantes=f.get("numero_integrantes"),
            )

        await _supabase_request(
            "PATCH", "/Formulario",
            params={"token": f"eq.{token}"},
            json={"resultado": "convertido", "completado_at": _utc_now_iso()},
            extra_headers={"Prefer": "return=minimal"},
        )

        return {"ok": True, "cliente_id": cliente_id, "cliente": cliente}
    except Exception as e:
        return respuesta_error(e, contexto="api_convertir_formulario")


@app.post("/api/newsletter")
async def api_newsletter(request: Request):
    """Guarda suscripción al blog/newsletter en Supabase."""
    ip = ip_cliente(request)
    if not _rate_limit_ok(f"newsletter:{ip}", max_req=3, window=60):
        return Response(content="Demasiadas solicitudes, intenta en un minuto", status_code=429)

    body = await request.json()
    email = (body.get("email") or "").strip().lower()

    if not email or "@" not in email:
        return Response(content="email inválido", status_code=422)

    logger.info("📧 Newsletter suscripción: %s", email)

    # Guardar en Supabase (upsert para evitar duplicados)
    try:
        await _supabase_request(
            "POST", "/newsletter",
            json={"email": email},
            extra_headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
        )
    except Exception as exc:
        logger.warning("No se pudo guardar newsletter en Supabase: %s", exc)

    return {"ok": True}


# ── Rutas de páginas (URLs limpias sin .html) ────────────────────────────────
@app.get("/reset-password")
async def page_reset_password():
    return FileResponse("frontend/reset-password.html")

@app.get("/panel")
async def page_panel():
    return FileResponse("frontend/panel.html")

@app.get("/empresas")
async def page_empresas():
    return RedirectResponse(url="/panel", status_code=302)

@app.get("/empresas.html")
async def page_empresas_html():
    return RedirectResponse(url="/panel", status_code=302)

@app.get("/login")
async def page_login():
    return RedirectResponse(url="/", status_code=301)

@app.get("/inmobiliaria")
async def page_inmobiliaria():
    return RedirectResponse(url="/panel", status_code=302)

@app.get("/inmobiliaria.html")
async def page_inmobiliaria_html():
    return RedirectResponse(url="/panel", status_code=302)

@app.get("/proyecto")
async def page_proyecto():
    return RedirectResponse(url="/panel", status_code=302)

@app.get("/proyecto.html")
async def page_proyecto_html():
    return RedirectResponse(url="/panel", status_code=302)

@app.get("/perfil")
async def page_perfil():
    return FileResponse("frontend/opcion_c.html")


# ── API Progreso usuario (requiere JWT de Supabase en Authorization header) ──


@app.get("/api/progreso")
async def api_get_progreso(request: Request):
    uid = await _uid_desde_jwt(request)
    if not uid:
        return Response(content="no autorizado", status_code=401)
    rows = await _supabase_request("GET", "/progreso_usuario",
        params={"user_id": f"eq.{uid}", "select": "mundo,req_id,completado"},
    ) or []
    resultado: Dict[str, List[str]] = {"s": [], "h": []}
    for row in rows:
        if row.get("completado"):
            m = row.get("mundo", "")
            if m in resultado:
                resultado[m].append(row.get("req_id", ""))
    return resultado


@app.post("/api/progreso")
async def api_set_progreso(request: Request):
    uid = await _uid_desde_jwt(request)
    if not uid:
        return Response(content="no autorizado", status_code=401)
    body = await request.json()
    mundo  = (body.get("mundo")  or "").strip()
    req_id = (body.get("req_id") or "").strip()
    completado = bool(body.get("completado", True))
    if not mundo or not req_id:
        return Response(content="mundo y req_id son requeridos", status_code=422)
    await _supabase_request(
        "POST", "/progreso_usuario",
        json={"user_id": uid, "mundo": mundo, "req_id": req_id, "completado": completado},
        extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    return {"ok": True}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=get_settings().host,
        port=get_settings().port,
        reload=True,
    )
