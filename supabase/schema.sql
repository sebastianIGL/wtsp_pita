-- ================================================================
-- Schema de referencia — CRM QueSubsidio
-- Actualizado: 2026-11-14, leído de la base real (columnas, restricciones, índices, funciones y disparadores) y comprobado tabla por tabla
-- ================================================================
-- Este archivo es DOCUMENTACIÓN del estado real de la BD: no se ejecuta como migración.
-- Para aplicar cambios usa Supabase Dashboard → SQL Editor (los cambios van en supabase/migraciones/).
-- Para volver a comprobarlo: supabase/leer_esquema.sql (consultas de solo lectura).
-- Tablas en singular PascalCase; excepción: log_correos, log_depas, newsletter y progreso_usuario.
-- Las columnas «bigint» sin valor por defecto son de identidad (generated always as identity).
-- Todas las tablas tienen seguridad por filas (RLS) activada, sin políticas: solo el backend (service role) lee y escribe.
-- La función rls_auto_enable() la activa sola en cada tabla nueva del esquema public.
-- ================================================================


-- ── Industrias ───────────────────────────────────────────────────
CREATE TABLE public."Industria" (
  id         serial PRIMARY KEY,
  nombre     text NOT NULL,
  created_at timestamptz DEFAULT now()
);

-- ── Empresas ─────────────────────────────────────────────────────
CREATE TABLE public."Empresa" (
  id          serial PRIMARY KEY,
  nombre      text NOT NULL,
  slug        text NOT NULL UNIQUE,
  logo_url    text,
  color_marca text DEFAULT '#7c3aed',
  estado      text DEFAULT 'activa' CHECK (estado IN ('activa', 'inactiva')),
  created_at  timestamptz DEFAULT now(),
  industria_id int REFERENCES public."Industria"(id)
);

-- ── Inmobiliarias ────────────────────────────────────────────────
CREATE TABLE public."Inmobiliaria" (
  id         serial PRIMARY KEY,
  empresa_id int  NOT NULL REFERENCES public."Empresa"(id) ON DELETE CASCADE,
  nombre     text NOT NULL,
  rut        text,
  logo_url   text,
  estado     text DEFAULT 'activa' CHECK (estado IN ('activa', 'inactiva')),
  created_at timestamptz DEFAULT now()
);

-- ── Proyectos ────────────────────────────────────────────────────
-- Ojo: Proyecto NO tiene grupo_ds19, subsidio_ds1_t23_uf ni nombre_plantilla (el bot los lee con valor por defecto: «A» y vacío).
CREATE TABLE public."Proyecto" (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo                      text NOT NULL UNIQUE,
  nombre                      text NOT NULL,
  ubicacion                   text NOT NULL,
  imagen_url                  text,
  ahorro_minimo_uf            numeric,
  valor_reserva_clp           numeric,
  tiene_piloto                boolean DEFAULT false,
  valor_estacionamiento_uf    numeric,
  estacionamiento_obligatorio boolean DEFAULT false,
  entidades_no_aceptadas      text,
  notas                       text,
  acepta_ds19                 boolean DEFAULT false,
  acepta_ds1_t23              boolean DEFAULT false,
  tipologias                  jsonb,       -- LEGACY (no leer en código nuevo; usar la tabla Tipologia)
  valor_reserva_uf            numeric,
  actualizado_en              timestamptz DEFAULT now(),
  nombres_csv                 text[],
  inmobiliaria_id             int REFERENCES public."Inmobiliaria"(id),
  fuente_externo_id           text UNIQUE,  -- id en BuyDepa (semi-nuevos)
  activo                      boolean NOT NULL DEFAULT true,
  condicion_propiedad         text NOT NULL DEFAULT 'nueva' CHECK (condicion_propiedad IN ('nueva', 'semi-nueva')),
  imagenes                    jsonb NOT NULL DEFAULT '[]'
);

