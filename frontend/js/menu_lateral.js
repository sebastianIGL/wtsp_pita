/* Menú lateral del panel comercial (/panel) y del panel de administración (/admin.html): queda fijo mientras se baja por la página y se puede esconder.
 * Lo que la persona eligió (visible u oculto) se recuerda en este navegador. El <aside> debe tener id="menu-lateral" y un botón con onclick="alternarMenu()".
 * Al esconderlo aparece un botón flotante arriba a la izquierda para volver a mostrarlo. */
(function () {
  'use strict'
  var CLAVE = 'crm-menu-oculto', raiz = document.documentElement
  try { if (localStorage.getItem(CLAVE) === '1') raiz.classList.add('menu-oculto') } catch (e) { /* sin almacenamiento: parte visible */ }
  var st = document.createElement('style')
  st.textContent = [
    '#menu-lateral { position: sticky; top: 0; height: 100vh; align-self: flex-start; }',
    'html.menu-oculto #menu-lateral { display: none; }',
    '#abrir-menu { display: none; position: fixed; top: 12px; left: 12px; z-index: 40; width: 36px; height: 36px; align-items: center; justify-content: center; border-radius: 10px; cursor: pointer; border: 1px solid rgba(148,163,184,.4); background: rgba(255,255,255,.95); color: #475569; box-shadow: 0 1px 4px rgba(0,0,0,.12); }',
    'html.dark #abrir-menu { background: #1e293b; color: #e2e8f0; border-color: #334155; }',
    '#abrir-menu:hover { color: #7c3aed; } #abrir-menu:focus-visible { outline: 2px solid #7c3aed; outline-offset: 2px; }',
    'html.menu-oculto #abrir-menu { display: flex; }',
    'html.menu-oculto main { padding-left: 4rem !important; }',
  ].join('\n')
  document.head.appendChild(st)
  window.alternarMenu = function () {
    var oculto = raiz.classList.toggle('menu-oculto')
    try { localStorage.setItem(CLAVE, oculto ? '1' : '0') } catch (e) { /* no se pudo recordar */ }
    var b = document.getElementById('abrir-menu')
    if (b) b.setAttribute('aria-expanded', String(!oculto))
  }
  document.addEventListener('DOMContentLoaded', function () {
    var b = document.createElement('button')
    b.type = 'button'; b.id = 'abrir-menu'; b.title = 'Mostrar el menú'; b.setAttribute('aria-label', 'Mostrar el menú'); b.setAttribute('aria-expanded', String(!raiz.classList.contains('menu-oculto')))
    b.innerHTML = '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>'
    b.addEventListener('click', window.alternarMenu)
    document.body.appendChild(b)
  })
})()
