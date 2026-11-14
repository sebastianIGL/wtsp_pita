/* Cálculo del estimador de comisiones (frontend/herramientas.html): la comisión que gana un broker por cada venta y los bonos por cantidad de ventas.
 * Son funciones puras (sin pantalla) para poder probarlas contra la planilla «Estimador de Comisión Broker — Venta de Propiedades Valores BRUTOS».
 *
 *   comisión por venta ($) = ticket (UF) × porcentaje ÷ 100 × valor de la UF
 *   bono único             = solo UNO, el más alto que se alcance: con 3 ventas o más, el bono «de 3 ventas»; con 2 (y menos de 3), el bono «de 2 ventas»; con menos de 2, ninguno.
 *                            Los bonos NO se suman entre sí ni se repiten por tener más ventas.
 *   total ($)              = ventas × comisión por venta + bono *
 * Todo sin redondear: se redondea solo al mostrar (así el total calza con la planilla al peso). */
(function (raiz) {
  'use strict'

  const num = v => { const n = Number(v); return v !== null && v !== '' && Number.isFinite(n) ? n : null }
  const ventasValidas = v => { const n = num(v); return n == null || n < 0 ? 0 : Math.floor(n) }

  function comisionPorVenta(plan, uf) {
    const t = num(plan && plan.ticket_uf), p = num(plan && plan.porcentaje), u = num(uf)
    if (t == null || p == null || u == null || u <= 0) return null
    return t * (p / 100) * u
  }

  // El bono único que corresponde: ``nivel`` es 3, 2 o 0 (ninguno).
  function bonos(plan, ventas) {
    const n = ventasValidas(ventas)
    const b2 = num(plan && plan.bono_2_clp) || 0, b3 = num(plan && plan.bono_3_clp) || 0
    const nivel = n >= 3 && b3 > 0 ? 3 : (n >= 2 && b2 > 0 ? 2 : 0)
    return { nivel, total: nivel === 3 ? b3 : (nivel === 2 ? b2 : 0) }
  }

  // Una fila del estimador. ``porVenta`` y ``total`` son null si falta el valor de la UF.
  function calcularPlan(plan, ventas, uf) {
    const n = ventasValidas(ventas), pv = comisionPorVenta(plan, uf), b = bonos(plan, n)
    return { ventas: n, porVenta: pv, comisiones: pv == null ? null : pv * n, bonos: b, total: pv == null ? null : pv * n + b.total }
  }

  // El total de todas las filas: ``filas`` = [{ plan, ventas }].
  function calcularTotal(filas, uf) {
    let ventas = 0, bonosTotal = 0, total = 0, completo = true
    for (const f of filas) {
      const r = calcularPlan(f.plan, f.ventas, uf)
      ventas += r.ventas; bonosTotal += r.bonos.total
      if (r.total == null) completo = false; else total += r.total
    }
    return { ventas, bonos: bonosTotal, total: completo ? total : null }
  }

  const API = { comisionPorVenta, bonos, calcularPlan, calcularTotal, ventasValidas }
  if (typeof module !== 'undefined' && module.exports) module.exports = API
  else raiz.CalculosComision = API
})(typeof window !== 'undefined' ? window : globalThis)
