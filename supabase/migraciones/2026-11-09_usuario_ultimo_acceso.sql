-- «Última vez en el sistema» de cada persona (la muestra la pestaña «Usuarios» del panel de administración).
-- Solo AGREGA una columna a Usuario. La ejecuta el dueño en el SQL Editor de Supabase, por bloques. Mientras no exista, el sistema funciona igual (la pestaña dice «Sin registro»).

-- A. Crear (se puede repetir sin problema)
alter table public."Usuario" add column if not exists ultimo_acceso timestamptz;

-- B. Comprobar (debe decir: B1 columna = ultimo_acceso)
select 'B1 columna' as comprobacion, coalesce((select column_name::text from information_schema.columns where table_schema = 'public' and table_name = 'Usuario' and column_name = 'ultimo_acceso'), 'NO EXISTE') as resultado;

-- C. Revertir (solo si hiciera falta; borra lo anotado)
--   alter table public."Usuario" drop column if exists ultimo_acceso;
