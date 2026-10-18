"""Utilidades puras (sin estado ni red): teléfonos, nombres, columnas de CSV, identificadores de WhatsApp, fechas.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). main.py las re-exporta con los mismos nombres.
"""
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from app.core.errors import detalle_error


def _horas_desde(iso: Optional[str]) -> Optional[float]:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(iso))).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def _safe_httpx_error(e: Exception) -> str:
    """Resumen del error para el log o para diagnóstico de administradores (nunca para el navegador:
    para eso está respuesta_error / mensaje_publico en app/core/errors.py)."""
    return detalle_error(e)


def _normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "").strip() if ch.isdigit())
    # Celular chileno sin código de país (9 dígitos, empieza con 9) — anteponer 56
    # para que calce con el formato que siempre manda WhatsApp (telefono_e164).
    if len(digits) == 9 and digits.startswith("9"):
        digits = "56" + digits
    return digits


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalizar_wamid(valor: Any) -> Optional[str]:
    """Identificador de WhatsApp tal como debe guardarse: sin espacios sobrantes y, si no hay uno real
    (None, vacío o solo espacios), NULL. Un texto vacío o un valor fijo chocaría con el índice único."""
    if valor is None:
        return None
    texto = valor.strip() if isinstance(valor, str) else str(valor).strip()
    return texto or None


def _es_violacion_unica(e: "httpx.HTTPStatusError") -> bool:
    """PostgREST responde 409 con code 23505 cuando se viola un índice único."""
    if getattr(e.response, "status_code", None) != 409:
        return False
    try:
        return (e.response.json() or {}).get("code") == "23505"
    except Exception:
        return False


def _normalizar_nombre(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (s or "").lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ── Normalización de columnas CSV ────────────────────────────────────────────
# Mapea cualquier variante normalizada al nombre canónico interno.
_CSV_COL_MAP: Dict[str, str] = {
    # contacto
    "contacto": "contacto", "nombre": "contacto", "nombrecompleto": "contacto", "cliente": "contacto",
    # rut
    "rut": "rut", "run": "rut",
    # telefono
    "telefono": "telefono", "fono": "telefono", "celular": "telefono",
    "movil": "telefono", "numerodecontacto": "telefono",
    # proyecto
    "proyecto": "proyecto", "nombreproyecto": "proyecto",
    # correo
    "correo": "correo", "email": "correo", "mail": "correo",
    "correoelectronico": "correo", "email1": "correo",
    # tramo_renta
    "tramoderenta": "tramo_renta", "renta": "tramo_renta", "tramo": "tramo_renta",
    "tramorenta": "tramo_renta", "ingreso": "tramo_renta",
    # estado
    "estado": "estado",
    # tiene_subsidio
    "tienesubsidio": "tiene_subsidio", "subsidio": "tiene_subsidio",
    # tipo_subsidio
    "tiposubsidio": "tipo_subsidio",
    # tiene_propiedad
    "tienepropiedad": "tiene_propiedad", "propiedad": "tiene_propiedad",
}


def _norm_col(s: str) -> str:
    """Normaliza un nombre de columna: minúsculas, sin tildes, sin caracteres no-alfanuméricos."""
    nfkd = unicodedata.normalize("NFKD", (s or "").lower().strip())
    ascii_s = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", ascii_s)


def _norm_row(row: dict) -> dict:
    """Construye un dict con claves canónicas a partir de una fila CSV."""
    result: Dict[str, str] = {}
    for k, v in row.items():
        canonical = _CSV_COL_MAP.get(_norm_col(k))
        if canonical and canonical not in result:
            result[canonical] = v or ""
    return result


def _norm_row_keys(row: dict) -> Dict[str, str]:
    """Dict indexado por nombre de columna normalizado (sin tildes/mayúsculas/espacios),
    sin mapeo semántico — para formatos con columnas fijas (ej. SERVIU) tolerantes a variantes."""
    return {_norm_col(k): (v or "") for k, v in row.items() if k}


def _slugify(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = t.encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t


def _normalizar_telefono_cl(phone_raw: str) -> Optional[str]:
    """Normaliza a formato E.164 chileno: 56XXXXXXXXX."""
    digits = _normalize_phone(phone_raw)
    if not digits:
        return None
    if digits.startswith("56") and len(digits) == 11:
        return digits
    if digits.startswith("9") and len(digits) == 9:
        return f"56{digits}"
    if digits.startswith("09") and len(digits) == 10:
        return f"56{digits[1:]}"
    return digits if len(digits) >= 10 else None
