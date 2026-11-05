-- Directorio de subsidios (Fase 6): otros subsidios y bonos (fichas ligeras, no son de vivienda)
-- Sin montos (se reajustan cada año). Cada ficha lleva a la ficha oficial para ver el monto vigente.
-- Se ejecuta TODO el archivo de una vez, en el SQL Editor de Supabase (pega el archivo completo y pulsa Run). Solo AGREGA filas y se puede repetir sin duplicar.
-- Todo queda en «borrador» y los datos en «por confirmar»: nada se ve en el sitio hasta que, en Admin → Subsidios, lo verifiques y lo publiques.

-- ═══ Cargar ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
insert into public."Fuente" (titulo, url, organismo, tipo, leida_en) values
  ('Las claves del Subsidio Unificado al Empleo: quiénes pueden postular y cómo acceder al beneficio (SENCE)', 'https://sence.gob.cl/sence/noticias/las-claves-del-subsidio-unificado-al-empleo-quienes-pueden-postular-y-como-acceder-al-beneficio', 'SENCE', 'otro', date '2026-11-05'),
  ('SUF automático', 'https://sufautomatico.gob.cl', 'Ministerio de Desarrollo Social y Familia', 'otro', date '2026-11-05'),
  ('Pensión Garantizada Universal (PGU)', 'https://www.chileatiende.gob.cl/fichas/102077-pension-garantizada-universal-pgu', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Subsidio Eléctrico', 'https://www.chileatiende.gob.cl/fichas/124375-subsidio-electrico', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Subsidio Unificado al Empleo (SUE)', 'https://www.chileatiende.gob.cl/fichas/147323-subsidio-unificado-al-empleo-sue', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Asignación Familiar', 'https://www.chileatiende.gob.cl/fichas/25878-asignacion-familiar-y-maternal', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Subsidio Familiar (SUF)', 'https://www.chileatiende.gob.cl/fichas/33112-subsidio-familiar-suf', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Aporte Familiar Permanente (ex Bono Marzo)', 'https://www.chileatiende.gob.cl/fichas/38913-aporte-familiar-permanente-ex-bono-marzo', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Subsidio de Discapacidad para menores de 18 años', 'https://www.chileatiende.gob.cl/fichas/43187-subsidio-de-discapacidad-para-menores-de-18-anos', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Subsidio al pago de consumo de agua potable y servicio de alcantarillado', 'https://www.chileatiende.gob.cl/fichas/51314-subsidio-al-pago-de-consumo-de-agua-potable-y-servicio-de-alcantarillado', 'ChileAtiende', 'ficha', date '2026-11-05'),
  ('Subsidio Unificado al Empleo (Ministerio del Trabajo)', 'https://www.mintrab.gob.cl/subsidio-unificado-al-empleo/', 'Ministerio del Trabajo', 'otro', date '2026-11-05'),
  ('Subsidio al Pago del Consumo de Agua Potable y Servicio de Alcantarillado de Aguas Servidas (SAP) - Ventanilla Única Social', 'https://www.ventanillaunicasocial.gob.cl/ficha/342/subsidio-pago-consumo-agua-potable-alcantarillado-aguas-servidas', 'Ventanilla Única Social', 'ficha', date '2026-11-05'),
  ('Subsidio Eléctrico - Ventanilla Única Social', 'https://www.ventanillaunicasocial.gob.cl/ficha/381/subsidio-electrico', 'Ventanilla Única Social', 'ficha', date '2026-11-05'),
  ('Aporte Familiar Permanente - Ventanilla Única Social', 'https://www.ventanillaunicasocial.gob.cl/ficha/80/aporte-familiar-permanente', 'Ventanilla Única Social', 'ficha', date '2026-11-05'),
  ('Asignación Familiar - Ventanilla Única Social', 'https://www.ventanillaunicasocial.gob.cl/ficha/83/asignacion-familiar', 'Ventanilla Única Social', 'ficha', date '2026-11-05'),
  ('Pensión Garantizada Universal - Ventanilla Única Social', 'https://www.ventanillaunicasocial.gob.cl/ficha/95/pension-garantizada-universal', 'Ventanilla Única Social', 'ficha', date '2026-11-05'),
  ('Subsidio de Discapacidad para menores de 18 años - Ventanilla Única Social', 'https://www.ventanillaunicasocial.gob.cl/ficha/96/subsidio-discapacidad-menores', 'Ventanilla Única Social', 'ficha', date '2026-11-05'),
  ('Subsidio Familiar - Ventanilla Única Social', 'https://www.ventanillaunicasocial.gob.cl/ficha/97/subsidio-familiar', 'Ventanilla Única Social', 'ficha', date '2026-11-05')
on conflict (url) do nothing;

-- Subsidio Familiar (SUF)
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'subsidio-familiar-suf', 'Subsidio Familiar (SUF)', 'Subsidio Familiar (SUF): incluye el SUF automático y el Subsidio Maternal', null, 'otro', 'ligera', 'borrador', 'otra', 200,
       'Aporte mensual para madres, padres, tutores o cuidadores, por sus hijos menores de 18 años y otras personas a su cargo, si el hogar pertenece al 60% más vulnerable del Registro Social de Hogares.',
       array(select id from public."Fuente" where url in ('https://sufautomatico.gob.cl', 'https://www.chileatiende.gob.cl/fichas/33112-subsidio-familiar-suf', 'https://www.ventanillaunicasocial.gob.cl/ficha/97/subsidio-familiar') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'subsidio-familiar-suf');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'- Pertenecer al 60% más vulnerable del Registro Social de Hogares.\n- Tener a cargo a personas que den derecho al beneficio (causantes): menores de 18 años, personas con discapacidad de cualquier edad o, en el Subsidio Maternal, estar embarazada desde el quinto mes.\n- Si perteneces al 40% más vulnerable y tienes a cargo a menores de 18 años, puedes recibirlo sin postular (SUF automático). Se consulta con el RUN del causante en sufautomatico.gob.cl.', 10, array(select id from public."Fuente" where url in ('https://sufautomatico.gob.cl', 'https://www.chileatiende.gob.cl/fichas/33112-subsidio-familiar-suf', 'https://www.ventanillaunicasocial.gob.cl/ficha/97/subsidio-familiar') order by id)),
       ('como_se_postula', 'Cómo se pide', E'El SUF tradicional se pide en la municipalidad correspondiente a tu domicilio. Los documentos varían entre municipalidades; por lo general son: certificado de nacimiento; para menores de 6 años, carné de control de niño sano; entre 6 y 18 años, certificado de alumno regular; si hay discapacidad intelectual, la declaración de la COMPIN; y, si no eres el padre o la madre, un documento que acredite que eres guardador o cuidador.\n\nEl SUF automático no se pide. El pago está a cargo del Instituto de Previsión Social (IPS).', 20, array(select id from public."Fuente" where url in ('https://sufautomatico.gob.cl', 'https://www.chileatiende.gob.cl/fichas/33112-subsidio-familiar-suf', 'https://www.ventanillaunicasocial.gob.cl/ficha/97/subsidio-familiar') order by id)),
       ('compatibilidades', 'Duración e incompatibilidades', E'El beneficio dura tres años mientras cumplas los requisitos. Es incompatible con las asignaciones familiares, con la Pensión Básica Solidaria de Invalidez y la Pensión Garantizada Universal (respecto de personas con discapacidad o invalidez) y con el Subsidio de Discapacidad para menores de 18 años.\n\nEl monto se reajusta cada año: consúltalo en la ficha oficial.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/33112-subsidio-familiar-suf') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'subsidio-familiar-suf' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);


