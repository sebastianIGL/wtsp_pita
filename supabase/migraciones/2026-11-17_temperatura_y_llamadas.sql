-- Temperatura (puntaje 0-99) guardada en cada cliente + registro de llamadas.
-- Solo AGREGA columnas a Cliente y una tabla nueva: no cambia ni borra ningun dato existente. Se ejecuta por bloques en el SQL Editor de Supabase.
-- Por que se guarda: con miles de clientes por ejecutivo, calcular el puntaje en pantalla seria lento y no se podria ordenar, filtrar ni avisar entre paginas.

-- A. Columnas de la temperatura en Cliente (se puede repetir sin problema)
alter table public."Cliente" add column if not exists temperatura_base smallint;          -- piso que dan los hitos (70 respondio la propuesta, 85 envio documentos, 99 pidio ejecutivo)
alter table public."Cliente" add column if not exists temperatura smallint;               -- puntaje final 0-99 (hitos + senales)
alter table public."Cliente" add column if not exists temperatura_en timestamptz;         -- cuando se calculo por ultima vez
alter table public."Cliente" add column if not exists temperatura_motivo text;            -- motivo corto («pidio ejecutivo», «se trabo en RSH», «se enfria»...)

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'cliente_temperatura_rango') then
    alter table public."Cliente" add constraint cliente_temperatura_rango check (temperatura is null or temperatura between 0 and 99);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'cliente_temperatura_base_rango') then
    alter table public."Cliente" add constraint cliente_temperatura_base_rango check (temperatura_base is null or temperatura_base between 0 and 99);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'cliente_temperatura_motivo_largo') then
    alter table public."Cliente" add constraint cliente_temperatura_motivo_largo check (temperatura_motivo is null or char_length(temperatura_motivo) <= 120);
  end if;
end $$;

-- Para ordenar «los mas calientes primero» de cada ejecutivo sin recorrer todos sus clientes
create index if not exists cliente_por_temperatura on public."Cliente"(usuario_id, temperatura desc nulls last);

-- B. Registro de llamadas (boton «Registre una llamada»)
create table if not exists public."LlamadaCliente" (
  id             bigint generated always as identity primary key,
  cliente_id     bigint not null references public."Cliente"(id) on delete cascade,
  usuario_id     uuid references public."Usuario"(id) on delete set null,
  usuario_nombre text,
  resultado      text not null check (resultado in ('contesto', 'no_contesto', 'buzon', 'numero_incorrecto')),
  nota           text check (nota is null or char_length(nota) <= 500),
  creado_en      timestamptz not null default now()
);
create index if not exists llamada_cliente_por_cliente on public."LlamadaCliente"(cliente_id, creado_en desc);
alter table public."LlamadaCliente" enable row level security;   -- sin politicas: solo el backend (service role) la lee y escribe

-- C. Comprobar (solo lectura): las 4 columnas, el indice, la tabla y su seguridad
select 'C1 columnas de temperatura' as comprobacion, count(*)::text as resultado
  from information_schema.columns where table_schema = 'public' and table_name = 'Cliente' and column_name in ('temperatura_base', 'temperatura', 'temperatura_en', 'temperatura_motivo')   -- debe decir 4
union all
select 'C2 indice por temperatura', coalesce((select indexname::text from pg_indexes where schemaname = 'public' and indexname = 'cliente_por_temperatura'), 'NO EXISTE')
union all
select 'C3 tabla de llamadas', coalesce((select to_regclass('public."LlamadaCliente"')::text), 'NO EXISTE')
union all
select 'C4 seguridad por filas de llamadas', coalesce((select relrowsecurity::text from pg_class where oid = to_regclass('public."LlamadaCliente"')), 'NO EXISTE')   -- debe decir true
union all
select 'C5 clientes con temperatura', count(*)::text from public."Cliente" where temperatura is not null;   -- 0 hasta que se construya el calculo

-- D. Revertir (solo si hiciera falta)
--   drop table if exists public."LlamadaCliente";
--   drop index if exists public.cliente_por_temperatura;
--   alter table public."Cliente" drop column if exists temperatura_motivo, drop column if exists temperatura_en, drop column if exists temperatura, drop column if exists temperatura_base;
