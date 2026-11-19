"""Pruebas negativas de autenticación y autorización (sin base de datos: repositorios simulados).

Escenario: unidad 1 = exhibición sin comprador; unidad 2 = vendida a User A (id 10);
unidad 3 = vendida a User B (id 11); admin = id 1.
"""

import os
import unittest
from datetime import timedelta
from unittest.mock import patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

import jwt
from fastapi.testclient import TestClient

from app.api import deps
from app.api.routes import consultas, mis_vehiculos, operaciones
from app.core import seguridad
from app.main import app
from app.services import auth as servicio_auth
from app.services.autorizacion import ANONIMO, Accion, Actor, evaluar

ADMIN, USER_A, USER_B = 1, 10, 11
USUARIOS = {
    ADMIN: {"rol": "admin", "email": "admin@prueba.test", "nombre": "Admin"},
    USER_A: {"rol": "usuario", "email": "a@prueba.test", "nombre": "A"},
    USER_B: {"rol": "usuario", "email": "b@prueba.test", "nombre": "B"},
}
UNIDADES = {
    1: {"id_vehiculo": 1, "tipo_unidad": "exhibicion", "activo": 1, "id_propietario": None},
    2: {"id_vehiculo": 2, "tipo_unidad": "venta", "activo": 1, "id_propietario": USER_A},
    3: {"id_vehiculo": 3, "tipo_unidad": "venta", "activo": 1, "id_propietario": USER_B},
}


def token(id_usuario: int, sid: str | None = None) -> str:
    expira = seguridad.ahora() + timedelta(minutes=30)
    return seguridad.emitir_token(sid or f"sid-{id_usuario}", id_usuario, expira)


def cabecera(id_usuario: int) -> dict:
    return {"Authorization": f"Bearer {token(id_usuario)}"}


class BaseApi(unittest.TestCase):
    """Simula sesiones, unidades, soporte, auditoría y las consultas de las rutas."""

    sesiones_revocadas: set = set()
    soporte: set = set()  # (id_vehiculo, id_admin)

    def setUp(self):
        self.sesiones_revocadas = set()
        self.soporte = set()
        self.auditoria = []

        def sesion_vigente(sid):
            if sid in self.sesiones_revocadas or not sid.startswith("sid-"):
                return None
            id_usuario = int(sid.split("-")[1])
            datos = USUARIOS.get(id_usuario)
            if datos is None:
                return None
            return {"sid": sid, "id_usuario": id_usuario, "activo": 1, **datos}

        parches = [
            patch("app.services.auth.sesiones.obtener_vigente", side_effect=sesion_vigente),
            patch("app.api.deps.propiedad.unidad", side_effect=lambda i: UNIDADES.get(i)),
            patch("app.api.deps.propiedad.soporte_vigente",
                  side_effect=lambda v, a: {"id_autorizacion": 1} if (v, a) in self.soporte else None),
            patch("app.api.deps.auditoria.registrar", side_effect=lambda *a, **k: self.auditoria.append((a, k))),
            patch.object(consultas, "consultar_procedimiento", return_value=[{"ok": 1}]),
        ]
        for parche in parches:
            parche.start()
            self.addCleanup(parche.stop)
        self.cliente = TestClient(app)

    def acciones_auditadas(self):
        return [(args[0], kwargs.get("resultado", "ok")) for args, kwargs in self.auditoria]


class ReglaPuraTests(unittest.TestCase):
    """Matriz de la regla `evaluar`, independiente de HTTP."""

    def test_matriz(self):
        admin = Actor("admin", ADMIN)
        a = Actor("usuario", USER_A)
        casos = [
            (a, 2, Accion.OPERAR, False, False, (True, 200)),        # dueño
            (a, 3, Accion.MONITOREAR, False, False, (False, 404)),   # ajena → 404 (no revela)
            (a, 3, Accion.VER, False, False, (False, 404)),
            (a, 99, Accion.VER, False, False, (False, 404)),         # inexistente → mismo 404
            (admin, 2, Accion.VER, False, False, (True, 200)),       # inventario
            (admin, 2, Accion.OPERAR, False, False, (False, 403)),   # vendida sin consentimiento
            (admin, 2, Accion.MONITOREAR, False, False, (False, 403)),
            (admin, 2, Accion.OPERAR, True, False, (True, 200)),     # con soporte vigente
            (admin, 1, Accion.OPERAR, False, False, (True, 200)),    # exhibición
            (ANONIMO, 1, Accion.OPERAR, False, False, (True, 200)),  # transición 4.1/4.2
            (ANONIMO, 2, Accion.VER, False, False, (False, 404)),    # vendida: nunca anónimo
            (ANONIMO, 1, Accion.VER, False, True, (False, 401)),     # AUTH_OBLIGATORIA
            (a, 1, Accion.OPERAR, False, True, (False, 404)),        # obligatoria: exhibición no es suya
            (a, 2, Accion.OPERAR, False, True, (True, 200)),
        ]
        for actor, unidad, accion, soporte, obligatoria, esperado in casos:
            with self.subTest(actor=actor.rol, unidad=unidad, accion=accion.value, obligatoria=obligatoria):
                d = evaluar(actor, UNIDADES.get(unidad), accion, soporte_vigente=soporte, auth_obligatoria=obligatoria)
                self.assertEqual((d.permitido, 200 if d.permitido else d.status), esperado)


