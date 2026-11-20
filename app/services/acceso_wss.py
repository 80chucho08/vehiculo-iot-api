"""Acceso al WebSocket por unidad (Etapa 8).

Navegador: el WebSocket del navegador no puede mandar la cabecera Authorization, así que primero
pide por REST (con su token) un TICKET de un solo uso válido 30 s para UNA unidad, y abre
wss://…/ws/vehiculos/{id}?ticket=…  El ticket ya lleva el actor autorizado; nunca se pone el token
de sesión en la URL (quedaría en logs).

Dispositivo: ?rol=dispositivo&token=… comparado (SHA-256, tiempo constante) con
credenciales_dispositivo. Un observador cualquiera ya no puede hacerse pasar por el ESP8266.

Revalidación: cada 15 s cada conexión de observador vuelve a pasar por `evaluar()` con datos
frescos de la base. Si la sesión venció o se revocó, la unidad se vendió a otro, terminó la reserva
o se retiró el soporte, el socket se cierra con 4403.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

import pymysql

from app.core.config import settings
from app.core.websocket_manager import websocket_manager
from app.repositories import invitados, propiedad, sesiones
from app.repositories.base import una
from app.services.autorizacion import ANONIMO, Accion, Actor, evaluar

logger = logging.getLogger(__name__)

TICKET_VIGENCIA_S = 30
CIERRE_SIN_CREDENCIAL = 4401
CIERRE_REVOCADO = 4403


@dataclass(frozen=True)
class _Ticket:
    actor: Actor
    id_vehiculo: int
    vence: float


_tickets: dict[str, _Ticket] = {}


def emitir_ticket(actor: Actor, id_vehiculo: int) -> str:
    ahora = time.monotonic()
    for clave in [k for k, t in _tickets.items() if t.vence <= ahora]:
        _tickets.pop(clave, None)
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = _Ticket(actor, id_vehiculo, ahora + TICKET_VIGENCIA_S)
    return ticket


def consumir_ticket(ticket: str, id_vehiculo: int) -> Actor | None:
    """Un solo uso, solo para la unidad pedida y dentro de su vigencia."""
    datos = _tickets.pop(ticket, None)
    if datos is None or datos.vence <= time.monotonic() or datos.id_vehiculo != id_vehiculo:
        return None
    return datos.actor


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def dispositivo_autorizado(id_vehiculo: int, token: str | None) -> bool:
    fila = una(
        "SELECT token_hash FROM credenciales_dispositivo WHERE id_vehiculo = %s AND revocado_en IS NULL",
        (id_vehiculo,),
    )
    if fila is None:
        if settings.dispositivo_token_obligatorio:
            return False
        logger.warning("Dispositivo sin token registrado conectado id_vehiculo=%s (modo transición).", id_vehiculo)
        return True
    return bool(token) and hmac.compare_digest(fila["token_hash"], hash_token(token))


def actor_vigente(actor: Actor | None) -> Actor | None:
    """Relee de la base la sesión del actor (rol y estado actuales). None si ya no es válida."""
    if actor is None or actor.rol == "anonimo":
        return ANONIMO
    if actor.rol == "invitado":
        reserva = invitados.obtener_vigente(actor.sid)
        return actor if reserva else None
    fila = sesiones.obtener_vigente(actor.sid)
    if fila is None or not fila["activo"]:
        return None
    return Actor(rol=fila["rol"], id_usuario=int(fila["id_usuario"]), sid=fila["sid"], email=fila["email"], nombre=fila["nombre"])


def puede_observar(actor: Actor, id_vehiculo: int) -> bool:
    """Un observador recibe estado y movimientos: exige MONITOREAR, igual que GET /estado e /historial."""
    unidad = propiedad.unidad(id_vehiculo)
    soporte = (
        actor.rol == "admin" and unidad is not None and unidad.get("id_propietario") is not None
        and propiedad.soporte_vigente(id_vehiculo, actor.id_usuario) is not None
    )
    return evaluar(actor, unidad, Accion.MONITOREAR, soporte_vigente=soporte).permitido


def _revisar(id_vehiculo: int, actor: Actor | None) -> bool:
    vigente = actor_vigente(actor)
    return vigente is not None and puede_observar(vigente, id_vehiculo)


async def revalidar_conexiones() -> int:
    """Cierra (4403) las conexiones de observador que ya no tienen permiso. Devuelve cuántas cerró."""
    cerradas = 0
    for id_vehiculo, websocket, actor in websocket_manager.observadores():
        try:
            permitido = await asyncio.to_thread(_revisar, id_vehiculo, actor)
        except pymysql.MySQLError:
            return cerradas  # RDS no responde: no se corta a nadie por un fallo de infraestructura
        if not permitido:
            await websocket_manager.cerrar(id_vehiculo, websocket, CIERRE_REVOCADO, "Acceso revocado.")
            cerradas += 1
    if cerradas:
        logger.info("WSS: %s conexión(es) cerrada(s) por cambio de permisos.", cerradas)
    return cerradas
