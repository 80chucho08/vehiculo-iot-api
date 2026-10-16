from fastapi import APIRouter, HTTPException
import pymysql

from app.db.connection import get_connection
from app.db.procedure_executor import execute_procedure

router = APIRouter(
    prefix="/api/vehiculos",
    tags=["Vehículos"],
)


@router.get("")
def obtener_vehiculos():
    connection = None

    try:
        connection = get_connection()

        resultados = execute_procedure(
            connection,
            "sp_obtener_vehiculos",
        )

        vehiculos = resultados[0] if resultados else []

        return {
            "total": len(vehiculos),
            "vehiculos": vehiculos,
        }

    except pymysql.MySQLError:
        raise HTTPException(
            status_code=500,
            detail="Error al consultar los vehículos.",
        )

    finally:
        if connection is not None:
            connection.close()