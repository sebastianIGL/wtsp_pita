"""Cotizar a varios clientes a la vez (barra de la «Tabla» al marcar clientes → «Enviar cotización»).

* ``POST /api/cotizaciones/lote/revisar``: no envía nada. Para cada cliente dice si se le puede cotizar (tiene tipología con valor, etc.), su correo y por dónde saldría el WhatsApp.
* ``POST /api/cotizaciones/lote/enviar``: envía de a uno (el PDF de cada cliente es distinto) y va contando en vivo (eventos ``data: {json}``). Un cliente con problema no frena a los demás.
* **Correo:** al correo que el cliente tiene registrado, con el PDF adjunto y desde el correo de quien envía.
* **WhatsApp:** dentro de las 24 h desde el último mensaje del cliente, el PDF como mensaje libre; fuera de ese plazo, la plantilla con documento ``TEMPLATE_COTIZACION_WA`` si está
  configurada (si no, ese cliente no recibe WhatsApp y se explica por qué).
* Un ejecutivo solo cotiza a sus clientes (igual que de a uno). Máximo ``MAX_LOTE`` clientes por vez. Cada intento queda registrado y quien recibe algo pasa a «Cotización enviada».
"""
import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.clients.whatsapp import detalle_error_meta, send_whatsapp_template_documento
from app.core.autorizacion import exigir_cliente
from app.core.config import get_settings
from app.core.errors import AppError, mensaje_publico
from app.core.permisos import puede_cotizar
from app.core.utilidades import _normalize_phone
from app.routers import cotizaciones as cot_router
from app.services import cotizacion as cz
from app.services.clientes_datos import marcar_cotizacion_enviada
from app.services.mensajes import insertar_mensaje

logger = logging.getLogger("wtsp_pita")

router = APIRouter()

MAX_LOTE = 40
MSG_SIN_CORREO = "El cliente no tiene un correo válido."
MSG_SIN_PLANTILLA = ("El cliente no ha escrito en las últimas 24 horas y no hay una plantilla con documento configurada para escribirle primero. "
                     "Envíaselo por correo, o pídele al administrador que configure la plantilla de cotización.")
MSG_NO_ES_TUYO = "No es uno de tus clientes."


def ids_del_cuerpo(cuerpo: Any) -> List[int]:
    """Los ids de clientes del cuerpo, sin repetir y en el mismo orden. ``AppError`` 400 si no sirven."""
    ids = cuerpo.get("ids") if isinstance(cuerpo, dict) else None
    if not isinstance(ids, list) or not ids:
        raise AppError("Marca al menos un cliente para cotizar.", 400)
    if len(ids) > MAX_LOTE:
        raise AppError(f"Se puede cotizar a hasta {MAX_LOTE} clientes por vez; marcaste {len(ids)}. Hazlo en grupos más chicos.", 400)
    salida: List[int] = []
    for i in ids:
        if isinstance(i, bool) or not isinstance(i, int) or i < 1:
            raise AppError("Uno de los clientes marcados no es válido. Vuelve a marcarlos.", 400)
        if i not in salida:
            salida.append(i)
    return salida


