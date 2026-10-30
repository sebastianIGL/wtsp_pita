"""Lo que el bot le entrega al cliente a partir de lo que contestó el modelo: ``_interpretar_respuesta`` y el mensaje de respaldo.

Movido sin cambios desde app/services/bot (Fase 8).
"""
import json
import logging
import re
from typing import Any, Dict

logger = logging.getLogger("wtsp_pita")


# Mensaje que recibe el cliente cuando la respuesta del modelo no se puede usar. No promete que alguien lo contactará: no se avisa a nadie.
MENSAJE_RESPALDO_BOT = "Disculpa, tuve un problema para procesar tu mensaje. ¿Podrías escribirlo de nuevo? 🙏"

_RE_RESPUESTA_CAMPO = re.compile(r'"respuesta"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)
_RE_PARECE_JSON = re.compile(r'"(?:respuesta|siguiente_paso|datos_extraidos)"|"\s*:|[{}\[\]]')


def _con_lista_de_documentos(texto: str, lista_documentos_requeridos: str) -> str:
    # Reemplazo estático: garantiza que la lista de documentos que llega al cliente sea exactamente la que valida el backend,
    # sin que el modelo pueda parafrasear nombres, cambiar cantidades o inventar documentos.
    return texto.replace("<<LISTA_DOCUMENTOS>>", lista_documentos_requeridos) if "<<LISTA_DOCUMENTOS>>" in texto else texto


def _interpretar_respuesta(texto_modelo: str, lista_documentos_requeridos: str) -> Dict[str, Any]:
    """Convierte lo que contestó el modelo en {respuesta, siguiente_paso, datos_extraidos}. ``texto_modelo`` es lo que viene DESPUÉS del
    prefill «{» (el código lo antepone). El cliente nunca recibe sintaxis JSON ni texto técnico:
      1. JSON válido con ``respuesta`` → se usa tal cual.
      2. JSON roto pero con el campo ``respuesta`` completo → se envía solo esa frase.
      3. Texto libre sin estructura JSON → se envía sin la «{» del prefill.
      4. Cualquier otra cosa → MENSAJE_RESPALDO_BOT y un error en el log.
    En 2, 3 y 4 no se avanza de paso ni se guardan datos."""
    original = "{" + texto_modelo.strip()
    raw = original
    # Limpiar si viene con markdown igual
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        result = json.loads(raw)
    except Exception:
        result = None
    if isinstance(result, dict) and result.get("respuesta"):
        return {
            "respuesta":       _con_lista_de_documentos(str(result["respuesta"]), lista_documentos_requeridos),
            "siguiente_paso":  result.get("siguiente_paso") or None,
            "datos_extraidos": result.get("datos_extraidos") or {},
        }

    sin_datos: Dict[str, Any] = {"siguiente_paso": None, "datos_extraidos": {}}
    rescatada = _RE_RESPUESTA_CAMPO.search(original)
    if rescatada:
        try:
            frase = json.loads('"' + rescatada.group(1) + '"').strip()
        except Exception:
            frase = ""
        if frase:
            logger.warning("Claude no devolvió JSON válido: se rescató el campo respuesta")
            return {"respuesta": _con_lista_de_documentos(frase, lista_documentos_requeridos), **sin_datos}

    libre = original[1:].strip()
    if libre and not _RE_PARECE_JSON.search(libre) and libre[0] not in '"`':
        logger.warning("Claude respondió texto libre sin JSON: se envía sin el prefill")
        return {"respuesta": _con_lista_de_documentos(libre, lista_documentos_requeridos), **sin_datos}

    logger.error("Claude no devolvió una respuesta utilizable: se envía el mensaje de respaldo",
                 extra={"largo_respuesta_modelo": len(texto_modelo)})
    return {"respuesta": MENSAJE_RESPALDO_BOT, **sin_datos}
