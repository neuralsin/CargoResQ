"""
Matching Engine APIRouter.
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import time
from .repository import find_candidates
from .scoring import calculate_rescue_score, RescueScore
from .spoilage import classify_safety_margin, SafetyMargin
from .routing import get_road_eta
from shared.database import get_db
from shared.observability import MATCH_LATENCY

router = APIRouter(prefix="/api/v1/matching", tags=["matching"])


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


@router.post("/candidates")
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
        truck_loc = (c.get("latitude", req.lat + 0.05), c.get("longitude", req.lng + 0.05))
        road_info = await get_road_eta(truck_loc, (req.lat, req.lng))

        eta_mins = road_info["etaMinutes"]
        dist_km = road_info["distanceKm"]

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


from .dijkstra import dijkstra_fastest_route, CORRIDOR_NODES


class DijkstraRouteRequest(BaseModel):
    startNode: str = Field("SF_ORIGIN", description="Start node code (e.g. SF_ORIGIN, PUN_DEP, VLR_BYP)")
    endNode: str = Field("SF_DEST", description="End node code (e.g. SF_DEST, BLR_HUB, KNC_EST)")


@router.post("/dijkstra")
async def calculate_dijkstra_route(req: DijkstraRouteRequest):
    """Computes fastest route using Dijkstra's algorithm with live congestion factor."""
    try:
        route = dijkstra_fastest_route(req.startNode, req.endNode)
        return route
    except ValueError as e:
        return {"error": str(e), "status": "failed"}


@router.get("/corridor/nodes")
async def list_corridor_nodes():
    """Lists all active logistics hubs and waypoints available for routing."""
    return [
        {"nodeId": k, "lat": v[0], "lng": v[1], "name": v[2]}
        for k, v in CORRIDOR_NODES.items()
    ]