-- Asignación Familiar
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'asignacion-familiar', 'Asignación Familiar', 'Asignación Familiar', null, 'otro', 'ligera', 'borrador', 'otra', 210,
       'Monto en dinero que reciben las personas trabajadoras y pensionadas por cada carga familiar reconocida (hijos, cónyuge y otras personas a su cargo).',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/25878-asignacion-familiar-y-maternal', 'https://www.ventanillaunicasocial.gob.cl/ficha/83/asignacion-familiar') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'asignacion-familiar');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Personas trabajadoras y pensionadas que acrediten cargas familiares: por ejemplo, hijos, cónyuge, nietos o ascendientes mayores de 65 años o con invalidez. Revisa en la ficha oficial quiénes pueden recibirla según su situación laboral y previsional.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/25878-asignacion-familiar-y-maternal', 'https://www.ventanillaunicasocial.gob.cl/ficha/83/asignacion-familiar') order by id)),
       ('como_se_postula', 'Cómo se pide', E'Se solicita el reconocimiento de tus cargas familiares: en línea con ClaveÚnica en ChileAtiende, por videoatención o en una sucursal ChileAtiende. Si estás afiliado a una caja de compensación (Los Andes, La Araucana, Los Héroes o 18 de Septiembre), a través de sus canales de atención.\n\nSiempre se necesita la cédula de identidad; el resto de los documentos depende de tu situación laboral y de la carga que quieres acreditar. Tu empleador te la paga todos los meses junto con el sueldo; a las personas pensionadas la paga el IPS o la AFP.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/25878-asignacion-familiar-y-maternal', 'https://www.ventanillaunicasocial.gob.cl/ficha/83/asignacion-familiar') order by id)),
       ('compatibilidades', 'Incompatibilidades', E'El Subsidio Familiar (SUF) es incompatible con las asignaciones familiares. El monto depende de tu ingreso y se reajusta cada año: consúltalo en la ficha oficial.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/25878-asignacion-familiar-y-maternal', 'https://www.chileatiende.gob.cl/fichas/33112-subsidio-familiar-suf') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'asignacion-familiar' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);


