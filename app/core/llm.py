"""Cliente único de Anthropic.

El SDK trae su propio cliente HTTP (``httpx2``), distinto del ``httpx`` compartido de
``app.core.http``; por eso tiene su propio ciclo de vida y su propio timeout.

  * Se crea una sola vez (de forma perezosa) y se cierra al apagar la aplicación.
  * Timeout explícito (conexión, lectura, escritura y pool).
  * ``max_retries=0``: nada que sea POST se reintenta automáticamente. Si el modelo falla, el
    mensaje queda con ``pendiente_respuesta`` y lo recoge ``_recovery_loop``.
  * El modelo NO se configura aquí: sigue fijo en main.py (pasa a variable de entorno en la Fase 8).
"""
from typing import Any, Optional

try:
    import anthropic
except ImportError:                      # igual que main.py: el bot cae a modo eco sin el SDK
    anthropic = None                     # type: ignore[assignment]

TIMEOUT_CONEXION = 5.0
TIMEOUT_LECTURA = 60.0
TIMEOUT_ESCRITURA = 15.0
TIMEOUT_POOL = 10.0

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
            max_retries=0,
        )
        _clave = api_key
    return _cliente


async def cerrar_anthropic() -> None:
    global _cliente, _clave
    c, _cliente, _clave = _cliente, None, None
    close = getattr(c, "close", None)
    if close is not None:
        await close()
