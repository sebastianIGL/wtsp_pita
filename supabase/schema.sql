-- ================================================================
-- Schema de referencia — CRM QueSubsidio
-- Actualizado: 2026-08-02
-- ================================================================
-- Este archivo es documentación del estado real de la BD.
-- Para aplicar cambios usa Supabase Dashboard → SQL Editor.
-- Tablas en singular PascalCase; excepción: log_correos,
-- newsletter y progreso_usuario (plataforma pública).
-- ================================================================


-- ── Industrias ───────────────────────────────────────────────────
CREATE TABLE public."Industria" (
  id     serial PRIMARY KEY,
  nombre text NOT NULL
);

-- ── Empresas ─────────────────────────────────────────────────────
CREATE TABLE public."Empresa" (
  id          serial PRIMARY KEY,
  nombre      text NOT NULL,
  slug        text NOT NULL UNIQUE,
  color_marca text,
  logo_url    text
);

-- ── Inmobiliarias ────────────────────────────────────────────────
CREATE TABLE public."Inmobiliaria" (
  id          serial PRIMARY KEY,
  nombre      text NOT NULL,
  empresa_id  int  NOT NULL REFERENCES public."Empresa"(id)
);

-- ── Proyectos ────────────────────────────────────────────────────
CREATE TABLE public."Proyecto" (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre                      text    NOT NULL,
  codigo                      text    NOT NULL UNIQUE,
  ubicacion                   text,
  imagen_url                  text,
  nombre_plantilla            text,       -- plantilla WA de bienvenida por defecto
  inmobiliaria_id             int REFERENCES public."Inmobiliaria"(id),
  acepta_ds19                 boolean NOT NULL DEFAULT false,
  acepta_ds1_t23              boolean NOT NULL DEFAULT false,
  subsidio_ds1_t23_uf         numeric,
  ahorro_minimo_uf            numeric,
  valor_reserva_clp           numeric,
  valor_reserva_uf            numeric,
  tiene_piloto                boolean DEFAULT false,
  valor_estacionamiento_uf    numeric,
  estacionamiento_obligatorio boolean DEFAULT false,
  grupo_ds19                  text    DEFAULT 'A',
  notas                       text,
  -- COLUMNA LEGACY (no leer en código nuevo; usar tabla Tipologia):
  tipologias                  jsonb
);

-- ── Etapas ───────────────────────────────────────────────────────
CREATE TABLE public."Etapa" (
  id            serial PRIMARY KEY,
  nombre        text NOT NULL,
  proyecto_id   uuid NOT NULL REFERENCES public."Proyecto"(id),
  estado        text NOT NULL DEFAULT 'entrega_futura',
  -- 'entrega_inmediata' | 'entrega_futura'
  fecha_entrega date,
  tipo_ahorro   text,  -- 'cuotas' | 'unico'
  num_cuotas    int,
  cuotas_ahorro int
);

-- ── Tipologías ───────────────────────────────────────────────────
-- Stock: gestionado exclusivamente en EtapaTipologia.stock
-- Para total por tipología: SELECT SUM(stock) FROM "EtapaTipologia" WHERE tipologia_id = ?
CREATE TABLE public."Tipologia" (
  id                 serial PRIMARY KEY,
  nombre             text    NOT NULL,
  proyecto_id        uuid    NOT NULL REFERENCES public."Proyecto"(id),
  dormitorios        int,
  banos              int,
  superficie_util_m2 numeric,
  valor_uf           numeric,
  monto_subsidio     numeric,
  tipo_subsidio      text DEFAULT 'ninguno',
  -- 'ninguno' | 'ds19' | 'ds1_t23' | 'ambos'
  estado             text DEFAULT 'activo'
  -- 'activo' | 'agotado' | 'inactivo'
);

-- ── EtapaTipologia — stock por etapa ────────────────────────────
CREATE TABLE public."EtapaTipologia" (
  id           serial PRIMARY KEY,
  etapa_id     int NOT NULL REFERENCES public."Etapa"(id),
  tipologia_id int NOT NULL REFERENCES public."Tipologia"(id),
  stock        int NOT NULL DEFAULT 0,
  UNIQUE (etapa_id, tipologia_id)
);

