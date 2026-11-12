-- Seguimiento del gasto por cliente (pestaña «Gasto» del panel de administración): lo que consume la IA (Anthropic) y los mensajes de WhatsApp (Meta).
-- Solo AGREGA dos tablas: no toca ninguna existente. La ejecuta el dueño en el SQL Editor de Supabase, por bloques.
-- ⚠ Hay que ejecutar el bloque A ANTES de usar la pestaña. Mientras no exista, el bot y los envíos funcionan igual (solo no se anota el consumo).
-- Se guarda el CONSUMO (tokens, mensajes por categoría), no el dinero: el costo se calcula con las tarifas de la pestaña.

-- A. Crear (se puede repetir sin problema). Sin comentarios dentro de las tablas: se pega limpio en el SQL Editor.
create table if not exists public."GastoUso" (
  id bigint generated always as identity primary key,
  creado_en timestamptz not null default now(),
  fuente text not null check (fuente in ('anthropic', 'meta')),
  detalle text check (detalle is null or char_length(detalle) <= 80),
  cliente_id bigint,
  prospecto_id text,
  proyecto_id text,
  tokens_entrada int check (tokens_entrada is null or tokens_entrada >= 0),
  tokens_salida int check (tokens_salida is null or tokens_salida >= 0),
  cantidad int not null default 1 check (cantidad >= 1),
  referencia text unique
);
create index if not exists gastouso_por_fecha on public."GastoUso" (creado_en);
create index if not exists gastouso_por_cliente on public."GastoUso" (cliente_id);
alter table public."GastoUso" enable row level security;

create table if not exists public."TarifaGasto" (
  clave text primary key,
  valor numeric not null check (valor >= 0),
  actualizado_en timestamptz not null default now()
);
alter table public."TarifaGasto" enable row level security;

-- Que guarda cada columna de GastoUso: detalle = el modelo (Anthropic) o la categoria (Meta: marketing, utility, authentication, service);
-- cliente_id, prospecto_id y proyecto_id = a quien se atribuye (sin llaves foraneas: si se borra el cliente, el gasto se conserva);
-- referencia = el id del mensaje de Meta (un aviso repetido no se cuenta dos veces). TarifaGasto guarda las tarifas que confirma la administracion.

-- B. Comprobar (solo lectura; una sola consulta, devuelve 5 filas)
--  B1 y B2 tablas: deben decir GastoUso y TarifaGasto.   B3 columnas de GastoUso: 11.   B4 seguridad por filas: true en ambas.   B5 filas: 0 al crear.
select 'B1 tabla' as comprobacion, coalesce(to_regclass('public."GastoUso"')::text, 'NO EXISTE') as resultado
union all
select 'B2 tabla', coalesce(to_regclass('public."TarifaGasto"')::text, 'NO EXISTE')
union all
select 'B3 columnas de GastoUso', count(*)::text from information_schema.columns where table_schema = 'public' and table_name = 'GastoUso'
union all
select 'B4 seguridad por filas', coalesce((select relrowsecurity::text from pg_class where oid = to_regclass('public."GastoUso"')), 'NO EXISTE') || ' / ' || coalesce((select relrowsecurity::text from pg_class where oid = to_regclass('public."TarifaGasto"')), 'NO EXISTE')
union all
select 'B5 filas', coalesce((select count(*)::text from public."GastoUso"), '0');

-- C. Revertir (solo si hiciera falta; borra lo anotado)
--   drop table if exists public."GastoUso";
--   drop table if exists public."TarifaGasto";