class AislamientoEntreCompradoresTests(BaseApi):
    def test_user_a_no_consulta_ni_opera_la_unidad_de_b(self):
        for metodo, ruta in [
            ("get", "/api/vehiculos/3"),
            ("get", "/api/vehiculos/3/estado"),
            ("get", "/api/vehiculos/3/historial"),
            ("get", "/api/vehiculos/3/comandos"),
            ("post", "/api/vehiculos/3/comandos/5/ejecutar"),
            ("post", "/api/vehiculos/3/detener"),
            ("post", "/api/vehiculos/3/demos/1/ejecutar"),
            ("post", "/api/vehiculos/3/demos/cancelar"),
        ]:
            with self.subTest(ruta=ruta):
                respuesta = getattr(self.cliente, metodo)(ruta, headers=cabecera(USER_A))
                self.assertEqual(respuesta.status_code, 404, respuesta.text)
                self.assertEqual(respuesta.json()["detail"], "Vehículo no encontrado.")
        self.assertIn(("acceso.denegado", "denegado"), self.acciones_auditadas())

    def test_user_a_si_monitorea_su_unidad(self):
        respuesta = self.cliente.get("/api/vehiculos/2/historial", headers=cabecera(USER_A))
        self.assertEqual(respuesta.status_code, 200)

    def test_unidad_ajena_e_inexistente_responden_igual(self):
        ajena = self.cliente.get("/api/vehiculos/3/estado", headers=cabecera(USER_A))
        inexistente = self.cliente.get("/api/vehiculos/999/estado", headers=cabecera(USER_A))
        self.assertEqual((ajena.status_code, ajena.json()), (inexistente.status_code, inexistente.json()))

    def test_anonimo_no_accede_a_unidad_vendida_ni_en_transicion(self):
        self.assertEqual(self.cliente.get("/api/vehiculos/2/historial").status_code, 404)
        self.assertEqual(self.cliente.post("/api/vehiculos/2/detener").status_code, 404)


class AdministradorSinConsentimientoTests(BaseApi):
    def test_admin_no_monitorea_ni_opera_unidad_vendida_sin_soporte(self):
        for metodo, ruta in [("get", "/api/vehiculos/2/historial"), ("post", "/api/vehiculos/2/detener"),
                             ("post", "/api/vehiculos/2/comandos/1/ejecutar")]:
            with self.subTest(ruta=ruta):
                respuesta = getattr(self.cliente, metodo)(ruta, headers=cabecera(ADMIN))
                self.assertEqual(respuesta.status_code, 403)
                self.assertIn("autorización de soporte", respuesta.json()["detail"])

    def test_admin_ve_inventario_de_la_unidad_vendida(self):
        self.assertEqual(self.cliente.get("/api/vehiculos/2/comandos", headers=cabecera(ADMIN)).status_code, 200)

    def test_con_soporte_vigente_si_monitorea_y_queda_auditado(self):
        self.soporte.add((2, ADMIN))
        respuesta = self.cliente.get("/api/vehiculos/2/historial", headers=cabecera(ADMIN))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(("soporte.monitorear", "ok"), self.acciones_auditadas())

    def test_soporte_de_otra_unidad_no_sirve(self):
        self.soporte.add((3, ADMIN))
        self.assertEqual(self.cliente.get("/api/vehiculos/2/historial", headers=cabecera(ADMIN)).status_code, 403)


