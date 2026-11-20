"""Tickets para abrir el WebSocket de una unidad (Etapa 8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import actor_actual, autorizar_unidad
from app.services import acceso_wss
from app.services.autorizacion import Accion, Actor

router = APIRouter(prefix="/api/ws", tags=["WebSocket"])


class TicketEntrada(BaseModel):
    id_vehiculo: int = Field(ge=1)


@router.post("/ticket", responses={401: {}, 403: {}, 404: {}, 409: {}})
def pedir_ticket(datos: TicketEntrada, request: Request, actor: Actor = Depends(actor_actual)) -> dict:
    """Ticket de un solo uso (30 s) para wss://…/ws/vehiculos/{id}?ticket=…

    Exige el mismo permiso que GET /estado (MONITOREAR): el canal publica estado y movimientos.
    """
    autorizar_unidad(request, actor, datos.id_vehiculo, Accion.MONITOREAR)
    return {
        "ticket": acceso_wss.emitir_ticket(actor, datos.id_vehiculo),
        "id_vehiculo": datos.id_vehiculo,
        "vigencia_s": acceso_wss.TICKET_VIGENCIA_S,
    }
