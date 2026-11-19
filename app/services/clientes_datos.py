"""Datos de un cliente que usan las vistas «Tabla» y «Pipeline» del panel: de dónde llegó (fuente), por qué canal se le habla (medio), la nota de su recordatorio y el paso automático a
«En evaluación» cuando se envía su evaluación.

Las listas de fuente, medio y estado de gestión son cerradas (las mismas que muestra la pantalla: ``FUENTES`` y ``MEDIOS`` de ``frontend/index.html``; una prueba las compara). Lo demás que llegue se rechaza
con un mensaje claro en vez de guardarse.
"""
import re
import asyncio
import logging
from typing import Any, Optional

from app.clients.supabase import _supabase_request

logger = logging.getLogger("wtsp_pita")

FUENTES = ("meta_ads", "chatbot", "formulario_web", "referido", "excel", "manual", "otro")
MEDIOS = ("whatsapp", "chatbot", "correo", "llamada", "presencial")
NOTA_MAX = 300
# Quien ya está más adelante en el proceso no vuelve atrás por enviar de nuevo su evaluación.
ESTADOS_QUE_NO_RETROCEDEN = ("aprobado", "cotizacion_enviada")
# «Estado de gestión» de quien aún no fue contactado: nada o «Sin contactar». Al enviarle el primer WhatsApp pasa solo a «Primer WA»; los demás estados los cambia la persona y no se tocan.
ESTADOS_SIN_CONTACTO = (None, "", "sin_contactar")
# Los únicos «Estado de gestión» que se aceptan al guardar (los mismos de ``_EG_MAP`` de ``frontend/index.html``; una prueba los compara). «Sin estado» se guarda como vacío (``None``).
ESTADOS_GESTION = ("sin_contactar", "primer_llamado", "primer_wtsp", "interesado", "sin_rsh", "creando_rsh", "enviando_docs", "por_evaluar", "en_evaluacion", "aprobado",
                   "cotizacion_enviada", "no_interesado", "no_califica", "cierre_perdido", "descartado")
ESTADOS_CERRADOS = ("no_interesado", "no_califica", "cierre_perdido", "descartado")        # los que salen de las listas normales


# Estados que la CONVERSACIÓN de WhatsApp pone sola (solo avanzan, nunca retroceden ni pisan lo que ya decidió una persona o un paso posterior). «Cierre perdido» NUNCA se pone solo.
#   Interesado     → pasó el filtro básico del bot (llegó a las preguntas de calificación)
#   Enviando docs  → empezó a enviar documentos
#   Enviar a evaluar → ya envió todos los documentos
_BAJOS = (None, "", "sin_contactar", "primer_llamado", "primer_wtsp")
_PUEDE_PASAR_A = {
    "interesado": _BAJOS,
    "enviando_docs": _BAJOS + ("interesado", "sin_rsh", "creando_rsh"),
    "por_evaluar": _BAJOS + ("interesado", "sin_rsh", "creando_rsh", "enviando_docs"),
}
_ABIERTOS = _BAJOS + ("interesado", "sin_rsh", "creando_rsh", "enviando_docs", "por_evaluar")
# Cuando el bot detecta que el cliente no califica o no está interesado, lo pasa a ese estado (si sigue «abierto») y deja una nota con el motivo y un aviso al ejecutivo. Lo ya evaluado, aprobado, cotizado o cerrado no se toca.
CIERRE_POR_PASO = {"NO_CALIFICA": "no_califica", "NO_INTERESADO": "no_interesado"}
_ETIQUETA_CIERRE = {"no_califica": "No califica", "no_interesado": "No interesado"}
_PUEDE_PASAR_A.update({"no_califica": _ABIERTOS, "no_interesado": _ABIERTOS})
ESTADO_POR_PASO = {"INICIO": "interesado", "ENTREGA": "interesado", "DOCUMENTACION": "interesado", "ESPERA_DOCS": "enviando_docs", "DOCS_RECIBIDOS": "por_evaluar"}


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


def estado_gestion(valor: Any) -> Optional[str]:
    """El estado de gestión, ``None`` si viene vacío («Sin estado»). Si no es de la lista, ``DatoInvalido``."""
    return _de_lista(valor, ESTADOS_GESTION, "Estado de gestión no válido")


MSG_TIPOLOGIA_AJENA = "La tipología y la etapa elegidas no son de ese proyecto. Elige las del proyecto del cliente."
MSG_TIPOLOGIA_SIN_PROYECTO = "Para elegir una tipología el cliente tiene que tener un proyecto."
MSG_CAMBIO_DE_PROYECTO = "Al cambiar de proyecto elige también la tipología y la etapa de ese proyecto (las que tenía son de otro)."


async def validar_tipologia_de_proyecto(etapa_tipologia_id: Any, proyecto_id: Any, *, cambia_proyecto: bool = False) -> None:
    """Comprueba que la tipología+etapa (``EtapaTipologia``) pertenece al proyecto del cliente. Sin tipología (``None`` o vacío) no hay nada que comprobar.
    ``cambia_proyecto``: el proyecto se está cambiando y la tipología que quedaba es la de antes (el mensaje explica que hay que elegir otra). ``DatoInvalido`` si no calza."""
    if etapa_tipologia_id is None or (isinstance(etapa_tipologia_id, str) and not etapa_tipologia_id.strip()):
        return
    try:
        et = int(etapa_tipologia_id)
    except (TypeError, ValueError):
        raise DatoInvalido("La tipología elegida no es válida")
    if isinstance(etapa_tipologia_id, bool) or et < 1:
        raise DatoInvalido("La tipología elegida no es válida")
    if proyecto_id is None or str(proyecto_id).strip() == "":
        raise DatoInvalido(MSG_TIPOLOGIA_SIN_PROYECTO)
    filas = await _supabase_request("GET", "/EtapaTipologia", params={"id": f"eq.{et}", "select": "id,Tipologia(proyecto_id),Etapa(proyecto_id)", "limit": "1"}) or []
    fila = filas[0] if filas else None
    de_tipologia = str(((fila or {}).get("Tipologia") or {}).get("proyecto_id") or "")
    de_etapa = str(((fila or {}).get("Etapa") or {}).get("proyecto_id") or "")
    if not fila or de_tipologia != str(proyecto_id) or de_etapa != str(proyecto_id):
        raise DatoInvalido(MSG_CAMBIO_DE_PROYECTO if cambia_proyecto else MSG_TIPOLOGIA_AJENA)


