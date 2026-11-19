"""Descargar el listado de clientes (Excel o CSV) para usarlo en otra aplicación, por ejemplo una de remarketing por WhatsApp.

Lo que sale es lo mismo que la persona ve en el CRM: un ejecutivo solo recibe sus propios clientes; administración, todos los que pida. Cada cliente trae el teléfono tal como está y el «Teléfono WhatsApp» (solo dígitos y
con el 56 de Chile), listo para pegar. **Por defecto no se incluye a quien pidió la baja de WhatsApp** (``Prospecto.opt_out``): mandarle mensajes sería faltar a su pedido; quien descarga puede incluirlos de forma explícita.

Seguridad: en el CSV, un texto que empieza con ``=``, ``+``, ``-``, ``@`` (o tabulación) se escribe con una comilla delante para que el programa de planillas no lo ejecute como fórmula; el Excel guarda todo texto como texto.
El Excel se arma a mano (un .xlsx es un zip de XML), sin librerías nuevas.
"""
import asyncio
import csv
import io
import logging
import re
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as _xml
from zoneinfo import ZoneInfo

from app.clients.supabase import _supabase_request
from app.core.permisos import _solo_admin
from app.core.utilidades import _normalize_phone
from app.services.clientes_vistas import enriquecer_clientes, leer_en_paginas

logger = logging.getLogger("wtsp_pita")

MAX_CLIENTES = 10000
LOTE_IDS = 200
LOTE_TELEFONOS = 250
FORMATOS = ("xlsx", "csv")
TZ = ZoneInfo("America/Santiago")

ESTADOS = {"": "Sin estado", "sin_contactar": "Sin contactar", "primer_llamado": "Primer llamado", "primer_wtsp": "Primer WA", "interesado": "Interesado", "sin_rsh": "Sin RSH", "creando_rsh": "Creando RSH",
           "enviando_docs": "Enviando docs", "por_evaluar": "Enviar a evaluar", "en_evaluacion": "En evaluación", "aprobado": "Aprobado", "cotizacion_enviada": "Cotización enviada",
           "no_interesado": "No interesado", "no_califica": "No califica", "cierre_perdido": "Cierre perdido", "descartado": "Rechazado"}
FUENTES = {"meta_ads": "Meta Ads", "chatbot": "Chatbot", "formulario_web": "Formulario web", "referido": "Referido", "excel": "Excel", "manual": "Manual", "otro": "Otro"}

# (encabezado, es número). El orden es el de las columnas del archivo.
COLUMNAS: List[Tuple[str, bool]] = [("Nombre", False), ("Teléfono", False), ("Teléfono WhatsApp", False), ("Correo", False), ("RUT", False), ("Proyecto", False), ("Tipología", False), ("Estado de gestión", False),
                                    ("Ejecutivo", False), ("Fuente", False), ("Fecha de llegada", False), ("Temperatura", True), ("Ahorro (UF)", True), ("Próxima acción (fecha)", False)]
COLUMNA_BAJA = ("Pidió no recibir WhatsApp", False)


def ids_validos(crudo: Any) -> List[int]:
    """Los ids pedidos, sin repetir y en el orden en que llegaron. Lanza ``ValueError`` con el mensaje para la persona si algo no sirve."""
    if not isinstance(crudo, list) or not crudo:
        raise ValueError("Elige a quiénes descargar.")
    if len(crudo) > MAX_CLIENTES:
        raise ValueError(f"Se pueden descargar hasta {MAX_CLIENTES:,} clientes a la vez. Filtra la lista e inténtalo de nuevo.".replace(",", "."))
    vistos: Dict[int, None] = {}
    for v in crudo:
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError("La lista de clientes no es válida.")
        vistos.setdefault(v, None)
    return list(vistos)


def _fecha(v: Any) -> str:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return (t.astimezone(TZ) if t.tzinfo else t).strftime("%d-%m-%Y")


def _fecha_sin_zona(v: Any) -> str:
    """La fecha del recordatorio se guarda como «AAAA-MM-DD…» y es la que escribió la persona: no se mueve de zona."""
    t = str(v or "")[:10]
    return f"{t[8:10]}-{t[5:7]}-{t[0:4]}" if len(t) == 10 and t[4] == "-" and t[7] == "-" else ""


