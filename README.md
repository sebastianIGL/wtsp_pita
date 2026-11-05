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
- `GET /sitemap.xml` (`app/services/seo.py`): las páginas públicas (`/`, `/como-funciona`, `/subsidios`, `/viviendas`, `/blog`) y la
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
- **Equivalentes en pesos:** solo para datos en UF, aproximados, con la UF del día que informa mindicador.cl (`app/services/uf.py`: sin ese valor, o con uno de más de 10 días,
  no se muestran pesos; nunca se usa un valor de respaldo).
- **Resiliencia:** lectura guardada 5 minutos; si la base falla se usa la última copia buena (y 503 si nunca hubo una); si las tablas aún no existen se trata como «no hay fichas»
  (404 normal) y el sitemap sigue funcionando. Las fichas vigentes entran al `sitemap.xml`.
- **Por ahora** (hasta que exista el editor del panel, fase 2) una ficha se publica o se oculta con el `update` del bloque D del SQL.

## Datos de subsidios (DS19, DS1, DS49)
- Las cifras y requisitos de `/subsidios`, de las tarjetas y preguntas frecuentes de la portada, del evaluador y de «Mi Camino» salen de fuentes oficiales
  (MINVU y ChileAtiende) leídas el **5 de noviembre de 2026**; cada tarjeta cita su enlace y esa fecha de **revisión** (las fichas de ChileAtiende no traen
  fecha propia). Si una cifra no está confirmada por una fuente oficial **no se publica** (por ejemplo, los montos del DS19, que difieren entre el folleto
  y la página del MINVU). Los montos, ahorros y topes cambian por llamado: al revisar, volver a leer cada fuente.
- El recuadro «Fechas de postulación» de `/subsidios` no publica fechas: remite a la página oficial del MINVU (decisión del dueño, para que nada venza).
- «3 subsidios que explicamos» (portada) = las tres tarjetas (DS19, DS1, DS49). Lo que dicen los bancos («suelen pedir…») es costumbre del mercado, no norma.

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
