-- Directorio de subsidios (Fase 3): las fichas de compra de vivienda que faltan (DS1 Tramos 1, 2 y 3 y DS19), EN BORRADOR, con los datos de la hoja de la Fase 0 que el dueño revisó.
-- Las ejecuta el dueño en el SQL Editor de Supabase, por bloques, DESPUÉS de las tablas (2026-11-05_directorio_subsidios.sql). Solo AGREGA filas; se puede repetir sin duplicar.
-- Quedan en «borrador»: no se ven en el sitio hasta que las publiques desde Admin → Subsidios (vista previa + Publicar).

-- ═══ A3. Cargar las cuatro fichas (fuentes, fichas, textos y datos) ══════════════════════════════════════════════════════════════════════
insert into public."Fuente" (titulo, url, organismo, tipo, leida_en) values
  ('Programa de Integración Social y Territorial DS19 (folleto, mayo 2026)', 'https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf', 'MINVU', 'folleto', date '2026-11-05'),
  ('Primer Llamado Nacional 2026 para postular al Subsidio para Sectores Medios (DS1)', 'https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'MINVU', 'pagina_llamado', date '2026-11-05'),
  ('DS1 - Tramo 1: subsidio para comprar una vivienda de hasta 1.100 UF', 'https://www.chileatiende.gob.cl/fichas/19094-ds-1-tramo-1-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-100-uf', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('DS1 - Tramo 2: subsidio para comprar una vivienda de hasta 1.600 UF', 'https://www.chileatiende.gob.cl/fichas/5172-ds-1-tramo-2-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-600-uf', 'ChileAtiende', 'ficha', date '2026-11-05')
on conflict (url) do nothing;

-- DS1 Tramo 1
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes, revisado_en, revisado_por)
select 'ds1-tramo-1', 'DS1 Tramo 1', 'Subsidio para Sectores Medios, Tramo 1 (compra de vivienda de hasta 1.100 UF)', 'DS 1', 'comprar', 'completa', 'borrador', 'por_llamados', 20,
       'Subsidio del MINVU para familias que no tienen vivienda y tienen capacidad de ahorro, para comprar una vivienda de hasta 1.100 UF.',
       array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.chileatiende.gob.cl/fichas/19094-ds-1-tramo-1-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-100-uf') order by id),
       date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)'
where not exists (select 1 from public."Subsidio" where slug = 'ds1-tramo-1');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que no tienen vivienda y tienen capacidad de ahorro.\n\n- Hasta el 60% del Registro Social de Hogares.\n- Los adultos mayores pueden estar hasta el 90% del Registro Social de Hogares.', 10, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.chileatiende.gob.cl/fichas/19094-ds-1-tramo-1-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-100-uf') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio que se aplica al precio de la vivienda y reduce el crédito hipotecario. En el Tramo 1 el monto promedio es de 600 UF, para todas las regiones. Es un promedio: no es el monto que corresponde a tu familia.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/19094-ds-1-tramo-1-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-100-uf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Un ahorro mínimo de 30 UF. Cada llamado fija su fecha de corte del ahorro.\n- Que la vivienda que quieres comprar valga hasta 1.100 UF.', 30, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.chileatiende.gob.cl/fichas/19094-ds-1-tramo-1-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-100-uf') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula en los llamados que abre el MINVU. Cada llamado tiene sus propias fechas y su fecha de corte del ahorro, y cambian durante el año.\n\nConfirma las fechas vigentes en la página oficial del MINVU (Comprar una vivienda, en minvu.gob.cl) antes de postular.', 40, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds1-tramo-1' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, revisado_en, revisado_por, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'verificado', date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'Hasta el 60% del RSH', 60, 'porcentaje', 'Primer Llamado Nacional 2026', 'https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '30 UF', 30, 'UF', 'Primer Llamado Nacional 2026', 'https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 20),
       ('precio_maximo', 'Precio máximo de la vivienda', 'Hasta 1.100 UF', 1100, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/19094-ds-1-tramo-1-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-100-uf', 30),
       ('subsidio_promedio', 'Monto promedio del subsidio', 'Promedio de 600 UF', 600, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/19094-ds-1-tramo-1-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-100-uf', 40),
       ('rsh_adulto_mayor', 'Adultos mayores', 'Hasta el 90% del RSH', 90, 'porcentaje', null::text, 'https://www.chileatiende.gob.cl/fichas/19094-ds-1-tramo-1-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-100-uf', 50)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds1-tramo-1'
on conflict (subsidio_id, clave) do nothing;

-- DS1 Tramo 2
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes, revisado_en, revisado_por)
select 'ds1-tramo-2', 'DS1 Tramo 2', 'Subsidio para Sectores Medios, Tramo 2 (compra de vivienda de hasta 1.600 UF)', 'DS 1', 'comprar', 'completa', 'borrador', 'por_llamados', 30,
       'Subsidio del MINVU para familias que no tienen vivienda y tienen capacidad de ahorro, para comprar una vivienda de hasta 1.600 UF.',
       array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.chileatiende.gob.cl/fichas/5172-ds-1-tramo-2-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-600-uf') order by id),
       date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)'
