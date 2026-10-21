"""Archivos (fotos y documentos) que el cliente manda por WhatsApp: se descargan de Meta, se clasifican, se suben a Storage y se
registran en el prospecto, se confirma la recepción con lo que falta, y se avisa al ejecutivo con el primer documento y con los
documentos completos. Un archivo ya procesado (mismo ``wa_media_id``) se ignora.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote D).
"""
import asyncio
import logging
from typing import Dict, Optional

from app.clients.supabase import _supabase_request
from app.clients.supabase_storage import subir_a_storage
from app.clients.whatsapp import descargar_media_whatsapp, send_whatsapp_message
from app.core.errors import registrar_error
from app.services.avisos import _notificar_recordatorio_estado
from app.services.documentos import _CAMPOS_CALIFICACION, _docs_requeridos, clasificar_documento, documentos_pendientes
from app.services.documentos_prospecto import insertar_documento, obtener_documentos_prospecto, tipo_por_texto_desde_envio
from app.services.mensajes import insertar_mensaje
from app.services.prospectos import actualizar_datos_prospecto

logger = logging.getLogger("wtsp_pita")


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
