"""Prospectos (los contactos que escriben por WhatsApp): crear o actualizar por teléfono, guardar los datos que el bot extrae de
la conversación y el paso en que va, leer uno por id y cambiarle el proyecto. Al pasar a DOCUMENTACION avisa al admin.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote C2).
"""
import asyncio
from typing import Any, Dict, Optional

from app.clients.supabase import _supabase_request
from app.core.utilidades import _utc_now_iso
from app.services.avisos import _notificar_interesado
from app.services.documentos import _CAMPOS_CALIFICACION


# Campos que tienen columna propia en Prospecto (no van al blob datos)
_CAMPOS_COLUMNA_PROPIA: set = {
    *_CAMPOS_CALIFICACION,
    "motivo_no_interesado", "fecha_tentativa_recontacto", "opt_out",
    "paso_origen_no_interesado",
    "motivo_no_califica", "quiere_contacto_ejecutivo", "intencion_regularizar",
    "renta_mensual", "numero_integrantes", "integrantes_rsh",
}


async def upsert_prospecto(
    *,
    telefono_e164: str,
    nombre: Optional[str] = None,
    rut: Optional[str] = None,
    rango_sueldo: Optional[str] = None,
    numero_integrantes: Optional[int] = None,
    proyecto_id: Optional[str] = None,
    estado: Optional[str] = None,
    paso: Optional[str] = None,
    ultimo_texto_entrante: Optional[str] = None,
    datos: Optional[Dict] = None,
    cliente_id: Optional[int] = None,
):
    row: Dict[str, Any] = {
        "telefono_e164": telefono_e164,
        "actualizado_en": _utc_now_iso(),
    }
    if nombre is not None:
        row["nombre"] = nombre
    if rut is not None:
        row["rut"] = rut
    if rango_sueldo is not None:
        row["rango_sueldo"] = rango_sueldo
    if numero_integrantes is not None:
        row["numero_integrantes"] = numero_integrantes
    if proyecto_id is not None:
        row["proyecto_id"] = proyecto_id
    if estado is not None:
        row["estado"] = estado
    if paso is not None:
        row["paso"] = paso
    if ultimo_texto_entrante is not None:
        row["ultimo_texto_entrante"] = ultimo_texto_entrante
        row["ultimo_entrante_en"]    = _utc_now_iso()
        row["pendiente_respuesta"]   = True   # el bot aún no ha respondido
    if datos is not None:
        row["datos"] = datos
    if cliente_id is not None:
        row["cliente_id"] = cliente_id

    data = await _supabase_request(
        "POST",
        "/Prospecto",
        params={"on_conflict": "telefono_e164"},
        json=row,
        extra_headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    )
    if not data:
        return None
    return data[0]


async def actualizar_datos_prospecto(
    prospecto_id: str,
    nuevos_datos: Dict,
    siguiente_paso: Optional[str] = None,
    paso_origen: Optional[str] = None,
):
    rows = await _supabase_request(
        "GET",
        "/Prospecto",
        params={"id": f"eq.{prospecto_id}", "select": "datos,paso,opt_out"},
    )
    if not rows:
        return

    campos_columna: Dict[str, Any] = {}
    campos_blob:    Dict[str, Any] = {}

    for k, v in nuevos_datos.items():
        if v is None:
            continue
        if k in _CAMPOS_COLUMNA_PROPIA:
            # opt_out: jamás se puede pasar de true a false automáticamente
            if k == "opt_out" and rows[0].get("opt_out") and not v:
                continue
            campos_columna[k] = v
        else:
            campos_blob[k] = v

    merged_blob = {**(rows[0].get("datos") or {}), **campos_blob}

    update: Dict[str, Any] = {
        "datos": merged_blob,
        "actualizado_en": _utc_now_iso(),
        **campos_columna,
    }
    if siguiente_paso:
        update["paso"]   = siguiente_paso
        update["estado"] = siguiente_paso
    if paso_origen:
        update["paso_origen_no_interesado"] = paso_origen

    await _supabase_request(
        "PATCH",
        "/Prospecto",
        params={"id": f"eq.{prospecto_id}"},
        json=update,
    )

    if siguiente_paso == "DOCUMENTACION":
        asyncio.create_task(_notificar_interesado(prospecto_id))


async def obtener_prospecto_por_id(prospecto_id: str) -> Optional[Dict]:
    filas = await _supabase_request("GET", "/Prospecto", params={"id": f"eq.{prospecto_id}", "select": "*", "limit": "1"})
    return filas[0] if filas else None


async def _cambiar_proyecto_prospecto(
    prospecto_id: str, nuevo_proyecto_id: str, cliente_id: Optional[int]
) -> None:
    """Actualiza proyecto_id en Prospecto y en Cliente cuando el cliente confirma cambio."""
    await _supabase_request("PATCH", "/Prospecto",
        params={"id": f"eq.{prospecto_id}"},
        json={"proyecto_id": nuevo_proyecto_id})
    if cliente_id:
        await _supabase_request("PATCH", "/Cliente",
            params={"id": f"eq.{cliente_id}"},
            json={"proyecto_id": nuevo_proyecto_id})
