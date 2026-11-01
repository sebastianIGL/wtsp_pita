"""Cola persistente de avisos al ejecutivo asignado (tabla ``AvisoEjecutivo`` y función ``reclamar_avisos`` de la base).

Es la alternativa a la espera en memoria de ``avisos.py`` (``asyncio.sleep`` hasta que abre el horario, que se pierde si el
servidor se reinicia): el aviso se guarda con la hora desde la que se puede enviar y un proceso lo envía cuando llega.
Solo se usa con ``AVISOS_PERSISTENTES`` encendido (apagado por defecto) y después de haber ejecutado la migración.

Garantías:
  * un aviso igual (mismo cliente y tipo) mientras otro espera o se está enviando se descarta: lo asegura un índice único
    parcial de la base (``aviso_unico_activo``), no este código;
  * cada aviso lo reclama un solo proceso (``FOR UPDATE SKIP LOCKED`` dentro de ``reclamar_avisos``), aunque convivan el
    contenedor viejo y el nuevo durante un despliegue;
  * si el proceso muere con el aviso reclamado, a los ``LEASE_MINUTOS`` vuelve a la cola (la función lo hace al reclamar);
  * un fallo de red, de Meta o de la base se reintenta con espera creciente y, al tercer intento, queda en ``error``;
  * lo que hoy devuelve ``{"ok": False, "motivo": ...}`` (sin ejecutivo, sin celular, ya notificado…) queda ``omitido``.

Limitación conocida: si el proceso muere justo después de que Meta aceptó el mensaje y antes de marcarlo ``enviado``, el aviso
puede salir una segunda vez al volver a la cola. Se prefiere repetir a perder un aviso de un cliente.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.clients.supabase import _supabase_request
from app.core.utilidades import _es_violacion_unica
from app.services import avisos
from app.services.horarios import (
    _dentro_horario_avisos,
    _dentro_horario_urgente,
    _segundos_hasta_horario_avisos,
    _segundos_hasta_horario_urgente,
)

logger = logging.getLogger("wtsp_pita")

TIPOS = ("necesita_ayuda", "primer_documento", "docs_completos")     # los tres que acepta la tabla
MAX_INTENTOS = 3                      # el tercer fallo deja el aviso en «error»
ESPERA_REINTENTO_MIN = (5, 10)        # espera tras el 1.er y el 2.º fallo
LEASE_MINUTOS = 10                    # un aviso «enviando» más antiguo que esto vuelve a la cola
LOTE = 10                             # avisos que se reclaman por ciclo


def _enviar_desde(tipo: str, ahora: Optional[datetime] = None) -> datetime:
    """Primer momento en que se puede enviar: ahora si estamos dentro del horario del tipo, y si no la próxima apertura
    (08:00 para los urgentes, 09:00 para los de documentos, hora de Chile; los mismos horarios de siempre)."""
    ahora = ahora or datetime.now(timezone.utc)
    if tipo == "necesita_ayuda":
        dentro, espera = _dentro_horario_urgente(ahora), _segundos_hasta_horario_urgente
    else:
        dentro, espera = _dentro_horario_avisos(ahora), _segundos_hasta_horario_avisos
    return ahora if dentro else ahora + timedelta(seconds=espera())


async def encolar_aviso(cliente_id: int, tipo: str, nombre_cliente: str) -> Dict[str, Any]:
    """Guarda un aviso en la cola. Devuelve {"ok": True, "encolado": True/False}: False si ya había uno igual esperando
    o enviándose. Las demás fallas (la base caída, la tabla inexistente) suben a quien llama."""
    if tipo not in TIPOS:
        return {"ok": False, "motivo": f"tipo '{tipo}' inválido"}
    desde = _enviar_desde(tipo)
    try:
        await _supabase_request("POST", "/AvisoEjecutivo",
            json={"cliente_id": cliente_id, "tipo": tipo, "nombre_cliente": nombre_cliente, "enviar_desde": desde.isoformat()},
            extra_headers={"Prefer": "return=minimal"})
    except httpx.HTTPStatusError as e:
        if _es_violacion_unica(e):
            return {"ok": True, "encolado": False, "motivo": "ya hay un aviso igual esperando o enviándose"}
        raise
    return {"ok": True, "encolado": True, "enviar_desde": desde.isoformat()}


async def _reclamar(max_avisos: int) -> List[Dict[str, Any]]:
    return await _supabase_request("POST", "/rpc/reclamar_avisos",
        json={"max_avisos": max_avisos, "lease_minutos": LEASE_MINUTOS}) or []


async def _cerrar(fila: Dict[str, Any], campos: Dict[str, Any]) -> None:
    """Deja el aviso en su estado final (o de vuelta en la cola). Solo si sigue «enviando»: si otro proceso ya lo retomó
    tras vencer el plazo, no se le pisa."""
    await _supabase_request("PATCH", "/AvisoEjecutivo",
        params={"id": f"eq.{fila['id']}", "estado": "eq.enviando"},
        json=campos, extra_headers={"Prefer": "return=minimal"})


async def _fallo(fila: Dict[str, Any], motivo: str) -> None:
    """Reintenta con espera creciente; al llegar a MAX_INTENTOS queda en «error»."""
    intentos = int(fila.get("intentos") or 1)          # la función de la base ya contó este intento
    motivo = (motivo or "error desconocido")[:300]
    if intentos >= MAX_INTENTOS:
        logger.warning("Aviso %s (cliente=%s, tipo=%s): se rinde tras %d intentos: %s",
                       fila.get("id"), fila.get("cliente_id"), fila.get("tipo"), intentos, motivo)
        await _cerrar(fila, {"estado": "error", "motivo": motivo})
        return
    espera = ESPERA_REINTENTO_MIN[min(intentos, len(ESPERA_REINTENTO_MIN)) - 1]
    logger.warning("Aviso %s (cliente=%s, tipo=%s): intento %d fallido, se reintenta en %d min: %s",
                   fila.get("id"), fila.get("cliente_id"), fila.get("tipo"), intentos, espera, motivo)
    await _cerrar(fila, {"estado": "pendiente", "motivo": motivo, "reclamado_en": None,
                         "enviar_desde": (datetime.now(timezone.utc) + timedelta(minutes=espera)).isoformat()})


async def _enviar_uno(fila: Dict[str, Any]) -> str:
    """Envía un aviso ya reclamado con la misma lógica de siempre (avisos.py) y lo deja en su estado.
    Devuelve 'enviado', 'omitido' o 'reintento'."""
    tipo, cliente_id = fila["tipo"], fila["cliente_id"]
    nombre = fila.get("nombre_cliente") or ""
    try:
        if tipo == "necesita_ayuda":
            resultado, wamid = await avisos._enviar_aviso_urgente(cliente_id, nombre, "necesita_ayuda")
        else:
            resultado, wamid = await avisos._enviar_recordatorio(cliente_id, nombre, tipo, avisos._TEXTOS_RECORDATORIO[tipo])
    except Exception as e:
        await _fallo(fila, str(e))
        return "reintento"
    if resultado.get("ok"):
        await _cerrar(fila, {"estado": "enviado", "enviado_en": datetime.now(timezone.utc).isoformat(), "wamid": wamid, "motivo": None})
        return "enviado"
    await _cerrar(fila, {"estado": "omitido", "motivo": resultado.get("motivo")})
    logger.info("Aviso %s (cliente=%s, tipo=%s) omitido: %s", fila.get("id"), cliente_id, tipo, resultado.get("motivo"))
    return "omitido"


async def procesar_avisos(max_avisos: int = LOTE) -> int:
    """Reclama los avisos que ya pueden salir y los envía uno por uno. Devuelve cuántos reclamó. Un aviso que falla no
    detiene a los demás. Si la base falla al reclamar, la excepción sube (el bucle lo registra y espera)."""
    filas = await _reclamar(max_avisos)
    for fila in filas:
        try:
            await _enviar_uno(fila)
        except Exception:
            logger.exception("Aviso %s: no se pudo dejar registrado su estado", fila.get("id"))
    return len(filas)


async def avisar(cliente_id: int, tipo: str, nombre_cliente: str) -> Dict[str, Any]:
    """Punto de entrada de los avisos con la cola encendida: guarda el aviso y, si ya se puede enviar, lo envía enseguida
    (sin esperar al ciclo del bucle). Si guardarlo falla, la excepción sube para que avisos.py use la espera en memoria."""
    resultado = await encolar_aviso(cliente_id, tipo, nombre_cliente)
    if resultado.get("encolado"):
        try:
            await procesar_avisos()
        except Exception:
            logger.exception("Avisos: no se pudo procesar la cola tras guardar el aviso (el bucle lo reintentará)")
    return resultado

