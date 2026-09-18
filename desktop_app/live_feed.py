"""
The live WebSocket feed.

Runs its own asyncio loop on a background thread and hands events back to the
Tk thread via a queue, because Tk widgets may only be touched from the thread
that created them.

The console used to display a "live API" badge and a WebSocket connection
count while opening no socket at all. This is the socket.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any, Callable, Dict, Optional

import websockets

RECONNECT_DELAY_SECONDS = 3.0
PING_INTERVAL_SECONDS = 20.0


class LiveFeed:
    """Background WebSocket client with automatic reconnect."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.connected = False
        self.last_error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="cargoresq-ws")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: None)

    def drain(self, limit: int = 50) -> list[Dict[str, Any]]:
        """Collect queued events. Called from the Tk thread on a timer."""
        drained = []
        for _ in range(limit):
            try:
                drained.append(self.events.get_nowait())
            except queue.Empty:
                break
        return drained

    # -- background ------------------------------------------------------
    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_forever())
        finally:
            self._loop.close()

    async def _connect_forever(self) -> None:
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, open_timeout=8) as socket:
                    # The socket is accepted before it is trusted; it has a
                    # few seconds to present a token or it is closed.
                    await socket.send(json.dumps({"type": "auth", "token": self.token}))
                    ack = json.loads(await asyncio.wait_for(socket.recv(), timeout=8))
                    if ack.get("type") != "auth.ok":
                        self.last_error = "Authentication rejected"
                        self.connected = False
                        await asyncio.sleep(RECONNECT_DELAY_SECONDS)
                        continue

                    self.connected = True
                    self.last_error = None
                    self.events.put({"type": "_connected", "payload": ack.get("payload", {})})

                    keepalive = asyncio.create_task(self._keepalive(socket))
                    try:
                        async for raw in socket:
                            try:
                                self.events.put(json.loads(raw))
                            except json.JSONDecodeError:
                                continue
                    finally:
                        keepalive.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                if self.connected:
                    self.connected = False
                    self.events.put({"type": "_disconnected", "payload": {}})

            if self._stop.is_set():
                break
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _keepalive(self, socket) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            await socket.send(json.dumps({"type": "ping"}))
