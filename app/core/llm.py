"""Cliente único de Anthropic.

El SDK trae su propio cliente HTTP (``httpx2``), distinto del ``httpx`` compartido de
``app.core.http``; por eso tiene su propio ciclo de vida y su propio timeout.

  * Se crea una sola vez (de forma perezosa) y se cierra al apagar la aplicación.
  * Timeout explícito por intento (conexión, lectura, escritura y pool).
  * ``max_retries=2`` (el valor por defecto del SDK): una llamada al modelo no tiene efectos
    secundarios, así que reintentarla es seguro. La regla de no reintentar aplica a POST con
    efectos (Meta, Gmail, escrituras), no a esto.
  * Presupuesto total por respuesta (``PRESUPUESTO_TOTAL_S``): el SDK atiende ``Retry-After`` sin
    tope (``min(retry_after, 4_294_967)``), así que los timeouts por intento no bastan para acotar
    el tiempo. Se calcula para que debounce + modelo quede por debajo de los 180 s tras los que
    ``_recovery_loop`` considera pendiente un mensaje y lo reprocesa (si no, la respuesta podría
    enviarse dos veces).
  * El modelo NO se configura aquí: sigue fijo en main.py (pasa a variable de entorno en la Fase 8).

Cuentas (valores por defecto):
    por intento      = conexión 5 s + lectura 35 s                      = 40 s
    3 intentos       = 3 × 40 s + esperas 0,5 s y 1 s (con jitter)      ≈ 121,5 s
    presupuesto      = 125 s (corta también cualquier Retry-After largo)
    debounce         = BOT_REPLY_DELAY = 30 s
    debounce + modelo= 155 s  <  180 s del _recovery_loop (margen 25 s)
"""
from typing import Any, Optional

try:
    import anthropic
except ImportError:                      # igual que main.py: el bot cae a modo eco sin el SDK
    anthropic = None                     # type: ignore[assignment]

TIMEOUT_CONEXION = 5.0
TIMEOUT_LECTURA = 35.0
TIMEOUT_ESCRITURA = 15.0
TIMEOUT_POOL = 10.0
MAX_REINTENTOS = 2                # valor por defecto del SDK, fijado explícitamente
PRESUPUESTO_TOTAL_S = 125.0       # tope de tiempo por respuesta, reintentos incluidos
UMBRAL_RECOVERY_S = 180           # _recovery_loop reprocesa mensajes pendientes con más de 3 min

_cliente: Optional[Any] = None
_clave: Optional[str] = None


def cliente_anthropic(api_key: str) -> Any:
    """Devuelve el cliente único; lo recrea solo si cambió la clave (nunca se registra)."""
    global _cliente, _clave
    if _cliente is None or _clave != api_key:
        _cliente = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=anthropic.Timeout(connect=TIMEOUT_CONEXION, read=TIMEOUT_LECTURA,
                                      write=TIMEOUT_ESCRITURA, pool=TIMEOUT_POOL),
            max_retries=MAX_REINTENTOS,
        )
        _clave = api_key
    return _cliente


async def cerrar_anthropic() -> None:
    global _cliente, _clave
    c, _cliente, _clave = _cliente, None, None
    close = getattr(c, "close", None)
    if close is not None:
        await close()


def margen_frente_a_recovery(bot_reply_delay: float) -> float:
    """Segundos de holgura entre (debounce + presupuesto del modelo) y el umbral del _recovery_loop.
    Negativo = una respuesta lenta podría enviarse dos veces."""
    return UMBRAL_RECOVERY_S - (bot_reply_delay + PRESUPUESTO_TOTAL_S)
