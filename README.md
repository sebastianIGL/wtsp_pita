# wtsp_pita

Webhook mínimo con FastAPI para WhatsApp Cloud API (Meta Business).

**Flujo real (alto nivel)**
- Cliente envía WhatsApp → Meta dispara evento → tu webhook recibe JSON
- Tu backend decide respuesta → tu backend llama a Graph API → WhatsApp responde al cliente

## Requisitos
- Python 3.13 (el de Railway; fijado en `.python-version`). También probado en 3.11.

## Instalación
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Variables de entorno
Este proyecto lee variables desde el entorno. En desarrollo podés usar un archivo `.env`.

1) Copiá el ejemplo:
```bash
cp .env.example .env
```

2) Completá estas variables (nombres usados por el código):
- `WA_VERIFY_TOKEN`: lo elegís vos (una cadena secreta). Se carga en Meta al configurar el webhook y tiene que coincidir.
- `WA_ACCESS_TOKEN`: lo obtenés en Meta (token de acceso para Graph API) y sirve para enviar mensajes.
- `WA_PHONE_NUMBER_ID`: lo obtenés en la sección de WhatsApp Cloud API (Phone Number ID del número).
- `WA_GRAPH_VERSION`: opcional (por defecto `v22.0`).

Nota: el código también acepta `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` y `WHATSAPP_GRAPH_API_VERSION` por compatibilidad.

### Supabase (recomendado)
Este proyecto persiste leads, estado y mensajes en Supabase (Postgres) vía la API REST.

1) Crear un proyecto en Supabase

2) Crear las tablas
- Abrí Supabase Dashboard → SQL Editor
- Ejecutá el script: [supabase/schema.sql](supabase/schema.sql)

3) Configurar variables en Railway (o tu entorno)
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (solo backend; no exponer en frontend)

Opcional:
- `INGEST_API_KEY` (protege el endpoint `/prospectos/ingesta`)

## Ejecutar
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Estructura del código
- `main.py`: solo arma la aplicación (FastAPI, middleware, el webhook de WhatsApp y el montaje de los routers y del frontend).
  Ya no re-exporta nada: el código vive en `app/`.
- `app/routers/`: las rutas HTTP, una por área (clientes, documentos, empresas, proyectos, usuarios, ingesta, formulario, páginas…).
- `app/services/`: la lógica de negocio (webhook, avisos, buydepa, plantillas, prospectos, documentos, importación CSV…).
  `app/services/bot/` es el bot: `prompts.py` (las instrucciones por paso), `contexto.py`, `generacion.py` (llama al modelo) y
  `respuesta.py` (interpreta lo que contesta el modelo).
- `app/clients/`: acceso a servicios externos (Supabase, WhatsApp, Gmail, mindicador).
- `app/core/`: configuración, autorización, límites de tasa, logging, cabeceras de seguridad y utilidades comunes.
- `frontend/`: páginas HTML (las públicas con plantillas Jinja2 y `partials/`).
- `supabase/schema.sql`: referencia del esquema de la base. Las columnas `movendo_*` figuran como obsoletas: se conservan en la
  base pero ya no se usan.

## Ficha pública de un proyecto (`/viviendas/{slug}`)
- `app/services/ficha_proyecto.py` + `frontend/proyecto.html`. Muestra galería, etiquetas (subsidios y entrega), las tipologías
  **activas con stock** («Disponible» o «Últimas unidades» hasta 3 unidades; el número exacto nunca se muestra), «Cómo se paga»
  (ahorro mínimo, reserva, estacionamiento, sala piloto, solo lo que tenga dato), la inmobiliaria y el aviso de valores
  referenciales. Solo proyectos activos; si no existe o está inactivo responde 404 con página amable y `noindex`.
- **El slug se calcula del nombre, sin columna nueva** (`mirador-del-sol`; en los semi-nuevos, la dirección + el identificador de la
  fuente). Si dos proyectos dan el mismo slug, ambos llevan el comienzo de su id. **Límite conocido:** si se cambia el nombre de
  un proyecto su URL cambia; la versión completa guardará el slug en la base.
- **Indexación:** las fichas de proyectos nuevos se indexan; las de semi-nuevos (copias de publicaciones de terceros) llevan
  `noindex, follow` y no van en el sitemap, pero **no** se bloquean en `robots.txt` (para que el buscador pueda leer el noindex).
  La cabecera común acepta la variable `robots` (por defecto `index, follow`).
- `GET /sitemap.xml` (`app/services/seo.py`): las páginas públicas (`/`, `/como-funciona`, `/subsidios`, `/viviendas`, `/blog`, `/calculadora` y las tres legales: `/terminos`, `/privacidad`, `/cookies`) y la
  ficha de cada proyecto nuevo activo; si la base falla responde 503 en vez de un sitemap sin proyectos. `GET /robots.txt`: bloquea
  `/api/` (salvo `/api/proyectos-landing`, con la que /viviendas arma sus tarjetas), el CRM, el login y el webhook, y apunta al sitemap.
  robots.txt es una indicación para buscadores, no protege nada: lo privado se protege con sesión.
- Los datos se leen de Supabase con caché de 5 minutos (índice de slugs y ficha por proyecto). Cada visita deja una línea de log
  `Ficha de proyecto | slug=… | origen=viviendas|bot|directo` (el origen sale de `?o=`, con lista cerrada).

## Medición (Google Tag Manager) y cookies del sitio público
- Variable **`GTM_ID`** (formato `GTM-XXXXXXX`; sin ella, o con un valor que no tenga ese formato, **no cambia nada**: ninguna página carga
  nada de Google ni muestra aviso de cookies; el arranque avisa si la variable existe pero no sirve).
- Con `GTM_ID`, todas las páginas públicas (inicio, cómo funciona, subsidios, viviendas, blog, fichas de proyecto y las legales) muestran un
  aviso de cookies (`frontend/partials/_cookies.html`, incluido desde `_scripts_common.html`) con «Aceptar todas», «Solo necesarias» y
  «Configurar» (hoy una sola categoría opcional: **medición**; la publicidad no se ofrece porque solo se mide con Google Analytics). El CRM, el login y «Mi camino» nunca lo cargan.
- **Consent Mode v2:** todo parte denegado (`gtag('consent','default')` antes que nada). **Google Tag Manager solo se carga si la persona
  acepta la medición** (nunca antes, ni si rechaza); la elección se guarda 12 meses en el navegador (`localStorage`, clave
  `qs_cookies`) y se puede cambiar desde «Configurar cookies» en el pie y en `/cookies`. Retirar el consentimiento borra las cookies de
  medición (`_ga*`, `_gid`, `_gat*`, `_gcl_*`, `_gac_*`, `_fbp`, `_fbc`). Si cambia el texto o las categorías, subir `VERSION` en el parcial
  para volver a preguntar a todos (hoy `VERSION = 2`: las elecciones de la versión 1, que traían «publicidad», se ignoran y se vuelve a preguntar).
- Dentro de Tag Manager hay que configurar GA4 y las etiquetas con **comprobaciones de consentimiento** (`analytics_storage`; `ad_storage`, `ad_user_data` y
  `ad_personalization` quedan siempre denegados: si algún día se hacen campañas hay que volver a ofrecer la categoría). El evento
  `qs_cookies_actualizadas` (`{medicion}`) se empuja al `dataLayer` en cada elección.
- **Eventos de medición** (`window.QSMedir`, definida solo en `partials/_cookies.html`, o sea solo con `GTM_ID`): se anotan **únicamente si la persona aceptó
  «Medición»** (sin elección o con «Solo necesarias» no se agrega nada al `dataLayer`; al retirar el consentimiento se dejan de anotar). Nombres y campos de lista cerrada:
  `qs_contenido_visto` (`tipo` ficha|guia|proyecto, `slug`), `qs_calculadora_usada` (`tipo` solo_subsidio|con_proyecto, `slug` del subsidio o del proyecto; una vez por visita y nunca las cifras), `qs_cta_evaluar` (`origen`: la ruta, cualquier enlace a `#registro`), `qs_fuente_oficial` (`destino`: solo el
  sitio, p. ej. `www.minvu.gob.cl`, y `origen`) y `qs_evaluacion_paso` (`paso` ingresos|resultado|cuenta|sin_cuenta, en `home.html` vía `_medir`). Nunca viajan nombre, correo,
  teléfono, RUT, tramo ni la dirección completa: el filtro descarta cualquier campo que no sea de la lista o cuyo valor no sea texto sencillo. «Mi camino» y el CRM no los emiten.
  Al agregar un evento hay que ampliar la lista, las pruebas (`tests/herramientas_frontend/cookies.js`, bloque 13) y los textos de `/cookies` y `/privacidad` (subir `legal.ACTUALIZADO` y `VERSION_POLITICA`).
- Las páginas legales (`/terminos`, `/privacidad`, `/cookies`) son una primera versión: conviene que las revise un abogado. Si se agrega un
  proveedor o una cookie nueva hay que actualizarlas (`app/services/legal.py` guarda la fecha); las pruebas avisan de varios casos.

