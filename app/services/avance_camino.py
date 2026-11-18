"""Avance de «Mi Camino» guardado en la cuenta de la familia: qué requisitos marcó, y el subsidio, la ruta y el objetivo que eligió.

Solo se guarda eso (nunca las respuestas del cuestionario, ni el RSH, ni el ahorro, ni ingresos). Cada cosa lleva la hora en que cambió (en milisegundos): al sincronizar dos dispositivos gana lo más reciente
de cada requisito, así que no se pisan cambios. Tablas: ``progreso_usuario`` (un registro por requisito) y ``camino_eleccion`` (uno por persona); ver ``supabase/migraciones/2026-11-18_mi_camino_cuenta.sql``.
"""
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.clients.supabase import _supabase_request
from app.services.elegibilidad import OBJETIVOS
from app.services.ficha_proyecto import SLUG_MAX, SLUG_VALIDO

# Los requisitos que existen en la página (mismos identificadores que usa frontend/mi_camino.html): nada más se guarda.
REQ_IDS = {
    "s": ("edad", "nacional", "sin_prop", "sin_sub", "rsh", "ahorro", "puntaje"),
    "h": ("edad", "ingresos", "antigued", "dicom", "capacid", "pie", "cotiz"),
}
RUTAS = ("subsidio", "credito")
MAX_ITEMS = 40
FUTURO_MS = 5 * 60 * 1000          # una hora más de 5 minutos adelante se corrige a «ahora» (relojes desajustados)


def _ms(iso: Any) -> int:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _t(valor: Any, ahora_ms: int) -> Optional[int]:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor != valor or valor < 0:
        return None
    return min(int(valor), ahora_ms + FUTURO_MS)


def validar(cuerpo: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """``{"items": [...], "eleccion": {...} | None}`` limpio, o (None, motivo)."""
    if not isinstance(cuerpo, dict):
        return None, "Faltan los datos."
    ahora = int(time.time() * 1000)
    items: List[Dict[str, Any]] = []
    crudos = cuerpo.get("items") or []
    if not isinstance(crudos, list) or len(crudos) > MAX_ITEMS:
        return None, "Demasiados requisitos."
    for it in crudos:
        if not isinstance(it, dict) or it.get("mundo") not in REQ_IDS or it.get("req_id") not in REQ_IDS[it["mundo"]] or not isinstance(it.get("completado"), bool):
            return None, "Un requisito no es válido."
        t = _t(it.get("t"), ahora)
        if t is None:
            return None, "Una hora no es válida."
        items.append({"mundo": it["mundo"], "req_id": it["req_id"], "completado": it["completado"], "t": t})
    eleccion = None
    e = cuerpo.get("eleccion")
    if e is not None:
        if not isinstance(e, dict):
            return None, "La elección no es válida."
        subsidio = e.get("subsidio") or ""
        if not isinstance(subsidio, str) or (subsidio and (len(subsidio) > SLUG_MAX or not SLUG_VALIDO.match(subsidio))):
            return None, "El subsidio elegido no es válido."
        ruta, objetivo = e.get("ruta"), e.get("objetivo")
        if ruta not in (None,) + RUTAS or objetivo not in (None,) + OBJETIVOS:
            return None, "La ruta u objetivo elegido no es válido."
        t = _t(e.get("t"), ahora)
        if t is None:
            return None, "Una hora no es válida."
        eleccion = {"subsidio": subsidio, "ruta": ruta, "objetivo": objetivo, "t": t}
    return {"items": items, "eleccion": eleccion}, None


async def leer(uid: str) -> Dict[str, Any]:
    filas = await _supabase_request("GET", "/progreso_usuario", params={"user_id": f"eq.{uid}", "select": "mundo,req_id,completado,marcado_en"}) or []
    items = [{"mundo": f.get("mundo"), "req_id": f.get("req_id"), "completado": bool(f.get("completado")), "t": _ms(f.get("marcado_en"))}
             for f in filas if f.get("mundo") in REQ_IDS and f.get("req_id") in REQ_IDS[f["mundo"]]]
    el = await _supabase_request("GET", "/camino_eleccion", params={"user_id": f"eq.{uid}", "select": "subsidio,ruta,objetivo,actualizado_en", "limit": "1"}) or []
    eleccion = {"subsidio": el[0].get("subsidio") or "", "ruta": el[0].get("ruta"), "objetivo": el[0].get("objetivo"), "t": _ms(el[0].get("actualizado_en"))} if el else None
    return {"items": items, "eleccion": eleccion}


async def sincronizar(uid: str, datos: Dict[str, Any]) -> Dict[str, Any]:
    """Guarda solo lo que es más reciente que lo ya guardado y devuelve el estado completo y actualizado."""
    actual = await leer(uid)
    hora = {(i["mundo"], i["req_id"]): i["t"] for i in actual["items"]}
    subir = [{"user_id": uid, "mundo": i["mundo"], "req_id": i["req_id"], "completado": i["completado"], "marcado_en": _iso(i["t"])}
             for i in datos["items"] if i["t"] > hora.get((i["mundo"], i["req_id"]), -1)]
    if subir:
        await _supabase_request("POST", "/progreso_usuario", params={"on_conflict": "user_id,mundo,req_id"}, json=subir,
                                extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    e = datos.get("eleccion")
    if e and e["t"] > ((actual["eleccion"] or {}).get("t") or -1):
        await _supabase_request("POST", "/camino_eleccion", params={"on_conflict": "user_id"},
                                json={"user_id": uid, "subsidio": e["subsidio"], "ruta": e["ruta"], "objetivo": e["objetivo"], "actualizado_en": _iso(e["t"])},
                                extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    return await leer(uid) if (subir or e) else actual


async def borrar(uid: str) -> None:
    """Borra todo el avance guardado de esa persona."""
    await _supabase_request("DELETE", "/progreso_usuario", params={"user_id": f"eq.{uid}"})
    await _supabase_request("DELETE", "/camino_eleccion", params={"user_id": f"eq.{uid}"})
