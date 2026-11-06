"""SEO técnico del sitio público: el contenido de ``/sitemap.xml`` y de ``/robots.txt``.

* sitemap.xml: las páginas públicas (incluidas las tres legales) y la ficha de cada proyecto **nuevo** activo. Las fichas de semi-nuevos no van (llevan noindex), ni las
  páginas privadas (CRM, login, API).
* robots.txt: deja pasar todo salvo lo privado y apunta al sitemap. Las fichas de semi-nuevos NO se bloquean aquí a propósito: para que
  un buscador respete su noindex tiene que poder leer la página. Se deja pasar ``/api/proyectos-landing`` porque /viviendas arma sus
  tarjetas (y los enlaces a las fichas) con esa llamada y un buscador que no pueda hacerla vería la página vacía.
  robots.txt no protege nada: es una indicación para buscadores; lo privado se protege con sesión.
"""
from typing import Iterable
from xml.sax.saxutils import escape

SITIO_URL = "https://quesubsidio.cl"          # el mismo que usan canonical y og:url (frontend/partials/_head.html)
PAGINAS_PUBLICAS = ("/", "/como-funciona", "/subsidios", "/viviendas", "/blog", "/terminos", "/privacidad", "/cookies")
API_PUBLICA = ("/api/proyectos-landing",)
RUTAS_PRIVADAS = ("/api/", "/panel", "/admin.html", "/index.html", "/login.html", "/reset-password", "/webhook", "/prospectos/")


def construir_sitemap(rutas: Iterable[str]) -> str:
    """Sitemap XML (protocolo sitemaps.org) con una URL absoluta por ruta, sin repetidas y en el orden recibido."""
    vistas, urls = set(), []
    for ruta in rutas:
        if ruta not in vistas:
            vistas.add(ruta)
            urls.append(f"  <url><loc>{escape(SITIO_URL + ruta)}</loc></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>\n")


def construir_robots() -> str:
    # Sin «Allow: /»: permitir es lo normal, y esa línea (la primera que coincide con todo) haría que los lectores que toman la primera
    # coincidencia, en vez de la más específica como Google, dejaran pasar también lo bloqueado.
    lineas = ["User-agent: *"]
    lineas += [f"Allow: {r}" for r in API_PUBLICA]          # antes de los Disallow: los lectores que toman la primera coincidencia la ven primero
    lineas += [f"Disallow: {r}" for r in RUTAS_PRIVADAS]
    lineas += ["", f"Sitemap: {SITIO_URL}/sitemap.xml", ""]
    return "\n".join(lineas)
