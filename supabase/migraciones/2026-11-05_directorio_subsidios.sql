-- Directorio de subsidios y guías (Fase 1): tablas, protección y una ficha de ejemplo (DS49, en borrador).
-- La ejecuta el dueño en el SQL Editor de Supabase, por bloques. Solo AGREGA tablas: no toca ninguna existente.
-- Mientras las tablas no existan el sitio funciona igual (la ruta /subsidios/{nombre} responde «no encontrado»); mientras una ficha esté en
-- «borrador» no se ve en el sitio.

-- ═══ A. Crear las tablas (se puede repetir sin problema) ═══════════════════════════════════════════════════════════════════════════
create table if not exists public."Fuente" (
  id          bigint generated always as identity primary key,
  titulo      text not null,
  url         text not null unique check (url ~ '^https://'),
  organismo   text not null,                        -- MINVU, ChileAtiende, Biblioteca del Congreso…
  tipo        text not null check (tipo in ('ficha','pagina_llamado','folleto','decreto','resolucion','otro')),
  publicada_en date,                                -- fecha de la fuente, si la trae
  leida_en    date not null,                        -- el día en que alguien la leyó
  creada_en   timestamptz not null default now()
);

create table if not exists public."Subsidio" (
  id              bigint generated always as identity primary key,
  slug            text not null unique check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  nombre_corto    text not null,                    -- p. ej. DS49
  nombre_oficial  text not null,
  decreto         text,
  grupo           text not null check (grupo in ('comprar','construir','arrendar','mejorar','rural','valech','otro')),
  alcance         text not null default 'completa' check (alcance in ('completa','ligera')),
  estado          text not null default 'borrador' check (estado in ('borrador','publicado','archivado')),
  postulacion     text not null default 'por_llamados' check (postulacion in ('por_llamados','todo_el_ano','otra')),
  orden           integer not null default 100,
  resumen         text not null,                    -- «en una frase»
  fuentes         bigint[] not null default '{}',   -- ids de Fuente que respaldan la ficha en general
  revisado_en     date,
  revisado_por    text,
  publicado_en    timestamptz,
  creado_en       timestamptz not null default now(),
  actualizado_en  timestamptz not null default now()
);
create index if not exists subsidio_estado_orden on public."Subsidio" (estado, orden);

create table if not exists public."SubsidioBloque" (
  id           bigint generated always as identity primary key,
  subsidio_id  bigint not null references public."Subsidio"(id) on delete cascade,
  clave        text not null check (clave in ('para_quien','que_entrega','antes_de_postular','como_se_postula','despues_de_ganarlo','compatibilidades','errores_frecuentes','otro')),
  titulo       text not null,
  cuerpo       text not null,                       -- párrafos separados por línea en blanco; las líneas que empiezan con «- » son viñetas
  orden        integer not null default 100,
  fuentes      bigint[] not null default '{}'
);
create index if not exists subsidiobloque_subsidio on public."SubsidioBloque" (subsidio_id, orden);

create table if not exists public."SubsidioDato" (
  id            bigint generated always as identity primary key,
  subsidio_id   bigint not null references public."Subsidio"(id) on delete cascade,
  clave         text not null,                      -- p. ej. ahorro_minimo
  etiqueta      text not null,                      -- lo que se muestra: «Ahorro mínimo»
  valor_texto   text not null,                      -- lo que se muestra: «10 UF»
  valor_numero  numeric,
  unidad        text not null default 'texto' check (unidad in ('UF','CLP','porcentaje','meses','anios','texto')),
  llamado       text,                               -- si el dato es de un llamado concreto: «Primer Llamado Nacional 2026»
  fuente_id     bigint not null references public."Fuente"(id),
  estado        text not null default 'por_confirmar' check (estado in ('por_confirmar','verificado')),
  revisado_en   date,
  revisado_por  text,
  nota          text,
  orden         integer not null default 100,
  unique (subsidio_id, clave)
);
create index if not exists subsidiodato_subsidio on public."SubsidioDato" (subsidio_id, orden);

create table if not exists public."Guia" (
  id            bigint generated always as identity primary key,
  slug          text not null unique check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  titulo        text not null,
  resumen       text not null,
  cuerpo        text not null,
  estado        text not null default 'borrador' check (estado in ('borrador','publicado','archivado')),
  orden         integer not null default 100,
  fuentes       bigint[] not null default '{}',
  revisado_en   date,
  revisado_por  text,
  creada_en     timestamptz not null default now(),
  actualizada_en timestamptz not null default now()
);

create table if not exists public."GlosarioTermino" (
  id          bigint generated always as identity primary key,
  termino     text not null unique,
  definicion  text not null,
  fuente_id   bigint references public."Fuente"(id),
  estado      text not null default 'borrador' check (estado in ('borrador','publicado','archivado')),
  orden       integer not null default 100
);

-- Sin políticas: solo el servidor (con su clave de servicio) lee y escribe; nadie desde el navegador.
alter table public."Fuente"          enable row level security;
alter table public."Subsidio"        enable row level security;
alter table public."SubsidioBloque"  enable row level security;
alter table public."SubsidioDato"    enable row level security;
alter table public."Guia"            enable row level security;
alter table public."GlosarioTermino" enable row level security;

