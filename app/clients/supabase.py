"""Acceso a Supabase (API REST de PostgREST).

Movido sin cambios desde main.py (Fase 6, tramo 1). Todas las lecturas y escrituras de datos pasan por
``_supabase_request``: usa la service role key (se salta RLS), el cliente HTTP compartido y reintenta solo los GET.
Los nombres se mantienen tal cual para que main.py y los tests sigan usándolos igual.
"""
from typing import Any, Dict, List, Optional, Tuple, Union

from app.core.config import get_settings
from app.core.http import http_client, request_con_reintentos_get


def _supabase_url() -> Optional[str]:
    return get_settings().supabase_url


def _supabase_service_role_key() -> Optional[str]:
    return get_settings().supabase_service_role_key


def _supabase_rest_base() -> str:
    url = _supabase_url()
    if not url:
        raise RuntimeError("Falta SUPABASE_URL")
    return f"{url.rstrip('/')}/rest/v1"


def _supabase_headers() -> dict:
    key = _supabase_service_role_key()
    if not key:
        raise RuntimeError("Falta SUPABASE_SERVICE_ROLE_KEY")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def _supabase_request(
    method: str,
    path: str,
    *,
    params: Optional[Union[Dict[str, str], List[Tuple[str, str]]]] = None,   # lista de pares = mismo filtro repetido
    json: Optional[Union[Dict[str, Any], List[Any]]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
):
    url = f"{_supabase_rest_base()}{path}"
    headers = _supabase_headers()
    if extra_headers:
        headers.update(extra_headers)
    async with http_client(20) as client:
        r = await request_con_reintentos_get(client, method, url, params=params, json=json, headers=headers)
        r.raise_for_status()
        if not r.content:
            return None
        return r.json()
