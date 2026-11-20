"""Catálogo público y prueba temporal de invitado sobre una unidad de exhibición (Etapa 7)."""

from __future__ import annotations

from datetime import datetime

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import actor_actual, ip_hash
from app.core import seguridad
from app.core.config import settings
from app.core.websocket_manager import websocket_manager
from app.repositories import auditoria, invitados
from app.repositories.base import Conflicto
from app.services.autorizacion import Actor
from app.services.vigilante import finalizar_reserva

router = APIRouter(tags=["Catálogo e invitados"])


class ReservaEntrada(BaseModel):
    id_vehiculo: int = Field(ge=1)
    acepto_condiciones: bool = Field(description="Confirma supervisión, zona despejada y que DETENER detiene todo")


class ReservaSalida(BaseModel):
    access_token: str
    token_type: str = "bearer"
    id_vehiculo: int
    expira_en: datetime
    minutos: int


def _bd(funcion, *args):
    try:
        return funcion(*args)
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "La base de datos no respondió.") from None


@router.get("/api/catalogo")
def catalogo() -> dict:
    """Público: modelos publicados y sus unidades de exhibición, con disponibilidad. Sin datos privados."""
    unidades = _bd(invitados.catalogo)
    for u in unidades:
        u["disponible"] = u.pop("ocupada_hasta") is None
        u["dispositivo_conectado"] = websocket_manager.connected_clients(u["id_vehiculo"], "dispositivo") > 0
    return {"total": len(unidades), "unidades": unidades, "minutos_prueba": settings.invitado_minutos}


@router.post("/api/invitado/sesiones", status_code=status.HTTP_201_CREATED, response_model=ReservaSalida,
             responses={400: {}, 404: {}, 409: {"description": "Unidad ocupada"}, 429: {}})
def reservar(datos: ReservaEntrada, request: Request) -> ReservaSalida:
    """Reserva EXCLUSIVA y temporal de una unidad de exhibición. No requiere cuenta."""
    if not datos.acepto_condiciones:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Debes aceptar las condiciones de seguridad de la prueba.")
    huella = ip_hash(request)
    unidad = _bd(invitados.unidad_reservable, datos.id_vehiculo)
    if unidad is None or unidad["tipo_unidad"] != "exhibicion" or not unidad["activo"] or not unidad["publicado"]:
        # Nunca se revela si existe una unidad vendida con ese id.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unidad de exhibición no encontrada.")
    if huella and _bd(invitados.reservas_recientes_ip, huella, settings.invitado_ventana_minutos) >= settings.invitado_reservas_por_ip:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Alcanzaste el límite de pruebas. Intenta más tarde.",
                            headers={"Retry-After": str(settings.invitado_ventana_minutos * 60)})
    sid = seguridad.nuevo_sid()
    try:
        reserva = invitados.crear(sid, datos.id_vehiculo, settings.invitado_minutos, huella)
    except Conflicto as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No fue posible crear la reserva.") from None
    auditoria.registrar("invitado.reserva", "vehiculo", id_actor=None, rol_actor="invitado", id_recurso=datos.id_vehiculo,
                        detalle={"id_sesion_invitado": reserva["id_sesion_invitado"], "minutos": settings.invitado_minutos},
                        ip_hash=huella)
    return ReservaSalida(access_token=seguridad.emitir_token_invitado(sid, reserva["expira_en"]),
                         id_vehiculo=datos.id_vehiculo, expira_en=reserva["expira_en"], minutos=settings.invitado_minutos)


@router.get("/api/invitado/sesion")
def mi_reserva(actor: Actor = Depends(actor_actual)) -> dict:
    if actor.rol != "invitado":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No hay una prueba de invitado activa.")
    reserva = _bd(invitados.obtener_vigente, actor.sid)
    if reserva is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "La prueba terminó.")
    return {"id_vehiculo": reserva["id_vehiculo"], "expira_en": reserva["expira_en"]}


@router.delete("/api/invitado/sesion", status_code=status.HTTP_204_NO_CONTENT)
async def terminar(actor: Actor = Depends(actor_actual)) -> Response:
    """Termina la prueba: libera la unidad y la DETIENE."""
    if actor.rol == "invitado":
        await finalizar_reserva(actor.sid, "finalizada")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