class TokensTests(BaseApi):
    def test_token_invalido_no_se_degrada_a_anonimo(self):
        # La unidad 1 admite anónimos en transición, pero un token malo se rechaza igual.
        respuesta = self.cliente.get("/api/vehiculos/1/historial", headers={"Authorization": "Bearer basura"})
        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.headers.get("www-authenticate"), "Bearer")

    def test_token_con_alg_none_o_firma_ajena_se_rechaza(self):
        carga = {"iss": seguridad.EMISOR, "sub": str(ADMIN), "sid": f"sid-{ADMIN}", "exp": 4102444800}
        sin_firma = jwt.encode(carga, key=None, algorithm="none")
        otra_firma = jwt.encode(carga, "otro-secreto-de-mas-de-32-caracteres-xx", algorithm="HS256")
        for falso in (sin_firma, otra_firma):
            with self.subTest(token=falso[:20]):
                r = self.cliente.get("/api/admin/dashboard", headers={"Authorization": f"Bearer {falso}"})
                self.assertEqual(r.status_code, 401)

    def test_token_vencido(self):
        vencido = seguridad.emitir_token(f"sid-{ADMIN}", ADMIN, seguridad.ahora() - timedelta(minutes=1))
        r = self.cliente.get("/api/admin/usuarios", headers={"Authorization": f"Bearer {vencido}"})
        self.assertEqual(r.status_code, 401)

    def test_sesion_revocada_por_logout(self):
        self.sesiones_revocadas.add(f"sid-{USER_A}")
        r = self.cliente.get("/api/vehiculos/2/historial", headers=cabecera(USER_A))
        self.assertEqual(r.status_code, 401)

    def test_el_rol_sale_de_la_base_no_del_cliente(self):
        # Token de User A con un claim "rol": "admin" añadido y bien firmado: se ignora.
        carga = jwt.decode(token(USER_A), options={"verify_signature": False})
        carga["rol"] = "admin"
        manipulado = jwt.encode(carga, seguridad._SECRETO, algorithm="HS256")
        r = self.cliente.get("/api/admin/usuarios", headers={"Authorization": f"Bearer {manipulado}",
                                                             "X-Rol": "admin"}, params={"rol": "admin"})
        self.assertEqual(r.status_code, 403)

    def test_sid_de_otro_usuario_no_sirve(self):
        # Token firmado con sub=A pero sid de la sesión del admin.
        mezclado = seguridad.emitir_token(f"sid-{ADMIN}", USER_A, seguridad.ahora() + timedelta(minutes=5))
        r = self.cliente.get("/api/vehiculos/2/historial", headers={"Authorization": f"Bearer {mezclado}"})
        self.assertEqual(r.status_code, 401)


class RolesYRutasTests(BaseApi):
    def test_rutas_de_administracion(self):
        for metodo, ruta, cuerpo in [
            ("get", "/api/admin/usuarios", None),
            ("get", "/api/vehiculos", None),
            ("post", "/api/vehiculos", {"nombre": "x"}),
            ("delete", "/api/vehiculos/1", None),
            ("delete", "/api/comandos/1", None),
            ("put", "/api/vehiculos/1/estado", {"conectado": True}),
            ("post", "/api/vehiculos/1/historial", {"origen": "api"}),
            ("post", "/api/admin/asignaciones", {"id_vehiculo": 2, "id_usuario": USER_B}),
        ]:
            with self.subTest(ruta=ruta, metodo=metodo):
                kwargs = {"json": cuerpo} if cuerpo is not None else {}
                self.assertEqual(getattr(self.cliente, metodo)(ruta, **kwargs).status_code, 401)
                self.assertEqual(getattr(self.cliente, metodo)(ruta, headers=cabecera(USER_A), **kwargs).status_code, 403)

    def test_borrado_de_vehiculo_por_admin_queda_auditado(self):
        with patch.object(operaciones, "desactivar_vehiculo", return_value=True):
            r = self.cliente.delete("/api/vehiculos/1", headers=cabecera(ADMIN))
        self.assertEqual(r.status_code, 200)
        self.assertIn(("vehiculo.baja", "ok"), self.acciones_auditadas())

    def test_operacion_fallida_de_admin_tambien_se_audita(self):
        with patch.object(operaciones, "desactivar_vehiculo", return_value=None):
            r = self.cliente.delete("/api/vehiculos/99", headers=cabecera(ADMIN))
        self.assertEqual(r.status_code, 404)
        self.assertIn(("vehiculo.baja", "error"), self.acciones_auditadas())

    def test_mis_vehiculos_solo_para_compradores(self):
        self.assertEqual(self.cliente.get("/api/mis-vehiculos").status_code, 401)
        self.assertEqual(self.cliente.get("/api/mis-vehiculos", headers=cabecera(ADMIN)).status_code, 403)
        with patch.object(mis_vehiculos.propiedad, "mis_vehiculos", return_value=[]) as consulta:
            r = self.cliente.get("/api/mis-vehiculos?id_usuario=11", headers=cabecera(USER_A))
        self.assertEqual(r.status_code, 200)
        consulta.assert_called_once_with(USER_A)  # el id sale del token, no de la query

    def test_soporte_solo_sobre_unidad_propia(self):
        with patch.object(mis_vehiculos.propiedad, "unidad", side_effect=lambda i: UNIDADES.get(i)), \
             patch.object(mis_vehiculos.propiedad, "otorgar_soporte", return_value=7) as otorgar:
            ajena = self.cliente.post("/api/mis-vehiculos/3/soporte", json={"motivo": "revisión"}, headers=cabecera(USER_A))
            propia = self.cliente.post("/api/mis-vehiculos/2/soporte", json={"motivo": "revisión", "minutos": 30},
                                       headers=cabecera(USER_A))
        self.assertEqual(ajena.status_code, 404)
        self.assertEqual(propia.status_code, 201)
        otorgar.assert_called_once()
        self.assertEqual(otorgar.call_args.args[:2], (2, USER_A))

    def test_validaciones_422(self):
        r = self.cliente.post("/api/mis-vehiculos/2/soporte", json={"motivo": "x", "minutos": 99999}, headers=cabecera(USER_A))
        self.assertEqual(r.status_code, 422)
        r = self.cliente.post("/api/auth/login", json={"email": "no-es-email", "password": "x"})
        self.assertEqual(r.status_code, 422)


