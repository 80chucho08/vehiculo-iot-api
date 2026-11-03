from typing import Any

import pymysql
from fastapi import HTTPException

from app.db.connection import get_connection
from app.db.procedure_executor import execute_procedure


def ejecutar_operacion(
    procedimiento: str,
    parametros: tuple[Any, ...],
    operaciones_adicionales: tuple[
        tuple[str, tuple[Any, ...]], ...
    ] = (),
) -> list[list[dict[str, Any]]]:
    """Ejecuta procedimientos de escritura en una transacción compartida."""
    connection = None
    try:
        connection = get_connection()
        resultados = execute_procedure(connection, procedimiento, parametros)
        for procedimiento_adicional, parametros_adicionales in operaciones_adicionales:
            execute_procedure(
                connection,
                procedimiento_adicional,
                parametros_adicionales,
            )
        connection.commit()
        return resultados
    except pymysql.MySQLError:
        if connection is not None:
            connection.rollback()
        raise HTTPException(
            status_code=500,
            detail="No fue posible completar la operación en la base de datos.",
        ) from None
    finally:
        if connection is not None:
            connection.close()

