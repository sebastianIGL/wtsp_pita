"""Cuerpo JSON opcional de algunos endpoints: sin cuerpo no es un error; si vino un cuerpo y no se pudo leer, se avisa en el
log y se sigue sin él. Lo usan ``api_convertir_formulario`` y ``api_enviar_evaluacion``.

Movido sin cambios desde main.py (Fase 6, tramo 3).
"""
import logging

from fastapi import Request

logger = logging.getLogger("wtsp_pita")


def _cuerpo_opcional_ilegible(request: Request, contexto: str, e: Exception) -> None:
    """El cuerpo JSON de estos endpoints es opcional: sin cuerpo no es un error. Si vino un cuerpo y no se
    pudo leer, se avisa en el log (y se sigue sin él, como siempre)."""
    if request.headers.get("content-length") not in (None, "0"):
        logger.warning("%s: el cuerpo no es JSON válido y se ignora (%s)", contexto, type(e).__name__,
                       extra={"contexto": contexto})
