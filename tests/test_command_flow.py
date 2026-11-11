import asyncio
import inspect
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from fastapi import FastAPI, HTTPException
from starlette.websockets import WebSocketDisconnect

from app.api.routes import operaciones, websocket as websocket_routes
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


class FakeDeviceWebSocket(FakeWebSocket):
    def __init__(self):
        super().__init__()
        self.query_params = {"rol": "dispositivo"}
        self.accepted = asyncio.Event()
        self.incoming = asyncio.Queue()

    async def accept(self):
        self.accepted.set()

    async def send_json(self, mensaje):
        await super().send_json(mensaje)
        if mensaje.get("tipo") == "comando_individual":
            await self.incoming.put(json.dumps({
                "tipo": "comando_ack",
                "id_vehiculo": mensaje["id_vehiculo"],
                "id_solicitud": mensaje["id_solicitud"],
                "estado": "procesado",
            }))

    async def receive_text(self):
        mensaje = await self.incoming.get()
        if mensaje is None:
            raise WebSocketDisconnect()
        return mensaje


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

    async def test_http_stop_request_resolves_ack_while_waiting(self):
        app = FastAPI()
        app.include_router(operaciones.router)
        dispositivo = FakeDeviceWebSocket()

        eventos = []

        async def receive():
            if not eventos:
                eventos.append("request")
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(event):
            eventos.append(event)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/vehiculos/1/detener",
            "raw_path": b"/api/vehiculos/1/detener",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
        }

        with patch.object(websocket_routes, "cargar_estado_inicial", return_value=([], [])):
            websocket_task = asyncio.create_task(
                websocket_routes.websocket_vehiculo(dispositivo, 1)
            )
            await dispositivo.accepted.wait()
            while websocket_manager.connected_clients(1, "dispositivo") == 0:
                await asyncio.sleep(0)
            with patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
                await app(scope, receive, send)
            await dispositivo.incoming.put(None)
            await websocket_task

        response_start = next(event for event in eventos if event["type"] == "http.response.start")
        response_body = next(event for event in eventos if event["type"] == "http.response.body")
        body = json.loads(response_body["body"])
        self.assertEqual(
            response_start["status"],
            200,
            f"sent={dispositivo.sent!r} incoming={dispositivo.incoming.qsize()} "
            f"websocket_done={websocket_task.done()}",
        )
        self.assertEqual(body["estado"], "procesado_por_firmware")
        self.assertNotIn(body["id_solicitud"], websocket_manager.pending_acks)

    async def test_unknown_ack_does_not_resolve_another_request(self):
        solicitud = "solicitud-pendiente"
        espera = websocket_manager.crear_espera_ack(solicitud)

        self.assertFalse(
            websocket_manager.resolver_ack(
                "solicitud-desconocida",
                {"estado": "procesado"},
            )
        )
        self.assertFalse(espera.done())
        self.assertTrue(
            websocket_manager.resolver_ack(
                solicitud,
                {"estado": "procesado"},
            )
        )
        self.assertEqual((await espera)["estado"], "procesado")

    async def test_late_ack_after_timeout_is_ignored_without_error(self):
        socket = FakeWebSocket()
        await websocket_manager.connect(1, socket, "dispositivo")
        id_solicitud = "solicitud-con-timeout"

        with self.assertRaises(HTTPException) as error:
            await operaciones.enviar_comando_y_esperar_ack(
                1,
                {
                    "tipo": "comando_individual",
                    "id_vehiculo": 1,
                    "id_solicitud": id_solicitud,
                    "codigo": 0,
                },
                timeout=0.01,
            )

        self.assertEqual(error.exception.status_code, 504)
        self.assertNotIn(id_solicitud, websocket_manager.pending_acks)
        self.assertFalse(
            websocket_manager.resolver_ack(
                id_solicitud,
                {"estado": "procesado"},
            )
        )
        self.assertEqual(
            inspect.signature(operaciones.enviar_comando_y_esperar_ack)
            .parameters["timeout"].default,
            3.0,
        )

    async def test_observer_fallback_requires_opt_in_and_delivers_without_ack(self):
        observer = FakeWebSocket()
        await websocket_manager.connect(21, observer, "observador", recibir_comandos=True)
        mensaje = {"tipo": "comando_individual", "id_vehiculo": 21, "id_solicitud": "fallback", "codigo": 0, "comando": "APAGAR_TODO"}
        enviados = await operaciones.enviar_comando_y_esperar_ack(21, mensaje)
        self.assertEqual(enviados, 1)
        self.assertEqual(observer.sent, [mensaje])
        self.assertNotIn("fallback", websocket_manager.pending_acks)

    async def test_no_device_and_no_opted_observer_keeps_409(self):
        observer = FakeWebSocket()
        await websocket_manager.connect(22, observer, "observador")
        with self.assertRaises(HTTPException) as error:
            await operaciones.enviar_comando_y_esperar_ack(
                22,
                {"tipo": "comando_individual", "id_vehiculo": 22, "id_solicitud": "none"},
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(observer.sent, [])

    async def test_device_has_priority_over_opted_observer(self):
        observer = FakeWebSocket()
        await websocket_manager.connect(23, observer, "observador", recibir_comandos=True)
        async def acknowledge(mensaje):
            websocket_manager.resolver_ack(mensaje["id_solicitud"], {"estado": "procesado"})
        device = FakeWebSocket(acknowledge)
        await websocket_manager.connect(23, device, "dispositivo")
        mensaje = {"tipo": "comando_individual", "id_vehiculo": 23, "id_solicitud": "device", "codigo": 1}
        respuesta = await operaciones.enviar_comando_y_esperar_ack(23, mensaje)
        self.assertEqual(respuesta["estado"], "procesado")
        self.assertEqual(device.sent, [mensaje])
        self.assertEqual(observer.sent, [])

    # Pruebas requeridas para los escenarios (a) a (f)

    async def test_scenarios_ejecutar_comando_individual(self):
        command = {"id_comando": 3, "nombre": "Avanzar", "codigo": 1}

        # (a) Con dispositivo
        async def ack(mensaje):
            websocket_manager.resolver_ack(mensaje["id_solicitud"], {"estado": "procesado"})
        device = FakeWebSocket(ack)
        await websocket_manager.connect(31, device, "dispositivo")
        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_a = await operaciones.ejecutar_comando_individual(31, 3)
        self.assertEqual(res_a["estado"], "procesado_por_firmware")

        # (b) Sin dispositivo y con receptor
        receiver = FakeWebSocket()
        await websocket_manager.connect(32, receiver, "observador", recibir_comandos=True)
        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_b = await operaciones.ejecutar_comando_individual(32, 3)
        self.assertEqual(res_b["estado"], "entregado_a_observadores")
        self.assertEqual(res_b["resultados"], [])
        self.assertTrue(any(m.get("tipo") == "comando_individual" for m in receiver.sent))

        # (c) Sin dispositivo y con observador no receptor -> 409
        non_receiver = FakeWebSocket()
        await websocket_manager.connect(33, non_receiver, "observador", recibir_comandos=False)
        with patch.object(operaciones, "validar_comando_individual", return_value=command):
            with self.assertRaises(HTTPException) as ctx_c:
                await operaciones.ejecutar_comando_individual(33, 3)
        self.assertEqual(ctx_c.exception.status_code, 409)
        self.assertEqual(ctx_c.exception.detail, "No hay un dispositivo WebSocket conectado.")

        # (d) Sin nadie -> 409
        with patch.object(operaciones, "validar_comando_individual", return_value=command):
            with self.assertRaises(HTTPException) as ctx_d:
                await operaciones.ejecutar_comando_individual(34, 3)
        self.assertEqual(ctx_d.exception.status_code, 409)
        self.assertEqual(ctx_d.exception.detail, "No hay un dispositivo WebSocket conectado.")

        # (e) Dispositivo muerto más receptor -> debe caer a la ruta alterna
        class DeadWebSocket(FakeWebSocket):
            async def send_json(self, _):
                raise ConnectionResetError("Socket cerrado abruptamente")

        dead_device = DeadWebSocket()
        receiver_e = FakeWebSocket()
        await websocket_manager.connect(35, dead_device, "dispositivo")
        await websocket_manager.connect(35, receiver_e, "observador", recibir_comandos=True)
        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_e = await operaciones.ejecutar_comando_individual(35, 3)
        self.assertEqual(res_e["estado"], "entregado_a_observadores")
        self.assertEqual(res_e["resultados"], [])
        self.assertTrue(any(m.get("tipo") == "comando_individual" for m in receiver_e.sent))

    async def test_scenarios_detener_vehiculo(self):
        # (f.a) Detener con dispositivo
        async def ack(mensaje):
            websocket_manager.resolver_ack(mensaje["id_solicitud"], {"estado": "procesado"})
        device = FakeWebSocket(ack)
        await websocket_manager.connect(41, device, "dispositivo")
        with patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_fa = await operaciones.detener_vehiculo(41)
        self.assertEqual(res_fa["estado"], "procesado_por_firmware")

        # (f.b) Detener sin dispositivo y con receptor
        receiver = FakeWebSocket()
        await websocket_manager.connect(42, receiver, "observador", recibir_comandos=True)
        with patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_fb = await operaciones.detener_vehiculo(42)
        self.assertEqual(res_fb["estado"], "entregado_a_observadores")
        self.assertEqual(res_fb["resultados"], [])
        msg_stop = next(m for m in receiver.sent if m.get("tipo") == "comando_individual")
        self.assertEqual(msg_stop["codigo"], 0)
        self.assertEqual(msg_stop["comando"], "APAGAR_TODO")

        # (f.c) Detener sin dispositivo y con observador no receptor -> 409
        non_receiver = FakeWebSocket()
        await websocket_manager.connect(43, non_receiver, "observador", recibir_comandos=False)
        with self.assertRaises(HTTPException) as ctx_fc:
            await operaciones.detener_vehiculo(43)
        self.assertEqual(ctx_fc.exception.status_code, 409)
        self.assertEqual(ctx_fc.exception.detail, "No hay un dispositivo WebSocket conectado.")

        # (f.d) Detener sin nadie -> 409
        with self.assertRaises(HTTPException) as ctx_fd:
            await operaciones.detener_vehiculo(44)
        self.assertEqual(ctx_fd.exception.status_code, 409)
        self.assertEqual(ctx_fd.exception.detail, "No hay un dispositivo WebSocket conectado.")

        # (f.e) Detener con dispositivo muerto más receptor -> cae a ruta alterna
        class DeadWebSocket(FakeWebSocket):
            async def send_json(self, _):
                raise ConnectionResetError("Socket cerrado abruptamente")

        dead_device = DeadWebSocket()
        receiver_fe = FakeWebSocket()
        await websocket_manager.connect(45, dead_device, "dispositivo")
        await websocket_manager.connect(45, receiver_fe, "observador", recibir_comandos=True)
        with patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_fe = await operaciones.detener_vehiculo(45)
        self.assertEqual(res_fe["estado"], "entregado_a_observadores")
        self.assertEqual(res_fe["resultados"], [])
        msg_stop_e = next(m for m in receiver_fe.sent if m.get("tipo") == "comando_individual")
        self.assertEqual(msg_stop_e["codigo"], 0)
        self.assertEqual(msg_stop_e["comando"], "APAGAR_TODO")


if __name__ == "__main__":
    unittest.main()
