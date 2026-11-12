"""Carga masiva de clientes: cuántos clientes por persona se recomienda en una misma carga, cómo repartirlos entre varios ejecutivos y cómo validar ese reparto.

Nació de un caso real: alguien subió un archivo con miles de clientes y todos quedaron asignados a una sola persona, que el rol de administración no pudo repartir después. Ahora
el panel revisa el archivo antes de cargarlo, recomienda un reparto y avisa (pidiendo confirmación, también en el servidor) cuando una persona recibiría más de lo recomendado.

Las funciones de este archivo no hablan con la base salvo ``validar_reparto``, ``ejecutivos_disponibles`` y ``cargas_actuales`` (solo lecturas).
"""
import csv
import io
import json
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.clients.supabase import _supabase_request
from app.core.errors import AppError
from app.core.utilidades import _norm_col

logger = logging.getLogger("wtsp_pita")

RECOMENDADO_POR_PERSONA = 100        # clientes por persona en una misma carga; más que esto pide confirmación
MAX_PERSONAS_EN_REPARTO = 100
MAX_BYTES_ARCHIVO = 15 * 1024 * 1024
_ID_USUARIO = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PAGINA = 1000
_MAX_PAGINAS = 30                    # tope de la lectura de cargas actuales (30.000 clientes): más allá se omite el dato en vez de demorar la pantalla

Reparto = List[Tuple[Optional[str], int]]          # [(usuario_id o None = «sin asignar», cantidad)]


def recomendar(total: int, disponibles: int, verbo: str = "cargarían") -> Dict[str, Any]:
    """La recomendación para ``total`` clientes con ``disponibles`` ejecutivos activos: cuántas personas y cuántos clientes a cada una."""
    if total <= 0:
        return {"personas": 0, "por_persona": 0, "excede": False, "mensaje": ""}
    necesarias = math.ceil(total / RECOMENDADO_POR_PERSONA)
    personas = 1 if necesarias <= 1 else min(necesarias, max(disponibles, 1))
    por_persona = math.ceil(total / personas)
    if necesarias <= 1:
        mensaje = f"Se {verbo} {total} clientes: puedes asignarlos a una sola persona (lo recomendado es hasta {RECOMENDADO_POR_PERSONA} por persona)."
    elif disponibles >= necesarias:
        mensaje = f"Se {verbo} {total} clientes. Recomendación: asignar {por_persona} a cada una de {personas} personas."
    else:
        mensaje = (f"Se {verbo} {total} clientes y lo recomendado es hasta {RECOMENDADO_POR_PERSONA} por persona, pero solo hay {disponibles} "
                   f"ejecutivo{'s' if disponibles != 1 else ''} activo{'s' if disponibles != 1 else ''}: tocarían {por_persona} a cada uno.")
    return {"personas": personas, "por_persona": por_persona, "excede": por_persona > RECOMENDADO_POR_PERSONA, "mensaje": mensaje}


def reparto_igual(total: int, usuario_ids: List[str]) -> List[Dict[str, Any]]:
    """Partes iguales; si no calza exacto, las primeras personas reciben uno más. Quien quede con cero no aparece."""
    if total <= 0 or not usuario_ids:
        return []
    base, resto = divmod(total, len(usuario_ids))
    partes = [{"usuario_id": u, "cantidad": base + (1 if i < resto else 0)} for i, u in enumerate(usuario_ids)]
    return [p for p in partes if p["cantidad"] > 0]


def asignador(usuario_por_defecto: Optional[str], reparto: Optional[Reparto]) -> Callable[[], Optional[str]]:
    """Función que, cada vez que se llama, dice a quién va el siguiente cliente creado: sin reparto, siempre a la misma persona; con reparto, bloques seguidos en el orden
    del archivo (los primeros ``n1`` a la primera persona, los siguientes ``n2`` a la segunda…). Si llegan más de lo previsto, el resto va a la última."""
    if not reparto:
        return lambda: usuario_por_defecto
    estado = {"i": 0, "usados": 0}

    def siguiente() -> Optional[str]:
        while estado["i"] < len(reparto) - 1 and estado["usados"] >= reparto[estado["i"]][1]:
            estado["i"] += 1
            estado["usados"] = 0
        estado["usados"] += 1
        return reparto[estado["i"]][0]
    return siguiente


