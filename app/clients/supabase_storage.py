"""Acceso a Supabase Storage (documentos de clientes).

Movido sin cambios desde main.py (Fase 6, tramo 1b). Usa la service role key (se salta las políticas del bucket)
y el cliente HTTP compartido; nada se reintenta.
"""
import re
import unicodedata

from app.clients.supabase import _supabase_service_role_key, _supabase_url
from app.core.http import http_client


async def subir_a_storage(
    file_bytes: bytes,
    nombre_archivo: str,
    prospecto_id: str,
    mime_type: str,
) -> str:
    """
    Sube un archivo al bucket documentos-clientes en Supabase Storage.
    Retorna la URL pública (firmada).
    """
    supa_url = _supabase_url()
    key = _supabase_service_role_key()
    if not supa_url or not key:
        raise RuntimeError("Falta configuración de Supabase")

    nombre_seguro = unicodedata.normalize("NFKD", nombre_archivo)
    nombre_seguro = nombre_seguro.encode("ascii", "ignore").decode("ascii")
    nombre_seguro = re.sub(r"[^\w.\-]", "_", nombre_seguro)
    path = f"{prospecto_id}/{nombre_seguro}"
    upload_url = f"{supa_url.rstrip('/')}/storage/v1/object/documentos-clientes/{path}"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": mime_type,
    }

    async with http_client(60) as client:
        r = await client.post(upload_url, headers=headers, content=file_bytes)
        r.raise_for_status()

    # URL de acceso (requiere service role para acceder, bucket privado)
    return f"{supa_url.rstrip('/')}/storage/v1/object/documentos-clientes/{path}"


async def _descargar_documento_storage(url_storage: str) -> bytes:
    key = _supabase_service_role_key()
    async with http_client(60) as client:
        r = await client.get(url_storage, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
    return r.content


async def _eliminar_documento_storage(url_storage: str) -> None:
    key = _supabase_service_role_key()
    async with http_client(30) as client:
        r = await client.delete(url_storage, headers={"Authorization": f"Bearer {key}"})
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()


class StorageError(RuntimeError):
    """Storage respondió con un estado distinto de 200/201 al subir un archivo."""


async def subir_imagen_proyecto(path: str, contenido: bytes, mime: str) -> str:
    """Sube la imagen de un proyecto al bucket público proyectos-imagenes y devuelve su URL pública.

    Extraído de api_subir_imagen_proyecto (main.py): el código de la petición es el mismo; la única diferencia es
    que un estado distinto de 200/201 ahora se lanza como StorageError y la ruta lo convierte en la misma respuesta.
    """
    supa_url = _supabase_url()
    key      = _supabase_service_role_key()
    upload_url = f"{supa_url.rstrip('/')}/storage/v1/object/proyectos-imagenes/{path}"

    async with http_client(60) as client:
        r = await client.post(upload_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": mime,
                     "x-upsert": "true"},
            content=contenido)
        if r.status_code not in (200, 201):
            raise StorageError(f"Storage respondió {r.status_code}: {r.text[:600]}")

    return f"{supa_url.rstrip('/')}/storage/v1/object/public/proyectos-imagenes/{path}"
