
import asyncio
import logging
import os
import time
import uuid
from typing import Any

from app.core.websocket_manager import websocket_manager
from app.db.connection import get_connection
from app.db.operation_executor import ejecutar_operacion
from app.db.procedure_executor import execute_procedure


logger = logging.getLogger(__name__)
DEMO_START_ACK_TIMEOUT_S = 12.0
DEMO_MOVEMENT_ACK_TIMEOUT_S = 10.0
DEMO_START_RESULT_TIMEOUT_S = DEMO_START_ACK_TIMEOUT_S + 4.0


def cargar_datos_demo(
    id_vehiculo: int,
    id_demo: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Consulta la demo y sus movimientos."""
    connection = get_connection()

    try:
        resultados_demos = execute_procedure(
            connection,
            "sp_obtener_demos",
            (id_vehiculo,),
        )
        demos = resultados_demos[0] if resultados_demos else []

        demo = next(
            (
                item
                for item in demos
                if int(item["id_demo"]) == id_demo
            ),
            None,
        )

        if demo is None:
            raise LookupError(
                "La demo no existe o no pertenece al vehículo indicado."
            )

        if not bool(demo["activo"]):
            raise ValueError("La demo está inactiva.")

        resultados_movimientos = execute_procedure(
            connection,
            "sp_obtener_movimientos_demo",
            (id_demo,),
        )
        movimientos = (
            resultados_movimientos[0]
            if resultados_movimientos
            else []
        )

        if not movimientos:
            raise ValueError("La demo no tiene movimientos configurados.")

        movimientos = sorted(
            movimientos,
            key=lambda movimiento: int(movimiento["orden"]),
        )

        return demo, movimientos

    finally:
        connection.close()


class DemoExecutionManager:
    def __init__(self):
        self.tasks: dict[int, asyncio.Task] = {}
        self.individual_commands: set[int] = set()
        self.start_results: dict[int, asyncio.Future[bool]] = {}
        self.lock = asyncio.Lock()

    async def iniciar(
        self,
        id_vehiculo: int,
        id_demo: int,
    ) -> None:
        # Las consultas usan PyMySQL, que es síncrono.
        demo, movimientos = await asyncio.to_thread(
            cargar_datos_demo,
            id_vehiculo,
            id_demo,
        )

        if websocket_manager.connected_clients(id_vehiculo, "dispositivo") == 0:
            raise RuntimeError(
                "No hay clientes WebSocket conectados al vehículo."
            )

        async with self.lock:
            tarea_actual = self.tasks.get(id_vehiculo)

            if tarea_actual is not None and not tarea_actual.done():
                raise RuntimeError(
                    "Ya hay una demo ejecutándose para este vehículo."
                )

            if id_vehiculo in self.individual_commands:
                raise RuntimeError(
                    "Hay un comando individual en curso para este vehículo."
                )

            start_result = asyncio.get_running_loop().create_future()
            self.start_results[id_vehiculo] = start_result
            tarea = asyncio.create_task(
                self._ejecutar(
                    id_vehiculo,
                    demo,
                    movimientos,
                )
            )

            self.tasks[id_vehiculo] = tarea

        espera_inicio = time.monotonic()
        try:
            aceptada = await asyncio.wait_for(
                asyncio.shield(start_result),
                timeout=DEMO_START_RESULT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Timeout esperando resultado inicial id_vehiculo=%s id_demo=%s "
                "pid=%s elapsed_s=%.3f timeout_s=%.1f",
                id_vehiculo,
                id_demo,
                os.getpid(),
                time.monotonic() - espera_inicio,
                DEMO_START_RESULT_TIMEOUT_S,
            )
            raise RuntimeError("El dispositivo no confirmó el inicio de la demo.") from None
        if not aceptada:
            raise RuntimeError("El firmware rechazó el inicio de la demo.")

    async def cancelar(self, id_vehiculo: int) -> None:
        async with self.lock:
            tarea = self.tasks.get(id_vehiculo)

            if tarea is None or tarea.done():
                raise RuntimeError(
                    "No hay una demo en ejecución para este vehículo."
                )

            tarea.cancel()

        # Esperamos a que termine la cancelación y el intento de detener
        # el vehículo. No mantenemos el lock durante esta espera.
        await tarea

    async def _ejecutar(
        self,
        id_vehiculo: int,
        demo: dict[str, Any],
        movimientos: list[dict[str, Any]],
    ) -> None:
        id_demo = int(demo["id_demo"])

        try:
            inicio_ack = websocket_manager.crear_espera_demo_ack(id_demo)
            logger.info(
                "ACK de inicio registrado id_vehiculo=%s id_demo=%s pid=%s "
                "monotonic_s=%.6f",
                id_vehiculo,
                id_demo,
                os.getpid(),
                time.monotonic(),
            )
            enviados_inicio = await websocket_manager.send_to_vehicle(
                id_vehiculo,
                {
                    "tipo": "demo_iniciada",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "nombre": demo["nombre"],
                    "total_movimientos": len(movimientos),
                },
                role="dispositivo",
            )
            if enviados_inicio == 0:
                websocket_manager.quitar_espera_demo_ack(id_demo)
                raise RuntimeError("No quedan clientes WebSocket conectados.")

            espera_ack_inicio = time.monotonic()
            logger.info(
                "Inicio de espera demo_ack id_vehiculo=%s id_demo=%s clientes=%s "
                "pid=%s ack_timeout_s=%.1f monotonic_s=%.6f",
                id_vehiculo,
                id_demo,
                enviados_inicio,
                os.getpid(),
                DEMO_START_ACK_TIMEOUT_S,
                espera_ack_inicio,
            )
            try:
                respuesta_inicio = await asyncio.wait_for(
                    inicio_ack,
                    timeout=DEMO_START_ACK_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Timeout esperando demo_ack id_vehiculo=%s id_demo=%s "
                    "pid=%s elapsed_s=%.3f timeout_s=%.1f",
                    id_vehiculo,
                    id_demo,
                    os.getpid(),
                    time.monotonic() - espera_ack_inicio,
                    DEMO_START_ACK_TIMEOUT_S,
                )
                raise
            finally:
                websocket_manager.quitar_espera_demo_ack(id_demo)
            logger.info(
                "demo_ack correlacionado id_vehiculo=%s id_demo=%s estado=%s "
                "pid=%s elapsed_s=%.3f",
                id_vehiculo,
                id_demo,
                respuesta_inicio.get("estado"),
                os.getpid(),
                time.monotonic() - espera_ack_inicio,
            )
            if respuesta_inicio.get("estado") != "aceptado":
                resultado_inicio = self.start_results.get(id_vehiculo)
                if resultado_inicio is not None and not resultado_inicio.done():
                    resultado_inicio.set_result(False)
                raise RuntimeError("El firmware rechazó el inicio de la demo.")
            resultado_inicio = self.start_results.get(id_vehiculo)
            if resultado_inicio is not None and not resultado_inicio.done():
                resultado_inicio.set_result(True)

            for movimiento in movimientos:
                id_solicitud = str(uuid.uuid4())
                mensaje = {
                    "tipo": "comando_demo",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "id_demo_movimiento": movimiento[
                        "id_demo_movimiento"
                    ],
                    "id_solicitud": id_solicitud,
                    "orden": movimiento["orden"],
                    "id_comando": movimiento["id_comando"],
                    "comando": movimiento["comando"],
                    "codigo": movimiento["codigo"],
                    "duracion_ms": movimiento["duracion_ms"],
                    "pausa_ms": movimiento["pausa_ms"],
                }

                espera_ack = websocket_manager.crear_espera_ack(id_solicitud)
                enviados = await websocket_manager.send_to_vehicle(
                    id_vehiculo,
                    mensaje,
                    role="dispositivo",
                )

                if enviados == 0:
                    websocket_manager.quitar_espera_ack(id_solicitud)
                    raise RuntimeError(
                        "No quedan clientes WebSocket conectados."
                    )

                espera_ack_movimiento = time.monotonic()
                logger.info(
                    "Inicio de espera comando_ack id_vehiculo=%s id_demo=%s "
                    "orden=%s id_demo_movimiento=%s id_solicitud=%s pid=%s "
                    "timeout_s=%.1f monotonic_s=%.6f",
                    id_vehiculo,
                    id_demo,
                    movimiento["orden"],
                    movimiento["id_demo_movimiento"],
                    id_solicitud,
                    os.getpid(),
                    DEMO_MOVEMENT_ACK_TIMEOUT_S,
                    espera_ack_movimiento,
                )
                # El callback del firmware confirma que despachó la acción;
                # no representa una medición física del actuador.
                try:
                    respuesta = await asyncio.wait_for(
                        espera_ack,
                        timeout=DEMO_MOVEMENT_ACK_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Timeout esperando comando_ack id_vehiculo=%s id_demo=%s "
                        "orden=%s id_demo_movimiento=%s id_solicitud=%s pid=%s "
                        "elapsed_s=%.3f timeout_s=%.1f",
                        id_vehiculo,
                        id_demo,
                        movimiento["orden"],
                        movimiento["id_demo_movimiento"],
                        id_solicitud,
                        os.getpid(),
                        time.monotonic() - espera_ack_movimiento,
                        DEMO_MOVEMENT_ACK_TIMEOUT_S,
                    )
                    raise
                finally:
                    websocket_manager.quitar_espera_ack(id_solicitud)
                logger.info(
                    "comando_ack recibido id_vehiculo=%s id_demo=%s orden=%s "
                    "id_demo_movimiento=%s id_solicitud=%s estado=%s pid=%s "
                    "elapsed_s=%.3f",
                    id_vehiculo,
                    id_demo,
                    movimiento["orden"],
                    movimiento["id_demo_movimiento"],
                    id_solicitud,
                    respuesta.get("estado"),
                    os.getpid(),
                    time.monotonic() - espera_ack_movimiento,
                )
                if respuesta.get("estado") != "procesado":
                    raise RuntimeError("El firmware rechazó un movimiento de la demo.")

                await asyncio.to_thread(
                    ejecutar_operacion,
                    "sp_actualizar_estado",
                    (id_vehiculo, int(movimiento["id_comando"]), 1),
                    ((
                        "sp_registrar_historial",
                        (id_vehiculo, int(movimiento["id_comando"]), "demo"),
                    ),),
                )

                await asyncio.sleep(
                    int(movimiento["duracion_ms"]) / 1000
                )

                if int(movimiento["pausa_ms"]) > 0:
                    await asyncio.sleep(
                        int(movimiento["pausa_ms"]) / 1000
                    )

            await websocket_manager.send_to_vehicle(
                id_vehiculo,
                {
                    "tipo": "demo_completada",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "total_movimientos": len(movimientos),
                },
            )

        except asyncio.CancelledError:
            resultado_inicio = self.start_results.get(id_vehiculo)
            if resultado_inicio is not None and not resultado_inicio.done():
                resultado_inicio.set_result(False)
            await websocket_manager.send_to_vehicle(
                id_vehiculo,
                {
                    "tipo": "comando_demo",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "id_solicitud": str(uuid.uuid4()),
                    "comando": "APAGAR_TODO",
                    "codigo": 0,
                    "duracion_ms": 0,
                    "pausa_ms": 0,
                    "motivo": "cancelacion",
                },
                role="dispositivo",
            )

            await websocket_manager.send_to_vehicle(
                id_vehiculo,
                {
                    "tipo": "demo_cancelada",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                },
            )

        except Exception:
            logger.exception(
                "Fallo durante ejecución de demo id_vehiculo=%s id_demo=%s pid=%s",
                id_vehiculo,
                id_demo,
                os.getpid(),
            )
            resultado_inicio = self.start_results.get(id_vehiculo)
            if resultado_inicio is not None and not resultado_inicio.done():
                resultado_inicio.set_result(False)
            await websocket_manager.send_to_vehicle(
                id_vehiculo,
                {
                    "tipo": "demo_error",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "mensaje": "La ejecución se interrumpió.",
                },
            )

        finally:
            async with self.lock:
                tarea = self.tasks.get(id_vehiculo)

                if tarea is asyncio.current_task():
                    self.tasks.pop(id_vehiculo, None)
                    self.start_results.pop(id_vehiculo, None)


demo_execution_manager = DemoExecutionManager()
