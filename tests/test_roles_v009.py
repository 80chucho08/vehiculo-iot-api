"""V009: cuenta de invitado, comandos propios, reportes de falla, gestión de demos del admin y
dispositivo compartido (sin base de datos: repositorios simulados)."""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from app.core.websocket_manager import WebSocketManager
from app.repositories import comandos as repo_comandos
from app.services.autorizacion import Accion, Actor, evaluar
from tests.test_autorizacion import ADMIN, USER_A, USER_B, USUARIOS, BaseApi, cabecera

INVITADO_CUENTA = 20
USUARIOS[INVITADO_CUENTA] = {"rol": "invitado", "email": "invitado@prueba.test", "nombre": "Invitado"}

EXHIBICION = {"id_vehiculo": 1, "tipo_unidad": "exhibicion", "activo": 1, "id_propietario": None}
STOCK = {"id_vehiculo": 4, "tipo_unidad": "venta", "activo": 1, "id_propietario": None}
VENDIDA = {"id_vehiculo": 2, "tipo_unidad": "venta", "activo": 1, "id_propietario": USER_A}


class CuentaInvitadoReglaTests(unittest.TestCase):
    def setUp(self):
        self.invitado = Actor("invitado", INVITADO_CUENTA, sid="sid-20")

    def test_controla_solo_la_exhibicion(self):
        for accion in Accion:
            self.assertTrue(evaluar(self.invitado, EXHIBICION, accion, auth_obligatoria=True).permitido, accion)
        for unidad in (STOCK, VENDIDA, {**EXHIBICION, "activo": 0}):
            decision = evaluar(self.invitado, unidad, Accion.OPERAR, auth_obligatoria=True)
            self.assertEqual((decision.permitido, decision.status), (False, 404))

    def test_respeta_la_reserva_de_un_invitado_anonimo(self):
        reservada = {**EXHIBICION, "reserva_sid": "res-1"}
        self.assertEqual(evaluar(self.invitado, reservada, Accion.OPERAR, auth_obligatoria=True).status, 409)
        self.assertTrue(evaluar(self.invitado, reservada, Accion.DETENER, auth_obligatoria=True).permitido)

    def test_no_se_confunde_con_la_reserva(self):
        self.assertFalse(self.invitado.es_reserva)
        self.assertTrue(Actor("invitado", sid="res-1").es_reserva)


class CuentaInvitadoApiTests(BaseApi):
    def test_comandos_no_permitidos_y_rutas_de_usuario(self):
        with patch("app.api.deps.una", return_value={"permitido_invitado": 0, "tipo": "fabrica", "id_propietario": None}):
            r = self.cliente.post("/api/vehiculos/1/comandos/5/ejecutar", headers=cabecera(INVITADO_CUENTA))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.cliente.get("/api/vehiculos/2/estado", headers=cabecera(INVITADO_CUENTA)).status_code, 404)
        self.assertEqual(self.cliente.get("/api/mis-vehiculos", headers=cabecera(INVITADO_CUENTA)).status_code, 403)
        self.assertEqual(self.cliente.post("/api/mis-vehiculos/1/demos", headers=cabecera(INVITADO_CUENTA),
                                           json={"nombre": "x", "pasos": [{"id_comando": 1, "duracion_ms": 500}]}).status_code, 403)
        self.assertEqual(self.cliente.get("/api/admin/reportes", headers=cabecera(INVITADO_CUENTA)).status_code, 403)

    def test_comando_propio_de_otro_usuario_no_se_ejecuta(self):
        ajeno = {"permitido_invitado": 0, "tipo": "usuario", "id_propietario": USER_B}
        with patch("app.api.deps.una", return_value=ajeno):
            r = self.cliente.post("/api/vehiculos/2/comandos/40/ejecutar", headers=cabecera(USER_A))
        self.assertEqual(r.status_code, 404)


