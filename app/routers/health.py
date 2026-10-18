"""Salud del proceso.

Solo confirma que el proceso responde. NO consulta Supabase ni ningún servicio externo: un
healthcheck que dependa de terceros reiniciaría la aplicación por una caída ajena.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
