
import asyncio

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        self.connections: dict[int, dict[WebSocket, str]] = {}
        self.observer_command_opt_in: set[WebSocket] = set()
        self.pending_acks: dict[str, asyncio.Future[dict]] = {}
        self.pending_demo_acks: dict[int, asyncio.Future[dict]] = {}
        # Etapa 8: quién está detrás de cada conexión de observador (None = anónimo de transición).
        self.actores: dict[WebSocket, object] = {}
        # V009 · dispositivo compartido: unidad registrada -> unidad cuyo ESP8266 la mueve.
        # Permite registrar un mismo carrito físico varias veces (exhibición, vendida, en stock).
        self.espejos: dict[int, int] = {}

    def fijar_espejos(self, mapa: dict[int, int]) -> None:
        self.espejos = {int(u): int(f) for u, f in mapa.items() if f is not None and int(u) != int(f)}

    def unidad_fisica(self, id_vehiculo: int) -> int:
        """Unidad cuya conexión de dispositivo atiende a `id_vehiculo` (ella misma si no comparte)."""
        return self.espejos.get(id_vehiculo, id_vehiculo)

    def unidades_del_dispositivo(self, id_fisica: int) -> list[int]:
        return [id_fisica, *sorted(u for u, f in self.espejos.items() if f == id_fisica)]

    async def connect(
        self,
        id_vehiculo: int,
        websocket: WebSocket,
        role: str = "observador",
        recibir_comandos: bool = False,
        actor: object = None,
    ):
        await websocket.accept()
        self.actores[websocket] = actor

        if id_vehiculo not in self.connections:
            self.connections[id_vehiculo] = {}

        self.connections[id_vehiculo][websocket] = role
        if role == "observador" and recibir_comandos:
            self.observer_command_opt_in.add(websocket)

    def disconnect(
        self,
        id_vehiculo: int,
        websocket: WebSocket,
    ):
        clientes = self.connections.get(id_vehiculo)

        if clientes is None:
            return

        clientes.pop(websocket, None)
        self.observer_command_opt_in.discard(websocket)
        self.actores.pop(websocket, None)

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
        if role == "dispositivo":
            id_vehiculo = self.unidad_fisica(id_vehiculo)
        clientes = self.connections.get(id_vehiculo, {})
        if role is None:
            return len(clientes)
        return sum(1 for cliente_role in clientes.values() if cliente_role == role)

    def receptores(self, id_vehiculo: int) -> list[WebSocket]:
        return [
            websocket
            for websocket, role in self.connections.get(id_vehiculo, {}).items()
            if role == "observador"
        ]

    def observer_connections(self, id_vehiculo: int) -> list[WebSocket]:
        return self.receptores(id_vehiculo)

    async def enviar_a_observadores(self, id_vehiculo: int, mensaje: dict) -> int:
        clientes = self.receptores(id_vehiculo)
        enviados = 0
        for websocket in clientes:
            try:
                await websocket.send_json(mensaje)
                enviados += 1
            except Exception:
                self.disconnect(id_vehiculo, websocket)
        return enviados

    async def send_to_vehicle(
        self,
        id_vehiculo: int,
        mensaje: dict,
        role: str | None = None,
    ) -> int:
        conexiones = self.connections.get(id_vehiculo, {})
        destinos = [
            (id_vehiculo, websocket, mensaje)
            for websocket, cliente_role in conexiones.items()
            if role is None or cliente_role == role
        ]
        fisica = self.unidad_fisica(id_vehiculo)
        if fisica != id_vehiculo and role in (None, "dispositivo"):
            # El firmware solo acepta mensajes con SU id_vehiculo: se reescribe para el ESP8266 compartido.
            copia = {**mensaje, "id_vehiculo": fisica} if "id_vehiculo" in mensaje else mensaje
            destinos = [d for d in destinos if conexiones.get(d[1]) != "dispositivo"]
            destinos += [
                (fisica, websocket, copia)
                for websocket, cliente_role in self.connections.get(fisica, {}).items()
                if cliente_role == "dispositivo"
            ]

        enviados = 0

        for id_destino, websocket, contenido in destinos:
            try:
                await websocket.send_json(contenido)
                enviados += 1
            except Exception:
                self.disconnect(id_destino, websocket)

        return enviados

    async def avisar_estado_dispositivo(self, id_fisica: int, conectado: bool) -> None:
        """dispositivo_estado a los observadores de la unidad física y de las que comparten su ESP8266."""
        for id_unidad in self.unidades_del_dispositivo(id_fisica):
            await self.send_to_vehicle(
                id_unidad, {"tipo": "dispositivo_estado", "id_vehiculo": id_unidad, "conectado": conectado}, role="observador"
            )

    def observadores(self) -> list[tuple[int, WebSocket, object]]:
        """(id_vehiculo, socket, actor) de todas las conexiones de observador abiertas."""
        return [
            (id_vehiculo, websocket, self.actores.get(websocket))
            for id_vehiculo, clientes in list(self.connections.items())
            for websocket, role in list(clientes.items())
            if role == "observador"
        ]

    async def cerrar(self, id_vehiculo: int, websocket: WebSocket, codigo: int, motivo: str) -> None:
        """Cierra una conexión por falta de permiso (4401 sin credencial válida, 4403 acceso revocado)."""
        self.disconnect(id_vehiculo, websocket)
        try:
            await asyncio.wait_for(websocket.close(code=codigo, reason=motivo[:120]), timeout=2)
        except Exception:
            pass

    async def cerrar_por_sid(self, sid: str, codigo: int, motivo: str) -> int:
        cerradas = 0
        for id_vehiculo, websocket, actor in self.observadores():
            if actor is not None and getattr(actor, "sid", None) == sid:
                await self.cerrar(id_vehiculo, websocket, codigo, motivo)
                cerradas += 1
        return cerradas

    def conexiones_de_sid(self, sid: str) -> int:
        return sum(1 for _, _, actor in self.observadores() if actor is not None and getattr(actor, "sid", None) == sid)

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
