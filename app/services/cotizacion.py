"""Cotización de una vivienda para un cliente: los números, el PDF, el correo y el mensaje de WhatsApp (botón «Cotizar» del panel).

Los números son los mismos que ve el banco en el correo de evaluación (``app/services/evaluacion_email.py``): valor de la tipología, subsidio que informa la inmobiliaria y
ahorro del cliente. Regla del dueño: **pie = ahorro + subsidio** y crédito = valor − pie (nunca menos de cero). Todo es referencial y está en UF; los pesos son aproximados y solo
se muestran si se pudo leer la UF del día (nunca se inventa un valor).

``armar`` y lo que viene después son funciones puras sobre un diccionario de datos: se pueden probar sin base de datos y la «foto» que queda guardada de cada envío
(columna ``valores`` de ``Cotizacion``) alcanza para reconstruir el mismo PDF.
"""
import io
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as _xml

from app.clients.supabase import _supabase_request
from app.core.errors import AppError
from app.services import ficha_proyecto as fp

_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_CONDICION = {"nueva": "Nueva", "semi-nueva": "Semi-nueva"}
DESCARGO = ("Cotización referencial. Los valores están en UF y los montos en pesos son aproximados, calculados con la UF indicada. Están sujetos a la evaluación crediticia del banco, "
            "a la aprobación del subsidio y a la disponibilidad de la vivienda; no constituyen una promesa de compraventa ni una oferta de crédito.")
MAX_CAPTION_WHATSAPP = 1024
ASUNTO_MAX = 150


# ── Formato ───────────────────────────────────────────────────────────────────

def fmt_uf(n: Any) -> str:
    """1.234,5 UF (un decimal, sin «,0»); el mismo criterio de la calculadora."""
    r = round(float(n) + 1e-9, 1)
    texto = f"{r:,.1f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return (texto[:-2] if texto.endswith(",0") else texto) + " UF"


def fmt_clp(n: Any) -> str:
    return "$" + f"{int(round(float(n))):,}".replace(",", ".")


def clp_aprox(uf: Optional[float], valor_uf: Optional[float]) -> Optional[int]:
    """Pesos aproximados (a los mil más cercanos), o None si no hay UF del día."""
    if uf is None or not valor_uf:
        return None
    return int(round(uf * valor_uf / 1000.0)) * 1000


def fecha_larga(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d:02d} {_MESES[m - 1]} {y}"


def _limpio(texto: Any, largo: int = 200) -> str:
    """Texto de una persona listo para mostrar: una sola línea, sin caracteres de control, de largo acotado."""
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", str(texto if texto is not None else "")).strip()
    return re.sub(r"\s{2,}", " ", t)[:largo]


def _latin(texto: str) -> str:
    """El PDF usa la tipografía Helvetica estándar (Latin-1/Windows): lo que no cabe ahí se reemplaza por «?» en vez de salir como un cuadrado."""
    return texto.encode("cp1252", errors="replace").decode("cp1252")


def nombre_archivo(cot: Dict[str, Any]) -> str:
    """Nombre del PDF adjunto: sin tildes ni caracteres raros (algunos programas de correo y WhatsApp los muestran mal)."""
    base = unicodedata.normalize("NFKD", f"Cotizacion {cot['proyecto']['nombre']} {cot['tipologia']['nombre']}").encode("ascii", errors="ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_")[:80] + ".pdf"


# ── Datos y armado ────────────────────────────────────────────────────────────

