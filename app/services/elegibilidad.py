"""«A qué subsidio puedes acceder» (Mi Camino): compara las respuestas de una persona con las reglas de cada subsidio y dice, para cada uno, si **puede**, **podría** (y qué le falta) o **por ahora no** (y por qué).

Es orientativo: no es una postulación ni garantiza nada; cada llamado tiene sus reglas y la decisión es del Minvu. Función pura, sin guardar nada (las respuestas no se almacenan ni se registran).

Las reglas viven aquí (tramo máximo del RSH, ahorro, qué vivienda se tiene) y se **comparan con la ficha publicada** de cada subsidio: si el ahorro o el tope del RSH de la ficha ya no coinciden con la regla (la ficha se
actualizó y esta regla quedó vieja), ese subsidio **no recibe veredicto** hasta que se revise (``sin_verificar``). Las fichas vencidas o en borrador ni siquiera llegan aquí (``mi_camino._subsidios``).

Respuestas (``Respuestas``):
  objetivo          comprar | construir | arrendar | mejorar | rural
  adulto            True si tiene 18 años o más y cédula vigente (o residencia definitiva)
  rsh               tramo del Registro Social de Hogares: 40, 50, 60, 70, 80, 90 o 100 (``None`` = no lo sabe / no tiene)
  vivienda          sin (ni vivienda ni sitio) | sitio (tiene un sitio, sin vivienda) | propia (tiene vivienda)
  subsidio_previo   True / False / None (no sabe)
  ahorro_uf         ahorro en la cuenta para la vivienda, en UF (``None`` = no tiene)
"""
import re
from typing import Any, Dict, List, Optional, Tuple

OBJETIVOS = ("comprar", "construir", "arrendar", "mejorar", "rural")
TRAMOS_RSH = (40, 50, 60, 70, 80, 90, 100)
VIVIENDAS = ("sin", "sitio", "propia")

# slug de la ficha → regla. ``rsh_max``: tramo máximo (None = basta con tener RSH). ``topes``: sobre el tramo máximo (o siempre, si no hay máximo) hay topes de ingreso que la persona debe revisar.
# ``rsh_individual``: tramo hasta el que se acepta si postula de forma individual. ``ahorro_uf``: ahorro mínimo, o ``ahorro_por_tramo``: lista de (hasta qué tramo del RSH, ahorro). ``vivienda``: qué debe tener (sin / sitio / propia).
REGLAS: Dict[str, Dict[str, Any]] = {
    "ds49-compra":                    {"objetivo": "comprar",   "vivienda": "sin",    "rsh_max": 40,   "ahorro_uf": 10},
    "ds1-tramo-1":                    {"objetivo": "comprar",   "vivienda": "sin",    "rsh_max": 60,   "ahorro_uf": 30},
    "ds1-tramo-2":                    {"objetivo": "comprar",   "vivienda": "sin",    "rsh_max": 80,   "ahorro_uf": 40},
    "ds1-tramo-3":                    {"objetivo": "comprar",   "vivienda": "sin",    "rsh_max": 90,   "ahorro_uf": 80, "topes": True},
    "ds1-tramo-4":                    {"objetivo": "comprar",   "vivienda": "sin",    "rsh_max": None, "ahorro_uf": 200, "topes": True},
    "ds19-integracion-social":        {"objetivo": "comprar",   "vivienda": "sin",    "rsh_max": None, "ahorro_por_tramo": [(80, 40), (100, 80)]},
    "ds52-arriendo":                  {"objetivo": "arrendar",  "vivienda": "sin",    "rsh_max": 70,   "ahorro_uf": 4},
    "ds49-construccion-sitio-propio": {"objetivo": "construir", "vivienda": "sitio",  "rsh_max": 40,   "ahorro_uf": 10, "rsh_individual": 50},
    "ds1-construccion-tramo-2":       {"objetivo": "construir", "vivienda": "sitio",  "rsh_max": 80,   "ahorro_uf": 30},
    "ds1-construccion-tramo-3":       {"objetivo": "construir", "vivienda": "sitio",  "rsh_max": 90,   "ahorro_uf": 50, "topes": True},
    "ds27-mejoramiento-vivienda":     {"objetivo": "mejorar",   "vivienda": "propia", "rsh_max": None, "ahorro_uf": 3, "sin_rsh": True},
    "ds10-rural-sitio-residente":     {"objetivo": "rural",     "vivienda": "sitio",  "rsh_max": None, "ahorro_por_tramo": [(40, 10), (60, 30), (100, 50)]},
}

