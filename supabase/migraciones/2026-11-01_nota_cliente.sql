-- Notas internas del equipo sobre un cliente (las usa app/routers/clientes.py: GET y POST /api/clientes/{id}/notas).
-- Solo AGREGA una tabla: no toca ninguna existente. La ejecuta el dueño en el SQL Editor de Supabase, por bloques.
-- ⚠ Hay que ejecutar el bloque A ANTES de usar el botón «Notas» (el código puede desplegarse antes: el botón mostrará un error hasta que exista la tabla).

-- A. Crear (se puede repetir sin problema)
create table if not exists public."NotaCliente" (
  id           bigint generated always as identity primary key,
  cliente_id   int  not null references public."Cliente"(id) on delete cascade,
  usuario_id   uuid references public."Usuario"(id) on delete set null,   -- quién la escribió (si el usuario se borra, la nota se conserva)
  autor_nombre text,                                                      -- su nombre al escribirla (se sigue viendo aunque cambie o se borre)
  texto        text not null check (char_length(btrim(texto)) between 1 and 2000),
  creado_en    timestamptz not null default now()
);
create index if not exists nota_cliente_por_cliente on public."NotaCliente" (cliente_id, creado_en desc);
alter table public."NotaCliente" enable row level security;   -- sin políticas: solo el backend (service role) la lee y escribe

-- B. Comprobar (solo lectura)
--   select to_regclass('public."NotaCliente"');                                                -- la tabla
--   select column_name, data_type, is_nullable from information_schema.columns
--    where table_schema='public' and table_name='NotaCliente' order by ordinal_position;       -- 6 columnas
--   select indexname from pg_indexes where tablename='NotaCliente';                            -- pkey y nota_cliente_por_cliente
--   select relrowsecurity from pg_class where oid = 'public."NotaCliente"'::regclass;         -- true
--   select count(*) from public."NotaCliente";                                                 -- 0 al crear
--   -- Prueba opcional que no deja rastro (se deshace sola):
--   begin;
--     insert into "NotaCliente"(cliente_id, texto) select id, 'PRUEBA' from "Cliente" limit 1;   -- debe insertar 1 fila
--     insert into "NotaCliente"(cliente_id, texto) select id, '   ' from "Cliente" limit 1;      -- debe FALLAR (texto vacío)
--   rollback;

-- C. Revertir (solo emergencia) ⚠ borra todas las notas
--   drop table if exists public."NotaCliente";