class ComandosPropiosTests(BaseApi):
    def test_codigo_no_registrado_422_y_fabrica_no_se_elimina(self):
        with patch("app.api.routes.mis_vehiculos.propiedad.unidad", return_value=VENDIDA), \
             patch("app.api.routes.mis_vehiculos.repo_comandos.crear_propio",
                   side_effect=repo_comandos.MovimientoNoRegistrado("no registrado")):
            r = self.cliente.post("/api/mis-vehiculos/2/comandos", headers=cabecera(USER_A),
                                  json={"nombre": "Garra lenta", "codigo": 3})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"][0]["type"], "movimiento_no_registrado")
        # Pydantic: códigos fuera de 1-12 ni siquiera llegan al repositorio.
        with patch("app.api.routes.mis_vehiculos.propiedad.unidad", return_value=VENDIDA):
            self.assertEqual(self.cliente.post("/api/mis-vehiculos/2/comandos", headers=cabecera(USER_A),
                                               json={"nombre": "Turbo", "codigo": 99}).status_code, 422)
        with patch("app.api.routes.mis_vehiculos.propiedad.unidad", return_value=VENDIDA), \
             patch("app.api.routes.mis_vehiculos.repo_comandos.eliminar_propio", return_value=None):
            self.assertEqual(self.cliente.delete("/api/mis-vehiculos/2/comandos/1", headers=cabecera(USER_A)).status_code, 404)
        self.assertIn(("acceso.denegado", "denegado"), self.acciones_auditadas())

    def test_crear_y_eliminar_propio(self):
        with patch("app.api.routes.mis_vehiculos.propiedad.unidad", return_value=VENDIDA), \
             patch("app.api.routes.mis_vehiculos.repo_comandos.crear_propio", return_value=50) as crear, \
             patch("app.api.routes.mis_vehiculos.repo_comandos.eliminar_propio", return_value="eliminado"):
            r = self.cliente.post("/api/mis-vehiculos/2/comandos", headers=cabecera(USER_A),
                                  json={"nombre": " Saludo ", "codigo": 3, "duracion_max_ms": 800})
            self.assertEqual(r.status_code, 201)
            self.assertEqual(crear.call_args.args[:4], (2, USER_A, "Saludo", 3))
            self.assertEqual(self.cliente.delete("/api/mis-vehiculos/2/comandos/50", headers=cabecera(USER_A)).status_code, 204)

    def test_unidad_ajena_404(self):
        with patch("app.api.routes.mis_vehiculos.propiedad.unidad", return_value=VENDIDA):
            self.assertEqual(self.cliente.get("/api/mis-vehiculos/2/comandos", headers=cabecera(USER_B)).status_code, 404)


