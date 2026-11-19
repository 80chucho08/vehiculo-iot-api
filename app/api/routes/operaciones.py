
import asyncio
import logging
import uuid
from typing import Any

import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import acceso_vehiculo, admin_auditado
from app.core.websocket_manager import websocket_manager
from app.db.bajas_logicas import desactivar_comando, desactivar_vehiculo
from app.db.connection import get_connection
from app.db.operation_executor import ejecutar_operacion
from app.db.procedure_executor import execute_procedure
from app.services.autorizacion import Accion
from app.services.demo_executor import demo_execution_manager


router = APIRouter(
    prefix="/api",
    tags=["Operaciones IoT"],
)
logger = logging.getLogger(__name__)


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


# Tope de seguridad física por paso y por pausa; coincide con los CHECK de la migración V004.
MAX_PASO_DEMO_MS = 10_000


class MovimientoDemoCrear(BaseModel):
    id_comando: int
    orden: int = Field(ge=1)
    duracion_ms: int = Field(ge=0, le=MAX_PASO_DEMO_MS)
    pausa_ms: int = Field(ge=0, le=MAX_PASO_DEMO_MS)


class MovimientoDemoActualizar(BaseModel):
    id_comando: int
    orden: int = Field(ge=1)
    duracion_ms: int = Field(ge=0, le=MAX_PASO_DEMO_MS)
    pausa_ms: int = Field(ge=0, le=MAX_PASO_DEMO_MS)

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
) -> dict[str, Any] | int:
    id_solicitud = mensaje["id_solicitud"]
    receptores = websocket_manager.receptores(id_vehiculo)
    num_receptores = len(receptores)

    # a. Si hay dispositivo: intenta el flujo actual.
    if websocket_manager.connected_clients(id_vehiculo, "dispositivo") > 0:
        espera = websocket_manager.crear_espera_ack(id_solicitud)
        logger.info(
            "ACK registrado id_solicitud=%s id_vehiculo=%s tipo=%s timeout_s=%.1f",
            id_solicitud,
            id_vehiculo,
            mensaje.get("tipo"),
            timeout,
        )
        try:
            enviados = await websocket_manager.send_to_vehicle(
                id_vehiculo,
                mensaje,
                role="dispositivo",
            )
            logger.info(
                "Envío WebSocket completado id_solicitud=%s id_vehiculo=%s conexiones_aceptadas=%s",
                id_solicitud,
                id_vehiculo,
                enviados,
            )
            if enviados > 0:
                logger.info(
                    "Ruta de comando=%s id_vehiculo=%s receptores=%s id_solicitud=%s",
                    "dispositivo",
                    id_vehiculo,
                    num_receptores,
                    id_solicitud,
                )
                try:
                    return await asyncio.wait_for(espera, timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Timeout esperando ACK id_solicitud=%s id_vehiculo=%s timeout_s=%.1f",
                        id_solicitud,
                        id_vehiculo,
                        timeout,
                    )
                    raise HTTPException(
                        status_code=504,
                        detail="El dispositivo no confirmó la recepción del comando.",
                    ) from None
            else:
                logger.warning(
                    "Envío a dispositivo falló o socket muerto id_solicitud=%s id_vehiculo=%s. Continuando con ruta alterna.",
                    id_solicitud,
                    id_vehiculo,
                )
        finally:
            websocket_manager.quitar_espera_ack(id_solicitud)

    # b. Si no hay dispositivo (o no había conexión viva) y hay receptores:
    receptores_vivos = websocket_manager.receptores(id_vehiculo)
    if receptores_vivos:
        enviados = await websocket_manager.enviar_a_observadores(id_vehiculo, mensaje)
        if enviados > 0:
            logger.info(
                "Ruta de comando=%s id_vehiculo=%s receptores=%s id_solicitud=%s",
                "observadores",
                id_vehiculo,
                enviados,
                id_solicitud,
            )
            return enviados

    # c. Si no hay dispositivo ni receptores:
    logger.info(
        "Ruta de comando=%s id_vehiculo=%s receptores=%s id_solicitud=%s",
        "ninguna",
        id_vehiculo,
        len(websocket_manager.receptores(id_vehiculo)),
        id_solicitud,
    )
    raise HTTPException(
        status_code=409,
        detail="No hay un dispositivo WebSocket conectado.",
    )


# VEHÍCULOS

@router.post("/vehiculos", status_code=201, dependencies=[Depends(admin_auditado("vehiculo.alta", "vehiculo"))])
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


@router.put("/vehiculos/{id_vehiculo}", dependencies=[Depends(admin_auditado("vehiculo.actualizar", "vehiculo", "id_vehiculo"))])
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


