"""Funciones del sitio público que el dueño decidió ocultar por ahora.

Con ``False`` la función no aparece en las páginas y su ruta de la API responde 404 (no hay nada que la use ni correos que recibir). El código
se conserva para cuando se retomen: cambiar a ``True`` y desplegar las muestra de nuevo. Las páginas reciben estos valores como
``mostrar_contacto_empresas``, etc. (ver ``_contexto_comun`` en ``app/routers/paginas.py``)."""

CONTACTO_EMPRESAS = False     # sección «Para inmobiliarias» de la portada y la ruta /api/contacto
