"""
CargoResQ -- cross-carrier emergency cargo rescue platform.

One deployable API. The services/ tree holds the domain modules (matching,
pricing, escrow, orchestration, realtime) that this process mounts; they are
boundaries in the code, not separate processes.

Run with:
    python main.py
or:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from services.core_api.app.api.auth import router as auth_router
from services.core_api.app.api.breakdowns import limiter, router as breakdowns_router
from services.core_api.app.api.companies import router as companies_router
from services.core_api.app.api.driver import router as driver_router
from services.core_api.app.api.shipments import router as shipments_router
from services.core_api.app.api.simulation import router as simulation_router
from services.core_api.app.api.trucks import router as trucks_router
from services.core_api.app.auth import decode_token
from services.core_api.app.config import allowed_origin_list, settings
from services.core_api.app.events.producer import event_producer
from services.escrow_ledger.app.router import router as escrow_router
from services.matching_engine.app.router import router as matching_router
from services.orchestrator.app.offers_router import router as offers_router
from services.orchestrator.app.relay_router import router as relay_router
from services.orchestrator.app.router import router as orchestrator_router
from services.pricing_engine.app.router import router as pricing_router
from services.realtime_gateway.app.connection_manager import Principal, manager
from services.sos.app.router import contacts_router, router as sos_router
from services.telemetry.app.router import router as telemetry_router
from services.telemetry.app.worker import run_sweeper
from services.realtime_gateway.app.projections import project
from services.realtime_gateway.app.rooms import authorise_room, base_rooms
from shared.database import async_session
from shared.observability import configure_logging, logger, metrics_endpoint_response
from shared.schema import ensure_schema

# Importing the registry is what populates Base.metadata.
import shared.models_registry  # noqa: F401

configure_logging()

#: How long a socket has to present a token before it is closed.
WS_AUTH_TIMEOUT_SECONDS = 10.0
WS_CLOSE_UNAUTHENTICATED = 4401
WS_CLOSE_FORBIDDEN = 4403


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting_cargoresq_backend")
    await ensure_schema()
    await event_producer.start()

    async def _realtime_event_bridge(topic: str, envelope: dict) -> None:
        """Route one backend event to its explicit audience.

        Events are no longer broadcast to every connected socket. The
        projection registry decides who sees what, and drops anything it does
        not recognise.
        """
        async with async_session() as session:
            deliveries = await project(topic, envelope, session)
        for room, payload in deliveries:
            await manager.send_to_room(room, payload)

    event_producer.register_local_subscriber(_realtime_event_bridge)

    # One background task handles everything periodic: GPS staleness, dwell,
    # and offer expiry. These are conditions no request can detect, because
    # the signal is that nothing arrived.
    sweeper = asyncio.create_task(run_sweeper(event_producer.publish))

    logger.info("cargoresq_backend_ready", port=os.getenv("PORT", 8000))
    try:
        yield
    finally:
        logger.info("stopping_cargoresq_backend")
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass
        await event_producer.stop()


app = FastAPI(
    title="CargoResQ -- Cross-Carrier Emergency Cargo Rescue Platform",
    description=(
        "Backend for cross-carrier cargo rescue: geospatial matching of compatible "
        "spare capacity, formula-driven pricing, a two-way rescue handshake, and an "
        "internal double-entry escrow ledger that settles against recorded condition "
        "data."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origin_list(),
    allow_credentials=allowed_origin_list() != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(companies_router)
app.include_router(trucks_router)
app.include_router(shipments_router)
app.include_router(breakdowns_router)
app.include_router(matching_router)
app.include_router(pricing_router)
app.include_router(escrow_router)
app.include_router(orchestrator_router)
app.include_router(offers_router)
app.include_router(relay_router)
app.include_router(simulation_router)
app.include_router(driver_router)
app.include_router(telemetry_router)
app.include_router(sos_router)
app.include_router(contacts_router)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "CargoResQ API",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
        "clients": [
            "desktop_app/ -- CustomTkinter operations console for carrier owners",
            "android_app/ -- native Android driver app",
        ],
    }


def _principal_from_claims(claims: dict) -> Principal:
    return Principal(
        company_id=str(claims["company_id"]),
        principal_type=str(claims.get("principal_type", "company")),
        principal_id=str(claims.get("principal_id") or claims.get("sub")),
        role=str(claims.get("role", "")),
        subject=str(claims.get("sub", "")),
        expires_at=claims.get("exp"),
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Authenticated, room-scoped live feed.

    The socket is accepted, then must send {"type":"auth","token":"<JWT>"}
    before it is subscribed to anything. Passing the token in a frame rather
    than the query string keeps it out of access logs and Referer headers.
    """
    await manager.accept(ws)

    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=WS_AUTH_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
        await ws.close(code=WS_CLOSE_UNAUTHENTICATED, reason="Authentication frame required")
        return

    if not isinstance(first, dict) or first.get("type") != "auth" or not first.get("token"):
        await ws.close(code=WS_CLOSE_UNAUTHENTICATED, reason="Expected an auth frame")
        return

    try:
        claims = decode_token(str(first["token"]))
        if not claims.get("company_id"):
            raise JWTError("token carries no company")
        principal = _principal_from_claims(claims)
    except (JWTError, KeyError, TypeError):
        await ws.close(code=WS_CLOSE_UNAUTHENTICATED, reason="Invalid token")
        return

    rooms = base_rooms(principal)
    manager.register(ws, principal, rooms)
    await manager.send_to_socket(
        ws,
        {
            "type": "auth.ok",
            "payload": {
                "companyId": principal.company_id,
                "principalType": principal.principal_type,
                "rooms": sorted(rooms),
            },
        },
    )

    try:
        while True:
            message = await ws.receive_json()
            if not isinstance(message, dict):
                continue

            kind = message.get("type")
            if kind == "ping":
                await manager.send_to_socket(ws, {"type": "pong"})
            elif kind == "subscribe":
                await _handle_subscribe(ws, principal, str(message.get("room", "")))
            elif kind == "unsubscribe":
                manager.unsubscribe(ws, str(message.get("room", "")))
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        logger.debug("ws_session_ended", error=str(exc))
        manager.disconnect(ws)


