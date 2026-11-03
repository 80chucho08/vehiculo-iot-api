
import json
import logging

import pymysql
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from app.core.websocket_manager import websocket_manager
from app.db.connection import get_connection
from app.db.procedure_executor import execute_procedure


router = APIRouter(tags=["WebSocket IoT"])
logger = logging.getLogger(__name__)


@router.websocket("/ws/vehiculos/{id_vehiculo}")
async def websocket_vehiculo(
    websocket: WebSocket,
    id_vehiculo: int,
):
    rol = websocket.query_params.get("rol", "observador")
    if rol not in {"dispositivo", "observador"}:
        await websocket.close(code=1008)
        return
    if rol == "dispositivo" and websocket_manager.connected_clients(
        id_vehiculo,
        "dispositivo",
    ) > 0:
        await websocket.close(code=1013)
        return

    await websocket_manager.connect(id_vehiculo, websocket, rol)

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

            resultados_demos = execute_procedure(
                connection,
                "sp_obtener_demos",
                (id_vehiculo,),
            )

            demos = resultados_demos[0] if resultados_demos else []

        finally:
            connection.close()

        await websocket.send_json(
            jsonable_encoder({
                "tipo": "estado_inicial",
                "id_vehiculo": id_vehiculo,
                "estado": estado,
                "demos": demos,
            })
        )

        if rol == "dispositivo":
            await websocket_manager.send_to_vehicle(
                id_vehiculo,
                {
                    "tipo": "dispositivo_estado",
                    "id_vehiculo": id_vehiculo,
                    "conectado": True,
                },
                role="observador",
            )

        # Los ACK relacionan el despacho del firmware con una solicitud REST.
        while True:
            texto = await websocket.receive_text()
            try:
                mensaje = json.loads(texto)
            except (TypeError, json.JSONDecodeError):
                logger.warning(
                    "Mensaje WebSocket rechazado: JSON inválido id_vehiculo_ruta=%s rol=%s",
                    id_vehiculo,
                    rol,
                )
                continue

            if not isinstance(mensaje, dict):
                logger.warning(
                    "Mensaje WebSocket rechazado: JSON no es objeto id_vehiculo_ruta=%s rol=%s",
                    id_vehiculo,
                    rol,
                )
                continue
            tipo = mensaje.get("tipo")
            id_vehiculo_mensaje = mensaje.get("id_vehiculo")
            id_solicitud = mensaje.get("id_solicitud")
            logger.info(
                "Mensaje WebSocket recibido rol=%s tipo=%s id_vehiculo=%s id_solicitud=%s",
                rol,
                tipo,
                id_vehiculo_mensaje,
                id_solicitud,
            )
            if id_vehiculo_mensaje != id_vehiculo:
                logger.warning(
                    "Mensaje WebSocket rechazado: vehículo no coincide tipo=%s id_vehiculo=%s "
                    "id_vehiculo_ruta=%s id_solicitud=%s",
                    tipo,
                    id_vehiculo_mensaje,
                    id_vehiculo,
                    id_solicitud,
                )
                continue

            if tipo == "comando_ack":
                if rol != "dispositivo":
                    logger.warning(
                        "ACK rechazado: rol no es dispositivo id_vehiculo=%s id_solicitud=%s rol=%s",
                        id_vehiculo,
                        id_solicitud,
                        rol,
                    )
                    continue
                if not isinstance(id_solicitud, str) or not id_solicitud:
                    logger.warning(
                        "ACK rechazado: id_solicitud ausente o inválido id_vehiculo=%s valor=%r",
                        id_vehiculo,
                        id_solicitud,
                    )
                    continue
                encontrada = websocket_manager.resolver_ack(id_solicitud, mensaje)
                logger.info(
                    "resolver_ack id_solicitud=%s id_vehiculo=%s pendiente_encontrada=%s estado=%s",
                    id_solicitud,
                    id_vehiculo,
                    encontrada,
                    mensaje.get("estado"),
                )
            elif rol == "dispositivo" and mensaje.get("tipo") == "demo_ack":
                id_demo = mensaje.get("id_demo")
                if isinstance(id_demo, int):
                    encontrada = websocket_manager.resolver_demo_ack(id_demo, mensaje)
                    logger.info(
                        "resolver_demo_ack id_vehiculo=%s id_demo=%s pendiente_encontrada=%s",
                        id_vehiculo,
                        id_demo,
                        encontrada,
                    )
                else:
                    logger.warning(
                        "ACK de demo rechazado: id_demo inválido id_vehiculo=%s id_demo=%r",
                        id_vehiculo,
                        id_demo,
                    )
            elif rol == "dispositivo" and mensaje.get("tipo") == "dispositivo_estado":
                conectado = mensaje.get("conectado")
                if isinstance(conectado, bool):
                    await websocket_manager.send_to_vehicle(
                        id_vehiculo,
                        {
                            "tipo": "dispositivo_estado",
                            "id_vehiculo": id_vehiculo,
                            "conectado": conectado,
                        },
                        role="observador",
                    )
                else:
                    logger.warning(
                        "Mensaje de estado rechazado: conectado no es booleano "
                        "id_vehiculo=%s id_solicitud=%s valor=%r",
                        id_vehiculo,
                        id_solicitud,
                        conectado,
                    )
            else:
                logger.warning(
                    "Mensaje WebSocket ignorado: tipo no manejado rol=%s tipo=%s "
                    "id_vehiculo=%s id_solicitud=%s",
                    rol,
                    tipo,
                    id_vehiculo,
                    id_solicitud,
                )

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
        if rol == "dispositivo":
            await websocket_manager.send_to_vehicle(
                id_vehiculo,
                {
                    "tipo": "dispositivo_estado",
                    "id_vehiculo": id_vehiculo,
                    "conectado": False,
                },
                role="observador",
            )