-- ── Etapas ───────────────────────────────────────────────────────
CREATE TABLE public."Etapa" (
  id            serial PRIMARY KEY,
  proyecto_id   uuid NOT NULL REFERENCES public."Proyecto"(id) ON DELETE CASCADE,
  nombre        text NOT NULL DEFAULT 'Etapa 1',
  estado        text NOT NULL DEFAULT 'entrega_futura',   -- 'entrega_inmediata' | 'entrega_futura'
  fecha_entrega text,
  descripcion   text,
  created_at    timestamptz DEFAULT now(),
  tipo_ahorro   varchar DEFAULT 'unico',                  -- 'cuotas' | 'unico'
  num_cuotas    int
);

-- ── Tipologías ───────────────────────────────────────────────────
-- Ojo: el valor por defecto de estado ('activa') NO cumple la restricción check (activo | agotado | inactivo): siempre hay que indicar el estado al crear una tipología.
CREATE TABLE public."Tipologia" (
  id                 serial PRIMARY KEY,
  nombre             text NOT NULL,
  dormitorios        int,
  banos              int,
  superficie_util_m2 numeric,
  valor_uf           numeric,
  estado             text DEFAULT 'activa' CHECK (estado IN ('activo', 'agotado', 'inactivo')),
  raw_data           jsonb,
  created_at         timestamptz DEFAULT now(),
  proyecto_id        uuid NOT NULL REFERENCES public."Proyecto"(id),
  tipo_subsidio      text DEFAULT 'ninguno',              -- 'ninguno' | 'ds19' | 'ds1_t23' | 'ambos'
  terreno_m2         numeric,
  monto_subsidio     numeric,
  cuotas_ahorro      int CHECK (cuotas_ahorro >= 0),
  estacionamientos   int,
  bodegas            int
);

-- ── EtapaTipologia — une tipología + etapa y guarda el stock ─────
CREATE TABLE public."EtapaTipologia" (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  etapa_id     bigint NOT NULL REFERENCES public."Etapa"(id) ON DELETE CASCADE,
  tipologia_id bigint NOT NULL REFERENCES public."Tipologia"(id) ON DELETE CASCADE,
  stock        int NOT NULL DEFAULT 0,
  UNIQUE (etapa_id, tipologia_id)
);

-- ── Usuarios CRM ─────────────────────────────────────────────────
-- Ojo: el valor por defecto de rol ('usuario') NO cumple la restricción check (owner | administrador | ejecutivo | cliente): siempre hay que indicar el rol al crear una persona.
CREATE TABLE public."Usuario" (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid() REFERENCES auth.users(id) ON DELETE CASCADE,   -- igual al id de auth.users
  nombre               text NOT NULL,
  rut                  text NOT NULL UNIQUE,
  correo               text NOT NULL UNIQUE,
  celular              text,
  estado               smallint DEFAULT 1 CHECK (estado IN (0, 1)),
  rol                  text DEFAULT 'usuario' CHECK (rol IN ('owner', 'administrador', 'ejecutivo', 'cliente')),
  password_provisional boolean DEFAULT true,
  created_at           timestamptz DEFAULT now(),
  updated_at           timestamptz DEFAULT now(),
  empresa_id           int REFERENCES public."Empresa"(id),
  industria_id         int REFERENCES public."Industria"(id),
  email_alias          text,                                          -- alias Gmail para envíos (domain-wide delegation)
  inmobiliaria_ids     int[]  DEFAULT '{}',                           -- acceso a inmobiliarias específicas
  proyecto_ids         uuid[] DEFAULT '{}',                           -- acceso a proyectos específicos
  ultimo_acceso        timestamptz                                    -- última vez en el sistema (pestaña «Usuarios»)
);
-- (Los índices únicos perfiles_correo_key y perfiles_rut_key de la base son los de las restricciones UNIQUE de correo y rut.)

