-- Consultas de SOLO LECTURA para que schema.sql quede igual a la base real. Se ejecutan una por una en el SQL Editor de Supabase y se pega cada resultado.

-- 1. Las columnas de cada tabla (una fila por tabla)
select table_name as tabla,
       string_agg(column_name || ' ' || data_type || case when is_nullable = 'NO' then ' not null' else '' end || coalesce(' default ' || column_default, ''), E'\n' order by ordinal_position) as columnas
from information_schema.columns
where table_schema = 'public'
group by table_name
order by table_name;

-- 2. Llaves primarias, foraneas, unicas y restricciones check
select conrelid::regclass::text as tabla, contype as tipo, pg_get_constraintdef(oid) as definicion
from pg_constraint
where connamespace = 'public'::regnamespace
order by 1, 2, 3;

-- 3. Indices
select tablename as tabla, indexdef as definicion
from pg_indexes
where schemaname = 'public'
order by 1, 2;

-- 4. Funciones y disparadores propios
select 'funcion' as tipo, p.proname as nombre, pg_get_functiondef(p.oid) as definicion
from pg_proc p
where p.pronamespace = 'public'::regnamespace
union all
select 'disparador', t.tgname, pg_get_triggerdef(t.oid)
from pg_trigger t
where not t.tgisinternal and t.tgrelid in (select oid from pg_class where relnamespace = 'public'::regnamespace)
order by 1, 2;