async def _autorizar(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return None, Response(content="Unauthorized", status_code=401)
    if not puede_cotizar(perfil):
        return None, Response(content=cot_router.MSG_SIN_PERMISO, status_code=403)
    return perfil, None


async def _cuerpo(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        return {}


def _evento(datos: Dict[str, Any]) -> str:
    return "data: " + json.dumps(datos, ensure_ascii=False) + "\n\n"


async def _nombres(ids: List[int]) -> Dict[int, str]:
    try:
        filas = await _supabase_request("GET", "/Cliente", params={"id": f"in.({','.join(str(i) for i in ids)})", "select": "id,Contacto"}) or []
    except Exception:
        return {}
    return {int(f["id"]): (f.get("Contacto") or "").strip() for f in filas if f.get("id") is not None}


def _plantilla() -> str:
    return (get_settings().template_cotizacion_wa or "").strip()


def _correo_valido(correo: Optional[str]) -> bool:
    return bool(correo) and bool(cot_router._CORREO.match(correo))


def _explicar_plantilla(e: httpx.HTTPStatusError, plantilla: str) -> Optional[str]:
    """Una explicación en español de los errores más comunes de Meta con la plantilla de cotización; ``None`` si no es uno conocido."""
    codigo, _ = detalle_error_meta(e)
    if codigo == 132001:
        return f"WhatsApp no encuentra la plantilla «{plantilla}» (no existe, no está aprobada o no está en es_CL). Revísala en Meta (WhatsApp Manager) o la variable TEMPLATE_COTIZACION_WA."
    if codigo in (132000, 132005, 132007, 132012, 132015, 132016):
        return f"La plantilla «{plantilla}» no calza con lo que se le envía: debe tener un documento en el encabezado y 2 variables de texto en el cuerpo (cliente y proyecto)."
    return None


async def _por_whatsapp_plantilla(cot: Dict[str, Any], cliente_id: int, telefono: str, prospecto: Optional[Dict[str, Any]], pdf: bytes, plantilla: str) -> Dict[str, Any]:
    nombre = (cot["cliente"].get("nombre") or "").split()[0] if cot["cliente"].get("nombre") else "cliente"
    proyecto = cot["proyecto"].get("nombre") or "tu proyecto"
    try:
        wa = await send_whatsapp_template_documento(to=telefono, template_name=plantilla, language_code="es_CL", contenido=pdf, filename=cz.nombre_archivo(cot),
                                                    body_text_params=[" ".join(nombre.split())[:60] or "cliente", " ".join(proyecto.split())[:100]])
    except httpx.HTTPStatusError as e:
        return {"ok": False, "destino": telefono, "error": _explicar_plantilla(e, plantilla) or cot_router._explicar_meta(e) or mensaje_publico(e, "cotizar_lote_whatsapp")}
    except Exception as e:
        return {"ok": False, "destino": telefono, "error": mensaje_publico(e, "cotizar_lote_whatsapp")}
    if prospecto:                                    # que quede en la conversación (si no se puede anotar, el mensaje ya salió igual)
        try:
            wamid = ((wa or {}).get("messages") or [{}])[0].get("id") if isinstance(wa, dict) else None
            await insertar_mensaje(prospecto_id=prospecto["id"], direccion="saliente", text="[Plantilla con cotización en PDF] " + cz.texto_whatsapp(cot), wa_message_id=wamid, cliente_id=cliente_id)
        except Exception as e:
            logger.warning("Cotizar lote | no se pudo anotar la cotización en la conversación (%s)", type(e).__name__)
    return {"ok": True, "destino": telefono, "plantilla": True}


@router.post("/api/cotizaciones/lote/revisar")
async def api_revisar_lote(request: Request):
    perfil, error = await _autorizar(request)
    if error:
        return error
    try:
        ids = ids_del_cuerpo(await _cuerpo(request))
        nombres = await _nombres(ids)
        hay_plantilla = bool(_plantilla())
        clientes: List[Dict[str, Any]] = []
        for cid in ids:
            fila: Dict[str, Any] = {"id": cid, "nombre": nombres.get(cid) or f"Cliente {cid}", "ok": False, "motivo": None, "correo": None, "telefono": None, "whatsapp": "no",
                                    "proyecto": None, "tipologia": None}
            if await exigir_cliente(perfil, cid):
                fila["motivo"] = MSG_NO_ES_TUYO
                clientes.append(fila)
                continue
            try:
                cot = await cot_router._armar(cid, perfil)
            except AppError as e:
                fila["motivo"] = str(e)
                clientes.append(fila)
                continue
            telefono = _normalize_phone(cot["cliente"].get("telefono") or "") or None
            fila.update(ok=True, nombre=cot["cliente"]["nombre"], correo=cot["cliente"].get("correo") if _correo_valido(cot["cliente"].get("correo")) else None, telefono=telefono,
                        proyecto=cot["proyecto"]["nombre"], tipologia=cot["tipologia"]["nombre"])
            if telefono:
                abierta = cot_router.ventana_abierta(((await cot_router._prospecto(telefono)) or {}).get("ultimo_entrante_en"))
                fila["whatsapp"] = "ventana" if abierta else ("plantilla" if hay_plantilla else "no")
            clientes.append(fila)
        return {"clientes": clientes, "plantilla_configurada": hay_plantilla, "maximo": MAX_LOTE}
    except Exception as e:
        return Response(content=mensaje_publico(e, "api_revisar_lote"), status_code=e.status_code if isinstance(e, AppError) else 500, media_type="text/plain")


async def _enviar_uno(perfil: Dict[str, Any], cid: int, canales: List[str], nombre: str) -> Dict[str, Any]:
    """Cotiza y envía a un cliente. Devuelve ``{id, nombre, ok, motivo, resultados}``; nunca lanza."""
    salida: Dict[str, Any] = {"id": cid, "nombre": nombre, "ok": False, "motivo": None, "resultados": {}}
    ahora = time.monotonic()
    desde = cot_router._en_curso.get(cid)
    if desde is not None and (ahora - desde) < cot_router._LOCK_SEG:
        salida["motivo"] = "Ya se está enviando una cotización para este cliente."
        return salida
    cot_router._en_curso[cid] = ahora
    try:
        if await exigir_cliente(perfil, cid):
            salida["motivo"] = MSG_NO_ES_TUYO
            return salida
        try:
            cot = await cot_router._armar(cid, perfil)
        except AppError as e:
            salida["motivo"] = str(e)
            return salida
        salida["nombre"] = cot["cliente"]["nombre"]
        pdf = cz.generar_pdf(cot)
        resultados: Dict[str, Dict[str, Any]] = {}
        if "correo" in canales:
            correo = cot["cliente"].get("correo")
            resultados["correo"] = await cot_router._por_correo(cot, perfil, correo, pdf) if _correo_valido(correo) else {"ok": False, "destino": "", "error": MSG_SIN_CORREO}
        if "whatsapp" in canales:
            telefono = _normalize_phone(cot["cliente"].get("telefono") or "") or None
            prospecto = await cot_router._prospecto(telefono)
            if not telefono:
                resultados["whatsapp"] = {"ok": False, "destino": "", "error": cot_router.MSG_SIN_TELEFONO}
            elif cot_router.ventana_abierta((prospecto or {}).get("ultimo_entrante_en")):
                resultados["whatsapp"] = await cot_router._por_whatsapp(cot, cid, telefono, prospecto, pdf)
            elif _plantilla():
                resultados["whatsapp"] = await _por_whatsapp_plantilla(cot, cid, telefono, prospecto, pdf, _plantilla())
            else:
                resultados["whatsapp"] = {"ok": False, "destino": telefono, "error": MSG_SIN_PLANTILLA}
        for canal, r in resultados.items():
            r["registrada"] = await cot_router._registrar(cid, perfil, canal, r.get("destino") or "", r["ok"], r.get("error"), cot)
        salida["resultados"] = resultados
        salida["ok"] = any(r["ok"] for r in resultados.values())
        if salida["ok"]:
            await marcar_cotizacion_enviada(cid)
        else:
            salida["motivo"] = "; ".join(f"{'Correo' if c == 'correo' else 'WhatsApp'}: {r.get('error') or 'no salió'}" for c, r in resultados.items())
        return salida
    except Exception as e:
        salida["motivo"] = mensaje_publico(e, "api_enviar_lote")
        return salida
    finally:
        cot_router._en_curso.pop(cid, None)


async def _flujo(perfil: Dict[str, Any], ids: List[int], canales: List[str], nombres: Dict[int, str]) -> AsyncIterator[str]:
    total, enviados, fallidos = len(ids), 0, []
    for n, cid in enumerate(ids, 1):
        r = await _enviar_uno(perfil, cid, canales, nombres.get(cid) or f"Cliente {cid}")
        if r["ok"]:
            enviados += 1
        else:
            fallidos.append({"id": cid, "nombre": r["nombre"], "motivo": r["motivo"]})
        yield _evento({"t": "prog", "n": n, "total": total, "enviados": enviados, "fallidos": len(fallidos), "cliente": r})
        await asyncio.sleep(0)
    yield _evento({"t": "fin", "total": total, "enviados": enviados, "fallidos": fallidos})


@router.post("/api/cotizaciones/lote/enviar")
async def api_enviar_lote(request: Request):
    perfil, error = await _autorizar(request)
    if error:
        return error
    try:
        cuerpo = await _cuerpo(request)
        ids = ids_del_cuerpo(cuerpo)
        canales = cuerpo.get("canales")
        if not isinstance(canales, list) or not canales or any(c not in cot_router.CANALES for c in canales):
            return Response(content="Elige al menos un canal: correo o WhatsApp.", status_code=400, media_type="text/plain")
        canales = [c for c in cot_router.CANALES if c in canales]
        nombres = await _nombres(ids)
    except Exception as e:
        return Response(content=mensaje_publico(e, "api_enviar_lote"), status_code=e.status_code if isinstance(e, AppError) else 500, media_type="text/plain")
    return StreamingResponse(_flujo(perfil, ids, canales, nombres), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
