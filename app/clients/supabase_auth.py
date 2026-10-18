"""Autenticación contra Supabase Auth: verificación del token de la persona usuaria.

Movido sin cambios desde main.py (Fase 6, tramo 1b).
  * ``_get_usuario_actual``: valida el JWT con ``/auth/v1/user`` y trae su perfil de ``Usuario``; recuerda el resultado
    30 s en ``app.core.user_cache`` (solo éxitos). El JWT lo verifica Supabase, no se valida localmente.
  * ``_uid_desde_jwt``: lo mismo con la clave anónima, solo para obtener el id (progreso "Mi Camino").
"""
from typing import Dict, Optional

from fastapi import Request

from app.clients.supabase import _supabase_request, _supabase_service_role_key, _supabase_url
from app.core.config import get_settings
from app.core.http import http_client, request_con_reintentos_get
from app.core.user_cache import reducir_perfil, user_cache


async def _get_usuario_actual(request: Request) -> Optional[Dict]:
    """Verifica el JWT de Supabase y retorna el perfil del usuario, o None.

    El resultado EXITOSO se recuerda hasta 30 s (app/core/user_cache.py): clave = hash del token, solo los
    campos necesarios del perfil. Los fallos no se cachean. El JWT lo sigue verificando Supabase."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    en_cache = user_cache.obtener(token)
    if en_cache is not None:
        user_cache.registrar_periodicamente()
        return en_cache
    supa_url = _supabase_url()
    key = _supabase_service_role_key()
    if not supa_url or not key:
        return None
    generacion = user_cache.generacion()   # antes de consultar: si mientras tanto cambia el usuario, no se guarda
    try:
        async with http_client(10) as client:
            r = await request_con_reintentos_get(
                client, "GET", f"{supa_url}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": key},
            )
            if r.status_code != 200:
                return None
            user_id = r.json().get("id")
            if not user_id:
                return None
        perfiles = await _supabase_request(
            "GET", "/Usuario",
            params={"id": f"eq.{user_id}", "select": "*", "limit": "1"},
        )
        if not perfiles:
            return None
        perfil = reducir_perfil(perfiles[0])
        user_cache.guardar(token, user_id, perfil, generacion)
        user_cache.registrar_periodicamente()
        return perfil
    except Exception:
        return None


async def _uid_desde_jwt(request: Request) -> Optional[str]:
    """Extrae el user_id desde el JWT de Supabase en el header Authorization."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        # Verificamos llamando a /auth/v1/user con el token del usuario
        sb_url = _supabase_url()
        if not sb_url:
            return None
        anon_key = get_settings().supabase_anon_key
        if not anon_key:
            return None
        async with http_client(10) as client:
            r = await request_con_reintentos_get(
                client, "GET", f"{sb_url.rstrip('/')}/auth/v1/user",
                headers={"apikey": anon_key, "Authorization": f"Bearer {token}"},
            )
            if r.status_code != 200:
                return None
            return r.json().get("id")
    except Exception:
        return None


async def _invitar_usuario_supabase(correo: str, nombre: str, rol: str) -> str:
    supa_url = _supabase_url()
    key = _supabase_service_role_key()
    async with http_client(20) as client:
        r = await client.post(
            f"{supa_url}/auth/v1/invite",
            headers={"Authorization": f"Bearer {key}", "apikey": key, "Content-Type": "application/json"},
            json={"email": correo, "data": {"nombre": nombre, "rol": rol}, "redirect_to": f"{get_settings().site_url_o_local}/reset-password"},
        )
        r.raise_for_status()
        return r.json()["id"]


async def cambiar_password_supabase(token: str, nueva: str) -> None:
    """Cambia la contraseña de la persona dueña del token en Supabase Auth.

    Extraído de api_cambiar_password (main.py): las tres sentencias de la petición son las mismas; la ruta conserva el
    resto (validación, actualización de ``password_provisional`` e invalidación de la caché de usuario).
    """
    supa_url = _supabase_url()
    key      = _supabase_service_role_key()
    async with http_client(20) as client:
        r = await client.put(
            f"{supa_url}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": key, "Content-Type": "application/json"},
            json={"password": nueva},
        )
        r.raise_for_status()
