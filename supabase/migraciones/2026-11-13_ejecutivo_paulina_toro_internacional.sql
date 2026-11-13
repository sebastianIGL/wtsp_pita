-- Paulina Toro es de Banco Internacional (se habia guardado como Itau porque la lista no tenia esa opcion).
-- La entidad de un ejecutivo bancario es un texto libre: no hay que crear nada, solo corregir ese registro. Se ejecuta por bloques en el SQL Editor de Supabase.

-- A. Ver el registro antes de cambiarlo (debe salir 1 fila, con entidad Itau)
select id, ejecutivo, entidad, email from public."EjecutivoBancario" where ejecutivo ilike '%paulina%' and ejecutivo ilike '%toro%';

-- B. Corregir (solo ese registro)
update public."EjecutivoBancario" set entidad = 'Banco Internacional' where ejecutivo ilike '%paulina%' and ejecutivo ilike '%toro%';

-- C. Comprobar (debe decir Banco Internacional)
select id, ejecutivo, entidad from public."EjecutivoBancario" where ejecutivo ilike '%paulina%' and ejecutivo ilike '%toro%';

-- D. Revertir (solo si hiciera falta)
--   update public."EjecutivoBancario" set entidad = 'Itaú' where ejecutivo ilike '%paulina%' and ejecutivo ilike '%toro%';
