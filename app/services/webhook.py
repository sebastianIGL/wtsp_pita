"""Procesamiento de un mensaje de WhatsApp en segundo plano (después de devolver 200 a Meta): filtra números de ejecutivos, crea o
actualiza el prospecto, guarda el mensaje ANTES de esperar, espera ``DELAY_RESPUESTA_SEGUNDOS`` (debounce: si el cliente escribe de
nuevo, la ruta cancela esta tarea y responde solo al último), arma el contexto, llama al bot, envía la respuesta y actualiza el paso
y los datos. ``_procesar_grupo`` atiende varios mensajes del mismo número de un lote: guarda todos en orden y responde el último.

Si el servidor se reinicia durante la espera, el mensaje ya quedó guardado con ``pendiente_respuesta=true`` y lo retoma
``_recovery_loop``. Movido sin cambios desde main.py (Fase 6, tramo 2, lote D).
"""
import asyncio
import logging
from typing import Dict, List, Optional

from app.clients.supabase import _supabase_request, _supabase_service_role_key, _supabase_url
from app.clients.whatsapp import send_whatsapp_message
from app.core.config import get_settings
from app.core.errors import registrar_error
from app.core.utilidades import _normalize_phone, _safe_httpx_error
from app.services.avisos import _notificar_ejecutivo_wa
from app.services.bot import _obtener_otros_proyectos, generar_respuesta_ia
from app.services.documentos_prospecto import etiquetar_documentos_sin_tipo, obtener_documentos_prospecto
from app.services.horarios import _dentro_horario_bot
from app.services.mensajes import insertar_mensaje, obtener_historial_mensajes
from app.services.prospectos import (
    _cambiar_proyecto_prospecto,
    actualizar_datos_prospecto,
    obtener_prospecto_por_id,
    upsert_prospecto,
)
from app.services.proyectos import obtener_proyecto_por_id
from app.services.webhook_entrada import _lanzar
from app.services.webhook_media import _procesar_media

logger = logging.getLogger("wtsp_pita")


# Segundos de espera antes de que el bot responda (para sensación más humana)
DELAY_RESPUESTA_SEGUNDOS = get_settings().bot_reply_delay


