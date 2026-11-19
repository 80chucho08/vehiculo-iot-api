"""Pruebas de R3 (prioridad de DETENER), R12/R13 (bajas lógicas) y R16 (pasos de demo)."""

import asyncio
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

import pymysql
from fastapi import HTTPException

from app.api.routes import operaciones
from app.core.websocket_manager import websocket_manager
from app.db import bajas_logicas
from app.db.procedure_executor import ALLOWED_PROCEDURES
from app.services import demo_executor

manager = demo_executor.demo_execution_manager


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_json(self, mensaje):
        self.sent.append(mensaje)


def limpiar_estado():
    websocket_manager.connections.clear()
    websocket_manager.pending_acks.clear()
    websocket_manager.pending_demo_acks.clear()
    manager.tasks.clear()
    manager.individual_commands.clear()
    manager.individual_done.clear()
    manager.stopping.clear()
    manager.start_results.clear()


def tipos(socket):
    return [mensaje.get("tipo") for mensaje in socket.sent]


class DetenerPrioridadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        limpiar_estado()

    async def asyncTearDown(self):
        for tarea in list(manager.tasks.values()):
            tarea.cancel()
        limpiar_estado()

    async def test_detener_durante_demo_la_cancela_y_no_responde_409(self):
        observador = FakeWebSocket()
        await websocket_manager.connect(81, observador, "observador")
        demo = {"id_demo": 9, "nombre": "Larga", "activo": 1}
        paso = {
            "id_demo_movimiento": 1, "orden": 1, "id_comando": 1,
            "comando": "Garra arriba", "codigo": 1,
            "duracion_ms": 60000, "pausa_ms": 0,
        }
        with patch.object(demo_executor, "cargar_datos_demo", return_value=(demo, [paso])), \
             patch.object(demo_executor, "ejecutar_operacion", return_value=[[]]), \
             patch.object(operaciones, "ejecutar_operacion", return_value=[[]]) as registro:
            await manager.iniciar(81, 9)
            for _ in range(50):
                if "comando_individual" in tipos(observador):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(manager.demo_activa(81))

            respuesta = await asyncio.wait_for(operaciones.detener_vehiculo(81), timeout=2)

        self.assertTrue(respuesta["demo_cancelada"])
        self.assertEqual(respuesta["estado"], "entregado_a_observadores")
        self.assertFalse(manager.demo_activa(81))
        self.assertFalse(manager.deteniendo(81))
        recibidos = tipos(observador)
        self.assertIn("demo_cancelada", recibidos)
        ultimo_comando = [m for m in observador.sent if m.get("tipo") == "comando_individual"][-1]
        self.assertEqual(ultimo_comando["comando"], "APAGAR_TODO")
        self.assertEqual(ultimo_comando["codigo"], 0)
        self.assertGreater(
            recibidos.index("comando_individual", recibidos.index("demo_cancelada")),
            recibidos.index("demo_cancelada"),
        )
        registro.assert_called_once()
        self.assertEqual(registro.call_args.args[1], (81, None, 1))

    async def test_detener_con_comando_pendiente_envia_ya_y_registra_despues(self):
        observador = FakeWebSocket()
        await websocket_manager.connect(82, observador, "observador")
        manager.individual_commands.add(82)
        terminado = asyncio.Event()
        manager.individual_done[82] = terminado
        orden = []

        def registrar(*_args, **_kwargs):
            orden.append("detener")
            return [[]]

        with patch.object(operaciones, "ejecutar_operacion", side_effect=registrar):
            tarea = asyncio.create_task(operaciones.detener_vehiculo(82))
            await asyncio.sleep(0.05)
            # La orden física ya salió aunque el comando previo siga en vuelo...
            self.assertIn("APAGAR_TODO", [m.get("comando") for m in observador.sent])
            # ...pero su fila espera a la del comando previo.
            self.assertEqual(orden, [])
            orden.append("comando_previo")
            terminado.set()
            respuesta = await asyncio.wait_for(tarea, timeout=2)

        self.assertEqual(orden, ["comando_previo", "detener"])
        self.assertFalse(respuesta["demo_cancelada"])
        # DETENER no libera la marca del comando individual ajeno.
        self.assertIn(82, manager.individual_commands)

    async def test_comando_individual_rechazado_mientras_se_detiene(self):
        manager.stopping[83] = 1
        comando = {"id_comando": 3, "nombre": "Garra arriba", "codigo": 1}
        with patch.object(operaciones, "validar_comando_individual", return_value=comando):
            with self.assertRaises(HTTPException) as error:
                await operaciones.ejecutar_comando_individual(83, 3)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("DETENER", error.exception.detail)
        self.assertNotIn(83, manager.individual_commands)

    async def test_demo_rechazada_mientras_se_detiene(self):
        await websocket_manager.connect(84, FakeWebSocket(), "observador")
        manager.stopping[84] = 1
        demo = {"id_demo": 2, "nombre": "demo", "activo": 1}
        with patch.object(demo_executor, "cargar_datos_demo", return_value=(demo, [{"orden": 1}])):
            with self.assertRaises(RuntimeError):
                await manager.iniciar(84, 2)
        self.assertEqual(manager.tasks, {})

    async def test_detener_sin_receptores_sigue_en_409_y_libera_marca(self):
        with self.assertRaises(HTTPException) as error:
            await operaciones.detener_vehiculo(85)
        self.assertEqual(error.exception.status_code, 409)
        self.assertFalse(manager.deteniendo(85))


