"""
WebSocket Connection Manager for Real-Time Gateway (Phase 6).
Handles concurrent WebSocket clients, dead socket pruning, and live broadcasts.
"""
from typing import Set, Dict, Any
from fastapi import WebSocket
from shared.observability import ACTIVE_WEBSOCKETS, logger


class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.add(ws)
        ACTIVE_WEBSOCKETS.set(len(self.active_connections))
        logger.info("websocket_client_connected", total_active=len(self.active_connections))

    def disconnect(self, ws: WebSocket):
        self.active_connections.discard(ws)
        ACTIVE_WEBSOCKETS.set(len(self.active_connections))
        logger.info("websocket_client_disconnected", total_active=len(self.active_connections))

    async def broadcast(self, event: Dict[str, Any]):
        dead: Set[WebSocket] = set()
        for conn in list(self.active_connections):
            try:
                await conn.send_json(event)
            except Exception as e:
                logger.debug("websocket_send_failed", error=str(e))
                dead.add(conn)

        if dead:
            self.active_connections.difference_update(dead)
            ACTIVE_WEBSOCKETS.set(len(self.active_connections))


manager = ConnectionManager()
