import logging

from app.api.routes.vehiculos import router as vehiculos_router
from app.api.routes.consultas import router as consultas_router
from app.api.routes.operaciones import router as operaciones_router

import pymysql
from fastapi import FastAPI, HTTPException

from app.core.config import settings
from app.db.connection import get_connection


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    description="API local para administrar vehículos IoT.",
    version="1.0.0",
)

app.include_router(vehiculos_router)
app.include_router(consultas_router)
app.include_router(operaciones_router)


@app.get("/", tags=["General"])
def inicio():
    return {
        "mensaje": "API Vehiculo IoT funcionando",
        "entorno": settings.app_env,
        "documentacion": "/docs",
    }


@app.get("/health/db", tags=["General"])
def comprobar_rds():
    """Comprueba la conexión y consulta información básica de MySQL."""
    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT DATABASE() AS base_datos, "
                "VERSION() AS version_mysql"
            )
            resultado = cursor.fetchone()

        return {
            "conexion": "exitosa",
            "base_datos": resultado["base_datos"],
            "version_mysql": resultado["version_mysql"],
        }

    except pymysql.MySQLError:
        logger.exception("Falló la comprobación de conexión a RDS.")
        raise HTTPException(
            status_code=503,
            detail="No fue posible conectar con la base de datos.",
        ) from None

    finally:
        if connection is not None:
            connection.close()
