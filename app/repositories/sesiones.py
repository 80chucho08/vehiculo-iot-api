from __future__ import annotations

from datetime import datetime
from typing import Any

from app.repositories.base import transaccion, una


def crear(sid: str, id_usuario: int, expira: datetime, absoluto: datetime) -> None:
    with transaccion() as cur:
        cur.execute(
            "INSERT INTO sesiones_usuario (sid, id_usuario, expira_en, expira_absoluto) VALUES (%s, %s, %s, %s)",
            (sid, id_usuario, expira, absoluto),
        )


def obtener_vigente(sid: str) -> dict[str, Any] | None:
    """Sesión no revocada ni vencida, con el estado ACTUAL del usuario (rol, activo)."""
    return una(
        "SELECT s.sid, s.id_usuario, s.emitida_en, s.expira_en, s.expira_absoluto, "
        "       u.email, u.nombre, u.rol, u.activo "
        "  FROM sesiones_usuario s JOIN usuarios u ON u.id_usuario = s.id_usuario "
        " WHERE s.sid = %s AND s.revocada_en IS NULL "
        "   AND s.expira_en > UTC_TIMESTAMP() AND s.expira_absoluto > UTC_TIMESTAMP()",
        (sid,),
    )


def renovar(sid: str, expira: datetime) -> bool:
    with transaccion() as cur:
        cur.execute(
            "UPDATE sesiones_usuario SET expira_en = %s "
            " WHERE sid = %s AND revocada_en IS NULL AND expira_en > UTC_TIMESTAMP() AND %s <= expira_absoluto",
            (expira, sid, expira),
        )
        return cur.rowcount == 1


def revocar(sid: str, motivo: str) -> None:
    with transaccion() as cur:
        cur.execute(
            "UPDATE sesiones_usuario SET revocada_en = UTC_TIMESTAMP(), motivo_revocacion = %s "
            " WHERE sid = %s AND revocada_en IS NULL",
            (motivo, sid),
        )


def revocar_de_usuario(id_usuario: int, motivo: str) -> int:
    """Cierra todas las sesiones abiertas del usuario (cambio de rol, desactivación, contraseña)."""
    with transaccion() as cur:
        cur.execute(
            "UPDATE sesiones_usuario SET revocada_en = UTC_TIMESTAMP(), motivo_revocacion = %s "
            " WHERE id_usuario = %s AND revocada_en IS NULL",
            (motivo, id_usuario),
        )
        return cur.rowcount
