"""Catálogo comercial (modelos), alta de unidades físicas y permisos por comando."""

from __future__ import annotations

from typing import Any

import pymysql

from app.repositories.base import CHECK_VIOLADO, DUPLICADO, Conflicto, codigo_error, todas, transaccion, una

# ---------- Modelos ----------

COLUMNAS_MODELO = (
    "m.id_modelo, m.nombre, m.descripcion, m.microcontrolador, m.duracion_max_paso_ms, m.pasos_max_demo, "
    "m.publicado, m.activo, m.creado_en, "
    "(SELECT COUNT(*) FROM vehiculos v WHERE v.id_modelo = m.id_modelo) AS unidades"
)


def listar_modelos() -> list[dict[str, Any]]:
    return todas(f"SELECT {COLUMNAS_MODELO} FROM modelos_vehiculo m ORDER BY m.id_modelo")


def modelo(id_modelo: int) -> dict[str, Any] | None:
    return una(f"SELECT {COLUMNAS_MODELO} FROM modelos_vehiculo m WHERE m.id_modelo = %s", (id_modelo,))


def _traducir(error: pymysql.MySQLError, duplicado: str) -> Exception:
    if codigo_error(error) == DUPLICADO:
        return Conflicto(duplicado)
    if codigo_error(error) == CHECK_VIOLADO:
        return Conflicto("Algún valor está fuera de los límites permitidos.")
    return error


def guardar_modelo(datos: dict[str, Any], id_modelo: int | None = None) -> int:
    campos = ("nombre", "descripcion", "microcontrolador", "duracion_max_paso_ms", "pasos_max_demo", "publicado", "activo")
    valores = tuple(datos[c] for c in campos)
    try:
        with transaccion() as cur:
            if id_modelo is None:
                cur.execute(
                    f"INSERT INTO modelos_vehiculo ({', '.join(campos)}) VALUES ({', '.join(['%s'] * len(campos))})",
                    valores,
                )
                return int(cur.lastrowid)
            cur.execute(
                f"UPDATE modelos_vehiculo SET {', '.join(c + ' = %s' for c in campos)} WHERE id_modelo = %s",
                (*valores, id_modelo),
            )
            if cur.rowcount == 0:
                cur.execute("SELECT 1 FROM modelos_vehiculo WHERE id_modelo = %s", (id_modelo,))
                if cur.fetchone() is None:
                    raise LookupError("Modelo no encontrado.")
            return id_modelo
    except pymysql.MySQLError as error:
        raise _traducir(error, "Ya existe un modelo con ese nombre.") from None


# ---------- Unidades ----------

def crear_unidad(datos: dict[str, Any]) -> dict[str, Any]:
    """Da de alta una unidad física y le copia comandos y demos de fábrica de una unidad plantilla
    del mismo modelo (preferentemente la de exhibición). Todo en una transacción."""
    try:
        with transaccion() as cur:
            cur.execute("SELECT nombre, microcontrolador, activo FROM modelos_vehiculo WHERE id_modelo = %s", (datos["id_modelo"],))
            mod = cur.fetchone()
            if mod is None:
                raise LookupError("Modelo no encontrado.")
            if not mod["activo"]:
                raise Conflicto("El modelo está inactivo.")
            cur.execute(
                "INSERT INTO vehiculos (nombre, modelo, id_modelo, numero_serie, tipo_unidad, descripcion, microcontrolador) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (datos["nombre"], mod["nombre"], datos["id_modelo"], datos["numero_serie"], datos["tipo_unidad"],
                 datos.get("descripcion"), datos.get("microcontrolador") or mod["microcontrolador"]),
            )
            nueva = int(cur.lastrowid)
            cur.execute("INSERT INTO estado_vehiculo (id_vehiculo, id_comando, conectado) VALUES (%s, NULL, 0)", (nueva,))

            cur.execute(
                "SELECT id_vehiculo FROM vehiculos WHERE id_modelo = %s AND activo = 1 AND id_vehiculo <> %s "
                " ORDER BY (tipo_unidad = 'exhibicion') DESC, id_vehiculo LIMIT 1",
                (datos["id_modelo"], nueva),
            )
            plantilla = cur.fetchone()
            comandos = demos = 0
            if plantilla:
                origen = plantilla["id_vehiculo"]
                cur.execute(
                    "INSERT INTO comandos (id_vehiculo, nombre, codigo, descripcion, activo, duracion_max_ms, permitido_invitado) "
                    "SELECT %s, nombre, codigo, descripcion, activo, duracion_max_ms, permitido_invitado "
                    "  FROM comandos WHERE id_vehiculo = %s",
                    (nueva, origen),
                )
                comandos = cur.rowcount
                cur.execute(
                    "SELECT id_demo, nombre, descripcion FROM demos "
                    " WHERE id_vehiculo = %s AND tipo = 'fabrica' AND activo = 1 ORDER BY id_demo",
                    (origen,),
                )
                for demo in cur.fetchall():
                    cur.execute(
                        "INSERT INTO demos (id_vehiculo, nombre, descripcion, tipo) VALUES (%s, %s, %s, 'fabrica')",
                        (nueva, demo["nombre"], demo["descripcion"]),
                    )
                    id_demo = int(cur.lastrowid)
                    # Los pasos apuntan al comando de la NUEVA unidad con el mismo código.
                    cur.execute(
                        "INSERT INTO demo_movimientos (id_demo, id_comando, orden, duracion_ms, pausa_ms) "
                        "SELECT %s, cn.id_comando, dm.orden, dm.duracion_ms, dm.pausa_ms "
                        "  FROM demo_movimientos dm "
                        "  JOIN comandos co ON co.id_comando = dm.id_comando "
                        "  JOIN comandos cn ON cn.id_vehiculo = %s AND cn.codigo = co.codigo "
                        " WHERE dm.id_demo = %s",
                        (id_demo, nueva, demo["id_demo"]),
                    )
                    demos += 1
            return {
                "id_vehiculo": nueva,
                "plantilla": plantilla["id_vehiculo"] if plantilla else None,
                "comandos_copiados": comandos,
                "demos_copiadas": demos,
            }
    except pymysql.MySQLError as error:
        raise _traducir(error, "Ya existe una unidad con ese número de serie.") from None


