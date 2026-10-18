"""Caché de usuario autenticado (Fase 2b).

Evita repetir en cada petición los 2 viajes de ``_get_usuario_actual`` (Supabase ``/auth/v1/user`` +
``SELECT`` en ``Usuario``).

Reglas:
  * TTL corto (30 s por defecto, ``USER_CACHE_TTL_SEGUNDOS``; 0 = desactivada) y tamaño máximo
    (1000 entradas por defecto, ``USER_CACHE_MAX_ENTRADAS``), con expulsión del menos usado.
  * La clave es el SHA-256 del token: el token en sí NUNCA se guarda ni se registra.
  * Solo se guardan consultas exitosas (token aceptado por Supabase Y fila encontrada en ``Usuario``).
    Los fallos nunca se cachean: un error transitorio no deja a nadie fuera 30 s.
  * Solo se guardan los campos necesarios (``CAMPOS_PERFIL``); el perfil completo (RUT, fechas…) no se retiene.
  * Invalidación inmediata cuando ESTE proceso modifica a un usuario (rol, estado, etc.). Un contador de
    generación impide que una consulta que ya estaba en vuelo guarde después un perfil anterior al cambio.
  * No se valida el JWT localmente: la verificación sigue siendo la de Supabase; la caché solo recuerda
    su resultado hasta 30 s.

Límites que hay que conocer: un cambio hecho FUERA de este proceso (panel de Supabase, otra réplica) o un
token revocado/expirado puede seguir aceptándose hasta TTL segundos.
"""
import copy
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Set, Tuple

from app.core.config import get_settings

logger = logging.getLogger("wtsp_pita")

# Lo que el backend y el frontend (/api/usuarios/me) realmente leen del perfil.
CAMPOS_PERFIL: Tuple[str, ...] = (
    "id", "nombre", "correo", "rol", "estado", "celular", "email_alias",
    "inmobiliaria_ids", "proyecto_ids", "password_provisional",
)


def reducir_perfil(fila: Dict[str, Any]) -> Dict[str, Any]:
    """Solo los campos necesarios, y solo los que la fila trae (se conserva la semántica de .get)."""
    return {k: copy.deepcopy(fila[k]) for k in CAMPOS_PERFIL if k in fila}


class CacheUsuarios:
    def __init__(self, reloj: Callable[[], float] = time.monotonic):
        self._reloj = reloj
        self._entradas: "OrderedDict[str, Tuple[float, str, Dict[str, Any]]]" = OrderedDict()
        self._por_usuario: Dict[str, Set[str]] = {}
        self._generacion = 0
        self.aciertos = 0
        self.fallos = 0
        self.expulsiones = 0
        self.invalidaciones = 0

    # ── utilidades ────────────────────────────────────────────────────────────
    @staticmethod
    def clave(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _ttl(self) -> float:
        return float(get_settings().user_cache_ttl_segundos)

    def _max(self) -> int:
        return int(get_settings().user_cache_max_entradas)

    def activa(self) -> bool:
        return self._ttl() > 0

    def __len__(self) -> int:
        return len(self._entradas)

    def _quitar(self, clave: str) -> None:
        entrada = self._entradas.pop(clave, None)
        if entrada:
            claves = self._por_usuario.get(entrada[1])
            if claves is not None:
                claves.discard(clave)
                if not claves:
                    self._por_usuario.pop(entrada[1], None)

    def _purgar_vencidas(self) -> None:
        ahora = self._reloj()
        for clave in [c for c, (exp, _, _) in self._entradas.items() if exp <= ahora]:
            self._quitar(clave)

    # ── API ───────────────────────────────────────────────────────────────────
    def generacion(self) -> int:
        """Contador que sube con cada invalidación; se captura ANTES de consultar a Supabase."""
        return self._generacion

    def obtener(self, token: str) -> Optional[Dict[str, Any]]:
        if not self.activa():
            return None
        clave = self.clave(token)
        entrada = self._entradas.get(clave)
        if entrada is None:
            self.fallos += 1
            return None
        expira, _, perfil = entrada
        if expira <= self._reloj():
            self._quitar(clave)
            self.fallos += 1
            return None
        self._entradas.move_to_end(clave)           # el más usado recientemente
        self.aciertos += 1
        return copy.deepcopy(perfil)                 # copia: quien la reciba no puede alterar la caché

    def guardar(self, token: str, user_id: str, perfil: Dict[str, Any], generacion: int) -> bool:
        """Guarda un resultado EXITOSO. Nunca lanza. Devuelve si lo guardó."""
        try:
            if not self.activa() or not user_id or not perfil:
                return False
            if generacion != self._generacion:       # hubo una invalidación mientras se consultaba
                return False
            clave = self.clave(token)
            self._quitar(clave)
            if len(self._entradas) >= self._max():
                self._purgar_vencidas()
            while len(self._entradas) >= self._max():
                viejo = next(iter(self._entradas))
                self._quitar(viejo)
                self.expulsiones += 1
            self._entradas[clave] = (self._reloj() + self._ttl(), user_id, reducir_perfil(perfil))
            self._por_usuario.setdefault(user_id, set()).add(clave)
            return True
        except Exception:
            logger.exception("Caché de usuario: no se pudo guardar (se ignora)")
            return False

    def invalidar_usuario(self, user_id: str) -> int:
        """Borra todas las entradas de ese usuario (cualquier token suyo) y anula consultas en vuelo."""
        self._generacion += 1
        self.invalidaciones += 1
        quitadas = 0
        for clave in list(self._por_usuario.get(user_id, ())):
            self._quitar(clave)
            quitadas += 1
        return quitadas

    def vaciar(self) -> None:
        self._generacion += 1
        self._entradas.clear()
        self._por_usuario.clear()

    def estadisticas(self) -> Dict[str, Any]:
        total = self.aciertos + self.fallos
        return {"entradas": len(self._entradas), "aciertos": self.aciertos, "fallos": self.fallos,
                "expulsiones": self.expulsiones, "invalidaciones": self.invalidaciones,
                "tasa_aciertos": round(self.aciertos / total, 3) if total else None}

    def registrar_periodicamente(self, cada: int = 500) -> None:
        total = self.aciertos + self.fallos
        if total and total % cada == 0:
            logger.info("Caché de usuario", extra=self.estadisticas())


user_cache = CacheUsuarios()