-- ═══ A2. Ficha de ejemplo: DS49 (compra), EN BORRADOR (no se ve en el sitio). Cada dato sale de la hoja de la Fase 0 que el dueño revisó. ════
-- Se puede ejecutar una sola vez (si se repite, no duplica: usa «on conflict do nothing»).
insert into public."Fuente" (titulo, url, organismo, tipo, leida_en) values
  ('Subsidio para comprar una vivienda construida de hasta 950 UF (DS49)', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-comprar-una-vivienda-construida-de-hasta-950-uf-ds49/', 'MINVU', 'ficha', date '2026-11-05'),
  ('Subsidio para comprar una vivienda de hasta 950 UF (llamado individual DS Nº 49)', 'https://www.chileatiende.gob.cl/fichas/37960-subsidio-para-comprar-una-vivienda-de-hasta-950-uf-llamado-individual-ds-n-49', 'ChileAtiende', 'ficha', date '2026-11-05')
on conflict (url) do nothing;

insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes, revisado_en, revisado_por)
select 'ds49-compra', 'DS49', 'Fondo Solidario de Elección de Vivienda (compra de vivienda construida)', 'DS 49', 'comprar', 'completa', 'borrador', 'por_llamados', 10,
       'Subsidio para familias que no tienen vivienda y viven en situación de vulnerabilidad social y necesidad habitacional, para comprar una vivienda construida de hasta 950 UF.',
       array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-comprar-una-vivienda-construida-de-hasta-950-uf-ds49/', 'https://www.chileatiende.gob.cl/fichas/37960-subsidio-para-comprar-una-vivienda-de-hasta-950-uf-llamado-individual-ds-n-49') order by id),
       date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)'
where not exists (select 1 from public."Subsidio" where slug = 'ds49-compra');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, s.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que no son dueñas de una vivienda y viven en situación de vulnerabilidad social y necesidad habitacional.\n\n- Hasta el 40% más vulnerable del Registro Social de Hogares.\n- Personas solas: solo pueden postular las personas mayores, los viudos y viudas, o quienes tienen discapacidad.', 10),
       ('que_entrega', 'Qué entrega', E'Un subsidio base desde 314 UF (varía según la comuna), más complementos que dependen de la vivienda y de la familia. Por ejemplo, 200 UF por localización en zonas urbanas.', 20),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Tener 18 años o más y cédula de identidad vigente (las personas extranjeras, con residencia definitiva).\n- Estar inscrito en el Registro Social de Hogares.\n- Una cuenta de ahorro para la vivienda con al menos 10 UF.', 30)
     ) as b(clave, titulo, cuerpo, orden)
where s.slug = 'ds49-compra' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, fuente_id, estado, revisado_en, revisado_por, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad,
       (select id from public."Fuente" where url = d.url), 'verificado', date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo',        'Registro Social de Hogares', 'Hasta el 40% más vulnerable', 40, 'porcentaje', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-comprar-una-vivienda-construida-de-hasta-950-uf-ds49/', 10),
       ('ahorro_minimo',     'Ahorro mínimo', '10 UF', 10, 'UF', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-comprar-una-vivienda-construida-de-hasta-950-uf-ds49/', 20),
       ('precio_maximo',     'Precio máximo de la vivienda', 'Hasta 950 UF', 950, 'UF', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-comprar-una-vivienda-construida-de-hasta-950-uf-ds49/', 30),
       ('subsidio_base',     'Subsidio base', 'Desde 314 UF', 314, 'UF', 'https://www.chileatiende.gob.cl/fichas/37960-subsidio-para-comprar-una-vivienda-de-hasta-950-uf-llamado-individual-ds-n-49', 40),
       ('complemento_urbano','Complemento por localización en zona urbana', '200 UF', 200, 'UF', 'https://www.chileatiende.gob.cl/fichas/37960-subsidio-para-comprar-una-vivienda-de-hasta-950-uf-llamado-individual-ds-n-49', 50),
       ('edad_minima',       'Edad mínima', '18 años', 18, 'anios', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-comprar-una-vivienda-construida-de-hasta-950-uf-ds49/', 60)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, url, orden)
where s.slug = 'ds49-compra'
on conflict (subsidio_id, clave) do nothing;

-- ═══ B. Comprobar (solo lectura; devuelve todo en una sola tabla) ═════════════════════════════════════════════════════════════════════
--   select 'tablas con seguridad activa (deben ser 6)' as comprobacion, count(*)::text as resultado
--     from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind = 'r' and c.relrowsecurity
--      and c.relname in ('Fuente','Subsidio','SubsidioBloque','SubsidioDato','Guia','GlosarioTermino')
--   union all select 'fuentes (debe ser 2)', count(*)::text from public."Fuente"
--   union all select 'ficha de ejemplo (debe decir ds49-compra · borrador)', coalesce(string_agg(slug || ' · ' || estado, ', '), 'no hay') from public."Subsidio"
--   union all select 'bloques (debe ser 3)', count(*)::text from public."SubsidioBloque"
--   union all select 'datos verificados (debe ser 6)', count(*)::text from public."SubsidioDato" where estado = 'verificado';

-- ═══ D. Ver la ficha de ejemplo en el sitio / ocultarla de nuevo (opcional) ═══════════════════════════════════════════════════════════
--   update public."Subsidio" set estado = 'publicado', publicado_en = now() where slug = 'ds49-compra';   -- se ve en quesubsidio.cl/subsidios/ds49-compra
--   update public."Subsidio" set estado = 'borrador' where slug = 'ds49-compra';                          -- vuelve a ocultarse

-- ═══ C. Revertir (solo emergencia) ⚠ BORRA las tablas del directorio y todo lo que tengan ════════════════════════════════════════════════
--   drop table if exists public."SubsidioDato", public."SubsidioBloque", public."Guia", public."GlosarioTermino", public."Subsidio", public."Fuente" cascade;
