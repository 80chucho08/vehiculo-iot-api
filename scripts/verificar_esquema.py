"""Pruebas de la migración sobre una base de PRUEBA (todo dentro de una transacción con ROLLBACK).

  .venv/Scripts/python scripts/verificar_esquema.py --bd vehiculo_iot_prueba

Comprueba:
  A. Compatibilidad: los 21 procedimientos de 4.1/4.2 siguen funcionando (lectura y escritura).
  B. Restricciones nuevas: cada caso inválido es rechazado por MySQL con el error esperado.
  C. Datos migrados: modelo, número de serie, unidad de exhibición y permisos de invitado.
Sale con código 1 si algún caso falla. Se niega a correr sobre producción.
"""

from __future__ import annotations

import argparse
import sys

import pymysql

from bd_comun import BD_PRODUCCION, conectar, validar_nombre_bd

DUPLICADO, FK_PADRE, FK_HIJO, CHECK = 1062, 1451, 1452, 3819
resultados: list[tuple[bool, str]] = []


def caso(ok: bool, descripcion: str) -> None:
    resultados.append((ok, descripcion))
    print(("OK    " if ok else "FALLA ") + descripcion)


def sp(cur, nombre, *params):
    cur.callproc(nombre, params)
    filas = cur.fetchall() if cur.description else []
    while cur.nextset():
        pass
    return filas


