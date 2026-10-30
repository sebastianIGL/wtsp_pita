"""Bot de WhatsApp (paquete). ``generacion`` arma el prompt del paso actual, llama a Claude y devuelve la respuesta, el siguiente
paso y los datos extraídos; ``contexto`` reúne los datos del proyecto que se le dan al modelo; ``respuesta`` interpreta lo que
contesta el modelo; ``prompts`` guarda las instrucciones por paso (PASOS_CONFIG).

Este archivo solo re-exporta los mismos nombres que tenía el módulo único (app.services.bot).
"""
from app.services.bot.contexto import _construir_mind, _obtener_otros_proyectos, _resumir_proyecto  # noqa: F401
from app.services.bot.generacion import ANTHROPIC_API_KEY, generar_respuesta_ia  # noqa: F401
from app.services.bot.prompts import PASOS_CONFIG  # noqa: F401
from app.services.bot.respuesta import (  # noqa: F401
    MENSAJE_RESPALDO_BOT,
    _RE_PARECE_JSON,
    _RE_RESPUESTA_CAMPO,
    _con_lista_de_documentos,
    _interpretar_respuesta,
)