## Directorio de subsidios (Fase 1: tablas y ficha pública)
- **Tablas** (`supabase/migraciones/2026-11-05_directorio_subsidios.sql`, la ejecuta el dueño por bloques: A crear + A2 ficha de ejemplo, B comprobar, C revertir,
  D publicar/ocultar el ejemplo): `Fuente`, `Subsidio`, `SubsidioBloque`, `SubsidioDato` (cada dato con valor, unidad, llamado, fuente, estado y fecha de revisión),
  `Guia` y `GlosarioTermino` (estas dos se usarán en fases siguientes). Todas con seguridad por filas y sin políticas: solo el servidor las lee.
- **Ruta pública** `GET /subsidios/{slug}` (`frontend/subsidio.html`, lógica en `app/services/directorio.py`). Solo se lee lo `publicado`. **Reglas al mostrar** (no solo al guardar):
  todos los datos deben estar `verificado` y cada dato/bloque/ficha citar una fuente que exista y sea un enlace **https de un dominio oficial** (gob.cl, bcn.cl, minvu.cl,
  cmfchile.cl, sii.cl); si no, la ficha responde 404 y deja un aviso en el log. Si la revisión de la ficha o de algún dato tiene **más de 90 días** (o falta la fecha o el
  revisor), la ficha se sigue mostrando con un aviso de «pendiente de revisión» y `noindex`, y sale del sitemap.
- **Equivalentes en pesos:** solo para datos en UF, aproximados, con la UF del día de **la página oficial del SII** (sii.cl/valores_y_fechas/uf; si no responde o cambia su formato, respaldo en mindicador.cl) (`app/services/uf.py`: sin ese valor, o con uno de más de 10 días,
  no se muestran pesos; nunca se usa un valor de respaldo).
- **Resiliencia:** lectura guardada 5 minutos; si la base falla se usa la última copia buena (y 503 si nunca hubo una); si las tablas aún no existen se trata como «no hay fichas»
  (404 normal) y el sitemap sigue funcionando. Las fichas vigentes entran al `sitemap.xml`.
- **Por ahora** (hasta que exista el editor del panel, fase 2) una ficha se publica o se oculta con el `update` del bloque D del SQL.

## Editor del directorio (Fase 2: pestaña «Subsidios» del panel de administración)
- **Quién:** solo el dueño (`rol = owner`); el resto recibe 403. Rutas `/api/directorio/...` (`app/routers/directorio_admin.py`, lógica y reglas en `app/services/directorio_admin.py`).
- **Reglas del editor:** una ficha **nace en borrador**; solo se publica si `directorio.validar` no da problemas graves **ni avisos** (todos los datos verificados con fuente oficial y la
  ficha y sus datos revisados en los últimos 90 días). **Cambiar un dato (valor, cifra, unidad, llamado o fuente) lo devuelve a «por confirmar»**: hay que volver a verificarlo, y
  mientras tanto una ficha ya publicada deja de mostrarse. Verificar un dato y «Marcar revisada hoy» guardan la fecha y a la persona autenticada. Las fuentes solo se crean con un enlace https de
  un sitio oficial y su enlace no se edita (se crea otra). La dirección (slug) de una ficha publicada no se cambia y una ficha publicada no se borra.
- **Vista previa** (`GET /api/directorio/subsidios/{id}/vista-previa`, con sesión): la misma plantilla pública con un aviso «VISTA PREVIA» y la lista de lo que falta; no se indexa ni se guarda en caché.
- Cada escritura invalida la copia de lectura pública (la siguiente visita ve el cambio). El texto de las fichas siempre se escapa (nunca se interpreta como HTML).

## Índice y comparación de /subsidios (Fase 3: fichas de compra de vivienda)
- **Fichas nuevas:** `supabase/migraciones/2026-11-05_directorio_fichas_compra.sql` (la ejecuta el dueño: A3 cargar, B3 comprobar, C3 revertir) agrega, **en borrador**, DS1 Tramo 1, 2 y 3 y DS19
  (con DS49 son las cinco de compra de vivienda). Cada cifra y cada frase sale de la hoja de la Fase 0 que el dueño revisó; lo que esa hoja dice que no está confirmado (monto y precio
  máximo del Tramo 3, montos del DS19) no se publica: la ficha lo dice. Una ficha se publica desde Admin → Subsidios (vista previa + Publicar).
- **Página `/subsidios`:** cuando las cinco fichas del `NUCLEO` (`app/services/directorio.py`) están **publicadas y al día**, las tarjetas, el selector por tipo de subsidio (solo si hay más de un
  tipo) y la tabla comparativa se arman con `indice_publico()` desde esas fichas (cifras, equivalente en pesos aproximado, asterisco con el llamado de cada cifra, «Sin dato confirmado» donde falta).
  Si falta o está vencida **una sola**, o la base falla, o `interruptores.DIRECTORIO_EN_SUBSIDIOS = False`, se ve la página fija ya revisada (mismos subsidios): nunca se pierde uno.
  Las fichas de otro tipo (arriendo, etc.) entran solas al índice cuando se publican; con dos o más fichas completas en un mismo tipo, se compara ese tipo en su propia tabla.
- El orden y el color de las tarjetas salen de `orden` y `decreto` de cada ficha.

## Guías y glosario (Fase 5)
- **Páginas públicas:** `/guias` (índice), `/guias/{slug}` y `/glosario` (`frontend/guias.html`, `guia.html`, `glosario.html`; lógica en `app/services/guias.py`). Leen las tablas `Guia` y `GlosarioTermino`.
  Mismas reglas que las fichas, aplicadas **al mostrar**: una guía publicada necesita título, resumen, texto y **al menos una fuente**, y todas sus fuentes deben existir y ser un enlace **https de un dominio
  oficial** (se sumaron `bcentral.cl` y `sernac.cl` a la lista); si no, 404 y aviso en el log. Con la revisión de más de 90 días (o sin fecha o sin revisor) se muestra con aviso «pendiente de revisión»,
  `noindex` y fuera del sitemap. Un término del glosario necesita definición y fuente oficial. Lectura guardada 5 minutos; si la base falla, la última copia buena (503 si nunca hubo); sin tablas = sin guías.
  El sitemap incluye `/guias`, cada guía vigente y `/glosario` solo si hay algo publicado. Texto de las guías: párrafos, viñetas (`- `) y pasos numerados (`1. `).
- **Editor** (solo el dueño; Admin → Subsidios → «Guías» y «Glosario»; rutas `/api/directorio/guias...` y `/glosario...`, lógica en `app/services/guias_admin.py`): nacen en borrador; una guía se publica solo
  sin problemas ni avisos; **cambiar el título, el resumen, el texto o las fuentes de una guía borra su revisión**; marcar revisada registra a la persona autenticada y la fecha; no se repite un término
  (sin mirar tildes ni mayúsculas); lo publicado no se borra. La ficha de subsidio suma **«Verificar todos los datos»** (`POST /api/directorio/subsidios/{id}/verificar-datos`): marca de una vez los datos
  «por confirmar», solo si todos tienen fuente oficial, con tu nombre y la fecha.

## Contenido de las Fases 4 a 6 (vivienda restante, guías, otros subsidios)
- **Tres archivos SQL** en `supabase/migraciones/` que el dueño ejecuta completos (cada uno es repetible y todo entra en **borrador** con los datos **«por confirmar»**; nada se ve hasta que él los verifica y publica en
  Admin → Subsidios): `2026-11-05_directorio_fase4_vivienda.sql` (arriendo DS52; construir: DS49 sitio propio y DS1 Tramos 2 y 3; mejoramiento DS27; rural DS10; Valech; DS1 Tramo 4; y ajustes a las fichas de compra DS1:
  precio máximo y monto promedio del Tramo 3, cifras rotuladas con los dos llamados de 2026), `..._fase5_guias_glosario.sql` (cinco guías y 19 términos) y `..._fase6_otros_subsidios.sql` (ocho fichas ligeras sin
  montos: SUF, Asignación Familiar, Aporte Familiar Permanente, Subsidio Eléctrico, SAP, SUE, Subsidio de Discapacidad <18 y PGU).
- **Regla de contenido:** cada dato sale de una página oficial leída el 2026-11-05 y va con su fuente; **si dos fuentes oficiales dan cifras distintas, la cifra no se publica** (la ficha lo dice o lo omite). No se publican
  fechas de llamados. Las fichas ligeras no llevan montos (se reajustan cada año) y aclaran que el beneficio no es de vivienda.
- Los SQL se generan desde un único conjunto de datos (fuera del repositorio, en la carpeta de trabajo del dueño) que también produce la hoja de verificación con la cita textual de cada cifra.

## «Mi Camino» con las fichas y las guías (Fase 7: opciones A y B)
- **A. Elegir el subsidio:** la pestaña «Subsidios» de `/perfil` (`frontend/mi_camino.html`) ofrece «¿Qué subsidio te interesa?». Con uno elegido, el requisito de **ahorro** y el del **tramo del RSH** salen de su ficha
  (con el equivalente en pesos aproximado, el llamado, la fecha de revisión y el enlace a la ficha). Sin elegir, se ve la lista general de siempre. Los identificadores de los requisitos y la clave del avance no cambian:
  **lo ya marcado se conserva**. La elección se guarda en el dispositivo (`quesubsidio-camino-subsidio`), como el avance.
