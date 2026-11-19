"""Mis vehículos: el comprador ve solo SUS unidades y decide si autoriza soporte."""

from __future__ import annotations

from datetime import timedelta

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import auditar, requiere_usuario
from app.core import seguridad
from app.core.websocket_manager import websocket_manager
from app.repositories import propiedad
from app.services.autorizacion import Actor

router = APIRouter(prefix="/api/mis-vehiculos", tags=["Mis vehículos"])


class SoporteOtorgar(BaseModel):
    minutos: int = Field(60, ge=15, le=1440, description="Duración de la autorización (15 min a 24 h)")
    motivo: str = Field(min_length=3, max_length=255)
    id_admin: int | None = Field(None, ge=1, description="Restringir a un administrador; vacío = cualquiera")


def _propia(actor: Actor, id_vehiculo: int) -> None:
    try:
        unidad = propiedad.unidad(id_vehiculo)
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No fue posible verificar la unidad.") from None
    if unidad is None or unidad.get("id_propietario") != actor.id_usuario:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehículo no encontrado.")


@router.get("")
def mis_vehiculos(actor: Actor = Depends(requiere_usuario)) -> dict:
    try:
        unidades = propiedad.mis_vehiculos(actor.id_usuario)
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Error al consultar tus vehículos.") from None
    for unidad in unidades:
        unidad["dispositivo_conectado"] = websocket_manager.connected_clients(unidad["id_vehiculo"], "dispositivo") > 0
    return {"total": len(unidades), "vehiculos": unidades}


@router.post("/{id_vehiculo}/soporte", status_code=status.HTTP_201_CREATED, responses={404: {}})
def otorgar_soporte(id_vehiculo: int, datos: SoporteOtorgar, request: Request,
                    actor: Actor = Depends(requiere_usuario)) -> dict:
    """Consentimiento explícito y con caducidad para que un administrador opere tu unidad."""
    _propia(actor, id_vehiculo)
    expira = seguridad.ahora() + timedelta(minutes=datos.minutos)
    id_autorizacion = propiedad.otorgar_soporte(id_vehiculo, actor.id_usuario, datos.id_admin, datos.motivo.strip(), expira)
    auditar(request, actor, "soporte.otorgar", "vehiculo", id_vehiculo,
            detalle={"id_autorizacion": id_autorizacion, "minutos": datos.minutos, "id_admin": datos.id_admin})
    return {"id_autorizacion": id_autorizacion, "id_vehiculo": id_vehiculo, "expira_en": expira}


@router.delete("/{id_vehiculo}/soporte", status_code=status.HTTP_204_NO_CONTENT, responses={404: {}})
def revocar_soporte(id_vehiculo: int, request: Request, actor: Actor = Depends(requiere_usuario)) -> Response:
    _propia(actor, id_vehiculo)
    revocadas = propiedad.revocar_soporte(id_vehiculo, actor.id_usuario)
    auditar(request, actor, "soporte.revocar", "vehiculo", id_vehiculo, detalle={"revocadas": revocadas})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
