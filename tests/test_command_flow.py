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

    async def test_observer_receives_command_without_opt_in(self):
        observer = FakeWebSocket()
        await websocket_manager.connect(21, observer, "observador")
        mensaje = {"tipo": "comando_individual", "id_vehiculo": 21, "id_solicitud": "fallback", "codigo": 0, "comando": "APAGAR_TODO"}
        enviados = await operaciones.enviar_comando_y_esperar_ack(21, mensaje)
        self.assertEqual(enviados, 1)
        self.assertEqual(observer.sent, [mensaje])
        self.assertNotIn("fallback", websocket_manager.pending_acks)

    async def test_no_device_and_no_observer_keeps_409(self):
        with self.assertRaises(HTTPException) as error:
            await operaciones.enviar_comando_y_esperar_ack(
                22,
                {"tipo": "comando_individual", "id_vehiculo": 22, "id_solicitud": "none"},
            )
        self.assertEqual(error.exception.status_code, 409)

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

    # Pruebas con sockets simulados para la ruta alterna sin dispositivo (todos los observadores)

    async def test_with_device_observers_do_not_receive_comando_individual(self):
        command = {"id_comando": 3, "nombre": "Avanzar", "codigo": 1}
        async def ack(mensaje):
            websocket_manager.resolver_ack(mensaje["id_solicitud"], {"estado": "procesado"})
        device = FakeWebSocket(ack)
        observer = FakeWebSocket()
        await websocket_manager.connect(51, device, "dispositivo")
        await websocket_manager.connect(51, observer, "observador")

        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res = await operaciones.ejecutar_comando_individual(51, 3)

        self.assertEqual(res["estado"], "procesado_por_firmware")
        self.assertTrue(any(m.get("tipo") == "comando_individual" for m in device.sent))
        # Observador NO debe recibir comando_individual
        self.assertFalse(any(m.get("tipo") == "comando_individual" for m in observer.sent))
        # Observador solo recibe el evento de finalización
        self.assertTrue(any(m.get("tipo") == "comando_individual_completado" for m in observer.sent))
        evento = next(m for m in observer.sent if m.get("tipo") == "comando_individual_completado")
        self.assertEqual(evento["mensaje"], "El comando se ejecutó correctamente.")

    async def test_without_device_observer_without_params_receives_200(self):
        command = {"id_comando": 3, "nombre": "Avanzar", "codigo": 1}
        observer = FakeWebSocket()
        # Conexión por defecto, sin parámetros adicionales
        await websocket_manager.connect(52, observer, "observador")

        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res = await operaciones.ejecutar_comando_individual(52, 3)

        self.assertEqual(res["estado"], "entregado_a_observadores")
        self.assertEqual(res["mensaje"], "No hay dispositivo conectado; el comando se entregó a 1 cliente(s) WebSocket.")
        self.assertEqual(res["resultados"], [])
        self.assertTrue(any(m.get("tipo") == "comando_individual" for m in observer.sent))

    async def test_without_device_observer_with_recibir_comandos_receives_200(self):
        command = {"id_comando": 3, "nombre": "Avanzar", "codigo": 1}
        observer = FakeWebSocket()
        await websocket_manager.connect(53, observer, "observador", recibir_comandos=True)

        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res = await operaciones.ejecutar_comando_individual(53, 3)

        self.assertEqual(res["estado"], "entregado_a_observadores")
        self.assertEqual(res["resultados"], [])
        self.assertTrue(any(m.get("tipo") == "comando_individual" for m in observer.sent))

    async def test_without_anyone_returns_409(self):
        command = {"id_comando": 3, "nombre": "Avanzar", "codigo": 1}
        with patch.object(operaciones, "validar_comando_individual", return_value=command):
            with self.assertRaises(HTTPException) as ctx:
                await operaciones.ejecutar_comando_individual(54, 3)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "No hay un dispositivo WebSocket conectado.")

    async def test_dead_device_with_observer_falls_back_to_alternate_route(self):
        command = {"id_comando": 3, "nombre": "Avanzar", "codigo": 1}
        class DeadWebSocket(FakeWebSocket):
            async def send_json(self, _):
                raise ConnectionResetError("Socket cerrado")

        dead_device = DeadWebSocket()
        observer = FakeWebSocket()
        await websocket_manager.connect(55, dead_device, "dispositivo")
        await websocket_manager.connect(55, observer, "observador")

        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res = await operaciones.ejecutar_comando_individual(55, 3)

        self.assertEqual(res["estado"], "entregado_a_observadores")
        self.assertEqual(res["resultados"], [])
        self.assertTrue(any(m.get("tipo") == "comando_individual" for m in observer.sent))

    async def test_order_and_event_message_text_alternate_route(self):
        command = {"id_comando": 3, "nombre": "Avanzar", "codigo": 1}
        observer = FakeWebSocket()
        await websocket_manager.connect(56, observer, "observador")

        with patch.object(operaciones, "validar_comando_individual", return_value=command), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            await operaciones.ejecutar_comando_individual(56, 3)

        self.assertEqual(len(observer.sent), 2)
        # Primero comando_individual
        self.assertEqual(observer.sent[0]["tipo"], "comando_individual")
        self.assertEqual(observer.sent[0]["codigo"], 1)
        # Después comando_individual_completado
        self.assertEqual(observer.sent[1]["tipo"], "comando_individual_completado")
        self.assertEqual(observer.sent[1]["estado"], "entregado_a_observadores")
        self.assertEqual(
            observer.sent[1]["mensaje"],
            "El comando se entregó a observadores; sin confirmación de firmware.",
        )
        self.assertNotIn("se ejecutó correctamente", observer.sent[1]["mensaje"])

    async def test_scenarios_detener_vehiculo(self):
        # Con dispositivo
        async def ack(mensaje):
            websocket_manager.resolver_ack(mensaje["id_solicitud"], {"estado": "procesado"})
        device = FakeWebSocket(ack)
        observer_a = FakeWebSocket()
        await websocket_manager.connect(61, device, "dispositivo")
        await websocket_manager.connect(61, observer_a, "observador")
        with patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_a = await operaciones.detener_vehiculo(61)
        self.assertEqual(res_a["estado"], "procesado_por_firmware")
        self.assertFalse(any(m.get("tipo") == "comando_individual" for m in observer_a.sent))

        # Sin dispositivo con observador sin parámetros
        observer_b = FakeWebSocket()
        await websocket_manager.connect(62, observer_b, "observador")
        with patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_b = await operaciones.detener_vehiculo(62)
        self.assertEqual(res_b["estado"], "entregado_a_observadores")
        self.assertEqual(len(observer_b.sent), 2)
        self.assertEqual(observer_b.sent[0]["tipo"], "comando_individual")
        self.assertEqual(observer_b.sent[0]["codigo"], 0)
        self.assertEqual(observer_b.sent[0]["comando"], "APAGAR_TODO")
        self.assertEqual(observer_b.sent[1]["tipo"], "comando_individual_completado")
        self.assertEqual(
            observer_b.sent[1]["mensaje"],
            "El comando se entregó a observadores; sin confirmación de firmware.",
        )

        # Sin nadie -> 409
        with self.assertRaises(HTTPException) as ctx_d:
            await operaciones.detener_vehiculo(63)
        self.assertEqual(ctx_d.exception.status_code, 409)

        # Dispositivo muerto + observador -> ruta alterna
        class DeadWebSocket(FakeWebSocket):
            async def send_json(self, _):
                raise ConnectionResetError("Socket cerrado")
        dead_device = DeadWebSocket()
        observer_e = FakeWebSocket()
        await websocket_manager.connect(64, dead_device, "dispositivo")
        await websocket_manager.connect(64, observer_e, "observador")
        with patch.object(operaciones, "ejecutar_operacion", return_value=[[]]):
            res_e = await operaciones.detener_vehiculo(64)
        self.assertEqual(res_e["estado"], "entregado_a_observadores")
        self.assertEqual(observer_e.sent[0]["tipo"], "comando_individual")
        self.assertEqual(observer_e.sent[1]["tipo"], "comando_individual_completado")
        self.assertEqual(
            observer_e.sent[1]["mensaje"],
            "El comando se entregó a observadores; sin confirmación de firmware.",
        )

    async def test_demo_alternate_route_order_and_event(self):
        observer = FakeWebSocket()
        await websocket_manager.connect(71, observer, "observador")
        demo = {"id_demo": 5, "nombre": "Demo Test", "activo": 1}
        movimiento = {
            "id_demo_movimiento": 101,
            "orden": 1,
            "id_comando": 2,
            "comando": "Retroceder",
            "codigo": 2,
            "duracion_ms": 100,
            "pausa_ms": 50,
        }
        sleeps = []
        async def mock_sleep(s):
            sleeps.append(s)

        with patch.object(demo_executor, "cargar_datos_demo", return_value=(demo, [movimiento])), \
             patch.object(demo_executor.asyncio, "sleep", new=mock_sleep), \
             patch.object(demo_executor, "ejecutar_operacion", return_value=[[]]):
            await demo_executor.demo_execution_manager.iniciar(71, 5)
            tarea = demo_executor.demo_execution_manager.tasks.get(71)
            if tarea:
                await tarea

        tipos = [m.get("tipo") for m in observer.sent]
        self.assertIn("demo_iniciada", tipos)
        self.assertIn("comando_individual", tipos)
        self.assertIn("comando_individual_completado", tipos)
        self.assertIn("demo_completada", tipos)

        idx_cmd = tipos.index("comando_individual")
        idx_comp = tipos.index("comando_individual_completado")
        # El comando se envía ANTES del completado
        self.assertLess(idx_cmd, idx_comp)

        msg_comp = observer.sent[idx_comp]
        self.assertEqual(msg_comp["estado"], "entregado_a_observadores")
        self.assertEqual(
            msg_comp["mensaje"],
            "El comando se entregó a observadores; sin confirmación de firmware.",
        )
        self.assertEqual(sleeps, [0.1, 0.05])


if __name__ == "__main__":
    unittest.main()
