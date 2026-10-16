
import asyncio
from typing import Any

from app.core.websocket_manager import websocket_manager
from app.db.connection import get_connection
from app.db.procedure_executor import execute_procedure


def cargar_datos_demo(
    id_vehiculo: int,
    id_demo: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Consulta la demo, sus movimientos y los comandos del vehículo."""
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

        resultados_comandos = execute_procedure(
            connection,
            "sp_obtener_comandos_vehiculo",
            (id_vehiculo,),
        )
        comandos = (
            resultados_comandos[0]
            if resultados_comandos
            else []
        )

        movimientos = sorted(
            movimientos,
            key=lambda movimiento: int(movimiento["orden"]),
        )

        return demo, movimientos, comandos

    finally:
        connection.close()


class DemoExecutionManager:
    def __init__(self):
        self.tasks: dict[int, asyncio.Task] = {}
        self.lock = asyncio.Lock()

    async def iniciar(
        self,
        id_vehiculo: int,
        id_demo: int,
    ) -> None:
        # Las consultas usan PyMySQL, que es síncrono.
        demo, movimientos, comandos = await asyncio.to_thread(
            cargar_datos_demo,
            id_vehiculo,
            id_demo,
        )

        if websocket_manager.connected_clients(id_vehiculo) == 0:
            raise RuntimeError(
                "No hay clientes WebSocket conectados al vehículo."
            )

        async with self.lock:
            tarea_actual = self.tasks.get(id_vehiculo)

            if tarea_actual is not None and not tarea_actual.done():
                raise RuntimeError(
                    "Ya hay una demo ejecutándose para este vehículo."
                )

            tarea = asyncio.create_task(
                self._ejecutar(
                    id_vehiculo,
                    demo,
                    movimientos,
                    comandos,
                )
            )

            self.tasks[id_vehiculo] = tarea

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
        comandos: list[dict[str, Any]],
    ) -> None:
        id_demo = int(demo["id_demo"])

        comando_detener = next(
            (
                comando
                for comando in comandos
                if str(comando.get("nombre", "")).strip().upper()
                in {"DETENER", "STOP", "ALTO"}
                and bool(comando.get("activo", 1))
            ),
            None,
        )

        try:
            await websocket_manager.send_to_vehicle(
                id_vehiculo,
                {
                    "tipo": "demo_iniciada",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "nombre": demo["nombre"],
                    "total_movimientos": len(movimientos),
                },
            )

            for movimiento in movimientos:
                mensaje = {
                    "tipo": "comando_demo",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "id_demo_movimiento": movimiento[
                        "id_demo_movimiento"
                    ],
                    "orden": movimiento["orden"],
                    "id_comando": movimiento["id_comando"],
                    "comando": movimiento["comando"],
                    "codigo": movimiento["codigo"],
                    "duracion_ms": movimiento["duracion_ms"],
                    "pausa_ms": movimiento["pausa_ms"],
                }

                enviados = await websocket_manager.send_to_vehicle(
                    id_vehiculo,
                    mensaje,
                )

                if enviados == 0:
                    raise RuntimeError(
                        "No quedan clientes WebSocket conectados."
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
            # Intentar detener el vehículo si existe un comando habilitado
            # para ello en la base de datos.
            if comando_detener is not None:
                await websocket_manager.send_to_vehicle(
                    id_vehiculo,
                    {
                        "tipo": "comando_demo",
                        "id_vehiculo": id_vehiculo,
                        "id_demo": id_demo,
                        "comando": comando_detener["nombre"],
                        "codigo": comando_detener["codigo"],
                        "duracion_ms": 0,
                        "pausa_ms": 0,
                        "motivo": "cancelacion",
                    },
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


demo_execution_manager = DemoExecutionManager()
