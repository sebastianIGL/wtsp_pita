/* Cotizar a varios clientes a la vez: la ventana que abre «Enviar cotización» de la barra de la Tabla cuando hay más de un cliente marcado.
 *
 * Primero REVISA a los clientes sin enviar nada (POST /api/cotizaciones/lote/revisar): a quién se le puede cotizar y a quién no (con el motivo), su correo y por dónde saldría el WhatsApp.
 * Después se eligen los canales (correo y/o WhatsApp) y se envía de a uno con avance en vivo (POST /api/cotizaciones/lote/enviar). Un cliente con problema no frena a los demás.
 *
 * Uso:  CotizarLote.abrir({ token: () => _token, ids: [ids de clientes], acento: '#0ea5e9', alTerminar() })
 * Todo texto que viene del servidor o de personas se escribe con esc(): nunca como HTML. La ventana no usa manejadores en línea (onclick…): usa delegación de eventos.
 */
(function () {
  'use strict'

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const nf = new Intl.NumberFormat('es-CL')

  // ── Funciones puras (se prueban sin la página) ─────────────────────────────
  // Resumen de la revisión: cuántos están listos, cuántos no, y cuántos recibirían WhatsApp / correo según los canales elegidos.
  function resumenRevision(clientes, canales) {
    const listos = clientes.filter(c => c.ok)
    return {
      total: clientes.length, listos: listos.length, no: clientes.length - listos.length,
      conCorreo: listos.filter(c => c.correo).length, sinCorreo: listos.filter(c => !c.correo).length,
      wa: listos.filter(c => c.whatsapp !== 'no').length, sinWa: listos.filter(c => c.whatsapp === 'no' && c.telefono).length, sinTelefono: listos.filter(c => !c.telefono).length,
      puedeEnviar: listos.length > 0 && canales.length > 0,
    }
  }
  function textoWhatsapp(c, plantillaConfigurada) {
    if (c.whatsapp === 'ventana') return 'Sí, dentro de las 24 h'
    if (c.whatsapp === 'plantilla') return plantillaConfigurada ? 'Sí, con plantilla' : 'No'
    return c.telefono ? 'No: fuera de las 24 h' : 'Sin teléfono'
  }
  function interpretarEvento(linea) {
    const t = String(linea || '').trim()
    if (!t.startsWith('data:')) return null
    try { return JSON.parse(t.slice(5).trim()) } catch (e) { return null }
  }

  // ── Estado y pantalla ──────────────────────────────────────────────────────
  let opts = null, est = null, modal = null, solicitud = 0

  const CSS = `
  #cl-modal { --cl-fondo:#ffffff; --cl-texto:#1e293b; --cl-suave:#64748b; --cl-borde:#cbd5e1; --cl-tenue:#f1f5f9; --cl-ok:#15803d; --cl-aviso:#b45309; --cl-aviso-fondo:#fef3c7; --cl-error:#b91c1c; --cl-error-fondo:#fee2e2; --cl-acento:#0ea5e9; }
  html.dark #cl-modal { --cl-fondo:#111c2e; --cl-texto:#e2e8f0; --cl-suave:#94a3b8; --cl-borde:#334155; --cl-tenue:#1b2740; --cl-ok:#4ade80; --cl-aviso:#fbbf24; --cl-aviso-fondo:rgba(245,158,11,.14); --cl-error:#f87171; --cl-error-fondo:rgba(239,68,68,.14); }
  #cl-modal { position:fixed; inset:0; z-index:70; display:flex; align-items:center; justify-content:center; padding:16px; background:rgba(15,23,42,.55); font-family:inherit; }
  #cl-modal[hidden] { display:none; }
  #cl-modal .cl-caja { width:100%; max-width:820px; max-height:92vh; display:flex; flex-direction:column; border-radius:16px; background:var(--cl-fondo); color:var(--cl-texto); border:1px solid var(--cl-borde); box-shadow:0 24px 60px rgba(0,0,0,.35); }
  #cl-modal header { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding:18px 22px 12px; border-bottom:1px solid var(--cl-borde); }
  #cl-modal .cl-sup { font-size:12px; color:var(--cl-suave); margin:0; }
  #cl-modal h3 { font-size:17px; font-weight:700; margin:2px 0 0; }
  #cl-modal .cl-x { font-size:20px; line-height:1; color:var(--cl-suave); background:none; border:0; cursor:pointer; padding:2px 6px; }
  #cl-modal .cl-cuerpo { padding:16px 22px; overflow-y:auto; display:flex; flex-direction:column; gap:14px; }
  #cl-modal .cl-cuerpo > * { flex-shrink:0; }
  #cl-modal footer { display:flex; align-items:center; justify-content:flex-end; gap:10px; padding:12px 22px 16px; border-top:1px solid var(--cl-borde); flex-wrap:wrap; }
  #cl-modal .cl-btn { font-size:14px; font-weight:600; padding:8px 16px; border-radius:10px; border:1px solid var(--cl-borde); background:var(--cl-tenue); color:var(--cl-texto); cursor:pointer; }
  #cl-modal .cl-btn.cl-primario { background:var(--cl-acento); border-color:var(--cl-acento); color:#fff; }
  #cl-modal .cl-btn[disabled] { opacity:.5; cursor:not-allowed; }
  #cl-modal .cl-tabla-caja { border:1px solid var(--cl-borde); border-radius:12px; overflow:auto; max-height:40vh; }
  #cl-modal table { width:100%; border-collapse:collapse; font-size:13px; }
  #cl-modal th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--cl-suave); padding:8px 12px; position:sticky; top:0; background:var(--cl-fondo); border-bottom:1px solid var(--cl-borde); }
  #cl-modal td { padding:8px 12px; border-bottom:1px solid var(--cl-borde); vertical-align:top; overflow-wrap:anywhere; }
  #cl-modal tr:last-child td { border-bottom:0; }
  #cl-modal .cl-ok { color:var(--cl-ok); font-weight:600; }
  #cl-modal .cl-mal { color:var(--cl-error); }
  #cl-modal .cl-suave { color:var(--cl-suave); }
  #cl-modal .cl-aviso { background:var(--cl-aviso-fondo); color:var(--cl-aviso); border-radius:10px; padding:10px 12px; font-size:13px; }
  #cl-modal .cl-error { background:var(--cl-error-fondo); color:var(--cl-error); border-radius:10px; padding:10px 12px; font-size:13px; }
  #cl-modal .cl-canal { display:flex; gap:10px; align-items:flex-start; padding:10px 12px; border:1px solid var(--cl-borde); border-radius:12px; cursor:pointer; }
  #cl-modal .cl-canal input { margin-top:3px; accent-color:var(--cl-acento); }
  #cl-modal .cl-canal b { display:block; font-size:14px; }
  #cl-modal .cl-canal small { color:var(--cl-suave); font-size:12px; }
  #cl-modal .cl-barra { height:10px; border-radius:999px; background:var(--cl-tenue); overflow:hidden; }
  #cl-modal .cl-barra > span { display:block; height:100%; background:var(--cl-acento); transition:width .2s; }
  #cl-modal .cl-tarjetas { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  #cl-modal .cl-tarjeta { border:1px solid var(--cl-borde); border-radius:12px; padding:10px 12px; background:var(--cl-tenue); }
  #cl-modal .cl-tarjeta b { display:block; font-size:22px; font-variant-numeric:tabular-nums; }
  #cl-modal .cl-tarjeta span { font-size:12px; color:var(--cl-suave); }
  #cl-modal button:focus-visible, #cl-modal input:focus-visible { outline:2px solid var(--cl-acento); outline-offset:2px; }
  `

  function montar() {
    if (modal) return
    const st = document.createElement('style')
    st.id = 'cl-estilos'
    st.textContent = CSS
    document.head.appendChild(st)
    modal = document.createElement('div')
    modal.id = 'cl-modal'
    modal.hidden = true
    modal.setAttribute('role', 'dialog')
    modal.setAttribute('aria-modal', 'true')
    modal.setAttribute('aria-labelledby', 'cl-titulo')
    modal.innerHTML = `<div class="cl-caja"><header><div><p class="cl-sup">Cotizar</p><h3 id="cl-titulo"></h3></div><button type="button" class="cl-x" data-cl="cerrar" aria-label="Cerrar">✕</button></header>
      <div class="cl-cuerpo" id="cl-cuerpo"></div><footer id="cl-pie"></footer></div>`
    document.body.appendChild(modal)
    modal.addEventListener('click', alClic)
    modal.addEventListener('change', alCambiar)
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && modal && !modal.hidden && est && est.paso !== 'enviando') cerrar() })
  }

  function abrir(o) {
    opts = Object.assign({ acento: '#0ea5e9', ids: [] }, o || {})
    montar()
    modal.style.setProperty('--cl-acento', opts.acento)
    est = { paso: 'revisando', clientes: [], plantilla: false, maximo: 40, canales: ['correo'], error: '', progreso: null, final: null, ultimos: [] }
    modal.hidden = false
    dibujar()
    revisar()
  }

  function cerrar() {
    if (!modal) return
    const hizoAlgo = est && (est.paso === 'resultado' || (est.progreso && est.progreso.n > 0))
    solicitud++
    modal.hidden = true
    if (hizoAlgo) opts.alTerminar?.()
  }

  async function revisar() {
    const mia = ++solicitud
    try {
      const res = await fetch('/api/cotizaciones/lote/revisar', { method: 'POST', headers: { Authorization: `Bearer ${opts.token()}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: opts.ids }) })
      if (!res.ok) throw new Error(await res.text())
      const d = await res.json()
      if (mia !== solicitud) return
      est.clientes = d.clientes || []; est.plantilla = !!d.plantilla_configurada; est.maximo = d.maximo || 40
      est.paso = 'elegir'; est.error = ''
    } catch (e) {
      if (mia !== solicitud) return
      est.paso = 'error'; est.error = e.message || 'No se pudo revisar a los clientes.'
    }
    dibujar()
  }

  async function enviar() {
    const listos = est.clientes.filter(c => c.ok).map(c => c.id)
    if (!listos.length || !est.canales.length) return
    est.paso = 'enviando'; est.progreso = { n: 0, total: listos.length, enviados: 0, fallidos: 0 }; est.ultimos = []; est.final = null; est.error = ''
    dibujar()
    const mia = ++solicitud
    try {
      const res = await fetch('/api/cotizaciones/lote/enviar', { method: 'POST', headers: { Authorization: `Bearer ${opts.token()}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: listos, canales: est.canales }) })
      if (!res.ok) throw new Error(await res.text())
      const lector = res.body.getReader(), dec = new TextDecoder()
      let resto = ''
      while (true) {
        const { value, done } = await lector.read()
        if (done) break
        resto += dec.decode(value, { stream: true })
        const partes = resto.split('\n\n')
        resto = partes.pop()
        for (const parte of partes) {
          const evt = interpretarEvento(parte)
          if (!evt) continue
          if (evt.t === 'prog') { est.progreso = evt; est.ultimos.unshift(evt.cliente); est.ultimos = est.ultimos.slice(0, 6); if (mia === solicitud && est.paso === 'enviando') dibujar() }
          else if (evt.t === 'fin') est.final = evt
        }
      }
      if (!est.final) throw new Error('El envío se interrumpió antes de terminar. Revisa en cada cliente si la cotización alcanzó a salir.')
      est.paso = 'resultado'
    } catch (e) {
      est.paso = 'resultado'
      est.error = e.message || 'No se pudo enviar.'
      est.final = est.final || { total: est.progreso ? est.progreso.total : listos.length, enviados: est.progreso ? est.progreso.enviados : 0, fallidos: [] }
    }
    dibujar()
  }

  function alClic(e) {
    const b = e.target.closest('[data-cl]')
    if (!b) return
    const a = b.dataset.cl
    if (a === 'cerrar') cerrar()
    else if (a === 'enviar') enviar()
    else if (a === 'reintentar') { est.paso = 'revisando'; est.error = ''; dibujar(); revisar() }
  }
  function alCambiar(e) {
    const c = e.target.closest('[data-canal]')
    if (!c) return
    const k = c.dataset.canal
    est.canales = est.canales.filter(x => x !== k).concat(c.checked ? [k] : [])
    dibujar()
  }

  function dibujar() {
    const cuerpo = modal.querySelector('#cl-cuerpo'), pie = modal.querySelector('#cl-pie'), titulo = modal.querySelector('#cl-titulo')
    const x = resumenRevision(est.clientes, est.canales)
    if (est.paso === 'revisando') {
      titulo.textContent = `Cotizar a ${opts.ids.length} clientes`
      cuerpo.innerHTML = '<p class="cl-suave">Revisando a los clientes… No se envía nada todavía.</p>'
      pie.innerHTML = '<button type="button" class="cl-btn" data-cl="cerrar">Cancelar</button>'
    } else if (est.paso === 'error') {
      titulo.textContent = 'No se pudo revisar'
      cuerpo.innerHTML = `<div class="cl-error" role="alert">${esc(est.error)}</div>`
      pie.innerHTML = '<button type="button" class="cl-btn" data-cl="cerrar">Cerrar</button><button type="button" class="cl-btn cl-primario" data-cl="reintentar">Reintentar</button>'
    } else if (est.paso === 'elegir') {
      titulo.textContent = `Cotizar a ${x.total} clientes`
      const filas = est.clientes.map(c => `<tr><td><b>${esc(c.nombre)}</b>${c.ok ? `<br><span class="cl-suave">${esc(c.proyecto)} · ${esc(c.tipologia)}</span>` : ''}</td>
        <td>${c.ok ? (c.correo ? esc(c.correo) : '<span class="cl-mal">Sin correo válido</span>') : '—'}</td>
        <td>${c.ok ? esc(textoWhatsapp(c, est.plantilla)) : '—'}</td>
        <td>${c.ok ? '<span class="cl-ok">✓ Listo</span>' : `<span class="cl-mal">${esc(c.motivo)}</span>`}</td></tr>`).join('')
      cuerpo.innerHTML = `<p><b>${nf.format(x.listos)}</b> de ${nf.format(x.total)} ${x.listos === 1 ? 'está listo' : 'están listos'} para cotizar${x.no ? `; ${nf.format(x.no)} no (se explica el motivo en cada uno, y no se les enviará nada)` : ''}.</p>
        <div class="cl-tabla-caja"><table><thead><tr><th>Cliente</th><th>Correo</th><th>WhatsApp</th><th>Estado</th></tr></thead><tbody>${filas}</tbody></table></div>
        <div role="group" aria-label="Canales">
          <label class="cl-canal"><input type="checkbox" data-canal="correo" ${est.canales.includes('correo') ? 'checked' : ''}><span><b>Correo</b><small>Al correo que cada cliente tiene registrado, con el PDF adjunto, desde tu correo.${x.sinCorreo ? ` ${nf.format(x.sinCorreo)} no tiene${x.sinCorreo === 1 ? '' : 'n'} correo válido: a ${x.sinCorreo === 1 ? 'ese' : 'esos'} no le saldrá.` : ''}</small></span></label>
          <label class="cl-canal" style="margin-top:8px"><input type="checkbox" data-canal="whatsapp" ${est.canales.includes('whatsapp') ? 'checked' : ''}><span><b>WhatsApp</b><small>Dentro de las 24 h desde su último mensaje, el PDF va directo. ${est.plantilla ? 'Fuera de ese plazo, se le escribe con la plantilla de cotización.' : 'Fuera de ese plazo no se puede (falta configurar la plantilla de cotización): a esos clientes no les saldrá.'}${x.sinWa ? ` Hoy ${nf.format(x.sinWa)} ${x.sinWa === 1 ? 'cliente quedaría' : 'clientes quedarían'} sin WhatsApp por eso.` : ''}</small></span></label>
        </div>
        ${x.listos > 0 ? '<p class="cl-suave" style="font-size:12px">Quien reciba la cotización por cualquier canal pasa a «Cotización enviada». Cada envío queda registrado en el cliente.</p>' : '<div class="cl-aviso">Ninguno de los clientes marcados se puede cotizar todavía. Completa lo que falta en cada uno y vuelve a intentarlo.</div>'}`
      pie.innerHTML = `<button type="button" class="cl-btn" data-cl="cerrar">Cancelar</button><button type="button" class="cl-btn cl-primario" data-cl="enviar" ${x.puedeEnviar ? '' : 'disabled'}>Enviar a ${nf.format(x.listos)} cliente${x.listos === 1 ? '' : 's'}</button>`
    } else if (est.paso === 'enviando') {
      const p = est.progreso || { n: 0, total: 0, enviados: 0, fallidos: 0 }
      titulo.textContent = 'Enviando cotizaciones…'
      cuerpo.innerHTML = `<div class="cl-barra" role="progressbar" aria-valuemin="0" aria-valuemax="${p.total}" aria-valuenow="${p.n}"><span style="width:${p.total ? Math.round((p.n / p.total) * 100) : 0}%"></span></div>
        <p><b>${nf.format(p.n)}</b> de ${nf.format(p.total)} · ${nf.format(p.enviados)} enviadas · ${nf.format(p.fallidos)} con problema</p>
        <ul class="cl-suave" style="list-style:none;padding:0;margin:0;font-size:13px">${est.ultimos.map(u => `<li>${u.ok ? '<span class="cl-ok">✓</span>' : '<span class="cl-mal">✗</span>'} ${esc(u.nombre)}${u.ok ? '' : ` — ${esc(u.motivo)}`}</li>`).join('')}</ul>
        <p class="cl-suave" style="font-size:12px">No cierres esta ventana hasta que termine.</p>`
      pie.innerHTML = ''
    } else if (est.paso === 'resultado') {
      const f = est.final
      titulo.textContent = 'Listo'
      cuerpo.innerHTML = `${est.error ? `<div class="cl-error" role="alert">${esc(est.error)}</div>` : ''}
        <div class="cl-tarjetas"><div class="cl-tarjeta"><b>${nf.format(f.enviados)}</b><span>${f.enviados === 1 ? 'cotización enviada' : 'cotizaciones enviadas'}</span></div><div class="cl-tarjeta"><b>${nf.format(f.fallidos.length)}</b><span>con problema</span></div><div class="cl-tarjeta"><b>${nf.format(f.total)}</b><span>revisadas</span></div></div>
        ${f.fallidos.length ? `<div><p><b>Los que no salieron</b></p><div class="cl-tabla-caja"><table><tbody>${f.fallidos.map(u => `<tr><td><b>${esc(u.nombre)}</b></td><td class="cl-mal">${esc(u.motivo)}</td></tr>`).join('')}</tbody></table></div></div>` : '<p class="cl-ok">Todas salieron bien.</p>'}`
      pie.innerHTML = '<button type="button" class="cl-btn cl-primario" data-cl="cerrar">Cerrar</button>'
    }
  }

  window.CotizarLote = { abrir, cerrar, _puro: { resumenRevision, textoWhatsapp, interpretarEvento } }
})()