- **B. «Cómo hacerlo»:** cada requisito enlaza a la guía que lo explica (tramo del RSH, cuenta de ahorro, UF, documentos del crédito), solo si esa guía está publicada y al día.
- **Datos:** `GET /api/mi-camino/subsidios` (público, solo lectura, caché de 5 minutos; `app/services/mi_camino.py`). Solo viajan las fichas completas, publicadas, verificadas y al día con un dato de ahorro mínimo o de tope del RSH
  (dos datos por ficha + fecha de revisión) y los nombres de las guías publicadas. Si la base falla o faltan las tablas responde listas vacías y la página muestra su lista general. La página escapa todo lo que llega de la lectura,
  valida los nombres y arma el enlace a la ficha ella misma (no usa la ruta que venga en los datos).
- Fuera de alcance (opción C, no hecha): guardar el avance en la cuenta. `/api/progreso` ya existe pero la página no lo usa.

## Rol revisor en el panel del directorio (persona administradora)
- Editar, publicar, ocultar, archivar, borrar y manejar fuentes sigue siendo **solo del dueño**. Una persona con rol **administrador** actúa como **revisora**: ve todo (borradores y vista previa) y puede **verificar datos**
  («Verificar» y «Verificar todos los datos») y **marcar revisadas** fichas y guías; queda su nombre y la fecha. Quien revisa no publica. Un ejecutivo no accede a nada de esto.
- Servidor: doce rutas de `/api/directorio/...` aceptan dueño o administrador (`_revisor` en `app/routers/directorio_admin.py`); el resto exige al dueño (403 «Solo el dueño»). El panel (`frontend/admin.html`) muestra el
  «Modo revisión»: oculta lo que solo puede hacer el dueño y bloquea los campos; si alguien llama a la API a mano, el servidor igual responde 403.

## Calculadora de ahorro y pie (`/calculadora`)
- Página pública e indexable (en el sitemap y enlazada desde el pie, las fichas de subsidios de compra, la ficha de cada proyecto y «Mi Camino»). El cálculo ocurre **en el navegador**: lo que
  la persona escribe (su ahorro) no viaja al servidor ni queda en la dirección (la dirección solo guarda `?subsidio=`, `?proyecto=` y `?tipologia=`).
- Datos (lecturas públicas, caché de 5 minutos; `app/services/calculadora.py`): `GET /api/calculadora/subsidios` (fichas de COMPRA publicadas, completas y al día, con `ahorro_uf` y
  `precio_maximo_uf` numéricos —el DS19 trae su ahorro solo como texto— y la UF de hoy) y `GET /api/calculadora/proyectos/{slug}` (ahorro/pie, reserva, estacionamiento y, por tipología con
  stock, precio, subsidio informado y plan de pago desde la etapa; nunca el stock). La lista de proyectos sale de `/api/proyectos-landing`.
- **Regla del dueño (2026-11-05, confirmada):** con CUALQUIER subsidio, su ahorro mínimo cuenta como parte del pie del proyecto (se necesita el MAYOR de los dos) y **el pie es ese ahorro MÁS el
  subsidio**. El monto del subsidio solo se suma si la inmobiliaria lo informa para ESE subsidio y el proyecto lo acepta: `calculadora.TIPO_PROYECTO` dice qué `Tipologia.tipo_subsidio` y qué
  aceptación (`acepta_ds1_t23`, `acepta_ds19`) corresponden a cada ficha (DS1 Tramo 2 y 3 → `ds1_t23`; DS19 → `ds19`); el DS49 y el DS1 Tramo 1 y 4 no tienen monto informado, así que solo se calcula
  el ahorro. Un `ambos` trae un solo monto que no se sabe de cuál es y nunca se suma. Sin monto informado no se inventa ninguna cifra. El DS19 trae su ahorro solo como texto (depende del caso): manda el del proyecto.
  La columna `Proyecto.subsidio_ds1_t23_uf` **no se lee**: ninguna pantalla la carga y su ausencia en la base rompería la ficha.
- El cálculo es la función pura `calcular()` de `frontend/calculadora.html` (probada con `tests/herramientas_frontend/calculadora.js`); lo que viene del servidor se escapa siempre al dibujar.
  Sin UF no se muestran pesos y el ahorro solo se puede escribir en UF; sin directorio la página lo avisa y sigue en pie.

## Notas internas del cliente (CRM)
- Tabla `NotaCliente` (`supabase/migraciones/2026-11-01_nota_cliente.sql`). Rutas en `app/routers/clientes.py`: `GET` y `POST /api/clientes/{id}/notas`,
  y `PATCH` y `DELETE /api/clientes/{id}/notas/{nota_id}`. Solo las ve y usa quien puede usar al cliente (administrador, owner y el ejecutivo dueño); nunca salen en un correo ni mensaje.
- **Editar:** solo quien escribió la nota (ni el administrador ni el owner editan notas ajenas; una nota sin autor no la edita nadie). **Borrar:** quien la escribió, el administrador o el
  owner. El borrado es definitivo (no hay papelera) y la edición no deja marca de «editada»: si se quisiera, hay que agregar una columna `editada_en` (SQL aparte).
- El servidor valida de nuevo todo: el texto (1 a 2000 caracteres), que la nota pertenezca a ese cliente y quién es el autor; el registro (log) anota quién editó o borró, nunca el texto.
- En la pantalla (`frontend/index.html`) «Editar» y «Borrar» aparecen solo cuando corresponde; borrar pide confirmación y el texto se muestra siempre escapado.

## Vistas del panel de clientes: Tabla, Pipeline y Original (`frontend/index.html`)
- El selector de la barra del panel tiene tres vistas sobre **los mismos clientes y los mismos filtros de arriba** (búsqueda, proyecto, ejecutivo, estados, pestañas Mis clientes / Todos / Descartados): **Tabla**
  (bandeja con 16 columnas, las opcionales se eligen en «Columnas» y se recuerdan en el navegador), **Pipeline** (tablero por etapa con tarjetas que se arrastran) y **Original** (la pantalla de siempre,
  sin cambios). **Se entra en Tabla** (el orden del selector es Tabla, Pipeline, Original) y el navegador recuerda la última vista elegida (`localStorage` `crm-vista`); una dirección con `?vista=tabla|pipeline|original` manda
  sobre lo recordado (así la usa el panel `/panel`); al entrar, la dirección se limpia a `/index.html` y la vista pedida queda recordada. La pestaña **Inbox** usa su tabla de siempre en las tres.
- **Etapa** de un cliente (`etapaDe`): *Nuevo* y *Contactado* salen solos de lo que ya pasó por WhatsApp; *En evaluación*, *Aprobado*, *Cotización enviada*, *No interesado* y *Rechazado* salen del **estado de gestión**
  que cambia el equipo (los bancos responden por ahí). «Cotización enviada» es un estado de gestión nuevo (`cotizacion_enviada`; el servidor no tiene lista cerrada, así que no necesitó cambios).
  Al soltar una tarjeta se guarda ese estado con `PATCH /api/clientes/{id}`; si el servidor falla, la tarjeta vuelve a su columna. *Nuevo* lo asigna el sistema: no se puede devolver a Nuevo a quien ya fue contactado.
  *Cerrados* (No interesado y Rechazado) quedan en una sección propia del Pipeline y aparecen en la Tabla al elegir esa etapa, aunque el filtro «Estado gestión» de arriba los esconda por defecto.
- **Menú lateral fijo y ocultable** (`frontend/js/menu_lateral.js`, en `/panel` y en `/admin.html`): el menú no se mueve al bajar por la página (así «Panel Comercial» / «Panel Admin» siempre se ven) y se esconde con ««» (vuelve con el botón ☰ de arriba a la izquierda); se recuerda en el navegador (`crm-menu-oculto`); sin el menú el contenido ocupa todo el ancho, y en el celular parte escondido si nunca se eligió. En las listas de proyectos de los modales de usuarios y ejecutivos hay un buscador de proyectos.
- **«Todos los clientes» en el menú lateral de `/panel`** (debajo del logo, sobre las empresas; **un solo botón**: la vista Tabla o Pipeline se elige dentro del CRM; ya no hay fila «Todos los clientes» dentro de la lista de proyectos): abre el CRM sin empresa ni proyecto, o sea **todos los clientes de todos los proyectos que la persona puede ver** (el servidor filtra por rol: un ejecutivo ve solo los suyos).
  Se guarda en `sessionStorage` como una empresa sin id marcada `_global` + «Todos los proyectos»; el CRM entiende «sin empresa» como «sin filtro» (`_esContextoGlobal`, `_textoMigas`), no pide las inmobiliarias de esa empresa y muestra «Todos los clientes · todas las empresas». La fila «Todos los clientes» dentro de la lista de proyectos sigue existiendo y es la vista de esa empresa/inmobiliaria.