@router.delete("/vehiculos/{id_vehiculo}", dependencies=[Depends(admin_auditado("vehiculo.baja", "vehiculo", "id_vehiculo"))])
def eliminar_vehiculo(id_vehiculo: int):
    """Baja lógica: marca activo = 0 y conserva comandos, demos e historial."""
    try:
        activo_anterior = desactivar_vehiculo(id_vehiculo)
    except pymysql.MySQLError:
        raise HTTPException(
            status_code=500,
            detail="No fue posible dar de baja el vehículo.",
        ) from None

    if activo_anterior is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")

    return {
        "mensaje": (
            "Vehículo dado de baja (activo = 0); su historial se conserva."
            if activo_anterior
            else "El vehículo ya estaba dado de baja."
        ),
        "id_vehiculo": id_vehiculo,
        "activo": False,
        "resultados": [],
    }


# COMANDOS

@router.post("/vehiculos/{id_vehiculo}/comandos", status_code=201, dependencies=[Depends(admin_auditado("comando.crear", "vehiculo", "id_vehiculo"))])
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


@router.put("/comandos/{id_comando}", dependencies=[Depends(admin_auditado("comando.actualizar", "comando", "id_comando"))])
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


@router.delete("/comandos/{id_comando}", dependencies=[Depends(admin_auditado("comando.desactivar", "comando", "id_comando"))])
def eliminar_comando(id_comando: int):
    """Baja lógica: un DELETE físico dejaría historial con comando NULL (= DETENER)."""
    try:
        activo_anterior = desactivar_comando(id_comando)
    except pymysql.MySQLError:
        raise HTTPException(
            status_code=500,
            detail="No fue posible desactivar el comando.",
        ) from None

    if activo_anterior is None:
        raise HTTPException(status_code=404, detail="Comando no encontrado.")

    return {
        "mensaje": (
            "Comando desactivado (activo = 0); el historial se conserva."
            if activo_anterior
            else "El comando ya estaba desactivado."
        ),
        "id_comando": id_comando,
        "activo": False,
        "resultados": [],
    }


# DEMOS

