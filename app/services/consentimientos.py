"""Constancia de los consentimientos de privacidad que las personas dan en los formularios públicos (evaluación, contacto y newsletter).

Los formularios exigen la casilla marcada (el servidor rechaza con 422 si falta) y, con la persona identificada por su correo, se guarda
en la tabla ConsentimientoPrivacidad qué aceptó, cuándo y con qué versión de la política. Guardar la constancia es de «mejor esfuerzo»: si la
tabla aún no existe o la base falla, se registra un aviso (sin el correo) y el formulario igual funciona: la persona ya dio su consentimiento y
no debe perder su solicitud por un fallo interno.
"""
import logging
from typing import Optional

from app.clients.supabase import _supabase_request
from app.services import legal

logger = logging.getLogger("wtsp_pita")

ORIGENES = ("formulario", "contacto", "newsletter")       # los tres que acepta la tabla


async def registrar_consentimiento(origen: str, correo: Optional[str] = None, formulario_token: Optional[str] = None) -> bool:
    """Guarda la constancia. Devuelve True si quedó guardada y False si no (nunca lanza)."""
    if origen not in ORIGENES:
        logger.warning("Consentimiento: origen desconocido %r", origen)
        return False
    try:
        await _supabase_request(
            "POST", "/ConsentimientoPrivacidad",
            json={"origen": origen, "correo": (correo or "").strip().lower() or None, "formulario_token": formulario_token or None,
                  "politica_version": legal.VERSION_POLITICA},
            extra_headers={"Prefer": "return=minimal"})
        return True
    except Exception as e:
        logger.warning("Consentimiento (%s): no se pudo guardar la constancia (%s)", origen, type(e).__name__)
        return False