async def _procesar_webhook(msg: Dict, es_reintento: bool = False, responder: bool = True):
    """Procesa un mensaje de WhatsApp en background (después de devolver 200 a Meta).

    es_reintento=True cuando lo invoca _recovery_loop: el mensaje entrante ya
    quedó guardado en el primer intento, así que NO debe volver a insertarse
    (evita duplicar el mensaje del cliente en el historial en cada reintento)."""
    try:
        from_number = _normalize_phone(msg["from"])
        msg_type    = msg.get("type", "text")

        texto_preview = ""
        if msg_type == "text":
            texto_preview = (msg.get("text", {}).get("body") or "")[:80]
        elif msg_type in ("image", "audio", "video", "document"):
            texto_preview = f"[{msg_type}]"
        logger.info("📨 Mensaje entrante | %s | tipo: %s | %s", from_number, msg_type, texto_preview)

        # ── Filtrar números de ejecutivos CRM ────────────────────────────
        _usr_crm = await _supabase_request("GET", "/Usuario",
            params={"celular": f"eq.{from_number}", "select": "id", "limit": "1"}) or []
        if _usr_crm:
            await send_whatsapp_message(
                to=from_number,
                text="Este número es exclusivo para notificaciones del CRM QueSubsidio. Gestiona tus clientes desde la plataforma en línea.",
            )
            return

        # ── Obtener o crear prospecto de inmediato ─────────────────────────
        prospecto = None
        proyecto  = None

        if _supabase_url() and _supabase_service_role_key():
            prospecto = await upsert_prospecto(
                telefono_e164=from_number,
                estado="RESPONDIO",
            )
            if prospecto and prospecto.get("proyecto_id"):
                proyecto = await obtener_proyecto_por_id(prospecto["proyecto_id"])

        prospecto_id = (prospecto or {}).get("id")
        cliente_id_prospecto: Optional[int] = (prospecto or {}).get("cliente_id")

        # ── Manejo de DOCUMENTOS (imagen o archivo) ───────────────────────
        if msg_type in ("image", "document"):
            # En su propia tarea y protegida: si el mismo número escribe otro mensaje mientras se descarga, el debounce
            # cancela esta tarea y la foto/documento se perdería a medias. Así la descarga termina igual.
            await asyncio.shield(_lanzar(_procesar_media(msg, msg_type, from_number, prospecto, prospecto_id)))
            return

        # ── Manejo de TEXTO ───────────────────────────────────────────────
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            return

        # Guardar mensaje entrante ANTES del debounce (queda en historial aunque llegue otro)
        # — se omite en reintentos del recovery loop: el mensaje ya se guardó la primera vez.
        if prospecto_id and not es_reintento:
            nuevo = await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="entrante",
                text=text,
                wa_message_id=msg.get("id"),
                cliente_id=cliente_id_prospecto,
            )
            if not nuevo:
                # Duplicado detectado por la base (índice único): sobrevive a reinicios y a varias réplicas.
                logger.info("Mensaje duplicado ignorado (ya estaba guardado): %s", msg.get("id"),
                            extra={"wamid": msg.get("id")})
                return
            await upsert_prospecto(
                telefono_e164=from_number,
                ultimo_texto_entrante=text,
            )

        # Mensajes anteriores de un lote del mismo número: solo se guardan; responde el último del lote.
        if not responder:
            return

        # Fuera del horario del bot (HORARIO_BOT_INICIO/FIN; por defecto 24/7, así que normalmente no frena):
        # no responder ahora. El mensaje ya quedó guardado con pendiente_respuesta=true;
        # _recovery_loop lo retoma solo cuando se abra el horario.
        if not _dentro_horario_bot():
            logger.info("Fuera de horario hábil, respuesta pospuesta para %s", from_number)
            return

        # Debounce: esperar a que el cliente termine de escribir
        await asyncio.sleep(DELAY_RESPUESTA_SEGUNDOS)

        historial = []
        docs_recibidos = []

        if prospecto_id:
            # Durante la espera pudo cambiar el estado (p. ej. se registró un archivo y el paso avanzó): se responde con
            # el estado de ahora, no con el que se leyó al llegar el mensaje.
            try:
                prospecto = await obtener_prospecto_por_id(prospecto_id) or prospecto
            except Exception as e_fresco:
                registrar_error(e_fresco, "_procesar_webhook: releer el prospecto después de la espera",
                                mensaje="No se pudo releer el prospecto; el bot responde con el estado de cuando llegó el mensaje",
                                nivel=logging.WARNING)
            try:
                await etiquetar_documentos_sin_tipo(prospecto_id)
            except Exception as e_etiq:
                registrar_error(e_etiq, "_procesar_webhook: tipo de archivos según lo que escribe el cliente",
                                mensaje="No se pudo asignar tipo a los archivos sin clasificar; el bot sigue sin ese dato",
                                nivel=logging.WARNING)
            try:
                historial = await obtener_historial_mensajes(prospecto_id)
                docs_recibidos = await obtener_documentos_prospecto(prospecto_id)
            except Exception as e_ctx:
                registrar_error(e_ctx, "_procesar_webhook: historial y documentos",
                                mensaje="No se pudo cargar el historial/documentos; el bot responde sin ese contexto",
                                nivel=logging.WARNING)

        # Cargar otros proyectos de la misma empresa para contexto del bot
        otros_proyectos: List[Dict] = []
        if proyecto:
            empresa_id_ctx = ((proyecto.get("Inmobiliaria") or {}).get("empresa_id"))
            if empresa_id_ctx:
                try:
                    otros_proyectos = await _obtener_otros_proyectos(empresa_id_ctx, proyecto.get("id"))
                except Exception as e_ctx:
                    registrar_error(e_ctx, "_procesar_webhook: otros proyectos de la empresa",
                                    mensaje="No se pudo cargar otros proyectos; el bot responde sin ese contexto",
                                    nivel=logging.WARNING)

        resultado = await generar_respuesta_ia(
            prospecto=prospecto or {},
            proyecto=proyecto,
            historial=historial,
            mensaje_actual=text,
            docs_recibidos=docs_recibidos,
            otros_proyectos=otros_proyectos,
        )

        reply_text      = (resultado["respuesta"] or "").strip()
        siguiente_paso  = resultado["siguiente_paso"]
        datos_extraidos = resultado["datos_extraidos"]

        if not reply_text:
            logger.warning("reply_text vacío para %s, omitiendo envío", from_number)
            return

        await send_whatsapp_message(to=from_number, text=reply_text)

        # Cambio de proyecto confirmado por el cliente
        nuevo_proyecto_id = datos_extraidos.pop("nuevo_proyecto_id", None)
        if nuevo_proyecto_id and prospecto_id:
            try:
                await _cambiar_proyecto_prospecto(prospecto_id, nuevo_proyecto_id, cliente_id_prospecto)
                logger.info("Proyecto actualizado → %s para prospecto %s", nuevo_proyecto_id, prospecto_id)
            except Exception:
                logger.exception("Error al cambiar proyecto del prospecto %s", prospecto_id)

        if prospecto_id:
            await insertar_mensaje(
                prospecto_id=prospecto_id,
                direccion="saliente",
                text=reply_text,
                cliente_id=cliente_id_prospecto,
            )
            # Bot respondió exitosamente → limpiar flag
            await _supabase_request("PATCH", "/Prospecto",
                params={"id": f"eq.{prospecto_id}"},
                json={"pendiente_respuesta": False})
            if datos_extraidos or siguiente_paso:
                paso_actual_prospecto = (prospecto or {}).get("paso") or "BIENVENIDA"

                # FASE 5.1 — guardar el paso de origen cuando el cliente se marca NO_INTERESADO
                paso_origen = paso_actual_prospecto if siguiente_paso == "NO_INTERESADO" else None

                # FASE 5.2 — calcular motivo_no_califica cuando transiciona a NO_CALIFICA
                if siguiente_paso == "NO_CALIFICA":
                    tiene_prop = datos_extraidos.get("tiene_propiedad") or (prospecto or {}).get("tiene_propiedad")
                    sub_prev   = datos_extraidos.get("subsidio_previo")  or (prospecto or {}).get("subsidio_previo")
                    if tiene_prop and sub_prev:
                        datos_extraidos["motivo_no_califica"] = "ambos"
                    elif tiene_prop:
                        datos_extraidos["motivo_no_califica"] = "tiene_propiedad"
                    elif sub_prev:
                        datos_extraidos["motivo_no_califica"] = "subsidio_previo"

                await actualizar_datos_prospecto(
                    prospecto_id,
                    datos_extraidos,
                    siguiente_paso,
                    paso_origen=paso_origen,
                )

                # Notificar ejecutivo si el cliente pide contacto humano
                if datos_extraidos.get("quiere_contacto_ejecutivo") and cliente_id_prospecto:
                    nombre_p = (prospecto or {}).get("nombre") or from_number
                    asyncio.create_task(_notificar_ejecutivo_wa(
                        cliente_id=cliente_id_prospecto,
                        nombre_cliente=nombre_p,
                        accion="necesita_ayuda",
                    ))

    except Exception as e:
        logger.exception("Error procesando webhook: %s", _safe_httpx_error(e))


async def _procesar_grupo(mensajes: List[Dict]) -> None:
    """Varios mensajes del mismo número en un mismo lote: se guardan todos en orden y responde el último."""
    for msg in mensajes[:-1]:
        await _procesar_webhook(msg, responder=False)
    await _procesar_webhook(mensajes[-1])
