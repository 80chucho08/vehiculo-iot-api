"""Mis vehículos: el comprador ve solo SUS unidades y decide si autoriza soporte."""

from __future__ import annotations

from datetime import timedelta

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import auditar, requiere_usuario
from app.core import seguridad
from app.core.websocket_manager import websocket_manager
from app.repositories import demos as repo_demos
from app.repositories import propiedad
from app.services import validacion_demos
from app.services.demo_executor import demo_execution_manager
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


# ---------- Editor de demos privadas (Etapa 6) ----------

class PasoDemo(BaseModel):
    id_comando: int = Field(ge=1)
    duracion_ms: int = Field(ge=100, le=10_000)
    pausa_ms: int = Field(0, ge=0, le=10_000)


class DemoPrivada(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(None, max_length=255)
    activo: bool = True
    pasos: list[PasoDemo] = Field(min_length=1, max_length=50, description="En orden de ejecución")


def _validar(id_vehiculo: int, datos: DemoPrivada) -> list[dict]:
    pasos = [p.model_dump() for p in datos.pasos]
    errores = validacion_demos.errores_pasos(pasos, repo_demos.contexto_unidad(id_vehiculo))
    if errores:
        # Misma forma que los 422 de FastAPI: el frontend ya sabe mostrarla.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=[{"loc": ["body", "pasos"], "msg": e, "type": "demo_insegura"} for e in errores])
    return pasos


def _sin_demo_en_curso(id_vehiculo: int) -> None:
    if demo_execution_manager.demo_activa(id_vehiculo):
        raise HTTPException(status.HTTP_409_CONFLICT, "Hay una demo ejecutándose en esta unidad; detenla antes de editar.")


@router.get("/{id_vehiculo}/demos", responses={404: {}})
def demos_de_mi_unidad(id_vehiculo: int, actor: Actor = Depends(requiere_usuario)) -> dict:
    """Demos de fábrica (solo lectura) y mis demos privadas, con pasos, comandos y límites del modelo."""
    _propia(actor, id_vehiculo)
    contexto = repo_demos.contexto_unidad(id_vehiculo)
    return {
        "demos": repo_demos.demos_con_pasos(id_vehiculo, actor.id_usuario),
        "limites": {**contexto["limites"], "paso_min_ms": validacion_demos.PASO_MIN_MS,
                    "pausa_max_ms": validacion_demos.PAUSA_MAX_MS, "total_max_ms": validacion_demos.TOTAL_MAX_MS},
        "comandos": [c for c in contexto["comandos"] if c["activo"] and 1 <= int(c["codigo"]) <= 12],
    }


@router.post("/{id_vehiculo}/demos", status_code=status.HTTP_201_CREATED, responses={404: {}, 422: {}})
def crear_demo_privada(id_vehiculo: int, datos: DemoPrivada, request: Request,
                       actor: Actor = Depends(requiere_usuario)) -> dict:
    _propia(actor, id_vehiculo)
    pasos = _validar(id_vehiculo, datos)
    id_demo = repo_demos.crear_privada(id_vehiculo, actor.id_usuario, datos.nombre.strip(), datos.descripcion,
                                       datos.activo, pasos)
    auditar(request, actor, "demo.privada_crear", "demo", id_demo,
            detalle={"id_vehiculo": id_vehiculo, "pasos": len(pasos)})
    return {"id_demo": id_demo, "pasos": len(pasos)}


@router.put("/{id_vehiculo}/demos/{id_demo}", responses={404: {}, 409: {}, 422: {}})
def actualizar_demo_privada(id_vehiculo: int, id_demo: int, datos: DemoPrivada, request: Request,
                            actor: Actor = Depends(requiere_usuario)) -> dict:
    """Guarda nombre y la lista COMPLETA de pasos (agregar, quitar, editar y reordenar en una operación)."""
    _propia(actor, id_vehiculo)
    _sin_demo_en_curso(id_vehiculo)
    pasos = _validar(id_vehiculo, datos)
    if not repo_demos.reemplazar_privada(id_demo, id_vehiculo, actor.id_usuario, datos.nombre.strip(),
                                         datos.descripcion, datos.activo, pasos):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Demo no encontrada.")
    auditar(request, actor, "demo.privada_actualizar", "demo", id_demo,
            detalle={"id_vehiculo": id_vehiculo, "pasos": len(pasos), "activo": datos.activo})
    return {"id_demo": id_demo, "pasos": len(pasos)}


@router.delete("/{id_vehiculo}/demos/{id_demo}", status_code=status.HTTP_204_NO_CONTENT, responses={404: {}, 409: {}})
def eliminar_demo_privada(id_vehiculo: int, id_demo: int, request: Request,
                          actor: Actor = Depends(requiere_usuario)) -> Response:
    _propia(actor, id_vehiculo)
    _sin_demo_en_curso(id_vehiculo)
    if not repo_demos.eliminar_privada(id_demo, id_vehiculo, actor.id_usuario):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Demo no encontrada.")
    auditar(request, actor, "demo.privada_eliminar", "demo", id_demo, detalle={"id_vehiculo": id_vehiculo})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
