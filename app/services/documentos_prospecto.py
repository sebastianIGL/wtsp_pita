"""Documentos de un prospecto en la base: guardar y listar, y completar el tipo de una foto sin nombre con lo que el cliente
escribe (antes de registrarla, según la hora de envío de WhatsApp, o después). Va aparte de ``documentos.py``, que son solo
reglas puras sin red.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote C2).
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.clients.supabase import _supabase_request
from app.services.documentos import clasificar_texto_documento


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