-- Aporte Familiar Permanente
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'aporte-familiar-permanente', 'Aporte Familiar Permanente', 'Aporte Familiar Permanente (ex Bono Marzo)', null, 'otro', 'ligera', 'borrador', 'otra', 220,
       'Bono anual en dinero (ex Bono Marzo) que se paga entre febrero y marzo a las familias más vulnerables. No se postula.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/38913-aporte-familiar-permanente-ex-bono-marzo', 'https://www.ventanillaunicasocial.gob.cl/ficha/80/aporte-familiar-permanente') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'aporte-familiar-permanente');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'El aporte corresponde, según la ficha de ChileAtiende, a cada carga familiar o persona que, a una fecha de referencia, te dio derecho a cobrar el Subsidio Familiar o Maternal (para el pago de 2026, el 31 de diciembre de 2025), y a los grupos familiares que a esa fecha pertenecían a Chile Solidario o al Subsistema Seguridades y Oportunidades.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/38913-aporte-familiar-permanente-ex-bono-marzo', 'https://www.ventanillaunicasocial.gob.cl/ficha/80/aporte-familiar-permanente') order by id)),
       ('como_se_postula', 'Cómo se consulta y se cobra', E'No se postula: es un bono no postulable. Con tu RUN y tu fecha de nacimiento puedes consultar si tienes derecho en el sitio de consulta del Aporte Familiar Permanente, o preguntar por videoatención, en la sucursal ChileAtiende o por teléfono al 101.\n\nSe paga por depósito bancario, CuentaRUT o presencialmente en la Caja de Compensación Los Héroes o en BancoEstado. Tienes 9 meses para cobrarlo desde que se genera el documento de pago. El monto se fija cada año: consúltalo en la ficha oficial.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/38913-aporte-familiar-permanente-ex-bono-marzo', 'https://www.ventanillaunicasocial.gob.cl/ficha/80/aporte-familiar-permanente') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'aporte-familiar-permanente' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);


-- Subsidio Eléctrico
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'subsidio-electrico', 'Subsidio Eléctrico', 'Subsidio Eléctrico', null, 'otro', 'ligera', 'borrador', 'otra', 230,
       'Descuento semestral en la cuenta de electricidad para hogares del 40% de mayor vulnerabilidad, que se otorga por convocatorias.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/124375-subsidio-electrico', 'https://www.ventanillaunicasocial.gob.cl/ficha/381/subsidio-electrico') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'subsidio-electrico');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'- Ser mayor de 18 años y ser cliente (arrendatario o propietario) de una empresa distribuidora o cooperativa eléctrica.\n- Estar al día en el pago de las cuentas de electricidad a la fecha que fije la convocatoria.\n- Que el hogar pertenezca al tramo del 40% de mayor vulnerabilidad del Registro Social de Hogares.\n- Los hogares con una persona inscrita en el Registro de Pacientes Electrodependientes tienen prioridad.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/124375-subsidio-electrico', 'https://www.ventanillaunicasocial.gob.cl/ficha/381/subsidio-electrico') order by id)),
       ('como_se_postula', 'Cómo se pide', E'El beneficio está a cargo del Ministerio de Energía y funciona por convocatorias: no hay una postulación permanente. Antes de postular, confirma en el sitio oficial del Subsidio Eléctrico, al que remite la ficha de ChileAtiende, si hay una convocatoria abierta. Se postula en línea con ClaveÚnica; para conocer el resultado también puedes ir a una sucursal ChileAtiende. El monto del descuento depende del tamaño del hogar y cambia: consúltalo en la ficha oficial.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/124375-subsidio-electrico', 'https://www.ventanillaunicasocial.gob.cl/ficha/381/subsidio-electrico') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'subsidio-electrico' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);


