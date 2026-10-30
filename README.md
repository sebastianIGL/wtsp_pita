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
