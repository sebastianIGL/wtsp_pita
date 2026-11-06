"""Calculadora de ahorro y pie (``/calculadora``): los datos públicos que necesita el navegador. El cálculo en sí ocurre en la página, en el navegador de la
persona: lo que ella escribe (su ahorro) no viaja al servidor.

* **Subsidios:** las fichas de COMPRA publicadas, completas y al día (las que ya pasaron ``directorio.validar``), con su ahorro mínimo y su precio máximo en UF (cifras
  verificadas, nunca borradores) y la UF de hoy para los equivalentes en pesos.
* **Proyecto:** los datos de un proyecto público que la inmobiliaria carga en el CRM: ahorro/pie que pide, reserva, estacionamiento y, por cada tipología con stock, su
  precio, el subsidio que la inmobiliaria informa y su plan de pago del ahorro (de una vez o en cuotas, con la fecha de entrega). Del stock nunca el número.

Regla del dueño (2026-11-05): con el DS1 Tramo 2 y el Tramo 3, el ahorro mínimo del subsidio cuenta como parte del pie del proyecto y se suma el subsidio. Con cualquier otro
subsidio (DS49, DS1 Tramo 1 y 4, DS19) la página muestra el ahorro del subsidio y el del proyecto por separado, sin sumarlos, hasta que esa regla se confirme.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services import directorio as dr
from app.services import ficha_proyecto as fp
from app.services import uf as servicio_uf

logger = logging.getLogger("wtsp_pita")

SUBSIDIOS_QUE_SUMAN = ("ds1-tramo-2", "ds1-tramo-3")        # decisión del dueño: su ahorro cuenta como parte del pie y se suma el subsidio
TIPOS_CON_SUBSIDIO_DS1 = ("ds1_t23",)                         # «ambos» trae un solo monto que no se sabe de cuál de los dos es: no se usa para sumar
CLAVE_AHORRO, CLAVE_PRECIO_MAXIMO = "ahorro_minimo", "precio_maximo"


def _dato(f: Dict[str, Any], clave: str) -> Optional[Dict[str, Any]]:
    return next((d for d in f["datos"] if d["clave"] == clave), None)


async def subsidios() -> Dict[str, Any]:
    """Subsidios de compra con su ahorro mínimo y precio máximo, y la UF de hoy. Si el directorio no está disponible, lista vacía (la página lo dice)."""
    lista: List[Dict[str, Any]] = []
    try:
        fichas = await dr.fichas_publicas()
    except dr.DirectorioNoDisponible:
        logger.info("Calculadora | directorio no disponible: sin subsidios")
        fichas = {}
    for f in sorted(fichas.values(), key=lambda x: (x["orden"], x["nombre"], x["slug"])):
        if f["vencida"] or f["alcance"] != "completa" or f["grupo_clave"] != "comprar":
            continue
        ahorro, precio = _dato(f, CLAVE_AHORRO), _dato(f, CLAVE_PRECIO_MAXIMO)
        if not ahorro:
            continue
        lista.append({
            "slug": f["slug"], "ruta": f["ruta"], "nombre": f["nombre"], "revisado": f["revisado"],
            "ahorro_uf": ahorro["numero"], "ahorro_texto": ahorro["valor"], "llamado": ahorro["llamado"],
            "precio_maximo_uf": precio["numero"] if precio else None,
            "suma": f["slug"] in SUBSIDIOS_QUE_SUMAN,
        })
    uf = None
    try:
        hoy = await servicio_uf.uf_del_dia()
        if hoy:
            uf = {"valor": hoy[0], "fecha": hoy[1].isoformat()}
    except Exception:
        logger.exception("Calculadora | no se pudo leer la UF")
    return {"uf": uf, "subsidios": lista}


def _positivo(v: Any) -> Optional[float]:
    n = fp._a_numero(v)
    return n if n is not None and n > 0 else None


def _etapa(e: Dict[str, Any]) -> Dict[str, Any]:
    inmediata = e.get("estado") == "entrega_inmediata"
    cuotas = _positivo(e.get("num_cuotas"))
    fecha = str(e.get("fecha_entrega") or "").strip()[:10]
    return {
        "entrega": "inmediata" if inmediata else ("futura" if e.get("estado") == "entrega_futura" else None),
        "texto": fp._entrega(e),
        "fecha": fecha if len(fecha) == 10 and fecha[4] == "-" and fecha[7] == "-" else None,
        "modo": "unico" if inmediata else ("cuotas" if e.get("tipo_ahorro") == "cuotas" and cuotas else ("unico" if e.get("tipo_ahorro") == "unico" else None)),
        "cuotas": int(cuotas) if cuotas and not inmediata and e.get("tipo_ahorro") == "cuotas" else None,
    }


def armar_proyecto(p: Dict[str, Any], slug: str) -> Dict[str, Any]:
    tipologias: List[Dict[str, Any]] = []
    for t in p.get("Tipologia") or []:
        if (t.get("estado") or "activo") != "activo":
            continue
        con_stock = [e for e in (t.get("EtapaTipologia") or []) if (fp._a_numero(e.get("stock")) or 0) > 0]
        if not con_stock:
            continue
        etapas: List[Dict[str, Any]] = []
        for e in con_stock:
            x = _etapa(e.get("Etapa") or {})
            if x not in etapas:
                etapas.append(x)
        monto = _positivo(t.get("monto_subsidio"))
        tipo = t.get("tipo_subsidio") if t.get("tipo_subsidio") in ("ds19", "ds1_t23", "ambos") else None
        tipologias.append({
            "nombre": str(t.get("nombre") or "Tipología"), "dormitorios": t.get("dormitorios"), "banos": t.get("banos"),
            "valor_uf": _positivo(t.get("valor_uf")),
            "subsidio": {"tipo": tipo, "monto_uf": monto, "para_ds1": tipo in TIPOS_CON_SUBSIDIO_DS1} if monto and tipo else None,
            "etapas": etapas,
        })
    tipologias.sort(key=lambda t: (t["valor_uf"] is None, t["valor_uf"] or 0, t["nombre"]))
    return {
        "slug": slug, "nombre": str(p.get("nombre") or "Proyecto"), "ubicacion": str(p.get("ubicacion") or ""), "ruta": f"/viviendas/{slug}",
        "acepta_ds19": bool(p.get("acepta_ds19")), "acepta_ds1_t23": bool(p.get("acepta_ds1_t23")),
        "ahorro_pie_uf": _positivo(p.get("ahorro_minimo_uf")),
        "reserva_clp": _positivo(p.get("valor_reserva_clp")), "reserva_uf": _positivo(p.get("valor_reserva_uf")),
        "estacionamiento_uf": _positivo(p.get("valor_estacionamiento_uf")), "estacionamiento_obligatorio": bool(p.get("estacionamiento_obligatorio")),
        "tipologias": tipologias,
    }


async def proyecto(slug: str) -> Optional[Dict[str, Any]]:
    """Los datos de la calculadora de un proyecto público, o None si el slug no existe, el proyecto no está activo o está entre los ocultos del sitio."""
    if len(slug) > fp.SLUG_MAX or not fp.SLUG_VALIDO.match(slug):
        return None
    entrada = (await fp._indice_fichas()).get(slug)
    if not entrada:
        return None
    fila = await fp._fila(entrada["id"])
    if not fila or not fp.es_publico(fila):
        return None
    return armar_proyecto(fila, slug)