-- ── Usuarios CRM ─────────────────────────────────────────────────
-- id: UUID de auth.users (Supabase Auth)
CREATE TABLE public."Usuario" (
  id                   uuid PRIMARY KEY,  -- REFERENCES auth.users(id)
  nombre               text,
  rut                  text UNIQUE,
  correo               text NOT NULL UNIQUE,
  celular              text,
  estado               smallint,
  rol                  text NOT NULL DEFAULT 'ejecutivo',
  -- 'owner' | 'administrador' | 'ejecutivo'
  password_provisional boolean DEFAULT false,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  empresa_id           int REFERENCES public."Empresa"(id),
  industria_id         int REFERENCES public."Industria"(id),
  email_alias          text,        -- alias Gmail para envíos (domain-wide delegation)
  inmobiliaria_ids     int[],       -- acceso a inmobiliarias específicas
  proyecto_ids         uuid[]       -- acceso a proyectos específicos
);

-- ── Clientes CRM ─────────────────────────────────────────────────
-- Nombres de columna heredados del sistema anterior (con mayúsculas y espacios).
CREATE TABLE public."Cliente" (
  id                   serial PRIMARY KEY,
  "Contacto"           text,
  "Rut"                text,
  "Telefono"           text,
  email                text,
  rut_conyuge          text,
  proyecto_id          uuid REFERENCES public."Proyecto"(id),
  tipologia_id         int4 REFERENCES public."Tipologia"(id),
  usuario_id           uuid REFERENCES public."Usuario"(id),
  "Tramo de renta"     text,
  movendo_id           text,  -- ID externo Movendo/Zonapropia
  rsh                  text,
  tipo_ingreso         text,  -- 'dependiente' | 'independiente' | etc.
  estado_plantilla     text,  -- 'enviado' | null
  "primer mensaje"     boolean DEFAULT true,
  wtsp_habilitado      boolean DEFAULT false,
  primer_wtsp_en       timestamptz,
  wamid_plantilla      text,
  ahorro_uf            numeric,     -- ahorro actual del cliente (en UF)
  recordatorio_at      timestamptz,
  "Fecha Ult. Gestión" timestamptz
);

CREATE INDEX idx_cliente_movendo_id ON public."Cliente"(movendo_id);
CREATE INDEX idx_cliente_usuario_id ON public."Cliente"(usuario_id);