def espera_error(cur, codigo: int, descripcion: str, sql: str, params=()) -> None:
    cur.execute("SAVEPOINT caso")
    try:
        cur.execute(sql, params)
    except pymysql.MySQLError as error:
        cur.execute("ROLLBACK TO SAVEPOINT caso")
        caso(error.args[0] == codigo, f"{descripcion} → rechazado (MySQL {error.args[0]}, esperado {codigo})")
        return
    cur.execute("ROLLBACK TO SAVEPOINT caso")
    caso(False, f"{descripcion} → ¡ACEPTADO! (se esperaba error {codigo})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bd", required=True)
    base = validar_nombre_bd(parser.parse_args().bd)
    if base == BD_PRODUCCION:
        sys.exit("Este verificador escribe (y revierte) datos de prueba: no se ejecuta en producción.")

    conexion = conectar(base, autocommit=False)
    try:
        with conexion.cursor() as cur:
            cur.execute("SET SESSION time_zone = '+00:00'")
            cur.execute("START TRANSACTION")

            print("A. Compatibilidad con los procedimientos de 4.1/4.2")
            vehiculos = sp(cur, "sp_obtener_vehiculos")
            originales = {"id_vehiculo", "nombre", "modelo", "descripcion", "microcontrolador", "fecha_registro", "activo"}
            caso(bool(vehiculos) and originales <= set(vehiculos[0]), "sp_obtener_vehiculos conserva las columnas originales")
            caso(len(sp(cur, "sp_obtener_vehiculo", 1)) == 1, "sp_obtener_vehiculo(1)")
            comandos = sp(cur, "sp_obtener_comandos_vehiculo", 1)
            caso(len(comandos) == 13 and {"id_comando", "codigo", "activo"} <= set(comandos[0]), "sp_obtener_comandos_vehiculo(1): 13 comandos")
            estado = sp(cur, "sp_obtener_estado", 1)
            caso(len(estado) == 1 and {"vehiculo", "comando", "codigo", "conectado", "fecha_actualizacion"} <= set(estado[0]), "sp_obtener_estado(1)")
            historial = sp(cur, "sp_obtener_historial", 1)
            caso(len(historial) >= 896 and set(historial[0]) == {"id_historial", "id_vehiculo", "comando", "codigo", "fecha_hora", "origen"},
                 "sp_obtener_historial(1): mismas 6 columnas que consume MONITOREO")
            caso(len(sp(cur, "sp_obtener_demos", 1)) == 1, "sp_obtener_demos(1)")
            caso(len(sp(cur, "sp_obtener_movimientos_demo", 1)) == 11, "sp_obtener_movimientos_demo(1): 11 pasos")

            nuevo = sp(cur, "sp_crear_vehiculo", "Unidad de prueba", "Carrito grúa", "verificación", "ESP8266")[0]["id_vehiculo"]
            caso(bool(nuevo), "sp_crear_vehiculo sigue funcionando (columnas nuevas con DEFAULT)")
            cur.execute("SELECT tipo_unidad, numero_serie FROM vehiculos WHERE id_vehiculo = %s", (nuevo,))
            fila = cur.fetchone()
            caso(fila["tipo_unidad"] == "venta" and fila["numero_serie"] is None, "unidad nueva: tipo 'venta' y serie pendiente (la asigna la API)")
            id_cmd = sp(cur, "sp_crear_comando", nuevo, "Avanzar", 1, None)[0]["id_comando"]
            sp(cur, "sp_actualizar_comando", id_cmd, "Avanzar", 1, "desc", 1)
            sp(cur, "sp_actualizar_vehiculo", nuevo, "Unidad de prueba", "Carrito grúa", "v2", "ESP8266", 1)
            sp(cur, "sp_actualizar_estado", nuevo, id_cmd, 1)
            sp(cur, "sp_registrar_historial", nuevo, id_cmd, "interfaz_web")
            sp(cur, "sp_registrar_historial", nuevo, None, "interfaz_web")
            caso(len(sp(cur, "sp_obtener_historial", nuevo)) == 2, "sp_registrar_historial / sp_actualizar_estado (incluye DETENER con NULL)")
            id_demo = sp(cur, "sp_crear_demo", nuevo, "Demo prueba", None)[0]["id_demo"]
            sp(cur, "sp_actualizar_demo", id_demo, "Demo prueba", "d", 1)
            sp(cur, "sp_agregar_movimiento_demo", id_demo, id_cmd, 1, 1000, 0)
            cur.execute("SELECT id_demo_movimiento FROM demo_movimientos WHERE id_demo = %s", (id_demo,))
            id_mov = cur.fetchone()["id_demo_movimiento"]
            sp(cur, "sp_actualizar_movimiento_demo", id_mov, id_cmd, 1, 2000, 100)
            sp(cur, "sp_eliminar_movimiento_demo", id_mov)
            sp(cur, "sp_eliminar_demo", id_demo)
            caso(True, "sp_*_demo y sp_*_movimiento_demo (crear, actualizar, eliminar)")

            print("\nB. Restricciones nuevas")
            espera_error(cur, FK_PADRE, "DELETE físico de la unidad 1 con historial", "DELETE FROM vehiculos WHERE id_vehiculo = 1")
            espera_error(cur, FK_PADRE, "DELETE físico de un comando con historial", "DELETE FROM comandos WHERE id_comando = 1")
            espera_error(cur, CHECK, "paso de demo de 20 s", "UPDATE demo_movimientos SET duracion_ms = 20000 WHERE id_demo = 1 AND orden = 1")
            espera_error(cur, CHECK, "pausa de demo de 11 s", "UPDATE demo_movimientos SET pausa_ms = 11000 WHERE id_demo = 1 AND orden = 1")
            espera_error(cur, CHECK, "comando con tope de 50 ms", "UPDATE comandos SET duracion_max_ms = 50 WHERE id_comando = 1")

            cur.execute("INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES ('admin@prueba.test', 'Admin', 'x', 'admin')")
            admin = cur.lastrowid
            cur.execute("INSERT INTO usuarios (email, nombre, password_hash) VALUES ('a@prueba.test', 'Comprador A', 'x')")
            usuario_a = cur.lastrowid
            cur.execute("INSERT INTO usuarios (email, nombre, password_hash) VALUES ('b@prueba.test', 'Comprador B', 'x')")
            usuario_b = cur.lastrowid
            espera_error(cur, DUPLICADO, "email de usuario repetido", "INSERT INTO usuarios (email, nombre, password_hash) VALUES ('A@prueba.test', 'Otro', 'x')")
            espera_error(cur, CHECK, "email sin formato", "INSERT INTO usuarios (email, nombre, password_hash) VALUES ('sin-arroba', 'X', 'x')")
            espera_error(cur, 1265, "rol inexistente (invitado no es rol de usuario)", "INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES ('c@prueba.test', 'C', 'x', 'invitado')")

            cur.execute("INSERT INTO asignaciones (id_vehiculo, id_usuario, asignado_por) VALUES (%s, %s, %s)", (nuevo, usuario_a, admin))
            asignacion = cur.lastrowid
            espera_error(cur, DUPLICADO, "segunda asignación activa de la misma unidad (a otro comprador)",
                         "INSERT INTO asignaciones (id_vehiculo, id_usuario, asignado_por) VALUES (%s, %s, %s)", (nuevo, usuario_b, admin))
            espera_error(cur, CHECK, "cerrar asignación sin indicar quién", "UPDATE asignaciones SET fin = CURRENT_TIMESTAMP WHERE id_asignacion = %s", (asignacion,))
            cur.execute("UPDATE asignaciones SET fin = CURRENT_TIMESTAMP, finalizado_por = %s WHERE id_asignacion = %s", (admin, asignacion))
            cur.execute("INSERT INTO asignaciones (id_vehiculo, id_usuario, asignado_por) VALUES (%s, %s, %s)", (nuevo, usuario_b, admin))
            caso(True, "tras cerrar la asignación, la unidad se puede reasignar (historial de propiedad conservado)")
            espera_error(cur, FK_HIJO, "asignar a un usuario inexistente",
                         "INSERT INTO asignaciones (id_vehiculo, id_usuario, asignado_por) VALUES (1, 999999, %s)", (admin,))

            espera_error(cur, CHECK, "demo privada sin propietario", "INSERT INTO demos (id_vehiculo, nombre, tipo) VALUES (1, 'x', 'privada')")
            espera_error(cur, CHECK, "demo de fábrica con propietario",
                         "INSERT INTO demos (id_vehiculo, nombre, tipo, id_propietario) VALUES (1, 'x', 'fabrica', %s)", (usuario_a,))
            cur.execute("INSERT INTO demos (id_vehiculo, nombre, tipo, id_propietario) VALUES (%s, 'Mía', 'privada', %s)", (nuevo, usuario_b))
            caso(True, "demo privada con propietario")

            cur.execute("INSERT INTO sesiones_invitado (sid, id_vehiculo, expira_en) VALUES (UUID(), 1, CURRENT_TIMESTAMP + INTERVAL 5 MINUTE)")
            sesion = cur.lastrowid
            espera_error(cur, DUPLICADO, "segunda reserva abierta de la unidad de exhibición",
                         "INSERT INTO sesiones_invitado (sid, id_vehiculo, expira_en) VALUES (UUID(), 1, CURRENT_TIMESTAMP + INTERVAL 5 MINUTE)")
            espera_error(cur, CHECK, "reserva que vence antes de empezar",
                         "INSERT INTO sesiones_invitado (sid, id_vehiculo, inicio, expira_en) VALUES (UUID(), %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP - INTERVAL 1 MINUTE)", (nuevo,))
            cur.execute("UPDATE sesiones_invitado SET finalizada_en = CURRENT_TIMESTAMP, motivo_fin = 'expirada' WHERE id_sesion_invitado = %s", (sesion,))
            cur.execute("INSERT INTO sesiones_invitado (sid, id_vehiculo, expira_en) VALUES (UUID(), 1, CURRENT_TIMESTAMP + INTERVAL 5 MINUTE)")
            sesion2 = cur.lastrowid
            caso(True, "al cerrar la reserva vencida, la unidad se puede reservar otra vez")

            espera_error(cur, CHECK, "historial con usuario y sesión de invitado a la vez",
                         "INSERT INTO historial_comandos (id_vehiculo, id_comando, origen, id_usuario, id_sesion_invitado) VALUES (1, 1, 'x', %s, %s)", (usuario_a, sesion2))
            cur.execute("INSERT INTO historial_comandos (id_vehiculo, id_comando, origen, id_sesion_invitado) VALUES (1, 1, 'invitado', %s)", (sesion2,))
            caso(True, "historial con autoría de invitado")

            espera_error(cur, CHECK, "sesión de usuario con renovación más allá del tope de 8 h",
                         "INSERT INTO sesiones_usuario (sid, id_usuario, expira_en, expira_absoluto) VALUES (UUID(), %s, CURRENT_TIMESTAMP + INTERVAL 9 HOUR, CURRENT_TIMESTAMP + INTERVAL 8 HOUR)", (usuario_a,))
            cur.execute("INSERT INTO auditoria (id_actor, rol_actor, accion, recurso, id_recurso, detalle) VALUES (%s, 'admin', 'asignacion.crear', 'vehiculo', %s, JSON_OBJECT('id_usuario', %s))",
                        (admin, str(nuevo), usuario_b))
            caso(True, "auditoría acepta detalle JSON")
            espera_error(cur, 1265, "auditoría con rol de actor inválido",
                         "INSERT INTO auditoria (rol_actor, accion, recurso) VALUES ('root', 'x', 'y')")

            print("\nC. Datos migrados")
            cur.execute("SELECT v.tipo_unidad, v.numero_serie, m.nombre, m.publicado FROM vehiculos v JOIN modelos_vehiculo m USING (id_modelo) WHERE v.id_vehiculo = 1")
            fila = cur.fetchone()
            caso(fila is not None and fila["tipo_unidad"] == "exhibicion" and fila["numero_serie"] == "UNI-0001" and fila["publicado"] == 1,
                 f"unidad 1: exhibición, serie UNI-0001, modelo «{fila and fila['nombre']}» publicado")
            cur.execute("SELECT GROUP_CONCAT(codigo ORDER BY codigo) AS c FROM comandos WHERE id_vehiculo = 1 AND permitido_invitado = 1")
            caso(cur.fetchone()["c"] == "1,2,3,4,8,9,10,11,12", "comandos permitidos a invitados: 1-4, 8-12")
            cur.execute("SELECT COUNT(*) AS n FROM demos WHERE id_vehiculo = 1 AND tipo = 'fabrica'")
            caso(cur.fetchone()["n"] == 1, "la demo existente quedó como demo de fábrica")
            cur.execute("SELECT COUNT(*) AS n FROM historial_comandos WHERE id_vehiculo = 1 AND id_usuario IS NULL AND id_sesion_invitado IS NULL AND origen <> 'invitado'")
            caso(cur.fetchone()["n"] >= 896, "historial previo intacto y sin autoría inventada")
    finally:
        conexion.rollback()
        conexion.close()

    fallas = [d for ok, d in resultados if not ok]
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} casos OK · transacción revertida (sin cambios en `{base}`)")
    if fallas:
        sys.exit(1)


if __name__ == "__main__":
    main()
