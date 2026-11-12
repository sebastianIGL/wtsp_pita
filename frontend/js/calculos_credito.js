/* Cálculos de las herramientas de ejecutivos (frontend/herramientas.html): simulador de crédito hipotecario con abonos a capital y calculadora del subsidio DS1 (tramos 2 y 3).
 * Son funciones puras (sin pantalla) para poder probarlas contra las planillas de referencia. Todo en UF salvo que se diga otra cosa. Las cifras son referenciales. */
(function (raiz) {
  'use strict'

  // ── Crédito hipotecario (sistema francés, dividendo fijo, tasa efectiva anual) ──────────────────────────
  function tasaMensual(teaPct) { return Math.pow(1 + teaPct / 100, 1 / 12) - 1 }
  function dividendo(saldo, i, cuotas) {
    if (cuotas <= 0) return saldo
    return i === 0 ? saldo / cuotas : saldo * i / (1 - Math.pow(1 + i, -cuotas))
  }
  function saldoTras(credito, i, cuotas, pagadas) {
    let saldo = credito; const d = dividendo(credito, i, cuotas)
    for (let k = 1; k <= pagadas && k <= cuotas; k++) saldo -= d - saldo * i
    return Math.max(0, saldo)
  }
  // Suma meses a una fecha «YYYY-MM-DD» (el día se mantiene o se corta al último del mes).
  function sumarMeses(iso, meses) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || '')); if (!m) return null
    const anio = +m[1], mes = +m[2] - 1 + meses, a = anio + Math.floor(mes / 12), mm = ((mes % 12) + 12) % 12
    const ultimo = new Date(Date.UTC(a, mm + 1, 0)).getUTCDate(), dia = Math.min(+m[3], ultimo)
    return `${String(a).padStart(4, '0')}-${String(mm + 1).padStart(2, '0')}-${String(dia).padStart(2, '0')}`
  }

  // Recorre el crédito cuota a cuota. abonos = { cuotasPagadas, unico, unicoEnCuota, mensual, desde, hasta, modalidad: 'plazo' | 'dividendo' } (o null = sin abonos).
  function recorrer(credito, i, cuotas, abonos) {
    let saldo = credito, d = dividendo(credito, i, cuotas), intereses = 0, abonado = 0, pagadoDividendos = 0, k = 0, ultimoDividendo = d
    const ab = abonos || null
    while (saldo > 1e-9 && k < cuotas + 1) {
      k++
      const interes = saldo * i
      let pago = Math.min(d, saldo + interes)
      saldo -= pago - interes; intereses += interes; pagadoDividendos += pago; ultimoDividendo = pago
      if (ab && k > (ab.cuotasPagadas || 0) && saldo > 1e-9) {
        let extra = (ab.unico && k === ab.unicoEnCuota ? ab.unico : 0) + (ab.mensual && k >= ab.desde && k <= ab.hasta ? ab.mensual : 0)
        extra = Math.min(extra, saldo)
        if (extra > 0) { saldo -= extra; abonado += extra; if (ab.modalidad === 'dividendo' && saldo > 1e-9) d = dividendo(saldo, i, cuotas - k) }
      }
      if (saldo < 1e-9) saldo = 0
    }
    return { cuotas: k, intereses, abonado, pagadoDividendos, ultimoDividendo, dividendoFinal: d }
  }

  function simularCredito(e) {
    const valor = Number(e.valorUF) || 0, extra = Math.max(0, Number(e.estacionamientoUF) || 0)
    const pieUF = valor * (Number(e.piePct) || 0) / 100
    const credito = Math.max(0, valor - pieUF + extra)                              // el estacionamiento (o bodega) se suma al crédito
    const plazo = Math.min(40, Math.max(1, Math.round(Number(e.plazoAnios) || 0))), cuotas = plazo * 12
    const i = tasaMensual(Number(e.teaPct) || 0), ufPesos = Number(e.valorUFPesos) || 0
    const div = dividendo(credito, i, cuotas)
    const ab = e.abonos ? Object.assign({ cuotasPagadas: 0, unico: 0, unicoEnCuota: 0, mensual: 0, desde: 1, hasta: cuotas, modalidad: 'plazo' }, e.abonos) : null
    const sin = recorrer(credito, i, cuotas, null), con = recorrer(credito, i, cuotas, ab)
    const pagadas = ab ? Math.min(ab.cuotasPagadas || 0, cuotas) : 0
    const saldoActual = saldoTras(credito, i, cuotas, pagadas)
    const fin = (n) => sumarMeses(e.primeraCuota, n - 1)
    const pesos = uf => uf * ufPesos
    const res = (r) => ({ cuotas: r.cuotas, anios: r.cuotas / 12, fechaUltima: fin(r.cuotas), interesesUF: r.intereses, interesesPesos: pesos(r.intereses), abonadoUF: r.abonado, pagadoUF: r.pagadoDividendos + r.abonado,
      dividendoDespuesUF: r.dividendoFinal, dividendoDespuesPesos: pesos(r.dividendoFinal) })
    const A = res(sin), B = res(con), menos = sin.cuotas - con.cuotas
    return {
      pieUF, creditoUF: credito, valorFinanciadoUF: valor + extra, tasaMensual: i, dividendoUF: div, dividendoPesos: pesos(div), cuotas, saldoActualUF: saldoActual, saldoActualPesos: pesos(saldoActual),
      sinAbonos: A, conAbonos: B,
      gana: { aniosMenos: Math.floor(menos / 12), mesesMenos: menos % 12, cuotasMenos: menos, interesesAhorradosUF: A.interesesUF - B.interesesUF, interesesAhorradosPesos: pesos(A.interesesUF - B.interesesUF),
        ahorroPorUFAbonada: B.abonadoUF > 0 ? (A.interesesUF - B.interesesUF) / B.abonadoUF : 0, ltv: valor + extra > 0 ? saldoActual / (valor + extra) : 0, rebajaDividendoUF: A.dividendoDespuesUF - B.dividendoDespuesUF },
    }
  }

  // ── Subsidio DS1, tramos 2 y 3 (modalidad adquisición, individual) ────────────────────────────────────
  const PARAMETROS_DS1 = {
    2: { constante: 850, factor: 0.375, subsidioMax: 550, precioMax: 1600, montoFijo: null, ahorroMin: 40 },
    3: { constante: 950, factor: 0.375, subsidioMax: 500, precioMax: 2600, montoFijo: 350, ahorroMin: 80 },
  }
  // El precio máximo (y algún monto) cambia según región y llamado: la pantalla deja editar los parámetros y se pasan aquí.
  function subsidioDS1(e) {
    const tramo = Number(e.tramo) === 2 ? 2 : 3, p = Object.assign({}, PARAMETROS_DS1[tramo], e.parametros || {})
    const precio = Math.max(0, Number(e.precioUF) || 0), ahorro = Math.max(0, Number(e.ahorroUF) || 0), ufPesos = Number(e.valorUFPesos) || 0
    const formula = p.constante - p.factor * precio
    let subsidio = 0, estado
    if (!(precio > 0)) estado = 'Ingresa el precio de la vivienda'
    else if (precio > p.precioMax) estado = `Sobre el precio máximo (${p.precioMax.toLocaleString('es-CL')} UF): no accede al subsidio`
    else if (tramo === 3 && p.montoFijo != null && precio >= 1600) { subsidio = p.montoFijo; estado = `Monto fijo (precio entre 1.600 UF y el máximo)` }
    else if (formula >= p.subsidioMax) { subsidio = p.subsidioMax; estado = 'Subsidio máximo (la fórmula supera el tope)' }
    else { subsidio = Math.max(0, formula); estado = 'Según la fórmula' }
    const credito = precio > 0 ? Math.max(0, precio - subsidio - ahorro) : 0
    const avisos = []
    if (precio > 0 && ahorro < p.ahorroMin) avisos.push(`El ahorro acreditado (${ahorro.toLocaleString('es-CL')} UF) es menor al mínimo del tramo ${tramo}: ${p.ahorroMin} UF`)
    return { tramo, parametros: p, formulaUF: formula, subsidioUF: subsidio, ahorroUF: ahorro, creditoUF: credito, precioUF: precio, subsidioPesos: subsidio * ufPesos, ahorroPesos: ahorro * ufPesos, creditoPesos: credito * ufPesos, precioPesos: precio * ufPesos, estado, avisos }
  }
  // Tabla de referencia del tramo (precio → subsidio, ahorro, crédito) para un ahorro dado
  function tablaDS1(tramo, preciosUF, ahorroUF, parametros) {
    return preciosUF.map(pr => { const r = subsidioDS1({ tramo, precioUF: pr, ahorroUF, parametros }); return { precioUF: pr, subsidioUF: r.subsidioUF, ahorroUF, creditoUF: r.creditoUF } })
  }

  const API = { tasaMensual, dividendo, saldoTras, sumarMeses, simularCredito, PARAMETROS_DS1, subsidioDS1, tablaDS1 }
  if (typeof module !== 'undefined' && module.exports) module.exports = API
  else raiz.CalculosCredito = API
})(typeof window !== 'undefined' ? window : globalThis)