def hora_recordatorio(valor: Any) -> Optional[str]:
    """La hora de un recordatorio («14:00», hora de Chile): ``None`` si viene vacía o es «00:00» (sin hora). Lanza ``DatoInvalido`` si no es una hora."""
    if valor is None or valor == "":
        return None
    if not isinstance(valor, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", valor.strip()):
        raise DatoInvalido("La hora del recordatorio no es válida (usa el formato 14:30)")
    hora = valor.strip()
    return None if hora == "00:00" else hora


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


async def marcar_cotizacion_enviada(cliente_id: int) -> Optional[str]:
    """Pasa al cliente a «Cotización enviada» y anota la gestión de hoy después de enviarle una cotización (a cualquier cliente, esté donde esté en el proceso).

    Devuelve ``"cotizacion_enviada"`` o ``None`` si no se pudo cambiar. Nunca lanza: la cotización ya salió y eso no se deshace; un fallo aquí solo se anota en el registro."""
    from datetime import datetime, timezone
    try:
        await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente_id}"},
                                json={"estado_gestion": "cotizacion_enviada", "Fecha Ult. Gestión": datetime.now(timezone.utc).isoformat()},
                                extra_headers={"Prefer": "return=minimal"})
        return "cotizacion_enviada"
    except Exception as e:
        logger.warning("Clientes | no se pudo pasar a «Cotización enviada» tras cotizar (%s)", type(e).__name__)
        return None


async def avanzar_estado_automatico(cliente_id: Optional[int], nuevo: str) -> Optional[str]:
    """Pasa al cliente a ``nuevo`` solo si hoy está en un estado más atrás (ver ``_PUEDE_PASAR_A``). Devuelve el estado nuevo si cambió; ``None`` si no hizo nada. Nunca lanza: es un apoyo de la conversación."""
    if cliente_id is None or nuevo not in _PUEDE_PASAR_A:
        return None
    try:
        filas = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": "estado_gestion", "limit": "1"}) or []
        if not filas or (filas[0].get("estado_gestion") or None) not in _PUEDE_PASAR_A[nuevo]:
            return None
        await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente_id}"}, json={"estado_gestion": nuevo}, extra_headers={"Prefer": "return=minimal"})
        return nuevo
    except Exception as e:
        logger.warning("Estado automático | no se pudo pasar al cliente %s a %s (%s)", cliente_id, nuevo, type(e).__name__)
        return None


async def nota_del_sistema(cliente_id: Optional[int], texto: str, autor: str = "Bot") -> None:
    """Deja una nota interna en el cliente (la ve el equipo). Nunca lanza."""
    if cliente_id is None:
        return
    try:
        await _supabase_request("POST", "/NotaCliente", json={"cliente_id": cliente_id, "autor_nombre": autor, "texto": texto[:1900]}, extra_headers={"Prefer": "return=minimal"})
    except Exception as e:
        logger.warning("Nota del sistema | no se pudo dejar la nota en el cliente %s (%s)", cliente_id, type(e).__name__)


async def cerrar_por_conversacion(cliente_id: Optional[int], paso: str, motivo: Optional[str]) -> Optional[str]:
    """El bot detectó «No califica» o «No interesado»: pasa al cliente a ese estado si sigue abierto, deja una nota con el motivo y avisa al ejecutivo. Devuelve el estado nuevo si cambió. Nunca lanza."""
    nuevo = CIERRE_POR_PASO.get(paso)
    if cliente_id is None or nuevo is None:
        return None
    etiqueta = _ETIQUETA_CIERRE[nuevo]
    motivo = " ".join(str(motivo or "").split())[:300] or "sin motivo registrado"
    try:
        filas = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": "estado_gestion,Contacto", "limit": "1"}) or []
        if not filas:
            return None
        actual, nombre = (filas[0].get("estado_gestion") or None), (filas[0].get("Contacto") or "Cliente")
        cambia = actual in _PUEDE_PASAR_A[nuevo]
        if cambia:
            await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente_id}"}, json={"estado_gestion": nuevo}, extra_headers={"Prefer": "return=minimal"})
        await nota_del_sistema(cliente_id, f"El bot lo clasificó como «{etiqueta}». Motivo: {motivo}." + ("" if cambia else f" (No se cambió su estado porque ya está en «{actual}»: revísalo.)"))
        if cambia:
            from app.services.avisos import _notificar_ejecutivo_wa           # import local: evita un ciclo de módulos
            asyncio.create_task(_notificar_ejecutivo_wa(cliente_id, nombre, f"Quedó como {etiqueta}: {motivo}"[:150]))
        return nuevo if cambia else None
    except Exception as e:
        logger.warning("Estado automático | no se pudo cerrar al cliente %s como %s (%s)", cliente_id, nuevo, type(e).__name__)
        return None