-- Subsidio de agua potable (SAP)
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'subsidio-agua-potable-sap', 'Subsidio de agua potable (SAP)', 'Subsidio al pago del consumo de agua potable y servicio de alcantarillado (SAP)', null, 'otro', 'ligera', 'borrador', 'otra', 240,
       'Aporte del Estado que cubre una parte de la cuenta mensual de agua potable y alcantarillado, en zonas urbanas y rurales, para hogares que destinan una parte importante de sus ingresos a ese pago.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/51314-subsidio-al-pago-de-consumo-de-agua-potable-y-servicio-de-alcantarillado', 'https://www.ventanillaunicasocial.gob.cl/ficha/342/subsidio-pago-consumo-agua-potable-alcantarillado-aguas-servidas') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'subsidio-agua-potable-sap');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'- Ser residente permanente de un domicilio que pertenezca a la municipalidad donde pides el beneficio.\n- Estar al día en los pagos con la empresa sanitaria o de agua potable rural, según corresponda.\n- Ser la jefa o el jefe de hogar de la familia principal que postula.\n- Tener Registro Social de Hogares y que tu hogar destine una parte importante de sus ingresos al pago del agua (la ficha indica el porcentaje exacto).\n- Que la vivienda cuente con conexión a la red de agua potable.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/51314-subsidio-al-pago-de-consumo-de-agua-potable-y-servicio-de-alcantarillado', 'https://www.ventanillaunicasocial.gob.cl/ficha/342/subsidio-pago-consumo-agua-potable-alcantarillado-aguas-servidas') order by id)),
       ('como_se_postula', 'Cómo se pide', E'Se postula durante todo el año, en el departamento social de la municipalidad correspondiente a tu domicilio. El trámite no tiene costo. Debes entregar la boleta de pago del consumo de agua potable de tu hogar.\n\nEl beneficio dura tres años y puedes renovarlo por otro período igual si sigues cumpliendo los requisitos y hay cupos en tu comuna. La renovación no es automática. El porcentaje que cubre el Estado depende del nivel socioeconómico: consúltalo en la ficha oficial.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/51314-subsidio-al-pago-de-consumo-de-agua-potable-y-servicio-de-alcantarillado', 'https://www.ventanillaunicasocial.gob.cl/ficha/342/subsidio-pago-consumo-agua-potable-alcantarillado-aguas-servidas') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'subsidio-agua-potable-sap' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);