- **Cuántos clientes se cargan:** `GET /api/clientes` lee **todos** los clientes de a páginas de 1.000 (`leer_en_paginas` en `app/services/clientes_vistas.py`; antes la base cortaba en silencio en 1.000 y los más antiguos desaparecían), hasta `MAX_CLIENTES_LISTA` = 10.000 (el mismo número está en `index.html`):
  si se llega al tope, un aviso sobre la tabla dice que hay más. La Tabla y «Original» paginan de a 30. El **Pipeline** muestra primero los 20 más recientes de cada columna; si hay más antiguos dice cuántos están ocultos y ofrece «Mostrar todos» (pregunta antes si son más de 300); cada columna también tiene «Ver 20 más».
- **Estados de gestión** (`_EG_MAP` en `index.html`, en este orden): Sin estado, **Sin contactar**, **Primer llamado**, Primer WA, Interesado, **Sin RSH**, **Creando RSH**, Enviando docs, En evaluación, Aprobado, Cotización enviada, No interesado, **No califica**, **Cierre perdido**, Rechazado (los seis en negrita los pidió Operaciones).
  Etapa: Sin RSH, Creando RSH y Primer llamado cuentan como «Contactado»; No interesado, No califica, Cierre perdido y Rechazado son etapas **cerradas** (salen de las listas normales y el Pipeline las ofrece en «Cerrados»). «Sin contactar» no decide la etapa: manda lo que ya pasó por WhatsApp (igual que «Sin estado»).
  **Automático:** los clientes nuevos (carga, formulario web, creación manual) quedan en «Sin contactar» y el **primer WhatsApp** (`POST /api/clientes/{id}/enviar-wtsp`) los pasa a «Primer WA» solo si estaban sin estado o «Sin contactar»; un estado que la persona ya eligió nunca se pisa. El servidor sigue sin lista cerrada de estados (pendiente de confirmar).
  **El estado elegido se mantiene:** al cambiarlo, también cambia en la lista en memoria (antes, al volver a dibujar —nota, editar, otra pestaña— se veía el anterior aunque en la base estuviera bien); si el servidor rechaza el cambio, queda el anterior.
- **Buscar encuentra a todos:** al escribir en el buscador (nombre, RUT o teléfono) aparecen también los clientes en estados que normalmente se esconden (cerrados), con **línea punteada** y el estado más destacado; el Pipeline abre solo su sección «Cerrados» mientras se busca.
- **A quién se asigna:** «Asignar ejecutivo» (una o en bloque) y los repartos solo ofrecen ejecutivos que tienen el proyecto del cliente (directo o por su inmobiliaria); administración y owner siempre. En la carga/reparto lo filtra el servidor (`ejecutivos_disponibles`).
- **Pipeline:** cada tarjeta muestra la **última nota** (con autor, hace cuánto y cuántas más hay; viaja `nota_ultima` con el comienzo, 200 caracteres, en `GET /api/clientes`) y un clic en la tarjeta abre la **ficha** del cliente (RUT, teléfono, correo, proyecto, tipología, etapa, estado, ejecutivo, ahorro, próxima acción y última nota, con «Ver notas» y «Editar cliente»).
- **Chatbot de WhatsApp para todos:** «Ver conversación», «Contexto del bot», «Enviar plantilla WhatsApp» (en la fila, la tarjeta, la barra de acciones en bloque, el encabezado y el embudo de «Original») y «Reenviar WA» están abiertos a **todas las personas con sesión** (`_puedeChatbot()` en `index.html`); un ejecutivo solo puede usarlos con sus propios clientes (el servidor lo exige con `exigir_cliente`). Siguen siendo solo de administración y owner: asignar, descartar/restaurar, repartir y borrar documentos.
- **Cotizar a varios clientes a la vez:** en la barra de la «Tabla», «Enviar cotización» con **un** cliente marcado abre su cotización de siempre; con **varios** abre la ventana «Cotizar a N clientes» (`frontend/js/cotizar_lote.js`). Primero **revisa** (`POST /api/cotizaciones/lote/revisar`, no envía nada): quién está listo (tiene tipología con valor, etc.) o por qué no, su correo y por dónde saldría el WhatsApp. Luego se eligen los canales y **envía de a uno** con avance en vivo (`POST /api/cotizaciones/lote/enviar`, eventos `data: {json}`); un cliente con problema no frena a los demás, y quien recibe algo por cualquier canal pasa a «Cotización enviada» (cada intento queda en la tabla `Cotizacion`). Máximo 40 por vez (`MAX_LOTE`); un ejecutivo solo cotiza a sus clientes. **Correo:** al correo registrado del cliente, con el PDF adjunto. **WhatsApp:** dentro de las 24 h desde su último mensaje, el PDF como mensaje libre; fuera de ese plazo, la plantilla con documento de la variable **`TEMPLATE_COTIZACION_WA`** (vacía = no se usa y ese cliente no recibe WhatsApp, con el motivo explicado). La plantilla en Meta debe ser de categoría Utilidad, idioma `es_CL`, con **encabezado de tipo Documento** y 2 variables de texto en el cuerpo ({{1}} nombre del cliente, {{2}} proyecto).
- **Estado de gestión: lista cerrada.** `PATCH /api/clientes/{id}` solo acepta los estados de `ESTADOS_GESTION` (`app/services/clientes_datos.py`; los mismos de `_EG_MAP` del CRM, una prueba los compara) o vacío («Sin estado»); cualquier otro valor responde 400 «Estado de gestión no válido» y no guarda nada. Los clientes que ya tengan un valor antiguo fuera de la lista se siguen viendo como «Sin estado» y se corrigen al volver a cambiarlos.
- **Tarjeta que se abre en su lugar (Pipeline):** un clic sobre una tarjeta (fuera de sus botones) la agranda en el mismo lugar —la columna se ensancha— y muestra RUT, teléfono, correo, proyecto, tipología, etapa, estado, ejecutivo, ahorro, próxima acción y la última nota, con los botones «Enviar plantilla» (si hay chatbot), «Notas», «Editar» y «Cerrar»; otro clic, «Cerrar» o Escape la cierran, y solo hay una abierta a la vez (`alternarFichaLead`, `_fichaTarjeta`). Reemplaza a la ventana «Ficha del cliente». «Enviar plantilla WhatsApp» ya no es un botón suelto de la cabecera: está dentro de cada cliente (tarjeta abierta y menú ⋯) y en la barra al marcar varios. La cabecera de Leads no trae botones.
- **Panel como contenedor (menú lateral siempre visible):** `/panel` muestra el CRM («Todos los clientes» e «Ingresar» de cada proyecto) y las herramientas (menú «Herramientas» → Simulador de crédito, Subsidio DS1) dentro de un marco (`<iframe id="marco-iframe">`) a la derecha del menú, en vez de cambiar de página. El CRM y las herramientas se cargan con `?embed=1` (el CRM esconde su cabecera; las herramientas, su propio menú; `?t=credito|ds1` elige la herramienta). Elegir una empresa o inmobiliaria del menú cierra el marco; al recargar con F5 se restaura lo que había (`sessionStorage.crm_marco`, solo direcciones del mismo sitio). Para que funcione, `/index.html` y `/herramientas.html` responden `X-Frame-Options: SAMEORIGIN` (las demás páginas siguen en `DENY`). El tema claro/oscuro del menú llega al marco por el evento `storage`; el cierre de sesión se hace en toda la ventana (`window.top`). Entrar directo a `/index.html` sigue funcionando como antes.
- **Gasto por cliente (pestaña «Gasto» del panel de administración):** suma lo que consume cada cliente en IA (Anthropic) y en mensajes de WhatsApp (Meta), por cliente, proyecto, inmobiliaria o ejecutivo y por período. Se guarda el **consumo**, no el dinero: cada respuesta del bot anota sus tokens y cada aviso de estado de Meta (que trae la categoría con la que cobra: marketing, utility, authentication o service) anota un mensaje, una sola vez por `wamid`, atribuido al cliente por el teléfono. El costo se calcula al mirar con las tarifas de la tabla `TarifaGasto` (editables en la misma pestaña; mientras no se confirmen, se usan valores de partida y se marcan «por confirmar»). Registrar nunca rompe el bot ni los envíos: si la tabla no existe, solo se anota un aviso en el log. Requiere ejecutar `supabase/migraciones/2026-11-12_gasto_uso.sql` (bloque A). La pestaña se puede ver por clientes, ejecutivos, proyectos o inmobiliarias, y trae tres gráficos hechos a mano en SVG (gasto por día —por mes si el período pasa de 92 días—, reparto Anthropic/Meta y quién gasta más); la serie viene en `serie` de `GET /api/gasto`. Rutas: `GET /api/gasto`, `PUT /api/gasto/tarifas` (solo administración). El registro parte el día en que se activa; antes no hay datos.
- **Colas** (con su cantidad): Mi día (recordatorio de hoy o vencido), Nuevos 24 h, Sin contactar (= la columna Nuevo), Seguimiento vencido, Cotizados y Todos. Las fechas se calculan con la hora de Chile y se escriben con nombres fijos
  (nunca dependen del idioma del navegador). «Próxima acción» es el recordatorio del cliente.
