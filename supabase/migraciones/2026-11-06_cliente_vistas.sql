-- Vistas «Tabla» y «Pipeline» del panel de clientes: datos nuevos en la tabla Cliente.
-- Solo AGREGA columnas (si no existen), una restricción de largo por texto y un disparador: no cambia ningún dato existente.
-- La ejecuta el dueño en el SQL Editor de Supabase: primero el bloque A (todo junto) y después la consulta B (todo junta).
-- El código solo usa estas columnas cuando existen; se puede ejecutar antes o después de subir el código de las vistas.

-- ═══ A. Crear (se puede repetir sin problema) ═══════════════════════════════════════════════════════════════════════════════════════
-- A1. Fecha de creación del cliente. SIN valor para los que ya existen (se verá «—»): ponerle «ahora» a todos los antiguos sería mentir.
alter table public."Cliente" add column if not exists creado_en timestamptz;
alter table public."Cliente" alter column creado_en set default now();                -- solo vale para los clientes que se creen desde ahora

-- A2. Fuente: de dónde llegó el cliente (Meta Ads, Chatbot, Formulario web, Referido, Excel, Manual…). La lista la maneja la aplicación.
alter table public."Cliente" add column if not exists fuente text;

-- A3. Medio: canal del contacto (WhatsApp, Chatbot, Correo, Llamada, Presencial…). Si la columna ya existía, esta línea no hace nada.
alter table public."Cliente" add column if not exists medio text;

-- A4. Cuándo cambió por última vez el estado de gestión (para «días en este estado»). La llena un disparador: vale venga de donde venga el cambio.
alter table public."Cliente" add column if not exists estado_gestion_en timestamptz;

-- A5. Texto de la próxima acción del recordatorio («Llamar y enviar a evaluar»).
alter table public."Cliente" add column if not exists recordatorio_nota text;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'cliente_fuente_largo') then
    alter table public."Cliente" add constraint cliente_fuente_largo check (fuente is null or char_length(fuente) between 1 and 40);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'cliente_recordatorio_nota_largo') then
    alter table public."Cliente" add constraint cliente_recordatorio_nota_largo check (recordatorio_nota is null or char_length(recordatorio_nota) between 1 and 300);
  end if;
end $$;

create or replace function public.cliente_marca_estado_gestion() returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.estado_gestion is not null and new.estado_gestion_en is null then
      new.estado_gestion_en := now();
    end if;
  elsif new.estado_gestion is distinct from old.estado_gestion then
    new.estado_gestion_en := now();
  end if;
  return new;
end $$;

drop trigger if exists cliente_estado_gestion_en on public."Cliente";
create trigger cliente_estado_gestion_en
  before insert or update of estado_gestion on public."Cliente"
  for each row execute function public.cliente_marca_estado_gestion();

comment on column public."Cliente".creado_en is 'Cuándo se creó el cliente (nulo en los anteriores a 2026-11-06).';
comment on column public."Cliente".fuente is 'De dónde llegó: meta_ads, chatbot, formulario_web, referido, excel, manual, otro…';
comment on column public."Cliente".medio is 'Canal del contacto: whatsapp, chatbot, correo, llamada, presencial… (la aplicación lo llena desde 2026-11-06).';
comment on column public."Cliente".estado_gestion_en is 'Cuándo cambió por última vez estado_gestion (lo pone el disparador cliente_estado_gestion_en).';
comment on column public."Cliente".recordatorio_nota is 'Qué hay que hacer en la fecha del recordatorio.';

-- ═══ B. Comprobar (solo lectura; una sola consulta, devuelve 5 filas) ═══════════════════════════════════════════════════════════
--  B1 columnas: deben salir las 5 (creado_en con default now(), las demás sin default).
--  B2 disparadores: debe incluir cliente_estado_gestion_en.
--  B3 clientes / con fecha: «con fecha» debe ser 0 al crear (los clientes antiguos no tienen fecha de creación).
--  B4 reglas sobre estado_gestion: debe decir «ninguna». Si muestra alguna, pásamela antes de seguir: la nueva etapa «Cotización enviada»
--      guarda el valor cotizacion_enviada y esa regla lo rechazaría.
--  B5 estados hoy: los valores de estado_gestion que hay (para confirmar que son los que conoce la pantalla).
select 'B1 columnas' as comprobacion,
       string_agg(column_name || ' (' || data_type || coalesce(', por defecto ' || column_default, '') || ')', ' | ' order by column_name) as resultado
  from information_schema.columns
 where table_schema = 'public' and table_name = 'Cliente'
   and column_name in ('creado_en', 'fuente', 'medio', 'estado_gestion_en', 'recordatorio_nota')
union all
select 'B2 disparadores', coalesce(string_agg(tgname, ', '), 'ninguno')
  from pg_trigger where tgrelid = 'public."Cliente"'::regclass and not tgisinternal
union all
select 'B3 clientes / con fecha', count(*)::text || ' / ' || count(creado_en)::text
  from public."Cliente"
union all
select 'B4 reglas sobre estado_gestion', coalesce(string_agg(conname || ': ' || pg_get_constraintdef(oid), ' | '), 'ninguna')
  from pg_constraint
 where conrelid = 'public."Cliente"'::regclass and contype = 'c' and pg_get_constraintdef(oid) ilike '%estado_gestion%'
union all
select 'B5 estados hoy', coalesce(string_agg(coalesce(estado_gestion, '(sin estado)') || ': ' || n::text, ' | ' order by n desc), 'ninguno')
  from (select estado_gestion, count(*) as n from public."Cliente" group by 1) t;

-- ═══ C. Revertir (SOLO EMERGENCIA) ═════════════════════════════════════════════════════════════════════════════════════════════════
-- ⚠ Borra lo que se haya guardado en estas columnas. Antes hay que volver al build anterior en Railway (el código nuevo las escribe).
-- «medio» NO se borra aquí porque puede haber existido antes de esta migración.
--   drop trigger if exists cliente_estado_gestion_en on public."Cliente";
--   drop function if exists public.cliente_marca_estado_gestion();
--   alter table public."Cliente" drop constraint if exists cliente_fuente_largo;
--   alter table public."Cliente" drop constraint if exists cliente_recordatorio_nota_largo;
--   alter table public."Cliente" drop column if exists creado_en, drop column if exists fuente,
--                                drop column if exists estado_gestion_en, drop column if exists recordatorio_nota;