where not exists (select 1 from public."Subsidio" where slug = 'ds1-tramo-2');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que no tienen vivienda y tienen capacidad de ahorro.\n\n- Hasta el 80% del Registro Social de Hogares.\n- Los adultos mayores pueden estar hasta el 90% del Registro Social de Hogares.', 10, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.chileatiende.gob.cl/fichas/5172-ds-1-tramo-2-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-600-uf') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio que se aplica al precio de la vivienda y reduce el crédito hipotecario. En el Tramo 2 el monto promedio es de 450 UF. Es un promedio: no es el monto que corresponde a tu familia.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/5172-ds-1-tramo-2-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-600-uf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Un ahorro mínimo de 40 UF. Cada llamado fija su fecha de corte del ahorro.\n- Que la vivienda que quieres comprar valga hasta 1.600 UF.', 30, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.chileatiende.gob.cl/fichas/5172-ds-1-tramo-2-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-600-uf') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula en los llamados que abre el MINVU. Cada llamado tiene sus propias fechas y su fecha de corte del ahorro, y cambian durante el año.\n\nConfirma las fechas vigentes en la página oficial del MINVU (Comprar una vivienda, en minvu.gob.cl) antes de postular.', 40, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds1-tramo-2' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, revisado_en, revisado_por, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'verificado', date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'Hasta el 80% del RSH', 80, 'porcentaje', 'Primer Llamado Nacional 2026', 'https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '40 UF', 40, 'UF', 'Primer Llamado Nacional 2026', 'https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 20),
       ('precio_maximo', 'Precio máximo de la vivienda', 'Hasta 1.600 UF', 1600, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/5172-ds-1-tramo-2-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-600-uf', 30),
       ('subsidio_promedio', 'Monto promedio del subsidio', 'Promedio de 450 UF', 450, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/5172-ds-1-tramo-2-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-600-uf', 40),
       ('rsh_adulto_mayor', 'Adultos mayores', 'Hasta el 90% del RSH', 90, 'porcentaje', null::text, 'https://www.chileatiende.gob.cl/fichas/5172-ds-1-tramo-2-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-1-600-uf', 50)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds1-tramo-2'
on conflict (subsidio_id, clave) do nothing;

-- DS1 Tramo 3
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes, revisado_en, revisado_por)
select 'ds1-tramo-3', 'DS1 Tramo 3', 'Subsidio para Sectores Medios, Tramo 3', 'DS 1', 'comprar', 'completa', 'borrador', 'por_llamados', 40,
       'Subsidio del MINVU para familias que no tienen vivienda y tienen capacidad de ahorro, con un ahorro mínimo de 80 UF y topes de ingreso si el Registro Social de Hogares supera el 90%.',
       array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id),
       date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)'
where not exists (select 1 from public."Subsidio" where slug = 'ds1-tramo-3');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que no tienen vivienda y tienen capacidad de ahorro.\n\n- Tener Registro Social de Hogares vigente.\n- Si superas el 90% del Registro Social de Hogares, aplican topes de ingreso mensual.', 10, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio que se aplica al precio de la vivienda y reduce el crédito hipotecario. El monto del subsidio y el precio máximo de la vivienda del Tramo 3 no los publicamos aquí, porque no pudimos confirmarlos en una fuente oficial vigente. Consúltalos en la página oficial del MINVU.', 20, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Un ahorro mínimo de 80 UF. Cada llamado fija su fecha de corte del ahorro.\n- Registro Social de Hogares vigente.', 30, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula en los llamados que abre el MINVU. Cada llamado tiene sus propias fechas y su fecha de corte del ahorro, y cambian durante el año.\n\nConfirma las fechas vigentes en la página oficial del MINVU (Comprar una vivienda, en minvu.gob.cl) antes de postular.', 40, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds1-tramo-3' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, revisado_en, revisado_por, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'verificado', date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'RSH vigente (con topes de ingreso si superas el 90%)', null::numeric, 'texto', 'Primer Llamado Nacional 2026', 'https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '80 UF', 80, 'UF', 'Primer Llamado Nacional 2026', 'https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 20)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds1-tramo-3'
on conflict (subsidio_id, clave) do nothing;