- Acciones en bloque (solo en Tabla): enviar plantilla, cambiar etapa, asignar ejecutivo, programar recordatorio y descartar; todas piden confirmación y hacen un `PATCH` por cliente. Plantilla, asignar y descartar solo las ve
  el administrador y el owner (igual que en Original; el servidor igual lo exige). «Cotizar» abre la ventana de cotización (sección siguiente).
- **Servidor:** `GET /api/clientes` agrega a cada cliente `tipologia_nombre`, `etapa_nombre` y `notas_n` (`app/services/clientes_vistas.py`, en lotes de 250 y de «mejor esfuerzo»: si una consulta falla, esa parte se omite y la lista
  sale igual; del texto de las notas nunca sale nada, solo el conteo). **Datos nuevos de `Cliente`** (`supabase/migraciones/2026-11-06_cliente_vistas.sql`, ya ejecutada y comprobada por el dueño: A crear, B comprobar, C revertir): `creado_en` (la base lo pone sola; los 1.856 clientes anteriores no tienen fecha y la pantalla dice «Sin fecha de creación»), `fuente` (de dónde llegó), `medio` (por qué canal se le habla), `estado_gestion_en` (lo anota un disparador cada vez que cambia el estado de gestión, venga de donde venga) y `recordatorio_nota` (la «próxima acción» del recordatorio). Listas cerradas en `app/services/clientes_datos.py` (`FUENTES`, `MEDIOS`; una prueba las compara con las de la pantalla): fuente = meta_ads, chatbot, formulario_web, referido, excel, manual, otro; medio = whatsapp, chatbot, correo, llamada, presencial. **Quién escribe cada fuente:** crear a mano en el panel → `manual` (se puede elegir otra, p. ej. «Referido»); desde el Inbox → `chatbot` (y medio `chatbot`); desde un formulario web → `formulario_web`; desde un Excel o CSV → `excel`. El medio, si no lo elige la persona, queda vacío y la pantalla muestra «WhatsApp» solo cuando ya se le envió la plantilla. «Editar cliente» manda fuente y medio **solo si la persona los cambió** (así no se pisa lo que ya había). Un valor fuera de la lista da 400 con su mensaje y no se guarda nada.
- **Recordatorio:** el campo «Próxima acción» (antes «Motivo») se guarda en `recordatorio_nota` (hasta 300 caracteres) junto con la fecha y es el mismo texto del aviso por WhatsApp; si se quita la fecha, se borra también la nota.
- **Evaluación → «En evaluación»:** al enviar con éxito el correo de evaluación (`POST /api/clientes/{id}/enviar-evaluacion`), el cliente pasa solo a «En evaluación», salvo que ya esté «Aprobado», «Cotización enviada» o «En evaluación». La respuesta trae `estado_gestion` con el estado en que quedó (nulo si no se pudo cambiar: el correo ya salió y eso no se deshace). Después, quien gestiona marca el resultado.
- Pruebas: `tests/test_vistas_frontend.py` y `tests/test_clientes_vistas.py` (pytest) y `tests/herramientas_frontend/vistas.js` (jsdom: funciones puras, dibujo, arrastrar y soltar, acciones en bloque, permisos, textos hostiles y
  comparación de «Original» contra la versión publicada). Recorridos en el navegador: `~/wtsp_capturas` con `QS_VISTAS=1`.

## Carga masiva de clientes: revisión previa y reparto entre ejecutivos
- **Una sola ventana para todo:** `frontend/js/carga_clientes.js` (`window.CargaClientes.abrir({token, esAdmin, acento, contexto, alIniciar, alFinalizar, alTerminar, enviarWhatsApp})`) la usan el panel de proyectos (`/panel`: botón «Cargar base» en la lista
  de empresas, de inmobiliarias y de proyectos) y el CRM (`index.html`: pestaña «Masivo» del modal de cliente ; ya no hay botón suelto «Importar Excel» en las vistas: se llega solo por «Nuevo cliente» → «Masivo»). Reemplazó a las dos ventanas antiguas (cada página tenía la suya). Se sirve desde `/js/` igual que el resto
  del frontend; inyecta sus propios estilos (`#cc-estilos`, claro y oscuro) y usa solo delegación de eventos (sin `onclick`), así que no necesita recompilar el CSS. Si se edita, conviene saber que el navegador puede tener la versión anterior guardada unas horas.
- **Pasos:** (1) elegir el archivo (arrastrar o clic) y el **proyecto de destino**: la lista parte con el proyecto donde está la persona («(donde estás)»), sigue con los demás y termina con «Según la columna «Proyecto» del archivo»;
  (2) **revisión sin cargar nada** (`POST /api/clientes/importar/analizar`): «Son 500 registros», cuántos se cargarían, duplicados, errores (los primeros 20) y a qué proyectos irían, más una **recomendación**
  («asignar 96 a cada una de 5 personas»); (3) **asignar**: el administrador elige entre *Repartir entre varios ejecutivos* (tabla con casilla y cantidad por persona, lo que cada una tiene hoy, «Usar la recomendación», «Repartir en partes iguales entre
  las marcadas» y «Limpiar»; el botón solo se habilita cuando lo asignado calza exacto con lo que se carga), *Todos a una sola persona* o *Dejarlos sin asignar*; (4) carga con avance en vivo (SSE) y resultado con a quién quedó asignado cada parte, el CSV de rechazados
  y, para administración, el envío de WhatsApp a los recién cargados.
- **Aviso anticipado:** más de **100 clientes para una misma persona** (`RECOMENDADO_POR_PERSONA` en `app/services/carga_clientes.py`) muestra «Una persona recibiría N clientes…» y pide marcar «Entiendo y quiero cargarlos igual». El servidor lo exige también
  (`POST /api/clientes/importar` responde **409** con el mensaje si falta `?confirmo=1`), así que ninguna pantalla vieja, copiada o automática puede saltárselo. Un ejecutivo (sin reparto) ve el mismo aviso si carga más de 100 a su nombre.
- **Servidor** (`app/routers/clientes_acciones.py`, `app/services/carga_clientes.py`): `analizar` (cualquier persona con sesión; valida el proyecto con `exigir_proyecto`; solo para administración devuelve la lista de ejecutivos con `clientes_actuales`
  —clientes no archivados— y `tiene_proyecto`, ejecutivos primero) corre la **misma** importación en modo simulación (`importar_clientes_stream(..., simular=True)`: no escribe nada). El campo de formulario `reparto` (`[{usuario_id, cantidad}]`; `usuario_id` nulo =
  sin asignar) es **solo de administración** (403 si no) y se valida: personas activas y existentes, sin repetir, hasta 100 personas, cantidades enteras ≥ 1. Los bloques se asignan **en el orden del archivo** y lo que sobre va a la última persona. Archivos de más de 15 MB: 413.
  El resumen final incluye `asignados` (persona → cantidad).
- **Un ejecutivo solo carga a sus proyectos:** con la columna «Proyecto» (por nombre), una fila que apunte a un proyecto que el ejecutivo no tiene (directo o por su inmobiliaria) se rechaza con «No tienes acceso al proyecto …», también en la revisión previa. Administración carga a cualquiera.
- **Velocidad (paquetes):** los clientes válidos se crean **de a 50 en una sola consulta** (`TAMANO_PAQUETE` en `app/services/importacion_csv.py`), no uno por uno. Si la base rechaza un paquete (la base lo deshace entero), ese paquete se reintenta **fila por fila**: falla solo la fila mala y las demás se cargan.
  Antes de reintentar se consulta qué clientes del paquete SÍ quedaron (mismo lote y teléfono): si la respuesta se perdió pero el paquete entró, no se duplica nada; si ni eso se puede confirmar, esas filas se informan como error en vez de arriesgar duplicados.
  Un teléfono repetido en el mismo archivo sigue siendo «duplicado» del primero (se cierra el paquete pendiente antes de decidir). Los errores salen ordenados por fila y llega un último avance al cerrar el último paquete.
- **Repartir clientes que ya están cargados** (solo administración): en la Tabla del CRM, con los filtros que se quiera (p. ej. el ejecutivo que quedó con todo), «Seleccionar los N de esta lista» + **«Repartir…»** abre la misma ventana de asignación (`CargaClientes.repartir({ids, …})`). Rutas: `POST /api/clientes/repartir/analizar`
  (`{ids}`: cuántos son, a quién pertenecen hoy, ejecutivos, recomendación) y `POST /api/clientes/repartir` (`{ids, reparto}`; `?confirmo=1` si una persona recibe más de 100). El reparto debe sumar exacto, se asigna en bloques seguidos en el orden de los ids, se guarda con una consulta por cada 100
  clientes y un bloque que falle no frena a los demás (`fallidos`; repetir el reparto es seguro). Hasta 20.000 clientes por vez (`app/services/reparto_existentes.py`). Solo cambia `usuario_id`.
- Pruebas: `tests/test_carga_clientes.py`, `tests/test_importacion_paquetes.py`, `tests/test_reparto_existentes.py` y `tests/test_carga_clientes_frontend.py` (pytest) y `tests/herramientas_frontend/carga.js` (jsdom: funciones puras, revisión, reparto, avisos, carga con avance, errores, textos hostiles).