class ReportesYDemosAdminTests(BaseApi):
    def test_reporte_con_soporte(self):
        with patch("app.api.routes.mis_vehiculos.propiedad.unidad", return_value=VENDIDA), \
             patch("app.api.routes.mis_vehiculos.reportes.crear", return_value=7), \
             patch("app.api.routes.mis_vehiculos.propiedad.otorgar_soporte", return_value=3) as otorgar:
            r = self.cliente.post("/api/mis-vehiculos/2/reportes", headers=cabecera(USER_A),
                                  json={"descripcion": "La garra no sube", "autorizar_soporte": True, "minutos": 60})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["soporte"]["id_autorizacion"], 3)
        self.assertIn("Reporte #7", otorgar.call_args.args[3])
        with patch("app.api.routes.mis_vehiculos.propiedad.unidad", return_value=VENDIDA):
            r = self.cliente.post("/api/mis-vehiculos/2/reportes", headers=cabecera(USER_B), json={"descripcion": "No es mía"})
        self.assertEqual(r.status_code, 404)

    def test_resolver_revoca_el_soporte_y_revalida_wss(self):
        with patch("app.api.routes.admin.reportes.actualizar",
                   return_value={"id_vehiculo": 2, "estado": "en_revision", "soportes_revocados": 1}), \
             patch("app.api.routes.admin.acceso_wss.revalidar_conexiones", new=AsyncMock(return_value=0)) as revalidar:
            r = self.cliente.put("/api/admin/reportes/7", headers=cabecera(ADMIN), json={"estado": "resuelto", "respuesta": "Listo"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["soportes_revocados"], 1)
        revalidar.assert_awaited_once()
        self.assertEqual(self.cliente.put("/api/admin/reportes/7", headers=cabecera(USER_A), json={"estado": "resuelto"}).status_code, 403)

    def test_admin_gestiona_demo_privada_pero_usuario_no_usa_rutas_admin(self):
        privada = {"id_demo": 9, "id_vehiculo": 2, "tipo": "privada", "id_propietario": USER_A}
        contexto = {"limites": {"duracion_max_paso_ms": 3000, "pasos_max_demo": 20},
                    "comandos": [{"id_comando": 3, "nombre": "Garra abajo", "codigo": 3, "activo": 1, "duracion_max_ms": None}]}
        cuerpo = {"nombre": "Saludo de garra", "pasos": [{"id_comando": 3, "duracion_ms": 800, "pausa_ms": 0}]}
        with patch("app.api.routes.admin.repo_demos.demo", return_value=privada), \
             patch("app.api.routes.admin.repo_demos.contexto_unidad", return_value=contexto), \
             patch("app.api.routes.admin.repo_demos.reemplazar_admin", return_value=True) as reemplazar, \
             patch("app.api.routes.admin.repo_demos.eliminar_admin", return_value=True):
            self.assertEqual(self.cliente.put("/api/admin/demos/9", headers=cabecera(ADMIN), json=cuerpo).status_code, 200)
            reemplazar.assert_called_once()
            self.assertEqual(self.cliente.delete("/api/admin/demos/9", headers=cabecera(ADMIN)).status_code, 204)
            self.assertEqual(self.cliente.put("/api/admin/demos/9", headers=cabecera(USER_A), json=cuerpo).status_code, 403)
            inseguro = {**cuerpo, "pasos": [{"id_comando": 3, "duracion_ms": 9000, "pausa_ms": 0}]}
            self.assertEqual(self.cliente.put("/api/admin/demos/9", headers=cabecera(ADMIN), json=inseguro).status_code, 422)

    def test_crear_usuario_con_rol_invitado(self):
        with patch("app.api.routes.admin.usuarios.crear", return_value=30) as crear, \
             patch("app.api.routes.admin.usuarios.por_id", return_value={"id_usuario": 30, "rol": "invitado"}, create=True):
            r = self.cliente.post("/api/admin/usuarios", headers=cabecera(ADMIN),
                                  json={"email": "inv@prueba.test", "nombre": "Inv", "password": "una-frase-larga", "rol": "invitado"})
        self.assertIn(r.status_code, (200, 201))
        self.assertEqual(crear.call_args.args[-1], "invitado")


class DispositivoCompartidoTests(unittest.TestCase):
    def test_mensajes_de_la_unidad_espejo_llegan_al_esp_con_su_id(self):
        manager = WebSocketManager()
        dispositivo, observador = MagicMock(), MagicMock()
        dispositivo.send_json = AsyncMock()
        observador.send_json = AsyncMock()
        manager.connections = {1: {dispositivo: "dispositivo"}, 2: {observador: "observador"}}
        manager.fijar_espejos({2: 1, 3: 3})
        self.assertEqual(manager.espejos, {2: 1})
        self.assertEqual(manager.connected_clients(2, "dispositivo"), 1)
        self.assertEqual(manager.connected_clients(3, "dispositivo"), 0)

        enviados = asyncio.run(manager.send_to_vehicle(2, {"tipo": "comando_individual", "id_vehiculo": 2, "codigo": 3},
                                                       role="dispositivo"))
        self.assertEqual(enviados, 1)
        dispositivo.send_json.assert_awaited_once_with({"tipo": "comando_individual", "id_vehiculo": 1, "codigo": 3})
        observador.send_json.assert_not_awaited()

        # Difusión (role None): el observador de la unidad 2 recibe su id; el ESP, el suyo.
        asyncio.run(manager.send_to_vehicle(2, {"tipo": "demo_cancelada", "id_vehiculo": 2}))
        observador.send_json.assert_awaited_with({"tipo": "demo_cancelada", "id_vehiculo": 2})
        dispositivo.send_json.assert_awaited_with({"tipo": "demo_cancelada", "id_vehiculo": 1})

        asyncio.run(manager.avisar_estado_dispositivo(1, True))
        observador.send_json.assert_awaited_with({"tipo": "dispositivo_estado", "id_vehiculo": 2, "conectado": True})


if __name__ == "__main__":
    unittest.main()