async def cargar(cliente_id: int) -> Dict[str, Any]:
    """Lee de la base lo que hace falta para cotizar: el cliente, su proyecto (con la inmobiliaria) y su tipología y etapa."""
    filas = await _supabase_request("GET", "/Cliente", params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"}) or []
    if not filas:
        raise AppError("Cliente no encontrado", 404)
    cliente = filas[0]
    proyecto = None
    if cliente.get("proyecto_id"):
        pf = await _supabase_request("GET", "/Proyecto", params={"id": f"eq.{cliente['proyecto_id']}", "select": "*,Inmobiliaria(nombre)", "limit": "1"}) or []
        proyecto = pf[0] if pf else None
    et = None
    if cliente.get("etapa_tipologia_id"):
        ef = await _supabase_request("GET", "/EtapaTipologia", params={"id": f"eq.{cliente['etapa_tipologia_id']}", "select": "Tipologia(*),Etapa(*)", "limit": "1"}) or []
        et = ef[0] if ef else None
    return {"cliente": cliente, "proyecto": proyecto, "et": et}


def _positivo(v: Any) -> Optional[float]:
    n = fp._a_numero(v)
    return n if n is not None and n > 0 else None


def armar(datos: Dict[str, Any], usuario: Optional[Dict[str, Any]], uf: Optional[Dict[str, Any]], hoy: date) -> Dict[str, Any]:
    """La cotización lista para mostrar, enviar y guardar. Si falta algo imprescindible lanza ``AppError`` con el mensaje para la persona."""
    c, p, et = datos["cliente"], datos.get("proyecto") or {}, datos.get("et")
    if not et or not (et.get("Tipologia") or {}):
        raise AppError("El cliente no tiene tipología y etapa asignadas. Edítalo y elige la tipología y la etapa antes de cotizar.", 400)
    tip, etapa = et.get("Tipologia") or {}, et.get("Etapa") or {}
    valor = _positivo(tip.get("valor_uf"))
    if valor is None:
        raise AppError("La tipología del cliente no tiene valor en UF. Complétalo en «Tipologías» antes de cotizar.", 400)
    subsidio = _positivo(tip.get("monto_subsidio"))
    ahorro = fp._a_numero(c.get("ahorro_uf"))
    ahorro = ahorro if ahorro is not None and ahorro >= 0 else None
    pie = None if ahorro is None else ahorro + (subsidio or 0.0)
    credito = None if pie is None else max(0.0, valor - pie)
    minimo = _positivo(p.get("ahorro_minimo_uf"))
    falta = None if (minimo is None or ahorro is None) else max(0.0, minimo - ahorro)

    avisos: List[str] = []
    if subsidio is None:
        avisos.append("Esta cotización no incluye subsidio: la inmobiliaria no informa un monto para esta tipología.")
    if ahorro is None:
        avisos.append("No hay ahorro registrado para este cliente: no se calculan el pie ni el crédito.")
    if falta:
        avisos.append(f"El proyecto pide un ahorro mínimo de {fmt_uf(minimo)}: faltarían {fmt_uf(falta)}.")

    usuario = usuario or {}
    ufv = None
    if uf and uf.get("valor"):
        ufv = {"valor": float(uf["valor"]), "fecha": str(uf.get("fecha") or "")}
    dorm, banos = tip.get("dormitorios"), tip.get("banos")
    detalle_tip = " · ".join(x for x in (f"{dorm} dormitorio{'s' if str(dorm) != '1' else ''}" if dorm not in (None, "") else "", f"{banos} baño{'s' if str(banos) != '1' else ''}" if banos not in (None, "") else "") if x)
    return {
        "emitida": hoy.isoformat(),
        "cliente": {"nombre": _limpio(c.get("Contacto")) or "Cliente", "rut": _limpio(c.get("Rut"), 20) or None, "correo": _limpio(c.get("Correo") or c.get("email"), 120) or None,
                    "telefono": _limpio(c.get("Telefono"), 30) or None},
        "proyecto": {"nombre": _limpio(p.get("nombre")) or "Proyecto", "ubicacion": _limpio(p.get("ubicacion")) or None,
                     "inmobiliaria": _limpio((p.get("Inmobiliaria") or {}).get("nombre")) or None,
                     "condicion": _CONDICION.get(p.get("condicion_propiedad") or "nueva", "Nueva"), "entrega": fp._entrega(etapa)},
        "tipologia": {"nombre": _limpio(tip.get("nombre")) or "Tipología", "detalle": detalle_tip or None},
        "montos": {"valor_uf": valor, "subsidio_uf": subsidio, "ahorro_uf": ahorro, "pie_uf": pie, "credito_uf": credito, "ahorro_minimo_uf": minimo, "falta_ahorro_uf": falta,
                   "reserva_clp": _positivo(p.get("valor_reserva_clp")), "reserva_uf": _positivo(p.get("valor_reserva_uf")),
                   "estacionamiento_uf": _positivo(p.get("valor_estacionamiento_uf")), "estacionamiento_obligatorio": bool(p.get("estacionamiento_obligatorio"))},
        "uf": ufv,
        "ejecutivo": {"nombre": _limpio(usuario.get("nombre")) or None, "correo": _limpio(usuario.get("email_alias") or usuario.get("correo"), 120) or None,
                      "celular": _limpio(usuario.get("celular"), 30) or None},
        "avisos": avisos,
    }


def filas_resumen(cot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Las líneas del resumen financiero: ``{rotulo, uf, pesos, destacada}`` en el orden en que se muestran."""
    m, ufv = cot["montos"], (cot.get("uf") or {}).get("valor")

    def fila(rotulo: str, valor: Optional[float], destacada: bool = False, signo: str = "") -> Optional[Dict[str, Any]]:
        if valor is None:
            return None
        pesos = clp_aprox(valor, ufv)
        return {"rotulo": rotulo, "uf": signo + fmt_uf(valor), "pesos": signo + fmt_clp(pesos) if pesos is not None else None, "destacada": destacada}
    filas = [fila("Valor de la vivienda", m["valor_uf"]), fila("Subsidio", m["subsidio_uf"], signo="– "), fila("Ahorro del cliente", m["ahorro_uf"], signo="– "),
             fila("Pie (ahorro + subsidio)", m["pie_uf"], destacada=True), fila("Crédito hipotecario estimado", m["credito_uf"], destacada=True)]
    return [f for f in filas if f]


def otros_costos(cot: Dict[str, Any]) -> List[Dict[str, str]]:
    m = cot["montos"]
    filas: List[Dict[str, str]] = []
    if m.get("reserva_clp") or m.get("reserva_uf"):
        filas.append({"rotulo": "Reserva", "valor": fmt_clp(m["reserva_clp"]) if m.get("reserva_clp") else fmt_uf(m["reserva_uf"])})
    if m.get("estacionamiento_uf"):
        filas.append({"rotulo": "Estacionamiento" + (" (obligatorio)" if m.get("estacionamiento_obligatorio") else " (opcional)"), "valor": fmt_uf(m["estacionamiento_uf"])})
    return filas


def asunto(cot: Dict[str, Any]) -> str:
    return _limpio(f"Cotización {cot['proyecto']['nombre']} – {cot['tipologia']['nombre']}", ASUNTO_MAX)


def _entrega_corta(entrega: Optional[str]) -> Optional[str]:
    """«Entrega junio 2027» → «Junio 2027» (en la fila que ya se llama «Entrega»)."""
    if not entrega:
        return None
    t = re.sub(r"^Entrega\s+", "", entrega).strip()
    return t[:1].upper() + t[1:] if t else None


def _linea_vivienda(cot: Dict[str, Any]) -> str:
    t = cot["tipologia"]
    return t["nombre"] + (f" ({t['detalle']})" if t.get("detalle") else "")


# ── Correo ────────────────────────────────────────────────────────────────────

def _h(t: Any) -> str:
    return _xml(str(t), {'"': "&quot;"})


def html_correo(cot: Dict[str, Any]) -> str:
    """Cuerpo del correo (todo dato que viene de fuera se escapa). El detalle va en el PDF adjunto."""
    e, p = cot["ejecutivo"], cot["proyecto"]
    filas = "".join(
        f'<tr style="{"background:#e8f0fe;" if f["destacada"] else ""}"><td style="padding:8px 12px;{"font-weight:bold;color:#1e3a5f;" if f["destacada"] else "color:#555;"}">{_h(f["rotulo"])}</td>'
        f'<td style="padding:8px 12px;text-align:right;{"font-weight:bold;color:#1e3a5f;" if f["destacada"] else ""}">{_h(f["uf"])}'
        f'{"<br><span style=\'font-size:11px;color:#777;font-weight:normal\'>aprox. " + _h(f["pesos"]) + "</span>" if f["pesos"] else ""}</td></tr>'
        for f in filas_resumen(cot))
    extras = "".join(f'<li>{_h(o["rotulo"])}: <b>{_h(o["valor"])}</b></li>' for o in otros_costos(cot))
    avisos = "".join(f"<li>{_h(a)}</li>" for a in cot["avisos"])
    firma = "<br>".join(_h(x) for x in (e.get("nombre"), e.get("celular"), e.get("correo")) if x)
    entrega = f" · {_h(p['entrega'])}" if p.get("entrega") else ""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#222;">
      <h2 style="color:#1e3a5f;margin-bottom:4px;">Cotización de tu vivienda</h2>
      <p style="margin:0 0 14px;">Hola {_h(cot['cliente']['nombre'])}, te enviamos la cotización de <b>{_h(_linea_vivienda(cot))}</b> en <b>{_h(p['nombre'])}</b>{entrega}. El detalle completo va en el PDF adjunto.</p>
      <table style="width:100%;border-collapse:collapse;border:1px solid #e3e8ef;font-size:14px;">{filas}</table>
      {f'<ul style="font-size:13px;color:#444;margin:12px 0 0;padding-left:18px;">{extras}</ul>' if extras else ''}
      {f'<ul style="font-size:12px;color:#8a5a00;margin:12px 0 0;padding-left:18px;">{avisos}</ul>' if avisos else ''}
      <p style="font-size:12px;color:#777;margin:16px 0 0;">{_h(DESCARGO)}</p>
      {f'<p style="margin:18px 0 0;font-size:14px;">Quedo atento/a a tus consultas.<br><br>{firma}</p>' if firma else ''}
    </div>"""


# ── WhatsApp ──────────────────────────────────────────────────────────────────

def texto_whatsapp(cot: Dict[str, Any]) -> str:
    """Texto que acompaña al PDF en WhatsApp (el máximo de WhatsApp para el pie de un documento es 1024 caracteres)."""
    m, e = cot["montos"], cot["ejecutivo"]
    quien = f"Soy {e['nombre']}" if e.get("nombre") else "Te escribo"
    lineas = [f"Hola {cot['cliente']['nombre']} 👋 {quien}. Te envío la cotización de {_linea_vivienda(cot)} en {cot['proyecto']['nombre']}.", "",
              f"• Valor: {fmt_uf(m['valor_uf'])}"]
    if m.get("subsidio_uf") is not None:
        lineas.append(f"• Subsidio: {fmt_uf(m['subsidio_uf'])}")
    if m.get("ahorro_uf") is not None:
        lineas.append(f"• Tu ahorro: {fmt_uf(m['ahorro_uf'])}")
    if m.get("pie_uf") is not None:
        lineas.append(f"• Pie (ahorro + subsidio): {fmt_uf(m['pie_uf'])}")
    if m.get("credito_uf") is not None:
        lineas.append(f"• Crédito estimado: {fmt_uf(m['credito_uf'])}")
    lineas += ["", "Valores referenciales, sujetos a evaluación del banco y a disponibilidad. Cualquier duda, escríbeme por aquí."]
    texto = "\n".join(lineas)
    return texto if len(texto) <= MAX_CAPTION_WHATSAPP else texto[:MAX_CAPTION_WHATSAPP - 1].rstrip() + "…"


# ── PDF ───────────────────────────────────────────────────────────────────────

def generar_pdf(cot: Dict[str, Any]) -> bytes:
    """El PDF de la cotización (una hoja A4 en lo normal). Solo depende del diccionario de ``armar``: sirve igual con la «foto» guardada."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    azul, gris, tenue = colors.HexColor("#1e3a5f"), colors.HexColor("#5b6472"), colors.HexColor("#e8f0fe")
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=13, textColor=colors.HexColor("#222222"))
    chico = ParagraphStyle("chico", parent=base, fontSize=8, leading=10.5, textColor=gris, leftIndent=4)
    titulo = ParagraphStyle("titulo", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=azul)
    seccion = ParagraphStyle("seccion", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=azul, spaceBefore=12, spaceAfter=4, leftIndent=4)
    derecha = ParagraphStyle("derecha", parent=base, alignment=TA_RIGHT)
    negrita = ParagraphStyle("negrita", parent=base, fontName="Helvetica-Bold")

    def P(texto: Any, estilo=base) -> Paragraph:
        return Paragraph(_xml(_latin(str(texto))), estilo)

    def tabla(filas: List[List[Any]], anchos: List[float], extra: Optional[list] = None) -> Table:
        t = Table(filas, colWidths=anchos)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dfe8")),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)] + (extra or [])))
        return t

    c, p, e, m = cot["cliente"], cot["proyecto"], cot["ejecutivo"], cot["montos"]
    ancho = A4[0] - 36 * mm - 12                      # el ancho útil del marco (descontado su margen interno)
    historia: List[Any] = []
    cabecera = Table([[P("Cotización", titulo), P(f"Emitida el {fecha_larga(cot['emitida'])}", derecha)],
                      [P(f"{p['nombre']} · {_linea_vivienda(cot)}", ParagraphStyle("sub", parent=base, fontSize=12, leading=15, textColor=gris)), ""]], colWidths=[ancho * 0.65, ancho * 0.35])
    cabecera.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("SPAN", (0, 1), (1, 1))]))
    historia += [cabecera, Spacer(1, 4)]

    historia.append(P("Cliente", seccion))
    datos_cliente = [[P("Nombre", chico), P(c["nombre"])]] + [[P(r, chico), P(v)] for r, v in (("RUT", c.get("rut")), ("Correo", c.get("correo")), ("Teléfono", c.get("telefono"))) if v]
    historia.append(tabla(datos_cliente, [ancho * 0.25, ancho * 0.75]))

    historia.append(P("Vivienda", seccion))
    datos_viv = [[P("Proyecto", chico), P(p["nombre"])]]
    for r, v in (("Inmobiliaria", p.get("inmobiliaria")), ("Ubicación", p.get("ubicacion")), ("Tipología", _linea_vivienda(cot)), ("Entrega", _entrega_corta(p.get("entrega"))), ("Condición", p.get("condicion"))):
        if v:
            datos_viv.append([P(r, chico), P(v)])
    historia.append(tabla(datos_viv, [ancho * 0.25, ancho * 0.75]))

    historia.append(P("Resumen financiero", seccion))
    filas_fin, estilos = [], []
    for i, f in enumerate(filas_resumen(cot)):
        est = negrita if f["destacada"] else base
        filas_fin.append([P(f["rotulo"], est), P(f["uf"], ParagraphStyle("d", parent=est, alignment=TA_RIGHT)), P(f"aprox. {f['pesos']}" if f["pesos"] else "", ParagraphStyle("p", parent=chico, alignment=TA_RIGHT))])
        if f["destacada"]:
            estilos.append(("BACKGROUND", (0, i), (-1, i), tenue))
    historia.append(tabla(filas_fin, [ancho * 0.48, ancho * 0.26, ancho * 0.26], estilos))
    if (cot.get("uf") or {}).get("valor"):
        fecha_uf = cot["uf"].get("fecha") or ""
        del_dia = f" del {fecha_larga(fecha_uf)}" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha_uf) else ""
        historia.append(P(f"Equivalencias en pesos con la UF{del_dia}: {fmt_clp(cot['uf']['valor'])}.", chico))

    extras = otros_costos(cot)
    if extras:
        historia.append(P("Otros costos informados", seccion))
        historia.append(tabla([[P(x["rotulo"]), P(x["valor"], derecha)] for x in extras], [ancho * 0.6, ancho * 0.4]))
    if cot["avisos"]:
        historia.append(P("A tener en cuenta", seccion))
        historia += [P(f"• {a}", ParagraphStyle("aviso", parent=base, leftIndent=4)) for a in cot["avisos"]]

    contacto = [x for x in (e.get("nombre"), e.get("celular"), e.get("correo")) if x]
    if contacto:
        historia.append(P("Tu ejecutivo/a", seccion))
        historia.append(P(" · ".join(contacto), ParagraphStyle("contacto", parent=base, leftIndent=4)))
    historia += [Spacer(1, 14), P(DESCARGO, chico)]

    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(gris)
        canvas.drawString(18 * mm, 10 * mm, "QueSubsidio · quesubsidio.cl")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Página {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm, title=_latin(asunto(cot)), author="QueSubsidio",
                      subject="Cotización referencial de vivienda").build(historia, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()