async def _handle_subscribe(ws: WebSocket, principal: Principal, room: str) -> None:
    """Authorise a requested room against the principal, server-side.

    A client-supplied room name is a request, not a grant.
    """
    if not room:
        return
    async with async_session() as session:
        refusal = await authorise_room(principal, room, session)

    if refusal:
        logger.warn(
            "ws_room_subscribe_refused",
            company_id=principal.company_id,
            room=room,
            reason=refusal,
        )
        await manager.send_to_socket(
            ws,
            {"type": "error", "payload": {"code": "forbidden_room", "room": room}},
        )
        return

    manager.subscribe(ws, room)
    await manager.send_to_socket(ws, {"type": "subscribed", "payload": {"room": room}})


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "CargoResQ Unified Backend",
        "version": app.version,
        "environment": settings.environment,
        "active_websockets": len(manager.active_connections),
    }


@app.get("/metrics")
async def metrics():
    return metrics_endpoint_response()


def print_network_help(port: int) -> None:
    import socket
    lan_ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print("\n" + "=" * 60)
    print(f"  CargoResQ Backend is LIVE on port {port}")
    print("=" * 60)
    print("  Driver Mobile App Connection Addresses:")
    if lan_ip:
        print(f"  * Wi-Fi / Hotspot:   http://{lan_ip}:{port}")
    print(f"  * USB Cable:         http://127.0.0.1:{port} (run connect_phone_usb.bat)")
    print(f"  * Android Emulator:  http://10.0.2.2:{port}")
    print("  [NOTE] Do NOT use WSL virtual adapter (e.g. 172.19.x.x)!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print_network_help(port)
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
