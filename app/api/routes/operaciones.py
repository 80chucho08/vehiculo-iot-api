
from typing import Any

import pymysql
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.connection import get_connection
from app.db.procedure_executor import execute_procedure


router = APIRouter(
    prefix="/api",
    tags=["Operaciones IoT"],
)


# Modelos de validación

class VehiculoCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    modelo: str | None = Field(default=None, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)
    microcontrolador: str | None = Field(default=None, max_length=50)


class VehiculoActualizar(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    modelo: str | None = Field(default=None, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)
    microcontrolador: str | None = Field(default=None, max_length=50)
    activo: bool


class ComandoCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=60)
    codigo: int
    descripcion: str | None = Field(default=None, max_length=255)


class ComandoActualizar(BaseModel):
    nombre: str = Field(min_length=1, max_length=60)
    codigo: int
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool

class DemoCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)


class DemoActualizar(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=255)
    activo: bool


class MovimientoDemoCrear(BaseModel):
    id_comando: int
    orden: int = Field(ge=1)
    duracion_ms: int = Field(ge=0)
    pausa_ms: int = Field(ge=0)


class MovimientoDemoActualizar(BaseModel):
    id_comando: int
    orden: int = Field(ge=1)
    duracion_ms: int = Field(ge=0)
    pausa_ms: int = Field(ge=0)

class EstadoActualizar(BaseModel):
    id_comando: int | None = None
    conectado: bool


class HistorialRegistrar(BaseModel):
    id_comando: int | None = None
    origen: str = Field(min_length=1, max_length=30)


# Función compartida para operaciones de escritura

def ejecutar_operacion(
    procedimiento: str,
    parametros: tuple[Any, ...],
) -> list[list[dict[str, Any]]]:
    connection = None

    try:
        connection = get_connection()

        resultados = execute_procedure(
            connection,
            procedimiento,
            parametros,
        )

        connection.commit()
        return resultados

    except pymysql.MySQLError:
        if connection is not None:
            connection.rollback()

        raise HTTPException(
            status_code=500,
            detail="No fue posible completar la operación en la base de datos.",
        )

    finally:
        if connection is not None:
            connection.close()


