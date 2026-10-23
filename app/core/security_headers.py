"""Cabeceras de seguridad que se agregan a todas las respuestas (nosniff, no incrustar en marcos, política de referencia, XSS y,
solo si la petición llegó por https, HSTS).

Movido sin cambios desde main.py (Fase 6, tramo 3, lote L10).
"""
from starlette.middleware.base import BaseHTTPMiddleware


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
