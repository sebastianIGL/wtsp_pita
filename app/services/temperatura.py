"""Temperatura de cada cliente (0 a 99): cuánto conviene contactarlo AHORA. Mide el interés de los primeros días, no el avance (el avance lo muestran los estados).

Vive mientras el cliente sigue «abierto»: desde que se le asigna hasta que envía todos los documentos («Enviar a evaluar») o se cierra. Después queda sin temperatura.
Se guarda en ``Cliente`` (temperatura_base, temperatura, temperatura_en, temperatura_motivo) para poder ordenar, filtrar y avisar entre páginas: con miles de clientes
por ejecutivo, calcularla en pantalla sería lento. Se recalcula (a) al pasar algo importante con ese cliente y (b) cada 10 minutos para todos (el tiempo también enfría).

Reglas (acordadas con la persona dueña):
  · Señales (suman, hasta 65): contactado +10 · respondió alguna vez +15 · respondió en las últimas 48 h +20 (o +8 si fue en la última semana) ·
    pasó el filtro del bot (le llegó la propuesta) +10 · llamada contestada en la última semana +10.  Así un «60» es un cliente que respondió hace poco y aún no avanzó.
  · Hitos (pisos): interactúa después de recibir la propuesta → desde 70 · envía documentos → desde 85 · pide un ejecutivo → 99 (hasta que alguien le contesta una llamada).
    Sobre el piso suman la frescura (+14 si respondió en 48 h, +6 si en la semana) y una llamada contestada (+5), sin pasar de 84 (o 98 desde el piso de 85).
  · Enfriamiento: sin ninguna interacción por más de 7 días resta 15; por más de 14 días, 30 (nunca baja un piso de 70 de los 40). Tres llamadas sin respuesta restan 10.
  · Sin temperatura (None): estados «Enviar a evaluar» en adelante, cerrados, archivados. Quien pidió la baja de WhatsApp queda en 0.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.clients.supabase import _supabase_request

logger = logging.getLogger("wtsp_pita")

SIN_TEMPERATURA = frozenset({"por_evaluar", "en_evaluacion", "aprobado", "cotizacion_enviada", "no_interesado", "no_califica", "cierre_perdido", "descartado"})
PASOS_CON_PROPUESTA = frozenset({"INICIO", "ENTREGA", "DOCUMENTACION", "ESPERA_DOCS", "DOCS_RECIBIDOS"})
ESTADOS_CON_PROPUESTA = frozenset({"interesado", "sin_rsh", "creando_rsh", "enviando_docs"})
VENTANA_HORAS = 48
RESULTADOS_LLAMADA = ("contesto", "no_contesto", "buzon", "numero_incorrecto")
INTERVALO_S = 600
INICIO_S = 120
ESPERA_MAX_ERROR_S = 3600
LOTE_ACTUALIZAR = 150


def _fecha(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _horas(desde: Optional[datetime], ahora: datetime) -> Optional[float]:
    return None if desde is None else max(0.0, (ahora - desde).total_seconds() / 3600)


def calcular(cliente: Dict[str, Any], prospecto: Optional[Dict[str, Any]], llamadas: Iterable[Dict[str, Any]], ahora: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """``{"base", "temperatura", "motivo"}`` del cliente, o ``None`` si ya no corresponde (sin temperatura). Función pura: no toca la base."""
    ahora = ahora or datetime.now(timezone.utc)
    p = prospecto or {}
    estado = cliente.get("estado_gestion") or ""
    if cliente.get("archivado") or estado in SIN_TEMPERATURA:
        return None
    if p.get("opt_out"):
        return {"base": 0, "temperatura": 0, "motivo": "Pidió no recibir WhatsApp"}

    llamadas = [l for l in llamadas if isinstance(l, dict)]
    ultimo_msg = _fecha(p.get("ultimo_entrante_en"))
    contestadas = sorted((t for t in (_fecha(l.get("creado_en")) for l in llamadas if l.get("resultado") == "contesto") if t), reverse=True)
    sin_respuesta = [t for t in (_fecha(l.get("creado_en")) for l in llamadas if l.get("resultado") in ("no_contesto", "buzon")) if t]
    ult_llamada_ok = contestadas[0] if contestadas else None
    interacciones = [t for t in (ultimo_msg, ult_llamada_ok) if t]
    ult_interaccion = max(interacciones) if interacciones else None
    h_msg, h_int = _horas(ultimo_msg, ahora), _horas(ult_interaccion, ahora)
    h_llamada = _horas(ult_llamada_ok, ahora)

    contactado = bool(cliente.get("primer_wtsp_en") or llamadas or ultimo_msg or estado in ("primer_wtsp", "primer_llamado"))
    respondio = ultimo_msg is not None
    con_propuesta = estado in ESTADOS_CON_PROPUESTA or (p.get("paso") in PASOS_CON_PROPUESTA)
    llamada_reciente = h_llamada is not None and h_llamada <= 24 * 7
    # ¿ya le contestaron al que pidió ejecutivo? Una llamada contestada DESPUÉS de su último mensaje
    atendido = bool(ult_llamada_ok and (ultimo_msg is None or ult_llamada_ok > ultimo_msg))
    pide_ejecutivo = bool(p.get("quiere_contacto_ejecutivo")) and not atendido

    # enfriamiento: el tiempo sin ninguna interacción (si nunca hubo, desde que se le asignó o se creó)
    desde = ult_interaccion or _fecha(cliente.get("asignado_en")) or _fecha(cliente.get("creado_en"))
    dias_frio = (_horas(desde, ahora) or 0) / 24
    enfria = 30 if dias_frio > 14 else 15 if dias_frio > 7 else 0
    if len(sin_respuesta) >= 3 and (ult_interaccion is None or max(sin_respuesta) > ult_interaccion):
        enfria += 10

    if pide_ejecutivo:
        return {"base": 99, "temperatura": 99, "motivo": "Pidió hablar con un ejecutivo"}

    extra = (14 if h_msg is not None and h_msg <= VENTANA_HORAS else 6 if h_msg is not None and h_msg <= 24 * 7 else 0) + (5 if llamada_reciente else 0)
    if estado == "enviando_docs" or p.get("paso") == "ESPERA_DOCS" or atendido and p.get("quiere_contacto_ejecutivo"):
        base, tope = 85, 98
    elif con_propuesta and respondio:
        base, tope = 70, 84
    else:
        base, tope = 0, 65
    if base:
        temp = min(tope, base + extra)
        temp = max(40, temp - enfria) if base == 70 else max(55, temp - enfria)
    else:
        puntos = (10 if contactado else 0) + (15 if respondio else 0) + (20 if h_msg is not None and h_msg <= VENTANA_HORAS else 8 if h_msg is not None and h_msg <= 24 * 7 else 0) \
            + (10 if con_propuesta else 0) + (10 if llamada_reciente else 0)
        temp = max(0, min(tope, puntos) - enfria) if puntos else 0

    return {"base": base, "temperatura": int(max(0, min(99, temp))), "motivo": _motivo(estado, p, base, atendido, contactado, respondio, h_msg, h_int, enfria, sin_respuesta, ult_interaccion)}


def _motivo(estado, p, base, atendido, contactado, respondio, h_msg, h_int, enfria, sin_respuesta, ult_interaccion) -> str:
    if atendido and p.get("quiere_contacto_ejecutivo"):
        return "Ya hablaron: dar seguimiento"
    if estado == "enviando_docs" or p.get("paso") == "ESPERA_DOCS":
        return "Está enviando documentos: ayudarlo a completarlos"
    if estado in ("sin_rsh", "creando_rsh"):
        return "Se trabó en el RSH"
    if enfria >= 15 and respondio:
        return "Se está enfriando: retomar contacto"
    if len(sin_respuesta) >= 3 and (ult_interaccion is None or max(sin_respuesta) > ult_interaccion):
        return "Llamado varias veces sin respuesta"
    if base == 70:
        return "Respondió la propuesta" if h_msg is not None and h_msg <= VENTANA_HORAS else "Recibió la propuesta"
    if respondio:
        return "Respondió hace poco" if h_msg is not None and h_msg <= VENTANA_HORAS else "Respondió hace días"
    return "Contactado, aún sin respuesta" if contactado else "Aún sin contactar"


_COLUMNAS_CLIENTE = "id,estado_gestion,primer_wtsp_en,creado_en,asignado_en,archivado,temperatura,temperatura_base,temperatura_motivo"
_COLUMNAS_PROSPECTO = "cliente_id,ultimo_entrante_en,paso,quiere_contacto_ejecutivo,opt_out"


def _mismo(fila: Dict[str, Any], nuevo: Optional[Dict[str, Any]]) -> bool:
    if nuevo is None:
        return fila.get("temperatura") is None and fila.get("temperatura_base") is None and not fila.get("temperatura_motivo")
    return fila.get("temperatura") == nuevo["temperatura"] and fila.get("temperatura_base") == nuevo["base"] and (fila.get("temperatura_motivo") or "") == nuevo["motivo"]


def _cambio(nuevo: Optional[Dict[str, Any]], ahora: datetime) -> Dict[str, Any]:
    return {"temperatura": None, "temperatura_base": None, "temperatura_motivo": None, "temperatura_en": ahora.isoformat()} if nuevo is None else \
        {"temperatura": nuevo["temperatura"], "temperatura_base": nuevo["base"], "temperatura_motivo": nuevo["motivo"], "temperatura_en": ahora.isoformat()}


async def recalcular_cliente(cliente_id: Any) -> Optional[Dict[str, Any]]:
    """Recalcula y guarda la temperatura de UN cliente (se llama al pasar algo importante con él). Nunca lanza: si algo falla, lo registra y la conversación sigue."""
    try:
        filas = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": _COLUMNAS_CLIENTE, "limit": "1"}) or []
        if not filas:
            return None
        pros = await _supabase_request("GET", "/Prospecto", params={"cliente_id": f"eq.{cliente_id}", "select": _COLUMNAS_PROSPECTO, "order": "ultimo_entrante_en.desc.nullslast", "limit": "1"}) or []
        llamadas = await _supabase_request("GET", "/LlamadaCliente", params={"cliente_id": f"eq.{cliente_id}", "select": "resultado,creado_en", "order": "creado_en.desc", "limit": "30"}) or []
        ahora = datetime.now(timezone.utc)
        nuevo = calcular(filas[0], pros[0] if pros else None, llamadas, ahora)
        if not _mismo(filas[0], nuevo):
            await _supabase_request("PATCH", "/Cliente", params={"id": f"eq.{cliente_id}"}, json=_cambio(nuevo, ahora))
        return nuevo
    except Exception as e:
        logger.warning("Temperatura: no se pudo recalcular el cliente %s: %s", cliente_id, e)
        return None


async def recalcular_todos() -> Dict[str, int]:
    """Recalcula la temperatura de todos los clientes y escribe SOLO los que cambiaron (agrupados por valor: pocas escrituras). Devuelve un conteo."""
    from app.services.clientes_vistas import leer_en_paginas
    ahora = datetime.now(timezone.utc)
    clientes = await leer_en_paginas("/Cliente", {"select": _COLUMNAS_CLIENTE, "order": "id.asc"}, maximo=100_000)
    prospectos = await leer_en_paginas("/Prospecto", {"select": "id," + _COLUMNAS_PROSPECTO, "cliente_id": "not.is.null", "order": "id.asc"}, maximo=100_000)
    desde = (ahora - timedelta(days=30)).isoformat()
    llamadas = await leer_en_paginas("/LlamadaCliente", {"select": "id,cliente_id,resultado,creado_en", "creado_en": f"gte.{desde}", "order": "id.asc"}, maximo=200_000)
    por_cliente_p: Dict[Any, Dict[str, Any]] = {}
    for p in prospectos:                                   # si un cliente tuviera varios prospectos, manda el que escribió más recientemente
        previo = por_cliente_p.get(p.get("cliente_id"))
        if previo is None or str(p.get("ultimo_entrante_en") or "") > str(previo.get("ultimo_entrante_en") or ""):
            por_cliente_p[p.get("cliente_id")] = p
    por_cliente_l: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for l in llamadas:
        por_cliente_l[l.get("cliente_id")].append(l)
    grupos: Dict[Any, List[Any]] = defaultdict(list)
    sin_cambio = 0
    for c in clientes:
        nuevo = calcular(c, por_cliente_p.get(c["id"]), por_cliente_l.get(c["id"], []), ahora)
        if _mismo(c, nuevo):
            sin_cambio += 1
            continue
        grupos[(None, None, None) if nuevo is None else (nuevo["temperatura"], nuevo["base"], nuevo["motivo"])].append(c["id"])
    cambiados = 0
    for (temp, base, motivo), ids in grupos.items():
        nuevo = None if temp is None else {"temperatura": temp, "base": base, "motivo": motivo}
        for i in range(0, len(ids), LOTE_ACTUALIZAR):
            lote = ids[i:i + LOTE_ACTUALIZAR]
            await _supabase_request("PATCH", "/Cliente", params={"id": f"in.({','.join(str(x) for x in lote)})"}, json=_cambio(nuevo, ahora))
            cambiados += len(lote)
    return {"clientes": len(clientes), "cambiados": cambiados, "sin_cambio": sin_cambio}


async def bucle_temperatura() -> None:
    """Cada 10 minutos recalcula la temperatura de todos (el tiempo también enfría). Si la base falla (por ejemplo, faltan las columnas) lo registra y espera cada vez más, hasta 1 hora."""
    await asyncio.sleep(INICIO_S)
    fallos = 0
    while True:
        espera = INTERVALO_S
        try:
            r = await recalcular_todos()
            fallos = 0
            logger.info("Temperatura: %d clientes, %d actualizados", r["clientes"], r["cambiados"])
        except Exception as e:
            fallos += 1
            espera = min(INTERVALO_S * 2 ** fallos, ESPERA_MAX_ERROR_S)
            logger.warning("Temperatura: no se pudo recalcular (falla %d, se reintenta en %d s): %s", fallos, espera, e)
        await asyncio.sleep(espera)
