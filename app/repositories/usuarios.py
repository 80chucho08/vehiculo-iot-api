from __future__ import annotations

from typing import Any

import pymysql

from app.repositories.base import DUPLICADO, Conflicto, codigo_error, paginar, transaccion, una

# Nunca se devuelve password_hash fuera de este módulo, salvo en por_email_con_hash (login).
COLUMNAS_PUBLICAS = "id_usuario, email, nombre, rol, activo, ultimo_acceso, creado_en"


def por_email_con_hash(email: str) -> dict[str, Any] | None:
    return una(
        "SELECT id_usuario, email, nombre, rol, activo, password_hash, intentos_fallidos, bloqueado_hasta "
        "FROM usuarios WHERE email = %s",
        (email,),
    )


def por_id(id_usuario: int) -> dict[str, Any] | None:
    return una(f"SELECT {COLUMNAS_PUBLICAS} FROM usuarios WHERE id_usuario = %s", (id_usuario,))


def listar(
    pagina: int, por_pagina: int, rol: str | None, activo: bool | None, buscar: str | None
) -> tuple[int, list[dict[str, Any]]]:
    condiciones, params = [], []
    if rol:
        condiciones.append("rol = %s")
        params.append(rol)
    if activo is not None:
        condiciones.append("activo = %s")
        params.append(int(activo))
    if buscar:
        condiciones.append("(email LIKE %s OR nombre LIKE %s)")
        patron = "%" + buscar.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params += [patron, patron]
    where = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
    limite, desplazamiento = paginar(pagina, por_pagina)
    with transaccion() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM usuarios{where}", tuple(params))
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"SELECT {COLUMNAS_PUBLICAS} FROM usuarios{where} ORDER BY id_usuario LIMIT %s OFFSET %s",
            (*params, limite, desplazamiento),
        )
        return total, list(cur.fetchall())


def crear(email: str, nombre: str, password_hash: str, rol: str) -> int:
    try:
        with transaccion() as cur:
            cur.execute(
                "INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES (%s, %s, %s, %s)",
                (email, nombre, password_hash, rol),
            )
            return int(cur.lastrowid)
    except pymysql.MySQLError as error:
        if codigo_error(error) == DUPLICADO:
            raise Conflicto("Ya existe un usuario con ese email.") from None
        raise


def actualizar(id_usuario: int, nombre: str, rol: str, activo: bool) -> dict[str, Any] | None:
    """Devuelve la fila anterior (para auditar el cambio) o None si no existe."""
    with transaccion() as cur:
        cur.execute(f"SELECT {COLUMNAS_PUBLICAS} FROM usuarios WHERE id_usuario = %s FOR UPDATE", (id_usuario,))
        anterior = cur.fetchone()
        if anterior is None:
            return None
        cur.execute(
            "UPDATE usuarios SET nombre = %s, rol = %s, activo = %s WHERE id_usuario = %s",
            (nombre, rol, int(activo), id_usuario),
        )
        return anterior


def cambiar_password(id_usuario: int, password_hash: str) -> None:
    with transaccion() as cur:
        cur.execute(
            "UPDATE usuarios SET password_hash = %s, intentos_fallidos = 0, bloqueado_hasta = NULL "
            "WHERE id_usuario = %s",
            (password_hash, id_usuario),
        )


def registrar_fallo(id_usuario: int, max_intentos: int, bloqueo_minutos: int) -> None:
    """Suma un intento fallido; al llegar al máximo bloquea la cuenta y reinicia el contador.

    En un UPDATE de MySQL las asignaciones se evalúan de izquierda a derecha y cada una ve
    el valor ya asignado: la segunda lee intentos_fallidos ya incrementado.
    """
    with transaccion() as cur:
        cur.execute(
            "UPDATE usuarios SET intentos_fallidos = intentos_fallidos + 1, "
            "bloqueado_hasta = IF(intentos_fallidos >= %s, "
            "  UTC_TIMESTAMP() + INTERVAL %s MINUTE, bloqueado_hasta), "
            "intentos_fallidos = IF(intentos_fallidos >= %s, 0, intentos_fallidos) "
            "WHERE id_usuario = %s",
            (max_intentos, bloqueo_minutos, max_intentos, id_usuario),
        )


def registrar_acceso(id_usuario: int) -> None:
    with transaccion() as cur:
        cur.execute(
            "UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL, ultimo_acceso = UTC_TIMESTAMP() "
            "WHERE id_usuario = %s",
            (id_usuario,),
        )


def contar_admins_activos() -> int:
    fila = una("SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'admin' AND activo = 1")
    return int(fila["n"]) if fila else 0
