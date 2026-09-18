"""
Room-based WebSocket connection manager.

Sockets subscribe to named rooms; events are delivered to a room, never to
"everyone". The previous manager held one flat set of sockets and a broadcast()
that sent every event to every client, which meant one carrier's breakdown --
including cargo value and exact coordinates -- was delivered to every other
connected carrier.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Set

from fastapi import WebSocket

from shared.observability import ACTIVE_WEBSOCKETS, logger


@dataclass
class Principal:
    """The authenticated identity behind a socket."""

    company_id: str
    principal_type: str  # "company" | "driver"
    principal_id: str
    role: str
    subject: str
    expires_at: Optional[int] = None

    @property
    def is_driver(self) -> bool:
        return self.principal_type == "driver"


@dataclass
class Session:
    websocket: WebSocket
    principal: Principal
    rooms: Set[str] = field(default_factory=set)


class ConnectionManager:
    def __init__(self) -> None:
        self._sessions: Dict[WebSocket, Session] = {}
        self._rooms: Dict[str, Set[WebSocket]] = defaultdict(set)

    # -- lifecycle -------------------------------------------------------
    async def accept(self, ws: WebSocket) -> None:
        """Accept the socket without granting it anything.

        The socket must authenticate before it is registered; until then it is
        connected but subscribed to nothing.
        """
        await ws.accept()

    def register(self, ws: WebSocket, principal: Principal, rooms: Iterable[str]) -> Session:
        session = Session(websocket=ws, principal=principal, rooms=set())
        self._sessions[ws] = session
        for room in rooms:
            self.subscribe(ws, room)
        ACTIVE_WEBSOCKETS.set(len(self._sessions))
        logger.info(
            "websocket_authenticated",
            company_id=principal.company_id,
            principal_type=principal.principal_type,
            rooms=sorted(session.rooms),
            total_active=len(self._sessions),
        )
        return session

    def disconnect(self, ws: WebSocket) -> None:
        session = self._sessions.pop(ws, None)
        if session:
            for room in session.rooms:
                members = self._rooms.get(room)
                if members:
                    members.discard(ws)
                    if not members:
                        self._rooms.pop(room, None)
        ACTIVE_WEBSOCKETS.set(len(self._sessions))

    # -- subscriptions ---------------------------------------------------
    def subscribe(self, ws: WebSocket, room: str) -> None:
        session = self._sessions.get(ws)
        if session is None:
            return
        session.rooms.add(room)
        self._rooms[room].add(ws)

    def unsubscribe(self, ws: WebSocket, room: str) -> None:
        session = self._sessions.get(ws)
        if session is None:
            return
        session.rooms.discard(room)
        members = self._rooms.get(room)
        if members:
            members.discard(ws)
            if not members:
                self._rooms.pop(room, None)

    def principal_for(self, ws: WebSocket) -> Optional[Principal]:
        session = self._sessions.get(ws)
        return session.principal if session else None

    # -- delivery --------------------------------------------------------
    async def send_to_room(self, room: str, payload: Dict[str, Any]) -> int:
        """Deliver one payload to one room. Returns the number of sockets reached."""
        members = list(self._rooms.get(room, ()))
        if not members:
            return 0

        dead = []
        delivered = 0
        for ws in members:
            try:
                await ws.send_json(payload)
                delivered += 1
            except Exception as exc:  # pragma: no cover - transport failure
                logger.debug("websocket_send_failed", room=room, error=str(exc))
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)
        return delivered

    async def send_to_socket(self, ws: WebSocket, payload: Dict[str, Any]) -> None:
        try:
            await ws.send_json(payload)
        except Exception as exc:  # pragma: no cover - transport failure
            logger.debug("websocket_send_failed", error=str(exc))
            self.disconnect(ws)

    # -- introspection ---------------------------------------------------
    @property
    def active_connections(self) -> Set[WebSocket]:
        return set(self._sessions)

    def room_size(self, room: str) -> int:
        return len(self._rooms.get(room, ()))

    def rooms_for(self, ws: WebSocket) -> Set[str]:
        session = self._sessions.get(ws)
        return set(session.rooms) if session else set()


manager = ConnectionManager()
