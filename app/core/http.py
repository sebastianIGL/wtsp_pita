"""Cliente HTTP compartido.

Un único ``httpx.AsyncClient`` por proceso: se crea al arrancar (lifespan) y se cierra al
apagar. Antes se creaba uno por llamada (17 sitios), lo que obligaba a abrir una conexión
TCP+TLS nueva en cada petición.

Reglas:
  * Timeouts explícitos (conexión, lectura, escritura y pool). Cada sitio de uso conserva el
    plazo total que tenía antes, vía ``http_client(timeout=N)`` (lectura y escritura = N).
  * El cliente compartido NO lleva cabeceras de autenticación por defecto: cada petición pasa
    las suyas, para que una credencial no pueda filtrarse a otra llamada.
  * Sin reintentos automáticos aquí. Los reintentos (solo GET a Supabase) viven en
    ``_supabase_request``; nada hacia Meta ni Gmail se reintenta.
  * Si el lifespan no corrió (scripts, pruebas) el cliente se crea de forma perezosa.
"""
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx

CONNECT_TIMEOUT = 10.0
POOL_TIMEOUT = 10.0
TIMEOUT_POR_DEFECTO = httpx.Timeout(connect=CONNECT_TIMEOUT, read=20.0, write=20.0, pool=POOL_TIMEOUT)
LIMITES = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)


def timeout(total: float) -> httpx.Timeout:
    """Plazo explícito para una petición: lectura y escritura = total; conexión y pool acotados."""
    return httpx.Timeout(connect=CONNECT_TIMEOUT, read=total, write=total, pool=POOL_TIMEOUT)


_cliente: Optional[httpx.AsyncClient] = None


def _crear() -> httpx.AsyncClient:
    # Sin headers=, sin auth= y sin base_url: nada se hereda entre llamadas.
    return httpx.AsyncClient(timeout=TIMEOUT_POR_DEFECTO, limits=LIMITES, follow_redirects=False)


def cliente_http() -> httpx.AsyncClient:
    global _cliente
    if _cliente is None or _cliente.is_closed:
        _cliente = _crear()
    return _cliente


async def iniciar_http() -> None:
    cliente_http()


async def cerrar_http() -> None:
    global _cliente
    c, _cliente = _cliente, None
    if c is not None and not c.is_closed:
        await c.aclose()


class _VistaConTimeout:
    """Vista del cliente compartido que aplica un plazo por defecto a cada petición.
    No cierra el cliente y no guarda estado propio."""

    def __init__(self, cliente: httpx.AsyncClient, plazo: httpx.Timeout):
        self._c = cliente
        self._t = plazo

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        kw.setdefault("timeout", self._t)
        return await self._c.request(method, url, **kw)

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", url, **kw)

    async def put(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("PUT", url, **kw)

    async def patch(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("PATCH", url, **kw)

    async def delete(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kw)


@asynccontextmanager
async def http_client(total: float):
    """Sustituto directo de ``async with httpx.AsyncClient(timeout=N) as client``: ``http_client(N)``."""
    yield _VistaConTimeout(cliente_http(), timeout(total))
