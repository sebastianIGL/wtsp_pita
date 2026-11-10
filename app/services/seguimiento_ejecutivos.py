"""Seguimiento de cada ejecutivo para el panel de administración (pestaña «Usuarios»): cuántos leads tiene, cuántos sin contactar, con seguimiento vencido, etc.

Solo lectura y solo para administración. Las etapas se calculan con las mismas reglas del CRM (``etapaDe`` / ``computeFunnelGroup`` en frontend/index.html), para que un número
del panel y el mismo número del CRM digan lo mismo. Se recuerda 60 segundos: la pestaña no vuelve a leer miles de clientes cada vez que se abre.
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.clients.supabase import _supabase_request
from app.services.clientes_vistas import leer_en_paginas

logger = logging.getLogger("wtsp_pita")

TTL_SEGUNDOS = 60
MAX_FILAS = 50_000
_AVANZADOS = {"cotizacion_enviada", "aprobado", "en_evaluacion", "no_interesado", "descartado"}
_CONTACTADOS = {"primer_wtsp", "interesado", "enviando_docs"}
_CERRADOS = {"no_interesado", "descartado"}
_cache: Dict[str, Any] = {"ts": 0.0, "datos": None}


def _ms(valor: Any) -> Optional[float]:
    if not valor:
        return None
    try:
        t = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def grupo_de_embudo(c: Dict[str, Any], p: Dict[str, Any]) -> str:
    """Misma regla que ``computeFunnelGroup`` del CRM (``c`` = el cliente, ``p`` = su conversación)."""
    paso = p.get("paso") or ""
    if paso == "DOCS_RECIBIDOS":
        return "docs_completos"
    if paso in ("ESPERA_DOCS", "DOCUMENTACION"):
        return "enviando_docs"
    if p.get("pendiente_respuesta"):
        return "pendientes"
    wtsp, ult = _ms(c.get("primer_wtsp_en")), _ms(p.get("ultimo_entrante_en"))
    if ult and (not wtsp or ult > wtsp):
        return "respondieron"
    if c.get("estado_plantilla") == "fallido":
        return "no_recibieron"
    if not wtsp:
        return "sin_enviar"
    return "no_respondieron"


def etapa_de(c: Dict[str, Any], p: Dict[str, Any]) -> str:
    """Misma regla que ``etapaDe`` del CRM: nuevo, contactado o el estado de gestión avanzado."""
    eg = c.get("estado_gestion") or ""
    if eg in _AVANZADOS:
        return eg
    if eg in _CONTACTADOS:
        return "contactado"
    return "nuevo" if grupo_de_embudo(c, p) == "sin_enviar" else "contactado"


def _vacio() -> Dict[str, int]:
    return {"leads": 0, "cerrados": 0, "sin_contactar": 0, "pendientes_respuesta": 0, "seguimiento_vencido": 0, "para_hoy": 0,
            "en_evaluacion": 0, "cotizados": 0, "aprobados": 0, "nuevos_7d": 0, "notas_7d": 0, "cotizaciones_30d": 0}


async def _contar_por_usuario(ruta: str, desde: datetime, extra: Optional[Dict[str, str]] = None) -> Optional[Dict[str, int]]:
    """Cuántas filas de esa tabla creó cada persona desde ``desde``. ``None`` si no se pudo leer (p. ej. la tabla aún no existe): es un dato de apoyo."""
    try:
        filas = await leer_en_paginas(ruta, {"select": "id,usuario_id", "creado_en": f"gte.{desde.isoformat()}", "order": "id.asc", **(extra or {})}, maximo=MAX_FILAS)
    except Exception as e:
        logger.warning("Seguimiento | no se pudo leer %s (%s)", ruta, type(e).__name__)
        return None
    cuenta: Dict[str, int] = {}
    for f in filas:
        if f.get("usuario_id"):
            cuenta[str(f["usuario_id"])] = cuenta.get(str(f["usuario_id"]), 0) + 1
    return cuenta


async def calcular(ahora: Optional[datetime] = None) -> Dict[str, Any]:
    ahora = ahora or datetime.now(timezone.utc)
    hoy: date = ahora.astimezone(ZoneInfo("America/Santiago")).date()
    hoy_txt = hoy.isoformat()
    hace_7d = (ahora - timedelta(days=7)).timestamp()
    clientes = await leer_en_paginas("/Cliente", {"select": "id,usuario_id,estado_gestion,recordatorio_at,primer_wtsp_en,estado_plantilla,creado_en",
                                                  "archivado": "not.is.true", "order": "id.asc"}, maximo=MAX_FILAS)
    prospectos = await leer_en_paginas("/Prospecto", {"select": "id,cliente_id,ultimo_entrante_en,pendiente_respuesta,paso", "cliente_id": "not.is.null", "order": "id.asc"}, maximo=MAX_FILAS)
    conversacion = {int(p["cliente_id"]): p for p in prospectos if p.get("cliente_id") is not None}
    por_usuario: Dict[str, Dict[str, int]] = {}
    sin_asignar = 0
    for c in clientes:
        uid = c.get("usuario_id")
        if not uid:
            sin_asignar += 1
            continue
        m = por_usuario.setdefault(str(uid), _vacio())
        p = conversacion.get(int(c["id"]), {})
        etapa = etapa_de(c, p)
        if etapa in _CERRADOS:
            m["cerrados"] += 1
            continue
        m["leads"] += 1
        m["sin_contactar"] += etapa == "nuevo"
        m["pendientes_respuesta"] += bool(p.get("pendiente_respuesta"))
        m["en_evaluacion"] += etapa == "en_evaluacion"
        m["cotizados"] += etapa == "cotizacion_enviada"
        m["aprobados"] += etapa == "aprobado"
        fecha = str(c.get("recordatorio_at") or "")[:10]
        if len(fecha) == 10:
            m["seguimiento_vencido"] += fecha < hoy_txt
            m["para_hoy"] += fecha == hoy_txt
        creado = _ms(c.get("creado_en"))
        m["nuevos_7d"] += bool(creado and creado >= hace_7d)
    notas = await _contar_por_usuario("/NotaCliente", ahora - timedelta(days=7))
    cotizaciones = await _contar_por_usuario("/Cotizacion", ahora - timedelta(days=30), {"estado": "eq.enviada"})
    for uid, n in (notas or {}).items():
        por_usuario.setdefault(uid, _vacio())["notas_7d"] = n
    for uid, n in (cotizaciones or {}).items():
        por_usuario.setdefault(uid, _vacio())["cotizaciones_30d"] = n
    return {"generado_en": ahora.isoformat(), "total_leads": sum(m["leads"] for m in por_usuario.values()), "sin_asignar": sin_asignar,
            "notas_disponibles": notas is not None, "cotizaciones_disponibles": cotizaciones is not None, "usuarios": por_usuario}


async def seguimiento(refrescar: bool = False) -> Dict[str, Any]:
    if not refrescar and _cache["datos"] is not None and time.monotonic() - _cache["ts"] < TTL_SEGUNDOS:
        return _cache["datos"]
    datos = await calcular()
    _cache.update(ts=time.monotonic(), datos=datos)
    return datos
