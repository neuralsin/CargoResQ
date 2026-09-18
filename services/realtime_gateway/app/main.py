"""
Real-Time Gateway FastAPI Application (Phase 6).
Provides live WebSocket streaming for Command Center map, countdowns, and escrow updates.
"""
import asyncio
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from .connection_manager import manager
from .consumer import consume_all_events
from shared.observability import metrics_endpoint_response, logger

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

app = FastAPI(
    title="CargoResQ Real-Time Gateway",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

consumer_task: asyncio.Task = None


@app.on_event("startup")
async def startup():
    global consumer_task
    # Start background consumer if Kafka bootstrap is set
    consumer_task = asyncio.create_task(
        consume_all_events(KAFKA_BOOTSTRAP, manager.broadcast)
    )


@app.on_event("shutdown")
async def shutdown():
    if consumer_task:
        consumer_task.cancel()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # Keep-alive ping/pong
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.debug("websocket_exception", error=str(e))
        manager.disconnect(ws)


class BroadcastPayload(BaseModel):
    type: str
    payload: dict


@app.post("/api/v1/broadcast")
async def broadcast_endpoint(event: BroadcastPayload):
    """Direct broadcast endpoint for local testing and internal triggers."""
    await manager.broadcast(event.model_dump())
    return {"status": "broadcast_sent", "active_clients": len(manager.active_connections)}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "realtime-gateway",
        "active_connections": len(manager.active_connections),
    }


@app.get("/metrics")
async def metrics():
    return metrics_endpoint_response()
