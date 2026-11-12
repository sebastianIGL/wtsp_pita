"""Cotizar a un cliente (botón «Cotizar» del panel): ver la cotización, bajar su PDF y enviarla por correo (PDF adjunto) y/o por WhatsApp (PDF como mensaje libre).

* **Permiso:** cualquier persona con sesión (``app/core/permisos.puede_cotizar``); un ejecutivo, como siempre, solo sobre sus clientes.
* **WhatsApp:** Meta solo deja mandar un mensaje libre dentro de las 24 horas desde el último mensaje del cliente; fuera de ese plazo el canal queda no disponible y se explica por qué.
* Cada intento (por canal) queda registrado en la tabla ``Cotizacion`` con una «foto» de lo cotizado; si esa tabla aún no existe el envío igual sale y solo se anota en el registro.
* Si al menos un canal salió bien, el cliente pasa a «Cotización enviada».
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request, Response

from app.clients.gmail import _gmail_send
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.clients.whatsapp import send_whatsapp_document
from app.core.autorizacion import exigir_cliente
from app.core.config import get_settings
from app.core.errors import AppError, mensaje_publico
from app.core.permisos import puede_cotizar
from app.core.utilidades import _normalize_phone
from app.services import cotizacion as cz
from app.services import uf as servicio_uf
from app.services.clientes_datos import marcar_cotizacion_enviada
from app.services.mensajes import insertar_mensaje

logger = logging.getLogger("wtsp_pita")

router = APIRouter()

CANALES = ("correo", "whatsapp")
VENTANA_WHATSAPP = timedelta(hours=24)
_LOCK_SEG = 30                                   # válvula de seguridad si algo deja el candado colgado (igual que en la evaluación)
_en_curso: Dict[int, float] = {}
_CORREO = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")
MSG_SIN_PERMISO = "Cotizar todavía no está habilitado para tu perfil."
MSG_FUERA_DE_VENTANA = ("El cliente no ha escrito en las últimas 24 horas y WhatsApp solo permite enviar un archivo en ese plazo. "
                        "Envíalo por correo, o escríbele primero con una plantilla para que responda.")
MSG_SIN_TELEFONO = "El cliente no tiene teléfono."


async def _autorizar(request: Request, cliente_id: int):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return None, Response(content="Unauthorized", status_code=401)
    if not puede_cotizar(perfil):
        return None, Response(content=MSG_SIN_PERMISO, status_code=403)
    denegado = await exigir_cliente(perfil, cliente_id)
    if denegado:
        return None, denegado
    return perfil, None


async def _armar(cliente_id: int, perfil: Dict[str, Any]) -> Dict[str, Any]:
    datos = await cz.cargar(cliente_id)
    uf = None
    try:
        hoy_uf = await servicio_uf.uf_del_dia()
        if hoy_uf:
            uf = {"valor": hoy_uf[0], "fecha": hoy_uf[1].isoformat()}
    except Exception:
        logger.warning("Cotizar | no se pudo leer la UF del día: la cotización sale sin pesos")
    return cz.armar(datos, perfil, uf, datetime.now(ZoneInfo("America/Santiago")).date())


def ventana_abierta(ultimo_entrante_en: Optional[str], ahora: Optional[datetime] = None) -> bool:
    """¿El cliente escribió en las últimas 24 horas? Sin fecha o con una fecha ilegible, no."""
    if not ultimo_entrante_en:
        return False
    try:
        t = datetime.fromisoformat(str(ultimo_entrante_en).replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    ahora = ahora or datetime.now(timezone.utc)
    return timedelta(0) <= ahora - t < VENTANA_WHATSAPP or (t > ahora and t - ahora < timedelta(minutes=5))


async def _prospecto(telefono: Optional[str]) -> Optional[Dict[str, Any]]:
    if not telefono:
        return None
    try:
        filas = await _supabase_request("GET", "/Prospecto", params={"telefono_e164": f"eq.{telefono}", "select": "id,ultimo_entrante_en", "limit": "1"}) or []
        return filas[0] if filas else None
    except Exception:
        logger.warning("Cotizar | no se pudo leer el prospecto para saber si WhatsApp está disponible")
        return None


async def _historial(cliente_id: int) -> List[Dict[str, Any]]:
    try:
        filas = await _supabase_request("GET", "/Cotizacion", params={"cliente_id": f"eq.{cliente_id}", "select": "canal,destino,estado,usuario_nombre,creado_en",
                                                                       "order": "creado_en.desc", "limit": "5"}) or []
        return [{k: f.get(k) for k in ("canal", "destino", "estado", "usuario_nombre", "creado_en")} for f in filas]      # nunca sale la «foto» (valores) ni el id
    except Exception:
        return []                                   # la tabla aún no existe o la base no respondió: la vista sale sin historial


@router.get("/api/clientes/{cliente_id}/cotizacion")
async def api_ver_cotizacion(cliente_id: int, request: Request):
    perfil, error = await _autorizar(request, cliente_id)
    if error:
        return error
    try:
        cot = await _armar(cliente_id, perfil)
        telefono = _normalize_phone(cot["cliente"].get("telefono") or "") or None
        prospecto = await _prospecto(telefono)
        abierta = ventana_abierta((prospecto or {}).get("ultimo_entrante_en"))
        wa = {"destino": telefono, "disponible": bool(telefono) and abierta,
              "motivo": None if (telefono and abierta) else (MSG_SIN_TELEFONO if not telefono else MSG_FUERA_DE_VENTANA)}
        return {"cotizacion": cot, "resumen": cz.filas_resumen(cot), "otros": cz.otros_costos(cot), "archivo": cz.nombre_archivo(cot),
                "canales": {"correo": {"destino": cot["cliente"].get("correo"), "disponible": True}, "whatsapp": wa}, "historial": await _historial(cliente_id)}
    except Exception as e:
        return Response(content=mensaje_publico(e, "api_ver_cotizacion"), status_code=e.status_code if isinstance(e, AppError) else 500, media_type="text/plain")


@router.get("/api/clientes/{cliente_id}/cotizacion/pdf")
async def api_pdf_cotizacion(cliente_id: int, request: Request):
    perfil, error = await _autorizar(request, cliente_id)
    if error:
        return error
    try:
        cot = await _armar(cliente_id, perfil)
        return Response(content=cz.generar_pdf(cot), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{cz.nombre_archivo(cot)}"', "Cache-Control": "no-store"})
    except Exception as e:
        return Response(content=mensaje_publico(e, "api_pdf_cotizacion"), status_code=e.status_code if isinstance(e, AppError) else 500, media_type="text/plain")


async def _registrar(cliente_id: int, perfil: Dict[str, Any], canal: str, destino: str, ok: bool, detalle: Optional[str], cot: Dict[str, Any]) -> bool:
    try:
        await _supabase_request("POST", "/Cotizacion", json={
            "cliente_id": cliente_id, "usuario_id": perfil.get("id"), "usuario_nombre": perfil.get("nombre"), "canal": canal, "destino": destino[:200] or "—",
            "estado": "enviada" if ok else "error", "detalle": (detalle or None) and detalle[:500], "valores": cot}, extra_headers={"Prefer": "return=minimal"})
        return True
    except Exception as e:
        logger.warning("Cotizar | no se pudo registrar el envío por %s (%s)", canal, type(e).__name__)
        return False


async def _por_correo(cot: Dict[str, Any], perfil: Dict[str, Any], destino: str, pdf: bytes) -> Dict[str, Any]:
    try:
        if not get_settings().google_service_account_json:
            raise AppError("El envío de correos no está configurado en el servidor.", 503)
        alias = perfil.get("email_alias") or perfil.get("correo") or get_settings().email_admin or ""
        if "@" not in alias:
            raise AppError("Tu usuario no tiene un correo configurado para enviar. Pídele al administrador que lo agregue.", 400)
        nombre = perfil.get("nombre") or "QueSubsidio"
        remitente = f"{nombre} <{alias}>" if "<" not in alias else alias               # _gmail_send codifica bien el nombre aunque lleve tildes
        await _gmail_send(from_addr=remitente, to=[destino], subject=cz.asunto(cot), html=cz.html_correo(cot),
                          attachments=[{"filename": cz.nombre_archivo(cot), "content": pdf, "content_type": "application/pdf"}])
        return {"ok": True, "destino": destino}
    except Exception as e:
        return {"ok": False, "destino": destino, "error": mensaje_publico(e, "cotizar_correo")}


def _explicar_meta(e: httpx.HTTPStatusError) -> Optional[str]:
    try:
        codigo = int(((e.response.json() or {}).get("error") or {}).get("code"))
    except Exception:
        return None
    if codigo == 131047:
        return MSG_FUERA_DE_VENTANA
    if codigo in (131026, 131030):
        return "WhatsApp no pudo entregar el mensaje a ese número: puede que no tenga WhatsApp."
    return None


async def _por_whatsapp(cot: Dict[str, Any], cliente_id: int, telefono: Optional[str], prospecto: Optional[Dict[str, Any]], pdf: bytes) -> Dict[str, Any]:
    if not telefono:
        return {"ok": False, "destino": "", "error": MSG_SIN_TELEFONO}
    if not ventana_abierta((prospecto or {}).get("ultimo_entrante_en")):
        return {"ok": False, "destino": telefono, "error": MSG_FUERA_DE_VENTANA}
    texto = cz.texto_whatsapp(cot)
    try:
        wa = await send_whatsapp_document(to=telefono, contenido=pdf, filename=cz.nombre_archivo(cot), caption=texto)
    except httpx.HTTPStatusError as e:
        return {"ok": False, "destino": telefono, "error": _explicar_meta(e) or mensaje_publico(e, "cotizar_whatsapp")}
    except Exception as e:
        return {"ok": False, "destino": telefono, "error": mensaje_publico(e, "cotizar_whatsapp")}
    try:                                            # que quede en la conversación (si no se puede anotar, el mensaje ya salió igual)
        wamid = ((wa or {}).get("messages") or [{}])[0].get("id") if isinstance(wa, dict) else None
        await insertar_mensaje(prospecto_id=prospecto["id"], direccion="saliente", text="[Cotización en PDF] " + texto, wa_message_id=wamid, cliente_id=cliente_id)
    except Exception as e:
        logger.warning("Cotizar | no se pudo anotar la cotización en la conversación (%s)", type(e).__name__)
    return {"ok": True, "destino": telefono}


@router.post("/api/clientes/{cliente_id}/cotizar")
async def api_cotizar(cliente_id: int, request: Request):
    perfil, error = await _autorizar(request, cliente_id)
    if error:
        return error
    ahora = time.monotonic()
    desde = _en_curso.get(cliente_id)
    if desde is not None and (ahora - desde) < _LOCK_SEG:
        return Response(content="Ya se está enviando una cotización para este cliente, espera unos segundos.", status_code=409, media_type="text/plain")
    _en_curso[cliente_id] = ahora
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        canales = body.get("canales")
        if not isinstance(canales, list) or not canales or any(c not in CANALES for c in canales):
            return Response(content="Elige al menos un canal: correo o WhatsApp.", status_code=400, media_type="text/plain")
        canales = [c for c in CANALES if c in canales]
        correo = str(body.get("correo") or "").strip() if "correo" in canales else ""
        if "correo" in canales and not _CORREO.match(correo):
            return Response(content="Escribe un correo válido para enviar la cotización.", status_code=400, media_type="text/plain")

        cot = await _armar(cliente_id, perfil)
        pdf = cz.generar_pdf(cot)
        resultados: Dict[str, Dict[str, Any]] = {}
        if "correo" in canales:
            resultados["correo"] = await _por_correo(cot, perfil, correo, pdf)
        if "whatsapp" in canales:
            telefono = _normalize_phone(cot["cliente"].get("telefono") or "") or None
            resultados["whatsapp"] = await _por_whatsapp(cot, cliente_id, telefono, await _prospecto(telefono), pdf)
        for canal, r in resultados.items():
            r["registrada"] = await _registrar(cliente_id, perfil, canal, r.get("destino") or "", r["ok"], r.get("error"), cot)
        alguno = any(r["ok"] for r in resultados.values())
        estado = await marcar_cotizacion_enviada(cliente_id) if alguno else None
        return {"ok": alguno, "resultados": resultados, "estado_gestion": estado}
    except Exception as e:
        return Response(content=mensaje_publico(e, "api_cotizar"), status_code=e.status_code if isinstance(e, AppError) else 500, media_type="text/plain")
    finally:
        _en_curso.pop(cliente_id, None)