-- ── Prospectos WhatsApp ──────────────────────────────────────────
-- 1 prospecto por número de teléfono. Upsert on_conflict=telefono_e164.
CREATE TABLE public."Prospecto" (
  id                         text PRIMARY KEY,
  telefono_e164              text NOT NULL UNIQUE,
  nombre                     text,
  rut                        text,
  proyecto_id                uuid REFERENCES public."Proyecto"(id),
  cliente_id                 int  REFERENCES public."Cliente"(id),
  estado                     text NOT NULL DEFAULT 'NUEVO',
  paso                       text NOT NULL DEFAULT 'INICIO',
  rango_sueldo               text,
  numero_integrantes         int,
  renta_mensual              numeric,
  -- Campos de calificación (extraídos por el bot IA):
  tiene_rsh                  boolean,
  tiene_propiedad            boolean,
  subsidio_previo            boolean,
  ahorro_ok                  boolean,
  trabajo_indefinido         boolean,
  complemento_renta          boolean,
  -- Seguimiento y descarte:
  motivo_no_interesado       text,
  fecha_tentativa_recontacto date,
  opt_out                    boolean DEFAULT false,
  paso_origen_no_interesado  text,
  motivo_no_califica         text,
  quiere_contacto_ejecutivo  boolean,
  intencion_regularizar      boolean,
  integrantes_rsh            int,
  pendiente_respuesta        boolean DEFAULT false,
  -- Blob para datos no estructurados del bot:
  datos                      jsonb NOT NULL DEFAULT '{}',
  ultimo_texto_entrante      text,
  ultimo_entrante_en         timestamptz,
  ultimo_saliente_en         timestamptz,
  creado_en                  timestamptz NOT NULL DEFAULT now(),
  actualizado_en             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_prospectos_cliente_id ON public."Prospecto"(cliente_id);
CREATE UNIQUE INDEX prospectos_telefono_e164_key ON public."Prospecto"(telefono_e164);

-- ── Mensajes WhatsApp ────────────────────────────────────────────
CREATE TABLE public."Mensaje" (
  id            serial PRIMARY KEY,
  prospecto_id  text REFERENCES public."Prospecto"(id) ON DELETE CASCADE,
  direccion     text NOT NULL,  -- 'entrante' | 'saliente'
  text          text,
  wa_message_id text,
  cliente_id    int  REFERENCES public."Cliente"(id),
  creado_en     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_mensajes_prospecto ON public."Mensaje"(prospecto_id, creado_en DESC);

-- ── Documentos ───────────────────────────────────────────────────
CREATE TABLE public."Documento" (
  id             serial PRIMARY KEY,
  prospecto_id   text REFERENCES public."Prospecto"(id) ON DELETE CASCADE,
  tipo           text NOT NULL,
  nombre_archivo text,
  url_storage    text,
  mime_type      text,
  creado_en      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_documentos_prospecto ON public."Documento"(prospecto_id, tipo);

-- ── Ejecutivos Bancarios ─────────────────────────────────────────
-- Renombrada desde "Ejecutivo" para evitar colisión con Usuario.rol='ejecutivo'.
CREATE TABLE public."EjecutivoBancario" (
  id         serial PRIMARY KEY,
  ejecutivo  text    NOT NULL,
  entidad    text    NOT NULL,
  email      text    NOT NULL,
  telefono   text,
  disponible boolean NOT NULL DEFAULT true
);

-- ── Plantillas WA — configuración de variables ───────────────────
CREATE TABLE public."PlantillaConfig" (
  id            serial PRIMARY KEY,
  template_name text  NOT NULL UNIQUE,
  variables     jsonb,  -- lista ordenada de claves del pool
  param_names   jsonb   -- nombres de parámetros (plantillas named params)
);

-- ── Formulario público de perfilamiento ──────────────────────────
-- Acceso sin auth vía token único. Pendiente: endpoint /convertir → Cliente CRM.
CREATE TABLE public."Formulario" (
  id                       serial PRIMARY KEY,
  token                    text    NOT NULL UNIQUE,
  nombre                   text,
  email                    text,
  telefono                 text,
  rsh                      text,
  cmf                      text,
  tipo_ingreso             text,
  antiguedad_meses         int,
  tiene_liquidaciones      boolean,
  tiene_cotizaciones_afp   boolean,
  anos_declaracion_renta   int,
  tiene_carpeta_tributaria boolean,
  tiene_boletas_honorarios boolean,
  tiene_cert_antiguedad    boolean,
  tiene_propiedad          boolean,
  numero_integrantes       int,
  resultado                text,
  paso_actual              text,
  completado_at            timestamptz
);

-- ── Log de correos enviados (Gmail API) ──────────────────────────
CREATE TABLE public.log_correos (
  id            serial PRIMARY KEY,
  tipo          text,
  destinatario  text NOT NULL,
  asunto        text,
  estado        text NOT NULL,  -- 'enviado' | 'error'
  detalle_error text,
  usuario_id    text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_log_correos_created_at   ON public.log_correos(created_at DESC);
CREATE INDEX idx_log_correos_destinatario ON public.log_correos(destinatario);
CREATE INDEX idx_log_correos_estado       ON public.log_correos(estado);

-- ── Newsletter — plataforma pública QueSubsidio.cl ───────────────
CREATE TABLE public.newsletter (
  id    serial PRIMARY KEY,
  email text NOT NULL UNIQUE
);

-- ── Progreso de usuario — plataforma pública QueSubsidio.cl ──────
CREATE TABLE public.progreso_usuario (
  user_id text NOT NULL,
  mundo   text NOT NULL,
  req_id  text NOT NULL,
  PRIMARY KEY (user_id, mundo, req_id)
);


-- ================================================================
-- PENDIENTES DB (ejecutar en Supabase SQL Editor):
-- ================================================================

-- 1. Renombrar tabla Ejecutivo → EjecutivoBancario (si no está hecho):
--    ALTER TABLE public."Ejecutivo" RENAME TO "EjecutivoBancario";

-- 2. Eliminar columna stock_disponible de Tipologia (ya no se usa):
--    ALTER TABLE public."Tipologia" DROP COLUMN IF EXISTS stock_disponible;

-- 3. Trigger de stock (opcional, ya no aplica — columna eliminada):
--    DROP TRIGGER IF EXISTS trg_sync_tipologia_stock ON public."EtapaTipologia";
--    DROP FUNCTION IF EXISTS public.fn_sync_tipologia_stock();

-- 4. Agregar columna proyecto_id a Formulario si no existe (para endpoint /convertir):
--    ALTER TABLE public."Formulario" ADD COLUMN IF NOT EXISTS proyecto_id uuid
--      REFERENCES public."Proyecto"(id);
