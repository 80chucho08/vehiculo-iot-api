import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.invitado import router as invitado_router
from app.api.routes.wss import router as wss_router
from app.services.acceso_wss import revalidar_conexiones
from app.services.vigilante import vigilar
from app.api.routes.mis_vehiculos import router as mis_vehiculos_router
from app.api.routes.vehiculos import router as vehiculos_router
from app.api.routes.consultas import router as consultas_router
from app.api.routes.operaciones import router as operaciones_router
from app.api.routes.websocket import router as websocket_router

from app.api.routes.ejecucion_demos import (
    router as ejecucion_demos_router,
)

import pymysql
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.connection import get_connection

from fastapi.middleware.cors import CORSMiddleware


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    """Arranca el vigilante (reservas vencidas y revalidación de WSS) y lo detiene al apagar."""
    tarea = asyncio.create_task(vigilar(revalidar_conexiones))
    try:
        yield
    finally:
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea


app = FastAPI(
    lifespan=ciclo_de_vida,
    title=settings.app_name,
    description=(
        "API de CONTROL, MONITOREO y ADMINISTRACIÓN de vehículos IoT. "
        "Usa **Authorize** con el token de `POST /api/auth/login`. "
        "CORS no sustituye la autorización: cada ruta comprueba rol y propiedad de la unidad."
    ),
    version="1.2.0",
)

ORIGENES_PERMITIDOS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://80chucho08.github.io",   # solo el origen, sin la subruta del repo
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENES_PERMITIDOS,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    # Authorization: token Bearer. Sin cookies (allow_credentials=False): el token viaja en la cabecera.
    allow_headers=["Accept", "Content-Type", "Authorization"],
    expose_headers=["Retry-After"],
    allow_credentials=False,
)



@app.exception_handler(pymysql.MySQLError)
async def error_base_datos(_request, error: pymysql.MySQLError):
    """Cualquier error de MySQL no capturado → 503 en JSON. Al manejarse aquí (dentro de CORS),
    el navegador recibe las cabeceras CORS y ve el error real en vez de "no se pudo conectar"."""
    logger.error("Error de base de datos no controlado: %s", error)
    return JSONResponse(status_code=503, content={"detail": "La base de datos no respondió correctamente. Intenta de nuevo."})


app.include_router(auth_router)
app.include_router(invitado_router)
app.include_router(wss_router)
app.include_router(admin_router)
app.include_router(mis_vehiculos_router)
app.include_router(vehiculos_router)
app.include_router(consultas_router)
app.include_router(operaciones_router)
app.include_router(websocket_router)
app.include_router(ejecucion_demos_router)


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