## Herramientas de ejecutivos (`/herramientas.html`)
- Página con menú lateral (accesos desde `/panel` y el encabezado del CRM; cualquier persona con sesión): **Simulador de crédito hipotecario** (dividendo, pie, estacionamiento o bodega que se suma al crédito, abonos a capital «reducir plazo» o «reducir dividendo», «lo que gana el cliente») y **Subsidio DS1 tramos 2 y 3** (fórmula, tope, monto fijo de 350 UF entre 1.600 y 2.600 UF en el tramo 3, ahorro mínimo, tablas de referencia, parámetros editables).
- Los cálculos son funciones puras en `frontend/js/calculos_credito.js`, **probadas contra las planillas de referencia** (mutuo de 2.700 UF: 193 cuotas con abonos, 1.157,79 UF de intereses, 13 años y 11 meses menos…; tablas del DS1) en `tests/herramientas_frontend/calculos_credito.js`; la página en `herramientas.js` (jsdom). Son solo cálculos: nada se guarda en el servidor.
- `GET /api/uf` (cualquier persona con sesión) entrega la UF del día para completar el valor en pesos (`null` si no se pudo leer).
- Pendiente: el **estimador de comisión** (necesita los montos por plan y proyecto; ver el backlog).

## Seguimiento de ejecutivos (panel de administración, pestaña «Usuarios»)
- La pestaña muestra **una tarjeta por persona** (el botón «Nuevo usuario» y el modal de «Editar» son los de siempre), con buscador, filtro por rol y orden (nombre, necesitan atención, más leads, más sin contactar, más seguimientos vencidos, menos actividad reciente), y arriba un resumen del equipo.
- **Cifras** (`GET /api/usuarios/seguimiento`, solo administración, `app/services/seguimiento_ejecutivos.py`; se recuerdan 60 s y «Actualizar» las refresca): leads activos (sin los «No interesado» ni «Rechazado», que van aparte como `cerrados`), sin contactar, clientes que esperan respuesta, seguimientos vencidos (fecha de Chile), para hoy,
  en evaluación, cotizados, aprobados, nuevos y notas de los últimos 7 días, cotizaciones enviadas en 30 días, y cuántos clientes no tienen ejecutivo asignado. Las etapas usan **las mismas reglas del CRM** (`etapaDe` / `computeFunnelGroup`), así un número del panel y el del CRM coinciden. Etiquetas de atención (máx. 3): seguimientos vencidos, sin contactar,
  esperan respuesta, «sin entrar hace N días» y «dado de baja con N leads» (para reasignarlos).
- **Última vez en el sistema:** `Usuario.ultimo_acceso`, que se anota como mucho cada 10 minutos cuando la persona usa el sistema (`_registrar_acceso` en `app/clients/supabase_auth.py`; un fallo nunca afecta a quien entra). Requiere el SQL `supabase/migraciones/2026-11-09_usuario_ultimo_acceso.sql` (la ejecuta el dueño: A crear, B comprobar, C revertir);
  mientras no exista la columna no se intenta anotar nada y la tarjeta dice «Sin registro aún».
- Pruebas: `tests/test_seguimiento_ejecutivos.py` (pytest) y `tests/herramientas_frontend/usuarios_admin.js` (jsdom).

## Cotizar a un cliente (botón «Cotizar» del panel)
- **Qué hace:** abre una ventana con la cotización del cliente (valor de su tipología, subsidio, ahorro, pie, crédito estimado y, si se pudo leer la UF del día, los pesos aproximados), permite ver el PDF y la envía **por correo con el PDF adjunto** y/o **por WhatsApp
  (el PDF como archivo)**. Cada intento por canal queda registrado en la tabla `Cotizacion` (`supabase/migraciones/2026-11-07_cotizacion.sql`, la ejecuta el dueño: A crear, B comprobar, C revertir) con una «foto» de lo cotizado; si la tabla aún no existe el envío igual sale.
  Si al menos un canal salió bien, el cliente pasa a «Cotización enviada» y se anota la gestión de hoy.
- **Números** (`app/services/cotizacion.py`; son los mismos del correo de evaluación): **pie = ahorro + subsidio**, crédito = valor − pie (nunca menos de cero), «ahorro mínimo» del proyecto con cuánto faltaría. Sin subsidio informado o sin ahorro se calcula lo que se puede y se avisa
  en la propia cotización. Falta de tipología/etapa o de valor en UF: no se cotiza y el mensaje dice qué completar. Los pesos solo salen con la UF del día (`app/services/uf.py`); nunca se inventa. El PDF (reportlab, una hoja A4, tipografía estándar Helvetica) y el correo escapan todo texto de personas;
  lo que no cabe en esa tipografía sale como «?». Incluye un descargo (valores referenciales, sujetos a evaluación del banco y disponibilidad): **conviene que el dueño revise ese texto** (`DESCARGO` en el mismo archivo).
- **Logo:** el PDF lleva el logo de QueSubsidio (`frontend/img/logo-horizontal.png`) a la derecha de la cabecera, sin sumar altura; si el archivo faltara, el PDF sale igual sin logo.
- **WhatsApp:** Meta solo deja mandar un archivo como mensaje libre dentro de las **24 horas** desde el último mensaje del cliente. La ventana del panel lo comprueba (`Prospecto.ultimo_entrante_en`) y, fuera de plazo, deja el canal desactivado explicando por qué (si Meta igual lo rechaza con el código 131047,
  se explica igual); el mensaje enviado queda también en la conversación. Mandar una cotización a quien no ha escrito requeriría una plantilla de Meta con documento (no existe todavía).
- **Permiso:** cualquier persona con sesión (owner, administrador y ejecutivo; desde 2026-11-12 ya no hay interruptor): cada ejecutivo cotiza solo a sus propios clientes. `GET /api/usuarios/me` devuelve `puede_cotizar` y el servidor lo exige igual.
- **Rutas** (`app/routers/cotizaciones.py`): `GET /api/clientes/{id}/cotizacion` (los números ya formateados, los canales y el historial), `GET /api/clientes/{id}/cotizacion/pdf` y `POST /api/clientes/{id}/cotizar` (`{canales: ["correo","whatsapp"], correo}`; responde por canal y no deja enviar dos veces a la vez al mismo cliente).
  El correo sale desde la cuenta de quien envía (alias o correo del usuario), con su firma. Dependencias nuevas: `reportlab` y `pillow` (fijadas en `requirements.txt`).
- **Remitente de los correos:** el nombre de quien envía se codifica aparte de su dirección (`app/clients/gmail.py`): antes, con tilde («Matías Rojas»), la dirección quedaba dentro del nombre codificado y el correo salía con un remitente ilegible (afectaba también al correo de evaluación).
- Pruebas: `tests/test_cotizacion.py`, `tests/test_gmail_remitente.py`, `tests/test_vistas_frontend.py` y `tests/herramientas_frontend/vistas.js` (sección E).

- **Correos con adjuntos:** cada archivo sale con su tipo real (PDF, imagen…): el que trae el adjunto o, si falta o es genérico, el que sugiere su nombre (`_tipo_adjunto` en `app/clients/gmail.py`).

## Textos de personas dentro de botones (CRM)
- El nombre de un cliente (y cualquier texto que escriba una persona) **nunca** va pegado dentro del `onclick` (`'${esc(nombre)}'`): el navegador decodifica el atributo antes de ejecutarlo, así que un nombre como
  O'Higgins rompía los botones de la fila y uno armado a propósito (`x');código;//`) se ejecutaba. Va en un atributo `data-*` escapado y el botón lo lee: `onclick="abrirNotas(+this.dataset.id, this.dataset.nombre)"`.
  Lo protegen `tests/test_botones_sin_texto_en_onclick.py` y `tests/herramientas_frontend/nombre_botones.js` (jsdom, con nombres hostiles).

## Datos de subsidios (DS19, DS1, DS49)
- Las cifras y requisitos de `/subsidios`, de las tarjetas y preguntas frecuentes de la portada, del evaluador y de «Mi Camino» salen de fuentes oficiales
  (MINVU y ChileAtiende) leídas el **5 de noviembre de 2026**; cada tarjeta cita su enlace y esa fecha de **revisión** (las fichas de ChileAtiende no traen
  fecha propia). Si una cifra no está confirmada por una fuente oficial **no se publica** (por ejemplo, los montos del DS19, que difieren entre el folleto
  y la página del MINVU). Los montos, ahorros y topes cambian por llamado: al revisar, volver a leer cada fuente.
- El recuadro «Fechas de postulación» de `/subsidios` no publica fechas: remite a la página oficial del MINVU (decisión del dueño, para que nada venza).
- «3 subsidios que explicamos» (portada) = las tres tarjetas (DS19, DS1, DS49). Lo que dicen los bancos («suelen pedir…») es costumbre del mercado, no norma.

## Preguntas frecuentes de la portada
- Cada pregunta y su respuesta se escriben **una sola vez**, al comienzo de `frontend/home.html` (`faq_q1…5` y `faq_a1…5`), y de ahí salen tanto el texto visible como los datos estructurados
  (JSON-LD `FAQPage`): Google exige que coincidan y así no pueden separarse. Para cambiar una, editar solo su bloque `{% set %}`; una prueba compara lo visible con lo estructurado.

