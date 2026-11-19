from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pymysql

from app.db.connection import get_connection

DUPLICADO = 1062
FK_INEXISTENTE = 1452
FK_EN_USO = 1451
CHECK_VIOLADO = 3819


class Conflicto(Exception):
    """La operación choca con el estado actual (p. ej. unidad ya asignada): HTTP 409."""


@contextmanager
def transaccion() -> Iterator[pymysql.cursors.DictCursor]:
    """Cursor dentro de una transacción: commit si todo sale bien, rollback si no."""
    conexion = get_connection()
    try:
        with conexion.cursor() as cursor:
            yield cursor
        conexion.commit()
    except BaseException:
        conexion.rollback()
        raise
    finally:
        conexion.close()


def todas(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with transaccion() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def una(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with transaccion() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def codigo_error(error: pymysql.MySQLError) -> int | None:
    return error.args[0] if error.args else None


def paginar(pagina: int, por_pagina: int) -> tuple[int, int]:
    """(LIMIT, OFFSET) con por_pagina acotado a 1..100."""
    por_pagina = max(1, min(100, por_pagina))
    return por_pagina, (max(1, pagina) - 1) * por_pagina
