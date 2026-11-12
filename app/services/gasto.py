"""Seguimiento del gasto por cliente: lo que cuesta la IA (Anthropic) y los mensajes de WhatsApp (Meta).

Se guarda el CONSUMO (tokens, cantidad de mensajes por categoría) y NO el dinero: el costo se calcula al mirar la pestaña, con las tarifas de ese
momento, y así una tarifa mal puesta se corrige sin perder nada. Las tarifas que no se han confirmado quedan marcadas «por confirmar».

* Anthropic: cada respuesta del bot anota los tokens que usó (``registrar_anthropic``).
* Meta: cada aviso de estado que llega de Meta trae la categoría con la que cobra el mensaje (marketing, utility, authentication o service);
  se anota una vez por mensaje (``registrar_meta``). Se atribuye al cliente por el teléfono del destinatario.

Registrar NUNCA rompe lo que se estaba haciendo: si la tabla aún no existe o la base falla, solo se anota un aviso en el log.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from app.clients.supabase import _supabase_request
from app.core.errors import AppError

logger = logging.getLogger("wtsp_pita")

ZONA = ZoneInfo("America/Santiago")
PAGINA = 1000
MAX_FILAS = 100_000
_LOTE = 100
CATEGORIAS_META = ("marketing", "utility", "authentication", "service")
AGRUPACIONES = ("cliente", "proyecto", "inmobiliaria", "ejecutivo")

# Valores de partida (por confirmar): el dueño los confirma o corrige en la pestaña y desde ahí quedan guardados.
TARIFAS_DEFECTO: Dict[str, Dict[str, Any]] = {
    "anthropic_entrada": {"valor": 1.0, "unidad": "USD por millón de tokens de entrada", "titulo": "Anthropic · lo que lee el modelo"},
    "anthropic_salida": {"valor": 5.0, "unidad": "USD por millón de tokens de salida", "titulo": "Anthropic · lo que escribe el modelo"},
    "meta_marketing": {"valor": 0.0889, "unidad": "USD por mensaje entregado", "titulo": "Meta · plantilla de marketing"},
    "meta_utility": {"valor": 0.02, "unidad": "USD por mensaje entregado", "titulo": "Meta · plantilla de utilidad"},
    "meta_authentication": {"valor": 0.02, "unidad": "USD por mensaje entregado", "titulo": "Meta · plantilla de autenticación"},
    "meta_service": {"valor": 0.0, "unidad": "USD por mensaje (dentro de las 24 h es gratis)", "titulo": "Meta · conversación de servicio"},
    "usd_clp": {"valor": 950.0, "unidad": "pesos chilenos por 1 dólar", "titulo": "Valor del dólar"},
}


def _tabla_no_existe(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        try:
            cuerpo = e.response.json()
        except Exception:
            cuerpo = {}
        return e.response.status_code == 404 or str((cuerpo or {}).get("code")) in ("PGRST205", "42P01")
    return False


# ── Registrar el consumo ────────────────────────────────────────────────────────────────────────────

async def _anotar(fila: Dict[str, Any], donde: str) -> None:
    try:
        await _supabase_request("POST", "/GastoUso", params={"on_conflict": "referencia"} if fila.get("referencia") else None, json=fila,
                                extra_headers={"Prefer": "return=minimal" + (",resolution=ignore-duplicates" if fila.get("referencia") else "")})
    except Exception as e:
        logger.warning("Gasto | no se pudo anotar el consumo de %s (%s%s)", donde, type(e).__name__, " — falta ejecutar el SQL de GastoUso" if _tabla_no_existe(e) else "")


async def registrar_anthropic(*, prospecto: Optional[Dict[str, Any]], proyecto: Optional[Dict[str, Any]], modelo: str, uso: Any) -> None:
    """Anota los tokens de una respuesta del bot. ``uso`` es el ``usage`` de la respuesta de Anthropic (puede faltar)."""
    try:
        entrada = int(getattr(uso, "input_tokens", 0) or 0)
        salida = int(getattr(uso, "output_tokens", 0) or 0)
    except (TypeError, ValueError):
        return
    if not entrada and not salida:
        return
    p, pr = prospecto or {}, proyecto or {}
    await _anotar({"fuente": "anthropic", "detalle": modelo, "tokens_entrada": entrada, "tokens_salida": salida, "cantidad": 1,
                   "cliente_id": p.get("cliente_id"), "prospecto_id": p.get("id"), "proyecto_id": pr.get("id") or p.get("proyecto_id")}, "Anthropic")


async def registrar_meta(status: Dict[str, Any]) -> None:
    """Anota un mensaje que Meta cobra (o cobraría) según la categoría de su aviso de estado. Se ignora el que no trae precio."""
    precio = status.get("pricing") if isinstance(status, dict) else None
    wamid = status.get("id") if isinstance(status, dict) else None
    categoria = str((precio or {}).get("category") or "").lower()
    if not precio or not wamid or not categoria:
        return
    if precio.get("billable") is False and categoria != "service":
        categoria = "service"                       # Meta dice que no lo cobra (p. ej. dentro de una ventana ya paga): cuenta como gratis
    cliente_id, proyecto_id, prospecto_id = None, None, None
    telefono = str(status.get("recipient_id") or "").strip()
    try:
        if telefono:
            c = await _supabase_request("GET", "/Cliente", params={"Telefono": f"eq.{telefono}", "select": "id,proyecto_id", "limit": "1"}) or []
            if c:
                cliente_id, proyecto_id = c[0].get("id"), c[0].get("proyecto_id")
            else:
                p = await _supabase_request("GET", "/Prospecto", params={"telefono_e164": f"eq.{telefono}", "select": "id,cliente_id,proyecto_id", "limit": "1"}) or []
                if p:
                    prospecto_id, cliente_id, proyecto_id = p[0].get("id"), p[0].get("cliente_id"), p[0].get("proyecto_id")
    except Exception as e:
        logger.warning("Gasto | no se pudo buscar al cliente del mensaje (%s); se anota sin cliente", type(e).__name__)
    await _anotar({"fuente": "meta", "detalle": categoria, "cantidad": 1, "referencia": str(wamid), "cliente_id": cliente_id,
                   "prospecto_id": prospecto_id, "proyecto_id": proyecto_id}, "Meta")


# ── Tarifas ─────────────────────────────────────────────────────────────────────────────────────────

async def leer_tarifas() -> Dict[str, Dict[str, Any]]:
    """Cada tarifa con su valor, su unidad y si ya fue confirmada por una persona (las que no, usan el valor de partida)."""
    guardadas: Dict[str, Any] = {}
    try:
        for f in await _supabase_request("GET", "/TarifaGasto", params={"select": "clave,valor"}) or []:
            guardadas[f.get("clave")] = f.get("valor")
    except Exception as e:
        logger.warning("Gasto | no se pudieron leer las tarifas guardadas (%s); se usan las de partida", type(e).__name__)
    salida = {}
    for clave, base in TARIFAS_DEFECTO.items():
        confirmada = guardadas.get(clave) is not None
        try:
            valor = float(guardadas[clave]) if confirmada else base["valor"]
        except (TypeError, ValueError):
            valor, confirmada = base["valor"], False
        salida[clave] = {"valor": valor, "unidad": base["unidad"], "titulo": base["titulo"], "confirmada": confirmada}
    return salida


def validar_tarifas(cuerpo: Any) -> Dict[str, float]:
    """Las tarifas nuevas del cuerpo de la petición (clave → número ≥ 0). ``AppError`` 400 si algo no sirve."""
    if not isinstance(cuerpo, dict) or not cuerpo:
        raise AppError("No llegó ninguna tarifa para guardar.", 400)
    salida: Dict[str, float] = {}
    for clave, valor in cuerpo.items():
        if clave not in TARIFAS_DEFECTO:
            raise AppError(f"«{clave}» no es una tarifa conocida.", 400)
        if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
            raise AppError(f"El valor de «{TARIFAS_DEFECTO[clave]['titulo']}» no es un número.", 400)
        try:
            numero = float(str(valor).replace(",", "."))
        except ValueError:
            raise AppError(f"El valor de «{TARIFAS_DEFECTO[clave]['titulo']}» no es un número.", 400)
        if not 0 <= numero <= 1_000_000 or numero != numero:
            raise AppError(f"El valor de «{TARIFAS_DEFECTO[clave]['titulo']}» debe estar entre 0 y 1.000.000.", 400)
        if clave == "usd_clp" and numero <= 0:
            raise AppError("El valor del dólar debe ser mayor que 0.", 400)
        salida[clave] = numero
    return salida


async def guardar_tarifas(nuevas: Dict[str, float]) -> None:
    ahora = datetime.now(timezone.utc).isoformat()
    await _supabase_request("POST", "/TarifaGasto", params={"on_conflict": "clave"},
                            json=[{"clave": k, "valor": v, "actualizado_en": ahora} for k, v in nuevas.items()],
                            extra_headers={"Prefer": "return=minimal,resolution=merge-duplicates"})


# ── Cálculo ─────────────────────────────────────────────────────────────────────────────────────────

def costo_usd(fila: Dict[str, Any], tarifas: Dict[str, Dict[str, Any]]) -> Tuple[float, float]:
    """(costo de Anthropic, costo de Meta) en dólares de un conjunto de consumos ya sumado (``fila``)."""
    ant = (fila.get("tokens_entrada", 0) / 1_000_000) * tarifas["anthropic_entrada"]["valor"] + (fila.get("tokens_salida", 0) / 1_000_000) * tarifas["anthropic_salida"]["valor"]
    meta = sum(n * tarifas.get(f"meta_{cat}", {"valor": 0.0})["valor"] for cat, n in (fila.get("meta") or {}).items())
    return ant, meta


def rango_utc(desde: Optional[str], hasta: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Del período pedido (fechas ``AAAA-MM-DD`` en hora de Chile, ambas incluidas) saca los límites en hora UTC y las fechas ya validadas."""
    def fecha(txt: Optional[str], nombre: str) -> Optional[date]:
        if not txt:
            return None
        try:
            return date.fromisoformat(txt)
        except ValueError:
            raise AppError(f"La fecha «{nombre}» no es válida.", 400)
    d, h = fecha(desde, "desde"), fecha(hasta, "hasta")
    if d and h and d > h:
        raise AppError("La fecha «desde» no puede ser posterior a «hasta».", 400)
    ini = datetime.combine(d, time.min, ZONA).astimezone(timezone.utc).isoformat() if d else None
    fin = datetime.combine(h + timedelta(days=1), time.min, ZONA).astimezone(timezone.utc).isoformat() if h else None
    return ini, fin, d.isoformat() if d else None, h.isoformat() if h else None


