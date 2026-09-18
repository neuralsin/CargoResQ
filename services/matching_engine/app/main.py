"""
Matching Engine Microservice API.
Coordinates geospatial candidate lookup, road-aware routing, Rescue Scoring,
and ETA-to-spoilage margins.
"""
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .repository import find_candidates
from .scoring import calculate_rescue_score, RescueScore
from .spoilage import classify_safety_margin, SafetyMargin
from .routing import get_road_eta
from shared.observability import MATCH_LATENCY, metrics_endpoint_response
import time
import os

DATABASE_URL = os.getenv("CORE_DATABASE_URL", "sqlite+aiosqlite:///cargoresq.db")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

app = FastAPI(
    title="CargoResQ Matching Engine",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


async def get_db():
    async with async_session() as session:
        yield session


class MatchRequest(BaseModel):
    lat: float
    lng: float
    requiresRefrigeration: bool = False
    requiredMaxTemp: Optional[float] = None
    isHazmat: bool = False
    volumeM3: float = Field(..., gt=0)
    weightKg: float = Field(..., gt=0)
    minutesUntilSpoilage: Optional[float] = None
    radiusKm: float = 50.0
    limit: int = 5


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "matching-engine", "version": "1.0.0"}


@app.get("/metrics")
async def metrics():
    return metrics_endpoint_response()


@app.post("/api/v1/matching/candidates")
async def get_ranked_candidates(req: MatchRequest, db: AsyncSession = Depends(get_db)):
    t0 = time.perf_counter()

    shipment_spec = req.model_dump()
    raw_candidates = await find_candidates(
        session=db,
        lat=req.lat,
        lng=req.lng,
        shipment=shipment_spec,
        radius_km=req.radiusKm,
        limit=req.limit,
    )

    enriched_candidates = []
    for c in raw_candidates:
        # Route-aware road distance & ETA
        truck_loc = (c.get("latitude", req.lat + 0.05), c.get("longitude", req.lng + 0.05))
        road_info = await get_road_eta(truck_loc, (req.lat, req.lng))

        eta_mins = road_info["etaMinutes"]
        dist_km = road_info["distanceKm"]

        # Rescue Scoring (Phase 13)
        candidate_dict = {
            "truckId": c.get("id"),
            "companyId": c.get("company_id"),
            "etaMinutes": eta_mins,
            "maxAcceptableEtaMinutes": 60.0,
            "fullyCompatible": True,
            "requiredVolumeM3": req.volumeM3,
            "availableVolumeM3": c.get("max_volume_m3", req.volumeM3 * 1.5),
            "companyTrustScore": 95.0,
            "historicalAcceptanceRate": 0.95,
            "routeAlignmentScore": 0.8,
        }
        score: RescueScore = calculate_rescue_score(candidate_dict)

        # Spoilage margin if perishable (Phase 14)
        margin_info: Optional[SafetyMargin] = None
        if req.minutesUntilSpoilage is not None:
            margin_info = classify_safety_margin(eta_mins, req.minutesUntilSpoilage)

        enriched_candidates.append({
            "truckId": c.get("id"),
            "companyId": c.get("company_id"),
            "distanceKm": dist_km,
            "etaMinutes": eta_mins,
            "isRoadAware": road_info["isRoadAware"],
            "score": score.total,
            "reasons": score.reasons,
            "spoilageMargin": margin_info.to_dict() if margin_info else None,
        })

    enriched_candidates.sort(key=lambda x: x["score"], reverse=True)
    elapsed = time.perf_counter() - t0
    MATCH_LATENCY.observe(elapsed)

    return {
        "candidateCount": len(enriched_candidates),
        "durationMs": round(elapsed * 1000, 2),
        "candidates": enriched_candidates,
    }
