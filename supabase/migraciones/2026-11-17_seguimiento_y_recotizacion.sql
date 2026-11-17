-- Seguimiento automatico y «volvio a cotizar». Solo AGREGA 3 columnas a Cliente: no cambia ni borra ningun dato. Se ejecuta por bloques en el SQL Editor de Supabase.
--   recotizo_en           : cuando un cliente que YA existia volvio a aparecer en una carga de Excel (volvio a cotizar). La temperatura lo cuenta como interes renovado.
--   alerta_sin_atender_en : cuando se avisó que llevaba mas de 24 h sin atender (para avisar una sola vez por asignacion).
--   remarketing_en        : cuando se le envio el ultimo mensaje de remarketing (para no repetirlo antes de 7 dias).
-- IMPORTANTE: ejecutarlo ANTES de subir el cambio. Mientras no existan estas columnas, el calculo de temperatura sigue funcionando (sin la señal de recotizacion) y el seguimiento automatico no hace nada.

-- A. Crear las columnas (se puede repetir sin problema)
alter table public."Cliente" add column if not exists recotizo_en timestamptz;
alter table public."Cliente" add column if not exists alerta_sin_atender_en timestamptz;
alter table public."Cliente" add column if not exists remarketing_en timestamptz;

-- Para encontrar rapido a los que llevan dias sin gestion (solo clientes con ejecutivo y aun sin contactar / con contacto antiguo)
create index if not exists cliente_por_asignado_en on public."Cliente"(asignado_en) where usuario_id is not null;

-- B. Comprobar (solo lectura): debe decir 3 columnas y el indice
select 'B1 columnas nuevas' as comprobacion, count(*)::text as resultado
  from information_schema.columns where table_schema = 'public' and table_name = 'Cliente' and column_name in ('recotizo_en', 'alerta_sin_atender_en', 'remarketing_en')   -- debe decir 3
union all
select 'B2 indice por asignacion', coalesce((select indexname::text from pg_indexes where schemaname = 'public' and indexname = 'cliente_por_asignado_en'), 'NO EXISTE');

-- C. Revertir (solo si hiciera falta)
--   drop index if exists public.cliente_por_asignado_en;
--   alter table public."Cliente" drop column if exists remarketing_en, drop column if exists alerta_sin_atender_en, drop column if exists recotizo_en;