@router.post("/vehiculos/{id_vehiculo}/demos", status_code=201, dependencies=[Depends(admin_auditado("demo.crear", "vehiculo", "id_vehiculo"))])
def crear_demo(id_vehiculo: int, datos: DemoCrear):
    resultados = ejecutar_operacion(
        "sp_crear_demo",
        (id_vehiculo, datos.nombre, datos.descripcion),
    )
    return {
        "mensaje": "Operación de creación de demo ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.put("/demos/{id_demo}", dependencies=[Depends(admin_auditado("demo.actualizar", "demo", "id_demo"))])
def actualizar_demo(id_demo: int, datos: DemoActualizar):
    resultados = ejecutar_operacion(
        "sp_actualizar_demo",
        (id_demo, datos.nombre, datos.descripcion, int(datos.activo)),
    )
    return {
        "mensaje": "Operación de actualización de demo ejecutada correctamente.",
        "resultados": primer_resultado(resultados),
    }


@router.delete("/demos/{id_demo}", dependencies=[Depends(admin_auditado("demo.eliminar", "demo", "id_demo"))])
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

@router.post("/demos/{id_demo}/movimientos", status_code=201, dependencies=[Depends(admin_auditado("demo.paso_agregar", "demo", "id_demo"))])
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


@router.put("/movimientos-demo/{id_demo_movimiento}", dependencies=[Depends(admin_auditado("demo.paso_actualizar", "demo_movimiento", "id_demo_movimiento"))])
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


@router.delete("/movimientos-demo/{id_demo_movimiento}", dependencies=[Depends(admin_auditado("demo.paso_eliminar", "demo_movimiento", "id_demo_movimiento"))])
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

@router.put("/vehiculos/{id_vehiculo}/estado", dependencies=[Depends(admin_auditado("estado.forzar", "vehiculo", "id_vehiculo"))])
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


@router.post("/vehiculos/{id_vehiculo}/comandos/{id_comando}/ejecutar", dependencies=[Depends(acceso_vehiculo(Accion.OPERAR))])
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
        if demo_execution_manager.deteniendo(id_vehiculo):
            raise HTTPException(
                status_code=409,
                detail="Se está ejecutando DETENER para este vehículo.",
            )
        demo_execution_manager.individual_commands.add(id_vehiculo)
        terminado = asyncio.Event()
        demo_execution_manager.individual_done[id_vehiculo] = terminado

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
        alterno = isinstance(ack, int)
        if not alterno and ack.get("estado") != "procesado":
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
        await websocket_manager.send_to_vehicle(
            id_vehiculo,
            {
                "tipo": "comando_individual_completado",
                "id_vehiculo": id_vehiculo,
                "id_solicitud": id_solicitud,
                "id_comando": id_comando,
                "comando": comando["nombre"],
                "estado": "entregado_a_observadores" if alterno else "procesado_por_firmware",
                "mensaje": (
                    "El comando se entregó a observadores; sin confirmación de firmware."
                    if alterno
                    else "El comando se ejecutó correctamente."
                ),
            },
            role="observador",
        )
        if alterno:
            return {
                "mensaje": f"No hay dispositivo conectado; el comando se entregó a {ack} cliente(s) WebSocket.",
                "id_solicitud": id_solicitud,
                "estado": "entregado_a_observadores",
                "resultados": [],
            }
        return {
            "mensaje": "El firmware aceptó y despachó el comando.",
            "id_solicitud": id_solicitud,
            "estado": "procesado_por_firmware",
            "resultados": primer_resultado(resultados),
        }
    finally:
        terminado.set()
        async with demo_execution_manager.lock:
            demo_execution_manager.individual_commands.discard(id_vehiculo)
            if demo_execution_manager.individual_done.get(id_vehiculo) is terminado:
                demo_execution_manager.individual_done.pop(id_vehiculo, None)


# Tiempo máximo que DETENER espera a que un comando individual en vuelo
# registre su fila, para que la fila de DETENER quede después en el historial.
# La orden física de apagado NO espera: se envía antes.
ESPERA_REGISTRO_PREVIO_S = 5.0


@router.post("/vehiculos/{id_vehiculo}/detener", dependencies=[Depends(acceso_vehiculo(Accion.OPERAR))])
async def detener_vehiculo(id_vehiculo: int):
    """DETENER tiene prioridad: cancela la demo en curso y no espera a otro comando.

    Nunca responde 409 por una demo o por un comando individual pendiente.
    Solo responde 409 si no hay ningún cliente WebSocket que reciba la orden
    o si el firmware la rechaza, y 504 si el firmware no confirma.
    """
    manager = demo_execution_manager
    async with manager.lock:
        manager.stopping[id_vehiculo] = manager.stopping.get(id_vehiculo, 0) + 1
        hay_demo = manager.demo_activa(id_vehiculo)

    try:
        demo_cancelada = False
        if hay_demo:
            # cancelar() envía APAGAR_TODO al dispositivo y demo_cancelada a todos.
            try:
                await manager.cancelar(id_vehiculo)
                demo_cancelada = True
            except RuntimeError:
                # La demo terminó entre la comprobación y la cancelación.
                pass
        return await _enviar_detener(id_vehiculo, demo_cancelada)
    finally:
        async with manager.lock:
            restantes = manager.stopping.get(id_vehiculo, 1) - 1
            if restantes > 0:
                manager.stopping[id_vehiculo] = restantes
            else:
                manager.stopping.pop(id_vehiculo, None)


async def _esperar_registro_previo(id_vehiculo: int) -> None:
    terminado = demo_execution_manager.individual_done.get(id_vehiculo)
    if terminado is None:
        return
    try:
        await asyncio.wait_for(terminado.wait(), timeout=ESPERA_REGISTRO_PREVIO_S)
    except asyncio.TimeoutError:
        logger.warning(
            "DETENER registra sin esperar al comando previo id_vehiculo=%s",
            id_vehiculo,
        )


async def _enviar_detener(id_vehiculo: int, demo_cancelada: bool) -> dict[str, Any]:
    id_solicitud = str(uuid.uuid4())
    mensaje = {
        "tipo": "comando_individual",
        "id_vehiculo": id_vehiculo,
        "id_solicitud": id_solicitud,
        "id_comando": None,
        "comando": "APAGAR_TODO",
        "codigo": 0,
    }
    # La orden física sale de inmediato, aunque haya un comando individual en vuelo.
    ack = await enviar_comando_y_esperar_ack(id_vehiculo, mensaje)
    alterno = isinstance(ack, int)
    if not alterno and ack.get("estado") != "procesado":
        raise HTTPException(
            status_code=409,
            detail=ack.get("detalle", "El firmware rechazó la orden de apagado."),
        )
    # Solo el REGISTRO espera al comando previo, para conservar el orden real
    # en historial_comandos y que estado_vehiculo termine en DETENER.
    await _esperar_registro_previo(id_vehiculo)
    resultados = await asyncio.to_thread(
        ejecutar_operacion,
        "sp_actualizar_estado",
        (id_vehiculo, None, 1),
        (("sp_registrar_historial", (id_vehiculo, None, "interfaz_web")),),
    )
    if alterno:
        await websocket_manager.send_to_vehicle(
            id_vehiculo,
            {
                "tipo": "comando_individual_completado",
                "id_vehiculo": id_vehiculo,
                "id_solicitud": id_solicitud,
                "id_comando": None,
                "comando": "APAGAR_TODO",
                "estado": "entregado_a_observadores",
                "mensaje": "El comando se entregó a observadores; sin confirmación de firmware.",
            },
            role="observador",
        )
    return {
        "mensaje": f"No hay dispositivo conectado; el comando se entregó a {ack} cliente(s) WebSocket." if alterno else "El firmware aceptó la orden de apagar todas las salidas.",
        "id_solicitud": id_solicitud,
        "estado": "entregado_a_observadores" if alterno else "procesado_por_firmware",
        "demo_cancelada": demo_cancelada,
        "resultados": [] if alterno else primer_resultado(resultados),
    }


# HISTORIAL DE COMANDOS

@router.post("/vehiculos/{id_vehiculo}/historial", status_code=201, dependencies=[Depends(admin_auditado("historial.registrar_manual", "vehiculo", "id_vehiculo"))])
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
