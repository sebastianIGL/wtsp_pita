-- Constancia de los consentimientos de privacidad de los formularios públicos (evaluación, contacto y newsletter).
-- La usa app/services/consentimientos.py. Solo AGREGA una tabla. La ejecuta el dueño en el SQL Editor de Supabase, por bloques.
-- Mientras no exista, los formularios igual funcionan: guardar la constancia es de «mejor esfuerzo» y solo deja un aviso en el log.

-- A. Crear (se puede repetir sin problema)
create table if not exists public."ConsentimientoPrivacidad" (
  id               bigint generated always as identity primary key,
  origen           text not null check (origen in ('formulario','contacto','newsletter')),
  correo           text,                       -- con qué correo se identificó quien aceptó (en minúsculas)
  formulario_token uuid,                       -- si vino del formulario de evaluación: el token de ese formulario
  politica_version text not null,              -- versión de la política que se mostraba (fecha, p. ej. 2026-11-04)
  aceptado_en      timestamptz not null default now()
);
create index if not exists consentimiento_privacidad_correo on public."ConsentimientoPrivacidad" (lower(correo));
alter table public."ConsentimientoPrivacidad" enable row level security;   -- sin políticas: solo el backend (service role)

-- B. Comprobar (solo lectura)
--   select to_regclass('public."ConsentimientoPrivacidad"');                                            -- la tabla
--   select column_name, data_type, is_nullable from information_schema.columns
--    where table_schema='public' and table_name='ConsentimientoPrivacidad' order by ordinal_position;   -- 6 columnas
--   select relrowsecurity from pg_class where oid = 'public."ConsentimientoPrivacidad"'::regclass;      -- true
--   select count(*) from public."ConsentimientoPrivacidad";                                              -- 0 al crear

-- C. Revertir (solo emergencia) ⚠ borra la constancia de los consentimientos
--   drop table if exists public."ConsentimientoPrivacidad";