class AutenticacionObligatoriaTests(BaseApi):
    def test_con_auth_obligatoria_anonimo_recibe_401_en_exhibicion(self):
        with patch("app.services.autorizacion.settings.auth_obligatoria", True):
            self.assertEqual(self.cliente.post("/api/vehiculos/1/detener").status_code, 401)
            self.assertEqual(self.cliente.get("/api/vehiculos/1/estado").status_code, 401)


class LoginTests(unittest.TestCase):
    def setUp(self):
        servicio_auth.limitador_login.limpiar()
        self.hash = seguridad.hash_password("contraseña-correcta-123")
        self.cliente = TestClient(app)
        self.fila = {"id_usuario": USER_A, "email": "a@prueba.test", "nombre": "A", "rol": "usuario",
                     "activo": 1, "password_hash": self.hash, "intentos_fallidos": 0, "bloqueado_hasta": None}
        for parche in (patch("app.api.routes.auth.auditoria.registrar"),
                       patch("app.services.auth.usuarios.registrar_acceso"),
                       patch("app.services.auth.sesiones.crear")):
            parche.start()
            self.addCleanup(parche.stop)

    def login(self, email="a@prueba.test", password="contraseña-correcta-123"):
        return self.cliente.post("/api/auth/login", json={"email": email, "password": password})

    def test_login_correcto_devuelve_token_corto(self):
        with patch("app.services.auth.usuarios.por_email_con_hash", return_value=self.fila):
            r = self.login(email="A@Prueba.test")  # el email se normaliza a minúsculas
        self.assertEqual(r.status_code, 200)
        cuerpo = r.json()
        self.assertEqual(cuerpo["usuario"]["rol"], "usuario")
        self.assertNotIn("password_hash", r.text)
        datos = seguridad.leer_token(cuerpo["access_token"])
        self.assertLessEqual(datos.expira - seguridad.ahora(), timedelta(minutes=30, seconds=1))

    def test_mismo_mensaje_para_email_inexistente_password_mala_y_cuenta_bloqueada(self):
        bloqueada = {**self.fila, "bloqueado_hasta": seguridad.ahora() + timedelta(minutes=5)}
        with patch("app.services.auth.usuarios.registrar_fallo") as fallo:
            with patch("app.services.auth.usuarios.por_email_con_hash", return_value=None):
                inexistente = self.login()
            with patch("app.services.auth.usuarios.por_email_con_hash", return_value=self.fila):
                mala = self.login(password="incorrecta")
            with patch("app.services.auth.usuarios.por_email_con_hash", return_value=bloqueada):
                bloqueo = self.login()  # contraseña correcta, pero bloqueada
        for r in (inexistente, mala, bloqueo):
            self.assertEqual((r.status_code, r.json()["detail"]), (401, "Email o contraseña incorrectos."))
        fallo.assert_called_once()  # solo la contraseña mala suma intento

    def test_cuenta_inactiva_no_inicia_sesion(self):
        with patch("app.services.auth.usuarios.por_email_con_hash", return_value={**self.fila, "activo": 0}):
            self.assertEqual(self.login().status_code, 401)

    def test_limite_por_ip_responde_429(self):
        with patch("app.services.auth.usuarios.por_email_con_hash", return_value=None):
            codigos = [self.login(password="x").status_code for _ in range(11)]
        self.assertEqual(codigos[:10], [401] * 10)
        self.assertEqual(codigos[10], 429)


if __name__ == "__main__":
    unittest.main()
