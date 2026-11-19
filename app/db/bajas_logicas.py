"""Bajas lógicas de vehículos y comandos.

En RDS, `sp_eliminar_vehiculo` y `sp_eliminar_comando` hacen DELETE físico.
Las llaves foráneas propagan ese borrado:

- borrar un vehículo elimina en CASCADE sus comandos, demos, pasos,
  estado e HISTORIAL completo;
- borrar un comando pone `historial_comandos.id_comando = NULL`, y NULL
  significa DETENER, así que el historial queda falseado.

Por eso la API ya no llama a esos procedimientos: marca `activo = 0` y
conserva toda la información. Las consultas son parametrizadas y los
nombres de tabla y columna son constantes internas, nunca datos del cliente.
"""

import pymysql

from app.db.connection import get_connection


_SQL_BUSCAR = {
    "vehiculo": "SELECT activo FROM vehiculos WHERE id_vehiculo = %s FOR UPDATE",
    "comando": "SELECT activo FROM comandos WHERE id_comando = %s FOR UPDATE",
}
_SQL_DESACTIVAR = {
    "vehiculo": "UPDATE vehiculos SET activo = 0 WHERE id_vehiculo = %s",
    "comando": "UPDATE comandos SET activo = 0 WHERE id_comando = %s",
}


def _desactivar(recurso: str, identificador: int) -> bool | None:
    """Pone activo = 0. Devuelve el valor anterior de activo, o None si no existe."""
    connection = None
    try:
        connection = get_connection()
        with connection.cursor() as cursor:
            cursor.execute(_SQL_BUSCAR[recurso], (identificador,))
            fila = cursor.fetchone()
            if fila is None:
                connection.rollback()
                return None
            cursor.execute(_SQL_DESACTIVAR[recurso], (identificador,))
        connection.commit()
        return bool(fila["activo"])
    except pymysql.MySQLError:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def desactivar_vehiculo(id_vehiculo: int) -> bool | None:
    return _desactivar("vehiculo", id_vehiculo)


def desactivar_comando(id_comando: int) -> bool | None:
    return _desactivar("comando", id_comando)
