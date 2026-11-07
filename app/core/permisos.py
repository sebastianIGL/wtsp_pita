"""Permisos por rol: owner y administrador ven y hacen todo en el CRM; el owner además tiene operaciones exclusivas.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). Funciones puras sobre el perfil de la persona usuaria.
"""
from typing import Dict, Optional


def _solo_admin(perfil: Optional[Dict]) -> bool:
    """True para owner y administrador — acceso a todo el CRM."""
    return bool(perfil and perfil.get("rol") in ("owner", "administrador"))


def puede_cotizar(perfil: Optional[Dict]) -> bool:
    """Owner y administrador siempre; los ejecutivos solo si el interruptor ``COTIZAR_PARA_EJECUTIVOS`` está encendido."""
    from app.core.config import get_settings
    if not perfil:
        return False
    return _solo_admin(perfil) or (perfil.get("rol") == "ejecutivo" and get_settings().cotizar_para_ejecutivos)


def _solo_owner(perfil: Optional[Dict]) -> bool:
    """True solo para owner — operaciones exclusivas del dueño."""
    return bool(perfil and perfil.get("rol") == "owner")
