"""Tareas periódicas de seguridad (un solo proceso uvicorn, ver R5):

- Cierra las reservas de invitado vencidas, DETIENE la unidad y desconecta al invitado.
- Revalida cada conexión WSS de observador (Etapa 8): si su sesión venció, se revocó, perdió la
  asignación o el soporte, se cierra con código 4403. Así un cambio de permisos corta el canal.
"""

from __future__ import annotations

import asyncio
import logging

import pymysql

from app.core.websocket_manager import websocket_manager
from app.repositories import invitados, propiedad

logger = logging.getLogger(__name__)

INTERVALO_S = 5
REVALIDAR_WSS_CADA = 3  # ciclos: cada 15 s


async def finalizar_reserva(sid: str, motivo: str) -> None:
    """Cierra la reserva (si seguía abierta), avisa por WSS, desconecta al invitado y detiene la unidad."""
    from app.api.routes.operaciones import detener_por_sistema  # import diferido: evita ciclo

    try:
        fila = await asyncio.to_thread(invitados.finalizar, sid, motivo)
    except pymysql.MySQLError:
        logger.exception("No se pudo cerrar la reserva sid=%s", sid)
        return
    if fila is None:
        return
    await _tras_cierre(int(fila["id_vehiculo"]), sid, motivo, detener_por_sistema)


async def _tras_cierre(id_vehiculo: int, sid: str, motivo: str, detener_por_sistema) -> None:
    await websocket_manager.send_to_vehicle(
        id_vehiculo, {"tipo": "reserva_finalizada", "id_vehiculo": id_vehiculo, "motivo": motivo}, role="observador"
    )
    await detener_por_sistema(id_vehiculo, f"reserva_{motivo}")
    await websocket_manager.cerrar_por_sid(sid, 4403, "La prueba de invitado terminó.")


async def refrescar_espejos() -> None:
    """Dispositivo compartido (V009): relee qué unidades usan el ESP8266 de otra."""
    try:
        websocket_manager.fijar_espejos(await asyncio.to_thread(propiedad.espejos))
    except pymysql.MySQLError:
        logger.warning("Vigilante: no se pudo leer el dispositivo compartido; se conserva el anterior.")


async def ciclo_unico(revalidar=None) -> None:
    from app.api.routes.operaciones import detener_por_sistema

    try:
        vencidas = await asyncio.to_thread(invitados.cerrar_vencidas)
    except pymysql.MySQLError:
        logger.warning("Vigilante: no se pudieron revisar las reservas (RDS no respondió).")
        vencidas = []
    for fila in vencidas:
        await _tras_cierre(int(fila["id_vehiculo"]), fila["sid"], "expirada", detener_por_sistema)
    await refrescar_espejos()
    if revalidar is not None:
        await revalidar()


async def vigilar(revalidar=None) -> None:
    ciclo = 0
    await refrescar_espejos()
    while True:
        await asyncio.sleep(INTERVALO_S)
        ciclo += 1
        try:
            await ciclo_unico(revalidar if ciclo % REVALIDAR_WSS_CADA == 0 else None)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Vigilante: error inesperado; se reintenta en el siguiente ciclo.")
