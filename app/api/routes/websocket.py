
import asyncio
import json
import logging
import os
import time

import pymysql
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from app.core.websocket_manager import websocket_manager
from app.db.connection import get_connection
from app.services import acceso_wss
from app.services.autorizacion import ANONIMO
from app.db.procedure_executor import execute_procedure


router = APIRouter(tags=["WebSocket IoT"])
logger = logging.getLogger(__name__)


def cargar_estado_inicial(id_vehiculo: int):
    """Carga el estado y las demos usando PyMySQL fuera del event loop."""
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
        # El canal WSS aún no autentica (Etapa 8): solo se publican demos de fábrica, nunca privadas.
        demos = [d for d in (resultados_demos[0] if resultados_demos else []) if d.get("tipo", "fabrica") == "fabrica"]
        return estado, demos
    finally:
        connection.close()


GRACIA_RECONEXION_S = 15


async def _cerrar_reserva_si_no_vuelve(sid: str) -> None:
    """El invitado se desconectó: si no vuelve en unos segundos (recarga, red), se libera y DETIENE la unidad."""
    from app.services.vigilante import finalizar_reserva

    await asyncio.sleep(GRACIA_RECONEXION_S)
    if websocket_manager.conexiones_de_sid(sid) == 0:
        await finalizar_reserva(sid, "desconexion")


@router.websocket("/ws/vehiculos/{id_vehiculo}")
async def websocket_vehiculo(
    websocket: WebSocket,
    id_vehiculo: int,
):
    params = getattr(websocket, "query_params", {})
    rol = params.get("rol", "observador")
    recibir_val = str(params.get("recibir_comandos", "")).strip().lower()
    recibir_comandos = recibir_val in ("1", "true", "yes", "t")
    if rol not in {"dispositivo", "observador"}:
        await websocket.close(code=1008)
        return

    # ---- Etapa 8: autenticación y autorización del canal ANTES de recibir datos ----
    actor = None
    try:
        if rol == "dispositivo":
            # Preferente: cabecera X-Dispositivo-Token (no queda en logs de URL); ?token= por compatibilidad.
            cabeceras = getattr(websocket, "headers", {}) or {}
            token = cabeceras.get("x-dispositivo-token") or params.get("token")
            permitido = await asyncio.to_thread(acceso_wss.dispositivo_autorizado, id_vehiculo, token)
        elif params.get("ticket"):
            actor = acceso_wss.consumir_ticket(str(params.get("ticket")), id_vehiculo)
            permitido = actor is not None
        else:
            # Sin ticket = anónimo: solo lo que permita la regla (transición, unidad sin comprador).
            permitido = await asyncio.to_thread(acceso_wss.puede_observar, ANONIMO, id_vehiculo)
    except pymysql.MySQLError:
        permitido = False
    if not permitido:
        # Se acepta y se cierra con código propio: si se rechazara el handshake, el navegador solo
        # vería 1006 y no podría distinguir "sin permiso" de "sin red".
        await websocket.accept()
        await websocket.close(code=acceso_wss.CIERRE_SIN_CREDENCIAL, reason="Sin credencial válida para esta unidad.")
        logger.warning("WSS rechazado rol=%s id_vehiculo=%s ticket=%s", rol, id_vehiculo, bool(params.get("ticket")))
        return

    if rol == "dispositivo":
        reemplazados = await websocket_manager.reemplazar_dispositivo(id_vehiculo)
        if reemplazados:
            logger.warning(
                "Conexión de dispositivo anterior reemplazada id_vehiculo=%s cantidad=%s",
                id_vehiculo,
                reemplazados,
            )

    await websocket_manager.connect(id_vehiculo, websocket, rol, recibir_comandos, actor=actor)

    try:
        # PyMySQL es síncrono: no bloquear el event loop que recibe los ACK.
        estado, demos = await asyncio.to_thread(
            cargar_estado_inicial,
            id_vehiculo,
        )

        await websocket.send_json(
            jsonable_encoder({
                "tipo": "estado_inicial",
                "id_vehiculo": id_vehiculo,
                "estado": estado,
                "demos": demos,
            })
        )

        if rol == "dispositivo":
            await websocket_manager.avisar_estado_dispositivo(id_vehiculo, True)

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
                        "resolver_demo_ack id_vehiculo=%s id_demo=%s estado=%s "
                        "pendiente_encontrada=%s pid=%s monotonic_s=%.6f",
                        id_vehiculo,
                        id_demo,
                        mensaje.get("estado"),
                        encontrada,
                        os.getpid(),
                        time.monotonic(),
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
                    await websocket_manager.avisar_estado_dispositivo(id_vehiculo, conectado)
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
        if actor is not None and getattr(actor, "es_reserva", False):
            asyncio.create_task(_cerrar_reserva_si_no_vuelve(actor.sid))
        if rol == "dispositivo" and websocket_manager.connected_clients(id_vehiculo, "dispositivo") == 0:
            await websocket_manager.avisar_estado_dispositivo(id_vehiculo, False)
