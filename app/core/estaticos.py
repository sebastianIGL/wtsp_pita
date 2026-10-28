"""Archivos estáticos del frontend, sin las plantillas internas (Fase 7b, grupo 2).

El sitio público se arma con plantillas Jinja (``home``, ``blog``, ``como-funciona``, ``subsidios``, ``viviendas``) y fragmentos en
``partials/``. Solo deben verse ya renderizadas desde sus rutas; servidas como archivo suelto mostrarían las etiquetas ``{% %}`` crudas.
Todo lo demás del frontend (CRM, panel, login, imágenes…) se sirve igual que antes.
"""
import posixpath

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles

PAGINAS_JINJA = frozenset({"home.html", "blog.html", "como-funciona.html", "subsidios.html", "viviendas.html"})
CARPETA_DE_FRAGMENTOS = "partials"


def es_interna(ruta: str) -> bool:
    """True si la ruta (relativa a ``frontend/``) es una plantilla interna. No distingue mayúsculas ni ``..``/``//``."""
    limpia = posixpath.normpath("/" + ruta.replace("\\", "/")).lstrip("/").lower()
    return limpia in PAGINAS_JINJA or limpia == CARPETA_DE_FRAGMENTOS or limpia.startswith(CARPETA_DE_FRAGMENTOS + "/")


class FrontendEstatico(StaticFiles):
    async def get_response(self, path, scope):
        if es_interna(path):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)
