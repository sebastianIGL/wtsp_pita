"""Páginas del sitio: las públicas con plantilla (inicio, cómo funciona, subsidios, viviendas, blog), las que sirven un
archivo del frontend (panel, perfil, restablecer contraseña) y las redirecciones de las páginas antiguas hacia /panel y /.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services.ficha_proyecto import ORIGENES, obtener_ficha, slugs_de_proyectos_nuevos
from app.services.seo import PAGINAS_PUBLICAS, SITIO_URL, construir_robots, construir_sitemap

logger = logging.getLogger("wtsp_pita")

router = APIRouter()


templates = Jinja2Templates(directory="frontend")


# ── Landing pública ───────────────────────────────────────────────────────────
@router.get("/")
async def page_home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "active": "inicio",
        "page_title": "QueSubsidio — Subsidios Habitacionales en Chile",
        "page_description": "Comprar tu propiedad con subsidio aún es posible. QueSubsidio acerca la vivienda a más familias chilenas con información clara sobre DS19, DS49 y DS1 T2/T3.",
        "page_path": "/",
        "supabase_needed": True,
    })


@router.get("/como-funciona")
async def page_como_funciona(request: Request):
    return templates.TemplateResponse(request, "como-funciona.html", {
        "active": "como-funciona",
        "page_title": "Cómo funciona — QueSubsidio",
        "page_description": "Conoce el proceso paso a paso para postular a un subsidio habitacional en Chile con la ayuda de QueSubsidio.",
        "page_path": "/como-funciona",
    })


@router.get("/subsidios")
async def page_subsidios(request: Request):
    return templates.TemplateResponse(request, "subsidios.html", {
        "active": "subsidios",
        "page_title": "Subsidios Habitacionales — QueSubsidio",
        "page_description": "Conoce todos los subsidios disponibles: DS19, DS49, DS1 T2/T3 y más. Encuentra el que se ajusta a tu situación.",
        "page_path": "/subsidios",
    })


@router.get("/viviendas")
async def page_viviendas(request: Request):
    return templates.TemplateResponse(request, "viviendas.html", {
        "active": "viviendas",
        "page_title": "Viviendas con Subsidio — QueSubsidio",
        "page_description": "Explora proyectos inmobiliarios disponibles con subsidio habitacional en todo Chile.",
        "page_path": "/viviendas",
    })


@router.get("/viviendas/{slug}")
async def page_ficha_proyecto(request: Request, slug: str, o: str = ""):
    """Ficha pública de un proyecto (solo activos). ``?o=viviendas|bot`` dice desde dónde llegó la visita; solo se registra en el log."""
    ficha = await obtener_ficha(slug)
    origen = o if o in ORIGENES else "directo"
    if ficha is None:
        logger.info("Ficha de proyecto | slug=%s | origen=%s | no encontrada", slug[:90], origen)
        return templates.TemplateResponse(request, "proyecto.html", {
            "active": "viviendas", "ficha": None, "robots": "noindex, follow",
            "page_title": "Proyecto no encontrado — QueSubsidio",
            "page_description": "Este proyecto no está disponible. Mira las viviendas con subsidio que sí lo están.",
            "page_path": "/viviendas",
        }, status_code=404)
    logger.info("Ficha de proyecto | slug=%s | origen=%s", ficha["slug"], origen)
    sitio = SITIO_URL
    return templates.TemplateResponse(request, "proyecto.html", {
        "active": "viviendas", "ficha": ficha,
        # Solo los proyectos nuevos se indexan: las fichas de semi-nuevos (copias de publicaciones de terceros) llevan noindex.
        "robots": "index, follow" if ficha["nuevo"] else "noindex, follow",
        "page_title": ficha["titulo"], "page_description": ficha["descripcion"], "page_path": ficha["ruta"],
        "json_ld": {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Inicio", "item": f"{sitio}/"},
            {"@type": "ListItem", "position": 2, "name": "Viviendas", "item": f"{sitio}/viviendas"},
            {"@type": "ListItem", "position": 3, "name": ficha["nombre"], "item": f"{sitio}{ficha['ruta']}"},
        ]},
    })


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """Navegadores y buscadores piden /favicon.ico aunque la página declare otro ícono: se les sirve el mismo (PNG, como en el <link>)."""
    return FileResponse("frontend/img/favicon.png", media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    """Páginas públicas + la ficha de cada proyecto nuevo activo (las de semi-nuevos llevan noindex y no van). Caché de 5 minutos en
    el índice de proyectos. Si la base falla se responde 503 (no un sitemap sin proyectos que haría creer que desaparecieron)."""
    try:
        slugs = await slugs_de_proyectos_nuevos()
    except Exception as e:
        logger.warning("Sitemap: no se pudieron leer los proyectos (%s)", type(e).__name__)
        return PlainTextResponse("Servicio no disponible", status_code=503, headers={"Retry-After": "300"})
    xml = construir_sitemap(list(PAGINAS_PUBLICAS) + [f"/viviendas/{s}" for s in slugs])
    return Response(content=xml, media_type="application/xml; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    return PlainTextResponse(construir_robots(), headers={"Cache-Control": "public, max-age=3600"})


@router.get("/blog")
async def page_blog(request: Request):
    return templates.TemplateResponse(request, "blog.html", {
        "active": "blog",
        "page_title": "Blog — QueSubsidio",
        "page_description": "Artículos, guías y novedades sobre subsidios habitacionales y el mercado inmobiliario chileno.",
        "page_path": "/blog",
    })


# ── Rutas de páginas (URLs limpias sin .html) ────────────────────────────────
@router.get("/reset-password")
async def page_reset_password():
    return FileResponse("frontend/reset-password.html")


@router.get("/panel")
async def page_panel():
    return FileResponse("frontend/panel.html")


@router.get("/empresas")
async def page_empresas():
    return RedirectResponse(url="/panel", status_code=302)


@router.get("/empresas.html")
async def page_empresas_html():
    return RedirectResponse(url="/panel", status_code=302)


@router.get("/login")
async def page_login():
    return RedirectResponse(url="/", status_code=301)


@router.get("/inmobiliaria")
async def page_inmobiliaria():
    return RedirectResponse(url="/panel", status_code=302)


@router.get("/inmobiliaria.html")
async def page_inmobiliaria_html():
    return RedirectResponse(url="/panel", status_code=302)


@router.get("/proyecto")
async def page_proyecto():
    return RedirectResponse(url="/panel", status_code=302)


@router.get("/proyecto.html")
async def page_proyecto_html():
    return RedirectResponse(url="/panel", status_code=302)


@router.get("/perfil")
async def page_perfil():
    return FileResponse("frontend/mi_camino.html")