## Cifras de la portada
- **«personas registradas»** sale de la base: `app/services/estadisticas.py` cuenta las filas de la tabla `Cliente` (una consulta liviana que
  pide solo el total en `Content-Range`), la guarda **una hora** en memoria y, si la base falla o tarda más de 3 s, conserva el último valor
  conocido; si no hay ninguno, la portada muestra solo las otras dos cifras (nunca inventa un número). Cuenta **todos** los clientes de la tabla
  (todas las empresas y proyectos, incluidos los de prueba que haya).
- **«subsidios que explicamos»** (3: DS19, DS1 T2/T3 y DS49) está escrito en `home.html`; una prueba lo compara con las tarjetas de `/subsidios`.
  Si se agrega o quita un subsidio en el sitio, cambiar ese número.
- **«89% tasa de éxito»** lo mantuvo el dueño por decisión propia (es un dato escrito a mano).

## Funciones ocultas del sitio público (interruptores)
- `app/core/interruptores.py` reúne las funciones que el dueño decidió ocultar por ahora. Con `False` la función **no aparece en las páginas y su
  ruta de la API responde 404** (así no hay formularios ni correos que nadie pidió). El código se conserva: cambiar a `True` y desplegar la
  vuelve a mostrar. Las plantillas reciben cada valor como `mostrar_<nombre>` desde `_contexto_comun` (`app/routers/paginas.py`).
- Hoy: `CONTACTO_EMPRESAS` (sección «Para inmobiliarias» de la portada, enlace «¿Eres inmobiliaria?» y `POST /api/contacto`, que enviaba al
  correo `EMAIL_ADMIN` el aviso «[QueSubsidio] Contacto desde la landing»). La frase del formulario de contacto en `/privacidad` también
  depende de este valor. `FUNCIONALIDADES` (bloque «Funcionalidades» de la portada).
  `NEWSLETTER` (suscripción del pie de todas las páginas y del blog, y `POST /api/newsletter`; el pie pasa a tres columnas y la política deja de
  nombrarlo). **Pendiente rehacerlo**: hoy solo guardaba correos, no existe ningún envío ni baja.
- **Proyectos ocultos del sitio público:** `PROYECTOS_OCULTOS_DEL_SITIO` (hoy `("subsidios.cl", "prueba inmobiliaria")`: los de la empresa Subsidios.cl y los de prueba) saca de la vitrina de la portada, de `/viviendas`, de
  las fichas `/viviendas/{slug}` (responden 404 con noindex) y del sitemap a los proyectos cuya **inmobiliaria o empresa** tenga ese texto en el
  nombre (sin importar mayúsculas ni tildes). No desactiva nada en la base: el CRM y el asistente de WhatsApp los siguen viendo. Para cambiar la
  lista basta editar el archivo; vacía = no se oculta ninguno.

## Proyectos que cada persona puede usar
- `app/core/autorizacion.py::exigir_proyecto` aplica el mismo criterio que la lista de `GET /api/proyectos`: administrador y owner, cualquier proyecto; el ejecutivo, los que tiene asignados
  (`proyecto_ids`) o los de una inmobiliaria que tiene asignada (`inmobiliaria_ids`). Lo denegado responde 403 `No tienes acceso a ese proyecto`, y un id mal formado nunca llega a la base.
- La usan `POST /api/formulario/{token}/convertir` (el proyecto del cuerpo, que manda, o el del formulario), `POST /api/clientes`, `POST /api/clientes/importar` y su revisión `…/analizar` (el `proyecto_id` de la dirección, formato SERVIU), `PATCH /api/clientes/{id}` (solo si el
  cuerpo cambia `proyecto_id` a otro distinto del actual: dejarlo donde está o quitarlo no exige nada) y `POST /api/inbox/{id}/registrar` (el proyecto elegido o, si no, el del prospecto).
  **Falta:** la importación por CSV que resuelve el proyecto por el nombre de cada fila (sin `proyecto_id` en la dirección) todavía puede apuntar a proyectos que el ejecutivo no ve.
  Una pantalla nueva que ponga clientes en un proyecto debe usarla también (la pantalla solo le ofrece al ejecutivo los suyos, pero la API es lo que protege).

## Autorización explícita en los formularios públicos
- Los tres formularios que guardan datos de personas exigen una **casilla sin marcar de antemano**, con el enlace a `/privacidad`: el
  evaluador del paso 3 de `/` (`p3-acepta`, nombra los datos de situación económica; se pide tanto al crear la cuenta con contraseña como
  por código), el contacto de empresas de `/` (`e-acepta`) y el newsletter (pie de todas las páginas: `footerAcepta`; blog: `blogAcepta`).
  «Continuar sin guardar» no envía nada, así que no la pide.
- **El servidor también la exige** (no solo el navegador): `POST /api/formulario` pide `consentimiento: true`, `POST /api/contacto`
  `acepta_privacidad: true` y `POST /api/newsletter` `acepta: true`, y deben ser exactamente `true` (no `"true"`, `1` ni `"on"`); si no, responde
  422 sin guardar nada ni enviar correos. Cualquier formulario nuevo que llame a estas rutas debe mandar su señal (una prueba lo vigila).
- **Constancia:** tras guardar, `app/services/consentimientos.py` inserta una fila en `ConsentimientoPrivacidad` (origen, correo en
  minúsculas, token del formulario si lo hay, versión de la política `legal.VERSION_POLITICA` y fecha). Es de «mejor esfuerzo»: si la tabla
  no existe o la base falla solo deja un aviso en el log (sin el correo) y el formulario igual funciona. La constancia vive solo en la
  tabla: no se agrega a ningún correo. **La migración `supabase/migraciones/2026-11-04_consentimiento_privacidad.sql` debe ejecutarse
  antes de publicar**; si cambia el texto de la política hay que subir `VERSION_POLITICA` junto con `ACTUALIZADO`.

## CSS de Tailwind (Fase 10)
- Tailwind **3.4.17** (la versión que servía el CDN) con el CLI standalone: <https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17>.
  El binario **no** va en el repo. El CSS compilado **sí** (`frontend/css/sitio.css` y `frontend/css/crm.css`); Railway no compila nada.
- Regenerar: `TAILWIND_BIN=/ruta/a/tailwindcss ./tailwind/construir.sh` (configuración en `tailwind/`). El resultado es determinista.
- `sitio.css`: sitio público, login, restablecer contraseña y Mi Camino. `crm.css`: CRM, panel y admin (modo oscuro por clase).
- **Dónde se enlaza:** el `<link rel="stylesheet" href="/css/sitio.css">` va al **final del `<head>`**, después de los `<style>` de la
  página. El CDN insertaba su CSS ahí, y el «preflight» de Tailwind debe ganarle a los estilos propios de la página: con el
  enlace antes de esos estilos, las páginas cambian de tamaño y espaciado.
- Si se agregan clases de Tailwind en una página, hay que volver a compilar (el escáner lee los HTML listados en cada
  `tailwind.*.config.js`; las clases armadas por concatenación en JS no se detectan y irían en `safelist`).

## Endpoints
- `GET /webhook`: verificación de Meta (usa `hub.mode`, `hub.verify_token`, `hub.challenge`)
- `POST /webhook`: recepción de eventos; si llega un mensaje de texto, hace un echo por Graph API

## Ingesta de leads (cuando el lead llega antes de escribir)
Si tus leads llegan antes de que el usuario escriba por WhatsApp, el primer mensaje debe ser un **template** aprobado.

1) En Supabase, crea un registro en `proyectos` con:
- `codigo` (ej. `miraflores_chillan`)
- `nombre` (ej. `Condominio Miraflores`)
- `ubicacion` (ej. `Chillán`)
- `nombre_plantilla` (nombre exacto del template aprobado en Meta)
- `idioma_plantilla` (ej. `es`)

2) Llama al endpoint `/prospectos/ingesta`:
```bash
curl -X POST "http://localhost:8000/prospectos/ingesta" \
	-H "Content-Type: application/json" \
	-H "X-API-Key: TU_INGEST_API_KEY" \
	-d '{"telefono_e164":"569XXXXXXXX","nombre":"Javiera","codigo_proyecto":"miraflores_chillan"}'
```

Esto:
- upsertea el prospecto en Supabase
- envía el template usando variable `{{1}}={{nombre}}`

## ¿De dónde saco cada dato?

### `WA_VERIFY_TOKEN` (no te lo “da” Meta)
Es un token que **definís vos** (por ejemplo una cadena larga aleatoria). Cuando configurás el webhook en Meta, te pide un “Verify token”: ponés ese mismo valor ahí y en tu `.env`.

### `WA_PHONE_NUMBER_ID`
En el panel de tu App en Meta (WhatsApp Cloud API) buscá el número de WhatsApp agregado y copiá el **Phone Number ID** (no es el número de teléfono).

### `WA_ACCESS_TOKEN`
En “Getting Started” de WhatsApp Cloud API podés generar un token de prueba (corto). Para producción normalmente se usa un token más estable asociado a un System User con permisos de WhatsApp/Graph.

## Decisiones tomadas para las fases siguientes

