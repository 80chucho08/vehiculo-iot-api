
from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        self.connections: dict[int, set[WebSocket]] = {}

    async def connect(
        self,
        id_vehiculo: int,
        websocket: WebSocket,
    ):
        await websocket.accept()

        if id_vehiculo not in self.connections:
            self.connections[id_vehiculo] = set()

        self.connections[id_vehiculo].add(websocket)

    def disconnect(
        self,
        id_vehiculo: int,
        websocket: WebSocket,
    ):
        clientes = self.connections.get(id_vehiculo)

        if clientes is None:
            return

        clientes.discard(websocket)

        if not clientes:
            self.connections.pop(id_vehiculo, None)

    def connected_clients(self, id_vehiculo: int) -> int:
        return len(self.connections.get(id_vehiculo, set()))

    async def send_to_vehicle(
        self,
        id_vehiculo: int,
        mensaje: dict,
    ) -> int:
        clientes = list(
            self.connections.get(id_vehiculo, set())
        )

        enviados = 0

        for websocket in clientes:
            try:
                await websocket.send_json(mensaje)
                enviados += 1
            except Exception:
                self.disconnect(id_vehiculo, websocket)

        return enviados


websocket_manager = WebSocketManager()
