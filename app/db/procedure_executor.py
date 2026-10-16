
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


# Lista permitida de procedimientos almacenados.
# El nombre del procedimiento no puede pasarse como parámetro SQL,
# por eso verificamos que pertenezca a esta lista.
ALLOWED_PROCEDURES = {
    "sp_crear_vehiculo",
    "sp_obtener_vehiculos",
    "sp_obtener_vehiculo",
    "sp_actualizar_vehiculo",
    "sp_eliminar_vehiculo",
    "sp_crear_comando",
    "sp_obtener_comandos_vehiculo",
    "sp_actualizar_comando",
    "sp_eliminar_comando",
    "sp_actualizar_estado",
    "sp_obtener_estado",
    "sp_registrar_historial",
    "sp_obtener_historial",
    "sp_crear_demo",
    "sp_obtener_demos",
    "sp_actualizar_demo",
    "sp_eliminar_demo",
    "sp_agregar_movimiento_demo",
    "sp_obtener_movimientos_demo",
    "sp_actualizar_movimiento_demo",
    "sp_eliminar_movimiento_demo",
}


def execute_procedure(
    connection: pymysql.connections.Connection,
    procedure_name: str,
    params: tuple[Any, ...] = (),
) -> list[list[dict[str, Any]]]:
    """
    Ejecuta un Stored Procedure permitido.

    Devuelve una lista de conjuntos de resultados.
    Cada conjunto contiene diccionarios, uno por fila.

    No abre, confirma ni cierra la conexión: esa responsabilidad
    corresponde al servicio que administra la operación.
    """
    if procedure_name not in ALLOWED_PROCEDURES:
        raise ValueError("Procedimiento almacenado no permitido.")

    result_sets: list[list[dict[str, Any]]] = []

    with connection.cursor(DictCursor) as cursor:
        cursor.callproc(procedure_name, params)

        while True:
            if cursor.description is not None:
                result_sets.append(cursor.fetchall())

            if not cursor.nextset():
                break

    return result_sets
