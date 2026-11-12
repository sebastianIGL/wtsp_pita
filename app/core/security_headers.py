"""Cabeceras de seguridad que se agregan a todas las respuestas (nosniff, no incrustar en marcos (salvo, desde el mismo sitio, el CRM y las herramientas), política de referencia, XSS y,
solo si la petición llegó por https, HSTS).

Movido sin cambios desde main.py (Fase 6, tramo 3, lote L10).
"""
from starlette.middleware.base import BaseHTTPMiddleware

# Las dos pantallas que el panel (/panel) muestra dentro de su marco (el CRM y las herramientas) pueden incrustarse, pero solo desde este mismo sitio.
PAGINAS_EN_MARCO = ("/index.html", "/herramientas.html")


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "SAMEORIGIN" if request.url.path in PAGINAS_EN_MARCO else "DENY"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