-- ── Clientes CRM ─────────────────────────────────────────────────
-- Nombres de columna heredados del sistema anterior (con mayúsculas y espacios).
-- Columnas sin uso en el código (se conservan en la base): estado_crm, tiene_subsidio, tipo_subsidio, tiene_propiedad, movendo_id, movendo_source, movendo_data.
CREATE TABLE public."Cliente" (
  id                   bigserial PRIMARY KEY,
  "Contacto"           text NOT NULL,
  "Rut"                text NOT NULL,
  "Correo"             text,
  "Telefono"           text,
  "Fecha Ult. Gestión" date,
  "Tramo de renta"     text,
  "primer mensaje"     boolean,
  estado_crm           text,
  tiene_subsidio       boolean,
  tipo_subsidio        text,
  tiene_propiedad      boolean,
  medio                text,        -- canal del contacto: whatsapp | chatbot | correo | llamada | presencial
  usuario_id           uuid REFERENCES public."Usuario"(id) ON DELETE SET NULL,
  wtsp_habilitado      boolean DEFAULT true,
  proyecto_id          uuid REFERENCES public."Proyecto"(id),
  movendo_id           text,
  movendo_source       text,
  movendo_data         jsonb,
  numero_integrantes   int,
  es_nuevo             boolean DEFAULT false,
  recordatorio_at      timestamptz,
  primer_wtsp_en       timestamptz,
  wamid_plantilla      text,
  estado_plantilla     text,        -- 'enviado' | 'entregado' | 'leido' | 'fallido'
  ahorro_uf            numeric,
  estado_gestion       text,        -- sin_contactar | primer_llamado | primer_wtsp | interesado | sin_rsh | creando_rsh | enviando_docs | en_evaluacion | aprobado | cotizacion_enviada | no_interesado | no_califica | cierre_perdido | descartado
  etapa_tipologia_id   int REFERENCES public."EtapaTipologia"(id),
  ultimo_recordatorio_tipo           text,
  ultimo_recordatorio_estado_gestion text,
  archivado            boolean DEFAULT false,
  origen_carga         text,
  creado_en            timestamptz DEFAULT now(),   -- nulo en los clientes anteriores a 2026-11-06
  fuente               text CHECK (fuente IS NULL OR char_length(fuente) BETWEEN 1 AND 40),   -- meta_ads | chatbot | formulario_web | referido | excel | manual | otro
  estado_gestion_en    timestamptz,                 -- lo pone el disparador cliente_estado_gestion_en
  recordatorio_nota    text CHECK (recordatorio_nota IS NULL OR char_length(recordatorio_nota) BETWEEN 1 AND 300)
);
CREATE INDEX idx_cliente_movendo_id  ON public."Cliente"(movendo_id);   -- OBSOLETO, sin uso
CREATE INDEX idx_cliente_usuario_id  ON public."Cliente"(usuario_id);

-- ── Prospectos WhatsApp ──────────────────────────────────────────
-- 1 prospecto por número de teléfono. Upsert on_conflict=telefono_e164. opt_out = pidió la baja de WhatsApp (escribió «BAJA»).
CREATE TABLE public."Prospecto" (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  telefono_e164              text NOT NULL UNIQUE,
  nombre                     text,
  rut                        text,
  rango_sueldo               text,
  estado                     text,
  paso                       text,
  datos                      jsonb DEFAULT '{}',
  ultimo_texto_entrante      text,
  ultimo_entrante_en         timestamptz,
  actualizado_en             timestamptz DEFAULT now(),
  cliente_id                 bigint REFERENCES public."Cliente"(id) ON DELETE SET NULL,
  tiene_rsh                  boolean,
  tiene_propiedad            boolean,
  subsidio_previo            boolean,
  ahorro_ok                  boolean,
  trabajo_indefinido         boolean,
  complemento_renta          boolean,
  motivo_no_interesado       text,
  fecha_tentativa_recontacto text,
  opt_out                    boolean DEFAULT false,
  paso_origen_no_interesado  text,
  motivo_no_califica         text,
  quiere_contacto_ejecutivo  boolean,
  intencion_regularizar      text,
  proyecto_id                uuid REFERENCES public."Proyecto"(id),
  renta_mensual              int,
  numero_integrantes         int,
  pendiente_respuesta        boolean DEFAULT false
);
CREATE INDEX idx_prospectos_cliente_id ON public."Prospecto"(cliente_id);
-- (La base también tiene el índice único prospectos_telefono_e164_key sobre telefono_e164, igual que la restricción UNIQUE.)

