/* Carga masiva de clientes desde un archivo CSV: la ventana que usan el panel (/panel) y el CRM (/index.html).
 *
 * Primero REVISA el archivo sin cargar nada (POST /api/clientes/importar/analizar): cuántos registros son, cuántos se cargarían, duplicados, errores y a qué proyectos irían.
 * Después, la administración elige a quién asignarlos: a una persona, repartidos entre varios ejecutivos (con una recomendación) o sin asignar. Si una persona recibiría más de lo
 * recomendado, la ventana avisa y pide confirmar (el servidor lo exige igual). Por último carga con avance en vivo (POST /api/clientes/importar).
 *
 * Uso:  CargaClientes.abrir({ token: () => _token, esAdmin: true|false, acento: '#0ea5e9', contexto: { empresa_id, inmobiliaria_id, proyecto_id },
 *                             alIniciar(), alTerminar(resultado), enviarWhatsApp(ids) /* opcional *\/ })
 * Todo texto que viene del servidor o de personas se escribe con esc() o como texto: nunca como HTML. La ventana no usa manejadores en línea (onclick…): usa delegación de eventos.
 */
(function () {
  'use strict'

  const LIMITE_POR_DEFECTO = 100
  const nf = new Intl.NumberFormat('es-CL')
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const num = n => nf.format(n)

  // ── Funciones puras (se prueban sin la página) ─────────────────────────────
  // Partes iguales; si no calza exacto, las primeras personas reciben uno más.
  function partesIguales(total, ids) {
    if (!(total > 0) || !ids.length) return []
    const base = Math.floor(total / ids.length), resto = total % ids.length
    return ids.map((id, i) => ({ id, cantidad: base + (i < resto ? 1 : 0) })).filter(p => p.cantidad > 0)
  }

  // Cómo va el reparto: cuántos clientes ya tienen dueño, cuántos faltan o sobran, y si alguien queda sobre lo recomendado.
  function estadoReparto(total, filas, limite) {
    const marcadas = filas.filter(f => f.marcada)
    const cantidades = marcadas.map(f => (Number.isInteger(f.cantidad) && f.cantidad > 0 ? f.cantidad : 0))
    const asignados = cantidades.reduce((a, b) => a + b, 0)
    const mayor = cantidades.length ? Math.max(...cantidades) : 0
    return { asignados, faltan: Math.max(0, total - asignados), sobran: Math.max(0, asignados - total), mayor, sobreLimite: mayor > limite,
             hayCantidadInvalida: marcadas.some(f => !(Number.isInteger(f.cantidad) && f.cantidad > 0)), valido: marcadas.length > 0 && asignados === total && !marcadas.some(f => !(Number.isInteger(f.cantidad) && f.cantidad > 0)) }
  }

  // Las personas que se recomienda marcar: primero quienes ya tienen el proyecto asignado, y entre ellas las que hoy tienen menos clientes (sin dato cuenta como cero); si empatan, el orden que ya traen.
  function seleccionRecomendada(ejecutivos, personas) {
    return ejecutivos.filter(e => e.rol === 'ejecutivo').map((e, i) => ({ e, i }))
      .sort((a, b) => ((a.e.tiene_proyecto === false) - (b.e.tiene_proyecto === false)) || ((a.e.clientes_actuales || 0) - (b.e.clientes_actuales || 0)) || (a.i - b.i)).slice(0, Math.max(0, personas)).map(x => x.e.id)
  }

  function cuerpoReparto(filas) {
    return filas.filter(f => f.marcada && Number.isInteger(f.cantidad) && f.cantidad > 0).map(f => ({ usuario_id: f.id || null, cantidad: f.cantidad }))
  }

  // ── Estado y pantalla ──────────────────────────────────────────────────────
  let opts = null
  let est = null
  let modal = null
  let cargando = false
  let contadorSolicitudes = 0   // global (no por ventana): una revisión lenta de una ventana ya cerrada no puede confundirse con la de una ventana nueva

  const CSS = `
  #cc-modal { --cc-fondo:#ffffff; --cc-texto:#1e293b; --cc-suave:#64748b; --cc-borde:#cbd5e1; --cc-tenue:#f1f5f9; --cc-ok:#15803d; --cc-aviso:#b45309; --cc-aviso-fondo:#fef3c7; --cc-error:#b91c1c; --cc-error-fondo:#fee2e2; --cc-acento:#0ea5e9; }
  html.dark #cc-modal { --cc-fondo:#111c2e; --cc-texto:#e2e8f0; --cc-suave:#94a3b8; --cc-borde:#334155; --cc-tenue:#1b2740; --cc-ok:#4ade80; --cc-aviso:#fbbf24; --cc-aviso-fondo:rgba(245,158,11,.14); --cc-error:#f87171; --cc-error-fondo:rgba(239,68,68,.14); }
  #cc-modal { position:fixed; inset:0; z-index:70; display:flex; align-items:center; justify-content:center; padding:16px; background:rgba(15,23,42,.55); font-family:inherit; }
  #cc-modal[hidden] { display:none; }
  #cc-modal .cc-caja { width:100%; max-width:760px; max-height:92vh; display:flex; flex-direction:column; border-radius:16px; background:var(--cc-fondo); color:var(--cc-texto); border:1px solid var(--cc-borde); box-shadow:0 24px 60px rgba(0,0,0,.35); }
  #cc-modal header { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding:18px 22px 12px; border-bottom:1px solid var(--cc-borde); }
  #cc-modal .cc-sup { font-size:12px; color:var(--cc-suave); margin:0; }
  #cc-modal h3 { font-size:17px; font-weight:700; margin:2px 0 0; }
  #cc-modal .cc-x { font-size:20px; line-height:1; color:var(--cc-suave); background:none; border:0; cursor:pointer; padding:2px 6px; }
  #cc-modal .cc-cuerpo { padding:16px 22px; overflow-y:auto; display:flex; flex-direction:column; gap:14px; }
  #cc-modal .cc-cuerpo > * { flex-shrink:0; }
  #cc-modal footer { display:flex; gap:10px; padding:14px 22px; border-top:1px solid var(--cc-borde); }
  #cc-modal label.cc-et { display:block; font-size:12px; color:var(--cc-suave); margin-bottom:4px; }
  #cc-modal select, #cc-modal input[type=number], #cc-modal input[type=text] { width:100%; border:1px solid var(--cc-borde); border-radius:8px; padding:7px 10px; font-size:14px; background:var(--cc-fondo); color:var(--cc-texto); }
  #cc-modal .cc-btn { flex:1; padding:10px 14px; border-radius:10px; font-size:14px; font-weight:600; border:1px solid var(--cc-borde); background:transparent; color:var(--cc-texto); cursor:pointer; }
  #cc-modal .cc-btn.cc-prim { background:var(--cc-acento); border-color:var(--cc-acento); color:#fff; }
  #cc-modal .cc-btn:disabled { opacity:.45; cursor:not-allowed; }
  #cc-modal .cc-mini { font-size:12px; padding:5px 10px; border-radius:8px; border:1px solid var(--cc-borde); background:transparent; color:var(--cc-texto); cursor:pointer; }
  #cc-modal .cc-mini:hover { border-color:var(--cc-acento); }
  #cc-modal .cc-drop { border:2px dashed var(--cc-borde); border-radius:12px; padding:26px 16px; text-align:center; cursor:pointer; color:var(--cc-suave); }
  #cc-modal .cc-drop.cc-sobre, #cc-modal .cc-drop:hover { border-color:var(--cc-acento); }
  #cc-modal .cc-drop b { color:var(--cc-texto); }
  #cc-modal .cc-tarjetas { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
  #cc-modal .cc-tarjeta { border:1px solid var(--cc-borde); border-radius:12px; padding:10px 12px; background:var(--cc-tenue); }
  #cc-modal .cc-tarjeta b { display:block; font-size:22px; line-height:1.2; font-variant-numeric:tabular-nums; }
  #cc-modal .cc-tarjeta span { font-size:12px; color:var(--cc-suave); }
  #cc-modal .cc-aviso, #cc-modal .cc-error, #cc-modal .cc-info { border-radius:10px; padding:10px 12px; font-size:13px; }
  #cc-modal .cc-aviso { background:var(--cc-aviso-fondo); color:var(--cc-aviso); }
  #cc-modal .cc-error { background:var(--cc-error-fondo); color:var(--cc-error); }
  #cc-modal .cc-info { background:var(--cc-tenue); color:var(--cc-texto); }
  #cc-modal .cc-tabla { width:100%; border-collapse:collapse; font-size:13px; }
  #cc-modal .cc-tabla th { text-align:left; font-weight:600; color:var(--cc-suave); padding:6px 8px; border-bottom:1px solid var(--cc-borde); }
  #cc-modal .cc-tabla td { padding:6px 8px; border-bottom:1px solid var(--cc-borde); vertical-align:middle; }
  #cc-modal .cc-tabla input[type=number] { width:84px; text-align:right; }
  #cc-modal .cc-lista { max-height:300px; overflow-y:auto; border:1px solid var(--cc-borde); border-radius:10px; }
  #cc-modal .cc-fila-op { display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer; }
  #cc-modal .cc-suave { color:var(--cc-suave); font-size:12px; }
  #cc-modal .cc-total { font-variant-numeric:tabular-nums; font-weight:600; }
  #cc-modal .cc-chip { display:inline-block; font-size:11px; padding:1px 8px; border-radius:999px; background:var(--cc-tenue); color:var(--cc-suave); }
  #cc-modal button:focus-visible, #cc-modal input:focus-visible, #cc-modal select:focus-visible { outline:2px solid var(--cc-acento); outline-offset:2px; }
  @media (max-width:640px) { #cc-modal .cc-tarjetas { grid-template-columns:repeat(2,1fr); } }
  `

  function montar() {
    if (modal) return
    const st = document.createElement('style')
    st.id = 'cc-estilos'
    st.textContent = CSS
    document.head.appendChild(st)
    modal = document.createElement('div')
    modal.id = 'cc-modal'
    modal.hidden = true
    modal.setAttribute('role', 'dialog')
    modal.setAttribute('aria-modal', 'true')
    modal.setAttribute('aria-labelledby', 'cc-titulo')
    modal.innerHTML = `<div class="cc-caja"><header><div><p class="cc-sup">Cargar clientes</p><h3 id="cc-titulo"></h3></div><button type="button" class="cc-x" data-cc="cerrar" aria-label="Cerrar">✕</button></header>
      <div class="cc-cuerpo" id="cc-cuerpo"></div><footer id="cc-pie"></footer></div>`
    document.body.appendChild(modal)
    modal.addEventListener('click', alClic)
    modal.addEventListener('change', alCambiar)
    modal.addEventListener('input', alEscribir)
    modal.addEventListener('dragover', e => { if (e.target.closest('.cc-drop')) { e.preventDefault(); e.target.closest('.cc-drop').classList.add('cc-sobre') } })
    modal.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('[data-cc="elegir"]')) { e.preventDefault(); e.target.click() } })
    modal.addEventListener('dragleave', e => e.target.closest?.('.cc-drop')?.classList.remove('cc-sobre'))
    modal.addEventListener('drop', e => {
      const z = e.target.closest('.cc-drop')
      if (!z) return
      e.preventDefault(); z.classList.remove('cc-sobre')
      const f = e.dataTransfer?.files?.[0]
      if (f) elegirArchivo(f)
    })
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && modal && !modal.hidden) cerrar() })
  }

  function abrir(o) {
    opts = Object.assign({ esAdmin: false, acento: '#0ea5e9', contexto: {} }, o || {})
    montar()
    modal.style.setProperty('--cc-acento', opts.acento)
    est = { paso: 'archivo', archivo: null, analisis: null, proyectos: [], proyectoId: opts.contexto.proyecto_id ? String(opts.contexto.proyecto_id) : '', error: '',
            modo: 'repartir', unaPersona: '', filas: [], confirmo: false, resultado: null, progreso: null, solicitud: 0 }
    cargando = false
    modal.hidden = false
    cargarProyectos()
    dibujar()
    modal.querySelector('.cc-drop')?.focus()
  }

  function cerrar() {
    if (cargando) return
    modal.hidden = true
    if (est && est.paso === 'resultado' && opts?.alTerminar) opts.alTerminar(est.resultado)
  }

  async function cargarProyectos() {
    const c = opts.contexto, qs = c.inmobiliaria_id ? `inmobiliaria_id=${encodeURIComponent(c.inmobiliaria_id)}` : (c.empresa_id ? `empresa_id=${encodeURIComponent(c.empresa_id)}` : '')
    const mia = est
    try {
      const res = await fetch(`/api/proyectos${qs ? '?' + qs : ''}`, { headers: { Authorization: `Bearer ${opts.token()}` } })
      if (!res.ok) return
      const lista = await res.json()
      if (est !== mia || !Array.isArray(lista)) return
      est.proyectos = lista.map(p => ({ id: String(p.id), nombre: p.nombre || 'Sin nombre' })).sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'))
      const sel = modal.querySelector('#cc-proyecto')      // solo se rehacen las opciones: no se redibuja la ventana (se perdería el foco)
      if (sel) sel.innerHTML = htmlOpcionesProyecto()
    } catch (e) { /* sin lista: solo queda «según la columna Proyecto del archivo» */ }
  }

  // El proyecto donde está la persona va primero; después los demás; al final «según el archivo».
  function opcionesProyecto() {
    const actual = opts.contexto.proyecto_id ? String(opts.contexto.proyecto_id) : ''
    const propio = est.proyectos.find(p => p.id === actual) || (actual ? { id: actual, nombre: opts.contexto.proyecto_nombre || 'Proyecto actual' } : null)
    const otros = est.proyectos.filter(p => !propio || p.id !== propio.id)
    return [...(propio ? [{ id: propio.id, texto: `${propio.nombre} (donde estás)` }] : []), ...otros.map(p => ({ id: p.id, texto: p.nombre })), { id: '', texto: 'Según la columna «Proyecto» del archivo' }]
  }

  function htmlOpcionesProyecto() { return opcionesProyecto().map(o => `<option value="${esc(o.id)}"${o.id === est.proyectoId ? ' selected' : ''}>${esc(o.texto)}</option>`).join('') }

  function selectorProyecto() {
    return `<div><label class="cc-et" for="cc-proyecto">Proyecto de destino</label>
      <select id="cc-proyecto" data-cc="proyecto">${htmlOpcionesProyecto()}</select>
      <p class="cc-suave" style="margin:4px 0 0">Las filas que traen su propio proyecto en la columna «Proyecto» se cargan en ese proyecto.</p></div>`
  }

  // ── Dibujo por paso ────────────────────────────────────────────────────────
  function titulo(t) { modal.querySelector('#cc-titulo').textContent = t }
  function cuerpo(html) { modal.querySelector('#cc-cuerpo').innerHTML = html }
  function pie(html) { modal.querySelector('#cc-pie').innerHTML = html }

  function dibujar() {
    if (!est) return
    ({ archivo: dibujarArchivo, revisando: dibujarRevisando, asignar: dibujarAsignar, cargando: dibujarCargando, resultado: dibujarResultado })[est.paso]()
  }

  function dibujarArchivo() {
    titulo('Sube tu archivo de clientes')
    cuerpo(`${selectorProyecto()}
      <div class="cc-drop" data-cc="elegir" role="button" tabindex="0">
        <input type="file" id="cc-archivo" accept=".csv,text/csv" hidden>
        <p style="margin:0 0 4px"><b>Haz clic o arrastra tu archivo .csv</b></p>
        <p class="cc-suave" style="margin:0">${est.archivo ? '📎 ' + esc(est.archivo.name) : 'Antes de cargar nada lo revisamos y te decimos cuántos son y cómo repartirlos.'}</p>
      </div>
      <p class="cc-suave" style="margin:0"><b>Columnas obligatorias:</b> Contacto, Rut, Teléfono. <b>Opcionales:</b> Proyecto, Correo, Tramo de renta, Estado, Tiene subsidio, Tipo subsidio, Tiene propiedad. El orden no importa.</p>
      ${est.error ? `<div class="cc-error" role="alert">${esc(est.error)}</div>` : ''}`)
    pie(`<button type="button" class="cc-btn" data-cc="cerrar">Cancelar</button>`)
  }

  function dibujarRevisando() {
    titulo('Revisando el archivo…')
    cuerpo(`<p class="cc-info" role="status">Estamos leyendo <b>${esc(est.archivo?.name || '')}</b> sin cargar nada. Un momento…</p>`)
    pie('')
  }

  function personaPorId(id) { return (est.analisis.ejecutivos || []).find(e => e.id === id) }

  function dibujarAsignar() {
    const a = est.analisis, limite = a.limite_recomendado || LIMITE_POR_DEFECTO
    titulo(`Son ${num(a.total)} registros`)
    const proyectos = Object.entries(a.por_proyecto || {})
    cuerpo(`${selectorProyecto()}
      <div class="cc-tarjetas">
        <div class="cc-tarjeta"><b>${num(a.total)}</b><span>Registros en el archivo</span></div>
        <div class="cc-tarjeta"><b style="color:var(--cc-ok)">${num(a.se_cargan)}</b><span>Se cargarían</span></div>
        <div class="cc-tarjeta"><b style="color:var(--cc-aviso)">${num(a.duplicados)}</b><span>Duplicados (ya existen)</span></div>
        <div class="cc-tarjeta"><b style="color:var(--cc-error)">${num(a.con_errores)}</b><span>Con errores</span></div>
      </div>
      ${proyectos.length ? `<p class="cc-suave" style="margin:0">Irían a: ${proyectos.map(([n, c]) => `<b>${esc(n)}</b> (${num(c)})`).join(' · ')}</p>` : ''}
      ${a.con_errores ? `<details><summary class="cc-suave" style="cursor:pointer">Ver los errores${a.errores.length < a.con_errores ? ` (los primeros ${a.errores.length})` : ''}</summary>
        <div class="cc-lista" style="margin-top:6px;padding:6px 10px;font-size:12px">${a.errores.map(e => `<p style="margin:2px 0">• Fila ${esc(e.fila ?? '?')}${e.nombre ? ` (${esc(e.nombre)})` : ''}: ${esc(e.motivo ?? '')}</p>`).join('')}</div></details>` : ''}
      ${a.se_cargan === 0 ? '<div class="cc-error" role="alert">No hay nada que cargar: revisa los errores o elige otro archivo.</div>' : seccionAsignacion(a, limite)}
      ${est.error ? `<div class="cc-error" role="alert">${esc(est.error)}</div>` : ''}`)
    pie(`<button type="button" class="cc-btn" data-cc="otro-archivo">Elegir otro archivo</button><button type="button" class="cc-btn cc-prim" data-cc="cargar" id="cc-cargar"></button>`)
    refrescarResumen()
  }

  function seccionAsignacion(a, limite) {
    const rec = a.recomendacion || {}
    if (!opts.esAdmin) {
      // Un ejecutivo carga para sí mismo: solo se le avisa si es mucho.
      return a.se_cargan > limite
        ? `<div class="cc-aviso" role="alert"><b>Son ${num(a.se_cargan)} clientes y lo recomendado es hasta ${limite} por persona.</b> Cargarlos todos a tu nombre puede dejarte con más de lo que puedes atender: pídele a un administrador que los reparta.
           <label class="cc-fila-op" style="margin-top:8px"><input type="checkbox" data-cc="confirmo"${est.confirmo ? ' checked' : ''}> Entiendo y quiero cargarlos igual a mi nombre</label></div>`
        : `<p class="cc-info">Se cargarán a tu nombre.</p>`
    }
    const personas = a.ejecutivos || []
    return `<div class="cc-info"><b>${esc(rec.mensaje || '')}</b></div>
      <fieldset style="border:0;padding:0;margin:0;display:flex;flex-direction:column;gap:8px"><legend class="cc-et" style="margin-bottom:6px">¿A quién se los asignamos?</legend>
        <label class="cc-fila-op"><input type="radio" name="cc-modo" value="repartir" data-cc="modo"${est.modo === 'repartir' ? ' checked' : ''}> Repartir entre varios ejecutivos</label>
        <label class="cc-fila-op"><input type="radio" name="cc-modo" value="una" data-cc="modo"${est.modo === 'una' ? ' checked' : ''}> Todos a una sola persona</label>
        <label class="cc-fila-op"><input type="radio" name="cc-modo" value="nadie" data-cc="modo"${est.modo === 'nadie' ? ' checked' : ''}> Dejarlos sin asignar (los asignas después)</label>
      </fieldset>
      <div id="cc-aviso-exceso"></div>
      ${est.modo === 'repartir' ? tablaReparto(personas, rec) : ''}
      ${est.modo === 'una' ? `<div><label class="cc-et" for="cc-una">Persona</label><select id="cc-una" data-cc="una"><option value="">Yo mismo</option>${personas.map(p => `<option value="${esc(p.id)}"${p.id === est.unaPersona ? ' selected' : ''}>${esc(p.nombre)}${p.clientes_actuales != null ? ` — tiene ${num(p.clientes_actuales)} clientes` : ''}</option>`).join('')}</select></div>` : ''}`
  }

  function tablaReparto(personas, rec) {
    if (!personas.length) return '<div class="cc-aviso">No hay otras personas activas a quienes repartir.</div>'
    return `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <button type="button" class="cc-mini" data-cc="usar-recomendacion">Usar la recomendación${rec.personas > 1 ? ` (${rec.personas} personas)` : ''}</button>
        <button type="button" class="cc-mini" data-cc="partes-iguales">Repartir en partes iguales entre las marcadas</button>
        <button type="button" class="cc-mini" data-cc="limpiar">Limpiar</button>
        <span class="cc-suave" id="cc-resumen-reparto" role="status" aria-live="polite"></span></div>
      <div class="cc-lista"><table class="cc-tabla"><thead><tr><th></th><th>Persona</th><th>Hoy tiene</th><th style="text-align:right">Clientes</th></tr></thead><tbody>
      ${est.filas.map((f, i) => { const p = personaPorId(f.id) || { nombre: 'Sin asignar', rol: null, clientes_actuales: null }
        return `<tr><td><input type="checkbox" data-cc="marcar" data-i="${i}" aria-label="Asignar a ${esc(p.nombre)}"${f.marcada ? ' checked' : ''}></td>
          <td>${esc(p.nombre)}${p.rol && p.rol !== 'ejecutivo' ? ` <span class="cc-chip">${p.rol === 'owner' ? 'owner' : 'administración'}</span>` : ''}${p.tiene_proyecto === false ? ' <span class="cc-chip" title="No tiene asignado este proyecto (igual verá sus clientes)">sin este proyecto</span>' : ''}</td>
          <td>${p.clientes_actuales == null ? '<span class="cc-suave">—</span>' : num(p.clientes_actuales)}</td>
          <td style="text-align:right"><input type="number" min="1" step="1" inputmode="numeric" data-cc="cantidad" data-i="${i}" value="${f.marcada && f.cantidad ? esc(f.cantidad) : ''}" ${f.marcada ? '' : 'disabled'} aria-label="Clientes para ${esc(p.nombre)}"></td></tr>` }).join('')}
      </tbody></table></div>`
  }

  // Actualiza solo los números (sin redibujar la tabla, para no perder el cursor al escribir).
  function refrescarResumen() {
    if (est.paso !== 'asignar') return
    const a = est.analisis, limite = a.limite_recomendado || LIMITE_POR_DEFECTO, boton = modal.querySelector('#cc-cargar')
    const aviso = modal.querySelector('#cc-aviso-exceso')
    let valido = a.se_cargan > 0, exceso = false, mayor = 0
    if (a.se_cargan > 0 && opts.esAdmin) {
      if (est.modo === 'repartir') {
        const r = estadoReparto(a.se_cargan, est.filas, limite)
        valido = r.valido; exceso = r.sobreLimite; mayor = r.mayor
        const el = modal.querySelector('#cc-resumen-reparto')
        if (el) el.innerHTML = `<span class="cc-total">Asignados ${num(r.asignados)} de ${num(a.se_cargan)}</span>${r.faltan ? ` · <b style="color:var(--cc-aviso)">faltan ${num(r.faltan)}</b>` : ''}${r.sobran ? ` · <b style="color:var(--cc-error)">sobran ${num(r.sobran)}</b>` : ''}`
      } else if (est.modo === 'una') { exceso = a.se_cargan > limite; mayor = a.se_cargan }
    } else if (a.se_cargan > 0) { exceso = a.se_cargan > limite; mayor = a.se_cargan }
    if (opts.esAdmin && aviso) {
      aviso.innerHTML = exceso && a.se_cargan > 0 ? `<div class="cc-aviso" role="alert"><b>Una persona recibiría ${num(mayor)} clientes</b> y lo recomendado es hasta ${limite}. Conviene repartirlos entre más ejecutivos.
        <label class="cc-fila-op" style="margin-top:8px"><input type="checkbox" data-cc="confirmo"${est.confirmo ? ' checked' : ''}> Entiendo y quiero cargarlos igual</label></div>` : ''
    }
    if (!exceso) est.confirmo = false
    est.exceso = exceso
    if (boton) {
      boton.textContent = a.se_cargan > 0 ? `Cargar ${num(a.se_cargan)} cliente${a.se_cargan === 1 ? '' : 's'}` : 'Nada que cargar'
      boton.disabled = !(valido && (!exceso || est.confirmo)) || a.se_cargan === 0
    }
  }

  function dibujarCargando() {
    titulo('Cargando clientes…')
    const p = est.progreso || { n: 0, total: est.analisis?.total || 0, creados: 0, dups: 0, errs: 0 }, pct = p.total > 0 ? Math.round((p.n / p.total) * 100) : 0
    cuerpo(`<div role="status" aria-live="polite"><div style="display:flex;justify-content:space-between;font-size:13px"><span>Fila ${num(p.n)} de ${num(p.total)}</span><b>${pct}%</b></div>
      <div style="height:8px;border-radius:999px;background:var(--cc-tenue);margin-top:6px"><div style="height:8px;border-radius:999px;background:var(--cc-acento);width:${pct}%"></div></div></div>
      <div class="cc-tarjetas" style="grid-template-columns:repeat(3,1fr)"><div class="cc-tarjeta"><b style="color:var(--cc-ok)">${num(p.creados)}</b><span>Creados</span></div>
      <div class="cc-tarjeta"><b style="color:var(--cc-aviso)">${num(p.dups)}</b><span>Duplicados</span></div><div class="cc-tarjeta"><b style="color:var(--cc-error)">${num(p.errs)}</b><span>Errores</span></div></div>
      <p class="cc-suave" style="margin:0">No cierres esta ventana hasta que termine.</p>`)
    pie('')
  }

  function nombreDe(id) { return id ? (personaPorId(id)?.nombre || 'Otra persona') : 'Sin asignar' }

  function dibujarResultado() {
    const d = est.resultado || {}
    titulo('Carga terminada')
    const asignados = d.asignados ? Object.entries(d.asignados) : []
    const dups = d.detalle_duplicados || [], errs = d.detalle_errores || []
    cuerpo(`<div class="cc-tarjetas" style="grid-template-columns:repeat(3,1fr)"><div class="cc-tarjeta"><b style="color:var(--cc-ok)">${num(d.creados || 0)}</b><span>Cargados</span></div>
      <div class="cc-tarjeta"><b style="color:var(--cc-aviso)">${num(d.duplicados || 0)}</b><span>Duplicados</span></div><div class="cc-tarjeta"><b style="color:var(--cc-error)">${num(d.errores || 0)}</b><span>Errores</span></div></div>
      ${asignados.length ? `<div class="cc-info"><b>Quedaron asignados así:</b> ${asignados.map(([id, n]) => `${esc(nombreDe(id))} (${num(n)})`).join(' · ')}</div>` : ''}
      ${dups.length || errs.length ? `<div class="cc-lista" style="padding:6px 10px;font-size:12px">
        ${dups.slice(0, 5).map(x => `<p style="margin:2px 0;color:var(--cc-aviso)">• Fila ${esc(x.fila ?? '?')} (${esc(x.nombre)}): el teléfono ya existe</p>`).join('')}${dups.length > 5 ? `<p class="cc-suave" style="margin:2px 0">… y ${num(dups.length - 5)} duplicados más</p>` : ''}
        ${errs.slice(0, 5).map(x => `<p style="margin:2px 0;color:var(--cc-error)">• Fila ${esc(x.fila ?? '?')}${x.nombre ? ` (${esc(x.nombre)})` : ''}: ${esc(x.motivo)}</p>`).join('')}${errs.length > 5 ? `<p class="cc-suave" style="margin:2px 0">… y ${num(errs.length - 5)} errores más</p>` : ''}</div>
        <button type="button" class="cc-mini" data-cc="descargar" style="align-self:flex-start">Descargar rechazados (.csv)</button>` : ''}
      ${est.aviso ? `<div class="cc-aviso">${esc(est.aviso)}</div>` : ''}
      ${opts.enviarWhatsApp && (d.ids_creados || []).length ? `<button type="button" class="cc-mini" data-cc="whatsapp" style="align-self:flex-start">Enviar WhatsApp a los ${num(d.ids_creados.length)} cargados</button>` : ''}`)
    pie(`<button type="button" class="cc-btn cc-prim" data-cc="cerrar">Cerrar</button>`)
  }

  // ── Acciones ───────────────────────────────────────────────────────────────
  function alClic(e) {
    const el = e.target.closest('[data-cc]')
    if (!el || !est) return
    const accion = el.dataset.cc
    if (accion === 'cerrar') cerrar()
    else if (accion === 'elegir') (modal.querySelector('#cc-archivo') || {}).click?.()
    else if (accion === 'otro-archivo') { est.paso = 'archivo'; est.archivo = null; est.analisis = null; est.error = ''; dibujar() }
    else if (accion === 'cargar') cargar()
    else if (accion === 'usar-recomendacion') usarRecomendacion()
    else if (accion === 'partes-iguales') partesEnIgual()
    else if (accion === 'limpiar') { est.filas.forEach(f => { f.marcada = false; f.cantidad = null }); dibujar() }
    else if (accion === 'descargar') descargarRechazados(est.resultado?.detalle_errores || [], est.resultado?.detalle_duplicados || [])
    else if (accion === 'whatsapp') { const ids = (est.resultado?.ids_creados || []).map(c => c.id); cerrar(); opts.enviarWhatsApp(ids) }
  }

  function alCambiar(e) {
    if (!est) return
    // El selector de archivos va primero: está dentro de la zona «elegir», y closest('[data-cc]') daría la zona en vez del <input>.
    if (e.target.id === 'cc-archivo') { if (e.target.files?.[0]) elegirArchivo(e.target.files[0]); return }
    const el = e.target.closest('[data-cc]')
    if (!el) return
    const a = el.dataset.cc
    if (a === 'proyecto') { est.proyectoId = el.value; if (est.archivo) analizar() }
    else if (a === 'modo') { est.modo = el.value; est.confirmo = false; dibujar() }
    else if (a === 'una') { est.unaPersona = el.value; refrescarResumen() }
    else if (a === 'marcar') { const f = est.filas[+el.dataset.i]; f.marcada = el.checked; if (!el.checked) f.cantidad = null; dibujar() }
    else if (a === 'confirmo') { est.confirmo = el.checked; refrescarResumen() }
  }

  function alEscribir(e) {
    const el = e.target.closest('[data-cc="cantidad"]')
    if (!el || !est) return
    const v = el.value.trim()
    est.filas[+el.dataset.i].cantidad = /^\d+$/.test(v) ? parseInt(v, 10) : (v === '' ? null : NaN)
    refrescarResumen()
  }

  function elegirArchivo(f) {
    if (!/\.csv$/i.test(f.name || '')) { est.error = 'Solo se aceptan archivos .csv'; dibujar(); return }
    est.archivo = f
    analizar()
  }

  // Lo que viaja en la dirección: el proyecto de destino y la empresa (acota la búsqueda de proyectos por nombre); al cargar, a quién se asigna y si se confirmó.
  function consulta(paraCargar, confirmo) {
    const c = opts.contexto, q = []
    if (est.proyectoId) q.push(`proyecto_id=${encodeURIComponent(est.proyectoId)}`)
    if (c.empresa_id) q.push(`empresa_id=${encodeURIComponent(c.empresa_id)}`)
    if (paraCargar && opts.esAdmin && est.modo === 'una' && est.unaPersona) q.push(`asignar_usuario_id=${encodeURIComponent(est.unaPersona)}`)
    if (paraCargar && confirmo) q.push('confirmo=1')
    return q.length ? '?' + q.join('&') : ''
  }

  async function analizar() {
    const mia = ++contadorSolicitudes
    est.solicitud = mia
    est.paso = 'revisando'; est.error = ''; est.confirmo = false
    dibujar()
    const fd = new FormData()
    fd.append('file', est.archivo)
    try {
      const res = await fetch('/api/clientes/importar/analizar' + consulta(false, false), { method: 'POST', headers: { Authorization: `Bearer ${opts.token()}` }, body: fd })
      if (!res.ok) throw new Error(await res.text())
      const a = await res.json()
      if (mia !== est.solicitud) return
      est.analisis = a
      est.filas = (a.ejecutivos || []).map(p => ({ id: p.id, marcada: false, cantidad: null }))
      est.modo = a.se_cargan > (a.limite_recomendado || LIMITE_POR_DEFECTO) ? 'repartir' : 'una'
      if (est.modo === 'repartir') usarRecomendacion(false)
      est.paso = 'asignar'
    } catch (err) {
      if (mia !== est.solicitud) return
      est.paso = 'archivo'; est.archivo = null; est.error = err.message || 'No se pudo revisar el archivo.'
    }
    dibujar()
  }

  function usarRecomendacion(redibujar = true) {
    const a = est.analisis, ids = seleccionRecomendada(a.ejecutivos || [], a.recomendacion?.personas || 1)
    const partes = partesIguales(a.se_cargan, ids)
    est.filas.forEach(f => { const p = partes.find(x => x.id === f.id); f.marcada = !!p; f.cantidad = p ? p.cantidad : null })
    est.confirmo = false
    if (redibujar) dibujar()
  }

  function partesEnIgual() {
    const marcadas = est.filas.filter(f => f.marcada), partes = partesIguales(est.analisis.se_cargan, marcadas.map(f => f.id))
    marcadas.forEach(f => { f.cantidad = (partes.find(p => p.id === f.id) || { cantidad: null }).cantidad })
    dibujar()
  }

  async function cargar() {
    if (cargando) return
    cargando = true
    est.paso = 'cargando'; est.progreso = null; est.error = ''
    dibujar()
    opts.alIniciar?.()
    const fd = new FormData()
    fd.append('file', est.archivo)
    if (opts.esAdmin && est.modo === 'repartir') fd.append('reparto', JSON.stringify(cuerpoReparto(est.filas)))
    else if (opts.esAdmin && est.modo === 'nadie') fd.append('reparto', JSON.stringify([{ usuario_id: null, cantidad: est.analisis.se_cargan }]))
    try {
      const res = await fetch('/api/clientes/importar' + consulta(true, est.exceso && est.confirmo), { method: 'POST', headers: { Authorization: `Bearer ${opts.token()}` }, body: fd })
      if (!res.ok) throw new Error(await res.text())
      const lector = res.body.getReader(), decodificador = new TextDecoder()
      let resto = '', final = null
      while (true) {
        const { value, done } = await lector.read()
        if (done) break
        resto += decodificador.decode(value, { stream: true })
        const partes = resto.split('\n\n')
        resto = partes.pop()
        for (const parte of partes) {
          const linea = parte.trim()
          if (!linea.startsWith('data:')) continue
          let evt
          try { evt = JSON.parse(linea.slice(5).trim()) } catch (x) { continue }
          if (evt.t === 'prog') { est.progreso = evt; if (est.paso === 'cargando') dibujar() }
          else if (evt.t === 'done') final = evt
          else if (evt.t === 'error') throw new Error(evt.msg)
        }
      }
      if (!final) throw new Error('La carga se interrumpió antes de terminar. Revisa en el panel qué clientes alcanzaron a cargarse.')
      est.resultado = final
      est.paso = 'resultado'
    } catch (err) {
      est.paso = 'asignar'; est.error = err.message || 'No se pudo cargar el archivo.'
    }
    cargando = false
    opts.alFinalizar?.()
    dibujar()
  }

  function descargarRechazados(errores, duplicados) {
    const cols = ['_tipo', 'Proyecto', 'Contacto', 'Rut', 'Teléfono', 'Correo', 'Estado', 'Tramo de renta', 'Tiene subsidio', 'Tipo subsidio', 'Tiene propiedad', '_motivo']
    const celda = v => `"${String(v ?? '').replace(/"/g, '""')}"`
    const fila = (x, tipo, motivo) => cols.map(c => celda(c === '_tipo' ? tipo : c === '_motivo' ? motivo : (x.datos || {})[c])).join(',')
    const csv = [cols.join(','), ...duplicados.map(d => fila(d, 'Duplicado', 'Teléfono ya existe en el sistema')), ...errores.map(e => fila(e, 'Error', e.motivo || ''))].join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' }))
    a.download = `rechazados_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
  }

  window.CargaClientes = { abrir, cerrar, _puro: { partesIguales, estadoReparto, seleccionRecomendada, cuerpoReparto } }
})()
