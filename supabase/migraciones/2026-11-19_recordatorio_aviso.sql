-- Recordatorio con hora: el aviso por WhatsApp se manda a la hora que la persona pidió (por ejemplo, 10 minutos antes de «llamar a las 14:00»).
-- Es seguro correrlo más de una vez. Mientras no se corra, los recordatorios con fecha funcionan como siempre y el aviso por WhatsApp sigue saliendo al instante.

create table if not exists public."RecordatorioAviso" (
  id         bigserial primary key,
  cliente_id int  not null references public."Cliente"(id) on delete cascade,
  usuario_id uuid not null,
  enviar_en  timestamptz not null,
  motivo     text not null,
  estado     text not null default 'pendiente' check (estado in ('pendiente', 'enviado', 'error', 'vencido', 'cancelado')),
  enviado_en timestamptz,
  creado_en  timestamptz not null default now()
);

-- El ciclo de cada minuto solo mira los pendientes.
create index if not exists recordatorio_aviso_pendientes on public."RecordatorioAviso" (enviar_en) where estado = 'pendiente';

-- Cerrada al público: solo el servidor (clave de servicio) puede leerla o escribirla.
alter table public."RecordatorioAviso" enable row level security;