-- ── Mensajes WhatsApp ────────────────────────────────────────────
CREATE TABLE public."Mensaje" (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  direccion     text NOT NULL CHECK (direccion IN ('entrante', 'saliente')),
  wa_id_mensaje text,
  texto         text,
  creado_en     timestamptz NOT NULL DEFAULT now(),
  cliente_id    bigint REFERENCES public."Cliente"(id) ON UPDATE CASCADE ON DELETE CASCADE,
  prospecto_id  uuid   REFERENCES public."Prospecto"(id) ON DELETE CASCADE
);
CREATE INDEX idx_mensajes_prospecto ON public."Mensaje"(prospecto_id, creado_en DESC);

-- ── Documentos ───────────────────────────────────────────────────
CREATE TABLE public."Documento" (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prospecto_id   uuid REFERENCES public."Prospecto"(id) ON DELETE CASCADE,
  tipo           text NOT NULL CHECK (tipo IN ('liquidacion_sueldo', 'certificado_afp', 'carnet_identidad', 'antiguedad_laboral', 'libreta_ahorro', 'informe_deudas', 'otro',
                                                'cedula_complementador', 'liquidaciones_complementador', 'certificado_afp_complementador', 'antiguedad_laboral_complementador',
                                                'certificado_rsh', 'carpeta_tributaria_sii', 'declaracion_anual_impuestos', 'cartola_ahorro')),
  nombre_archivo text,
  url_storage    text,
  wa_media_id    text,
  mime_type      text,
  verificado     boolean DEFAULT false,
  creado_en      timestamptz DEFAULT now()
);
CREATE INDEX idx_documentos_prospecto ON public."Documento"(prospecto_id, tipo);

-- ── Ejecutivos Bancarios ─────────────────────────────────────────
-- La entidad es texto libre (la lista de opciones está en frontend/admin.html). telefono es numeric en la base.
CREATE TABLE public."EjecutivoBancario" (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ejecutivo    text NOT NULL,
  entidad      text NOT NULL,
  email        text NOT NULL,
  inmobiliaria text,
  proyecto     uuid REFERENCES public."Proyecto"(id) ON UPDATE CASCADE,
  telefono     numeric,
  disponible   boolean
);
CREATE TABLE public."ProyectoEjecutivo" (                 -- qué proyectos atiende cada ejecutivo bancario (sin clave primaria en la base)
  ejecutivo_id bigint NOT NULL REFERENCES public."EjecutivoBancario"(id) ON DELETE CASCADE,
  proyecto_id  uuid   NOT NULL REFERENCES public."Proyecto"(id)
);

-- ── Plantillas WA — configuración de variables ───────────────────
CREATE TABLE public."PlantillaConfig" (
  id            serial PRIMARY KEY,
  template_name text NOT NULL UNIQUE,
  variables     jsonb NOT NULL DEFAULT '[]',
  updated_at    timestamptz DEFAULT now(),
  param_names   text[]
);

-- ── Formulario público de perfilamiento ──────────────────────────
CREATE TABLE public."Formulario" (
  id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  token                    uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  created_at               timestamptz DEFAULT now(),
  completado_at            timestamptz,
  nombre                   text,
  email                    text,
  telefono                 text,
  rsh                      text,
  cmf                      text,
  tiene_propiedad          boolean,
  numero_integrantes       int,
  tipo_ingreso             text,
  antiguedad_meses         int,
  tiene_liquidaciones      boolean,
  tiene_cotizaciones_afp   boolean,
  anos_declaracion_renta   int,
  tiene_carpeta_tributaria boolean,
  tiene_boletas_honorarios boolean,
  tiene_cert_antiguedad    boolean,
  resultado                jsonb,
  cliente_id               bigint REFERENCES public."Cliente"(id) ON DELETE SET NULL,
  paso_actual              int DEFAULT 1,
  proyecto_id              uuid REFERENCES public."Proyecto"(id)
);

