"""ADMINISTRACIÓN: usuarios, inventario, asignaciones, métricas y auditoría. Solo rol admin."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import auditar, requiere_admin
from app.api.routes.auth import PATRON_EMAIL
from app.core import seguridad
from app.core.websocket_manager import websocket_manager
from app.repositories import auditoria, propiedad, sesiones, usuarios
from app.repositories.base import Conflicto, todas
from app.services.autorizacion import Actor

router = APIRouter(prefix="/api/admin", tags=["Administración"], dependencies=[Depends(requiere_admin)])

Rol = Literal["admin", "usuario"]


class Pagina(BaseModel):
    total: int
    pagina: int
    por_pagina: int
    items: list[dict]


class UsuarioCrear(BaseModel):
    email: str = Field(min_length=3, max_length=120, pattern=PATRON_EMAIL)
    nombre: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=128)
    rol: Rol = "usuario"


class UsuarioActualizar(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    rol: Rol
    activo: bool


class PasswordAsignar(BaseModel):
    password: str = Field(min_length=10, max_length=128)


class AsignacionCrear(BaseModel):
    id_vehiculo: int = Field(ge=1)
    id_usuario: int = Field(ge=1)


class AsignacionFinalizar(BaseModel):
    motivo: str = Field(min_length=3, max_length=255)


def _bd(funcion, *args):
    try:
        return funcion(*args)
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Error al consultar la base de datos.") from None


# ---------- Usuarios ----------

@router.get("/usuarios", response_model=Pagina)
def listar_usuarios(
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(20, ge=1, le=100),
    rol: Rol | None = None,
    activo: bool | None = None,
    buscar: str | None = Query(None, max_length=80),
) -> Pagina:
    total, items = _bd(usuarios.listar, pagina, por_pagina, rol, activo, buscar)
    return Pagina(total=total, pagina=pagina, por_pagina=por_pagina, items=items)


@router.post("/usuarios", status_code=status.HTTP_201_CREATED, responses={409: {"description": "Email repetido"}})
def crear_usuario(datos: UsuarioCrear, request: Request, actor: Actor = Depends(requiere_admin)) -> dict:
    email = datos.email.strip().lower()
    try:
        id_usuario = usuarios.crear(email, datos.nombre.strip(), seguridad.hash_password(datos.password), datos.rol)
    except Conflicto as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    auditar(request, actor, "usuario.crear", "usuario", id_usuario, detalle={"email": email, "rol": datos.rol})
    return _bd(usuarios.por_id, id_usuario)


@router.put("/usuarios/{id_usuario}", responses={404: {}, 409: {"description": "Dejaría sin administradores"}})
def actualizar_usuario(id_usuario: int, datos: UsuarioActualizar, request: Request,
                       actor: Actor = Depends(requiere_admin)) -> dict:
    actual = _bd(usuarios.por_id, id_usuario)
    if actual is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado.")
    pierde_admin = actual["rol"] == "admin" and actual["activo"] and (datos.rol != "admin" or not datos.activo)
    if pierde_admin and _bd(usuarios.contar_admins_activos) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "No se puede quitar o desactivar al último administrador activo.")
    anterior = _bd(usuarios.actualizar, id_usuario, datos.nombre.strip(), datos.rol, datos.activo)
    cambios = {k: {"antes": anterior[k], "despues": v} for k, v in
               (("nombre", datos.nombre.strip()), ("rol", datos.rol), ("activo", int(datos.activo)))
               if anterior[k] != v}
    if "rol" in cambios or "activo" in cambios:
        # El rol se relee en cada petición, pero además se cierran las sesiones abiertas.
        _bd(sesiones.revocar_de_usuario, id_usuario, "cambio_rol_o_estado")
    auditar(request, actor, "usuario.actualizar", "usuario", id_usuario, detalle=cambios)
    return _bd(usuarios.por_id, id_usuario)


@router.put("/usuarios/{id_usuario}/password", status_code=status.HTTP_204_NO_CONTENT)
def asignar_password(id_usuario: int, datos: PasswordAsignar, request: Request,
                     actor: Actor = Depends(requiere_admin)) -> Response:
    """Recuperación de acceso: el admin fija una contraseña temporal y se cierran las sesiones del usuario."""
    if _bd(usuarios.por_id, id_usuario) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado.")
    _bd(usuarios.cambiar_password, id_usuario, seguridad.hash_password(datos.password))
    _bd(sesiones.revocar_de_usuario, id_usuario, "password_restablecida")
    auditar(request, actor, "usuario.restablecer_password", "usuario", id_usuario)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Inventario y asignaciones ----------

@router.get("/inventario")
def inventario() -> dict:
    unidades = _bd(propiedad.inventario)
    for unidad in unidades:
        unidad["dispositivo_conectado"] = websocket_manager.connected_clients(unidad["id_vehiculo"], "dispositivo") > 0
    return {"total": len(unidades), "unidades": unidades}


@router.get("/asignaciones", response_model=Pagina)
def listar_asignaciones(
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(20, ge=1, le=100),
    id_vehiculo: int | None = Query(None, ge=1),
    id_usuario: int | None = Query(None, ge=1),
    vigentes: bool | None = None,
) -> Pagina:
    total, items = _bd(propiedad.listar_asignaciones, pagina, por_pagina, id_vehiculo, id_usuario, vigentes)
    return Pagina(total=total, pagina=pagina, por_pagina=por_pagina, items=items)


@router.post("/asignaciones", status_code=status.HTTP_201_CREATED,
             responses={404: {}, 409: {"description": "Unidad ya asignada, de exhibición o dada de baja"}})
def crear_asignacion(datos: AsignacionCrear, request: Request, actor: Actor = Depends(requiere_admin)) -> dict:
    try:
        id_asignacion = propiedad.crear_asignacion(datos.id_vehiculo, datos.id_usuario, actor.id_usuario)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    except Conflicto as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Error al registrar la asignación.") from None
    auditar(request, actor, "asignacion.crear", "vehiculo", datos.id_vehiculo,
            detalle={"id_asignacion": id_asignacion, "id_usuario": datos.id_usuario})
    return {"id_asignacion": id_asignacion, "id_vehiculo": datos.id_vehiculo, "id_usuario": datos.id_usuario}


@router.post("/asignaciones/{id_asignacion}/finalizar", responses={404: {}, 409: {}})
def finalizar_asignacion(id_asignacion: int, datos: AsignacionFinalizar, request: Request,
                         actor: Actor = Depends(requiere_admin)) -> dict:
    try:
        fila = propiedad.finalizar_asignacion(id_asignacion, actor.id_usuario, datos.motivo.strip())
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    except Conflicto as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Error al finalizar la asignación.") from None
    auditar(request, actor, "asignacion.finalizar", "vehiculo", fila["id_vehiculo"],
            detalle={"id_asignacion": id_asignacion, "id_usuario": fila["id_usuario"], "motivo": datos.motivo})
    return {"id_asignacion": id_asignacion, "estado": "finalizada"}


# ---------- Dashboard y auditoría ----------

@router.get("/dashboard")
def dashboard() -> dict:
    """Métricas reales de la base (sin datos de ejemplo) y del proceso WSS."""
    consultas = {
        "unidades": "SELECT tipo_unidad, activo, COUNT(*) AS n FROM vehiculos GROUP BY tipo_unidad, activo",
        "usuarios": "SELECT rol, activo, COUNT(*) AS n FROM usuarios GROUP BY rol, activo",
        "resumen": (
            "SELECT (SELECT COUNT(*) FROM asignaciones WHERE fin IS NULL) AS unidades_vendidas,"
            " (SELECT COUNT(*) FROM modelos_vehiculo WHERE activo = 1) AS modelos_activos,"
            " (SELECT COUNT(*) FROM demos WHERE activo = 1) AS demos_activas,"
            " (SELECT COUNT(*) FROM sesiones_invitado WHERE finalizada_en IS NULL AND expira_en > UTC_TIMESTAMP()) AS reservas_invitado_activas,"
            " (SELECT COUNT(*) FROM autorizaciones_soporte WHERE revocada_en IS NULL AND expira_en > UTC_TIMESTAMP()) AS soportes_vigentes,"
            " (SELECT COUNT(*) FROM historial_comandos WHERE fecha_hora >= UTC_TIMESTAMP() - INTERVAL 24 HOUR) AS movimientos_24h,"
            " (SELECT COUNT(*) FROM auditoria WHERE resultado = 'denegado' AND fecha >= UTC_TIMESTAMP() - INTERVAL 24 HOUR) AS accesos_denegados_24h"
        ),
    }
    datos = {nombre: _bd(todas, sql) for nombre, sql in consultas.items()}
    resumen = datos["resumen"][0] if datos["resumen"] else {}
    conectados = sum(1 for id_v in list(websocket_manager.connections) if websocket_manager.connected_clients(id_v, "dispositivo") > 0)
    return {
        "generado_en": seguridad.ahora(),
        "unidades_por_tipo": datos["unidades"],
        "usuarios_por_rol": datos["usuarios"],
        **{k: int(v) for k, v in resumen.items()},
        "dispositivos_conectados": conectados,
    }


@router.get("/auditoria", response_model=Pagina)
def listar_auditoria(
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(50, ge=1, le=100),
    accion: str | None = Query(None, max_length=60),
    recurso: str | None = Query(None, max_length=40),
    id_actor: int | None = Query(None, ge=1),
    resultado: Literal["ok", "denegado", "error"] | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
) -> Pagina:
    total, items = _bd(auditoria.listar, pagina, por_pagina, accion, recurso, id_actor, resultado, desde, hasta)
    return Pagina(total=total, pagina=pagina, por_pagina=por_pagina, items=items)
