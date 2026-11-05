-- Directorio de subsidios (Fase 4): fichas de vivienda que faltan (arriendo, construir, mejorar, rural, Valech y DS1 Tramo 4) y ajustes al DS1 de compra
-- Cada dato sale de las páginas oficiales leídas el 2026-11-05; lo que las fuentes oficiales dan distinto NO se publica (ver la hoja de verificación).
-- Se ejecuta TODO el archivo de una vez, en el SQL Editor de Supabase (pega el archivo completo y pulsa Run). Solo AGREGA filas y se puede repetir sin duplicar.
-- Todo queda en «borrador» y los datos en «por confirmar»: nada se ve en el sitio hasta que, en Admin → Subsidios, lo verifiques y lo publiques.

-- ═══ Cargar ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
insert into public."Fuente" (titulo, url, organismo, tipo, leida_en) values
  ('Material informativo Subsidio de Arriendo DS52 (julio 2026)', 'https://territoriociudadano.minvu.gob.cl/sites/default/files/2026-07/Material%20informativo_Subsidio%20Arriendo.pdf', 'MINVU', 'folleto', date '2026-11-05'),
  ('Programa de Habitabilidad Rural: construcción de conjunto habitacional, localidades de hasta 5 mil habitantes', 'https://www.chileatiende.gob.cl/fichas/111032/1/pdf', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Programa de Habitabilidad Rural: mejoramiento y ampliación de vivienda existente, localidades de hasta 5 mil habitantes', 'https://www.chileatiende.gob.cl/fichas/111261/1/pdf', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('DS1: Nuevo tramo 4.000', 'https://www.chileatiende.gob.cl/fichas/147994-ds-1-nuevo-tramo-4-000', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Subsidio de Arriendo de Vivienda', 'https://www.chileatiende.gob.cl/fichas/29888-subsidio-de-arriendo-de-vivienda', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Programa de mejoramiento de viviendas y barrios: proyectos para la vivienda (mejoramiento y ampliación)', 'https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('DS1 - Tramo 3: Subsidio habitacional para comprar una vivienda de hasta 2.200 UF', 'https://www.chileatiende.gob.cl/fichas/5436-ds-1-tramo-3-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-2-200-uf', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('DS1 - Tramo 2: Subsidio habitacional para construir una vivienda de hasta 1.600 UF', 'https://www.chileatiende.gob.cl/fichas/81721-ds-1-tramo-2-subsidio-habitacional-para-construir-una-vivienda-de-hasta-1-600-uf', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Subsidio para construir una vivienda (llamado individual DS Nº 49)', 'https://www.chileatiende.gob.cl/fichas/82745-subsidio-para-construir-una-vivienda-llamado-individual-ds-n-49', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Programa de Habitabilidad Rural: construcción en sitio del residente, localidades de hasta 5 mil habitantes', 'https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('DS1 - Tramo 3: Subsidio habitacional para construir una vivienda de hasta 2.200 UF', 'https://www.chileatiende.gob.cl/fichas/83931-ds1-tramo-3-subsidio-habitacional-para-construir-una-vivienda-de-hasta-2-200-uf', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Arriendo de una vivienda', 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 'MINVU', 'ficha', date '2026-11-05'),
  ('Informe Valech', 'https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 'MINVU', 'ficha', date '2026-11-05'),
  ('Programa de Habitabilidad Rural: construcción en sitio del residente, localidades de hasta 5.000 habitantes', 'https://www.minvu.gob.cl/beneficio/vivienda/programa-de-habitabilidad-rural-construccion-en-sitio-del-residente-localidades-de-hasta-5-000-habitantes/', 'MINVU', 'ficha', date '2026-11-05'),
  ('Programa de mejoramiento de viviendas y barrios: proyectos para la vivienda', 'https://www.minvu.gob.cl/beneficio/vivienda/programa-de-mejoramiento-de-viviendas-y-barrios-proyectos-para-la-vivienda/', 'MINVU', 'ficha', date '2026-11-05'),
  ('D.S.1 / Tramo 2: Subsidio habitacional para construir una vivienda de hasta 1.600 UF', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-1600-uf-ds1/', 'MINVU', 'ficha', date '2026-11-05'),
  ('D.S.1 / Tramo 3: Subsidio habitacional para construir una vivienda de hasta 2.200 UF', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-2200-uf-ds1/', 'MINVU', 'ficha', date '2026-11-05'),
  ('Subsidio para construir una vivienda DS49', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-construir-una-vivienda-de-hasta-950-uf-ds49/', 'MINVU', 'ficha', date '2026-11-05'),
  ('Mejorar vivienda y barrio (Hogar Mejor)', 'https://www.minvu.gob.cl/beneficios/mejorar-vivienda-y-barrio/', 'MINVU', 'otro', date '2026-11-05'),
  ('Programa rural', 'https://www.minvu.gob.cl/beneficios/programa-rural/', 'MINVU', 'otro', date '2026-11-05'),
  ('Llamado en condiciones especiales del DS49 para Construcción en Sitio Propio, Densificación Predial y Pequeño Condominio (2026)', 'https://www.minvu.gob.cl/postulacion/llamado-en-condiciones-especiales-del-ds49-para-construccion-en-sitio-propio-densificacion-predial-y-pequeno-condominio-incluyendo-el-procedimiento-de-autoconstruccion-asistida-2026/', 'MINVU', 'pagina_llamado', date '2026-11-05'),
  ('Llamado Nacional en condiciones especiales para el Tramo 4, Subsidio de Sectores Medios (D.S.1)', 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/', 'MINVU', 'pagina_llamado', date '2026-11-05'),
  ('Segundo Llamado Nacional 2026 para postular al Subsidio para Sectores Medios (D.S.1)', 'https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'MINVU', 'pagina_llamado', date '2026-11-05'),
  ('Tramo 4000', 'https://www.minvu.gob.cl/tramo-4000/', 'MINVU', 'otro', date '2026-11-05'),
  ('D.S. N° 49 de 2011, Fondo Solidario de Elección de Vivienda (texto 23.07.20)', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf', 'MINVU', 'decreto', date '2026-11-05'),
  ('Guía de aplicación para la construcción de vivienda, Programa para Sectores Medios D.S. N°1 (junio 2026)', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf', 'MINVU', 'folleto', date '2026-11-05'),
  ('D.S. N° 10 de 2015, Reglamento del Programa de Habitabilidad Rural (texto 21.06.17)', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS_10_2015_ACT_22_06_2017_Programa-de-Habitabilidad-Rural.pdf', 'MINVU', 'decreto', date '2026-11-05'),
  ('Reglamento del Programa de Subsidio de Arriendo de Vivienda, D.S. N° 52 de 2013 (texto actualizado 21.06.17)', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS_52_2013_ACT_21_06_2017_Subsidio-Arriendo.pdf', 'MINVU', 'decreto', date '2026-11-05'),
  ('D.S. N° 27 de 2016, Programa de Mejoramiento de Viviendas y Barrios (texto actualizado al 19.02.19)', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/Decreto-27_2016-Mejoramiento-Vivienda-y-Barrio_ACT_19feb19.pdf', 'MINVU', 'decreto', date '2026-11-05')
on conflict (url) do nothing;

-- Ya cargadas en la Fase 3 (siguen en borrador): DS1 Tramo 3 (compra) gana el precio máximo y el monto promedio, y las cifras de compra se rotulan con los dos llamados.
insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('precio_maximo', 'Precio máximo de la vivienda', 'Hasta 2.200 UF', 2200, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/5436-ds-1-tramo-3-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-2-200-uf', 30),
       ('subsidio_promedio', 'Monto promedio del subsidio', 'Promedio de 270 UF', 270, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/5436-ds-1-tramo-3-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-2-200-uf', 40)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds1-tramo-3'
on conflict (subsidio_id, clave) do nothing;

update public."SubsidioBloque" set cuerpo = E'Un subsidio que se aplica al precio de la vivienda y reduce el crédito hipotecario. En el Tramo 3 el monto promedio es de 270 UF y la vivienda puede costar hasta 2.200 UF (hasta 2.600 UF en algunas zonas). Es un promedio: no es el monto que corresponde a tu familia.', fuentes = array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.chileatiende.gob.cl/fichas/5436-ds-1-tramo-3-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-2-200-uf') order by id)
 where clave = 'que_entrega' and subsidio_id = (select id from public."Subsidio" where slug = 'ds1-tramo-3');
update public."Subsidio" set fuentes = array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/primer-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.chileatiende.gob.cl/fichas/5436-ds-1-tramo-3-subsidio-habitacional-para-comprar-una-vivienda-de-hasta-2-200-uf') order by id) where slug = 'ds1-tramo-3';
update public."SubsidioDato" set llamado = 'Primer y Segundo Llamado Nacional 2026'
 where llamado = 'Primer Llamado Nacional 2026'
   and subsidio_id in (select id from public."Subsidio" where slug in ('ds1-tramo-1', 'ds1-tramo-2', 'ds1-tramo-3'));

-- Arriendo DS52
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'ds52-arriendo', 'Arriendo DS52', 'Subsidio de Arriendo de Vivienda', 'DS 52', 'arrendar', 'completa', 'borrador', 'por_llamados', 60,
       'Aporte del Estado, por un tiempo limitado, para pagar una parte del arriendo mensual de una vivienda.',
       array(select id from public."Fuente" where url in ('https://territoriociudadano.minvu.gob.cl/sites/default/files/2026-07/Material%20informativo_Subsidio%20Arriendo.pdf', 'https://www.chileatiende.gob.cl/fichas/29888-subsidio-de-arriendo-de-vivienda', 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS_52_2013_ACT_21_06_2017_Subsidio-Arriendo.pdf') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'ds52-arriendo');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que pueden pagar una parte del arriendo mensual y no son dueñas de una vivienda.\n\n- Estar dentro del 70% del Registro Social de Hogares.\n- Tener 18 años o más y cédula de identidad vigente (las personas extranjeras, cédula para extranjeros vigente).\n- Tener un núcleo familiar en el RSH (cónyuge, conviviente o hijos). Las personas de más de 60 años no lo necesitan.\n- Tener un ingreso familiar mensual de entre 7 y 25 UF.\n- No ser dueña de una vivienda, ni tu cónyuge o tu núcleo familiar.', 10, array(select id from public."Fuente" where url in ('https://territoriociudadano.minvu.gob.cl/sites/default/files/2026-07/Material%20informativo_Subsidio%20Arriendo.pdf', 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS_52_2013_ACT_21_06_2017_Subsidio-Arriendo.pdf') order by id)),
       ('que_entrega', 'Qué entrega', E'Un aporte del Estado para pagar parte del arriendo mensual. En total son hasta 170 UF, que se entregan mes a mes: hasta 4,2 UF mensuales (el monto varía según la región).\n\nEl valor del arriendo de la vivienda puede ser de hasta 11 UF al mes (en algunas regiones, hasta 13 UF). La renta se compone del aporte del Estado y del copago que paga la persona beneficiada.\n\nEl subsidio se usa solo para pagar las rentas de arriendo y el mes de garantía: no cubre gastos comunes ni cuentas de servicios.', 20, array(select id from public."Fuente" where url in ('https://territoriociudadano.minvu.gob.cl/sites/default/files/2026-07/Material%20informativo_Subsidio%20Arriendo.pdf', 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS_52_2013_ACT_21_06_2017_Subsidio-Arriendo.pdf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Una cuenta de ahorro para la vivienda con al menos 4 UF al momento de postular.\n- Cotizaciones registradas, si trabajas: la postulación en línea usa la información de la Superintendencia de Pensiones para revisar tus ingresos. Si no tienes contrato de trabajo, puedes acreditar tus ingresos en forma presencial en la OIRS del SERVIU de tu región.', 30, array(select id from public."Fuente" where url in ('https://territoriociudadano.minvu.gob.cl/sites/default/files/2026-07/Material%20informativo_Subsidio%20Arriendo.pdf', 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula en los llamados que abre el MINVU: en línea, con ClaveÚnica, en minvu.cl, o en forma presencial en las oficinas del SERVIU. Cada llamado tiene sus propias fechas: confirma las vigentes en la página oficial del MINVU antes de postular.\n\nSe permite una postulación por RSH en cada llamado.', 40, array(select id from public."Fuente" where url in ('https://territoriociudadano.minvu.gob.cl/sites/default/files/2026-07/Material%20informativo_Subsidio%20Arriendo.pdf', 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/') order by id)),
       ('despues_de_ganarlo', 'Si eres seleccionada o seleccionado', E'Tienes 24 meses desde la publicación de la selección para activar el subsidio. Para hacerlo:\n\n1. Busca una vivienda para arrendar.\n2. Imprime tres copias del contrato y fírmalas, junto con el dueño de la vivienda, en una notaría.\n3. Presenta el contrato legalizado en el SERVIU de tu región.\n4. Paga el primer copago. El copago mensual se paga hasta el día 5 de cada mes.', 50, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/') order by id)),
       ('compatibilidades', 'Con qué otros subsidios se puede combinar', E'Tu familia puede postular al mismo tiempo al subsidio del DS1 (Sectores Medios). También puede postular al DS49 siendo beneficiaria del subsidio de arriendo regular.', 60, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/29888-subsidio-de-arriendo-de-vivienda') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds52-arriendo' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'Hasta el 70% del RSH', 70, 'porcentaje', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '4 UF', 4, 'UF', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 20),
       ('arriendo_maximo', 'Arriendo máximo de la vivienda', 'Hasta 11 UF al mes (13 UF en algunas regiones)', 11, 'UF', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 30),
       ('aporte_mensual', 'Aporte mensual del Estado', 'Hasta 4,2 UF al mes (varía según la región)', 4.2, 'UF', null::text, 'https://territoriociudadano.minvu.gob.cl/sites/default/files/2026-07/Material%20informativo_Subsidio%20Arriendo.pdf', 40),
       ('subsidio_total', 'Subsidio total', 'Hasta 170 UF en total', 170, 'UF', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 50),
       ('ingreso_familiar', 'Ingreso familiar mensual', 'Entre 7 y 25 UF', null::numeric, 'texto', null::text, 'https://territoriociudadano.minvu.gob.cl/sites/default/files/2026-07/Material%20informativo_Subsidio%20Arriendo.pdf', 60),
       ('edad_minima', 'Edad mínima', '18 años', 18, 'anios', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/arriendo-de-una-vivienda/', 70)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds52-arriendo'
on conflict (subsidio_id, clave) do nothing;


-- DS49 Construir
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'ds49-construccion-sitio-propio', 'DS49 Construir', 'Fondo Solidario de Elección de Vivienda: construcción en sitio propio', 'DS 49', 'construir', 'completa', 'borrador', 'por_llamados', 70,
       'Subsidio del Fondo Solidario de Elección de Vivienda para construir una vivienda en un sitio que es tuyo, sin crédito hipotecario. Puedes postular solo o en grupo, con una Entidad Patrocinante.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/82745-subsidio-para-construir-una-vivienda-llamado-individual-ds-n-49', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-construir-una-vivienda-de-hasta-950-uf-ds49/', 'https://www.minvu.gob.cl/postulacion/llamado-en-condiciones-especiales-del-ds49-para-construccion-en-sitio-propio-densificacion-predial-y-pequeno-condominio-incluyendo-el-procedimiento-de-autoconstruccion-asistida-2026/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'ds49-construccion-sitio-propio');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que no son dueñas de una vivienda y tienen un sitio propio donde construir.\n\n- Estar dentro del 40% del Registro Social de Hogares. Quien postula de forma individual puede estar hasta el 50%.\n- Tener 18 años o más. Las personas extranjeras deben presentar su cédula con permanencia definitiva.\n- No ser dueña de una vivienda ni estar postulando a otro programa habitacional. Quien tiene un sitio solo puede postular para construir en él.\n- No pueden postular personas solas (familias de una sola persona), salvo adultos mayores y viudos o viudas, entre otras excepciones que fija el reglamento.\n- Se puede postular de forma individual o en un grupo organizado (de 2 a 160 integrantes).', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/82745-subsidio-para-construir-una-vivienda-llamado-individual-ds-n-49', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-construir-una-vivienda-de-hasta-950-uf-ds49/', 'https://www.minvu.gob.cl/postulacion/llamado-en-condiciones-especiales-del-ds49-para-construccion-en-sitio-propio-densificacion-predial-y-pequeno-condominio-incluyendo-el-procedimiento-de-autoconstruccion-asistida-2026/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio para construir la vivienda en tu sitio. Se compone de un subsidio base y de aportes complementarios (por ejemplo, para quien tiene movilidad reducida). La obra puede hacerla una empresa constructora o, en la modalidad de autoconstrucción asistida, la propia familia con apoyo técnico.\n\nNo publicamos el monto del subsidio: las fuentes oficiales que revisamos (el reglamento, el llamado de 2026 y la nómina de seleccionados) dan cifras distintas, y el monto cambia por llamado y por comuna. Consúltalo con tu Entidad Patrocinante y en el MINVU.', 20, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-construir-una-vivienda-de-hasta-950-uf-ds49/', 'https://www.minvu.gob.cl/postulacion/llamado-en-condiciones-especiales-del-ds49-para-construccion-en-sitio-propio-densificacion-predial-y-pequeno-condominio-incluyendo-el-procedimiento-de-autoconstruccion-asistida-2026/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Una cuenta de ahorro para la vivienda a tu nombre con al menos 10 UF. El ahorro debe estar depositado a más tardar el último día hábil del mes anterior al ingreso de los antecedentes. No hagas giros en la cuenta.\n- Un sitio propio donde construir: debes acreditar que dispones del terreno con alguno de los documentos que acepta el reglamento.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/82745-subsidio-para-construir-una-vivienda-llamado-individual-ds-n-49', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-construir-una-vivienda-de-hasta-950-uf-ds49/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula con una Entidad Patrocinante (EP), que desarrolla el proyecto. El proyecto lo evalúa el SERVIU antes de que postules. El MINVU abre llamados: confirma en su página oficial el que está vigente y sus condiciones.', 40, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-construir-una-vivienda-de-hasta-950-uf-ds49/', 'https://www.minvu.gob.cl/postulacion/llamado-en-condiciones-especiales-del-ds49-para-construccion-en-sitio-propio-densificacion-predial-y-pequeno-condominio-incluyendo-el-procedimiento-de-autoconstruccion-asistida-2026/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds49-construccion-sitio-propio' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'Hasta el 40% del RSH (hasta el 50% si postulas de forma individual)', 40, 'porcentaje', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-construir-una-vivienda-de-hasta-950-uf-ds49/', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '10 UF (sube a 15 UF en algunos tramos del RSH)', 10, 'UF', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-para-construir-una-vivienda-de-hasta-950-uf-ds49/', 20),
       ('edad_minima', 'Edad mínima', '18 años', 18, 'anios', null::text, 'https://www.chileatiende.gob.cl/fichas/82745-subsidio-para-construir-una-vivienda-llamado-individual-ds-n-49', 30),
       ('credito_hipotecario', 'Crédito hipotecario', 'Se construye sin crédito hipotecario', null::numeric, 'texto', null::text, 'https://www.minvu.gob.cl/postulacion/llamado-en-condiciones-especiales-del-ds49-para-construccion-en-sitio-propio-densificacion-predial-y-pequeno-condominio-incluyendo-el-procedimiento-de-autoconstruccion-asistida-2026/', 40)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds49-construccion-sitio-propio'
on conflict (subsidio_id, clave) do nothing;


-- DS1 Construir T2
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'ds1-construccion-tramo-2', 'DS1 Construir T2', 'Subsidio para Sectores Medios, Tramo 2: construcción de vivienda de hasta 1.600 UF', 'DS 1', 'construir', 'completa', 'borrador', 'por_llamados', 80,
       'Subsidio del MINVU para construir una vivienda de hasta 1.600 UF en un sitio propio, o en un terreno que ya tiene otra vivienda (densificación predial).',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/81721-ds-1-tramo-2-subsidio-habitacional-para-construir-una-vivienda-de-hasta-1-600-uf', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-1600-uf-ds1/', 'https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'ds1-construccion-tramo-2');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que no son dueñas de una vivienda y tienen un terreno propio donde construir.\n\n- Estar dentro del 80% del Registro Social de Hogares (los adultos mayores, hasta el 90%).\n- Tener 18 años o más. Las personas extranjeras deben tener su cédula vigente, con residencia definitiva.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/81721-ds-1-tramo-2-subsidio-habitacional-para-construir-una-vivienda-de-hasta-1-600-uf', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-1600-uf-ds1/', 'https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio para construir una vivienda de hasta 140 m² en un sitio propio que tenga factibilidad de servicios básicos (alcantarillado, agua potable y luz), o en un terreno que ya tiene otra vivienda (densificación predial).\n\nEn el Tramo 2 el monto promedio es de 600 UF, para todas las regiones. Es un promedio: no es el monto que corresponde a tu familia.\n\nPuedes complementar el valor de la vivienda con recursos propios o con un crédito hipotecario.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/81721-ds-1-tramo-2-subsidio-habitacional-para-construir-una-vivienda-de-hasta-1-600-uf', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Un ahorro mínimo de 30 UF en una cuenta de ahorro para la vivienda. Cada llamado fija su monto y su fecha de corte.\n- En el Segundo Llamado Nacional 2026, la cuenta de ahorro no podía haberse abierto después del 30 de octubre de 2025.\n- Acreditar que el terreno es tuyo y que cuenta con factibilidad de servicios.\n- Contar con una Entidad Patrocinante y con una empresa constructora o un contratista, ambos inscritos en los registros del MINVU.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/81721-ds-1-tramo-2-subsidio-habitacional-para-construir-una-vivienda-de-hasta-1-600-uf', 'https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula en los llamados que abre el MINVU. Para construir, el proyecto lo desarrolla una Entidad Patrocinante inscrita en el MINVU. Cada llamado tiene sus propias fechas: confirma las vigentes en la página oficial del MINVU antes de postular.', 40, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id)),
       ('despues_de_ganarlo', 'Si eres seleccionada o seleccionado', E'El certificado de subsidio tiene una vigencia de 21 meses desde su emisión. Si no alcanzas a cobrarlo en ese período, hay un plazo adicional automático de 18 meses para aplicarlo y presentarlo a cobro.\n\nDurante 5 años no se puede vender ni arrendar la vivienda.', 50, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds1-construccion-tramo-2' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'Hasta el 80% del RSH', 80, 'porcentaje', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-1600-uf-ds1/', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '30 UF', 30, 'UF', 'Segundo Llamado Nacional 2026', 'https://www.chileatiende.gob.cl/fichas/81721-ds-1-tramo-2-subsidio-habitacional-para-construir-una-vivienda-de-hasta-1-600-uf', 20),
       ('precio_maximo', 'Precio máximo de la vivienda', 'Hasta 1.600 UF', 1600, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/81721-ds-1-tramo-2-subsidio-habitacional-para-construir-una-vivienda-de-hasta-1-600-uf', 30),
       ('subsidio_promedio', 'Monto promedio del subsidio', 'Promedio de 600 UF', 600, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/81721-ds-1-tramo-2-subsidio-habitacional-para-construir-una-vivienda-de-hasta-1-600-uf', 40),
       ('rsh_adulto_mayor', 'Adultos mayores', 'Hasta el 90% del RSH', 90, 'porcentaje', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-1600-uf-ds1/', 50)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds1-construccion-tramo-2'
on conflict (subsidio_id, clave) do nothing;


-- DS1 Construir T3
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'ds1-construccion-tramo-3', 'DS1 Construir T3', 'Subsidio para Sectores Medios, Tramo 3: construcción de vivienda de hasta 2.200 UF', 'DS 1', 'construir', 'completa', 'borrador', 'por_llamados', 85,
       'Subsidio del MINVU para construir una vivienda de hasta 2.200 UF en un sitio propio, o en un terreno que ya tiene otra vivienda (densificación predial), con topes de ingreso si el Registro Social de Hogares supera el 90%.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83931-ds1-tramo-3-subsidio-habitacional-para-construir-una-vivienda-de-hasta-2-200-uf', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-2200-uf-ds1/', 'https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'ds1-construccion-tramo-3');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que no son dueñas de una vivienda y tienen un terreno propio donde construir.\n\n- Tener Registro Social de Hogares vigente. Si superas el 90%, no puedes pasar los topes de ingreso del llamado.\n- Tener 18 años o más. Las personas extranjeras deben tener su cédula vigente, con residencia definitiva.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83931-ds1-tramo-3-subsidio-habitacional-para-construir-una-vivienda-de-hasta-2-200-uf', 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-2200-uf-ds1/', 'https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio para construir una vivienda de hasta 140 m² en un sitio propio que tenga factibilidad de servicios básicos (alcantarillado, agua potable y luz), o en un terreno que ya tiene otra vivienda (densificación predial).\n\nEn el Tramo 3 el monto promedio es de 400 UF, para todas las regiones. Es un promedio: no es el monto que corresponde a tu familia.\n\nPuedes complementar el valor de la vivienda con recursos propios o con un crédito hipotecario.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83931-ds1-tramo-3-subsidio-habitacional-para-construir-una-vivienda-de-hasta-2-200-uf', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Un ahorro mínimo de 50 UF en una cuenta de ahorro para la vivienda. Cada llamado fija su monto y su fecha de corte.\n- En el Segundo Llamado Nacional 2026, la cuenta de ahorro no podía haberse abierto después del 30 de octubre de 2025.\n- Acreditar que el terreno es tuyo y que cuenta con factibilidad de servicios.\n- Contar con una Entidad Patrocinante y con una empresa constructora o un contratista, ambos inscritos en los registros del MINVU.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83931-ds1-tramo-3-subsidio-habitacional-para-construir-una-vivienda-de-hasta-2-200-uf', 'https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula en los llamados que abre el MINVU. Para construir, el proyecto lo desarrolla una Entidad Patrocinante inscrita en el MINVU. Cada llamado tiene sus propias fechas: confirma las vigentes en la página oficial del MINVU antes de postular.', 40, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/postulacion/segundo-llamado-nacional-2026-para-postular-al-subsidio-para-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id)),
       ('despues_de_ganarlo', 'Si eres seleccionada o seleccionado', E'El certificado de subsidio tiene una vigencia de 21 meses desde su emisión. Si no alcanzas a cobrarlo en ese período, hay un plazo adicional automático de 18 meses para aplicarlo y presentarlo a cobro.\n\nDurante 5 años no se puede vender ni arrendar la vivienda.', 50, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS1_Guia_Construccion_VF_062026.pdf') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds1-construccion-tramo-3' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'RSH vigente (con topes de ingreso si superas el 90%)', null::numeric, 'texto', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/subsidio-habitacional-para-construir-una-vivienda-de-hasta-2200-uf-ds1/', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '50 UF', 50, 'UF', 'Segundo Llamado Nacional 2026', 'https://www.chileatiende.gob.cl/fichas/83931-ds1-tramo-3-subsidio-habitacional-para-construir-una-vivienda-de-hasta-2-200-uf', 20),
       ('precio_maximo', 'Precio máximo de la vivienda', 'Hasta 2.200 UF', 2200, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/83931-ds1-tramo-3-subsidio-habitacional-para-construir-una-vivienda-de-hasta-2-200-uf', 30),
       ('subsidio_promedio', 'Monto promedio del subsidio', 'Promedio de 400 UF', 400, 'UF', null::text, 'https://www.chileatiende.gob.cl/fichas/83931-ds1-tramo-3-subsidio-habitacional-para-construir-una-vivienda-de-hasta-2-200-uf', 40)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds1-construccion-tramo-3'
on conflict (subsidio_id, clave) do nothing;


-- DS27 Mejorar
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'ds27-mejoramiento-vivienda', 'DS27 Mejorar', 'Programa de Mejoramiento de Viviendas y Barrios: proyectos para la vivienda (Hogar Mejor)', 'DS 27', 'mejorar', 'completa', 'borrador', 'por_llamados', 90,
       'Subsidio para reparar o mejorar la vivienda propia y para ampliarla. La familia no postula sola: lo hace una Entidad Patrocinante.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 'https://www.minvu.gob.cl/beneficio/vivienda/programa-de-mejoramiento-de-viviendas-y-barrios-proyectos-para-la-vivienda/', 'https://www.minvu.gob.cl/beneficios/mejorar-vivienda-y-barrio/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/Decreto-27_2016-Mejoramiento-Vivienda-y-Barrio_ACT_19feb19.pdf') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'ds27-mejoramiento-vivienda');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que son dueñas o asignatarias de la vivienda que quieren mejorar o ampliar, en una localidad urbana de más de 5.000 habitantes. Para zonas rurales o localidades más pequeñas existe el Programa de Habitabilidad Rural.\n\n- Tener 18 años o más y cédula de identidad vigente. Las personas extranjeras deben presentar su cédula con residencia definitiva y el certificado de residencia definitiva.\n- Estar en el Registro Social de Hogares. El tope cambia de un llamado a otro (la ficha de ChileAtiende dice 60% para postulaciones individuales y el llamado de 2026 dijo 70%): confirma el del llamado vigente.\n- No ser dueña, ni tu cónyuge o conviviente, de otra vivienda, salvo que la SEREMI lo autorice.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 'https://www.minvu.gob.cl/beneficios/mejorar-vivienda-y-barrio/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/Decreto-27_2016-Mejoramiento-Vivienda-y-Barrio_ACT_19feb19.pdf') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio para reparar o mejorar la vivienda (obras estructurales, de instalaciones, de reparación de la envolvente o de mantención) y para reducir el hacinamiento con una ampliación (nuevo baño, dormitorio o cocina). También existe la adecuación de viviendas.\n\nNo publicamos los montos: la ficha, el reglamento y el llamado de 2026 dan cifras distintas, y el monto depende del tipo de obra y del llamado. Consúltalos con tu Entidad Patrocinante y en el MINVU.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/Decreto-27_2016-Mejoramiento-Vivienda-y-Barrio_ACT_19feb19.pdf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Un ahorro mínimo en una cuenta de ahorro para la vivienda: 3 UF para mejoramiento, 5 UF para ampliación y 7 UF para adecuación. Debe estar depositado el último día del mes anterior a la postulación. A las personas de 60 años o más se les rebajan 2 UF del ahorro exigido.\n- Una Entidad Patrocinante que te asesore y un constructor o contratista inscrito en el registro de constructores del MINVU.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/Decreto-27_2016-Mejoramiento-Vivienda-y-Barrio_ACT_19feb19.pdf') order by id)),
       ('como_se_postula', 'Cómo se postula', E'La familia no postula directamente: la Entidad Patrocinante ingresa la postulación al sistema informático del MINVU. Se puede postular de forma individual o en grupo. El MINVU abre llamados nacionales: confirma en su página oficial el que está vigente y sus fechas.', 40, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 'https://www.minvu.gob.cl/beneficio/vivienda/programa-de-mejoramiento-de-viviendas-y-barrios-proyectos-para-la-vivienda/') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds27-mejoramiento-vivienda' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('alcance_territorial', 'Dónde aplica', 'Solo en localidades urbanas de más de 5.000 habitantes', null::numeric, 'texto', null::text, 'https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '3 UF (mejoramiento), 5 UF (ampliación) o 7 UF (adecuación)', null::numeric, 'texto', null::text, 'https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 20),
       ('edad_minima', 'Edad mínima', '18 años', 18, 'anios', null::text, 'https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 30),
       ('propiedad', 'Sobre la vivienda', 'Ser dueña o asignataria de la vivienda', null::numeric, 'texto', null::text, 'https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 40),
       ('quien_postula', 'Quién presenta el proyecto', 'Una Entidad Patrocinante', null::numeric, 'texto', null::text, 'https://www.chileatiende.gob.cl/fichas/5180-programa-hogar-mejor-proyectos-para-la-vivienda-mejoramiento-y-ampliacion', 50)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds27-mejoramiento-vivienda'
on conflict (subsidio_id, clave) do nothing;


-- DS10 Rural
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'ds10-rural-sitio-residente', 'DS10 Rural', 'Programa de Habitabilidad Rural: construcción en sitio del residente', 'DS 10', 'rural', 'completa', 'borrador', 'por_llamados', 100,
       'Subsidio para construir una vivienda en tu sitio si vives en una zona rural o en una localidad urbana de hasta 5.000 habitantes. Se postula con una Entidad de Gestión Rural.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 'https://www.minvu.gob.cl/beneficio/vivienda/programa-de-habitabilidad-rural-construccion-en-sitio-del-residente-localidades-de-hasta-5-000-habitantes/', 'https://www.minvu.gob.cl/beneficios/programa-rural/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS_10_2015_ACT_22_06_2017_Programa-de-Habitabilidad-Rural.pdf') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'ds10-rural-sitio-residente');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que viven en zonas rurales o en localidades urbanas de hasta 5.000 habitantes y tienen un sitio donde construir.\n\n- Personas chilenas, y también extranjeras con residencia definitiva, de 18 años o más y con cédula de identidad vigente.\n- Tener Registro Social de Hogares y un terreno en una zona rural o en una localidad urbana de hasta 5.000 habitantes.\n- Acreditar que el terreno es apto para construir: hay 13 formas de acreditarlo (consulta cuál te corresponde en el SERVIU de tu región).', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 'https://www.minvu.gob.cl/beneficio/vivienda/programa-de-habitabilidad-rural-construccion-en-sitio-del-residente-localidades-de-hasta-5-000-habitantes/') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio para construir una vivienda con espacios mínimos (dos dormitorios, cocina, baño y estar-comedor). Incluye hasta dos obras adicionales (como un tercer dormitorio o un recinto de almacenamiento o productivo) y subsidios complementarios (por ejemplo, mejoramiento del terreno, alcantarillado, agua potable, conexión eléctrica, discapacidad y acondicionamiento térmico).\n\nLa ficha oficial no publica el monto en UF: dice que puede variar según el Registro Social de Hogares de la familia y el aislamiento geográfico de la localidad.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83930/1/pdf') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'El ahorro mínimo en una cuenta de ahorro para la vivienda depende de tu tramo del RSH:\n\n- Hasta el 40%: 10 UF para la vivienda (y 5 UF por un tercer dormitorio o un recinto complementario).\n- Del 50% al 60%: 30 UF (y 5 UF por cada obra adicional).\n- Desde el 70%: 50 UF (y 7 UF por cada obra adicional).', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 'https://www.minvu.gob.cl/beneficio/vivienda/programa-de-habitabilidad-rural-construccion-en-sitio-del-residente-localidades-de-hasta-5-000-habitantes/') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Solo a través de una Entidad de Gestión Rural (EGR), que te asesora durante todo el proceso. Se puede postular de forma individual o colectiva. Tu familia puede contratar a una empresa para que ejecute el proyecto o hacerlo por autoconstrucción asistida. El MINVU abre llamados: confirma en su página oficial el que está vigente.', 40, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/83930/1/pdf') order by id)),
       ('otro', 'Otras modalidades del programa rural', E'El Programa de Habitabilidad Rural tiene otras líneas, cada una con su ficha oficial: la construcción de conjuntos habitacionales (proyectos de hasta 60 viviendas) y el mejoramiento y ampliación de una vivienda existente. Revisa sus fichas en las fuentes de esta página.', 50, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/111032/1/pdf', 'https://www.chileatiende.gob.cl/fichas/111261/1/pdf', 'https://www.minvu.gob.cl/beneficios/programa-rural/') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds10-rural-sitio-residente' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('alcance_territorial', 'Dónde aplica', 'Zonas rurales o localidades urbanas de hasta 5.000 habitantes', null::numeric, 'texto', null::text, 'https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 10),
       ('rsh_maximo', 'Registro Social de Hogares', 'Tener Registro Social de Hogares (el ahorro exigido cambia según tu tramo)', null::numeric, 'texto', null::text, 'https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 20),
       ('ahorro_minimo', 'Ahorro mínimo', '10 UF hasta el 40% del RSH; 30 UF del 50% al 60%; 50 UF desde el 70%', null::numeric, 'texto', null::text, 'https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 30),
       ('edad_minima', 'Edad mínima', '18 años', 18, 'anios', null::text, 'https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 40),
       ('quien_postula', 'Con quién se postula', 'Con una Entidad de Gestión Rural', null::numeric, 'texto', null::text, 'https://www.chileatiende.gob.cl/fichas/83930/1/pdf', 50)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds10-rural-sitio-residente'
on conflict (subsidio_id, clave) do nothing;


-- Informe Valech
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'informe-valech-vivienda', 'Informe Valech', 'Medidas habitacionales para personas reconocidas en los informes sobre Prisión Política y Tortura (Informe Valech)', null, 'valech', 'completa', 'borrador', 'otra', 110,
       'No es un subsidio aparte: son puntajes adicionales y excepciones dentro de los programas habitacionales del MINVU para las personas reconocidas como víctimas de prisión política y tortura o, en su reemplazo, su cónyuge.',
       array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS_10_2015_ACT_22_06_2017_Programa-de-Habitabilidad-Rural.pdf', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/Decreto-27_2016-Mejoramiento-Vivienda-y-Barrio_ACT_19feb19.pdf') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'informe-valech-vivienda');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Las medidas están dirigidas a las personas incluidas en los informes de la Comisión Nacional sobre Prisión Política y Tortura y, en su reemplazo, a su cónyuge. Están pensadas sobre todo para quienes no son dueñas de una vivienda.', 10, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/') order by id)),
       ('que_entrega', 'Qué entrega', E'No hay un subsidio propio: la condición suma ventajas dentro de otros programas.\n\n- Puntaje adicional en la selección: 600 puntos en el Fondo Solidario de Elección de Vivienda (DS49) y en el Programa de Habitabilidad Rural (DS10), 100 puntos en Mejoramiento de Viviendas (DS27), 300 puntos en el DS1 y 100 puntos en el Subsidio de Arriendo (DS52).\n- En el DS49 y en el programa rural, las personas reconocidas pueden postular aunque no tengan núcleo familiar.\n- Quien perdió el dominio de su vivienda antes de ciertas fechas puede postular igual: los reglamentos del DS49 y del programa rural mencionan el 28 de noviembre de 2004.\n\nEl ahorro mínimo y el monto del subsidio son los de cada programa.', 20, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS_10_2015_ACT_22_06_2017_Programa-de-Habitabilidad-Rural.pdf', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/Decreto-27_2016-Mejoramiento-Vivienda-y-Barrio_ACT_19feb19.pdf') order by id)),
       ('como_se_postula', 'Cómo se usa', E'No hay un trámite aparte: la condición se invoca al postular a cada programa, y el sistema de postulaciones la verifica. El acceso a estos beneficios no caduca: no hay un plazo para acogerse. Para consultas, la página del MINVU indica la línea 800 388 000 y el correo avalech@minvu.cl.', 30, array(select id from public."Fuente" where url in ('https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/DS-49_FSEV-texto-DS-105_2014_23jul20.pdf', 'https://www.minvu.gob.cl/wp-content/uploads/2019/05/Decreto-27_2016-Mejoramiento-Vivienda-y-Barrio_ACT_19feb19.pdf') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'informe-valech-vivienda' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('puntaje_ds49', 'Puntaje adicional en el DS49', '600 puntos', 600, 'texto', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 10),
       ('puntaje_ds10', 'Puntaje adicional en el Programa Rural (DS10)', '600 puntos', 600, 'texto', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 20),
       ('puntaje_ds27', 'Puntaje adicional en Mejoramiento (DS27)', '100 puntos', 100, 'texto', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 30),
       ('puntaje_ds1', 'Puntaje adicional en el DS1 (Sectores Medios)', '300 puntos en la selección interna', 300, 'texto', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 40),
       ('puntaje_ds52', 'Puntaje adicional en el Subsidio de Arriendo (DS52)', '100 puntos en la selección interna', 100, 'texto', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 50),
       ('plazo', 'Plazo para acogerse', 'No hay un plazo definido', null::numeric, 'texto', null::text, 'https://www.minvu.gob.cl/beneficio/vivienda/informe-valech/', 60)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'informe-valech-vivienda'
on conflict (subsidio_id, clave) do nothing;


-- DS1 Tramo 4
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'ds1-tramo-4', 'DS1 Tramo 4', 'Subsidio para Sectores Medios, Tramo 4 (compra de vivienda de más de 2.200 y hasta 4.000 UF)', 'DS 1', 'comprar', 'completa', 'borrador', 'por_llamados', 45,
       'Subsidio del MINVU para comprar una vivienda nueva o usada de más de 2.200 y hasta 4.000 UF, pensado para familias de sectores medios que tienen dificultades para comprar por los altos valores de las viviendas.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/147994-ds-1-nuevo-tramo-4-000', 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/tramo-4000/') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'ds1-tramo-4');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Familias que no son dueñas de una vivienda, tienen capacidad de ahorro y pueden complementar el valor de la vivienda con un crédito hipotecario.\n\n- Tener 18 años o más. Las personas extranjeras deben tener su cédula vigente, con residencia definitiva.\n- Tener Registro Social de Hogares. Si superas el 90%, no puedes pasar los topes de ingreso del llamado.\n- Es un llamado solo para postulación individual (no hay vía colectiva ni automática).\n\nLa ficha de ChileAtiende dice que hay que tener capacidad para acceder a un crédito hipotecario; el llamado indica que, de forma excepcional, no se pide presentar el certificado de preaprobación de crédito.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/147994-ds-1-nuevo-tramo-4-000', 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/tramo-4000/') order by id)),
       ('que_entrega', 'Qué entrega', E'Un subsidio para comprar una vivienda nueva o usada que cueste más de 2.200 y hasta 4.000 UF (hasta 4.500 UF en algunas zonas extremas). El subsidio es de 400 UF, y mayor en algunas zonas del norte y del sur del país.\n\nEs un tramo nuevo, creado para ampliar las viviendas que se pueden comprar con subsidio.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/147994-ds-1-nuevo-tramo-4-000', 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/', 'https://www.minvu.gob.cl/tramo-4000/') order by id)),
       ('antes_de_postular', 'Lo que debes tener antes de postular', E'- Un ahorro mínimo de 200 UF. En este llamado, el ahorro debía estar como saldo disponible al 30 de octubre de 2026.\n- En este llamado no se exige que la cuenta de ahorro tenga 12 meses de antigüedad.\n- Una vivienda con al menos 3 recintos (zona de estar-comedor-cocina, un dormitorio con cabida para una cama de dos plazas y un baño) y con ventilación y luz natural en los espacios habitables.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/147994-ds-1-nuevo-tramo-4-000', 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/') order by id)),
       ('como_se_postula', 'Cómo se postula', E'Se postula de forma individual, en línea con ClaveÚnica en el sitio del MINVU o en forma presencial en las oficinas de información (OIRS) del SERVIU, con hora reservada. Cada llamado tiene sus propias fechas: confirma las vigentes en la página oficial del MINVU antes de postular.', 40, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/147994-ds-1-nuevo-tramo-4-000', 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/') order by id)),
       ('compatibilidades', 'Si ya tienes un subsidio', E'Si tienes un subsidio vigente de los tramos 1, 2 o 3, puedes pedir su aplicación al Tramo 4 para comprar una vivienda cuyo valor supere el máximo permitido para el Tramo 3, sin pasar el límite de este nuevo tramo. Consulta los requisitos en la ficha de ChileAtiende.', 50, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/147994-ds-1-nuevo-tramo-4-000') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'ds1-tramo-4' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);

insert into public."SubsidioDato" (subsidio_id, clave, etiqueta, valor_texto, valor_numero, unidad, llamado, fuente_id, estado, orden)
select s.id, d.clave, d.etiqueta, d.valor_texto, d.valor_numero, d.unidad, d.llamado, (select id from public."Fuente" where url = d.url), 'por_confirmar', d.orden
from public."Subsidio" s,
     (values
       ('rsh_maximo', 'Registro Social de Hogares', 'RSH vigente (con topes de ingreso si superas el 90%)', null::numeric, 'texto', null::text, 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/', 10),
       ('ahorro_minimo', 'Ahorro mínimo', '200 UF', 200, 'UF', 'Llamado Nacional en condiciones especiales, Tramo 4', 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/', 20),
       ('precio_maximo', 'Precio máximo de la vivienda', 'Más de 2.200 y hasta 4.000 UF (hasta 4.500 UF en algunas zonas)', 4000, 'UF', null::text, 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/', 30),
       ('subsidio_base', 'Subsidio base', 'Desde 400 UF (más en algunas zonas)', 400, 'UF', null::text, 'https://www.minvu.gob.cl/postulacion/llamado-nacional-en-condiciones-especiales-para-el-tramo-4-subsidio-de-sectores-medios-d-s-1/', 40),
       ('edad_minima', 'Edad mínima', '18 años', 18, 'anios', null::text, 'https://www.chileatiende.gob.cl/fichas/147994-ds-1-nuevo-tramo-4-000', 50)
     ) as d(clave, etiqueta, valor_texto, valor_numero, unidad, llamado, url, orden)
where s.slug = 'ds1-tramo-4'
on conflict (subsidio_id, clave) do nothing;


-- ═══ Comprobar (solo lectura; devuelve todo en una sola tabla) ═══════════════════════════════════════════════════════
--   select 'fichas nuevas en borrador (deben ser 8)' as comprobacion, count(*)::text as resultado from public."Subsidio" where slug in ('ds52-arriendo', 'ds49-construccion-sitio-propio', 'ds1-construccion-tramo-2', 'ds1-construccion-tramo-3', 'ds27-mejoramiento-vivienda', 'ds10-rural-sitio-residente', 'informe-valech-vivienda', 'ds1-tramo-4') and estado = 'borrador'
--   union all select 'textos (deben ser 37)', count(*)::text from public."SubsidioBloque" b join public."Subsidio" s on s.id = b.subsidio_id where s.slug in ('ds52-arriendo', 'ds49-construccion-sitio-propio', 'ds1-construccion-tramo-2', 'ds1-construccion-tramo-3', 'ds27-mejoramiento-vivienda', 'ds10-rural-sitio-residente', 'informe-valech-vivienda', 'ds1-tramo-4')
--   union all select 'datos por confirmar (deben ser 41)', count(*)::text from public."SubsidioDato" d join public."Subsidio" s on s.id = d.subsidio_id where s.slug in ('ds52-arriendo', 'ds49-construccion-sitio-propio', 'ds1-construccion-tramo-2', 'ds1-construccion-tramo-3', 'ds27-mejoramiento-vivienda', 'ds10-rural-sitio-residente', 'informe-valech-vivienda', 'ds1-tramo-4') and d.estado = 'por_confirmar'
--   union all select 'datos de DS1 Tramo 3 compra (deben ser 4)', count(*)::text from public."SubsidioDato" d join public."Subsidio" s on s.id = d.subsidio_id where s.slug = 'ds1-tramo-3'
--   union all select 'datos de DS1 Tramo 2 compra (deben ser 5)', count(*)::text from public."SubsidioDato" d join public."Subsidio" s on s.id = d.subsidio_id where s.slug = 'ds1-tramo-2'

-- ═══ Revertir (solo emergencia) ⚠ BORRA lo que cargó este archivo (las fuentes se quedan) ═════════════════════════════
--   delete from public."Subsidio" where slug in ('ds52-arriendo', 'ds49-construccion-sitio-propio', 'ds1-construccion-tramo-2', 'ds1-construccion-tramo-3', 'ds27-mejoramiento-vivienda', 'ds10-rural-sitio-residente', 'informe-valech-vivienda', 'ds1-tramo-4');