def actualizar_unidad(id_vehiculo: int, datos: dict[str, Any]) -> dict[str, Any]:
    """Devuelve la fila anterior. Una unidad vendida no puede dejar de ser 'venta' ni darse de baja."""
    try:
        with transaccion() as cur:
            cur.execute(
                "SELECT v.id_vehiculo, v.nombre, v.numero_serie, v.tipo_unidad, v.descripcion, v.microcontrolador, v.activo, "
                "       a.id_usuario AS id_propietario "
                "  FROM vehiculos v LEFT JOIN asignaciones a ON a.vehiculo_activo = v.id_vehiculo "
                " WHERE v.id_vehiculo = %s FOR UPDATE",
                (id_vehiculo,),
            )
            anterior = cur.fetchone()
            if anterior is None:
                raise LookupError("Vehículo no encontrado.")
            if anterior["id_propietario"] is not None and (datos["tipo_unidad"] != "venta" or not datos["activo"]):
                raise Conflicto("La unidad está vendida: finaliza la asignación antes de cambiar su tipo o darla de baja.")
            cur.execute(
                "UPDATE vehiculos SET nombre = %s, numero_serie = %s, tipo_unidad = %s, descripcion = %s, "
                "       microcontrolador = %s, activo = %s WHERE id_vehiculo = %s",
                (datos["nombre"], datos["numero_serie"], datos["tipo_unidad"], datos.get("descripcion"),
                 datos.get("microcontrolador"), int(datos["activo"]), id_vehiculo),
            )
            return anterior
    except pymysql.MySQLError as error:
        raise _traducir(error, "Ya existe una unidad con ese número de serie.") from None


# ---------- Comandos permitidos ----------

def actualizar_permisos_comando(id_comando: int, activo: bool, permitido_invitado: bool, duracion_max_ms: int | None) -> dict[str, Any]:
    try:
        with transaccion() as cur:
            cur.execute("SELECT id_comando, id_vehiculo, codigo, activo, permitido_invitado, duracion_max_ms "
                        "  FROM comandos WHERE id_comando = %s FOR UPDATE", (id_comando,))
            anterior = cur.fetchone()
            if anterior is None:
                raise LookupError("Comando no encontrado.")
            if permitido_invitado and not 1 <= int(anterior["codigo"]) <= 12:
                raise Conflicto("Solo los movimientos 1-12 pueden habilitarse para invitados.")
            cur.execute(
                "UPDATE comandos SET activo = %s, permitido_invitado = %s, duracion_max_ms = %s WHERE id_comando = %s",
                (int(activo), int(permitido_invitado), duracion_max_ms, id_comando),
            )
            return anterior
    except pymysql.MySQLError as error:
        raise _traducir(error, "Conflicto al guardar el comando.") from None
