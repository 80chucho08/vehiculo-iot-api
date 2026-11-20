"""Etapas 7 y 8: invitados con reserva exclusiva y WebSocket autenticado por unidad (sin base de datos)."""

import asyncio
import os
import time
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from app.api.routes import websocket as websocket_routes
from app.core import seguridad
from app.core.websocket_manager import websocket_manager
from app.repositories.base import Conflicto
from app.services import acceso_wss, vigilante
from app.services.autorizacion import ANONIMO, Accion, Actor, evaluar
from tests.test_autorizacion import ADMIN, USER_A, UNIDADES, BaseApi, cabecera

EXHIBICION_RESERVADA = {**UNIDADES[1], "reserva_sid": "res-1"}
INVITADO = Actor(rol="invitado", sid="res-1", id_sesion_invitado=7, id_vehiculo_reservado=1)
OTRO_INVITADO = Actor(rol="invitado", sid="res-2", id_sesion_invitado=8, id_vehiculo_reservado=1)


class ReglaInvitadoTests(unittest.TestCase):
    def test_matriz(self):
        admin = Actor("admin", ADMIN)
        casos = [
            (INVITADO, EXHIBICION_RESERVADA, Accion.OPERAR, (True, 200)),
            (INVITADO, EXHIBICION_RESERVADA, Accion.MONITOREAR, (True, 200)),
            (INVITADO, UNIDADES[2], Accion.VER, (False, 404)),            # otra unidad: no existe para él
            (INVITADO, UNIDADES[1], Accion.OPERAR, (False, 404)),         # su reserva ya no está vigente
            (OTRO_INVITADO, EXHIBICION_RESERVADA, Accion.OPERAR, (False, 404)),
            (admin, EXHIBICION_RESERVADA, Accion.OPERAR, (False, 409)),   # exclusividad: ni el admin
            (admin, EXHIBICION_RESERVADA, Accion.DETENER, (True, 200)),   # pero DETENER siempre
            (admin, EXHIBICION_RESERVADA, Accion.MONITOREAR, (True, 200)),
            (ANONIMO, EXHIBICION_RESERVADA, Accion.OPERAR, (False, 409)),
            (ANONIMO, EXHIBICION_RESERVADA, Accion.DETENER, (True, 200)),
            (Actor("usuario", USER_A), UNIDADES[2], Accion.OPERAR, (True, 200)),  # sus unidades, sin cambios
        ]
        for actor, unidad, accion, esperado in casos:
            with self.subTest(actor=actor.rol, sid=actor.sid, accion=accion.value):
                d = evaluar(actor, unidad, accion, auth_obligatoria=False)
                self.assertEqual((d.permitido, 200 if d.permitido else d.status), esperado)


