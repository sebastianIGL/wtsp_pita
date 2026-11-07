-- Cotizaciones enviadas a clientes desde el botón «Cotizar» del panel (las usa app/routers/cotizaciones.py).
-- Solo AGREGA una tabla: no toca ninguna existente. La ejecuta el dueño en el SQL Editor de Supabase, por bloques.
-- ⚠ Hay que ejecutar el bloque A ANTES de usar «Cotizar» (si no existe, la cotización igual se envía pero no queda registrada).

-- A. Crear (se puede repetir sin problema)
create table if not exists public."Cotizacion" (
  id             bigint generated always as identity primary key,
  cliente_id     int  not null references public."Cliente"(id) on delete cascade,
  usuario_id     uuid references public."Usuario"(id) on delete set null,     -- quién la envió (si el usuario se borra, el registro se conserva)
  usuario_nombre text,                                                        -- su nombre al enviarla (se sigue viendo aunque cambie o se borre)
  canal          text not null check (canal in ('correo', 'whatsapp')),
  destino        text not null check (char_length(destino) between 3 and 200), -- el correo o el teléfono al que se envió
  estado         text not null check (estado in ('enviada', 'error')),
  detalle        text check (detalle is null or char_length(detalle) <= 500),  -- si falló: el motivo, en palabras para personas
  valores        jsonb not null,                                              -- foto de lo cotizado (vivienda, subsidio, ahorro, pie, crédito) tal como se envió
  creado_en      timestamptz not null default now()
);
create index if not exists cotizacion_por_cliente on public."Cotizacion" (cliente_id, creado_en desc);
alter table public."Cotizacion" enable row level security;   -- sin políticas: solo el backend (service role) la lee y escribe

-- B. Comprobar (solo lectura; una sola consulta, devuelve 4 filas)
--  B1 tabla: debe decir Cotizacion.   B2 columnas: 10.   B3 seguridad por filas: debe decir true.   B4 filas: 0 al crear.
select 'B1 tabla' as comprobacion, coalesce(to_regclass('public."Cotizacion"')::text, 'NO EXISTE') as resultado
union all
select 'B2 columnas', count(*)::text from information_schema.columns where table_schema = 'public' and table_name = 'Cotizacion'
union all
select 'B3 seguridad por filas', coalesce((select relrowsecurity::text from pg_class where oid = to_regclass('public."Cotizacion"')), 'NO EXISTE')
union all
select 'B4 filas', coalesce((select count(*)::text from public."Cotizacion"), '0');

-- C. Revertir (solo emergencia) ⚠ borra el registro de todas las cotizaciones enviadas
--   drop table if exists public."Cotizacion";
