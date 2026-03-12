# wtsp_pita

Webhook mínimo con FastAPI para WhatsApp Cloud API (Meta Business).

**Flujo real (alto nivel)**
- Cliente envía WhatsApp → Meta dispara evento → tu webhook recibe JSON
- Tu backend decide respuesta → tu backend llama a Graph API → WhatsApp responde al cliente

## Requisitos
- Python 3.8+

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
