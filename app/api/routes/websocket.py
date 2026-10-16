
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
import pymysql

from app.core.websocket_manager import websocket_manager
from app.db.connection import get_connection
from app.db.procedure_executor import execute_procedure


router = APIRouter(tags=["WebSocket IoT"])


@router.websocket("/ws/vehiculos/{id_vehiculo}")
async def websocket_vehiculo(
    websocket: WebSocket,
    id_vehiculo: int,
):
    await websocket_manager.connect(id_vehiculo, websocket)

    try:
        # Recuperar el último estado almacenado en RDS.
        connection = get_connection()

        try:
            resultados = execute_procedure(
                connection,
                "sp_obtener_estado",
                (id_vehiculo,),
            )

            estado = resultados[0] if resultados else []

        finally:
            connection.close()

        await websocket.send_json(
            jsonable_encoder({
                "tipo": "estado_inicial",
                "id_vehiculo": id_vehiculo,
                "estado": estado,
            })
        )

        # Mantener abierta la conexión hasta que el cliente se desconecte.
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass

    except pymysql.MySQLError:
        try:
            await websocket.send_json({
                "tipo": "error",
                "mensaje": "No fue posible recuperar el estado del vehículo.",
            })
            await websocket.close(code=1011)
        except Exception:
            pass

    finally:
        websocket_manager.disconnect(id_vehiculo, websocket)
