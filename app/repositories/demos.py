"""Demos de fábrica y privadas: lectura filtrada por dueño y escritura atómica de pasos."""

from __future__ import annotations

from typing import Any

from app.repositories.base import todas, transaccion, una

# Límites por omisión si la unidad no tiene modelo comercial (coinciden con V002).
LIMITES_POR_OMISION = {"duracion_max_paso_ms": 3000, "pasos_max_demo": 20}


def visible_para(demo: dict[str, Any], id_usuario: int | None) -> bool:
    """Una demo de fábrica la ve quien puede ver la unidad; una privada, solo su dueño."""
    return demo.get("tipo", "fabrica") != "privada" or (id_usuario is not None and demo.get("id_propietario") == id_usuario)


def demo(id_demo: int) -> dict[str, Any] | None:
    return una(
        "SELECT id_demo, id_vehiculo, nombre, descripcion, activo, tipo, id_propietario FROM demos WHERE id_demo = %s",
        (id_demo,),
    )


def contexto_unidad(id_vehiculo: int) -> dict[str, Any]:
    """Límites del modelo y comandos de la unidad, para validar y para el editor."""
    fila = una(
        "SELECT m.duracion_max_paso_ms, m.pasos_max_demo FROM vehiculos v "
        "  LEFT JOIN modelos_vehiculo m ON m.id_modelo = v.id_modelo WHERE v.id_vehiculo = %s",
        (id_vehiculo,),
    )
    limites = {k: (fila or {}).get(k) or v for k, v in LIMITES_POR_OMISION.items()}
    comandos = todas(
        "SELECT id_comando, nombre, codigo, activo, duracion_max_ms FROM comandos WHERE id_vehiculo = %s ORDER BY codigo",
        (id_vehiculo,),
    )
    return {"limites": limites, "comandos": comandos}


def demos_con_pasos(id_vehiculo: int, id_usuario: int) -> list[dict[str, Any]]:
    """Demos de fábrica activas y TODAS las privadas del usuario en esa unidad, con sus pasos."""
    demos = todas(
        "SELECT id_demo, nombre, descripcion, activo, tipo, id_propietario FROM demos "
        " WHERE id_vehiculo = %s AND ((tipo = 'fabrica' AND activo = 1) OR (tipo = 'privada' AND id_propietario = %s)) "
        " ORDER BY tipo = 'privada', id_demo",
        (id_vehiculo, id_usuario),
    )
    if not demos:
        return []
    marcadores = ", ".join(["%s"] * len(demos))
    pasos = todas(
        "SELECT dm.id_demo, dm.orden, dm.id_comando, c.nombre AS comando, c.codigo, dm.duracion_ms, dm.pausa_ms "
        "  FROM demo_movimientos dm JOIN comandos c ON c.id_comando = dm.id_comando "
        f" WHERE dm.id_demo IN ({marcadores}) ORDER BY dm.id_demo, dm.orden",
        tuple(d["id_demo"] for d in demos),
    )
    for d in demos:
        d["pasos"] = [p for p in pasos if p["id_demo"] == d["id_demo"]]
        d["editable"] = d["tipo"] == "privada"
    return demos


def _insertar_pasos(cur, id_demo: int, pasos: list[dict[str, Any]]) -> None:
    for orden, paso in enumerate(pasos, start=1):
        cur.execute(
            "INSERT INTO demo_movimientos (id_demo, id_comando, orden, duracion_ms, pausa_ms) VALUES (%s, %s, %s, %s, %s)",
            (id_demo, paso["id_comando"], orden, paso["duracion_ms"], paso["pausa_ms"]),
        )


def crear_privada(id_vehiculo: int, id_usuario: int, nombre: str, descripcion: str | None, activo: bool,
                  pasos: list[dict[str, Any]]) -> int:
    with transaccion() as cur:
        cur.execute(
            "INSERT INTO demos (id_vehiculo, nombre, descripcion, activo, tipo, id_propietario) "
            "VALUES (%s, %s, %s, %s, 'privada', %s)",
            (id_vehiculo, nombre, descripcion, int(activo), id_usuario),
        )
        id_demo = int(cur.lastrowid)
        _insertar_pasos(cur, id_demo, pasos)
        return id_demo


def reemplazar_privada(id_demo: int, id_vehiculo: int, id_usuario: int, nombre: str, descripcion: str | None,
                       activo: bool, pasos: list[dict[str, Any]]) -> bool:
    """Reemplaza nombre y TODOS los pasos en una transacción (agregar, quitar, editar y reordenar
    se guardan juntos; así no choca con UNIQUE(id_demo, orden)). False si no es del usuario."""
    with transaccion() as cur:
        cur.execute(
            "SELECT id_demo FROM demos WHERE id_demo = %s AND id_vehiculo = %s AND tipo = 'privada' "
            "   AND id_propietario = %s FOR UPDATE",
            (id_demo, id_vehiculo, id_usuario),
        )
        if cur.fetchone() is None:
            return False
        cur.execute("UPDATE demos SET nombre = %s, descripcion = %s, activo = %s WHERE id_demo = %s",
                    (nombre, descripcion, int(activo), id_demo))
        cur.execute("DELETE FROM demo_movimientos WHERE id_demo = %s", (id_demo,))
        _insertar_pasos(cur, id_demo, pasos)
        return True


def eliminar_privada(id_demo: int, id_vehiculo: int, id_usuario: int) -> bool:
    """Borra la demo privada y sus pasos (CASCADE). No toca el historial: este referencia comandos."""
    with transaccion() as cur:
        cur.execute(
            "DELETE FROM demos WHERE id_demo = %s AND id_vehiculo = %s AND tipo = 'privada' AND id_propietario = %s",
            (id_demo, id_vehiculo, id_usuario),
        )
        return cur.rowcount == 1