class RutasInvitadoTests(BaseApi):
    def setUp(self):
        super().setUp()
        reservas = {"res-1": {"id_sesion_invitado": 7, "sid": "res-1", "id_vehiculo": 1}}
        for parche in (
            patch("app.services.auth.invitados.obtener_vigente", side_effect=lambda sid: reservas.get(sid)),
            patch("app.api.deps.propiedad.unidad",
                  side_effect=lambda i: EXHIBICION_RESERVADA if i == 1 else UNIDADES.get(i)),
        ):
            parche.start()
            self.addCleanup(parche.stop)
        expira = seguridad.ahora() + timedelta(minutes=5)
        self.h_invitado = {"Authorization": f"Bearer {seguridad.emitir_token_invitado('res-1', expira)}"}
        self.h_vencido = {"Authorization": f"Bearer {seguridad.emitir_token_invitado('res-9', expira)}"}

    def test_invitado_solo_ejecuta_comandos_permitidos(self):
        with patch("app.api.deps.una", return_value={"permitido_invitado": 0}):
            r = self.cliente.post("/api/vehiculos/1/comandos/5/ejecutar", headers=self.h_invitado)
        self.assertEqual(r.status_code, 403)
        self.assertIn("prueba de invitado", r.json()["detail"])

    def test_invitado_no_ve_otras_unidades_ni_rutas_privadas(self):
        self.assertEqual(self.cliente.get("/api/vehiculos/2/estado", headers=self.h_invitado).status_code, 404)
        self.assertEqual(self.cliente.get("/api/mis-vehiculos", headers=self.h_invitado).status_code, 401)
        self.assertEqual(self.cliente.get("/api/admin/dashboard", headers=self.h_invitado).status_code, 401)

    def test_reserva_terminada_invalida_el_token(self):
        self.assertEqual(self.cliente.get("/api/vehiculos/1/estado", headers=self.h_vencido).status_code, 401)

    def test_admin_no_opera_la_exhibicion_reservada_pero_si_la_detiene(self):
        r = self.cliente.post("/api/vehiculos/1/comandos/1/ejecutar", headers=cabecera(ADMIN))
        self.assertEqual(r.status_code, 409)
        r = self.cliente.post("/api/vehiculos/1/detener", headers=cabecera(ADMIN))
        self.assertNotIn(r.status_code, (401, 403, 404))  # pasa la autorización (409 = sin clientes WSS)

    def test_reservar(self):
        base = {"id_vehiculo": 1, "acepto_condiciones": True}
        exhib = {"id_vehiculo": 1, "tipo_unidad": "exhibicion", "activo": 1, "publicado": 1}
        with patch("app.api.routes.invitado.invitados.unidad_reservable", return_value=exhib), \
             patch("app.api.routes.invitado.invitados.reservas_recientes_ip", return_value=0), \
             patch("app.api.routes.invitado.auditoria.registrar"), \
             patch("app.api.routes.invitado.invitados.crear",
                   return_value={"id_sesion_invitado": 9, "expira_en": seguridad.ahora() + timedelta(minutes=5)}):
            r = self.cliente.post("/api/invitado/sesiones", json=base)
            self.assertEqual(r.status_code, 201)
            self.assertEqual(seguridad.leer_token(r.json()["access_token"]).tipo, "invitado")
            self.assertEqual(self.cliente.post("/api/invitado/sesiones", json={**base, "acepto_condiciones": False}).status_code, 400)
        with patch("app.api.routes.invitado.invitados.unidad_reservable", return_value={**exhib, "tipo_unidad": "venta"}):
            self.assertEqual(self.cliente.post("/api/invitado/sesiones", json=base).status_code, 404)
        with patch("app.api.routes.invitado.invitados.unidad_reservable", return_value=exhib), \
             patch("app.api.routes.invitado.invitados.reservas_recientes_ip", return_value=0), \
             patch("app.api.routes.invitado.invitados.crear", side_effect=Conflicto("en uso")):
            self.assertEqual(self.cliente.post("/api/invitado/sesiones", json=base).status_code, 409)
        with patch("app.api.routes.invitado.invitados.unidad_reservable", return_value=exhib), \
             patch("app.api.routes.invitado.invitados.reservas_recientes_ip", return_value=99):
            self.assertEqual(self.cliente.post("/api/invitado/sesiones", json=base).status_code, 429)

    def test_ticket_wss_exige_monitorear(self):
        self.assertEqual(self.cliente.post("/api/ws/ticket", json={"id_vehiculo": 2}, headers=cabecera(ADMIN)).status_code, 403)
        self.assertEqual(self.cliente.post("/api/ws/ticket", json={"id_vehiculo": 3}, headers=cabecera(USER_A)).status_code, 404)
        r = self.cliente.post("/api/ws/ticket", json={"id_vehiculo": 2}, headers=cabecera(USER_A))
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(acceso_wss.consumir_ticket(r.json()["ticket"], 2))


class TicketsTests(unittest.TestCase):
    def test_un_solo_uso_y_solo_para_su_unidad(self):
        actor = Actor("usuario", USER_A, sid="s")
        t = acceso_wss.emitir_ticket(actor, 2)
        self.assertIsNone(acceso_wss.consumir_ticket(t, 3))   # otra unidad (y ya se gastó)
        t = acceso_wss.emitir_ticket(actor, 2)
        self.assertEqual(acceso_wss.consumir_ticket(t, 2), actor)
        self.assertIsNone(acceso_wss.consumir_ticket(t, 2))   # reutilizado

    def test_vencido(self):
        t = acceso_wss.emitir_ticket(Actor("usuario", USER_A), 2)
        with patch.object(acceso_wss.time, "monotonic", return_value=time.monotonic() + 31):
            self.assertIsNone(acceso_wss.consumir_ticket(t, 2))

    def test_token_de_dispositivo(self):
        h = acceso_wss.hash_token("secreto-del-esp")
        with patch.object(acceso_wss, "una", return_value={"token_hash": h}):
            self.assertTrue(acceso_wss.dispositivo_autorizado(1, "secreto-del-esp"))
            self.assertFalse(acceso_wss.dispositivo_autorizado(1, "otro"))
            self.assertFalse(acceso_wss.dispositivo_autorizado(1, None))
        with patch.object(acceso_wss, "una", return_value=None):
            with patch.object(acceso_wss.settings, "dispositivo_token_obligatorio", False):
                self.assertTrue(acceso_wss.dispositivo_autorizado(1, None))   # transición
            with patch.object(acceso_wss.settings, "dispositivo_token_obligatorio", True):
                self.assertFalse(acceso_wss.dispositivo_autorizado(1, None))


