"""Regla única de autorización por unidad (RBAC + propiedad). La usan REST y, en la Etapa 8, WSS.

Acciones:
  VER          datos de catálogo de la unidad: detalle, comandos y demos.
  MONITOREAR   estado e historial (privados del comprador).
  OPERAR       ejecutar comandos, DETENER, iniciar o cancelar demos.

Reglas:
  - Unidad inexistente → 404.
  - Usuario (comprador): todo sobre las unidades con asignación VIGENTE a él. Sobre las ajenas
    responde 404, igual que si no existieran, para no revelar cuáles están vendidas (BOLA/IDOR).
  - Admin: VER siempre. MONITOREAR/OPERAR una unidad VENDIDA solo con una autorización de soporte
    vigente del comprador → si no, 403. Las unidades sin comprador (inventario, exhibición) sí.
  - Anónimo: con AUTH_OBLIGATORIA=false (transición de 4.1/4.2) puede VER/MONITOREAR/OPERAR
    unidades SIN comprador; una unidad vendida nunca. Con AUTH_OBLIGATORIA=true → 401.
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


@dataclass(frozen=True)
class Actor:
    rol: str  # 'admin' | 'usuario' | 'anonimo'
    id_usuario: int | None = None
    sid: str | None = None
    email: str | None = None
    nombre: str | None = None

    @property
    def autenticado(self) -> bool:
        return self.id_usuario is not None


ANONIMO = Actor(rol="anonimo")


@dataclass(frozen=True)
class Decision:
    permitido: bool
    status: int = 200
    detalle: str = ""
    via: str = ""  # propietario | soporte | inventario | transicion


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

    if actor.rol == "usuario":
        if propietario is not None and propietario == actor.id_usuario:
            return Decision(True, via="propietario")
        if propietario is None and not obligatoria:
            return Decision(True, via="transicion")
        return Decision(False, 404, "Vehículo no encontrado.")

    if actor.rol == "admin":
        if accion is Accion.VER or propietario is None:
            return Decision(True, via="inventario")
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
        return Decision(True, via="transicion")
    return Decision(False, 404, "Vehículo no encontrado.")
