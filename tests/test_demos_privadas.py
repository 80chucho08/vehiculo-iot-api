"""Etapa 6: demos privadas (privacidad, editor del comprador) y validación de seguridad física.

Mismo escenario que test_autorizacion: unidad 2 de User A (10), unidad 3 de User B (11), admin 1.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from app.api.routes import consultas, mis_vehiculos
from app.services.validacion_demos import errores_pasos
from tests.test_autorizacion import ADMIN, USER_A, USER_B, UNIDADES, BaseApi, cabecera

DEMOS = {
    1: {"id_demo": 1, "id_vehiculo": 2, "nombre": "Fábrica", "tipo": "fabrica", "id_propietario": None, "activo": 1},
    5: {"id_demo": 5, "id_vehiculo": 2, "nombre": "Privada de A", "tipo": "privada", "id_propietario": USER_A, "activo": 1},
    6: {"id_demo": 6, "id_vehiculo": 2, "nombre": "Privada del dueño anterior", "tipo": "privada", "id_propietario": 99, "activo": 1},
}
CONTEXTO = {
    "limites": {"duracion_max_paso_ms": 3000, "pasos_max_demo": 3},
    "comandos": [
        {"id_comando": 21, "nombre": "Garra arriba", "codigo": 1, "activo": 1, "duracion_max_ms": None},
        {"id_comando": 22, "nombre": "Girar", "codigo": 2, "activo": 1, "duracion_max_ms": 1500},
        {"id_comando": 25, "nombre": "Control ON/OFF", "codigo": 5, "activo": 0, "duracion_max_ms": None},
        {"id_comando": 33, "nombre": "Demo local", "codigo": 13, "activo": 1, "duracion_max_ms": None},
    ],
}
paso = lambda c=21, d=1000, p=0: {"id_comando": c, "duracion_ms": d, "pausa_ms": p}  # noqa: E731


class ValidacionSeguridadFisicaTests(unittest.TestCase):
    def test_demo_valida(self):
        self.assertEqual(errores_pasos([paso(), paso(22, 1500, 200)], CONTEXTO), [])

    def test_rechazos(self):
        casos = {
            "sin pasos": ([], "al menos un paso"),
            "demasiados pasos": ([paso()] * 4, "como máximo 3 pasos"),
            "comando de otra unidad": ([paso(c=999)], "no pertenece"),
            "comando desactivado": ([paso(c=25)], "desactivado"),
            "código 13": ([paso(c=33)], "no es un movimiento válido"),
            "excede el tope del modelo": ([paso(d=3500)], "entre 100 y 3000"),
            "excede el tope del comando": ([paso(c=22, d=2000)], "entre 100 y 1500"),
            "paso demasiado corto": ([paso(d=50)], "entre 100"),
            "pausa excesiva": ([paso(p=20000)], "pausa"),
        }
        for nombre, (pasos, fragmento) in casos.items():
            with self.subTest(nombre):
                errores = errores_pasos(pasos, CONTEXTO)
                self.assertTrue(any(fragmento in e for e in errores), errores)

    def test_duracion_total(self):
        contexto = {**CONTEXTO, "limites": {"duracion_max_paso_ms": 3000, "pasos_max_demo": 50}}
        errores = errores_pasos([paso(d=3000, p=10000)] * 10, contexto)  # 130 s
        self.assertTrue(any("máximo es 120 s" in e for e in errores))


class PrivacidadDemosTests(BaseApi):
    def setUp(self):
        super().setUp()
        filas = [{k: v for k, v in d.items()} for d in DEMOS.values()]
        for parche in (patch.object(consultas, "consultar_procedimiento", return_value=filas),
                       patch("app.api.deps.una", side_effect=lambda sql, params: DEMOS.get(params[0]))):
            parche.start()
            self.addCleanup(parche.stop)

    def nombres(self, respuesta):
        return sorted(d["nombre"] for d in respuesta.json()["demos"])

    def test_dueno_ve_fabrica_y_sus_privadas_no_las_del_dueno_anterior(self):
        r = self.cliente.get("/api/vehiculos/2/demos", headers=cabecera(USER_A))
        self.assertEqual(self.nombres(r), ["Fábrica", "Privada de A"])

    def test_admin_solo_ve_las_de_fabrica(self):
        r = self.cliente.get("/api/vehiculos/2/demos", headers=cabecera(ADMIN))
        self.assertEqual(self.nombres(r), ["Fábrica"])

    def test_pasos_de_demo_privada_ajena_responden_404(self):
        self.assertEqual(self.cliente.get("/api/demos/5/movimientos", headers=cabecera(ADMIN)).status_code, 404)
        self.assertEqual(self.cliente.get("/api/demos/6/movimientos", headers=cabecera(USER_A)).status_code, 404)
        self.assertEqual(self.cliente.get("/api/demos/5/movimientos", headers=cabecera(USER_A)).status_code, 200)

    def test_admin_con_soporte_no_ejecuta_la_demo_privada_del_comprador(self):
        self.soporte.add((2, ADMIN))
        r = self.cliente.post("/api/vehiculos/2/demos/5/ejecutar", headers=cabecera(ADMIN))
        self.assertEqual(r.status_code, 404)

    def test_user_b_no_ejecuta_demos_de_la_unidad_de_a(self):
        r = self.cliente.post("/api/vehiculos/2/demos/1/ejecutar", headers=cabecera(USER_B))
        self.assertEqual(r.status_code, 404)


class EditorDemosTests(BaseApi):
    def setUp(self):
        super().setUp()
        for parche in (
            patch.object(mis_vehiculos.propiedad, "unidad", side_effect=lambda i: UNIDADES.get(i)),
            patch.object(mis_vehiculos.repo_demos, "contexto_unidad", return_value=CONTEXTO),
        ):
            parche.start()
            self.addCleanup(parche.stop)
        self.cuerpo = {"nombre": "Mi rutina", "pasos": [paso(), paso(22, 1200, 300)]}

    def test_crear_reemplazar_y_eliminar(self):
        with patch.object(mis_vehiculos.repo_demos, "crear_privada", return_value=40) as crear, \
             patch.object(mis_vehiculos.repo_demos, "reemplazar_privada", return_value=True) as reemplazar, \
             patch.object(mis_vehiculos.repo_demos, "eliminar_privada", return_value=True):
            r = self.cliente.post("/api/mis-vehiculos/2/demos", json=self.cuerpo, headers=cabecera(USER_A))
            self.assertEqual((r.status_code, r.json()["id_demo"]), (201, 40))
            self.assertEqual(crear.call_args.args[:2], (2, USER_A))  # dueño = el del token
            reordenada = {**self.cuerpo, "pasos": list(reversed(self.cuerpo["pasos"]))}
            r = self.cliente.put("/api/mis-vehiculos/2/demos/40", json=reordenada, headers=cabecera(USER_A))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(reemplazar.call_args.args[6][0]["id_comando"], 22)
            self.assertEqual(self.cliente.delete("/api/mis-vehiculos/2/demos/40", headers=cabecera(USER_A)).status_code, 204)
        acciones = [a for a, _ in self.acciones_auditadas()]
        for accion in ("demo.privada_crear", "demo.privada_actualizar", "demo.privada_eliminar"):
            self.assertIn(accion, acciones)

    def test_paso_inseguro_responde_422_con_detalle(self):
        cuerpo = {"nombre": "x", "pasos": [paso(d=3500)]}
        with patch.object(mis_vehiculos.repo_demos, "crear_privada") as crear:
            r = self.cliente.post("/api/mis-vehiculos/2/demos", json=cuerpo, headers=cabecera(USER_A))
        self.assertEqual(r.status_code, 422)
        self.assertIn("entre 100 y 3000", r.json()["detail"][0]["msg"])
        crear.assert_not_called()

    def test_pydantic_rechaza_mas_de_10_s_aunque_el_modelo_lo_permitiera(self):
        r = self.cliente.post("/api/mis-vehiculos/2/demos", json={"nombre": "x", "pasos": [paso(d=20000)]},
                              headers=cabecera(USER_A))
        self.assertEqual(r.status_code, 422)

    def test_unidad_ajena_404_y_admin_403(self):
        self.assertEqual(self.cliente.post("/api/mis-vehiculos/3/demos", json=self.cuerpo, headers=cabecera(USER_A)).status_code, 404)
        self.assertEqual(self.cliente.get("/api/mis-vehiculos/3/demos", headers=cabecera(USER_A)).status_code, 404)
        self.assertEqual(self.cliente.post("/api/mis-vehiculos/2/demos", json=self.cuerpo, headers=cabecera(ADMIN)).status_code, 403)

    def test_demo_ajena_de_la_misma_unidad_404(self):
        with patch.object(mis_vehiculos.repo_demos, "reemplazar_privada", return_value=False), \
             patch.object(mis_vehiculos.repo_demos, "eliminar_privada", return_value=False):
            self.assertEqual(self.cliente.put("/api/mis-vehiculos/2/demos/6", json=self.cuerpo, headers=cabecera(USER_A)).status_code, 404)
            self.assertEqual(self.cliente.delete("/api/mis-vehiculos/2/demos/6", headers=cabecera(USER_A)).status_code, 404)

    def test_no_se_edita_con_una_demo_en_curso(self):
        with patch.object(mis_vehiculos.demo_execution_manager, "demo_activa", return_value=True):
            r = self.cliente.put("/api/mis-vehiculos/2/demos/5", json=self.cuerpo, headers=cabecera(USER_A))
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
