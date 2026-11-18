-- «Mi Camino» con cuenta: guardar el avance de la familia (qué requisitos marcó y qué subsidio/ruta eligió) en su cuenta.
-- Es seguro correrlo más de una vez. Mientras no se corra, «Mi Camino» sigue guardando solo en el dispositivo y todo lo demás funciona igual.
-- NO se guardan las respuestas del cuestionario (RSH, ahorro, vivienda): solo lo marcado y lo elegido.

-- 1) Cada requisito marcado lleva la hora en que cambió (para no pisar cambios entre dos dispositivos).
alter table public.progreso_usuario add column if not exists marcado_en timestamptz default now();

-- 2) El subsidio, la ruta y el objetivo elegidos (una fila por persona).
create table if not exists public.camino_eleccion (
  user_id        uuid primary key references auth.users(id) on delete cascade,
  subsidio       text not null default '',
  ruta           text check (ruta is null or ruta in ('subsidio', 'credito')),
  objetivo       text check (objetivo is null or objetivo in ('comprar', 'construir', 'arrendar', 'mejorar', 'rural')),
  actualizado_en timestamptz not null default now()
);

-- 3) Cerradas al público: solo el servidor (clave de servicio) puede leer o escribir. Sin esto, la clave pública que viaja en la página podría leerlas.
alter table public.progreso_usuario enable row level security;
alter table public.camino_eleccion  enable row level security;
