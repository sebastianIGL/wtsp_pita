"""«Mi Camino»: a qué subsidio puede acceder una persona (público, sin guardar nada) y el avance guardado en su cuenta (con sesión).

El avance usa la sesión de la familia (su token de Supabase); no es el CRM, así que no pide perfil de personal. La sesión se verifica con la clave del servidor (como el CRM): no hace falta la clave pública.
Si las tablas aún no existen (falta correr el SQL), las rutas de avance responden 503 y la página sigue guardando solo en el dispositivo.
"""
import logging

from fastapi import APIRouter, Request, Response

from app.clients.supabase_auth import _uid_desde_jwt
from app.core.rate_limit import _rate_limit_ok, ip_cliente
from app.services import avance_camino, elegibilidad, mi_camino
from app.services import uf as servicio_uf

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


def _json(datos, status: int = 200) -> Response:
    import json
    return Response(content=json.dumps(datos, ensure_ascii=False), status_code=status, media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/api/mi-camino/evaluar")
async def api_mi_camino_evaluar(request: Request):
    """Dice a qué subsidios puede acceder la persona según sus respuestas. Público; no guarda ni registra las respuestas (solo se compara con las fichas publicadas)."""
    if not _rate_limit_ok(f"mi_camino_evaluar:{ip_cliente(request)}", max_req=30, window=60):
        return Response(content="Demasiadas solicitudes, intenta en un minuto", status_code=429)
    try:
        cuerpo = await request.json()
    except Exception:
        return _json({"error": "Faltan las respuestas."}, 422)
    if isinstance(cuerpo, dict) and cuerpo.get("ahorro_uf") is None and cuerpo.get("ahorro_clp") is not None:      # el ahorro en pesos se pasa a UF con la UF del día
        clp = cuerpo["ahorro_clp"]
        if isinstance(clp, bool) or not isinstance(clp, (int, float)) or clp != clp or clp < 0 or clp > 10 ** 11:
            return _json({"error": "El ahorro debe ser un número válido."}, 422)
        try:
            uf, _ = await servicio_uf.uf_del_dia()
        except Exception as e:
            logger.warning("Mi Camino | no se pudo leer la UF (%s)", type(e).__name__)
            return _json({"error": "No pudimos convertir tu ahorro a UF ahora. Escríbelo en UF o inténtalo en unos minutos."}, 503)
        cuerpo = {**cuerpo, "ahorro_uf": clp / uf if uf else 0}
    r, error = elegibilidad.validar(cuerpo)
    if error:
        return _json({"error": error}, 422)
    try:
        fichas = await mi_camino._subsidios()
    except Exception as e:
        logger.warning("Mi Camino | no se pudieron leer las fichas para evaluar (%s)", type(e).__name__)
        return _json({"error": "No pudimos revisar los subsidios en este momento. Intenta de nuevo en unos minutos."}, 503)
    return _json(elegibilidad.evaluar(cuerpo, fichas))


async def _uid(request: Request):
    if not _rate_limit_ok(f"mi_camino_avance:{ip_cliente(request)}", max_req=60, window=60):
        return None, Response(content="Demasiadas solicitudes, intenta en un minuto", status_code=429)
    uid = await _uid_desde_jwt(request)
    return (uid, None) if uid else (None, Response(content="no autorizado", status_code=401))


@router.get("/api/mi-camino/avance")
async def api_mi_camino_avance_leer(request: Request):
    uid, error = await _uid(request)
    if error:
        return error
    try:
        return _json(await avance_camino.leer(uid))
    except Exception as e:
        logger.warning("Mi Camino | no se pudo leer el avance (%s)", type(e).__name__)
        return _json({"error": "El guardado en tu cuenta no está disponible por ahora."}, 503)


@router.put("/api/mi-camino/avance")
async def api_mi_camino_avance_guardar(request: Request):
    uid, error = await _uid(request)
    if error:
        return error
    try:
        cuerpo = await request.json()
    except Exception:
        return _json({"error": "Faltan los datos."}, 422)
    datos, motivo = avance_camino.validar(cuerpo)
    if motivo:
        return _json({"error": motivo}, 422)
    try:
        return _json(await avance_camino.sincronizar(uid, datos))
    except Exception as e:
        logger.warning("Mi Camino | no se pudo guardar el avance (%s)", type(e).__name__)
        return _json({"error": "El guardado en tu cuenta no está disponible por ahora."}, 503)


@router.delete("/api/mi-camino/avance")
async def api_mi_camino_avance_borrar(request: Request):
    uid, error = await _uid(request)
    if error:
        return error
    try:
        await avance_camino.borrar(uid)
    except Exception as e:
        logger.warning("Mi Camino | no se pudo borrar el avance (%s)", type(e).__name__)
        return _json({"error": "No pudimos borrar tu avance ahora. Intenta de nuevo."}, 503)
    return _json({"ok": True})