def _numero(v: Any) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def _telefono_wsp(tel: Any) -> str:
    d = _normalize_phone(str(tel or ""))
    return d if 10 <= len(d) <= 15 else ""


async def _leer_clientes(perfil: Dict[str, Any], ids: List[int]) -> List[Dict[str, Any]]:
    cupo = asyncio.Semaphore(6)

    async def uno(lote: List[int]):
        async with cupo:
            params = {"select": "*", "id": f"in.({','.join(str(i) for i in lote)})"}
            if not _solo_admin(perfil):
                params["usuario_id"] = f"eq.{perfil['id']}"
            return await leer_en_paginas("/Cliente", params, maximo=LOTE_IDS + 1)
    lotes = await asyncio.gather(*[uno(ids[i:i + LOTE_IDS]) for i in range(0, len(ids), LOTE_IDS)])
    por_id = {int(c["id"]): c for lote in lotes for c in lote if c.get("id") is not None}
    return [por_id[i] for i in ids if i in por_id]                         # en el orden pedido; lo que la persona no puede ver no aparece


async def _bajas(clientes: List[Dict[str, Any]]) -> Dict[str, bool]:
    """``{teléfono_e164: pidió la baja}`` para los teléfonos de estos clientes."""
    tels = sorted({_normalize_phone(c.get("Telefono") or "") for c in clientes if c.get("Telefono")} - {""})
    cupo = asyncio.Semaphore(6)

    async def uno(lote: List[str]):
        async with cupo:
            return await _supabase_request("GET", "/Prospecto", params={"telefono_e164": f"in.({','.join(lote)})", "select": "telefono_e164,opt_out"}) or []
    salida: Dict[str, bool] = {}
    for lote in await asyncio.gather(*[uno(tels[i:i + LOTE_TELEFONOS]) for i in range(0, len(tels), LOTE_TELEFONOS)]):
        for p in lote:
            if p.get("opt_out"):
                salida[str(p.get("telefono_e164"))] = True
    return salida


async def _nombres(ruta: str, clave: str = "id") -> Dict[str, str]:
    try:
        filas = await leer_en_paginas(ruta, {"select": f"{clave},nombre", "order": f"{clave}.asc"}, maximo=20000)
        return {str(f.get(clave)): str(f.get("nombre") or "") for f in filas}
    except Exception as e:
        logger.warning("Exportar clientes | no se pudieron leer los nombres de %s (%s)", ruta, type(e).__name__)
        return {}


async def construir(perfil: Dict[str, Any], ids: List[int], incluir_baja: bool = False) -> Dict[str, Any]:
    """``{"encabezados", "filas", "exportados", "excluidos"}``. Las filas son listas de texto o número en el orden de ``COLUMNAS``."""
    clientes = await _leer_clientes(perfil, ids)
    bajas = await _bajas(clientes) if clientes else {}
    visibles = clientes if incluir_baja else [c for c in clientes if not bajas.get(_normalize_phone(c.get("Telefono") or ""))]
    await enriquecer_clientes(visibles)
    proyectos, usuarios = await asyncio.gather(_nombres("/Proyecto"), _nombres("/Usuario"))
    columnas = COLUMNAS + ([COLUMNA_BAJA] if incluir_baja else [])
    filas: List[List[Any]] = []
    for c in visibles:
        fila: List[Any] = [
            (c.get("Contacto") or "").strip(), str(c.get("Telefono") or "").strip(), _telefono_wsp(c.get("Telefono")), (c.get("Correo") or c.get("email") or "").strip(), (c.get("Rut") or "").strip(),
            proyectos.get(str(c.get("proyecto_id")), ""), c.get("tipologia_nombre") or "", ESTADOS.get(c.get("estado_gestion") or "", ESTADOS[""]), usuarios.get(str(c.get("usuario_id")), "") if c.get("usuario_id") else "",
            FUENTES.get(c.get("fuente") or "", ""), _fecha(c.get("creado_en")), _numero(c.get("temperatura")), _numero(c.get("ahorro_uf")), _fecha_sin_zona(c.get("recordatorio_at"))]
        if incluir_baja:
            fila.append("Sí" if bajas.get(_normalize_phone(c.get("Telefono") or "")) else "No")
        filas.append(fila)
    return {"encabezados": [h for h, _ in columnas], "numericas": [n for _, n in columnas], "filas": filas, "exportados": len(filas), "excluidos": len(clientes) - len(visibles)}


