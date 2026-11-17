-- «Fecha de asignacion» de cada cliente: desde cuando lo gestiona su ejecutivo actual (la usaran el avance/prioridad y el seguimiento de brokers).
-- Solo AGREGA una columna y un disparador a Cliente: no cambia ningun dato existente salvo rellenar la fecha de los clientes que ya tienen creado_en. Se ejecuta por bloques en el SQL Editor de Supabase.
-- Regla: el cliente empieza su «vida» cuando lo crea el mismo ejecutivo o cuando se le asigna (a mano, en bloque o repartiendo). Si el cliente queda sin ejecutivo, la fecha queda vacia.

-- A. Crear la columna y el disparador (se puede repetir sin problema)
alter table public."Cliente" add column if not exists asignado_en timestamptz;

create or replace function public.cliente_marca_asignado() returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.usuario_id is not null and new.asignado_en is null then
      new.asignado_en := now();
    end if;
  elsif new.usuario_id is distinct from old.usuario_id then
    new.asignado_en := case when new.usuario_id is null then null else now() end;
  end if;
  return new;
end $$;

drop trigger if exists cliente_asignado_en on public."Cliente";
create trigger cliente_asignado_en before insert or update of usuario_id on public."Cliente"
  for each row execute function public.cliente_marca_asignado();

-- B. Rellenar los clientes que ya existen: se usa su fecha de creacion (los anteriores al 2026-11-06 no la tienen y quedan sin fecha, a proposito: no se inventa)
update public."Cliente" set asignado_en = creado_en where usuario_id is not null and asignado_en is null and creado_en is not null;

-- C. Comprobar (solo lectura): debe decir columna existe, disparador existe, y cuantos clientes tienen fecha
select 'C1 columna' as comprobacion, coalesce((select column_name::text from information_schema.columns where table_schema = 'public' and table_name = 'Cliente' and column_name = 'asignado_en'), 'NO EXISTE') as resultado
union all
select 'C2 disparador', coalesce((select tgname::text from pg_trigger where tgname = 'cliente_asignado_en' and not tgisinternal), 'NO EXISTE')
union all
select 'C3 clientes con fecha', count(*)::text from public."Cliente" where asignado_en is not null
union all
select 'C4 clientes con ejecutivo y sin fecha', count(*)::text from public."Cliente" where usuario_id is not null and asignado_en is null;

-- D. Revertir (solo si hiciera falta)
--   drop trigger if exists cliente_asignado_en on public."Cliente";
--   drop function if exists public.cliente_marca_asignado();
--   alter table public."Cliente" drop column if exists asignado_en;
