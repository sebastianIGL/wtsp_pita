-- URL estable de cada proyecto: la dirección de su ficha (/viviendas/<slug>) se guarda una vez y ya no cambia aunque se renombre el proyecto.
-- La aplicación llena la columna sola (con la misma dirección que hoy tiene cada proyecto) la primera vez que lo lee; no hay que rellenarla a mano.
-- Es seguro correrlo más de una vez. Mientras no se corra, el sitio funciona como antes (la dirección se calcula desde el nombre).

alter table public."Proyecto" add column if not exists slug text;

-- Dos proyectos no pueden tener la misma dirección (los vacíos no cuentan).
create unique index if not exists proyecto_slug_unico on public."Proyecto" (slug) where slug is not null;
