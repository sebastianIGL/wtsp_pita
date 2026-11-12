"""Permisos por rol: owner y administrador ven y hacen todo en el CRM; el owner además tiene operaciones exclusivas.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). Funciones puras sobre el perfil de la persona usuaria.
"""
from typing import Dict, Optional


def _solo_admin(perfil: Optional[Dict]) -> bool:
    """True para owner y administrador — acceso a todo el CRM."""
    return bool(perfil and perfil.get("rol") in ("owner", "administrador"))


def puede_cotizar(perfil: Optional[Dict]) -> bool:
    """Cualquier persona con sesión (owner, administrador y ejecutivo); un ejecutivo cotiza solo a sus propios clientes (lo exige ``exigir_cliente``)."""
    return bool(perfil)


def _solo_owner(perfil: Optional[Dict]) -> bool:
    """True solo para owner — operaciones exclusivas del dueño."""
    return bool(perfil and perfil.get("rol") == "owner")
