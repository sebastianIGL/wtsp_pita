"""Funciones del sitio público que el dueño decidió ocultar por ahora.

Con ``False`` la función no aparece en las páginas y su ruta de la API responde 404 (no hay nada que la use ni correos que recibir). El código
se conserva para cuando se retomen: cambiar a ``True`` y desplegar las muestra de nuevo. Las páginas reciben estos valores como
``mostrar_contacto_empresas``, etc. (ver ``_contexto_comun`` en ``app/routers/paginas.py``)."""

# Proyectos que NO se muestran en el sitio público (vitrina de la portada, /viviendas, fichas /viviendas/{slug} y sitemap): los de una inmobiliaria o
# empresa cuyo nombre contenga alguno de estos textos (sin importar mayúsculas ni tildes). Siguen activos en el CRM y para el asistente de WhatsApp.
# Vacía = no se oculta ninguno.
# «prueba inmobiliaria» agrupa los proyectos de prueba («prueba proyecto», «prueba-tipologia»).
PROYECTOS_OCULTOS_DEL_SITIO = ("subsidios.cl", "prueba inmobiliaria")

CONTACTO_EMPRESAS = False     # sección «Para inmobiliarias» de la portada y la ruta /api/contacto
NEWSLETTER = False            # suscripción del pie y del blog y la ruta /api/newsletter (pendiente de rehacer: envío real, baja, texto)
FUNCIONALIDADES = False       # bloque «Funcionalidades» de la portada (escrito para inmobiliarias: el sitio público es solo informativo)