-- Subsidio Unificado al Empleo (SUE)
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'subsidio-unificado-al-empleo-sue', 'Subsidio Unificado al Empleo (SUE)', 'Subsidio Unificado al Empleo (SUE)', null, 'otro', 'ligera', 'borrador', 'otra', 250,
       'Aporte monetario mensual que busca promover el empleo formal. Desde el 1 de octubre de 2026 reemplaza al Subsidio al Empleo Joven, al Bono al Trabajo de la Mujer y al Subsidio Previsional a los Trabajadores Jóvenes.',
       array(select id from public."Fuente" where url in ('https://sence.gob.cl/sence/noticias/las-claves-del-subsidio-unificado-al-empleo-quienes-pueden-postular-y-como-acceder-al-beneficio', 'https://www.chileatiende.gob.cl/fichas/147323-subsidio-unificado-al-empleo-sue', 'https://www.mintrab.gob.cl/subsidio-unificado-al-empleo/') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'subsidio-unificado-al-empleo-sue');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'Según la ficha de ChileAtiende:\n\n- Jóvenes de 18 a 24 años, 11 meses y 30 días.\n- Mujeres de 25 a 54 años, 11 meses y 30 días.\n- Personas de 55 años o más.\n- Personas con discapacidad de 18 años o más inscritas en el Registro Nacional de la Discapacidad.\n- Pertenecer al 40% del Registro Social de Hogares y tener una renta bruta mensual por debajo del tope que fija la ley.\n- También pueden postular las empresas privadas que contraten a personas de esos grupos.\n\nEl SENCE y el Ministerio del Trabajo detallan además requisitos de empleo previo (por ejemplo, meses de desempleo anteriores) que la ficha de ChileAtiende no menciona: revísalos en sus páginas antes de postular.', 10, array(select id from public."Fuente" where url in ('https://sence.gob.cl/sence/noticias/las-claves-del-subsidio-unificado-al-empleo-quienes-pueden-postular-y-como-acceder-al-beneficio', 'https://www.chileatiende.gob.cl/fichas/147323-subsidio-unificado-al-empleo-sue', 'https://www.mintrab.gob.cl/subsidio-unificado-al-empleo/') order by id)),
       ('como_se_postula', 'Cómo se pide', E'Se postula a través de la Ventanilla Única Social: las personas trabajadoras con su ClaveÚnica y las empresas con su Clave Tributaria. Según el Ministerio del Trabajo, se puede postular en cualquier fecha del año si se cumplen los requisitos. El beneficio está a cargo del SENCE.\n\nEl pago es mensual para empresas y trabajadores dependientes, y anual en agosto para trabajadores independientes. Dura hasta 12 meses. Quienes ya recibían el Subsidio al Empleo Joven o el Bono al Trabajo de la Mujer siguen cobrándolos por el tiempo que corresponda.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/147323-subsidio-unificado-al-empleo-sue', 'https://www.mintrab.gob.cl/subsidio-unificado-al-empleo/') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'subsidio-unificado-al-empleo-sue' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);


-- Subsidio de Discapacidad (menores de 18)
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'subsidio-discapacidad-menores-de-18', 'Subsidio de Discapacidad (menores de 18)', 'Subsidio de Discapacidad para menores de 18 años', null, 'otro', 'ligera', 'borrador', 'otra', 260,
       'Aporte mensual para menores de 18 años de escasos recursos con discapacidad mental, física o sensorial severa.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/43187-subsidio-de-discapacidad-para-menores-de-18-anos', 'https://www.ventanillaunicasocial.gob.cl/ficha/96/subsidio-discapacidad-menores') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'subsidio-discapacidad-menores-de-18');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'- Menores de 18 años con discapacidad mental, física o sensorial severa, de al menos un 50%.\n- Estar dentro del 60% más vulnerable de la población, según la clasificación socioeconómica.\n- Tener residencia continua en el país de, a lo menos, tres años inmediatamente anteriores.\n- Contar con una persona que se haga cargo del cuidado permanente del menor.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/43187-subsidio-de-discapacidad-para-menores-de-18-anos', 'https://www.ventanillaunicasocial.gob.cl/ficha/96/subsidio-discapacidad-menores') order by id)),
       ('como_se_postula', 'Cómo se pide', E'Se solicita en el sitio web con ClaveÚnica, por videoatención o en una sucursal ChileAtiende con tu cédula, o en tu municipio si tiene convenio con el Instituto de Previsión Social (IPS), llevando los documentos requeridos: el Certificado de Discapacidad (COMPIN) o del Registro Nacional de Discapacidad, y una resolución judicial, un informe social u otro antecedente que acredite la situación de cuidados.\n\nEl IPS paga el beneficio todos los meses. El monto se reajusta cada año: consúltalo en la ficha oficial.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/43187-subsidio-de-discapacidad-para-menores-de-18-anos', 'https://www.ventanillaunicasocial.gob.cl/ficha/96/subsidio-discapacidad-menores') order by id)),
       ('compatibilidades', 'Incompatibilidades', E'Si el menor ya recibe una pensión o es causante de una prestación familiar (como el Subsidio Familiar), al solicitar este subsidio hay que renunciar al otro beneficio.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/33112-subsidio-familiar-suf', 'https://www.chileatiende.gob.cl/fichas/43187-subsidio-de-discapacidad-para-menores-de-18-anos') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'subsidio-discapacidad-menores-de-18' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);


