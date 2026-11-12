-- Deja guardadas (y confirmadas) las tarifas del gasto. Se pueden cambiar despues desde Admin > Gasto, o repitiendo este SQL con otros valores.
-- Anthropic (Haiku 4.5): lista oficial. Meta (Chile): valores de terceros; confirmalos con la tarifa oficial antes de ejecutar.

-- A. Guardar (se puede repetir sin problema)
insert into public."TarifaGasto" (clave, valor, actualizado_en) values
  ('anthropic_entrada', 1, now()),
  ('anthropic_salida', 5, now()),
  ('meta_marketing', 0.0889, now()),
  ('meta_utility', 0.02, now()),
  ('meta_authentication', 0.02, now()),
  ('meta_service', 0.02, now()),
  ('usd_clp', 980, now())
on conflict (clave) do update set valor = excluded.valor, actualizado_en = excluded.actualizado_en;

-- B. Comprobar (debe devolver 7 filas)
select clave, valor from public."TarifaGasto" order by clave;
