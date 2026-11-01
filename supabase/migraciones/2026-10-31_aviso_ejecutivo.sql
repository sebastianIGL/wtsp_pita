-- Cola persistente de avisos al ejecutivo asignado (la usa app/services/avisos_cola.py con AVISOS_PERSISTENTES=true).
-- EJECUTADA en producción el 2026-10-31 (bloque A y comprobaciones B). Se guarda aquí como registro; no hay nada que volver a correr.

-- A. Crear (se puede repetir sin problema)
create table if not exists public."AvisoEjecutivo" (
  id             bigint generated always as identity primary key,
  cliente_id     int not null references public."Cliente"(id) on delete cascade,
  tipo           text not null check (tipo in ('necesita_ayuda','primer_documento','docs_completos')),
  nombre_cliente text,
  estado         text not null default 'pendiente' check (estado in ('pendiente','enviando','enviado','omitido','error')),
  motivo         text,
  creado_en      timestamptz not null default now(),
  enviar_desde   timestamptz not null default now(),
  reclamado_en   timestamptz,
  enviado_en     timestamptz,
  intentos       int not null default 0,
  wamid          text
);
-- Un solo aviso activo por cliente y tipo: el segundo, mientras el primero espera o se envía, se rechaza (23505).
create unique index if not exists aviso_unico_activo
  on public."AvisoEjecutivo" (cliente_id, tipo) where estado in ('pendiente','enviando');
create index if not exists aviso_cola
  on public."AvisoEjecutivo" (enviar_desde) where estado = 'pendiente';
alter table public."AvisoEjecutivo" enable row level security;   -- sin políticas: solo el backend (service role)

-- Reclamo atómico: devuelve a la cola los avisos «enviando» vencidos y entrega hasta max_avisos listos para enviar.
create or replace function public.reclamar_avisos(max_avisos int default 10, lease_minutos int default 10)
returns setof public."AvisoEjecutivo"
language plpgsql security definer set search_path = public as $$
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
revoke all on function public.reclamar_avisos(int, int) from public, anon, authenticated;
grant execute on function public.reclamar_avisos(int, int) to service_role;

-- B. Comprobar (solo lectura)
--   select to_regclass('public."AvisoEjecutivo"');                                           -- la tabla
--   select relrowsecurity from pg_class where oid = 'public."AvisoEjecutivo"'::regclass;     -- true
--   select has_function_privilege('anon','public.reclamar_avisos(int,int)','execute');       -- false
--   select has_function_privilege('service_role','public.reclamar_avisos(int,int)','execute'); -- true

-- C. Revertir (solo emergencia; primero apagar AVISOS_PERSISTENTES o redesplegar el build anterior)
--   drop function if exists public.reclamar_avisos(int, int);
--   drop table if exists public."AvisoEjecutivo";   -- ⚠ borra los avisos pendientes
