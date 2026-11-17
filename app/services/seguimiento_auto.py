"""Seguimiento automático de clientes: lo que se pidió para que ningún lead se quede esperando.

APAGADO por defecto (``SEGUIMIENTO_AUTOMATICO``): sin la variable no hace nada ni consulta la base. Encendido, cada 10 minutos y solo de 9 a 19 h (hora de Chile):

  A. «Sin atender más de 24 h»: un cliente que tiene ejecutivo desde hace más de 24 h y sigue «Sin contactar» (sin WhatsApp ni llamada).
     → se avisa por WhatsApp a su ejecutivo Y a administración (administradores y owner con celular) y se deja una nota del sistema;
     → si hay una plantilla «por defecto» configurada (``TEMPLATE_SIN_ATENDER_WA``), se le escribe al cliente (cuenta como su primer WhatsApp). Una sola vez por asignación.
  B. «Remarketing»: un cliente abierto, ya contactado, que lleva más de 7 días sin responder ni volver a cotizar.
     → si hay una plantilla de remarketing configurada (``TEMPLATE_REMARKETING_WA``), se le escribe. No se repite antes de 7 días.

Seguridad: nunca se le escribe a quien pidió la baja de WhatsApp; cada paquete tiene un tope de envíos (para no gastar de golpe ni quemar la calidad del número); el cliente se «reclama» en la base ANTES de
enviar (si el servidor se reinicia a medias, no se repite); un fallo con un cliente no detiene a los demás; solo plantillas de texto (sin encabezado de imagen). Con las plantillas vacías solo se avisa al equipo.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.clients.supabase import _supabase_request
from app.clients.whatsapp import _build_body_params, send_whatsapp_template
from app.core.config import get_settings
from app.core.utilidades import _normalize_phone
from app.services import clientes_datos as cd
from app.services.avisos import _wamid_de
from app.services.baja_whatsapp import esta_de_baja
from app.services.horarios import _dentro_horario_avisos
from app.services.mensajes import insertar_mensaje
from app.services.plantillas import TEMPLATE_VARS_MAP, _pool_plantilla
from app.services.proyectos import obtener_proyecto_por_id
from app.services.prospectos import upsert_prospecto
from app.services.temperatura import SIN_TEMPERATURA

logger = logging.getLogger("wtsp_pita")

INTERVALO_S = 600
INICIO_S = 180
ESPERA_MAX_ERROR_S = 3600
HORAS_SIN_ATENDER = 24
DIAS_REMARKETING = 7
TOPE_ALERTAS = 25               # por ciclo
TOPE_REMARKETING = 20           # por ciclo (≈ 120 por hora como máximo)
PAUSA_ENTRE_ENVIOS_S = 0.5
_TEXTO_ADMIN = "Sin atender más de 24 h (ejecutivo: {ejecutivo})"
_TEXTO_EJECUTIVO = "Lleva más de 24 h sin atender"


def _fecha(v: Any) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


# ── Enviar una plantilla de texto a un cliente ──────────────────────────────────────────────────────

async def _parametros(plantilla: str, cliente: Dict[str, Any]) -> List[Any]:
    """Variables de la plantilla, igual que el botón «Enviar plantilla»: primero la configuración guardada (``PlantillaConfig``), luego el mapa del código; sin ninguna, sin variables."""
    proyecto = await obtener_proyecto_por_id(cliente["proyecto_id"]) if cliente.get("proyecto_id") else None
    pool = await _pool_plantilla(cliente.get("Contacto") or "", proyecto)
    pool.setdefault("cliente_nombre", (cliente.get("Contacto") or "").strip())
    cfg = await _supabase_request("GET", "/PlantillaConfig", params={"template_name": f"eq.{plantilla}", "select": "variables,param_names", "limit": "1"}) or []
    if cfg and cfg[0].get("variables"):
        return _build_body_params(pool, cfg[0]["variables"], cfg[0].get("param_names") or None)
    if plantilla in TEMPLATE_VARS_MAP:
        return [pool.get(k, "") for k in TEMPLATE_VARS_MAP[plantilla]]
    return []


async def enviar_plantilla(cliente: Dict[str, Any], plantilla: str, primer_contacto: bool) -> bool:
    """Escribe al cliente con la plantilla y lo deja registrado (conversación y, si era su primer WhatsApp, su estado). ``True`` si Meta la aceptó. Nunca a quien pidió la baja."""
    telefono = _normalize_phone(cliente.get("Telefono") or "")
    if not telefono or await esta_de_baja(telefono):
        return False
    params = await _parametros(plantilla, cliente)
    wa = await send_whatsapp_template(to=telefono, template_name=plantilla, language_code="es_CL", body_text_params=params)
    ahora = datetime.now(timezone.utc)
    if primer_contacto:
        cambios: Dict[str, Any] = {"primer_wtsp_en": _iso(ahora), "Fecha Ult. Gestión": _iso(ahora), "wamid_plantilla": _wamid_de(wa), "estado_plantilla": "enviado"}
        if (cliente.get("estado_gestion") or None) in cd.ESTADOS_SIN_CONTACTO:
            cambios["estado_gestion"] = "primer_wtsp"
        await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente['id']}"}, json=cambios, extra_headers={"Prefer": "return=minimal"})
    pros = await upsert_prospecto(telefono_e164=telefono, nombre=cliente.get("Contacto"), rut=cliente.get("Rut"), rango_sueldo=cliente.get("Tramo de renta"), proyecto_id=cliente.get("proyecto_id"),
                                  estado="PLANTILLA_ENVIADA", paso="BIENVENIDA", cliente_id=cliente["id"]) if primer_contacto else None
    if pros is None:
        filas = await _supabase_request("GET", "/Prospecto", params={"telefono_e164": f"eq.{telefono}", "select": "id", "limit": "1"}) or []
        pros = filas[0] if filas else None
    if pros and pros.get("id"):
        await insertar_mensaje(prospecto_id=pros["id"], direccion="saliente", text=f"[Plantilla: {plantilla}]", wa_message_id=_wamid_de(wa), cliente_id=cliente["id"])
    return True


# ── A. Sin atender más de 24 h ──────────────────────────────────────────────────────────────────────

async def _administradores() -> List[Dict[str, Any]]:
    filas = await _supabase_request("GET", "/Usuario", params={"rol": "in.(administrador,owner)", "estado": "eq.1", "select": "id,nombre,celular"}) or []
    return [u for u in filas if _normalize_phone(u.get("celular") or "")]


async def _avisar(celular: str, nombre_cliente: str, texto: str, proyecto: str) -> None:
    await send_whatsapp_template(to=_normalize_phone(celular), template_name=get_settings().template_recordatorio_wa, language_code="es_CL", body_text_params=[nombre_cliente, texto, proyecto])


async def alertar_sin_atender(ahora: Optional[datetime] = None) -> int:
    """Avisa (una vez por asignación) de los clientes con ejecutivo que llevan más de 24 h «Sin contactar». Devuelve cuántos alertó."""
    ahora = ahora or datetime.now(timezone.utc)
    limite = ahora - timedelta(hours=HORAS_SIN_ATENDER)
    filas = await _supabase_request("GET", "/Cliente", params=[
        ("usuario_id", "not.is.null"), ("asignado_en", f"lt.{_iso(limite)}"), ("primer_wtsp_en", "is.null"), ("archivado", "not.is.true"),
        ("or", "(estado_gestion.is.null,estado_gestion.eq.sin_contactar)"),
        ("select", "id,Contacto,Rut,Telefono,proyecto_id,usuario_id,asignado_en,alerta_sin_atender_en,estado_gestion,Proyecto(nombre)"), ("order", "asignado_en.asc"), ("limit", "200")]) or []
    pendientes = []
    for c in filas:
        asignado, alerta = _fecha(c.get("asignado_en")), _fecha(c.get("alerta_sin_atender_en"))
        if asignado and (alerta is None or alerta < asignado):             # ya avisado para ESTA asignación: no se repite; si lo reasignaron, vuelve a contar
            pendientes.append(c)
    if not pendientes:
        return 0
    pendientes = pendientes[:TOPE_ALERTAS]
    admins = await _administradores()
    usuarios = {u["id"]: u for u in await _supabase_request("GET", "/Usuario", params={"id": f"in.({','.join(str(c['usuario_id']) for c in pendientes)})", "select": "id,nombre,celular"}) or []}
    plantilla = get_settings().template_sin_atender_wa.strip()
    hechos = 0
    for c in pendientes:
        try:
            await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{c['id']}"}, json={"alerta_sin_atender_en": _iso(ahora)}, extra_headers={"Prefer": "return=minimal"})      # primero se reclama: un reinicio no lo repite
        except Exception as e:
            logger.warning("Seguimiento | no se pudo reclamar al cliente %s (%s): no se avisa", c.get("id"), type(e).__name__)
            continue
        nombre, proyecto = (c.get("Contacto") or "").strip() or "Cliente", (c.get("Proyecto") or {}).get("nombre") or "Sin proyecto"
        ejecutivo = usuarios.get(c["usuario_id"]) or {}
        destinos = [(ejecutivo.get("celular"), _TEXTO_EJECUTIVO)] + [(a.get("celular"), _TEXTO_ADMIN.format(ejecutivo=ejecutivo.get("nombre") or "sin nombre")) for a in admins if a.get("id") != c["usuario_id"]]
        avisados = 0
        for celular, texto in destinos:
            if not celular:
                continue
            try:
                await _avisar(celular, nombre, texto, proyecto)
                avisados += 1
            except Exception as e:
                logger.warning("Seguimiento | no se pudo avisar de un cliente sin atender (%s)", type(e).__name__)
        escrito = False
        if plantilla:
            try:
                escrito = await enviar_plantilla(c, plantilla, primer_contacto=True)
            except Exception as e:
                logger.warning("Seguimiento | no se pudo escribir la plantilla «por defecto» al cliente %s (%s)", c.get("id"), type(e).__name__)
        await cd.nota_del_sistema(c["id"], f"Alerta: llevaba más de 24 h sin atender. Se avisó a {avisados} persona(s) del equipo" + (" y se le envió la plantilla por defecto." if escrito else "."), autor="Sistema")
        hechos += 1
        await asyncio.sleep(PAUSA_ENTRE_ENVIOS_S)
    logger.info("Seguimiento | %d cliente(s) sin atender alertados", hechos)
    return hechos


# ── B. Remarketing a los 7 días ─────────────────────────────────────────────────────────────────────

async def remarketing(ahora: Optional[datetime] = None) -> int:
    """Escribe la plantilla de remarketing a los clientes abiertos y contactados que llevan más de 7 días sin responder ni volver a cotizar. Sin plantilla configurada no hace nada."""
    plantilla = get_settings().template_remarketing_wa.strip()
    if not plantilla:
        return 0
    ahora = ahora or datetime.now(timezone.utc)
    limite = ahora - timedelta(days=DIAS_REMARKETING)
    filas = await _supabase_request("GET", "/Cliente", params=[
        ("primer_wtsp_en", f"lt.{_iso(limite)}"), ("archivado", "not.is.true"), ("Telefono", "not.is.null"),
        ("or", f"(remarketing_en.is.null,remarketing_en.lt.{_iso(limite)})"),
        ("select", "id,Contacto,Rut,Telefono,proyecto_id,estado_gestion,primer_wtsp_en,recotizo_en,remarketing_en,Tramo de renta"), ("order", "primer_wtsp_en.asc"), ("limit", "300")]) or []
    candidatos = [c for c in filas if (c.get("estado_gestion") or "") not in SIN_TEMPERATURA and not ((_fecha(c.get("recotizo_en")) or limite) > limite)]
    if not candidatos:
        return 0
    ids = ",".join(str(c["id"]) for c in candidatos)
    ultimo: Dict[Any, datetime] = {}
    for p in await _supabase_request("GET", "/Prospecto", params={"cliente_id": f"in.({ids})", "select": "cliente_id,ultimo_entrante_en", "limit": "1000"}) or []:
        t = _fecha(p.get("ultimo_entrante_en"))
        if t and (p.get("cliente_id") not in ultimo or t > ultimo[p["cliente_id"]]):
            ultimo[p["cliente_id"]] = t
    por_enviar = [c for c in candidatos if ultimo.get(c["id"], limite) <= limite][:TOPE_REMARKETING]          # quien respondió en la última semana no recibe remarketing
    enviados = 0
    for c in por_enviar:
        try:
            await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{c['id']}"}, json={"remarketing_en": _iso(ahora)}, extra_headers={"Prefer": "return=minimal"})          # se reclama antes de enviar
            if await enviar_plantilla(c, plantilla, primer_contacto=False):
                enviados += 1
                await cd.nota_del_sistema(c["id"], "Remarketing: se le envió la plantilla por llevar más de 7 días sin responder.", autor="Sistema")
        except Exception as e:
            logger.warning("Seguimiento | no se pudo enviar el remarketing al cliente %s (%s)", c.get("id"), type(e).__name__)
        await asyncio.sleep(PAUSA_ENTRE_ENVIOS_S)
    if enviados:
        logger.info("Seguimiento | %d remarketing enviados", enviados)
    return enviados


async def ciclo(ahora: Optional[datetime] = None) -> Dict[str, int]:
    """Un ciclo completo (solo de 9 a 19 h de Chile). Devuelve cuántos alertó y a cuántos les envió remarketing."""
    if not _dentro_horario_avisos(ahora):
        return {"alertas": 0, "remarketing": 0}
    return {"alertas": await alertar_sin_atender(ahora), "remarketing": await remarketing(ahora)}


async def bucle_seguimiento() -> None:
    """Cada 10 minutos hace un ciclo. Con ``SEGUIMIENTO_AUTOMATICO`` apagado no hace nada (ni consulta la base). Si la base falla (por ejemplo, faltan las columnas) lo registra y espera cada vez más, hasta 1 hora."""
    await asyncio.sleep(INICIO_S)
    fallos = 0
    while True:
        espera = INTERVALO_S
        if get_settings().seguimiento_automatico:
            try:
                await ciclo()
                fallos = 0
            except Exception as e:
                fallos += 1
                espera = min(INTERVALO_S * 2 ** fallos, ESPERA_MAX_ERROR_S)
                logger.warning("Seguimiento: no se pudo hacer el ciclo (falla %d, se reintenta en %d s): %s", fallos, espera, type(e).__name__)
        await asyncio.sleep(espera)