def primer_resultado(
    resultados: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return resultados[0] if resultados else []


# VEHÍCULOS

@router.post("/vehiculos", status_code=201)
def crear_vehiculo(datos: VehiculoCrear):
    resultados = ejecutar_operacion(
        "sp_crear_vehiculo",
        (
            datos.nombre,
            datos.modelo,
            datos.descripcion,
            datos.microcontrolador,
        ),
    )

    return {
        "mensaje": "Operación de creación ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.put("/vehiculos/{id_vehiculo}")
def actualizar_vehiculo(
    id_vehiculo: int,
    datos: VehiculoActualizar,
):
    resultados = ejecutar_operacion(
        "sp_actualizar_vehiculo",
        (
            id_vehiculo,
            datos.nombre,
            datos.modelo,
            datos.descripcion,
            datos.microcontrolador,
            int(datos.activo),
        ),
    )

    return {
        "mensaje": "Operación de actualización ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.delete("/vehiculos/{id_vehiculo}")
def eliminar_vehiculo(id_vehiculo: int):
    resultados = ejecutar_operacion(
        "sp_eliminar_vehiculo",
        (id_vehiculo,),
    )

    return {
        "mensaje": "Procedimiento de eliminación ejecutado correctamente.",
        "resultados": primer_resultado(resultados),
    }


# COMANDOS

@router.post("/vehiculos/{id_vehiculo}/comandos", status_code=201)
def crear_comando(
    id_vehiculo: int,
    datos: ComandoCrear,
):
    resultados = ejecutar_operacion(
        "sp_crear_comando",
        (
            id_vehiculo,
            datos.nombre,
            datos.codigo,
            datos.descripcion,
        ),
    )

    return {
        "mensaje": "Operación de creación de comando ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.put("/comandos/{id_comando}")
def actualizar_comando(
    id_comando: int,
    datos: ComandoActualizar,
):
    resultados = ejecutar_operacion(
        "sp_actualizar_comando",
        (
            id_comando,
            datos.nombre,
            datos.codigo,
            datos.descripcion,
            int(datos.activo),
        ),
    )

    return {
        "mensaje": "Operación de actualización de comando ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.delete("/comandos/{id_comando}")
def eliminar_comando(id_comando: int):
    resultados = ejecutar_operacion(
        "sp_eliminar_comando",
        (id_comando,),
    )

    return {
        "mensaje": "Procedimiento de eliminación de comando ejecutado correctamente.",
        "resultados": primer_resultado(resultados),
    }


# DEMOS

@router.post("/vehiculos/{id_vehiculo}/demos", status_code=201)
def crear_demo(id_vehiculo: int, datos: DemoCrear):
    resultados = ejecutar_operacion(
        "sp_crear_demo",
        (id_vehiculo, datos.nombre, datos.descripcion),
    )
    return {
        "mensaje": "Operación de creación de demo ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.put("/demos/{id_demo}")
def actualizar_demo(id_demo: int, datos: DemoActualizar):
    resultados = ejecutar_operacion(
        "sp_actualizar_demo",
        (id_demo, datos.nombre, datos.descripcion, int(datos.activo)),
    )
    return {
        "mensaje": "Operación de actualización de demo ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.delete("/demos/{id_demo}")
def eliminar_demo(id_demo: int):
    resultados = ejecutar_operacion(
        "sp_eliminar_demo",
        (id_demo,),
    )
    return {
        "mensaje": "Procedimiento de eliminación de demo ejecutado correctamente.",
        "resultados": primer_resultado(resultados),
    }


# MOVIMIENTOS DE DEMO

@router.post("/demos/{id_demo}/movimientos", status_code=201)
def agregar_movimiento_demo(id_demo: int, datos: MovimientoDemoCrear):
    resultados = ejecutar_operacion(
        "sp_agregar_movimiento_demo",
        (
            id_demo,
            datos.id_comando,
            datos.orden,
            datos.duracion_ms,
            datos.pausa_ms,
        ),
    )
    return {
        "mensaje": "Operación de creación de movimiento ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.put("/movimientos-demo/{id_demo_movimiento}")
def actualizar_movimiento_demo(
    id_demo_movimiento: int,
    datos: MovimientoDemoActualizar,
):
    resultados = ejecutar_operacion(
        "sp_actualizar_movimiento_demo",
        (
            id_demo_movimiento,
            datos.id_comando,
            datos.orden,
            datos.duracion_ms,
            datos.pausa_ms,
        ),
    )
    return {
        "mensaje": "Operación de actualización de movimiento ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.delete("/movimientos-demo/{id_demo_movimiento}")
def eliminar_movimiento_demo(id_demo_movimiento: int):
    resultados = ejecutar_operacion(
        "sp_eliminar_movimiento_demo",
        (id_demo_movimiento,),
    )
    return {
        "mensaje": "Procedimiento de eliminación de movimiento ejecutado correctamente.",
        "resultados": primer_resultado(resultados),
    }

# ESTADO DEL VEHÍCULO

@router.put("/vehiculos/{id_vehiculo}/estado")
def actualizar_estado(
    id_vehiculo: int,
    datos: EstadoActualizar,
):
    resultados = ejecutar_operacion(
        "sp_actualizar_estado",
        (
            id_vehiculo,
            datos.id_comando,
            int(datos.conectado),
        ),
    )

    return {
        "mensaje": "Operación de actualización de estado ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


# HISTORIAL DE COMANDOS

@router.post("/vehiculos/{id_vehiculo}/historial", status_code=201)
def registrar_historial(
    id_vehiculo: int,
    datos: HistorialRegistrar,
):
    resultados = ejecutar_operacion(
        "sp_registrar_historial",
        (
            id_vehiculo,
            datos.id_comando,
            datos.origen,
        ),
    )

    return {
        "mensaje": "Operación de registro de historial ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }