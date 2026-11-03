import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from fastapi import HTTPException

from app.api.routes import operaciones
from app.core.websocket_manager import WebSocketManager, websocket_manager
from app.services import demo_executor


class FakeConnection:
    def close(self):
        pass


class FakeWebSocket:
    def __init__(self, on_send=None):
        self.on_send = on_send
        self.sent = []

    async def accept(self):
        pass

    async def send_json(self, mensaje):
        self.sent.append(mensaje)
        if self.on_send is not None:
            await self.on_send(mensaje)


class CommandValidationTests(unittest.TestCase):
    def _validate(self, vehicle, commands, command_id=3):
        def execute(_connection, procedure, _params):
            if procedure == "sp_obtener_vehiculo":
                return [[vehicle]] if vehicle else [[]]
            if procedure == "sp_obtener_comandos_vehiculo":
                return [commands]
            raise AssertionError(procedure)

        with patch.object(operaciones, "get_connection", return_value=FakeConnection()), \
             patch.object(operaciones, "execute_procedure", side_effect=execute):
            return operaciones.validar_comando_individual(7, command_id)

    def test_valid_command_and_demo_code_rejection(self):
        vehicle = {"id_vehiculo": 7, "activo": 1}
        command = {"id_comando": 3, "nombre": "Garra arriba", "codigo": 1, "activo": 1}
        self.assertEqual(self._validate(vehicle, [command]), command)
        demo_command = {**command, "codigo": 13}
        with self.assertRaises(HTTPException) as error:
            self._validate(vehicle, [demo_command])
        self.assertEqual(error.exception.status_code, 409)

    def test_missing_and_inactive_entities_are_rejected(self):
        with self.assertRaises(HTTPException) as missing:
            self._validate(None, [])
        self.assertEqual(missing.exception.status_code, 404)

        vehicle = {"id_vehiculo": 7, "activo": 0}
        with self.assertRaises(HTTPException) as inactive_vehicle:
            self._validate(vehicle, [])
        self.assertEqual(inactive_vehicle.exception.status_code, 409)

        vehicle["activo"] = 1
        command = {"id_comando": 3, "nombre": "Garra arriba", "codigo": 1, "activo": 0}
        with self.assertRaises(HTTPException) as inactive_command:
            self._validate(vehicle, [command])
        self.assertEqual(inactive_command.exception.status_code, 409)

        with self.assertRaises(HTTPException) as missing_command:
            self._validate(vehicle, [], command_id=4)
        self.assertEqual(missing_command.exception.status_code, 404)


class AsyncCommandFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        websocket_manager.connections.clear()
        websocket_manager.pending_acks.clear()
        websocket_manager.pending_demo_acks.clear()
        demo_executor.demo_execution_manager.tasks.clear()
        demo_executor.demo_execution_manager.individual_commands.clear()

    async def asyncTearDown(self):
        websocket_manager.connections.clear()
        websocket_manager.pending_acks.clear()
        websocket_manager.pending_demo_acks.clear()
        demo_executor.demo_execution_manager.tasks.clear()
        demo_executor.demo_execution_manager.individual_commands.clear()

    async def test_acknowledged_commands_can_be_sent_consecutively(self):
        async def acknowledge(mensaje):
            websocket_manager.resolver_ack(
                mensaje["id_solicitud"],
                {"estado": "procesado"},
            )

        socket = FakeWebSocket(acknowledge)
        await websocket_manager.connect(7, socket, "dispositivo")
        command = {"id_comando": 3, "nombre": "Garra arriba", "codigo": 1}
        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            first = await operaciones.ejecutar_comando_individual(7, 3)
            second = await operaciones.ejecutar_comando_individual(7, 3)

        self.assertEqual(first["estado"], "procesado_por_firmware")
        self.assertEqual(second["estado"], "procesado_por_firmware")
        self.assertEqual(len(socket.sent), 2)
        self.assertTrue(all(message["tipo"] == "comando_individual" for message in socket.sent))
        self.assertNotIn(7, demo_executor.demo_execution_manager.individual_commands)

    async def test_disconnected_or_rejected_command_is_not_recorded(self):
        command = {"id_comando": 3, "nombre": "Garra arriba", "codigo": 1}
        with patch.object(operaciones, "validar_comando_individual", return_value=command):
            with self.assertRaises(HTTPException) as disconnected:
                await operaciones.ejecutar_comando_individual(7, 3)
        self.assertEqual(disconnected.exception.status_code, 409)

        async def reject(mensaje):
            websocket_manager.resolver_ack(
                mensaje["id_solicitud"],
                {"estado": "rechazado", "detalle": "demo activa"},
            )

        socket = FakeWebSocket(reject)
        await websocket_manager.connect(7, socket, "dispositivo")
        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion") as record:
            with self.assertRaises(HTTPException) as rejected:
                await operaciones.ejecutar_comando_individual(7, 3)
        self.assertEqual(rejected.exception.status_code, 409)
        record.assert_not_called()

    async def test_demo_and_individual_operations_conflict(self):
        class ActiveTask:
            def done(self):
                return False

        command = {"id_comando": 3, "nombre": "Garra arriba", "codigo": 1}
        demo_executor.demo_execution_manager.tasks[7] = ActiveTask()
        with patch.object(operaciones, "validar_comando_individual", return_value=command):
            with self.assertRaises(HTTPException) as error:
                await operaciones.ejecutar_comando_individual(7, 3)
        self.assertEqual(error.exception.status_code, 409)

        demo_executor.demo_execution_manager.tasks.clear()
        socket = FakeWebSocket()
        await websocket_manager.connect(7, socket, "dispositivo")
        demo_executor.demo_execution_manager.individual_commands.add(7)
        demo = {"id_demo": 2, "nombre": "demo", "activo": 1}
        with patch.object(demo_executor, "cargar_datos_demo", return_value=(demo, [{"orden": 1}])):
            with self.assertRaises(RuntimeError):
                await demo_executor.demo_execution_manager.iniciar(7, 2)
        self.assertEqual(demo_executor.demo_execution_manager.tasks, {})

    async def test_physical_messages_are_sent_only_to_device_role(self):
        manager = WebSocketManager()
        device = FakeWebSocket()
        observer = FakeWebSocket()
        await manager.connect(4, device, "dispositivo")
        await manager.connect(4, observer, "observador")
        sent = await manager.send_to_vehicle(4, {"tipo": "comando_individual"}, role="dispositivo")
        self.assertEqual(sent, 1)
        self.assertEqual(len(device.sent), 1)
        self.assertEqual(observer.sent, [])

    async def test_demo_observes_configured_duration_and_pause(self):
        async def acknowledge(mensaje):
            if mensaje["tipo"] == "demo_iniciada":
                websocket_manager.resolver_demo_ack(
                    mensaje["id_demo"],
                    {"estado": "aceptado"},
                )
            elif mensaje["tipo"] == "comando_demo":
                websocket_manager.resolver_ack(
                    mensaje["id_solicitud"],
                    {"estado": "procesado"},
                )

        socket = FakeWebSocket(acknowledge)
        await websocket_manager.connect(9, socket, "dispositivo")
        sleeps = []

        async def record_sleep(seconds):
            sleeps.append(seconds)

        movimiento = {
            "id_demo_movimiento": 12,
            "orden": 1,
            "id_comando": 1,
            "comando": "Garra arriba",
            "codigo": 1,
            "duracion_ms": 500,
            "pausa_ms": 200,
        }
        with patch.object(demo_executor.asyncio, "sleep", new=record_sleep), \
             patch.object(demo_executor, "ejecutar_operacion", return_value=[[]]):
            await demo_executor.demo_execution_manager._ejecutar(
                9,
                {"id_demo": 2, "nombre": "prueba"},
                [movimiento],
            )
        self.assertEqual(sleeps, [0.5, 0.2])
        self.assertIn("demo_completada", [message["tipo"] for message in socket.sent])


if __name__ == "__main__":
    unittest.main()
