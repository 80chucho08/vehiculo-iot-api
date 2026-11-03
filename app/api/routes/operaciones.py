
import asyncio
import uuid
from typing import Any

import pymysql
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.websocket_manager import websocket_manager
from app.db.connection import get_connection
from app.db.operation_executor import ejecutar_operacion
from app.db.procedure_executor import execute_procedure
from app.services.demo_executor import demo_execution_manager


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


def primer_resultado(
    resultados: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return resultados[0] if resultados else []


def validar_comando_individual(
    id_vehiculo: int,
    id_comando: int,
) -> dict[str, Any]:
    connection = None
    try:
        connection = get_connection()
        vehiculos = execute_procedure(
            connection,
            "sp_obtener_vehiculo",
            (id_vehiculo,),
        )
        filas_vehiculo = vehiculos[0] if vehiculos else []
        vehiculo = filas_vehiculo[0] if filas_vehiculo else None
        if vehiculo is None:
            raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
        if "activo" not in vehiculo:
            raise HTTPException(
                status_code=500,
                detail="sp_obtener_vehiculo no devuelve el campo activo.",
            )
        if not bool(vehiculo["activo"]):
            raise HTTPException(status_code=409, detail="El vehículo está inactivo.")

        resultado_comandos = execute_procedure(
            connection,
            "sp_obtener_comandos_vehiculo",
            (id_vehiculo,),
        )
        comandos = resultado_comandos[0] if resultado_comandos else []
        comando = next(
            (
                fila
                for fila in comandos
                if int(fila.get("id_comando", -1)) == id_comando
            ),
            None,
        )
        if comando is None:
            raise HTTPException(
                status_code=404,
                detail="El comando no existe o no pertenece al vehículo.",
            )
        if "activo" not in comando:
            raise HTTPException(
                status_code=500,
                detail="sp_obtener_comandos_vehiculo no devuelve el campo activo.",
            )
        if not bool(comando["activo"]):
            raise HTTPException(status_code=409, detail="El comando está inactivo.")
        codigo = int(comando["codigo"])
        if codigo == 13:
            raise HTTPException(
                status_code=409,
                detail="El código 13 inicia una demo y no es un movimiento individual.",
            )
        if not 1 <= codigo <= 12:
            raise HTTPException(status_code=409, detail="Código de movimiento no admitido.")
        return comando
    except HTTPException:
        raise
    except pymysql.MySQLError:
        raise HTTPException(
            status_code=500,
            detail="No fue posible validar el vehículo y el comando.",
        ) from None
    finally:
        if connection is not None:
            connection.close()


async def enviar_comando_y_esperar_ack(
    id_vehiculo: int,
    mensaje: dict[str, Any],
    timeout: float = 3.0,
) -> dict[str, Any]:
    id_solicitud = mensaje["id_solicitud"]
    espera = websocket_manager.crear_espera_ack(id_solicitud)
    try:
        enviados = await websocket_manager.send_to_vehicle(
            id_vehiculo,
            mensaje,
            role="dispositivo",
        )
        if enviados == 0:
            raise HTTPException(
                status_code=409,
                detail="No hay un dispositivo WebSocket conectado.",
            )
        try:
            return await asyncio.wait_for(espera, timeout=timeout)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail="El dispositivo no confirmó la recepción del comando.",
            ) from None
    finally:
        websocket_manager.quitar_espera_ack(id_solicitud)


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
async def actualizar_estado(
    id_vehiculo: int,
    datos: EstadoActualizar,
):
    operaciones_adicionales = ()
    if datos.id_comando is not None:
        operaciones_adicionales = (
            (
                "sp_registrar_historial",
                (id_vehiculo, datos.id_comando, "api"),
            ),
        )

    resultados = await asyncio.to_thread(
        ejecutar_operacion,
        "sp_actualizar_estado",
        (
            id_vehiculo,
            datos.id_comando,
            int(datos.conectado),
        ),
        operaciones_adicionales,
    )

    await websocket_manager.send_to_vehicle(
        id_vehiculo,
        {
            "tipo": "estado_actualizado",
            "id_vehiculo": id_vehiculo,
            "estado": {
                "id_comando": datos.id_comando,
                "conectado": datos.conectado,
            },
        },
    )

    return {
        "mensaje": "Operación de actualización de estado ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.post("/vehiculos/{id_vehiculo}/comandos/{id_comando}/ejecutar")
async def ejecutar_comando_individual(id_vehiculo: int, id_comando: int):
    comando = await asyncio.to_thread(
        validar_comando_individual,
        id_vehiculo,
        id_comando,
    )

    async with demo_execution_manager.lock:
        tarea = demo_execution_manager.tasks.get(id_vehiculo)
        if tarea is not None and not tarea.done():
            raise HTTPException(
                status_code=409,
                detail="No se puede enviar un comando individual durante una demo.",
            )
        if id_vehiculo in demo_execution_manager.individual_commands:
            raise HTTPException(
                status_code=409,
                detail="Ya hay un comando individual pendiente para este vehículo.",
            )
        if websocket_manager.connected_clients(id_vehiculo, "dispositivo") == 0:
            raise HTTPException(
                status_code=409,
                detail="No hay un dispositivo WebSocket conectado.",
            )
        demo_execution_manager.individual_commands.add(id_vehiculo)

    id_solicitud = str(uuid.uuid4())
    mensaje = {
        "tipo": "comando_individual",
        "id_vehiculo": id_vehiculo,
        "id_solicitud": id_solicitud,
        "id_comando": id_comando,
        "comando": comando["nombre"],
        "codigo": int(comando["codigo"]),
    }

    try:
        ack = await enviar_comando_y_esperar_ack(id_vehiculo, mensaje)
        if ack.get("estado") != "procesado":
            raise HTTPException(
                status_code=409,
                detail=ack.get("detalle", "El firmware rechazó el comando."),
            )

        resultados = await asyncio.to_thread(
            ejecutar_operacion,
            "sp_actualizar_estado",
            (id_vehiculo, id_comando, 1),
            (("sp_registrar_historial", (id_vehiculo, id_comando, "interfaz_web")),),
        )
        return {
            "mensaje": "El firmware aceptó y despachó el comando.",
            "id_solicitud": id_solicitud,
            "estado": "procesado_por_firmware",
            "resultados": primer_resultado(resultados),
        }
    finally:
        async with demo_execution_manager.lock:
            demo_execution_manager.individual_commands.discard(id_vehiculo)


@router.post("/vehiculos/{id_vehiculo}/detener")
async def detener_vehiculo(id_vehiculo: int):
    async with demo_execution_manager.lock:
        tarea = demo_execution_manager.tasks.get(id_vehiculo)
        if tarea is not None and not tarea.done():
            raise HTTPException(
                status_code=409,
                detail="Cancela la demo antes de detener manualmente el vehículo.",
            )
        if id_vehiculo in demo_execution_manager.individual_commands:
            raise HTTPException(
                status_code=409,
                detail="Hay un comando individual pendiente para este vehículo.",
            )
        if websocket_manager.connected_clients(id_vehiculo, "dispositivo") == 0:
            raise HTTPException(
                status_code=409,
                detail="No hay un dispositivo WebSocket conectado.",
            )
        demo_execution_manager.individual_commands.add(id_vehiculo)

    id_solicitud = str(uuid.uuid4())
    mensaje = {
        "tipo": "comando_individual",
        "id_vehiculo": id_vehiculo,
        "id_solicitud": id_solicitud,
        "id_comando": None,
        "comando": "APAGAR_TODO",
        "codigo": 0,
    }
    try:
        ack = await enviar_comando_y_esperar_ack(id_vehiculo, mensaje)
        if ack.get("estado") != "procesado":
            raise HTTPException(
                status_code=409,
                detail=ack.get("detalle", "El firmware rechazó la orden de apagado."),
            )
        resultados = await asyncio.to_thread(
            ejecutar_operacion,
            "sp_actualizar_estado",
            (id_vehiculo, None, 1),
            (("sp_registrar_historial", (id_vehiculo, None, "interfaz_web")),),
        )
        return {
            "mensaje": "El firmware aceptó la orden de apagar todas las salidas.",
            "id_solicitud": id_solicitud,
            "estado": "procesado_por_firmware",
            "resultados": primer_resultado(resultados),
        }
    finally:
        async with demo_execution_manager.lock:
            demo_execution_manager.individual_commands.discard(id_vehiculo)


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
