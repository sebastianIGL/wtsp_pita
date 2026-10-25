"""Catálogo (3/3): tipologías, etapas y la relación etapa-tipología (con su stock). Listados filtrables por proyecto,
inmobiliaria o empresa; los selects y listas de campos permitidos viajan con las rutas.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
from app.clients.supabase import _supabase_request
from app.clients.supabase_auth import _get_usuario_actual
from app.core.errors import respuesta_error
from app.core.permisos import _solo_admin
from app.services.proyectos import obtener_proyecto_por_id
from fastapi import APIRouter, Request, Response
from typing import Any, Dict

router = APIRouter()


_TIPOLOGIA_SELECT = "id,proyecto_id,Proyecto(nombre),nombre,dormitorios,banos,superficie_util_m2,terreno_m2,valor_uf,monto_subsidio,tipo_subsidio,estado,cuotas_ahorro,estacionamientos,bodegas,EtapaTipologia(id,stock,Etapa(id,nombre,fecha_entrega,estado))"


@router.get("/api/tipologias")
async def api_listar_tipologias(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _TIPOLOGIA_SELECT, "order": "nombre.asc"}

    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    elif inmobiliaria_id:
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"}) or []
        ids = ",".join(p["id"] for p in prows)
        if not ids: return []
        params["proyecto_id"] = f"in.({ids})"
    elif empresa_id:
        inms = await _supabase_request("GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
        inm_ids = ",".join(str(i["id"]) for i in inms)
        if not inm_ids: return []
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"}) or []
        ids = ",".join(p["id"] for p in prows)
        if not ids: return []
        params["proyecto_id"] = f"in.({ids})"

    rows = await _supabase_request("GET", "/Tipologia", params=params)
    return rows or []


_CAMPOS_TIPOLOGIA = ("dormitorios", "banos", "superficie_util_m2", "terreno_m2",
                     "valor_uf", "monto_subsidio",
                     "tipo_subsidio", "estado", "cuotas_ahorro",
                     "estacionamientos", "bodegas")


@router.post("/api/tipologias")
async def api_crear_tipologia(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body = await request.json()
        proyecto_id = body.get("proyecto_id")
        nombre      = (body.get("nombre") or "").strip()
        if not proyecto_id or not nombre:
            return Response(content="Faltan campos obligatorios", status_code=400)
        if not await obtener_proyecto_por_id(proyecto_id):
            return Response(content="Proyecto no encontrado", status_code=404)
        payload: Dict[str, Any] = {"proyecto_id": proyecto_id, "nombre": nombre}
        for campo in _CAMPOS_TIPOLOGIA:
            if body.get(campo) is not None:
                payload[campo] = body[campo]
        row = await _supabase_request("POST", "/Tipologia",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_tipologia")


@router.patch("/api/tipologias/{tip_id}")
async def api_editar_tipologia(tip_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body = await request.json()
        payload: Dict[str, Any] = {}
        if body.get("nombre"): payload["nombre"] = body["nombre"].strip()
        for campo in _CAMPOS_TIPOLOGIA:
            if campo in body: payload[campo] = body[campo]
        if not payload:
            return Response(content="Nada que actualizar", status_code=400)
        await _supabase_request("PATCH", "/Tipologia",
            params={"id": f"eq.{tip_id}"},
            json=payload, extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_tipologia")


@router.delete("/api/tipologias/{tip_id}")
async def api_eliminar_tipologia(tip_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    await _supabase_request("DELETE", "/Tipologia",
        params={"id": f"eq.{tip_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


_ETAPA_SELECT = "id,proyecto_id,Proyecto(nombre),nombre,descripcion,fecha_entrega,estado,tipo_ahorro,num_cuotas,EtapaTipologia(id,stock,Tipologia(id,nombre,dormitorios,banos))"


_CAMPOS_ETAPA = ("nombre", "descripcion", "fecha_entrega", "estado", "tipo_ahorro", "num_cuotas")


@router.get("/api/etapas")
async def api_listar_etapas(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    proyecto_id     = request.query_params.get("proyecto_id")
    inmobiliaria_id = request.query_params.get("inmobiliaria_id")
    empresa_id      = request.query_params.get("empresa_id")
    params: Dict[str, str] = {"select": _ETAPA_SELECT, "order": "fecha_entrega.asc"}

    if proyecto_id:
        params["proyecto_id"] = f"eq.{proyecto_id}"
    elif inmobiliaria_id:
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"eq.{inmobiliaria_id}", "select": "id"}) or []
        ids = ",".join(str(p["id"]) for p in prows)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"
    elif empresa_id:
        inms = await _supabase_request("GET", "/Inmobiliaria",
            params={"empresa_id": f"eq.{empresa_id}", "select": "id"}) or []
        inm_ids = ",".join(str(i["id"]) for i in inms)
        if not inm_ids:
            return []
        prows = await _supabase_request("GET", "/Proyecto",
            params={"inmobiliaria_id": f"in.({inm_ids})", "select": "id"}) or []
        ids = ",".join(str(p["id"]) for p in prows)
        if not ids:
            return []
        params["proyecto_id"] = f"in.({ids})"

    rows = await _supabase_request("GET", "/Etapa", params=params)
    return rows or []


@router.post("/api/etapas")
async def api_crear_etapa(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body = await request.json()
        proyecto_id = body.get("proyecto_id")
        if not proyecto_id:
            return Response(content="Falta proyecto_id", status_code=400)
        if not await obtener_proyecto_por_id(proyecto_id):
            return Response(content="Proyecto no encontrado", status_code=404)
        payload: Dict[str, Any] = {"proyecto_id": proyecto_id}
        for campo in _CAMPOS_ETAPA:
            if campo in body:
                payload[campo] = body[campo] or None
        row = await _supabase_request("POST", "/Etapa",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_etapa")


@router.patch("/api/etapas/{etapa_id}")
async def api_editar_etapa(etapa_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body = await request.json()
        payload: Dict[str, Any] = {}
        for campo in _CAMPOS_ETAPA:
            if campo in body:
                payload[campo] = body[campo] or None
        if not payload:
            return Response(content="Nada que actualizar", status_code=400)
        await _supabase_request("PATCH", "/Etapa",
            params={"id": f"eq.{etapa_id}"},
            json=payload, extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_etapa")


@router.delete("/api/etapas/{etapa_id}")
async def api_eliminar_etapa(etapa_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    await _supabase_request("DELETE", "/Etapa",
        params={"id": f"eq.{etapa_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}


_ETAPA_TIP_SELECT = "id,etapa_id,tipologia_id,stock,Etapa(id,nombre,fecha_entrega,estado),Tipologia(id,nombre,dormitorios,banos,valor_uf,tipo_subsidio)"


@router.get("/api/etapa-tipologia")
async def api_listar_etapa_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    tipologia_id = request.query_params.get("tipologia_id")
    etapa_id     = request.query_params.get("etapa_id")
    params: Dict[str, str] = {"select": _ETAPA_TIP_SELECT}
    if tipologia_id:
        params["tipologia_id"] = f"eq.{tipologia_id}"
    elif etapa_id:
        params["etapa_id"] = f"eq.{etapa_id}"
    else:
        return Response(content="Se requiere tipologia_id o etapa_id", status_code=400)
    rows = await _supabase_request("GET", "/EtapaTipologia", params=params)
    return rows or []


@router.get("/api/etapa-tipologia/{et_id}")
async def api_get_etapa_tipologia(et_id: int, request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    rows = await _supabase_request("GET", "/EtapaTipologia",
        params={"id": f"eq.{et_id}", "select": "id,tipologia_id,etapa_id", "limit": "1"}) or []
    return rows[0] if rows else {}


@router.post("/api/etapa-tipologia/find-or-create")
async def api_find_or_create_etapa_tipologia(request: Request):
    if not await _get_usuario_actual(request):
        return Response(content="Unauthorized", status_code=401)
    try:
        body = await request.json()
        tipologia_id = body.get("tipologia_id")
        etapa_id     = body.get("etapa_id")
        if not tipologia_id or not etapa_id:
            return Response(content="Faltan tipologia_id o etapa_id", status_code=400)
        existing = await _supabase_request("GET", "/EtapaTipologia",
            params={"tipologia_id": f"eq.{tipologia_id}", "etapa_id": f"eq.{etapa_id}",
                    "select": "id", "limit": "1"}) or []
        if existing:
            return {"id": existing[0]["id"]}
        # Antes de crear un vínculo nuevo: la tipología y la etapa deben existir y ser del MISMO proyecto (para todos los roles)
        tip = await _supabase_request("GET", "/Tipologia",
            params={"id": f"eq.{tipologia_id}", "select": "id,proyecto_id", "limit": "1"}) or []
        etapa = await _supabase_request("GET", "/Etapa",
            params={"id": f"eq.{etapa_id}", "select": "id,proyecto_id", "limit": "1"}) or []
        if not tip or not etapa:
            return Response(content="Tipología o etapa no encontrada", status_code=404)
        proyecto_tip = tip[0].get("proyecto_id")
        if not proyecto_tip or str(proyecto_tip) != str(etapa[0].get("proyecto_id")):
            return Response(content="La tipología y la etapa deben ser del mismo proyecto", status_code=400)
        row = await _supabase_request("POST", "/EtapaTipologia",
            json={"etapa_id": int(etapa_id), "tipologia_id": int(tipologia_id), "stock": 1},
            extra_headers={"Prefer": "return=representation"})
        return {"id": (row[0]["id"] if isinstance(row, list) and row else row["id"])}
    except Exception as e:
        return respuesta_error(e, contexto="api_find_or_create_etapa_tipologia")


@router.post("/api/etapa-tipologia")
async def api_crear_etapa_tipologia(request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body = await request.json()
        etapa_id     = body.get("etapa_id")
        tipologia_id = body.get("tipologia_id")
        stock        = body.get("stock", 0)
        if not etapa_id or not tipologia_id:
            return Response(content="Faltan etapa_id o tipologia_id", status_code=400)
        payload = {"etapa_id": int(etapa_id), "tipologia_id": int(tipologia_id), "stock": int(stock or 0)}
        row = await _supabase_request("POST", "/EtapaTipologia",
            json=payload, extra_headers={"Prefer": "return=representation"})
        return row[0] if isinstance(row, list) and row else row
    except Exception as e:
        return respuesta_error(e, contexto="api_crear_etapa_tipologia")


@router.patch("/api/etapa-tipologia/{et_id}")
async def api_editar_etapa_tipologia(et_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    try:
        body = await request.json()
        stock = body.get("stock", 0)
        await _supabase_request("PATCH", "/EtapaTipologia",
            params={"id": f"eq.{et_id}"},
            json={"stock": int(stock or 0)},
            extra_headers={"Prefer": "return=minimal"})
        return {"ok": True}
    except Exception as e:
        return respuesta_error(e, contexto="api_editar_etapa_tipologia")


@router.delete("/api/etapa-tipologia/{et_id}")
async def api_eliminar_etapa_tipologia(et_id: int, request: Request):
    perfil = await _get_usuario_actual(request)
    if not perfil:
        return Response(content="Unauthorized", status_code=401)
    if not _solo_admin(perfil):
        return Response(content="Solo administradores", status_code=403)
    await _supabase_request("DELETE", "/EtapaTipologia",
        params={"id": f"eq.{et_id}"},
        extra_headers={"Prefer": "return=minimal"})
    return {"ok": True}