class SocketFalso:
    def __init__(self, query):
        self.query_params = query
        self.cerrado = None
        self.enviados = []

    async def accept(self):
        pass

    async def close(self, code=1000, reason=""):
        self.cerrado = code

    async def send_json(self, m):
        self.enviados.append(m)

    async def receive_text(self):
        from starlette.websockets import WebSocketDisconnect
        raise WebSocketDisconnect()


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        websocket_manager.connections.clear()
        websocket_manager.actores.clear()

    async def test_sin_ticket_en_unidad_vendida_se_cierra_4401(self):
        ws = SocketFalso({"rol": "observador"})
        with patch.object(acceso_wss, "propiedad") as prop:
            prop.unidad.return_value = UNIDADES[2]
            await websocket_routes.websocket_vehiculo(ws, 2)
        self.assertEqual(ws.cerrado, 4401)
        self.assertEqual(ws.enviados, [])  # no recibió ningún dato de la unidad

    async def test_ticket_ajeno_o_reutilizado_se_rechaza(self):
        t = acceso_wss.emitir_ticket(Actor("usuario", USER_A, sid="s"), 2)
        acceso_wss.consumir_ticket(t, 2)
        ws = SocketFalso({"rol": "observador", "ticket": t})
        await websocket_routes.websocket_vehiculo(ws, 2)
        self.assertEqual(ws.cerrado, 4401)

    async def test_con_ticket_valido_recibe_estado(self):
        t = acceso_wss.emitir_ticket(Actor("usuario", USER_A, sid="s"), 2)
        ws = SocketFalso({"rol": "observador", "ticket": t})
        with patch.object(websocket_routes, "cargar_estado_inicial", return_value=([], [])):
            await websocket_routes.websocket_vehiculo(ws, 2)
        self.assertIsNone(ws.cerrado)
        self.assertEqual(ws.enviados[0]["tipo"], "estado_inicial")

    async def test_dispositivo_falso_no_desplaza_al_real(self):
        ws = SocketFalso({"rol": "dispositivo", "token": "adivinado"})
        with patch.object(acceso_wss, "una", return_value={"token_hash": acceso_wss.hash_token("real")}), \
             patch.object(websocket_manager, "reemplazar_dispositivo", new=AsyncMock()) as reemplazar:
            await websocket_routes.websocket_vehiculo(ws, 1)
        self.assertEqual(ws.cerrado, 4401)
        reemplazar.assert_not_called()

    async def test_revalidacion_cierra_conexiones_sin_permiso(self):
        ws_a, ws_b = SocketFalso({}), SocketFalso({})
        await websocket_manager.connect(2, ws_a, "observador", actor=Actor("usuario", USER_A, sid="vigente"))
        await websocket_manager.connect(2, ws_b, "observador", actor=Actor("usuario", USER_A, sid="revocada"))
        with patch.object(acceso_wss, "_revisar", side_effect=lambda v, a: a.sid == "vigente"):
            cerradas = await acceso_wss.revalidar_conexiones()
        self.assertEqual(cerradas, 1)
        self.assertEqual(ws_b.cerrado, 4403)
        self.assertIsNone(ws_a.cerrado)


class VigilanteTests(unittest.IsolatedAsyncioTestCase):
    async def test_reserva_vencida_detiene_y_desconecta(self):
        websocket_manager.connections.clear()
        ws = SocketFalso({})
        await websocket_manager.connect(1, ws, "observador", actor=INVITADO)
        detener = AsyncMock()
        with patch.object(vigilante.invitados, "cerrar_vencidas", return_value=[{"id_vehiculo": 1, "sid": "res-1"}]), \
             patch("app.api.routes.operaciones.detener_por_sistema", new=detener):
            await vigilante.ciclo_unico()
        detener.assert_awaited_once_with(1, "reserva_expirada")
        self.assertEqual(ws.cerrado, 4403)
        self.assertIn("reserva_finalizada", [m["tipo"] for m in ws.enviados])


if __name__ == "__main__":
    unittest.main()
