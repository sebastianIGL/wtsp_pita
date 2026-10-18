"""Reglas de documentos: qué documentos se piden según la calificación del cliente, cómo se clasifica un archivo
recibido y qué falta por entregar.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote A). Funciones puras, sin red ni estado.
``_CAMPOS_CALIFICACION`` (las preguntas de calificación) vive aquí porque de ellas depende qué documentos se piden;
también lo usan el bot y las rutas.
"""
import re
import unicodedata
from typing import Dict, List, Optional


# Campos de calificación que viven en columnas boolean propias (no en el blob datos)
_CAMPOS_CALIFICACION = [
    "tiene_rsh", "tiene_propiedad", "subsidio_previo",
    "ahorro_ok", "trabajo_indefinido", "complemento_renta",
]


# Documentos base (siempre requeridos)
_DOCS_BASE: Dict[str, Dict] = {
    "carnet_identidad": {"label": "Cédula de identidad",     "cantidad": 2},
    "certificado_afp":  {"label": "Certificado de AFP",      "cantidad": 1},
    "informe_deudas":   {"label": "Informe de deudas CMF",   "cantidad": 1},
}


# Documentos condicionales: (tipo, config, función_condición)
_DOCS_CONDICIONALES: List[tuple] = [
    ("certificado_rsh",              {"label": "Certificado RSH",                  "cantidad": 1}, lambda d: d.get("tiene_rsh") is True),
    ("liquidacion_sueldo",           {"label": "Liquidaciones de sueldo",          "cantidad": 6}, lambda d: d.get("trabajo_indefinido") is True),
    ("antiguedad_laboral",           {"label": "Certificado de antigüedad",        "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is True),
    ("carpeta_tributaria_sii",       {"label": "Carpeta tributaria SII",           "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is False),
    ("declaracion_anual_impuestos",  {"label": "Declaración anual de impuestos",   "cantidad": 1}, lambda d: d.get("trabajo_indefinido") is False),
    ("cartola_ahorro",               {"label": "Cartola de ahorro",                "cantidad": 1}, lambda d: d.get("ahorro_ok") is True),
    ("cedula_complementador",        {"label": "Cédula del complementador",        "cantidad": 2}, lambda d: d.get("complemento_renta") is True),
    ("liquidaciones_complementador", {"label": "Liquidaciones del complementador", "cantidad": 6}, lambda d: d.get("complemento_renta") is True),
    ("certificado_afp_complementador",    {"label": "Certificado de AFP del complementador",        "cantidad": 1}, lambda d: d.get("complemento_renta") is True),
    ("antiguedad_laboral_complementador", {"label": "Certificado de antigüedad del complementador", "cantidad": 1}, lambda d: d.get("complemento_renta") is True),
]


def _docs_requeridos(datos: Dict) -> Dict[str, Dict]:
    """Retorna el dict de documentos aplicables según la calificación del cliente."""
    result = dict(_DOCS_BASE)
    for tipo, cfg, condicion in _DOCS_CONDICIONALES:
        if condicion(datos):
            result[tipo] = cfg
    return result


# Palabras clave para clasificar documentos por nombre de archivo
_KEYWORDS_TIPO: List[tuple] = [
    ("liquidacion_sueldo", ["liquidacion", "liquidación", "sueldo", "remuneracion", "remuneración", "renta"]),
    ("carnet_identidad",   ["carnet", "cedula", "cédula", "ci_", "dni", "identidad", "rut"]),
    ("antiguedad_laboral", ["antiguedad", "antigüedad", "contrato", "laboral", "empleador"]),
    ("certificado_afp",    ["afp", "prevision", "previsión", "pension", "pensión", "retiro"]),
    ("libreta_ahorro",     ["libreta", "ahorro", "cartola", "cuenta_vista", "cuenta_rut", "cuentarut", "saldo"]),
    ("informe_deudas",     ["informe_de_deudas", "informe_deuda", "deudas", "dicom", "clave_unica", "claveunica"]),
    ("certificado_rsh",    ["rsh", "registro_social", "registro social", "hogares", "cas", "ficha_social", "ficha social"]),
]


# El clasificador y la lista de documentos requeridos nacieron por separado y usan
# nombres distintos para el mismo documento real (cartola/libreta de ahorro).
_TIPO_ALIAS: Dict[str, str] = {"libreta_ahorro": "cartola_ahorro"}


def clasificar_documento(nombre_archivo: str, mime_type: str = "") -> str:
    """Clasifica el tipo de documento según el nombre del archivo."""
    nombre_lower = (nombre_archivo or "").lower().replace(" ", "_").replace("-", "_")
    for tipo, keywords in _KEYWORDS_TIPO:
        if any(kw in nombre_lower for kw in keywords):
            return tipo
    return "otro"


def documentos_pendientes(docs_recibidos: List[Dict], datos: Optional[Dict] = None) -> Dict[str, int]:
    """Retorna cuántos documentos faltan por tipo según la calificación del cliente."""
    requeridos = _docs_requeridos(datos or {})
    conteo: Dict[str, int] = {t: 0 for t in requeridos}
    for doc in docs_recibidos:
        tipo = _TIPO_ALIAS.get(doc.get("tipo", ""), doc.get("tipo", ""))
        if tipo in conteo:
            conteo[tipo] += 1
    pendientes = {}
    for tipo, cfg in requeridos.items():
        faltantes = cfg["cantidad"] - conteo.get(tipo, 0)
        if faltantes > 0:
            pendientes[tipo] = faltantes
    return pendientes


def _lista_documentos_requeridos_texto(datos: Optional[Dict] = None) -> str:
    """Lista fija de documentos (nombre y cantidad exactos según _DOCS_BASE/_DOCS_CONDICIONALES,
    la misma fuente de verdad que valida los uploads) para que el bot la inserte tal cual —
    sin parafrasear, inventar cantidades ni agregar documentos que no se piden realmente."""
    requeridos = _docs_requeridos(datos or {})
    lineas = []
    for cfg in requeridos.values():
        n = cfg["cantidad"]
        sufijo = f"{n} archivos" if n > 1 else "1 archivo"
        lineas.append(f"▸ {cfg['label']} ({sufijo})")
    return "\n".join(lineas)


def resumen_documentos(docs_recibidos: List[Dict], datos: Optional[Dict] = None) -> str:
    """Genera 'Recibidos: .../Pendientes: ...' para inyectar en el prompt ESPERA_DOCS."""
    requeridos = _docs_requeridos(datos or {})
    conteo: Dict[str, int] = {t: 0 for t in requeridos}
    for doc in docs_recibidos:
        tipo = _TIPO_ALIAS.get(doc.get("tipo"), doc.get("tipo"))
        if tipo in conteo:
            conteo[tipo] += 1
    recibidos_labels  = [cfg["label"] for tipo, cfg in requeridos.items() if conteo.get(tipo, 0) >= cfg["cantidad"]]
    pendientes_labels = [cfg["label"] for tipo, cfg in requeridos.items() if conteo.get(tipo, 0) <  cfg["cantidad"]]
    rec = ", ".join(recibidos_labels)  if recibidos_labels  else "(ninguno)"
    pen = ", ".join(pendientes_labels) if pendientes_labels else "(ninguno)"
    return f"Recibidos: {rec}\nPendientes: {pen}"


# Palabras con las que el cliente nombra un documento en un mensaje de texto. Más estricto que _KEYWORDS_TIPO (que mira
# nombres de archivo): en una conversación "casa", "renta" o "ahorro" aparecen sin referirse a un documento.
# Cada alternativa es un conjunto de palabras que deben estar todas.
_PALABRAS_TEXTO_TIPO: List[tuple] = [
    ("carnet_identidad",   [{"carnet"}, {"cedula"}, {"identidad"}, {"ci"}]),
    ("libreta_ahorro",     [{"libreta"}, {"cartola"}]),
    ("certificado_afp",    [{"afp"}]),
    ("informe_deudas",     [{"cmf"}, {"dicom"}, {"informe", "deudas"}]),
    ("certificado_rsh",    [{"rsh"}, {"registro", "social"}]),
    ("liquidacion_sueldo", [{"liquidacion"}, {"liquidaciones"}]),
    ("antiguedad_laboral", [{"antiguedad"}]),
]
_MAX_PALABRAS_TEXTO_DOC = 12


def clasificar_texto_documento(texto: str) -> Optional[str]:
    """Tipo de documento que nombra un mensaje corto del cliente ("mi libreta de ahorro"), o None si no nombra
    ninguno o nombra varios (ambiguo)."""
    sin_tildes = unicodedata.normalize("NFD", (texto or "").lower())
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")
    palabras = re.findall(r"[a-z0-9]+", sin_tildes)
    if not palabras or len(palabras) > _MAX_PALABRAS_TEXTO_DOC:
        return None
    conjunto = set(palabras)
    tipos = [tipo for tipo, alternativas in _PALABRAS_TEXTO_TIPO if any(a <= conjunto for a in alternativas)]
    return tipos[0] if len(tipos) == 1 else None
