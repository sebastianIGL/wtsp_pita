-- Supabase schema for wtsp_pita
-- Run this in Supabase Dashboard -> SQL Editor.

-- Enable UUID generation
create extension if not exists pgcrypto;

-- Catálogo de proyectos (se usa para poblar variables del template)
create table if not exists public.proyectos (
  id uuid primary key default gen_random_uuid(),
  codigo text unique not null,
  nombre text not null,
  ubicacion text not null,
  nombre_plantilla text not null,
  idioma_plantilla text not null default 'es',
  creado_en timestamptz not null default now(),
  actualizado_en timestamptz not null default now()
);

-- Prospectos (1 por número de WhatsApp)
create table if not exists public.prospectos (
  id uuid primary key default gen_random_uuid(),
  telefono_e164 text unique not null,
  nombre text,
  codigo_proyecto text references public.proyectos(codigo) on update cascade,
  estado text not null default 'NUEVO',
  paso text not null default 'INICIO',
  datos jsonb not null default '{}'::jsonb,
  ultimo_texto_entrante text,
  ultimo_entrante_en timestamptz,
  ultimo_saliente_en timestamptz,
  creado_en timestamptz not null default now(),
  actualizado_en timestamptz not null default now()
);

-- Mensajes (trazabilidad)
create table if not exists public.mensajes (
  id uuid primary key default gen_random_uuid(),
  prospecto_id uuid not null references public.prospectos(id) on delete cascade,
  direccion text not null check (direccion in ('entrante','saliente')),
  wa_id_mensaje text,
  wa_timestamp timestamptz,
  texto text,
  crudo jsonb,
  creado_en timestamptz not null default now()
);

-- Simple trigger to keep updated_at fresh
create or replace function public.set_updated_at() returns trigger as $$
begin
  new.actualizado_en = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_proyectos_actualizado_en on public.proyectos;
create trigger trg_proyectos_actualizado_en
before update on public.proyectos
for each row execute function public.set_updated_at();

drop trigger if exists trg_prospectos_actualizado_en on public.prospectos;
create trigger trg_prospectos_actualizado_en
before update on public.prospectos
for each row execute function public.set_updated_at();

-- Helpful indexes
create index if not exists idx_mensajes_prospecto_id_creado_en on public.mensajes(prospecto_id, creado_en desc);