-- ── Avisos a ejecutivos (cola persistente; migraciones/2026-10-31_aviso_ejecutivo.sql) ──
CREATE TABLE public."AvisoEjecutivo" (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  cliente_id     int  NOT NULL REFERENCES public."Cliente"(id) ON DELETE CASCADE,
  tipo           text NOT NULL CHECK (tipo IN ('necesita_ayuda', 'primer_documento', 'docs_completos')),
  nombre_cliente text,
  estado         text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'enviando', 'enviado', 'omitido', 'error')),
  motivo         text,
  creado_en      timestamptz NOT NULL DEFAULT now(),
  enviar_desde   timestamptz NOT NULL DEFAULT now(),
  reclamado_en   timestamptz,
  enviado_en     timestamptz,
  intentos       int NOT NULL DEFAULT 0,
  wamid          text
);
CREATE INDEX aviso_cola ON public."AvisoEjecutivo"(enviar_desde) WHERE estado = 'pendiente';
CREATE UNIQUE INDEX aviso_unico_activo ON public."AvisoEjecutivo"(cliente_id, tipo) WHERE estado IN ('pendiente', 'enviando');

-- ── Notas internas del equipo sobre un cliente ───────────────────
CREATE TABLE public."NotaCliente" (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  cliente_id   int  NOT NULL REFERENCES public."Cliente"(id) ON DELETE CASCADE,
  usuario_id   uuid REFERENCES public."Usuario"(id) ON DELETE SET NULL,
  autor_nombre text,
  texto        text NOT NULL CHECK (char_length(btrim(texto)) BETWEEN 1 AND 2000),
  creado_en    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX nota_cliente_por_cliente ON public."NotaCliente"(cliente_id, creado_en DESC);

-- ── Cotizaciones enviadas (botón «Cotizar») ──────────────────────
CREATE TABLE public."Cotizacion" (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  cliente_id     int  NOT NULL REFERENCES public."Cliente"(id) ON DELETE CASCADE,
  usuario_id     uuid REFERENCES public."Usuario"(id) ON DELETE SET NULL,
  usuario_nombre text,
  canal          text NOT NULL CHECK (canal IN ('correo', 'whatsapp')),
  destino        text NOT NULL CHECK (char_length(destino) BETWEEN 3 AND 200),
  estado         text NOT NULL CHECK (estado IN ('enviada', 'error')),
  detalle        text CHECK (detalle IS NULL OR char_length(detalle) <= 500),
  valores        jsonb NOT NULL,
  creado_en      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cotizacion_por_cliente ON public."Cotizacion"(cliente_id, creado_en DESC);

-- ── Registro de envíos fallidos de WhatsApp ──────────────────────
CREATE TABLE public."LogWhatsapp" (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  cliente_id    bigint REFERENCES public."Cliente"(id) ON DELETE SET NULL,
  wamid         text,
  error_code    text,
  error_message text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_logwhatsapp_cliente ON public."LogWhatsapp"(cliente_id);

-- ── Gasto: consumo de IA (Anthropic) y mensajes de WhatsApp (Meta) ─
CREATE TABLE public."GastoUso" (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  creado_en      timestamptz NOT NULL DEFAULT now(),
  fuente         text NOT NULL CHECK (fuente IN ('anthropic', 'meta')),
  detalle        text CHECK (detalle IS NULL OR char_length(detalle) <= 80),   -- Anthropic: el modelo · Meta: la categoría (marketing, utility, authentication, service, gratis)
  cliente_id     bigint,        -- sin llave foránea: si se borra el cliente, el gasto se conserva
  prospecto_id   text,
  proyecto_id    text,
  tokens_entrada int CHECK (tokens_entrada IS NULL OR tokens_entrada >= 0),
  tokens_salida  int CHECK (tokens_salida  IS NULL OR tokens_salida  >= 0),
  cantidad       int NOT NULL DEFAULT 1 CHECK (cantidad >= 1),
  referencia     text UNIQUE    -- Meta: el id del mensaje (un aviso repetido no se cuenta dos veces)
);
CREATE INDEX gastouso_por_fecha   ON public."GastoUso"(creado_en);
CREATE INDEX gastouso_por_cliente ON public."GastoUso"(cliente_id);
CREATE TABLE public."TarifaGasto" (
  clave          text PRIMARY KEY,     -- anthropic_entrada, anthropic_salida, meta_marketing, meta_utility, meta_authentication, meta_service, usd_clp
  valor          numeric NOT NULL CHECK (valor >= 0),
  actualizado_en timestamptz NOT NULL DEFAULT now()
);

-- ── Estimador de comisiones ──────────────────────────────────────
CREATE TABLE public."PlanComision" (
  clave      text PRIMARY KEY,
  nombre     text NOT NULL,
  ticket_uf  numeric NOT NULL CHECK (ticket_uf > 0),
  porcentaje numeric NOT NULL CHECK (porcentaje >= 0 AND porcentaje <= 100),
  bono_2_clp numeric CHECK (bono_2_clp IS NULL OR bono_2_clp >= 0),
  bono_3_clp numeric CHECK (bono_3_clp IS NULL OR bono_3_clp >= 0),
  condicion  text,
  orden      int NOT NULL DEFAULT 0,
  activo     boolean NOT NULL DEFAULT true
);
CREATE TABLE public."PlanComisionAsignacion" (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  plan_clave      text NOT NULL REFERENCES public."PlanComision"(clave) ON DELETE CASCADE,
  proyecto_id     text,
  inmobiliaria_id bigint,
  CHECK ((proyecto_id IS NOT NULL) <> (inmobiliaria_id IS NOT NULL))
);
CREATE UNIQUE INDEX plancomisionasignacion_unica ON public."PlanComisionAsignacion"(plan_clave, COALESCE(proyecto_id, ''), COALESCE(inmobiliaria_id, 0));

-- ── Consentimientos de privacidad ────────────────────────────────
CREATE TABLE public."ConsentimientoPrivacidad" (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  origen           text NOT NULL CHECK (origen IN ('formulario', 'contacto', 'newsletter')),
  correo           text,
  formulario_token uuid,
  politica_version text NOT NULL,
  aceptado_en      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX consentimiento_privacidad_correo ON public."ConsentimientoPrivacidad"(lower(correo));

-- ── Directorio de subsidios (migraciones/2026-11-05_directorio_*.sql) ──
CREATE TABLE public."Fuente" (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  titulo      text NOT NULL,
  url         text NOT NULL UNIQUE CHECK (url ~ '^https://'),
  organismo   text NOT NULL,
  tipo        text NOT NULL CHECK (tipo IN ('ficha', 'pagina_llamado', 'folleto', 'decreto', 'resolucion', 'otro')),
  publicada_en date,
  leida_en    date NOT NULL,
  creada_en   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public."Subsidio" (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug           text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  nombre_corto   text NOT NULL,
  nombre_oficial text NOT NULL,
  decreto        text,
  grupo          text NOT NULL CHECK (grupo IN ('comprar', 'construir', 'arrendar', 'mejorar', 'rural', 'valech', 'otro')),
  alcance        text NOT NULL DEFAULT 'completa' CHECK (alcance IN ('completa', 'ligera')),
  estado         text NOT NULL DEFAULT 'borrador' CHECK (estado IN ('borrador', 'publicado', 'archivado')),
  postulacion    text NOT NULL DEFAULT 'por_llamados' CHECK (postulacion IN ('por_llamados', 'todo_el_ano', 'otra')),
  orden          int NOT NULL DEFAULT 100,
  resumen        text NOT NULL,
  fuentes        bigint[] NOT NULL DEFAULT '{}',
  revisado_en    date,
  revisado_por   text,
  publicado_en   timestamptz,
  creado_en      timestamptz NOT NULL DEFAULT now(),
  actualizado_en timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subsidio_estado_orden ON public."Subsidio"(estado, orden);
CREATE TABLE public."SubsidioBloque" (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  subsidio_id bigint NOT NULL REFERENCES public."Subsidio"(id) ON DELETE CASCADE,
  clave       text NOT NULL CHECK (clave IN ('para_quien', 'que_entrega', 'antes_de_postular', 'como_se_postula', 'despues_de_ganarlo', 'compatibilidades', 'errores_frecuentes', 'otro')),
  titulo      text NOT NULL,
  cuerpo      text NOT NULL,
  orden       int NOT NULL DEFAULT 100,
  fuentes     bigint[] NOT NULL DEFAULT '{}'
);
CREATE INDEX subsidiobloque_subsidio ON public."SubsidioBloque"(subsidio_id, orden);
CREATE TABLE public."SubsidioDato" (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  subsidio_id  bigint NOT NULL REFERENCES public."Subsidio"(id) ON DELETE CASCADE,
  clave        text NOT NULL,
  etiqueta     text NOT NULL,
  valor_texto  text NOT NULL,
  valor_numero numeric,
  unidad       text NOT NULL DEFAULT 'texto' CHECK (unidad IN ('UF', 'CLP', 'porcentaje', 'meses', 'anios', 'texto')),
  llamado      text,
  fuente_id    bigint NOT NULL REFERENCES public."Fuente"(id),
  estado       text NOT NULL DEFAULT 'por_confirmar' CHECK (estado IN ('por_confirmar', 'verificado')),
  revisado_en  date,
  revisado_por text,
  nota         text,
  orden        int NOT NULL DEFAULT 100,
  UNIQUE (subsidio_id, clave)
);
CREATE INDEX subsidiodato_subsidio ON public."SubsidioDato"(subsidio_id, orden);
CREATE TABLE public."GlosarioTermino" (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  termino    text NOT NULL UNIQUE,
  definicion text NOT NULL,
  fuente_id  bigint REFERENCES public."Fuente"(id),
  estado     text NOT NULL DEFAULT 'borrador' CHECK (estado IN ('borrador', 'publicado', 'archivado')),
  orden      int NOT NULL DEFAULT 100
);
CREATE TABLE public."Guia" (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug           text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  titulo         text NOT NULL,
  resumen        text NOT NULL,
  cuerpo         text NOT NULL,
  estado         text NOT NULL DEFAULT 'borrador' CHECK (estado IN ('borrador', 'publicado', 'archivado')),
  orden          int NOT NULL DEFAULT 100,
  fuentes        bigint[] NOT NULL DEFAULT '{}',
  revisado_en    date,
  revisado_por   text,
  creada_en      timestamptz NOT NULL DEFAULT now(),
  actualizada_en timestamptz NOT NULL DEFAULT now()
);

-- ── Scraping de BuyDepa (semi-nuevos) ────────────────────────────
CREATE TABLE public."ScrapingLog" (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fuente          text NOT NULL,
  ejecutado_en    timestamptz NOT NULL DEFAULT now(),
  ejecutado_por   uuid REFERENCES public."Usuario"(id),
  creados         int NOT NULL DEFAULT 0,
  actualizados    int NOT NULL DEFAULT 0,
  desactivados    int NOT NULL DEFAULT 0,
  errores         int NOT NULL DEFAULT 0,
  detalle_errores jsonb,
  estado          text NOT NULL DEFAULT 'ok'
);
CREATE TABLE public.log_depas (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  detectado_en      timestamptz NOT NULL DEFAULT now(),
  direccion         text,
  fuente_externo_id text NOT NULL,
  campo             text NOT NULL,
  valor_anterior    text,
  valor_nuevo       text
);
CREATE INDEX log_depas_fuente_idx ON public.log_depas(fuente_externo_id, detectado_en DESC);

-- ── Log de correos enviados (Gmail API) ──────────────────────────
CREATE TABLE public.log_correos (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo          text NOT NULL,
  destinatario  text NOT NULL,
  asunto        text,
  estado        text NOT NULL CHECK (estado IN ('enviado', 'error')),
  detalle_error text,
  usuario_id    uuid REFERENCES public."Usuario"(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_log_correos_created_at   ON public.log_correos(created_at DESC);
CREATE INDEX idx_log_correos_destinatario ON public.log_correos(destinatario);
CREATE INDEX idx_log_correos_estado       ON public.log_correos(estado);

-- ── Newsletter y progreso de usuario — plataforma pública QueSubsidio.cl ──
CREATE TABLE public.newsletter (
  id        bigserial PRIMARY KEY,
  email     text NOT NULL UNIQUE,
  creado_en timestamptz DEFAULT now()
);
CREATE TABLE public.progreso_usuario (
  user_id    uuid NOT NULL REFERENCES auth.users(id),
  mundo      text NOT NULL,
  req_id     text NOT NULL,
  completado boolean DEFAULT true,
  marcado_en timestamptz DEFAULT now(),
  PRIMARY KEY (user_id, mundo, req_id)
);


-- ================================================================
-- Funciones y disparadores
-- ================================================================

-- Pone la fecha en que cambió el estado de gestión de un cliente.
CREATE OR REPLACE FUNCTION public.cliente_marca_estado_gestion() RETURNS trigger LANGUAGE plpgsql AS $$
begin
  if tg_op = 'INSERT' then
    if new.estado_gestion is not null and new.estado_gestion_en is null then
      new.estado_gestion_en := now();
    end if;
  elsif new.estado_gestion is distinct from old.estado_gestion then
    new.estado_gestion_en := now();
  end if;
  return new;
end $$;
CREATE TRIGGER cliente_estado_gestion_en BEFORE INSERT OR UPDATE OF estado_gestion ON public."Cliente"
  FOR EACH ROW EXECUTE FUNCTION cliente_marca_estado_gestion();

CREATE OR REPLACE FUNCTION public.perfiles_touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;
CREATE TRIGGER perfiles_updated_at BEFORE UPDATE ON public."Usuario" FOR EACH ROW EXECUTE FUNCTION perfiles_touch_updated_at();

CREATE OR REPLACE FUNCTION public.set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.actualizado_en = NOW();
  RETURN NEW;
END;
$$;
CREATE TRIGGER trg_proyectos_actualizado_en BEFORE UPDATE ON public."Proyecto" FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Reclama los avisos pendientes para enviarlos (cola persistente de avisos a ejecutivos; vuelve a poner en cola los que quedaron «enviando» demasiado tiempo).
CREATE OR REPLACE FUNCTION public.reclamar_avisos(max_avisos integer DEFAULT 10, lease_minutos integer DEFAULT 10)
RETURNS SETOF "AvisoEjecutivo" LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
begin
  update "AvisoEjecutivo"
     set estado = case when intentos >= 3 then 'error' else 'pendiente' end,
         motivo = case when intentos >= 3 then 'agotó los intentos' else motivo end,
         reclamado_en = null
   where estado = 'enviando' and reclamado_en < now() - make_interval(mins => lease_minutos);
  return query
  update "AvisoEjecutivo" a
     set estado = 'enviando', reclamado_en = now(), intentos = a.intentos + 1
   where a.id in (select id from "AvisoEjecutivo"
                   where estado = 'pendiente' and enviar_desde <= now()
                   order by enviar_desde, id limit max_avisos for update skip locked)
  returning a.*;
end $$;

-- Activa la seguridad por filas en cada tabla nueva del esquema public (la usa un disparador de eventos de la base).
CREATE OR REPLACE FUNCTION public.rls_auto_enable() RETURNS event_trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$$;
