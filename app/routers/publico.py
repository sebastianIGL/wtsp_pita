"""Rutas públicas del sitio (sin sesión): la vitrina de proyectos de la landing (con caché de 5 minutos), el formulario de
contacto y la suscripción al newsletter. Contacto y newsletter tienen límite de peticiones por IP.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import random
import time
import logging
from html import escape as html_escape
from app.clients.gmail import _gmail_send
from app.clients.supabase import _supabase_request
from app.core.cache_proyectos import _CACHE_TTL, _cache_proyectos
from app.core.config import get_settings
from app.core.rate_limit import _rate_limit_ok, ip_cliente
from fastapi import APIRouter, Request, Response
from typing import Any, Dict, List

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


@router.get("/api/proyectos-landing")
async def api_proyectos_landing(limite: int = 12):
    """Proyectos públicos para la vitrina (sin autenticación): activos, nuevos primero y luego
    semi-nuevos (solo con fotos). Con límite menor al total, los semi-nuevos se muestrean al azar."""
    limite = max(1, min(int(limite), 150))
    now = time.time()
    if not (_cache_proyectos["data"] and now - _cache_proyectos["ts"] < _CACHE_TTL):
        rows = await _supabase_request("GET", "/Proyecto", params={
            "select": "id,nombre,imagen_url,imagenes,ubicacion,activo,condicion_propiedad,"
                      "Tipologia(valor_uf,dormitorios,banos,superficie_util_m2)",
            "order": "nombre.asc",
            "limit": "1000",
        }) or []
        nuevos: List[Dict[str, Any]] = []
        semi: List[Dict[str, Any]] = []
        for r in rows:
            if not r.get("nombre") or r.get("activo") is False:
                continue
            condicion = r.get("condicion_propiedad") or "nueva"
            imagenes = [u for u in (r.get("imagenes") or []) if isinstance(u, str)]
            if r.get("imagen_url") and r["imagen_url"] not in imagenes:
                imagenes.insert(0, r["imagen_url"])
            item = {
                "id":        r.get("id"),
                "nombre":    r.get("nombre") or "Proyecto",
                "imagen":    imagenes[0] if imagenes else None,
                "imagenes":  imagenes,
                "ubicacion": r.get("ubicacion") or "",
                "condicion": condicion,
            }
            if condicion == "nueva":
                nuevos.append(item)
            elif imagenes:
                tip = (r.get("Tipologia") or [None])[0] or {}
                item.update({
                    "precio_uf":   tip.get("valor_uf"),
                    "dormitorios": tip.get("dormitorios"),
                    "banos":       tip.get("banos"),
                    "m2":          tip.get("superficie_util_m2"),
                })
                semi.append(item)
        _cache_proyectos["data"] = {"nuevos": nuevos, "semi": semi}
        _cache_proyectos["ts"] = now

    nuevos = _cache_proyectos["data"]["nuevos"]
    semi = _cache_proyectos["data"]["semi"]
    cupo = max(0, limite - len(nuevos))
    elegidos_semi = semi if cupo >= len(semi) else random.sample(semi, cupo)
    return {"proyectos": (nuevos + elegidos_semi)[:limite], "total": len(nuevos) + len(semi)}


@router.post("/api/contacto")
async def api_contacto(request: Request):
    """Recibe formulario de contacto de la landing y notifica por email."""
    ip = ip_cliente(request)
    if not _rate_limit_ok(f"contacto:{ip}", max_req=5, window=60):
        return Response(content="Demasiadas solicitudes, intenta en un minuto", status_code=429)

    body = await request.json()
    nombre   = (body.get("nombre")   or "").strip()
    correo   = (body.get("correo")   or "").strip()
    telefono = (body.get("telefono") or "").strip()
    empresa  = (body.get("empresa")  or "").strip()
    mensaje  = (body.get("mensaje")  or "").strip()

    if not nombre or not correo:
        return Response(content="nombre y correo son requeridos", status_code=422)

    logger.info("📩 Contacto landing | %s | %s | %s", nombre, correo, empresa)

    destino = get_settings().email_admin
    if destino:
        try:
            cuerpo = f"""<h2>Nuevo contacto desde la landing</h2>
<p><b>Nombre:</b> {html_escape(nombre)}<br>
<b>Correo:</b> {html_escape(correo)}<br>
<b>Teléfono:</b> {html_escape(telefono or '—')}<br>
<b>Empresa:</b> {html_escape(empresa or '—')}</p>
<p><b>Mensaje:</b><br>{html_escape(mensaje or '—')}</p>"""
            await _gmail_send(
                from_addr=(get_settings().email_admin or ""),
                to=[destino],
                subject=f"[QueSubsidio] Contacto desde la landing — {nombre}",
                html=cuerpo,
            )
        except Exception as exc:
            logger.warning("No se pudo enviar email de contacto: %s", exc)

    return {"ok": True}


@router.post("/api/newsletter")
async def api_newsletter(request: Request):
    """Guarda suscripción al blog/newsletter en Supabase."""
    ip = ip_cliente(request)
    if not _rate_limit_ok(f"newsletter:{ip}", max_req=3, window=60):
        return Response(content="Demasiadas solicitudes, intenta en un minuto", status_code=429)

    body = await request.json()
    email = (body.get("email") or "").strip().lower()

    if not email or "@" not in email:
        return Response(content="email inválido", status_code=422)

    logger.info("📧 Newsletter suscripción: %s", email)

    # Guardar en Supabase (upsert para evitar duplicados)
    try:
        await _supabase_request(
            "POST", "/newsletter",
            json={"email": email},
            extra_headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
        )
    except Exception as exc:
        logger.warning("No se pudo guardar newsletter en Supabase: %s", exc)

    return {"ok": True}
