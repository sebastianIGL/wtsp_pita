"""Configuración de la aplicación (pydantic-settings).

Reglas:
  * Los nombres de variables de entorno son los que ya existen en producción; cada campo
    acepta además sus alias históricos (``SUPABASE_URL`` / ``SUPABASE_PROJECT_URL`` / …).
    El primer alias presente gana, igual que el antiguo ``_get_env``.
  * Un valor vacío cuenta como ausente (``env_ignore_empty``), igual que ``_get_env``.
  * Los nombres distinguen mayúsculas, como el entorno de Linux.
  * Solo es obligatorio lo imprescindible (Supabase). Todo lo demás es opcional: el módulo
    afectado se da por deshabilitado y se registra un aviso al arrancar.
  * Al arrancar se registran los NOMBRES de las variables cargadas y de las que faltan.
    Jamás se registran valores.

El entorno se vuelve a leer solo si cambió desde la última llamada (huella de los nombres
conocidos): en producción equivale a un singleton, y en pruebas respeta ``monkeypatch.setenv``.
"""
import os
from typing import Dict, List, Optional, Tuple

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Falta configuración imprescindible. El mensaje solo contiene nombres de variables."""


def _alias(*nombres: str):
    return AliasChoices(*nombres)


_VERDADERO = ("1", "true", "yes", "on")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=False,
    )

    # ── Supabase (obligatorio) ──────────────────────────────────────────────
    supabase_url: Optional[str] = Field(None, validation_alias=_alias("SUPABASE_URL", "SUPABASE_PROJECT_URL", "SUPABASE_REST_URL"))
    supabase_service_role_key: Optional[str] = Field(None, validation_alias=_alias("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE"))
    supabase_anon_key: Optional[str] = Field(None, validation_alias=_alias("SUPABASE_ANON_KEY", "SUPABASE_KEY"))

    # ── WhatsApp (Meta) ─────────────────────────────────────────────────────
    wa_verify_token: Optional[str] = Field(None, validation_alias=_alias("WA_VERIFY_TOKEN", "WHATSAPP_VERIFY_TOKEN"))
    wa_access_token: Optional[str] = Field(None, validation_alias=_alias("WA_ACCESS_TOKEN", "WHATSAPP_ACCESS_TOKEN"))
    wa_phone_number_id: Optional[str] = Field(None, validation_alias=_alias("WA_PHONE_NUMBER_ID", "WHATSAPP_PHONE_NUMBER_ID"))
    wa_graph_version: str = Field("v22.0", validation_alias=_alias("WA_GRAPH_VERSION", "WHATSAPP_GRAPH_API_VERSION"))
    wa_waba_id: Optional[str] = Field(None, validation_alias=_alias("WA_WABA_ID", "WHATSAPP_WABA_ID"))
    template_recordatorio_wa: str = Field("recordatorio_cliente", validation_alias=_alias("TEMPLATE_RECORDATORIO_WA"))
    admin_notify_phone: Optional[str] = Field(None, validation_alias=_alias("ADMIN_NOTIFY_PHONE"))
    bot_reply_delay: int = Field(30, validation_alias=_alias("BOT_REPLY_DELAY"))

    # ── Bot (Anthropic) ─────────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = Field(None, validation_alias=_alias("ANTHROPIC_API_KEY"))

    # ── Correo (Gmail por cuenta de servicio) ───────────────────────────────
    google_service_account_json: Optional[str] = Field(None, validation_alias=_alias("GOOGLE_SERVICE_ACCOUNT_JSON"))
    email_admin: Optional[str] = Field(None, validation_alias=_alias("EMAIL_ADMIN"))

    # ── Integraciones entrantes / salientes ─────────────────────────────────
    ingest_api_key: Optional[str] = Field(None, validation_alias=_alias("INGEST_API_KEY"))
    movendo_webhook_key: Optional[str] = Field(None, validation_alias=_alias("MOVENDO_WEBHOOK_KEY"))
    movendo_client_id: Optional[str] = Field(None, validation_alias=_alias("MOVENDO_CLIENT_ID"))
    movendo_client_secret: Optional[str] = Field(None, validation_alias=_alias("MOVENDO_CLIENT_SECRET"))
    movendo_username: Optional[str] = Field(None, validation_alias=_alias("MOVENDO_USERNAME"))
    movendo_password: Optional[str] = Field(None, validation_alias=_alias("MOVENDO_PASSWORD"))
    movendo_default_user_id: Optional[str] = Field(None, validation_alias=_alias("MOVENDO_DEFAULT_USER_ID"))

    # ── Sitio y servidor ────────────────────────────────────────────────────
    site_url: Optional[str] = Field(None, validation_alias=_alias("SITE_URL"))
    enable_docs: bool = Field(False, validation_alias=_alias("ENABLE_DOCS"))
    log_level: str = Field("INFO", validation_alias=_alias("LOG_LEVEL"))
    host: str = Field("0.0.0.0", validation_alias=_alias("HOST"))
    port: int = Field(8000, validation_alias=_alias("PORT"))

    @field_validator("enable_docs", mode="before")
    @classmethod
    def _enable_docs_laxo(cls, v):
        # Igual que antes: cualquier cosa distinta de 1/true/yes/on es False (nunca error)
        return str(v).strip().lower() in _VERDADERO

    # Conveniencias que replican valores por defecto que antes vivían en cada sitio de uso
    @property
    def site_url_o_local(self) -> str:
        return self.site_url or "http://localhost:8000"


# ── Metadatos derivados de los campos ────────────────────────────────────────

def _aliases_de(campo: str) -> Tuple[str, ...]:
    va = Settings.model_fields[campo].validation_alias
    return tuple(va.choices) if isinstance(va, AliasChoices) else (str(va),)


NOMBRES_ENTORNO: Tuple[str, ...] = tuple(
    n for campo in Settings.model_fields for n in _aliases_de(campo)
)

OBLIGATORIAS: Tuple[str, ...] = ("supabase_url", "supabase_service_role_key")

# módulo → (campos necesarios, consecuencia de que falten)
MODULOS: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "whatsapp_envio": (("wa_access_token", "wa_phone_number_id"),
                       "el bot no podrá enviar mensajes ni plantillas por WhatsApp"),
    "whatsapp_verificacion": (("wa_verify_token",),
                              "Meta no podrá verificar el webhook (GET /webhook responde 500)"),
    "whatsapp_plantillas": (("wa_waba_id",), "no se podrá listar las plantillas aprobadas de Meta"),
    "bot_ia": (("anthropic_api_key",), "el bot responde con eco en vez de usar el modelo"),
    "correo": (("google_service_account_json",), "no se podrán enviar correos (evaluaciones, contacto, avisos)"),
    "correo_admin": (("email_admin",), "no se enviarán avisos al correo del administrador"),
    "progreso_mi_camino": (("supabase_anon_key",), "/api/progreso responderá 401 a todos"),
    "ingesta": (("ingest_api_key",), "POST /prospectos/ingesta queda ABIERTO a cualquiera"),
    "movendo_entrante": (("movendo_webhook_key",), "las rutas /api/movendo/* y de contactos quedan ABIERTAS a cualquiera"),
    "movendo_saliente": (("movendo_username", "movendo_password"), "no se podrá autenticar contra la API de Movendo"),
    "sitio": (("site_url",), "CORS solo admite localhost y los enlaces de invitación apuntan a localhost"),
}


# ── Carga con huella del entorno ─────────────────────────────────────────────

_cache: Optional[Tuple[tuple, Settings]] = None


def _huella() -> tuple:
    return tuple(os.environ.get(n) for n in NOMBRES_ENTORNO)


def get_settings() -> Settings:
    global _cache
    h = _huella()
    if _cache is None or _cache[0] != h:
        _cache = (h, Settings())
    return _cache[1]


# ── Diagnóstico de arranque (solo nombres) ───────────────────────────────────

def _alias_presente(campo: str) -> Optional[str]:
    for n in _aliases_de(campo):
        if os.environ.get(n):
            return n
    return None


def diagnostico() -> Dict[str, List[str]]:
    """Nombres de variables cargadas y ausentes. Nunca incluye valores.

    ``usan_defecto``: ausentes pero con valor por defecto (no es un problema).
    ``faltan_opcionales``: ausentes y sin valor por defecto (el módulo afectado queda deshabilitado)."""
    cargadas: List[str] = []
    usan_defecto: List[str] = []
    faltan_obligatorias: List[str] = []
    faltan_opcionales: List[str] = []
    for campo, info in Settings.model_fields.items():
        usado = _alias_presente(campo)
        primario = _aliases_de(campo)[0]
        if usado:
            cargadas.append(usado)
        elif campo in OBLIGATORIAS:
            faltan_obligatorias.append(" | ".join(_aliases_de(campo)))
        elif info.default is not None:
            usan_defecto.append(primario)
        else:
            faltan_opcionales.append(primario)
    return {"cargadas": cargadas, "usan_defecto": usan_defecto,
            "faltan_obligatorias": faltan_obligatorias, "faltan_opcionales": faltan_opcionales}


def modulos_deshabilitados() -> List[Tuple[str, List[str], str]]:
    """(módulo, variables que faltan, consecuencia) de cada módulo opcional sin configurar."""
    salida = []
    for modulo, (campos, efecto) in MODULOS.items():
        faltan = [_aliases_de(c)[0] for c in campos if not _alias_presente(c)]
        if faltan:
            salida.append((modulo, faltan, efecto))
    return salida


def validar_obligatorias() -> None:
    """Lanza ConfigError (solo con nombres) si falta algo imprescindible."""
    faltan = diagnostico()["faltan_obligatorias"]
    if faltan:
        raise ConfigError(
            "Falta configuración obligatoria: " + "; ".join(faltan)
            + ". Defínela en las variables de entorno del servicio."
        )


def registrar_configuracion(logger) -> None:
    """Registra al arrancar qué variables hay (solo nombres) y qué módulos quedan deshabilitados.
    Lanza ConfigError si falta algo obligatorio (después de registrarlo)."""
    d = diagnostico()
    logger.info("Configuración: %d variables cargadas: %s", len(d["cargadas"]), ", ".join(d["cargadas"]) or "(ninguna)")
    if d["usan_defecto"]:
        logger.info("Configuración: ausentes, usan valor por defecto: %s", ", ".join(d["usan_defecto"]))
    if d["faltan_opcionales"]:
        logger.info("Configuración: opcionales ausentes (sin valor por defecto): %s", ", ".join(d["faltan_opcionales"]))
    for modulo, faltan, efecto in modulos_deshabilitados():
        logger.warning("Módulo deshabilitado o incompleto [%s]: falta %s → %s", modulo, ", ".join(faltan), efecto)
    if d["faltan_obligatorias"]:
        logger.error("Configuración: faltan variables obligatorias: %s", "; ".join(d["faltan_obligatorias"]))
    validar_obligatorias()
