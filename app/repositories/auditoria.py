from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import pymysql

from app.repositories.base import paginar, transaccion

logger = logging.getLogger(__name__)


def registrar(
    accion: str,
    recurso: str,
    *,
    id_actor: int | None,
    rol_actor: str,
    id_recurso: Any = None,
    resultado: str = "ok",
    detalle: dict[str, Any] | None = None,
    ip_hash: str | None = None,
) -> None:
    """Agrega una fila a la bitácora. Un fallo al auditar se registra en el log, sin tumbar la petición."""
    try:
        with transaccion() as cur:
            cur.execute(
                "INSERT INTO auditoria (id_actor, rol_actor, accion, recurso, id_recurso, resultado, detalle, ip_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    id_actor,
                    rol_actor,
                    accion,
                    recurso,
                    None if id_recurso is None else str(id_recurso),
                    resultado,
                    json.dumps(detalle, ensure_ascii=False, default=str) if detalle else None,
                    ip_hash,
                ),
            )
    except pymysql.MySQLError:
        logger.exception("No se pudo registrar la auditoría accion=%s recurso=%s", accion, recurso)


def listar(
    pagina: int,
    por_pagina: int,
    accion: str | None,
    recurso: str | None,
    id_actor: int | None,
    resultado: str | None,
    desde: datetime | None,
    hasta: datetime | None,
) -> tuple[int, list[dict[str, Any]]]:
    condiciones, params = [], []
    for columna, valor in (("a.accion", accion), ("a.recurso", recurso), ("a.id_actor", id_actor), ("a.resultado", resultado)):
        if valor is not None:
            condiciones.append(f"{columna} = %s")
            params.append(valor)
    if desde is not None:
        condiciones.append("a.fecha >= %s")
        params.append(desde)
    if hasta is not None:
        condiciones.append("a.fecha < %s")
        params.append(hasta)
    where = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
    limite, desplazamiento = paginar(pagina, por_pagina)
    with transaccion() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM auditoria a{where}", tuple(params))
        total = int(cur.fetchone()["n"])
        cur.execute(
            "SELECT a.id_auditoria, a.fecha, a.id_actor, u.email AS actor, a.rol_actor, a.accion, a.recurso, "
            "       a.id_recurso, a.resultado, a.detalle "
            "  FROM auditoria a LEFT JOIN usuarios u ON u.id_usuario = a.id_actor"
            f"{where} ORDER BY a.id_auditoria DESC LIMIT %s OFFSET %s",
            (*params, limite, desplazamiento),
        )
        filas = list(cur.fetchall())
    for fila in filas:
        if isinstance(fila.get("detalle"), str):
            try:
                fila["detalle"] = json.loads(fila["detalle"])
            except ValueError:
                pass
    return total, filas
