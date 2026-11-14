-- Estimador de comisiones (herramienta «Estimador de comisiones» del panel): los planes de comision de la planilla y a que proyectos se aplica cada uno.
-- Solo AGREGA tablas: no toca ninguna existente. La ejecuta el dueno en el SQL Editor de Supabase, por bloques.
-- Los montos son los de la planilla (valores brutos). Se pueden cambiar despues con un update, sin tocar el sistema.

-- A. Crear las tablas (se puede repetir sin problema)
create table if not exists public."PlanComision" (
  clave text primary key,
  nombre text not null,
  ticket_uf numeric not null check (ticket_uf > 0),
  porcentaje numeric not null check (porcentaje >= 0 and porcentaje <= 100),
  bono_2_clp numeric check (bono_2_clp is null or bono_2_clp >= 0),
  bono_3_clp numeric check (bono_3_clp is null or bono_3_clp >= 0),
  condicion text,
  orden int not null default 0,
  activo boolean not null default true
);
alter table public."PlanComision" enable row level security;

create table if not exists public."PlanComisionAsignacion" (
  id bigint generated always as identity primary key,
  plan_clave text not null references public."PlanComision"(clave) on delete cascade,
  proyecto_id text,
  inmobiliaria_id bigint,
  check ((proyecto_id is not null) <> (inmobiliaria_id is not null))
);
create unique index if not exists plancomisionasignacion_unica on public."PlanComisionAsignacion" (plan_clave, coalesce(proyecto_id, ''), coalesce(inmobiliaria_id, 0));
alter table public."PlanComisionAsignacion" enable row level security;

-- B. Cargar los planes de la planilla (se puede repetir: actualiza los montos)
insert into public."PlanComision" (clave, nombre, ticket_uf, porcentaje, bono_2_clp, bono_3_clp, condicion, orden) values
  ('vinedos_ds19', 'Viñedos de Rengo DS19', 2400, 0.27, null, 300000, 'Comisión única contra promesa', 1),
  ('vinedos_ds1', 'Viñedos de Rengo DS1-2/3', 1600, 0.30, 200000, 500000, 'Comisión única contra promesa', 2),
  ('seminuevos', 'Seminuevos', 1957, 1.00, null, 1000000, '50% a la promesa, 50% a la escritura', 3),
  ('subsidios_futura', 'Proyectos Subsidios.cl entrega futura', 2600, 0.15, null, null, 'Comisión única contra promesa', 4),
  ('subsidios_inmediata', 'Proyectos Subsidios.cl entrega inmediata', 2800, 0.30, null, 100000, 'Comisión única contra promesa', 5),
  ('jardines_volcanes', 'Jardines de los volcanes', 2600, 0.30, null, 100000, 'Comisión única contra promesa', 6)
on conflict (clave) do update set nombre = excluded.nombre, ticket_uf = excluded.ticket_uf, porcentaje = excluded.porcentaje, bono_2_clp = excluded.bono_2_clp,
  bono_3_clp = excluded.bono_3_clp, condicion = excluded.condicion, orden = excluded.orden;

-- C. A que proyectos se aplica cada plan (se puede repetir)
-- Regla general (no hay que anotarla): un proyecto sin asignacion usa «entrega futura» o «entrega inmediata» segun el estado de sus etapas.
-- Estas son las excepciones: todos los proyectos de la inmobiliaria Seminuevos, y Viñedos de Rengo (que vende con DS19 y con DS1).
insert into public."PlanComisionAsignacion" (plan_clave, inmobiliaria_id)
select 'seminuevos', i.id from public."Inmobiliaria" i where i.nombre = 'Seminuevos'
on conflict do nothing;
insert into public."PlanComisionAsignacion" (plan_clave, proyecto_id)
select v.plan, p.id::text from public."Proyecto" p cross join (values ('vinedos_ds19'), ('vinedos_ds1')) as v(plan) where p.nombre = 'Viñedos de Rengo'
on conflict do nothing;

-- D. Comprobar (debe decir: 6 planes, y asignaciones: 1 de Seminuevos + 2 de Viñedos de Rengo)
select 'D1 planes' as comprobacion, count(*)::text as resultado from public."PlanComision"
union all
select 'D2 asignaciones', count(*)::text from public."PlanComisionAsignacion"
union all
select 'D3 seguridad por filas', coalesce((select relrowsecurity::text from pg_class where oid = to_regclass('public."PlanComision"')), 'NO EXISTE') || ' / ' || coalesce((select relrowsecurity::text from pg_class where oid = to_regclass('public."PlanComisionAsignacion"')), 'NO EXISTE');

-- E. Revertir (solo si hiciera falta)
--   drop table if exists public."PlanComisionAsignacion";
--   drop table if exists public."PlanComision";
