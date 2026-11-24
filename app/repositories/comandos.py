"""Comandos propios del usuario (V009).

Un comando propio es un ALIAS con nombre propio de un movimiento físico que la unidad YA tiene
registrado como comando de fábrica (mismo código). Así el usuario no puede inventar movimientos que
el firmware no conoce: el código siempre sale de un comando de fábrica activo de esa unidad.
"""

from __future__ import annotations

from typing import Any

import pymysql

from app.repositories.base import CHECK_VIOLADO, DUPLICADO, Conflicto, codigo_error, todas, transaccion

# Movimientos físicos que se pueden reutilizar en un comando propio (los mismos que en demos).
CODIGOS_PROPIOS = range(1, 13)


class MovimientoNoRegistrado(Exception):
    """El código pedido no corresponde a un movimiento de fábrica activo de la unidad: HTTP 422."""


def movimientos_base(id_vehiculo: int) -> list[dict[str, Any]]:
    """Movimientos de fábrica activos que pueden servir de base a un comando propio."""
    return todas(
        "SELECT id_comando, nombre, codigo, duracion_max_ms FROM comandos "
        " WHERE id_vehiculo = %s AND tipo = 'fabrica' AND activo = 1 AND codigo BETWEEN 1 AND 12 ORDER BY codigo",
        (id_vehiculo,),
    )


def propios(id_vehiculo: int, id_usuario: int) -> list[dict[str, Any]]:
    return todas(
        "SELECT c.id_comando, c.nombre, c.codigo, c.descripcion, c.duracion_max_ms, c.activo, "
        "       f.nombre AS movimiento_base "
        "  FROM comandos c LEFT JOIN comandos f ON f.id_vehiculo = c.id_vehiculo AND f.codigo_fabrica = c.codigo "
        " WHERE c.id_vehiculo = %s AND c.tipo = 'usuario' AND c.id_propietario = %s AND c.activo = 1 "
        " ORDER BY c.codigo, c.id_comando",
        (id_vehiculo, id_usuario),
    )


def de_unidad_admin(id_vehiculo: int) -> list[dict[str, Any]]:
    """Todos los comandos de la unidad con su tipo y dueño (para ADMINISTRACIÓN → Inventario)."""
    return todas(
        "SELECT c.id_comando, c.nombre, c.codigo, c.descripcion, c.activo, c.permitido_invitado, c.duracion_max_ms, "
        "       c.tipo, c.id_propietario, u.email AS propietario "
        "  FROM comandos c LEFT JOIN usuarios u ON u.id_usuario = c.id_propietario "
        " WHERE c.id_vehiculo = %s ORDER BY c.tipo, c.codigo, c.id_comando",
        (id_vehiculo,),
    )


def crear_propio(id_vehiculo: int, id_usuario: int, nombre: str, codigo: int, descripcion: str | None,
                 duracion_max_ms: int | None) -> int:
    try:
        with transaccion() as cur:
            cur.execute(
                "SELECT id_comando, nombre, duracion_max_ms FROM comandos "
                " WHERE id_vehiculo = %s AND tipo = 'fabrica' AND activo = 1 AND codigo = %s",
                (id_vehiculo, codigo),
            )
            base = cur.fetchone()
            if base is None or codigo not in CODIGOS_PROPIOS:
                raise MovimientoNoRegistrado(
                    f"El código {codigo} no es un movimiento registrado de esta unidad; elige uno de la lista."
                )
            tope_base = base["duracion_max_ms"]
            if duracion_max_ms is not None and tope_base is not None and duracion_max_ms > int(tope_base):
                raise MovimientoNoRegistrado(
                    f"«{base['nombre']}» admite como máximo {tope_base} ms por paso; tu comando no puede superarlo."
                )
            cur.execute(
                "INSERT INTO comandos (id_vehiculo, nombre, codigo, descripcion, activo, duracion_max_ms, "
                "                      permitido_invitado, tipo, id_propietario) "
                "VALUES (%s, %s, %s, %s, 1, %s, 0, 'usuario', %s)",
                (id_vehiculo, nombre, codigo, descripcion, duracion_max_ms or tope_base, id_usuario),
            )
            return int(cur.lastrowid)
    except pymysql.MySQLError as error:
        if codigo_error(error) == DUPLICADO:
            raise Conflicto("Ya existe un comando con ese nombre en esta unidad.") from None
        if codigo_error(error) == CHECK_VIOLADO:
            raise Conflicto("Algún valor está fuera de los límites permitidos.") from None
        raise


def eliminar_propio(id_comando: int, id_vehiculo: int, id_usuario: int) -> str | None:
    """Elimina un comando PROPIO del usuario. None si no existe o no es suyo (404 en la ruta).

    - Si alguna demo lo usa → Conflicto (hay que quitarlo de la demo primero).
    - Si ya tiene historial → baja lógica con el nombre liberado (el historial lo conserva).
    - Si no → borrado físico.
    """
    with transaccion() as cur:
        cur.execute(
            "SELECT id_comando, nombre FROM comandos WHERE id_comando = %s AND id_vehiculo = %s "
            "   AND tipo = 'usuario' AND id_propietario = %s AND activo = 1 FOR UPDATE",
            (id_comando, id_vehiculo, id_usuario),
        )
        fila = cur.fetchone()
        if fila is None:
            return None
        cur.execute(
            "SELECT d.nombre FROM demo_movimientos dm JOIN demos d ON d.id_demo = dm.id_demo "
            " WHERE dm.id_comando = %s LIMIT 1",
            (id_comando,),
        )
        demo = cur.fetchone()
        if demo is not None:
            raise Conflicto(f"La demo «{demo['nombre']}» usa este comando; quítalo de la demo antes de eliminarlo.")
        cur.execute("SELECT 1 FROM historial_comandos WHERE id_comando = %s LIMIT 1", (id_comando,))
        if cur.fetchone() is not None:
            cur.execute(
                "UPDATE comandos SET activo = 0, nombre = LEFT(CONCAT(nombre, ' · eliminado ', id_comando), 60) "
                " WHERE id_comando = %s",
                (id_comando,),
            )
            return "baja_logica"
        cur.execute("UPDATE estado_vehiculo SET id_comando = NULL WHERE id_comando = %s", (id_comando,))
        cur.execute("DELETE FROM comandos WHERE id_comando = %s", (id_comando,))
        return "eliminado"
