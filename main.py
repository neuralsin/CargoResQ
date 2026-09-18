"""
CargoResQ — Cross-Carrier Emergency Cargo Rescue Platform.
Unified, production-ready asynchronous backend engine.

Run simply via:
    python main.py
or:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# Shared utilities
from shared.database import init_database
from shared.observability import configure_logging, metrics_endpoint_response, logger

# Services & Routers
from services.core_api.app.events.producer import event_producer
from services.core_api.app.api.breakdowns import limiter, router as breakdowns_router
from services.core_api.app.api.auth import router as auth_router
from services.core_api.app.api.companies import router as companies_router
from services.core_api.app.api.trucks import router as trucks_router
from services.core_api.app.api.shipments import router as shipments_router
from services.core_api.app.api.simulation import router as simulation_router
from services.core_api.app.api.driver import router as driver_router
from services.core_api.app.config import allowed_origin_list
from services.matching_engine.app.router import router as matching_router
from services.pricing_engine.app.router import router as pricing_router
from services.escrow_ledger.app.router import router as escrow_router
from services.orchestrator.app.router import router as orchestrator_router
from services.realtime_gateway.app.connection_manager import manager

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting_cargoresq_backend")
    # Initialize database tables
    await init_database()
    # Start event producer
    await event_producer.start()

    # Bridge all backend events directly to real-time WebSocket broadcast
    async def _realtime_event_bridge(topic: str, envelope: dict):
        await manager.broadcast({"type": topic, "payload": envelope})

    event_producer.register_local_subscriber(_realtime_event_bridge)
    logger.info("cargoresq_backend_ready", port=os.getenv("PORT", 8000))
    try:
        yield
    finally:
        logger.info("stopping_cargoresq_backend")
        await event_producer.stop()


app = FastAPI(
    title="CargoResQ — Cross-Carrier Emergency Cargo Rescue Platform",
    description=(
        "Production-grade backend engine implementing real-time geospatial rescue matching, "
        "algorithmic pricing, double-entry escrow ledger, incident orchestration state machine, "
        "and live WebSocket streaming."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Phase 18.6: Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS: allow seamless frontend and mobile app access
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origin_list(),
    allow_credentials=allowed_origin_list() != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all domain routers (Phase 18.3: all prefixed with /api/v1/)
app.include_router(auth_router)
app.include_router(companies_router)
app.include_router(trucks_router)
app.include_router(shipments_router)
app.include_router(breakdowns_router)
app.include_router(matching_router)
app.include_router(pricing_router)
app.include_router(escrow_router)
app.include_router(orchestrator_router)
app.include_router(simulation_router)
app.include_router(driver_router)


from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def serve_desktop_console():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "CargoResQ API Server running. Visit /docs for OpenAPI."}


@app.get("/mobile", include_in_schema=False)
async def serve_mobile_driver_app():
    mobile_file = STATIC_DIR / "mobile.html"
    if mobile_file.exists():
        return FileResponse(mobile_file)
    return {"message": "CargoResQ Mobile Driver App not found."}


# Phase 6: Real-Time WebSocket Gateway endpoint
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Live real-time feed for Command Center map, spoilage countdowns,
    and escrow ledger progression.
    """
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.debug("ws_disconnect_handled", error=str(e))
        manager.disconnect(ws)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "CargoResQ Unified Backend",
        "version": "2.0.0",
        "active_websockets": len(manager.active_connections),
    }


@app.get("/metrics")
async def metrics():
    return metrics_endpoint_response()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
