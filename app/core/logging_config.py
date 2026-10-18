"""Logging estructurado en JSON, con ``request_id`` por petición y datos sensibles enmascarados.

  * Una línea JSON por registro, a stdout (Railway pinta como error todo lo que va a stderr).
  * ``request_id``: viene de ``X-Request-ID`` si es un valor seguro; si no, se genera uno. Se
    devuelve en la cabecera ``X-Request-ID`` de la respuesta y viaja por ``contextvars``: las
    tareas creadas durante la petición (p. ej. el procesamiento del webhook) lo heredan.
  * Se enmascaran tokens, claves, JWT, teléfonos y RUT en el mensaje, en los campos extra y en
    las trazas de excepción. Correos y nombres NO se enmascaran (no estaba pedido).
  * El log de acceso lo emite el middleware con la plantilla de la ruta (``/api/formulario/{token}``),
    nunca con la ruta cruda ni con la query, que pueden llevar tokens o teléfonos.
"""
import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from starlette.datastructures import MutableHeaders

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# ── Enmascarado ──────────────────────────────────────────────────────────────

_VALOR = r"""(?:Bearer\s+)?[^\s"',;&)}\]]+"""
_CLAVES = r"(?:api[_-]?key|apikey|x-api-key|authorization|access[_-]?token|auth[_-]?token|token|secret|password|passwd|service[_-]?role[_-]?key|client[_-]?secret)"

_REGLAS = [
    # clave: valor / clave=valor / "clave": "valor"
    (re.compile(rf"""(?i)(\b{_CLAVES}\b["']?\s*[:=]\s*["']?)({_VALOR})"""), r"\1***"),
    # RUT precedido de la palabra rut
    (re.compile(r"""(?i)(\brut\b["']?\s*[:=]\s*["']?)([0-9.\-kK]+)"""), r"\1***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"), "Bearer ***"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]*"), "***jwt***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "AIza***"),
    # RUT con formato: 12.345.678-9 / 12345678-9 / 12.345.678-K
    (re.compile(r"(?<![\w.])\d{1,2}\.?\d{3}\.?\d{3}-[\dkK](?![\w])"), "***-*"),
]

# Teléfonos: móvil chileno (con o sin +56 / espacios / guiones) y E.164 con "+"
_TELEFONO = re.compile(
    r"(?<![\w])(\+?56[\s-]?9[\s-]?\d{4}[\s-]?\d{4}|\+?56\d{9}|9\d{8}|\+\d{10,15})(?![\w])"
)


def _mascara_telefono(m: "re.Match[str]") -> str:
    digitos = re.sub(r"\D", "", m.group(0))
    return "***" + digitos[-4:]


def enmascarar(texto: Any) -> Any:
    if not isinstance(texto, str) or not texto:
        return texto
    for patron, reemplazo in _REGLAS:
        texto = patron.sub(reemplazo, texto)
    return _TELEFONO.sub(_mascara_telefono, texto)


# ── Formato JSON ─────────────────────────────────────────────────────────────

_ESTANDAR = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        datos: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": enmascarar(record.getMessage()),
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
        }
        for k, v in record.__dict__.items():
            if k not in _ESTANDAR and k != "request_id" and not k.startswith("_"):
                datos[k] = enmascarar(v) if isinstance(v, str) else v
        if record.exc_info:
            datos["exc"] = enmascarar(self.formatException(record.exc_info))
        elif record.exc_text:
            datos["exc"] = enmascarar(record.exc_text)
        return json.dumps(datos, ensure_ascii=False, default=str)


class _StdoutHandler(logging.StreamHandler):
    """Escribe siempre en el sys.stdout vigente (útil cuando algo lo redirige, como pytest)."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


_HANDLER_MARCA = "_wtsp_json"


def configurar_logging(nivel: str = "INFO") -> None:
    """Idempotente: deja un único handler JSON en el logger raíz y reencamina a uvicorn."""
    raiz = logging.getLogger()
    if not any(getattr(h, _HANDLER_MARCA, False) for h in raiz.handlers):
        h = _StdoutHandler()
        h.setFormatter(JsonFormatter())
        setattr(h, _HANDLER_MARCA, True)
        raiz.handlers = [h]
    raiz.setLevel(nivel)
    # uvicorn trae sus propios handlers con otro formato: se reencaminan al raíz (JSON).
    for nombre in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(nombre)
        lg.handlers = []
        lg.propagate = True
    # httpx registra en INFO cada petición saliente con su URL completa (query con teléfonos/tokens)
    for nombre in ("httpx", "httpcore"):
        logging.getLogger(nombre).setLevel(logging.WARNING)
    # el log de acceso lo emite RequestContextMiddleware (con request_id y sin query)
    acceso = logging.getLogger("uvicorn.access")
    acceso.handlers = []
    acceso.propagate = False


# ── Middleware ASGI: request_id + log de acceso ──────────────────────────────

_ID_SEGURO = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
_log = logging.getLogger("wtsp_pita.http")
_SIN_LOG_DE_ACCESO = {"/health"}


def _id_entrante(scope: Dict[str, Any]) -> Optional[str]:
    for nombre, valor in scope.get("headers", []):
        if nombre == b"x-request-id":
            texto = valor.decode("latin-1")
            return texto if _ID_SEGURO.match(texto) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _id_entrante(scope) or uuid.uuid4().hex
        token = request_id_var.set(rid)
        inicio = time.perf_counter()
        estado: Dict[str, Optional[int]] = {"codigo": None}

        async def enviar(mensaje):
            if mensaje["type"] == "http.response.start":
                estado["codigo"] = mensaje["status"]
                MutableHeaders(scope=mensaje)["X-Request-ID"] = rid
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        except Exception:
            _log.exception("Excepción no controlada", extra={"method": scope.get("method"), "path": _ruta(scope)})
            raise
        finally:
            ruta = _ruta(scope)
            if ruta not in _SIN_LOG_DE_ACCESO:
                codigo = estado["codigo"] or 500
                _log.log(logging.ERROR if codigo >= 500 else logging.INFO, "request",
                         extra={"method": scope.get("method"), "path": ruta, "status": codigo,
                                "duration_ms": round((time.perf_counter() - inicio) * 1000, 1)})
            request_id_var.reset(token)


def _ruta(scope: Dict[str, Any]) -> str:
    """Plantilla de la ruta (sin valores de parámetros ni query); la ruta cruda solo si no hay plantilla."""
    ruta = scope.get("route")
    plantilla = getattr(ruta, "path", None)
    return plantilla or scope.get("path", "")
