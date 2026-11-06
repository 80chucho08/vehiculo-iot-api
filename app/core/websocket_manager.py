
import asyncio

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        self.connections: dict[int, dict[WebSocket, str]] = {}
        self.pending_acks: dict[str, asyncio.Future[dict]] = {}
        self.pending_demo_acks: dict[int, asyncio.Future[dict]] = {}

    async def connect(
        self,
        id_vehiculo: int,
        websocket: WebSocket,
        role: str = "observador",
    ):
        await websocket.accept()

        if id_vehiculo not in self.connections:
            self.connections[id_vehiculo] = {}

        self.connections[id_vehiculo][websocket] = role

    def disconnect(
        self,
        id_vehiculo: int,
        websocket: WebSocket,
    ):
        clientes = self.connections.get(id_vehiculo)

        if clientes is None:
            return

        clientes.pop(websocket, None)

        if not clientes:
            self.connections.pop(id_vehiculo, None)

    async def reemplazar_dispositivo(self, id_vehiculo: int) -> int:
        clientes = self.connections.get(id_vehiculo, {})
        viejos = [ws for ws, role in clientes.items() if role == "dispositivo"]
        for ws in viejos:
            clientes.pop(ws, None)
            try:
                await asyncio.wait_for(ws.close(code=1012), timeout=2)
            except Exception:
                pass
        return len(viejos)

    def connected_clients(
        self,
        id_vehiculo: int,
        role: str | None = None,
    ) -> int:
        clientes = self.connections.get(id_vehiculo, {})
        if role is None:
            return len(clientes)
        return sum(1 for cliente_role in clientes.values() if cliente_role == role)

    async def send_to_vehicle(
        self,
        id_vehiculo: int,
        mensaje: dict,
        role: str | None = None,
    ) -> int:
        conexiones = self.connections.get(id_vehiculo, {})
        clientes = [
            websocket
            for websocket, cliente_role in conexiones.items()
            if role is None or cliente_role == role
        ]

        enviados = 0

        for websocket in clientes:
            try:
                await websocket.send_json(mensaje)
                enviados += 1
            except Exception:
                self.disconnect(id_vehiculo, websocket)

        return enviados

    def crear_espera_ack(self, id_solicitud: str) -> asyncio.Future[dict]:
        future = asyncio.get_running_loop().create_future()
        self.pending_acks[id_solicitud] = future
        return future

    def resolver_ack(self, id_solicitud: str, mensaje: dict) -> bool:
        future = self.pending_acks.get(id_solicitud)
        if future is None or future.done():
            return False
        future.set_result(mensaje)
        return True

    def quitar_espera_ack(self, id_solicitud: str) -> None:
        self.pending_acks.pop(id_solicitud, None)

    def crear_espera_demo_ack(self, id_demo: int) -> asyncio.Future[dict]:
        future = asyncio.get_running_loop().create_future()
        self.pending_demo_acks[id_demo] = future
        return future

    def resolver_demo_ack(self, id_demo: int, mensaje: dict) -> bool:
        future = self.pending_demo_acks.get(id_demo)
        if future is None or future.done():
            return False
        future.set_result(mensaje)
        return True

    def quitar_espera_demo_ack(self, id_demo: int) -> None:
        self.pending_demo_acks.pop(id_demo, None)


websocket_manager = WebSocketManager()
