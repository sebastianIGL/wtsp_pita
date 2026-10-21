"""Páginas del sitio: las públicas con plantilla (inicio, cómo funciona, subsidios, viviendas, blog), las que sirven un
archivo del frontend (panel, perfil, restablecer contraseña) y las redirecciones de las páginas antiguas hacia /panel y /.

Movido desde main.py (Fase 6, tramo 3). Lo único que cambia del código de cada ruta es el decorador (@app → @router).
"""
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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
    return FileResponse("frontend/opcion_c.html")