-- Pensión Garantizada Universal (PGU)
insert into public."Subsidio" (slug, nombre_corto, nombre_oficial, decreto, grupo, alcance, estado, postulacion, orden, resumen, fuentes)
select 'pension-garantizada-universal-pgu', 'Pensión Garantizada Universal (PGU)', 'Pensión Garantizada Universal (PGU)', null, 'otro', 'ligera', 'borrador', 'otra', 270,
       'Pensión mensual para personas de 65 años o más que cumplen los requisitos de focalización y residencia. La paga el IPS. Sirve a las familias con personas adultas mayores.',
       array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/102077-pension-garantizada-universal-pgu', 'https://www.ventanillaunicasocial.gob.cl/ficha/95/pension-garantizada-universal') order by id)
where not exists (select 1 from public."Subsidio" where slug = 'pension-garantizada-universal-pgu');

insert into public."SubsidioBloque" (subsidio_id, clave, titulo, cuerpo, orden, fuentes)
select s.id, b.clave, b.titulo, b.cuerpo, b.orden, b.fuentes
from public."Subsidio" s,
     (values
       ('para_quien', 'Para quién es', E'- Tener 65 años cumplidos.\n- No pertenecer al 10% más rico de la población, lo que se acredita mediante el Puntaje de Focalización Previsional.\n- Haber residido en Chile durante un período no inferior a 20 años, continuos o discontinuos, desde que cumpliste 20 años, y un período no inferior a 4 años en los últimos 5 años.\n\nLa ficha indica que puedes acceder aunque sigas trabajando o ya estés pensionada o pensionado.', 10, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/102077-pension-garantizada-universal-pgu', 'https://www.ventanillaunicasocial.gob.cl/ficha/95/pension-garantizada-universal') order by id)),
       ('como_se_postula', 'Cómo se pide', E'Con ClaveÚnica en línea, por videoatención, por el call center 101 o en una sucursal ChileAtiende. La paga todos los meses el Instituto de Previsión Social (IPS). Los montos se reajustan y cambian: consúltalos en la ficha oficial.\n\nSi te rechazan o te suspenden la pensión, puedes reclamar en un plazo de 5 días hábiles contados desde la notificación.', 20, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/102077-pension-garantizada-universal-pgu', 'https://www.ventanillaunicasocial.gob.cl/ficha/95/pension-garantizada-universal') order by id)),
       ('compatibilidades', 'Incompatibilidades', E'El Subsidio Familiar (SUF) es incompatible con la PGU respecto de las personas en situación de discapacidad o invalidez.', 30, array(select id from public."Fuente" where url in ('https://www.chileatiende.gob.cl/fichas/33112-subsidio-familiar-suf') order by id))
     ) as b(clave, titulo, cuerpo, orden, fuentes)
where s.slug = 'pension-garantizada-universal-pgu' and not exists (select 1 from public."SubsidioBloque" x where x.subsidio_id = s.id);


-- ═══ Comprobar (solo lectura; devuelve todo en una sola tabla) ═══════════════════════════════════════════════════════
--   select 'fichas ligeras en borrador (deben ser 8)' as comprobacion, count(*)::text as resultado from public."Subsidio" where slug in ('subsidio-familiar-suf', 'asignacion-familiar', 'aporte-familiar-permanente', 'subsidio-electrico', 'subsidio-agua-potable-sap', 'subsidio-unificado-al-empleo-sue', 'subsidio-discapacidad-menores-de-18', 'pension-garantizada-universal-pgu') and estado = 'borrador' and alcance = 'ligera'
--   union all select 'textos (deben ser 20)', count(*)::text from public."SubsidioBloque" b join public."Subsidio" s on s.id = b.subsidio_id where s.slug in ('subsidio-familiar-suf', 'asignacion-familiar', 'aporte-familiar-permanente', 'subsidio-electrico', 'subsidio-agua-potable-sap', 'subsidio-unificado-al-empleo-sue', 'subsidio-discapacidad-menores-de-18', 'pension-garantizada-universal-pgu')

-- ═══ Revertir (solo emergencia) ⚠ BORRA lo que cargó este archivo (las fuentes se quedan) ═════════════════════════════
--   delete from public."Subsidio" where slug in ('subsidio-familiar-suf', 'asignacion-familiar', 'aporte-familiar-permanente', 'subsidio-electrico', 'subsidio-agua-potable-sap', 'subsidio-unificado-al-empleo-sue', 'subsidio-discapacidad-menores-de-18', 'pension-garantizada-universal-pgu');