# ── Archivos ──────────────────────────────────────────────────────────────────────────

def _seguro_csv(v: Any) -> Any:
    if isinstance(v, str) and v and (v[0] in "=+-@\t\r"):
        return "'" + v
    return v


_SOLO_TELEFONO = re.compile(r"^\+?[\d ()-]{6,20}$")


def a_csv(datos: Dict[str, Any]) -> bytes:
    """CSV en UTF-8 con marca de orden (BOM) para que Excel respete las tildes. La columna «Teléfono» deja pasar un teléfono tal cual («+56 9 1111 2222»): solo tiene dígitos y signos de teléfono, no puede ser una fórmula peligrosa."""
    sal = io.StringIO()
    w = csv.writer(sal, lineterminator="\r\n")
    w.writerow(datos["encabezados"])
    col_tel = datos["encabezados"].index("Teléfono") if "Teléfono" in datos["encabezados"] else -1
    for f in datos["filas"]:
        w.writerow(["" if v is None else (v if (i == col_tel and isinstance(v, str) and _SOLO_TELEFONO.match(v)) else _seguro_csv(v)) for i, v in enumerate(f)])
    return ("﻿" + sal.getvalue()).encode("utf-8")


def _letra(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _texto_xml(v: Any) -> str:
    """Sin caracteres de control (un .xlsx con uno no abre) y con lo que XML exige escapado."""
    limpio = "".join(ch for ch in str(v) if ch in "\t\n\r" or ord(ch) >= 32)
    return _xml(limpio)


def a_xlsx(datos: Dict[str, Any]) -> bytes:
    """Un libro de Excel de una hoja («Clientes») con el encabezado en negrita y fijo. Todo texto va como texto; los números, como números."""
    numericas = datos.get("numericas") or [False] * len(datos["encabezados"])
    filas_xml = []

    def celda(col: int, fila: int, v: Any, estilo: int = 0) -> str:
        ref = f"{_letra(col)}{fila}"
        if v is None or v == "":
            return ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<c r="{ref}"{f" s=\"{estilo}\"" if estilo else ""}><v>{repr(float(v)) if isinstance(v, float) and not float(v).is_integer() else int(v)}</v></c>'
        return f'<c r="{ref}" t="inlineStr"{f" s=\"{estilo}\"" if estilo else ""}><is><t xml:space="preserve">{_texto_xml(v)}</t></is></c>'
    filas_xml.append('<row r="1">' + "".join(celda(i, 1, h, 1) for i, h in enumerate(datos["encabezados"])) + "</row>")
    for n, f in enumerate(datos["filas"], start=2):
        filas_xml.append(f'<row r="{n}">' + "".join(celda(i, n, v if (not numericas[i] or isinstance(v, (int, float))) else str(v)) for i, v in enumerate(f)) + "</row>")
    anchos = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{ancho}" customWidth="1"/>' for i, ancho in enumerate([26, 16, 18, 28, 14, 26, 16, 20, 22, 14, 16, 12, 12, 20, 24][:len(datos["encabezados"])]))
    hoja = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            f'<cols>{anchos}</cols><sheetData>{"".join(filas_xml)}</sheetData></worksheet>')
    estilos = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
               '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
               '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
               '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
               '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
               '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    partes = {
        "[Content_Types].xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'),
        "_rels/.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Clientes" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                                       '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'),
        "xl/styles.xml": estilos, "xl/worksheets/sheet1.xml": hoja}
    sal = io.BytesIO()
    with zipfile.ZipFile(sal, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, contenido in partes.items():
            z.writestr(nombre, contenido)
    return sal.getvalue()


def nombre_archivo(formato: str, hoy: Optional[datetime] = None) -> str:
    return f"clientes_{(hoy or datetime.now(TZ)).strftime('%Y-%m-%d')}.{formato}"
