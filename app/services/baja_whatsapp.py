"""Baja de WhatsApp: el cliente escribe «BAJA» y deja de recibir mensajes. Todo ocurre en NUESTRO sistema: WhatsApp (Meta) no tiene un aviso de «baja» que haya que mandarle;
lo que Meta exige es respetar el pedido y dar una forma fácil de pedirlo, y eso lo hacemos nosotros.

* **Pedirla:** si el mensaje entero (sin tildes, mayúsculas ni signos) es «baja», «stop», «parar», «darme de baja», «no quiero más mensajes», etc., se marca ``Prospecto.opt_out`` y se le confirma
  UNA vez. Es a propósito exigir el mensaje completo: «no sé si me alcanza, ¿puedo parar un momento?» no es una baja.
* **Volver:** «alta» (o «quiero recibir mensajes») la deshace y se le confirma.
* **Mientras está de baja:** el bot no le responde, no se le manda plantilla, cotización ni confirmaciones. Sus mensajes siguen guardándose y el ejecutivo los ve.
* **El equipo se entera:** si el prospecto es un cliente del CRM, queda una nota interna («Pidió la baja de WhatsApp…») y quien intenta escribirle recibe un aviso claro.
"""
import logging
import re
import unicodedata
from typing import Any, Dict, Optional

from app.clients.supabase import _supabase_request

logger = logging.getLogger("wtsp_pita")

MSG_BAJA_CONFIRMADA = "Listo, ya no te enviaremos más mensajes por WhatsApp. Si cambias de opinión, escribe ALTA y volveremos a contactarte. ¡Gracias!"
MSG_ALTA_CONFIRMADA = "Listo, volverás a recibir nuestros mensajes. Si quieres dejar de recibirlos, escribe BAJA. ¡Gracias!"
MSG_NO_ESCRIBIR = "Este cliente pidió no recibir más mensajes por WhatsApp (baja). No se le puede escribir por este medio."

_PEDIDOS_BAJA = {"baja", "stop", "parar", "detener", "desuscribir", "desuscribirme", "dar de baja", "darme de baja", "quiero darme de baja", "quiero la baja", "dame de baja",
                 "no quiero mas mensajes", "no quiero recibir mas mensajes", "no quiero mas whatsapp", "no me escriban mas", "no me escribas mas", "no me manden mas mensajes"}
_PEDIDOS_ALTA = {"alta", "reanudar", "suscribirme", "volver a recibir mensajes", "quiero recibir mensajes", "quiero volver a recibir mensajes"}


def normalizar(texto: Any) -> str:
    """Minúsculas, sin tildes, sin signos y con los espacios juntos: «¡BAJA!» → «baja»."""
    if not isinstance(texto, str):
        return ""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t).split())


def es_baja(texto: Any) -> bool:
    return normalizar(texto) in _PEDIDOS_BAJA


def es_alta(texto: Any) -> bool:
    return normalizar(texto) in _PEDIDOS_ALTA


async def esta_de_baja(telefono: Optional[str]) -> bool:
    """¿Este número pidió no recibir mensajes? Si la base falla, el error sube: ante la duda no se le escribe."""
    if not telefono:
        return False
    filas = await _supabase_request("GET", "/Prospecto", params={"telefono_e164": f"eq.{telefono}", "select": "opt_out", "limit": "1"}) or []
    return bool(filas and filas[0].get("opt_out"))


async def _nota(cliente_id: Optional[int], texto: str) -> None:
    if cliente_id is None:
        return
    try:
        await _supabase_request("POST", "/NotaCliente", json={"cliente_id": cliente_id, "autor_nombre": "Sistema", "texto": texto}, extra_headers={"Prefer": "return=minimal"})
    except Exception as e:
        logger.warning("Baja WhatsApp | no se pudo dejar la nota en el cliente (%s)", type(e).__name__)


async def registrar_baja(prospecto_id: str, cliente_id: Optional[int]) -> None:
    """Marca la baja: sin mensajes automáticos y sin respuesta pendiente (el bot no va a contestar)."""
    await _supabase_request("PATCH", "/Prospecto", params={"id": f"eq.{prospecto_id}"}, json={"opt_out": True, "pendiente_respuesta": False}, extra_headers={"Prefer": "return=minimal"})
    await _nota(cliente_id, "Pidió la baja de WhatsApp: no se le puede escribir por ese medio hasta que escriba ALTA.")


async def registrar_alta(prospecto_id: str, cliente_id: Optional[int]) -> None:
    await _supabase_request("PATCH", "/Prospecto", params={"id": f"eq.{prospecto_id}"}, json={"opt_out": False, "pendiente_respuesta": False}, extra_headers={"Prefer": "return=minimal"})
    await _nota(cliente_id, "Pidió volver a recibir mensajes de WhatsApp (escribió ALTA).")


async def atender_mensaje(texto: str, prospecto: Optional[Dict[str, Any]]) -> Optional[str]:
    """Qué hacer con un mensaje entrante respecto de la baja. Devuelve ``None`` si hay que seguir normal; si no, lo que hay que responder:
    el texto de confirmación (baja o alta) o ``""`` (está de baja y escribió otra cosa: no se le responde, se guarda para el ejecutivo)."""
    if not prospecto or not prospecto.get("id"):
        return None
    pid, cid = str(prospecto["id"]), prospecto.get("cliente_id")
    if es_baja(texto):
        await registrar_baja(pid, cid)
        return MSG_BAJA_CONFIRMADA
    if prospecto.get("opt_out"):
        if es_alta(texto):
            await registrar_alta(pid, cid)
            return MSG_ALTA_CONFIRMADA
        await _supabase_request("PATCH", "/Prospecto", params={"id": f"eq.{pid}"}, json={"pendiente_respuesta": False}, extra_headers={"Prefer": "return=minimal"})
        return ""
    return None
