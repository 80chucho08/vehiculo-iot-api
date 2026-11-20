"""Reservas de invitado (sesiones_invitado): una abierta por unidad de exhibición, con caducidad."""

from __future__ import annotations

from typing import Any

import pymysql

from app.repositories.base import DUPLICADO, Conflicto, codigo_error, todas, transaccion, una

_CIERRE = (
    "UPDATE sesiones_invitado SET finalizada_en = UTC_TIMESTAMP(), motivo_fin = %s "
    " WHERE id_sesion_invitado = %s AND finalizada_en IS NULL"
)


def catalogo() -> list[dict[str, Any]]:
    """Unidades de exhibición activas de modelos publicados, con su disponibilidad (sin datos privados)."""
    return todas(
        "SELECT v.id_vehiculo, v.nombre, v.numero_serie, m.id_modelo, m.nombre AS modelo, m.descripcion, "
        "       m.microcontrolador, s.expira_en AS ocupada_hasta "
        "  FROM vehiculos v JOIN modelos_vehiculo m ON m.id_modelo = v.id_modelo "
        "  LEFT JOIN sesiones_invitado s ON s.vehiculo_reservado = v.id_vehiculo AND s.expira_en > UTC_TIMESTAMP() "
        " WHERE v.tipo_unidad = 'exhibicion' AND v.activo = 1 AND m.publicado = 1 AND m.activo = 1 "
        " ORDER BY m.nombre, v.id_vehiculo"
    )


def unidad_reservable(id_vehiculo: int) -> dict[str, Any] | None:
    return una(
        "SELECT v.id_vehiculo, v.tipo_unidad, v.activo, m.publicado FROM vehiculos v "
        "  LEFT JOIN modelos_vehiculo m ON m.id_modelo = v.id_modelo WHERE v.id_vehiculo = %s",
        (id_vehiculo,),
    )


def reservas_recientes_ip(ip_hash: str, minutos: int) -> int:
    fila = una(
        "SELECT COUNT(*) AS n FROM sesiones_invitado WHERE ip_hash = %s AND inicio >= UTC_TIMESTAMP() - INTERVAL %s MINUTE",
        (ip_hash, minutos),
    )
    return int(fila["n"]) if fila else 0


def crear(sid: str, id_vehiculo: int, minutos: int, ip_hash: str | None) -> dict[str, Any]:
    """Cierra en la misma transacción una reserva vencida que siga abierta y crea la nueva.
    Si otra reserva sigue vigente, el UNIQUE(vehiculo_reservado) lo impide → Conflicto."""
    try:
        with transaccion() as cur:
            cur.execute(
                "SELECT id_sesion_invitado, expira_en > UTC_TIMESTAMP() AS vigente, expira_en "
                "  FROM sesiones_invitado WHERE vehiculo_reservado = %s FOR UPDATE",
                (id_vehiculo,),
            )
            abierta = cur.fetchone()
            if abierta and abierta["vigente"]:
                raise Conflicto(f"La unidad está en uso por otro invitado hasta {abierta['expira_en']:%H:%M:%S} UTC.")
            if abierta:
                cur.execute(_CIERRE, ("expirada", abierta["id_sesion_invitado"]))
            cur.execute(
                "INSERT INTO sesiones_invitado (sid, id_vehiculo, inicio, expira_en, ip_hash) "
                "VALUES (%s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP() + INTERVAL %s MINUTE, %s)",
                (sid, id_vehiculo, minutos, ip_hash),
            )
            id_sesion = int(cur.lastrowid)
            cur.execute("SELECT expira_en FROM sesiones_invitado WHERE id_sesion_invitado = %s", (id_sesion,))
            return {"id_sesion_invitado": id_sesion, "expira_en": cur.fetchone()["expira_en"]}
    except pymysql.MySQLError as error:
        if codigo_error(error) == DUPLICADO:
            raise Conflicto("La unidad acaba de ser reservada por otro invitado.") from None
        raise


def obtener_vigente(sid: str) -> dict[str, Any] | None:
    return una(
        "SELECT id_sesion_invitado, sid, id_vehiculo, expira_en FROM sesiones_invitado "
        " WHERE sid = %s AND finalizada_en IS NULL AND expira_en > UTC_TIMESTAMP()",
        (sid,),
    )


def finalizar(sid: str, motivo: str) -> dict[str, Any] | None:
    """Cierra la reserva si seguía abierta; devuelve sus datos para detener la unidad."""
    with transaccion() as cur:
        cur.execute(
            "SELECT id_sesion_invitado, id_vehiculo FROM sesiones_invitado "
            " WHERE sid = %s AND finalizada_en IS NULL FOR UPDATE",
            (sid,),
        )
        fila = cur.fetchone()
        if fila:
            cur.execute(_CIERRE, (motivo, fila["id_sesion_invitado"]))
        return fila


def cerrar_vencidas() -> list[dict[str, Any]]:
    """Cierra las reservas cuyo tiempo terminó. Devuelve [{id_vehiculo, sid}] para detener cada unidad."""
    with transaccion() as cur:
        cur.execute(
            "SELECT id_sesion_invitado, id_vehiculo, sid FROM sesiones_invitado "
            " WHERE finalizada_en IS NULL AND expira_en <= UTC_TIMESTAMP() FOR UPDATE"
        )
        vencidas = list(cur.fetchall())
        for fila in vencidas:
            cur.execute(_CIERRE, ("expirada", fila["id_sesion_invitado"]))
        return vencidas