async def _leer_consumos(ini: Optional[str], fin: Optional[str]) -> Tuple[List[Dict[str, Any]], bool]:
    filtros: List[Tuple[str, str]] = [("select", "id,creado_en,fuente,detalle,cliente_id,proyecto_id,tokens_entrada,tokens_salida,cantidad"), ("order", "id.asc")]
    if ini:
        filtros.append(("creado_en", f"gte.{ini}"))
    if fin:
        filtros.append(("creado_en", f"lt.{fin}"))
    filas: List[Dict[str, Any]] = []
    while len(filas) < MAX_FILAS:
        lote = await _supabase_request("GET", "/GastoUso", params=filtros + [("limit", str(PAGINA)), ("offset", str(len(filas)))]) or []
        filas.extend(lote)
        if len(lote) < PAGINA:
            return filas, False
    return filas, True


async def _primer_registro() -> Optional[str]:
    """Desde cuándo se está registrando el consumo (antes no hay datos: el seguimiento parte el día que se activó)."""
    try:
        f = await _supabase_request("GET", "/GastoUso", params={"select": "creado_en", "order": "id.asc", "limit": "1"}) or []
        return f[0].get("creado_en") if f else None
    except Exception:
        return None


async def _por_lotes(ruta: str, columna: str, ids: Iterable[Any], select: str) -> List[Dict[str, Any]]:
    ids = sorted({str(i) for i in ids if i is not None})
    salida: List[Dict[str, Any]] = []
    for i in range(0, len(ids), _LOTE):
        try:
            salida.extend(await _supabase_request("GET", ruta, params={columna: f"in.({','.join(ids[i:i + _LOTE])})", "select": select}) or [])
        except Exception as e:
            logger.warning("Gasto | no se pudo leer un lote de %s (%s)", ruta, type(e).__name__)
    return salida


