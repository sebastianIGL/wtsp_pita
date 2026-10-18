"""Firma de los mensajes que Meta envía al webhook de WhatsApp (cabecera ``X-Hub-Signature-256``).

Meta firma el CUERPO EXACTO de cada POST con HMAC-SHA256 usando el "App Secret" de la app. Comprobarla
demuestra que el mensaje vino de Meta y no de alguien que conoce la URL del webhook.

  * Etapa 1 (por defecto): solo se REGISTRA el resultado; nunca se rechaza nada.
  * Etapa 2: con ``WA_SIGNATURE_ENFORCE=true`` se rechazan (403) los mensajes sin firma válida.
  * Sin ``WA_APP_SECRET`` no se puede comprobar nada: no se rechaza (aunque ENFORCE esté activa) y el
    arranque avisa. Un secreto ausente no debe tumbar el bot.

Se firma el cuerpo en bruto (bytes): nunca el JSON ya interpretado, porque cualquier espacio o reordenación
cambia la firma.
"""
import hashlib
import hmac
from typing import Optional

VALIDA = "valida"
INVALIDA = "invalida"
AUSENTE = "ausente"
SIN_SECRETO = "sin_secreto"

_PREFIJO = "sha256="


def verificar_firma(cuerpo: bytes, cabecera: Optional[str], secreto: Optional[str]) -> str:
    """Devuelve VALIDA, INVALIDA, AUSENTE o SIN_SECRETO. Nunca lanza."""
    if not secreto:
        return SIN_SECRETO
    cabecera = (cabecera or "").strip()
    if not cabecera:
        return AUSENTE
    if not cabecera.lower().startswith(_PREFIJO):
        return INVALIDA
    recibida = cabecera[len(_PREFIJO):].strip().lower()
    esperada = hmac.new(secreto.encode("utf-8"), cuerpo, hashlib.sha256).hexdigest()
    # comparación en tiempo constante; en bytes para tolerar caracteres no ASCII en la cabecera
    return VALIDA if hmac.compare_digest(esperada.encode("ascii"), recibida.encode("utf-8")) else INVALIDA


def debe_rechazar(resultado: str, enforce: bool) -> bool:
    """Solo se rechaza con ENFORCE activa y una firma comprobable que no es válida."""
    return enforce and resultado in (INVALIDA, AUSENTE)