AVISO = ("Es una orientación según lo que nos contaste: no es una postulación ni asegura que recibas el subsidio. Cada llamado tiene sus propias reglas y la decisión final es del Minvu "
         "(y del banco, si pides un crédito). Confirma los requisitos en las fuentes oficiales.")

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _primer_numero(texto: Any) -> Optional[float]:
    m = _NUM.search(str(texto or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def _tope_rsh_de_la_ficha(texto: Any) -> Optional[float]:
    m = re.search(r"(?:hasta el|superas el) (\d+)\s*%", str(texto or ""), re.IGNORECASE)       # «Hasta el 60% del RSH» / «con topes de ingreso si superas el 90%»
    return float(m.group(1)) if m else None


def regla_al_dia(regla: Dict[str, Any], ficha: Dict[str, Any]) -> bool:
    """True si el ahorro y el tope del RSH de la ficha siguen coincidiendo con la regla (candado contra reglas viejas)."""
    ahorro = (ficha.get("ahorro") or {}).get("valor") if isinstance(ficha.get("ahorro"), dict) else None
    rsh = (ficha.get("rsh") or {}).get("valor") if isinstance(ficha.get("rsh"), dict) else None
    if "ahorro_uf" in regla and _primer_numero(ahorro) != float(regla["ahorro_uf"]):
        return False
    if "ahorro_por_tramo" in regla and not ahorro:
        return False
    tope = _tope_rsh_de_la_ficha(rsh)
    if regla.get("rsh_max") is not None and tope != float(regla["rsh_max"]):
        return False
    return True


def validar(r: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Limpia las respuestas. Devuelve (respuestas, None) o (None, motivo del rechazo)."""
    if not isinstance(r, dict):
        return None, "Faltan las respuestas."
    objetivo = r.get("objetivo")
    if objetivo not in OBJETIVOS:
        return None, "Elige qué quieres hacer."
    vivienda = r.get("vivienda")
    if vivienda not in VIVIENDAS:
        return None, "Cuéntanos qué tienes hoy (vivienda o sitio)."
    adulto = r.get("adulto")
    if not isinstance(adulto, bool):
        return None, "Cuéntanos si tienes 18 años o más y cédula vigente."
    rsh = r.get("rsh")
    if rsh is not None and (isinstance(rsh, bool) or rsh not in TRAMOS_RSH):
        return None, "Elige un tramo del Registro Social de Hogares de la lista."
    previo = r.get("subsidio_previo")
    if previo is not None and not isinstance(previo, bool):
        return None, "Cuéntanos si ya usaste un subsidio."
    ahorro = r.get("ahorro_uf")
    if ahorro is not None:
        if isinstance(ahorro, bool) or not isinstance(ahorro, (int, float)) or ahorro != ahorro or ahorro < 0 or ahorro > 100000:
            return None, "El ahorro debe ser un número válido."
    return {"objetivo": objetivo, "vivienda": vivienda, "adulto": adulto, "rsh": rsh, "subsidio_previo": previo, "ahorro_uf": float(ahorro or 0)}, None


def _ahorro_necesario(regla: Dict[str, Any], rsh: Optional[int]) -> Optional[float]:
    if "ahorro_uf" in regla:
        return float(regla["ahorro_uf"])
    tramos = regla.get("ahorro_por_tramo") or []
    if not tramos:
        return None
    if rsh is None:
        return float(tramos[0][1])                      # sin tramo conocido se muestra el mínimo posible
    return float(next((a for hasta, a in tramos if rsh <= hasta), tramos[-1][1]))


def _uf(n: float) -> str:
    return (f"{n:.1f}".rstrip("0").rstrip(".")).replace(".", ",")


def evaluar_uno(regla: Dict[str, Any], r: Dict[str, Any]) -> Dict[str, Any]:
    """``{"estado": puede|podrias|no, "bien": [...], "faltan": [...], "razones": [...]}``."""
    bien: List[str] = []
    faltan: List[str] = []
    razones: List[str] = []
    if not r["adulto"]:
        razones.append("Hay que tener 18 años o más y cédula vigente (o residencia definitiva).")
    else:
        bien.append("Cumples la edad y el documento.")
    esperado, tiene = regla["vivienda"], r["vivienda"]
    if esperado != tiene:
        razones.append({"sin": "Es para quien no tiene vivienda ni sitio propio.",
                        "sitio": "Es para quien ya tiene su sitio (terreno) y no tiene vivienda.",
                        "propia": "Es para quien ya tiene su vivienda."}[esperado])
    else:
        bien.append("Tu situación de vivienda calza.")
    rsh, tope = r["rsh"], regla.get("rsh_max")
    if regla.get("sin_rsh"):
        pass
    elif rsh is None:
        faltan.append("Consulta tu tramo del Registro Social de Hogares (lo necesitas para saber si calificas).")
    elif tope is not None and rsh > tope:
        if regla.get("rsh_individual") and rsh <= regla["rsh_individual"]:
            faltan.append(f"Tu tramo ({rsh}%) supera el {tope}%: solo calificas si postulas de forma individual (hasta el {regla['rsh_individual']}%).")
        elif regla.get("topes"):
            faltan.append(f"Tu tramo ({rsh}%) supera el {tope}%: pasarías solo si tus ingresos están dentro de los topes del llamado. Revísalo en la ficha.")
        else:
            razones.append(f"Tu tramo del RSH ({rsh}%) supera el máximo de este subsidio ({tope}%).")
    elif tope is None and regla.get("topes"):
        faltan.append("Este subsidio tiene topes de ingreso: revísalos en la ficha antes de contar con él.")
    else:
        bien.append("Tu tramo del RSH alcanza.")
    if r["subsidio_previo"] is True:
        faltan.append("Ya usaste un subsidio: según el programa, eso puede impedir postular. Consúltalo en minvu.gob.cl.")
    elif r["subsidio_previo"] is None:
        faltan.append("Confirma si alguna vez usaste un subsidio (según el programa, puede impedir postular).")
    necesario = _ahorro_necesario(regla, rsh)
    if necesario is not None:
        if r["ahorro_uf"] >= necesario:
            bien.append(f"Tu ahorro alcanza el mínimo ({_uf(necesario)} UF).")
        else:
            faltan.append(f"Te faltan {_uf(necesario - r['ahorro_uf'])} UF de ahorro (el mínimo es {_uf(necesario)} UF).")
    estado = "no" if razones else ("podrias" if faltan else "puede")
    return {"estado": estado, "bien": bien, "faltan": faltan, "razones": razones}


_ORDEN = {"puede": 0, "podrias": 1, "no": 2}


def evaluar(respuestas: Any, fichas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """``fichas``: las de ``mi_camino._subsidios()`` (ya publicadas, verificadas y al día). Devuelve ``{"resultados": [...], "sin_verificar": n, "aviso": ...}`` o ``{"error": motivo}``."""
    r, error = validar(respuestas)
    if error:
        return {"error": error}
    resultados: List[Dict[str, Any]] = []
    sin_verificar = 0
    for f in fichas:
        regla = REGLAS.get(f.get("slug"))
        if not regla or regla["objetivo"] != r["objetivo"]:
            continue
        if not regla_al_dia(regla, f):
            sin_verificar += 1
            continue
        v = evaluar_uno(regla, r)
        resultados.append({"slug": f["slug"], "nombre": f["nombre"], "ruta": f.get("ruta"), "revisado": f.get("revisado"), "ahorro": f.get("ahorro"), "rsh": f.get("rsh"), **v})
    resultados.sort(key=lambda x: (_ORDEN[x["estado"]], x["nombre"]))
    return {"resultados": resultados, "sin_verificar": sin_verificar, "aviso": AVISO}
