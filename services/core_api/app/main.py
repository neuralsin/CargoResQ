"""
CargoResQ Core API Service (Phase 1).
Main application wiring domain repositories, auth, event publishing, and simulation.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from .database import init_db
from .events.producer import event_producer
from .api.breakdowns import limiter, router as breakdowns_router
from .api.auth import router as auth_router
from .api.companies import router as companies_router
from .api.trucks import router as trucks_router
from .api.shipments import router as shipments_router
from .api.simulation import router as simulation_router
from .api.driver import router as driver_router
from shared.observability import configure_logging, metrics_endpoint_response

configure_logging()

app = FastAPI(
    title="CargoResQ Core API",
    description="Cross-Carrier Emergency Cargo Rescue Platform - Core Domain & Orchestration API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Rate Limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Wire Routers (Phase 18.3: all prefixed with /api/v1/)
app.include_router(auth_router)
app.include_router(companies_router)
app.include_router(trucks_router)
app.include_router(shipments_router)
app.include_router(breakdowns_router)
app.include_router(simulation_router)
app.include_router(driver_router)


@app.on_event("startup")
async def startup():
    await init_db()
    await event_producer.start()


@app.on_event("shutdown")
async def shutdown():
    await event_producer.stop()


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "core-api", "version": "1.0.0"}


@app.get("/metrics")
async def metrics():
    return metrics_endpoint_response()
