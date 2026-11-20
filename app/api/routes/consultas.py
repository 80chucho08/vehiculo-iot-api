from typing import Any

import pymysql
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import acceso_demo, acceso_vehiculo
from app.core.websocket_manager import websocket_manager
from app.db.connection import get_connection
from app.db.procedure_executor import execute_procedure
from app.repositories.demos import visible_para
from app.services.autorizacion import Accion, Actor

router = APIRouter(
    prefix="/api",
    tags=["Consultas IoT"],
)


def consultar_procedimiento(
    nombre: str,
    parametros: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    connection = None

    try:
        connection = get_connection()

        resultados = execute_procedure(
            connection,
            nombre,
            parametros,
        )

        return resultados[0] if resultados else []

    except pymysql.MySQLError:
        raise HTTPException(
            status_code=500,
            detail="Error al consultar la información en la base de datos.",
        )

    finally:
        if connection is not None:
            connection.close()


@router.get("/vehiculos/{id_vehiculo}", dependencies=[Depends(acceso_vehiculo(Accion.VER))])
def obtener_vehiculo(id_vehiculo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_vehiculo",
        (id_vehiculo,),
    )

    if not resultados:
        raise HTTPException(
            status_code=404,
            detail="Vehículo no encontrado.",
        )

    return resultados[0]


@router.get("/vehiculos/{id_vehiculo}/comandos", dependencies=[Depends(acceso_vehiculo(Accion.VER))])
def obtener_comandos(id_vehiculo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_comandos_vehiculo",
        (id_vehiculo,),
    )

    return {
        "total": len(resultados),
        "comandos": resultados,
    }


@router.get("/vehiculos/{id_vehiculo}/estado", dependencies=[Depends(acceso_vehiculo(Accion.MONITOREAR))])
def obtener_estado(id_vehiculo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_estado",
        (id_vehiculo,),
    )

    return {
        "total": len(resultados),
        "estado": resultados,
        "conectado_websocket": (
            websocket_manager.connected_clients(id_vehiculo, "dispositivo") > 0
        ),
    }


@router.get("/vehiculos/{id_vehiculo}/historial", dependencies=[Depends(acceso_vehiculo(Accion.MONITOREAR))])
def obtener_historial(id_vehiculo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_historial",
        (id_vehiculo,),
    )

    return {
        "total": len(resultados),
        "historial": resultados,
    }


@router.get("/vehiculos/{id_vehiculo}/demos")
def obtener_demos(id_vehiculo: int, actor: Actor = Depends(acceso_vehiculo(Accion.VER))):
    """Demos de fábrica y, de las privadas, solo las del usuario que consulta."""
    resultados = [
        demo for demo in consultar_procedimiento("sp_obtener_demos", (id_vehiculo,))
        if visible_para(demo, actor.id_usuario)
    ]

    return {
        "total": len(resultados),
        "demos": resultados,
    }


@router.get("/demos/{id_demo}/movimientos", dependencies=[Depends(acceso_demo(Accion.VER))])
def obtener_movimientos_demo(id_demo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_movimientos_demo",
        (id_demo,),
    )

    return {
        "total": len(resultados),
        "movimientos": resultados,
    }
