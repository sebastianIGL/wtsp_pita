"""Manejo central de errores (Fase 3).

Dos clases de error, tratadas distinto:
  * De negocio (``AppError`` o cualquier ``ValueError`` capturado en un endpoint): el mensaje es para la
    persona usuaria ("El campo Ahorro (UF) es requerido…") y llega tal cual, en texto plano, con el mismo
    código de estado que ya tenía el endpoint.
  * Todo lo demás (errores de Supabase/Meta/Gmail, bugs, desconocidos): al navegador solo llega
    "Ocurrió un error. Código: A1B2C3". El detalle completo (estado y cuerpo del error remoto, traza) va al
    log con ese mismo código en el campo ``error_code`` —buscable— junto al ``request_id``.

``EXPOSE_ERROR_DETAIL`` (apagada por defecto, solo para desarrollo) añade el detalle a la respuesta.
Los errores no capturados por ningún endpoint los resuelve ``RequestContextMiddleware`` con este mismo
mecanismo; allí solo ``AppError`` pasa tal cual (un ``ValueError`` suelto no se tratará como mensaje de
negocio: nadie lo capturó a propósito).
"""
import logging
import secrets
from typing import Optional

import httpx
from starlette.responses import PlainTextResponse, Response

from app.core.config import get_settings

logger = logging.getLogger("wtsp_pita")

MENSAJE_GENERICO = "Ocurrió un error. Código: {codigo}"
_MAX_DETALLE = 600


class AppError(ValueError):
    """Error de negocio: su mensaje se muestra a la persona usuaria. Hereda de ValueError para que los
    ``raise ValueError("…")`` de negocio que ya existen sigan funcionando igual."""

    def __init__(self, mensaje: str, status_code: int = 500):
        super().__init__(mensaje)
        self.status_code = status_code


def generar_codigo() -> str:
    """6 caracteres hexadecimales en mayúscula (16,7 millones de combinaciones)."""
    return secrets.token_hex(3).upper()


def detalle_error(e: Exception) -> str:
    """Resumen del error para el log: estado y cuerpo (recortado) del error remoto, o el texto de la excepción."""
    if isinstance(e, httpx.HTTPStatusError):
        status = getattr(e.response, "status_code", "?")
        try:
            body = e.response.text
        except Exception:
            body = ""
        body = (body or "").strip().replace("\n", " ")
        if len(body) > _MAX_DETALLE:
            body = body[:_MAX_DETALLE] + "…"
        return f"Upstream HTTP {status}: {body}" if body else f"Upstream HTTP {status}"
    return str(e)


def es_de_negocio(e: BaseException) -> bool:
    return isinstance(e, ValueError)


def registrar_error(e: BaseException, contexto: str, mensaje: str = "Error", nivel: int = logging.ERROR) -> str:
    """Registra el error con un código nuevo y lo devuelve."""
    codigo = generar_codigo()
    extra = {"error_code": codigo, "contexto": contexto, "tipo_error": type(e).__name__,
             "detalle": detalle_error(e)}  # type: ignore[arg-type]
    if isinstance(e, httpx.HTTPStatusError):
        extra["status_remoto"] = getattr(e.response, "status_code", None)
    logger.log(nivel, "%s [%s] en %s", mensaje, codigo, contexto, exc_info=(type(e), e, e.__traceback__), extra=extra)
    return codigo


def mensaje_publico(e: BaseException, contexto: str, *, solo_apperror: bool = False) -> str:
    """Texto que puede ver quien llama. Registra el detalle si no es un error de negocio."""
    negocio = isinstance(e, AppError) if solo_apperror else es_de_negocio(e)
    if negocio:
        logger.info("Error de negocio en %s: %s", contexto, e, extra={"contexto": contexto, "tipo_error": type(e).__name__})
        return str(e)
    texto = MENSAJE_GENERICO.format(codigo=registrar_error(e, contexto))
    if get_settings().expose_error_detail:
        texto += f"\n\nDetalle (solo desarrollo): {detalle_error(e)}"
    return texto


def respuesta_error(e: BaseException, contexto: str, status_code: int = 500) -> Response:
    """Respuesta de error de un endpoint: texto plano, con el código de estado que el endpoint ya usaba."""
    if isinstance(e, AppError):
        status_code = e.status_code
    return PlainTextResponse(mensaje_publico(e, contexto), status_code=status_code)


def respuesta_no_controlada(e: BaseException, contexto: str) -> Response:
    """Para el middleware: un error que ningún endpoint capturó."""
    status = e.status_code if isinstance(e, AppError) else 500
    texto = mensaje_publico(e, contexto, solo_apperror=True)
    return PlainTextResponse(texto, status_code=status)
