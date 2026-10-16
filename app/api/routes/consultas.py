from typing import Any

import pymysql
from fastapi import APIRouter, HTTPException

from app.db.connection import get_connection
from app.db.procedure_executor import execute_procedure

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


@router.get("/vehiculos/{id_vehiculo}")
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


@router.get("/vehiculos/{id_vehiculo}/comandos")
def obtener_comandos(id_vehiculo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_comandos_vehiculo",
        (id_vehiculo,),
    )

    return {
        "total": len(resultados),
        "comandos": resultados,
    }


@router.get("/vehiculos/{id_vehiculo}/estado")
def obtener_estado(id_vehiculo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_estado",
        (id_vehiculo,),
    )

    return {
        "total": len(resultados),
        "estado": resultados,
    }


@router.get("/vehiculos/{id_vehiculo}/historial")
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
def obtener_demos(id_vehiculo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_demos",
        (id_vehiculo,),
    )

    return {
        "total": len(resultados),
        "demos": resultados,
    }


@router.get("/demos/{id_demo}/movimientos")
def obtener_movimientos_demo(id_demo: int):
    resultados = consultar_procedimiento(
        "sp_obtener_movimientos_demo",
        (id_demo,),
    )

    return {
        "total": len(resultados),
        "movimientos": resultados,
    }