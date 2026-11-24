"""Regla única de autorización por unidad (RBAC + propiedad). La usan REST y, en la Etapa 8, WSS.

Acciones:
  VER          datos de catálogo de la unidad: detalle, comandos y demos.
  MONITOREAR   estado e historial (privados del comprador).
  OPERAR       ejecutar comandos e iniciar demos.
  DETENER      DETENER y cancelar demo: mismas reglas que OPERAR, pero la reserva de un invitado
               NO lo bloquea (detener siempre es seguro).

Reglas:
  - Unidad inexistente → 404.
  - Usuario (comprador): todo sobre las unidades con asignación VIGENTE a él. Sobre las ajenas
    responde 404, igual que si no existieran, para no revelar cuáles están vendidas (BOLA/IDOR).
  - Admin: VER siempre. MONITOREAR/OPERAR una unidad VENDIDA solo con una autorización de soporte
    vigente del comprador → si no, 403. Las unidades sin comprador (inventario, exhibición) sí.
  - Anónimo: con AUTH_OBLIGATORIA=false (transición de 4.1/4.2) puede VER/MONITOREAR/OPERAR
    unidades SIN comprador; una unidad vendida nunca. Con AUTH_OBLIGATORIA=true → 401.
  - Invitado (reserva anónima del catálogo): solo la unidad de exhibición de SU reserva vigente
    (VER, MONITOREAR, OPERAR, DETENER); cualquier otra → 404.
  - Cuenta de invitado (rol 'invitado' en usuarios, V009): cualquier unidad de EXHIBICIÓN activa y
    sin comprador; una vendida, en inventario o simulada → 404. Respeta la reserva de un invitado
    anónimo (409 al OPERAR). Solo comandos `permitido_invitado` y demos de fábrica (deps.py). Mientras una reserva está vigente nadie más puede OPERAR esa
    unidad (409), ni siquiera el admin; DETENER sí.
  - Nunca se confía en rol, id_usuario ni propietario enviados por el cliente: el actor sale del
    token y se relee de la base; el propietario sale de `asignaciones`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.core.config import settings


class Accion(str, Enum):
    VER = "ver"
    MONITOREAR = "monitorear"
    OPERAR = "operar"
    DETENER = "detener"


@dataclass(frozen=True)
class Actor:
    rol: str  # 'admin' | 'usuario' | 'invitado' (cuenta o reserva) | 'anonimo'
    id_usuario: int | None = None
    sid: str | None = None
    email: str | None = None
    nombre: str | None = None
    # Solo invitados: su reserva.
    id_sesion_invitado: int | None = None
    id_vehiculo_reservado: int | None = None

    @property
    def autenticado(self) -> bool:
        return self.id_usuario is not None

    @property
    def es_reserva(self) -> bool:
        """Invitado anónimo del catálogo (token de reserva, sin cuenta)."""
        return self.rol == "invitado" and self.id_usuario is None


ANONIMO = Actor(rol="anonimo")


@dataclass(frozen=True)
class Decision:
    permitido: bool
    status: int = 200
    detalle: str = ""
    via: str = ""  # propietario | soporte | inventario | transicion | reserva | exhibicion


def evaluar(
    actor: Actor,
    unidad: dict[str, Any] | None,
    accion: Accion,
    soporte_vigente: bool = False,
    auth_obligatoria: bool | None = None,
) -> Decision:
    obligatoria = settings.auth_obligatoria if auth_obligatoria is None else auth_obligatoria
    if unidad is None:
        return Decision(False, 404, "Vehículo no encontrado.")
    propietario = unidad.get("id_propietario")
    reserva = unidad.get("reserva_sid")
    reservada_por_otro = reserva is not None and not (actor.es_reserva and actor.sid == reserva)
    bloqueo = (
        Decision(False, 409, "La unidad de exhibición está reservada por un invitado: puedes DETENERLA, no operarla.")
        if accion is Accion.OPERAR and reservada_por_otro and propietario is None
        else None
    )

    if actor.rol == "invitado" and actor.id_usuario is not None:
        if unidad.get("tipo_unidad") == "exhibicion" and unidad.get("activo", 1) and propietario is None:
            return bloqueo or Decision(True, via="exhibicion")
        return Decision(False, 404, "Vehículo no encontrado.")

    if actor.rol == "invitado":
        if reserva is not None and reserva == actor.sid and unidad.get("id_vehiculo") == actor.id_vehiculo_reservado:
            return Decision(True, via="reserva")
        return Decision(False, 404, "Vehículo no encontrado.")

    if actor.rol == "usuario":
        if propietario is not None and propietario == actor.id_usuario:
            return Decision(True, via="propietario")
        if propietario is None and not obligatoria:
            return bloqueo or Decision(True, via="transicion")
        return Decision(False, 404, "Vehículo no encontrado.")

    if actor.rol == "admin":
        if accion is Accion.VER:
            return Decision(True, via="inventario")
        if propietario is None:
            return bloqueo or Decision(True, via="inventario")
        if soporte_vigente:
            return Decision(True, via="soporte")
        return Decision(
            False,
            403,
            "La unidad pertenece a un comprador: se requiere su autorización de soporte vigente.",
        )

    # Anónimo
    if obligatoria:
        return Decision(False, 401, "Inicia sesión para usar esta unidad.")
    if propietario is None:
        return bloqueo or Decision(True, via="transicion")
    return Decision(False, 404, "Vehículo no encontrado.")