def exige_confirmacion(total_filas: int, reparto: Optional[Reparto]) -> Optional[str]:
    """Si esta carga pondría a una persona por encima de lo recomendado, el mensaje que se le muestra (y que hay que confirmar); si no, ``None``."""
    if reparto:
        mayor = max((c for u, c in reparto if u), default=0)          # «sin asignar» no recarga a nadie
        if mayor > RECOMENDADO_POR_PERSONA:
            return (f"El reparto deja hasta {mayor} clientes en una sola persona (lo recomendado es hasta {RECOMENDADO_POR_PERSONA}). "
                    "Repártelos entre más ejecutivos o confirma la carga.")
        return None
    if total_filas > RECOMENDADO_POR_PERSONA:
        return (f"Este archivo trae {total_filas} filas y se asignarían a una sola persona (lo recomendado es hasta {RECOMENDADO_POR_PERSONA}). "
                "Repártelas entre varios ejecutivos o confirma la carga.")
    return None


def _entero(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


async def usuarios_activos(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """De esos ids, los que existen y están activos (``estado`` = 1), por id."""
    ids = [i for i in dict.fromkeys(ids) if isinstance(i, str) and _ID_USUARIO.fullmatch(i)]
    if not ids:
        return {}
    filas = await _supabase_request("GET", "/Usuario", params={"id": f"in.({','.join(ids)})", "select": "id,nombre,rol,estado"}) or []
    return {str(f["id"]): f for f in filas if f.get("estado") == 1}


async def validar_asignado(usuario_id: str) -> None:
    """La persona a la que se asigna toda la carga debe existir y estar activa."""
    if not isinstance(usuario_id, str) or not _ID_USUARIO.fullmatch(usuario_id) or usuario_id not in await usuarios_activos([usuario_id]):
        raise AppError("La persona elegida para asignar los clientes no existe o está dada de baja.", 400)


async def validar_reparto(crudo: Any) -> Reparto:
    """El reparto que llegó de la pantalla (texto JSON o lista de ``{usuario_id, cantidad}``) ya validado: formato, cantidades enteras, personas sin repetir, y que cada
    persona exista y esté activa. ``usuario_id`` nulo o vacío = «sin asignar». Lanza ``AppError`` 400 con el mensaje para la persona."""
    try:
        datos = json.loads(crudo) if isinstance(crudo, (str, bytes)) else crudo
    except ValueError:
        raise AppError("El reparto no se pudo leer. Vuelve a elegir cómo repartir los clientes.", 400)
    if not isinstance(datos, list) or not datos:
        raise AppError("El reparto está vacío: elige al menos una persona.", 400)
    if len(datos) > MAX_PERSONAS_EN_REPARTO:
        raise AppError(f"El reparto admite hasta {MAX_PERSONAS_EN_REPARTO} personas.", 400)
    reparto: Reparto = []
    vistos: set = set()
    for item in datos:
        if not isinstance(item, dict):
            raise AppError("El reparto no tiene el formato esperado.", 400)
        uid = item.get("usuario_id")
        if uid in (None, ""):
            uid = None
        elif not isinstance(uid, str) or not _ID_USUARIO.fullmatch(uid):
            raise AppError("Una de las personas del reparto no es válida.", 400)
        cantidad = _entero(item.get("cantidad"))
        if cantidad is None or cantidad < 1:
            raise AppError("Cada persona del reparto necesita una cantidad entera de 1 o más.", 400)
        if uid in vistos:
            raise AppError("Una misma persona aparece más de una vez en el reparto.", 400)
        vistos.add(uid)
        reparto.append((uid, cantidad))
    activos = await usuarios_activos([u for u, _ in reparto if u])
    if any(u and u not in activos for u, _ in reparto):
        raise AppError("Una de las personas del reparto no existe o está dada de baja.", 400)
    return reparto


async def cargas_actuales(ids: List[str]) -> Optional[Dict[str, int]]:
    """Cuántos clientes (no descartados) tiene hoy cada una de esas personas (quien no aparece, tiene cero). Es información de apoyo: si no se pudo contar, ``None``."""
    ids = [i for i in ids if isinstance(i, str) and _ID_USUARIO.fullmatch(i)]
    cuenta: Dict[str, int] = {}
    if not ids:
        return cuenta
    try:
        for pagina in range(_MAX_PAGINAS):
            filas = await _supabase_request("GET", "/Cliente", params={
                "select": "usuario_id", "archivado": "not.is.true", "usuario_id": f"in.({','.join(ids)})", "order": "id.asc",
                "limit": str(_PAGINA), "offset": str(pagina * _PAGINA)}) or []
            for f in filas:
                u = str(f.get("usuario_id"))
                cuenta[u] = cuenta.get(u, 0) + 1
            if len(filas) < _PAGINA:
                return cuenta
        return None                                    # demasiados clientes para contarlos aquí: mejor no mostrar el dato que mostrarlo incompleto
    except Exception as e:
        logger.warning("Carga masiva | no se pudo contar la carga actual de los ejecutivos (%s)", type(e).__name__)
        return None


async def ejecutivos_disponibles(proyecto_id: Optional[str]) -> List[Dict[str, Any]]:
    """Las personas activas a quienes se puede repartir (ejecutivos primero, luego administración), con cuántos clientes tienen hoy y si tienen asignado el proyecto de destino. Si hay un proyecto de destino, solo se ofrecen los ejecutivos que lo tienen asignado."""
    filas = await _supabase_request("GET", "/Usuario", params={"estado": "eq.1", "select": "id,nombre,rol,proyecto_ids,inmobiliaria_ids", "order": "nombre.asc"}) or []
    inmobiliaria = None
    if proyecto_id and isinstance(proyecto_id, str) and _ID_USUARIO.fullmatch(proyecto_id):
        pf = await _supabase_request("GET", "/Proyecto", params={"id": f"eq.{proyecto_id}", "select": "id,inmobiliaria_id", "limit": "1"}) or []
        inmobiliaria = str(pf[0].get("inmobiliaria_id")) if pf and pf[0].get("inmobiliaria_id") is not None else None
    cargas = await cargas_actuales([str(f["id"]) for f in filas])
    salida = []
    for f in filas:
        uid = str(f["id"])
        if proyecto_id:
            asignado = proyecto_id in {str(p) for p in (f.get("proyecto_ids") or [])} or (inmobiliaria is not None and inmobiliaria in {str(i) for i in (f.get("inmobiliaria_ids") or [])})
        else:
            asignado = None
        if proyecto_id and f.get("rol") == "ejecutivo" and not asignado:
            continue                                       # un ejecutivo que no tiene ese proyecto asignado no aparece como opción (administración sí, siempre)
        salida.append({"id": uid, "nombre": f.get("nombre") or "Sin nombre", "rol": f.get("rol"), "clientes_actuales": cargas.get(uid, 0) if cargas is not None else None,
                       "tiene_proyecto": asignado if f.get("rol") == "ejecutivo" else None})
    orden = {"ejecutivo": 0, "administrador": 1, "owner": 2}
    salida.sort(key=lambda x: (orden.get(x["rol"], 3), x["nombre"].lower()))
    return salida


def leer_archivo(contenido: bytes) -> Tuple[str, str, bool]:
    """El texto del CSV, su separador y si es el formato SERVIU (columna «Dv» o «Primer Apellido»). Lanza ``AppError`` si el archivo pesa demasiado."""
    if len(contenido) > MAX_BYTES_ARCHIVO:
        raise AppError(f"El archivo es demasiado grande (máximo {MAX_BYTES_ARCHIVO // (1024 * 1024)} MB). Divídelo en partes.", 413)
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")
    # Exportaciones desde Excel en configuración regional es-CL suelen usar «;» (porque la coma se usa como separador decimal).
    try:
        separador = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        separador = ","
    encabezados = csv.DictReader(io.StringIO(texto, newline=""), delimiter=separador).fieldnames or []
    normalizados = {_norm_col(h) for h in encabezados}
    return texto, separador, ("dv" in normalizados or "primerapellido" in normalizados)


def contar_filas(texto: str) -> int:
    """Filas de datos del archivo (sin el encabezado ni las líneas vacías)."""
    return max(0, sum(1 for l in texto.splitlines() if l.strip()) - 1)
