"""Unidades, asignaciones (propiedad) y autorizaciones de soporte."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pymysql

from app.repositories.base import (
    DUPLICADO,
    FK_INEXISTENTE,
    Conflicto,
    codigo_error,
    paginar,
    todas,
    transaccion,
    una,
)


def unidad(id_vehiculo: int) -> dict[str, Any] | None:
    """Datos mínimos para autorizar: tipo, activo y dueño vigente (si lo hay)."""
    return una(
        "SELECT v.id_vehiculo, v.nombre, v.numero_serie, v.tipo_unidad, v.activo, "
        "       a.id_usuario AS id_propietario "
        "  FROM vehiculos v LEFT JOIN asignaciones a ON a.vehiculo_activo = v.id_vehiculo "
        " WHERE v.id_vehiculo = %s",
        (id_vehiculo,),
    )


def soporte_vigente(id_vehiculo: int, id_admin: int) -> dict[str, Any] | None:
    return una(
        "SELECT id_autorizacion, expira_en FROM autorizaciones_soporte "
        " WHERE id_vehiculo = %s AND revocada_en IS NULL AND expira_en > UTC_TIMESTAMP() "
        "   AND (id_admin IS NULL OR id_admin = %s) "
        " ORDER BY expira_en DESC LIMIT 1",
        (id_vehiculo, id_admin),
    )


_COLUMNAS_UNIDAD = (
    "v.id_vehiculo, v.nombre, v.numero_serie, v.tipo_unidad, v.activo, v.microcontrolador, v.descripcion, "
    "m.id_modelo, m.nombre AS modelo, "
    "e.conectado, e.fecha_actualizacion, c.nombre AS ultimo_comando, c.codigo AS ultimo_codigo"
)
_JOIN_UNIDAD = (
    " FROM vehiculos v "
    " LEFT JOIN modelos_vehiculo m ON m.id_modelo = v.id_modelo "
    " LEFT JOIN estado_vehiculo e ON e.id_vehiculo = v.id_vehiculo "
    " LEFT JOIN comandos c ON c.id_comando = e.id_comando "
)


def mis_vehiculos(id_usuario: int) -> list[dict[str, Any]]:
    """Solo las unidades con asignación VIGENTE al usuario (el id sale del token, nunca del cliente)."""
    return todas(
        f"SELECT {_COLUMNAS_UNIDAD}, a.inicio AS asignado_desde, "
        # Sin historial, estado_vehiculo.id_comando NULL NO significa DETENER: la unidad no se ha movido.
        "       EXISTS(SELECT 1 FROM historial_comandos h WHERE h.id_vehiculo = v.id_vehiculo) AS con_historial, "
        "       (SELECT MAX(s.expira_en) FROM autorizaciones_soporte s "
        "         WHERE s.id_vehiculo = v.id_vehiculo AND s.revocada_en IS NULL "
        "           AND s.expira_en > UTC_TIMESTAMP()) AS soporte_hasta "
        f"{_JOIN_UNIDAD} JOIN asignaciones a ON a.vehiculo_activo = v.id_vehiculo AND a.id_usuario = %s "
        " ORDER BY v.id_vehiculo",
        (id_usuario,),
    )


def listar_asignaciones(
    pagina: int, por_pagina: int, id_vehiculo: int | None, id_usuario: int | None, vigentes: bool | None
) -> tuple[int, list[dict[str, Any]]]:
    condiciones, params = [], []
    if id_vehiculo is not None:
        condiciones.append("a.id_vehiculo = %s")
        params.append(id_vehiculo)
    if id_usuario is not None:
        condiciones.append("a.id_usuario = %s")
        params.append(id_usuario)
    if vigentes is True:
        condiciones.append("a.fin IS NULL")
    elif vigentes is False:
        condiciones.append("a.fin IS NOT NULL")
    where = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
    limite, desplazamiento = paginar(pagina, por_pagina)
    with transaccion() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM asignaciones a{where}", tuple(params))
        total = int(cur.fetchone()["n"])
        cur.execute(
            "SELECT a.id_asignacion, a.id_vehiculo, v.nombre AS vehiculo, v.numero_serie, "
            "       a.id_usuario, u.email, u.nombre AS comprador, a.inicio, a.fin, a.motivo_fin, "
            "       a.asignado_por, a.finalizado_por "
            "  FROM asignaciones a JOIN vehiculos v ON v.id_vehiculo = a.id_vehiculo "
            "  JOIN usuarios u ON u.id_usuario = a.id_usuario"
            f"{where} ORDER BY a.id_asignacion DESC LIMIT %s OFFSET %s",
            (*params, limite, desplazamiento),
        )
        return total, list(cur.fetchall())


def crear_asignacion(id_vehiculo: int, id_usuario: int, asignado_por: int) -> int:
    """Valida en la misma transacción (con bloqueo) que la unidad sea vendible y el comprador apto."""
    try:
        with transaccion() as cur:
            cur.execute(
                "SELECT tipo_unidad, activo FROM vehiculos WHERE id_vehiculo = %s FOR UPDATE", (id_vehiculo,)
            )
            vehiculo = cur.fetchone()
            if vehiculo is None:
                raise LookupError("Vehículo no encontrado.")
            if not vehiculo["activo"]:
                raise Conflicto("El vehículo está dado de baja.")
            if vehiculo["tipo_unidad"] != "venta":
                raise Conflicto("Solo se asignan unidades de tipo 'venta' (no exhibición ni simuladas).")
            cur.execute("SELECT rol, activo FROM usuarios WHERE id_usuario = %s", (id_usuario,))
            usuario = cur.fetchone()
            if usuario is None:
                raise LookupError("Usuario no encontrado.")
            if usuario["rol"] != "usuario" or not usuario["activo"]:
                raise Conflicto("Solo se asigna a usuarios con rol 'usuario' y activos.")
            cur.execute(
                "INSERT INTO asignaciones (id_vehiculo, id_usuario, asignado_por) VALUES (%s, %s, %s)",
                (id_vehiculo, id_usuario, asignado_por),
            )
            return int(cur.lastrowid)
    except pymysql.MySQLError as error:
        if codigo_error(error) == DUPLICADO:
            raise Conflicto("La unidad ya tiene una asignación vigente; finalízala antes de reasignar.") from None
        if codigo_error(error) == FK_INEXISTENTE:
            raise LookupError("Vehículo o usuario inexistente.") from None
        raise


def finalizar_asignacion(id_asignacion: int, finalizado_por: int, motivo: str) -> dict[str, Any]:
    """Cierra la asignación y revoca las autorizaciones de soporte vigentes de esa unidad."""
    with transaccion() as cur:
        cur.execute(
            "SELECT id_asignacion, id_vehiculo, id_usuario, fin FROM asignaciones WHERE id_asignacion = %s FOR UPDATE",
            (id_asignacion,),
        )
        fila = cur.fetchone()
        if fila is None:
            raise LookupError("Asignación no encontrada.")
        if fila["fin"] is not None:
            raise Conflicto("La asignación ya estaba finalizada.")
        cur.execute(
            "UPDATE asignaciones SET fin = UTC_TIMESTAMP(), finalizado_por = %s, motivo_fin = %s "
            " WHERE id_asignacion = %s",
            (finalizado_por, motivo, id_asignacion),
        )
        cur.execute(
            "UPDATE autorizaciones_soporte SET revocada_en = UTC_TIMESTAMP() "
            " WHERE id_vehiculo = %s AND revocada_en IS NULL",
            (fila["id_vehiculo"],),
        )
        return fila


def otorgar_soporte(id_vehiculo: int, id_usuario: int, id_admin: int | None, motivo: str, expira: datetime) -> int:
    with transaccion() as cur:
        cur.execute(
            "INSERT INTO autorizaciones_soporte (id_vehiculo, id_usuario, id_admin, motivo, expira_en) "
            "VALUES (%s, %s, %s, %s, %s)",
            (id_vehiculo, id_usuario, id_admin, motivo, expira),
        )
        return int(cur.lastrowid)


def revocar_soporte(id_vehiculo: int, id_usuario: int) -> int:
    with transaccion() as cur:
        cur.execute(
            "UPDATE autorizaciones_soporte SET revocada_en = UTC_TIMESTAMP() "
            " WHERE id_vehiculo = %s AND id_usuario = %s AND revocada_en IS NULL",
            (id_vehiculo, id_usuario),
        )
        return cur.rowcount


def inventario() -> list[dict[str, Any]]:
    """Inventario para el administrador: unidad, modelo, estado y comprador vigente."""
    return todas(
        f"SELECT {_COLUMNAS_UNIDAD}, a.id_usuario AS id_propietario, u.email AS propietario "
        f"{_JOIN_UNIDAD} LEFT JOIN asignaciones a ON a.vehiculo_activo = v.id_vehiculo "
        " LEFT JOIN usuarios u ON u.id_usuario = a.id_usuario "
        " ORDER BY v.id_vehiculo"
    )