def _sumar(acum: Dict[str, Any], f: Dict[str, Any]) -> None:
    if f.get("fuente") == "anthropic":
        acum["tokens_entrada"] += int(f.get("tokens_entrada") or 0)
        acum["tokens_salida"] += int(f.get("tokens_salida") or 0)
        acum["llamadas"] += int(f.get("cantidad") or 1)
    elif f.get("fuente") == "meta":
        cat = str(f.get("detalle") or "otra")
        acum["meta"][cat] = acum["meta"].get(cat, 0) + int(f.get("cantidad") or 1)


def _vacio() -> Dict[str, Any]:
    return {"tokens_entrada": 0, "tokens_salida": 0, "llamadas": 0, "meta": {}}


def _cerrar(acum: Dict[str, Any], tarifas: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ant, meta = costo_usd(acum, tarifas)
    clp = tarifas["usd_clp"]["valor"]
    pagados = sum(n for c, n in acum["meta"].items() if tarifas.get(f"meta_{c}", {"valor": 0})["valor"] > 0)
    return {**acum, "mensajes_meta": sum(acum["meta"].values()), "mensajes_meta_pagados": pagados,
            "anthropic_usd": round(ant, 6), "meta_usd": round(meta, 6), "total_usd": round(ant + meta, 6), "total_clp": round((ant + meta) * clp)}


async def resumen(desde: Optional[str], hasta: Optional[str], agrupar: str) -> Dict[str, Any]:
    """El gasto del período, sumado por cliente, proyecto, inmobiliaria o ejecutivo, más los totales y las tarifas usadas."""
    if agrupar not in AGRUPACIONES:
        raise AppError("Elige cómo agrupar: cliente, proyecto, inmobiliaria o ejecutivo.", 400)
    ini, fin, d, h = rango_utc(desde, hasta)
    tarifas = await leer_tarifas()
    try:
        filas, recortado = await _leer_consumos(ini, fin)
    except Exception as e:
        if _tabla_no_existe(e):
            return {"disponible": False, "tarifas": tarifas}
        raise

    clientes = {c["id"]: c for c in await _por_lotes("/Cliente", "id", [f.get("cliente_id") for f in filas], "id,Contacto,usuario_id,proyecto_id")}
    proyectos_ids = {f.get("proyecto_id") or (clientes.get(f.get("cliente_id")) or {}).get("proyecto_id") for f in filas}
    proyectos = {p["id"]: p for p in await _por_lotes("/Proyecto", "id", proyectos_ids, "id,nombre,inmobiliaria_id")}
    inmobiliarias = {i["id"]: i for i in await _por_lotes("/Inmobiliaria", "id", [p.get("inmobiliaria_id") for p in proyectos.values()], "id,nombre")}
    usuarios = {u["id"]: u for u in await _por_lotes("/Usuario", "id", [c.get("usuario_id") for c in clientes.values()], "id,nombre")}

    def clave_y_nombre(f: Dict[str, Any]) -> Tuple[str, str]:
        c = clientes.get(f.get("cliente_id")) or {}
        pid = f.get("proyecto_id") or c.get("proyecto_id")
        pr = proyectos.get(pid) or {}
        if agrupar == "cliente":
            return (f"c{f['cliente_id']}", (c.get("Contacto") or f"Cliente {f['cliente_id']}")) if f.get("cliente_id") is not None else ("", "Sin cliente (avisos internos y conversaciones sin ficha)")
        if agrupar == "proyecto":
            return (str(pid), pr.get("nombre") or "Proyecto sin nombre") if pid else ("", "Sin proyecto")
        if agrupar == "inmobiliaria":
            im = inmobiliarias.get(pr.get("inmobiliaria_id")) or {}
            return (f"i{im['id']}", im.get("nombre") or "Inmobiliaria sin nombre") if im else ("", "Sin inmobiliaria")
        u = usuarios.get(c.get("usuario_id")) or {}
        return (str(u["id"]), u.get("nombre") or "Ejecutivo sin nombre") if u else ("", "Sin ejecutivo")

    grupos: Dict[str, Dict[str, Any]] = {}
    total = _vacio()
    for f in filas:
        k, nombre = clave_y_nombre(f)
        g = grupos.setdefault(k, {"clave": k, "nombre": nombre, **_vacio()})
        _sumar(g, f)
        _sumar(total, f)
    lista = sorted((_cerrar(g, tarifas) | {"clave": g["clave"], "nombre": g["nombre"]} for g in grupos.values()), key=lambda g: (-g["total_usd"], -g["mensajes_meta"], -g["llamadas"], g["nombre"]))
    return {"disponible": True, "desde": d, "hasta": h, "agrupar": agrupar, "recortado": recortado, "tarifas": tarifas,
            "primer_registro": await _primer_registro(), "totales": _cerrar(total, tarifas), "grupos": lista[:500], "grupos_total": len(lista)}
