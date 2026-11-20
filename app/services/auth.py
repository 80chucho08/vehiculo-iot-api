"""Inicio de sesión, renovación y cierre con token corto revocable (decisión D1)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from app.core import seguridad
from app.core.config import settings
from app.repositories import invitados, sesiones, usuarios
from app.services.autorizacion import Actor


class CredencialesInvalidas(Exception):
    """Mensaje único hacia el cliente para email inexistente, contraseña errónea, cuenta
    inactiva o bloqueada; el motivo real y el id solo van a la auditoría."""

    def __init__(self, motivo: str, id_usuario: int | None = None):
        super().__init__(motivo)
        self.motivo = motivo
        self.id_usuario = id_usuario


class DemasiadosIntentos(Exception):
    def __init__(self, reintentar_en: int):
        super().__init__("Demasiados intentos de inicio de sesión.")
        self.reintentar_en = reintentar_en


@dataclass(frozen=True)
class SesionEmitida:
    token: str
    expira_en: datetime
    expira_absoluto: datetime
    usuario: dict


class LimitadorIntentos:
    """Ventana deslizante por huella de IP, en memoria (un solo proceso uvicorn, ver R5)."""

    def __init__(self, maximo: int = 10, ventana_s: int = 300):
        self.maximo, self.ventana_s = maximo, ventana_s
        self._intentos: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def comprobar(self, clave: str | None) -> None:
        if not clave:
            return
        ahora = time.monotonic()
        with self._lock:
            cola = self._intentos[clave]
            while cola and ahora - cola[0] > self.ventana_s:
                cola.popleft()
            if len(cola) >= self.maximo:
                raise DemasiadosIntentos(int(self.ventana_s - (ahora - cola[0])) + 1)
            cola.append(ahora)

    def limpiar(self) -> None:
        with self._lock:
            self._intentos.clear()


limitador_login = LimitadorIntentos()


def iniciar_sesion(email: str, password: str, ip_hash: str | None) -> SesionEmitida:
    limitador_login.comprobar(ip_hash)
    fila = usuarios.por_email_con_hash(email.strip().lower())
    # Se verifica SIEMPRE (con un hash señuelo si no hay usuario) para no revelar por tiempo
    # de respuesta qué emails existen.
    valida = seguridad.verificar_password(fila["password_hash"] if fila else None, password)
    if fila is None:
        raise CredencialesInvalidas("email_inexistente")
    id_usuario = int(fila["id_usuario"])
    bloqueado = fila["bloqueado_hasta"] is not None and fila["bloqueado_hasta"] > seguridad.ahora()
    if bloqueado:
        raise CredencialesInvalidas("cuenta_bloqueada", id_usuario)
    if not valida:
        usuarios.registrar_fallo(id_usuario, settings.login_max_intentos, settings.login_bloqueo_minutos)
        raise CredencialesInvalidas("password_incorrecta", id_usuario)
    if not fila["activo"]:
        raise CredencialesInvalidas("cuenta_inactiva", id_usuario)

    if seguridad.necesita_rehash(fila["password_hash"]):
        usuarios.cambiar_password(fila["id_usuario"], seguridad.hash_password(password))
    usuarios.registrar_acceso(fila["id_usuario"])

    inicio = seguridad.ahora()
    expira, absoluto = seguridad.vencimientos(inicio)
    sid = seguridad.nuevo_sid()
    sesiones.crear(sid, fila["id_usuario"], expira, absoluto)
    usuario = {k: fila[k] for k in ("id_usuario", "email", "nombre", "rol")}
    return SesionEmitida(seguridad.emitir_token(sid, fila["id_usuario"], expira), expira, absoluto, usuario)


def actor_desde_token(token: str) -> Actor | None:
    """Actor si el token es auténtico, su sesión sigue vigente y el usuario está activo."""
    datos = seguridad.leer_token(token)
    if datos is None:
        return None
    if datos.tipo == "invitado":
        reserva = invitados.obtener_vigente(datos.sid)
        if reserva is None:
            return None
        return Actor(rol="invitado", sid=reserva["sid"], nombre="Invitado",
                     id_sesion_invitado=int(reserva["id_sesion_invitado"]),
                     id_vehiculo_reservado=int(reserva["id_vehiculo"]))
    fila = sesiones.obtener_vigente(datos.sid)
    if fila is None or int(fila["id_usuario"]) != datos.id_usuario or not fila["activo"]:
        return None
    # El rol sale de la base en cada petición: un cambio de rol surte efecto de inmediato.
    return Actor(rol=fila["rol"], id_usuario=int(fila["id_usuario"]), sid=fila["sid"], email=fila["email"], nombre=fila["nombre"])


def renovar(actor: Actor) -> SesionEmitida:
    fila = sesiones.obtener_vigente(actor.sid)
    if fila is None:
        raise CredencialesInvalidas("sesion_vencida")
    expira, absoluto = seguridad.vencimientos(fila["emitida_en"])
    if expira <= seguridad.ahora() or not sesiones.renovar(actor.sid, expira):
        raise CredencialesInvalidas("tope_absoluto")
    usuario = {"id_usuario": actor.id_usuario, "email": actor.email, "nombre": actor.nombre, "rol": actor.rol}
    return SesionEmitida(seguridad.emitir_token(actor.sid, actor.id_usuario, expira), expira, absoluto, usuario)


def cerrar_sesion(actor: Actor) -> None:
    if actor.sid:
        sesiones.revocar(actor.sid, "logout")