-- DS19
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes, revisado_en, revisado_por)
select 'ds19-integracion-social', 'DS19', 'Programa de Integración Social y Territorial', 'DS 19', 'comprar', 'completa', 'borrador', 'todo_el_ano', 50,
       'Proyectos de viviendas nuevas construidos en alianza entre el Estado y empresas privadas, donde conviven familias de distintos niveles socioeconómicos. Puedes comprar tu primera vivienda con o sin subsidio previo.',
       array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf') order by id),
       date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)'
where not exists (select 1 from public."Subsidio" where slug = 'ds19-integracion-social');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Pueden participar dos grupos de personas:\n\n- Personas sin subsidio habitacional ni vivienda propia.\n- Personas con un subsidio habitacional: Fondo Solidario de Elección de Vivienda (DS49, modalidad individual) o Sectores Medios (DS1).', 10, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Tener 18 años o más y cédula de identidad vigente (las personas extranjeras, con residencia definitiva).\n- Tener Registro Social de Hogares.\n- Tener una cuenta de ahorro para la vivienda.\n- Si no tienes subsidio previo, el ahorro mínimo es de 40 UF (condiciones del DS1 Tramo 2) u 80 UF (Tramo 3).', 20, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula durante todo el año, en las entidades desarrolladoras que tengan proyectos disponibles.\n\nLos proyectos disponibles están publicados en www.minvu.cl.', 30, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf') order by id)),
       ('otro', 'Precio de la vivienda y monto del subsidio', E'El precio máximo de la vivienda depende de la comuna donde está el proyecto y del año en que fue seleccionado. El monto del subsidio depende de tu caso, la comuna y el año del proyecto.\n\nNo publicamos montos en pesos ni en UF, porque las fuentes oficiales que revisamos no coinciden entre sí. Confírmalos con la entidad desarrolladora del proyecto y en el MINVU.', 40, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds19-integracion-social' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, revisado_en, revisado_por, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'verificado', date '2026-11-05', 'Dueño del sitio (revisión de la hoja de la Fase 0)', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'Tener Registro Social de Hogares', null::numeric, 'texto', null::text, 'https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf', 10),
       ('ahorro_minimo', 'Ahorro mínimo', 'Sin subsidio previo: 40 UF (condiciones del DS1 Tramo 2) u 80 UF (Tramo 3)', null::numeric, 'texto', null::text, 'https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf', 20),
       ('precio_maximo', 'Precio máximo de la vivienda', 'Depende de la comuna y del año del proyecto', null::numeric, 'texto', null::text, 'https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf', 30),
       ('subsidio_base', 'Monto del subsidio', 'Depende de tu caso, la comuna y el año del proyecto', null::numeric, 'texto', null::text, 'https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf', 40),
       ('edad_minima', 'Edad mínima', '18 años', 18, 'anios', null::text, 'https://www.minvu.gob.cl/wp-content/uploads/2026/03/DS19_folleto.pdf', 50)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds19-integracion-social'
on conflict (subsidio_id, clave) do nothing;

-- ═══ B3. Comprobar (solo lectura; devuelve todo en una sola tabla; debe dar 4 · 16 · 17 · 6) ══════════════════════════════════════════════════════
--   select 'fichas nuevas en borrador (deben ser 4)' as comprobacion, count(*)::text as resultado from public."Subsidio" where slug in ('ds1-tramo-1', 'ds1-tramo-2', 'ds1-tramo-3', 'ds19-integracion-social') and estado = 'borrador'
--   union all select 'textos (deben ser 16)', count(*)::text from public."SubsidioBloque" b join public."Subsidio" s on s.id = b.subsidio_id where s.slug in ('ds1-tramo-1', 'ds1-tramo-2', 'ds1-tramo-3', 'ds19-integracion-social')
--   union all select 'datos verificados (deben ser 17)', count(*)::text from public."SubsidioDato" d join public."Subsidio" s on s.id = d.subsidio_id where s.slug in ('ds1-tramo-1', 'ds1-tramo-2', 'ds1-tramo-3', 'ds19-integracion-social') and d.estado = 'verificado'
--   union all select 'fuentes en total (deben ser 6)', count(*)::text from public."Fuente";

-- ═══ C3. Revertir (solo emergencia) ⚠ BORRA estas cuatro fichas con sus textos y datos (las fuentes se quedan) ═══════════════════════════════════════════
--   delete from public."Subsidio" where slug in ('ds1-tramo-1', 'ds1-tramo-2', 'ds1-tramo-3', 'ds19-integracion-social');