class BajasLogicasRutasTests(unittest.TestCase):
    def test_procedimientos_de_borrado_fisico_retirados(self):
        self.assertNotIn("sp_eliminar_vehiculo", ALLOWED_PROCEDURES)
        self.assertNotIn("sp_eliminar_comando", ALLOWED_PROCEDURES)

    def test_eliminar_vehiculo_hace_baja_logica(self):
        with patch.object(operaciones, "desactivar_vehiculo", return_value=True):
            respuesta = operaciones.eliminar_vehiculo(1)
        self.assertFalse(respuesta["activo"])
        self.assertIn("historial se conserva", respuesta["mensaje"])

        with patch.object(operaciones, "desactivar_vehiculo", return_value=False):
            self.assertIn("ya estaba", operaciones.eliminar_vehiculo(1)["mensaje"])

    def test_eliminar_vehiculo_inexistente_404_y_error_bd_500(self):
        with patch.object(operaciones, "desactivar_vehiculo", return_value=None):
            with self.assertRaises(HTTPException) as no_existe:
                operaciones.eliminar_vehiculo(99)
        self.assertEqual(no_existe.exception.status_code, 404)

        with patch.object(operaciones, "desactivar_vehiculo", side_effect=pymysql.MySQLError()):
            with self.assertRaises(HTTPException) as falla:
                operaciones.eliminar_vehiculo(1)
        self.assertEqual(falla.exception.status_code, 500)

    def test_eliminar_comando_hace_baja_logica(self):
        with patch.object(operaciones, "desactivar_comando", return_value=True):
            respuesta = operaciones.eliminar_comando(3)
        self.assertFalse(respuesta["activo"])
        with patch.object(operaciones, "desactivar_comando", return_value=None):
            with self.assertRaises(HTTPException) as no_existe:
                operaciones.eliminar_comando(99)
        self.assertEqual(no_existe.exception.status_code, 404)


class FakeCursor:
    def __init__(self, fila):
        self.fila = fila
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.sql.append((sql, params))

    def fetchone(self):
        return self.fila


class FakeConnection:
    def __init__(self, fila):
        self.cursor_obj = FakeCursor(fila)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class BajasLogicasSqlTests(unittest.TestCase):
    def test_desactivar_usa_update_parametrizado_y_nunca_delete(self):
        conexion = FakeConnection({"activo": 1})
        with patch.object(bajas_logicas, "get_connection", return_value=conexion):
            self.assertTrue(bajas_logicas.desactivar_vehiculo(1))
        sentencias = [sql for sql, _ in conexion.cursor_obj.sql]
        self.assertTrue(all("DELETE" not in sql.upper() for sql in sentencias))
        self.assertIn("UPDATE vehiculos SET activo = 0", sentencias[1])
        self.assertEqual(conexion.cursor_obj.sql[1][1], (1,))
        self.assertEqual(conexion.commits, 1)

    def test_desactivar_inexistente_no_escribe(self):
        conexion = FakeConnection(None)
        with patch.object(bajas_logicas, "get_connection", return_value=conexion):
            self.assertIsNone(bajas_logicas.desactivar_comando(42))
        self.assertEqual(len(conexion.cursor_obj.sql), 1)
        self.assertEqual(conexion.commits, 0)
        self.assertEqual(conexion.rollbacks, 1)


class ValidacionPasosDemoTests(unittest.TestCase):
    def _cargar(self, vehiculo_activo=1, comando=None):
        comando = comando or {"id_comando": 1, "codigo": 1, "activo": 1}

        def execute(_conexion, procedimiento, _params):
            return {
                "sp_obtener_vehiculo": [[{"id_vehiculo": 1, "activo": vehiculo_activo}]],
                "sp_obtener_demos": [[{"id_demo": 1, "nombre": "d", "activo": 1}]],
                "sp_obtener_movimientos_demo": [[{
                    "id_demo_movimiento": 1, "orden": 1, "id_comando": 1,
                    "comando": "x", "codigo": 1, "duracion_ms": 1000, "pausa_ms": 0,
                }]],
                "sp_obtener_comandos_vehiculo": [[comando]],
            }[procedimiento]

        class Conexion:
            def close(self):
                pass

        with patch.object(demo_executor, "get_connection", return_value=Conexion()), \
             patch.object(demo_executor, "execute_procedure", side_effect=execute):
            return demo_executor.cargar_datos_demo(1, 1)

    def test_demo_valida_se_carga(self):
        demo, pasos = self._cargar()
        self.assertEqual(demo["id_demo"], 1)
        self.assertEqual(len(pasos), 1)

    def test_rechaza_vehiculo_dado_de_baja(self):
        with self.assertRaisesRegex(ValueError, "dado de baja"):
            self._cargar(vehiculo_activo=0)

    def test_rechaza_comando_desactivado(self):
        with self.assertRaisesRegex(ValueError, "desactivado"):
            self._cargar(comando={"id_comando": 1, "codigo": 1, "activo": 0})

    def test_rechaza_codigo_13(self):
        with self.assertRaisesRegex(ValueError, "código no admitido"):
            self._cargar(comando={"id_comando": 1, "codigo": 13, "activo": 1})

    def test_rechaza_comando_de_otra_unidad(self):
        with self.assertRaisesRegex(ValueError, "otra unidad"):
            self._cargar(comando={"id_comando": 77, "codigo": 1, "activo": 1})


if __name__ == "__main__":
    unittest.main()
