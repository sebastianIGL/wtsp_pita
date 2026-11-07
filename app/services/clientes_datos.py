"""Datos de un cliente que usan las vistas «Tabla» y «Pipeline» del panel: de dónde llegó (fuente), por qué canal se le habla (medio), la nota de su recordatorio y el paso automático a
«En evaluación» cuando se envía su evaluación.

Las listas de fuente y medio son cerradas (las mismas que muestra la pantalla: ``FUENTES`` y ``MEDIOS`` de ``frontend/index.html``; una prueba las compara). Lo demás que llegue se rechaza
con un mensaje claro en vez de guardarse.
"""
import logging
from typing import Any, Optional

from app.clients.supabase import _supabase_request

logger = logging.getLogger("wtsp_pita")

FUENTES = ("meta_ads", "chatbot", "formulario_web", "referido", "excel", "manual", "otro")
MEDIOS = ("whatsapp", "chatbot", "correo", "llamada", "presencial")
NOTA_MAX = 300
# Quien ya está más adelante en el proceso no vuelve atrás por enviar de nuevo su evaluación.
ESTADOS_QUE_NO_RETROCEDEN = ("aprobado", "cotizacion_enviada")


class DatoInvalido(ValueError):
    """Un dato que llegó de la pantalla no es válido; el mensaje es el que ve la persona."""


def _de_lista(valor: Any, validos: tuple, mensaje: str) -> Optional[str]:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if not isinstance(valor, str) or valor.strip() not in validos:
        raise DatoInvalido(mensaje)
    return valor.strip()


def fuente(valor: Any) -> Optional[str]:
    """La fuente (de dónde llegó el cliente), ``None`` si viene vacía. Si no es de la lista, ``DatoInvalido``."""
    return _de_lista(valor, FUENTES, "Fuente no válida")


def medio(valor: Any) -> Optional[str]:
    """El medio de contacto, ``None`` si viene vacío. Si no es de la lista, ``DatoInvalido``."""
    return _de_lista(valor, MEDIOS, "Medio no válido")


def nota_recordatorio(valor: Any) -> Optional[str]:
    """La nota de un recordatorio (qué hay que hacer): sin espacios de más, de 1 a 300 caracteres; ``None`` si viene vacía."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise DatoInvalido("La nota del recordatorio no es válida")
    texto = valor.strip()
    if not texto:
        return None
    if len(texto) > NOTA_MAX:
        raise DatoInvalido(f"La nota del recordatorio admite hasta {NOTA_MAX} caracteres")
    return texto


async def marcar_en_evaluacion(cliente_id: int) -> Optional[str]:
    """Pasa al cliente a «En evaluación» (estado de gestión) después de enviar su evaluación, salvo que ya esté más adelante.

    Devuelve el estado en que queda el cliente, o ``None`` si no se pudo saber. Nunca lanza: el correo ya salió y eso no se puede deshacer, así que un fallo aquí solo se anota en el registro.
    """
    try:
        filas = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": "estado_gestion", "limit": "1"}) or []
        if not filas:
            return None
        actual = filas[0].get("estado_gestion")
        if actual == "en_evaluacion" or actual in ESTADOS_QUE_NO_RETROCEDEN:
            return actual
        await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente_id}"}, json={"estado_gestion": "en_evaluacion"}, extra_headers={"Prefer": "return=minimal"})
        return "en_evaluacion"
    except Exception as e:
        logger.warning("Clientes | no se pudo pasar a «En evaluación» tras enviar la evaluación (%s)", type(e).__name__)
        return None