- **Tests**: no forman parte del repositorio; se mantienen fuera de él, en la copia local del dueño.
- **Tailwind**: CLI standalone v3.4 (equivale al CDN actual). La migración a v4 es una tarea aparte.
- **Caché de usuario (Fase 2)**: TTL de 30 s, tamaño acotado e invalidación inmediata cuando el propio proceso
  cambia el rol o el estado de un usuario. Sin validación local del JWT por ahora.

## Operación: configuración, logs y salud (Fase 2a)

- **Configuración**: `app/core/config.py` (pydantic-settings). Acepta los mismos nombres de variable de siempre y
  sus alias (`SUPABASE_URL`/`SUPABASE_PROJECT_URL`/…, `WA_*`/`WHATSAPP_*`). Solo **`SUPABASE_URL` y
  `SUPABASE_SERVICE_ROLE_KEY` son obligatorias**: si faltan, el proceso no arranca y el error nombra qué falta.
  Todo lo demás es opcional: al arrancar se registra un aviso por cada módulo que queda deshabilitado.
  Al arrancar se registran los **nombres** de las variables cargadas y de las ausentes, nunca los valores.
- **Logs**: JSON, una línea por registro, a stdout (`message`, `level`, `request_id`, …). Se enmascaran tokens,
  claves, JWT, teléfonos (quedan los 4 últimos dígitos), RUT y correos (`a***@dominio.cl`); los nombres no. Cada petición lleva un `request_id`, que también
  se devuelve en la cabecera `X-Request-ID` (se respeta el que envíe el proxy si es seguro).
- **`/health`**: responde `{"status":"ok"}` sin consultar Supabase ni terceros. Railway aún no lo usa como healthcheck.
- **Horario del bot**: `HORARIO_BOT_INICIO` y `HORARIO_BOT_FIN` (enteros, hora de Chile) fijan cuándo el bot **responde
  mensajes de texto**. Por defecto `0` y `24` = 24/7. Para volver a 9–19 basta definir `HORARIO_BOT_INICIO=9` y
  `HORARIO_BOT_FIN=19` en Railway (sin tocar código). Un valor no válido (texto, fuera de rango o inicio ≥ fin) no
  tumba el servicio: se usa 24/7 y el arranque lo avisa. No afecta a los avisos a ejecutivos, que conservan sus
  horarios fijos: "primer documento" y "documentos completos" de 9 a 19, y "necesita ayuda" de 8 a 24.
- **Recuperación de mensajes sin respuesta** (`_recovery_loop`, cada 3 min): reprocesa conversaciones cuyo último
  mensaje entrante tiene entre 3 min y `RECOVERY_MAX_ANTIGUEDAD_HORAS` horas (por defecto **24**, la ventana de WhatsApp
  para mensajes libres; `0` = sin tope), los más antiguos primero y hasta 10 por ciclo. Los más antiguos se ignoran y
  quedan en el log (un aviso al arrancar y luego cada ~1 h, con id, horas y teléfono enmascarado); no se modifican en
  la base de datos.
- **Avisos a ejecutivos persistentes** (`app/services/avisos_cola.py`, variable `AVISOS_PERSISTENTES`, apagada por defecto):
  los tres avisos por WhatsApp al ejecutivo asignado («necesita ayuda», «primer documento» y «documentos completos»)
  esperan por defecto **dormidos en la memoria** hasta que abre su horario, así que un reinicio del servidor los pierde y
  dos avisos iguales esperando salen dos veces. Con `AVISOS_PERSISTENTES=true` se guardan en la tabla `AvisoEjecutivo`
  (migración en `supabase/migraciones/2026-10-31_aviso_ejecutivo.sql`, ya ejecutada) y un bucle (cada minuto, y de
  inmediato si ya es hora) los reclama con la función `reclamar_avisos` (un solo proceso por aviso), los envía, y los
  deja `enviado`, `omitido` (con su motivo: sin ejecutivo, sin celular, ya notificado…) o `error` tras 3 intentos (los
  reintentos esperan 5 y 10 min). Un aviso reclamado cuyo proceso murió vuelve a la cola a los 10 min. Para encenderlo:
  la tabla y la función deben existir; para apagarlo basta quitar la variable (vuelve a la espera en memoria). Si no se
  puede guardar un aviso, se usa la espera en memoria y queda un aviso en el log. Límite conocido: si el proceso muere
  justo después de que Meta aceptó el mensaje y antes de marcarlo `enviado`, el aviso puede salir una segunda vez.
- **Caché de usuario autenticado** (`app/core/user_cache.py`): recuerda 30 s el resultado de verificar un token
  (`USER_CACHE_TTL_SEGUNDOS`, `0` = desactivada; `USER_CACHE_MAX_ENTRADAS`, 1000 por defecto) para no repetir en cada
  petición los 2 viajes a Supabase. La clave es el hash del token (el token no se guarda), solo se guardan los campos
  necesarios del perfil y solo las consultas exitosas. Cuando este proceso cambia a un usuario (rol, estado, nombre,
  contraseña) la entrada se borra al instante. **Límite conocido:** un cambio hecho fuera del proceso (panel de Supabase,
  otra réplica) o un token revocado puede seguir aceptándose hasta 30 s. El JWT no se valida localmente.
- **Errores** (`app/core/errors.py`): los mensajes de negocio (`AppError` o `ValueError` capturado en un endpoint, p. ej.
  "El campo Ahorro (UF) es requerido") llegan tal cual al navegador, en texto plano y con el mismo código de estado de
  siempre. Cualquier otro error (Supabase, Meta, Gmail, bugs) devuelve **`Ocurrió un error. Código: A1B2C3`**: el detalle
  completo (estado y cuerpo del error remoto, traza) queda solo en el log, con ese código en el campo `error_code` y el
  `request_id` de la petición. Para investigar un reporte: buscar el código en los logs de Railway (`@error_code:A1B2C3`
  o el texto del código). Un error que ningún endpoint capturó también devuelve el mensaje genérico (y `X-Request-ID`).
  `EXPOSE_ERROR_DETAIL=true` (apagada por defecto, **solo para desarrollo**) agrega el detalle a la respuesta; el arranque
  avisa si está activa. Los `except` que antes tragaban el error en silencio ahora lo registran con contexto y código.
  **Excepción (envío de correos por Gmail):** si Google rechaza el envío por un motivo de configuración que se puede corregir (la dirección no es un usuario de Google Workspace o es un alias, falta la delegación de dominio, Gmail sin habilitar, adjuntos muy pesados, cuota), la persona ve la causa con la dirección afectada (`No se pudo enviar el correo desde …`, estado 502) y el detalle de Google queda en el log; el resto de los errores de Gmail siguen siendo genéricos (`app/clients/gmail.py`).
- **Firma del webhook de WhatsApp** (`app/core/webhook_signature.py`): Meta firma cada mensaje con el *App Secret* de la
  app (cabecera `X-Hub-Signature-256`). Sin `WA_APP_SECRET` no se comprueba nada y el arranque lo avisa. Con
  `WA_APP_SECRET` definido, cada POST se registra en el log como `Webhook: firma válida` / `firma invalida` / `firma
  ausente` (campo `firma_webhook`) y **no se rechaza nada** (modo vigilar). Con `WA_SIGNATURE_ENFORCE=true` se rechazan (403)
  los mensajes sin firma válida; si falta `WA_APP_SECRET` no se rechaza nada y el arranque lo marca como error. Dónde
  sacar el secreto: developers.facebook.com → tu app → Configuración de la app → Básica → Clave secreta de la app.
- **Webhook de WhatsApp: lote y duplicados.** Se procesan todos los mensajes y estados de cada POST de Meta (los mensajes
  de un mismo número, en orden y respondiendo solo al último). Un mensaje repetido se ignora primero por memoria (últimos
  500) y, de forma durable, por un **índice único en la base** que hay que crear a mano en Supabase (si no existe, el
  sistema funciona igual pero solo con la memoria):
  `CREATE UNIQUE INDEX IF NOT EXISTS mensaje_wa_id_mensaje_uniq ON public."Mensaje" (wa_id_mensaje);`
  Para revertirlo: `DROP INDEX IF EXISTS public.mensaje_wa_id_mensaje_uniq;`. Los archivos se deduplican por
  `Documento.wa_media_id`. El aviso "WA status … sin cliente" es informativo salvo que el envío haya fallado.
- **HTTP saliente**: un único cliente `httpx` compartido (`app/core/http.py`) con timeouts explícitos. Solo los GET a
  Supabase se reintentan (máx. 2); nada hacia Meta ni Gmail. Anthropic tiene su propio cliente (`app/core/llm.py`):
  timeout por intento (5 s de conexión + 35 s de lectura), los 2 reintentos por defecto del SDK y un presupuesto
  total de 125 s por respuesta (al agotarse se registra un warning con el `request_id`; cada respuesta registra su duración). Con el debounce de 30 s (`BOT_REPLY_DELAY`) suma 155 s, por debajo de los 180 s tras
  los que `_recovery_loop` reprocesa un mensaje pendiente; si se sube `BOT_REPLY_DELAY` por encima de 55 s el
  arranque lo avisa (riesgo de responder dos veces).
