"""Reportes de falla del comprador sobre su unidad (V009).

Flujo: el comprador reporta (y puede autorizar soporte temporal en el mismo paso) → el admin lo
pasa a «en revisión» y, con la autorización vigente, prueba la unidad → al marcarlo «resuelto» se
REVOCA el soporte de esa unidad: el acceso temporal termina con la intervención.
"""

from __future__ import annotations

from typing import Any

from app.repositories.base import Conflicto, paginar, todas, transaccion

_COLUMNAS = (
    "r.id_reporte, r.id_vehiculo, v.nombre AS vehiculo, v.numero_serie, r.id_usuario, u.email, u.nombre AS comprador, "
    "r.descripcion, r.estado, r.creado_en, r.actualizado_en, r.atendido_por, r.respuesta, r.resuelto_en, "
    "(SELECT MAX(s.expira_en) FROM autorizaciones_soporte s WHERE s.id_vehiculo = r.id_vehiculo "
    "   AND s.revocada_en IS NULL AND s.expira_en > UTC_TIMESTAMP()) AS soporte_hasta"
)
_JOIN = (
    " FROM reportes_falla r JOIN vehiculos v ON v.id_vehiculo = r.id_vehiculo "
    " JOIN usuarios u ON u.id_usuario = r.id_usuario"
)


def crear(id_vehiculo: int, id_usuario: int, descripcion: str) -> int:
    with transaccion() as cur:
        cur.execute(
            "INSERT INTO reportes_falla (id_vehiculo, id_usuario, descripcion) VALUES (%s, %s, %s)",
            (id_vehiculo, id_usuario, descripcion),
        )
        return int(cur.lastrowid)


def de_usuario(id_usuario: int, id_vehiculo: int | None = None) -> list[dict[str, Any]]:
    condicion, params = "r.id_usuario = %s", [id_usuario]
    if id_vehiculo is not None:
        condicion += " AND r.id_vehiculo = %s"
        params.append(id_vehiculo)
    return todas(f"SELECT {_COLUMNAS}{_JOIN} WHERE {condicion} ORDER BY r.id_reporte DESC LIMIT 50", tuple(params))


def listar(pagina: int, por_pagina: int, estado: str | None) -> tuple[int, list[dict[str, Any]]]:
    where, params = ("", ()) if estado is None else (" WHERE r.estado = %s", (estado,))
    limite, desplazamiento = paginar(pagina, por_pagina)
    with transaccion() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM reportes_falla r{where}", params)
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"SELECT {_COLUMNAS}{_JOIN}{where} ORDER BY r.estado = 'resuelto', r.id_reporte DESC LIMIT %s OFFSET %s",
            (*params, limite, desplazamiento),
        )
        return total, list(cur.fetchall())


def actualizar(id_reporte: int, estado: str, respuesta: str | None, id_admin: int) -> dict[str, Any]:
    """Cambia el estado. Al resolver, revoca el soporte vigente de la unidad. Devuelve la fila anterior
    con `soportes_revocados`."""
    with transaccion() as cur:
        cur.execute("SELECT id_reporte, id_vehiculo, id_usuario, estado FROM reportes_falla WHERE id_reporte = %s FOR UPDATE",
                    (id_reporte,))
        anterior = cur.fetchone()
        if anterior is None:
            raise LookupError("Reporte no encontrado.")
        if anterior["estado"] == "resuelto":
            raise Conflicto("El reporte ya está resuelto.")
        resuelto = estado == "resuelto"
        cur.execute(
            "UPDATE reportes_falla SET estado = %s, respuesta = COALESCE(%s, respuesta), atendido_por = %s, "
            "       resuelto_en = IF(%s, UTC_TIMESTAMP(), NULL) WHERE id_reporte = %s",
            (estado, respuesta, id_admin, int(resuelto), id_reporte),
        )
        revocados = 0
        if resuelto:
            cur.execute(
                "UPDATE autorizaciones_soporte SET revocada_en = UTC_TIMESTAMP() "
                " WHERE id_vehiculo = %s AND revocada_en IS NULL AND expira_en > UTC_TIMESTAMP()",
                (anterior["id_vehiculo"],),
            )
            revocados = cur.rowcount
        return {**anterior, "soportes_revocados": revocados}
