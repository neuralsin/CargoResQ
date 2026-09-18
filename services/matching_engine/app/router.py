"""
Matching API.

Ranks compatible spare capacity near a breakdown. Every input to the rescue
score is now computed or looked up: previously `fullyCompatible`, the
company's trust score, its historical acceptance rate and its route alignment
were hardcoded constants, and between them they carried half the weight of the
formula. Every truck therefore scored almost identically and printed the same
explanation -- "95% successful rescues" -- regardless of history.

The Dijkstra showpiece endpoints are gone. Routing is not a feature to display;
it is how an ETA is obtained, so it lives inside candidate ranking.
"""
import math
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.auth import get_current_principal
from services.core_api.app.models import Shipment
from services.orchestrator.app.reputation import (
    acceptance_rates_for,
    trust_scores_for,
)
from services.pricing_engine.app.pricing import calculate_price
from shared.database import get_db
from shared.observability import MATCH_LATENCY

from .repository import explain_no_match, find_candidates
from .routing import get_road_eta
from .scoring import calculate_rescue_score
from .spoilage import classify_safety_margin

router = APIRouter(prefix="/api/v1/matching", tags=["matching"])

#: Beyond this an arrival is not a rescue, so ETA scores zero.
MAX_ACCEPTABLE_ETA_MINUTES = 90.0


class MatchRequest(BaseModel):
    """Search parameters.

    Cargo requirements are taken from the shipment when one is given, so a
    caller cannot understate a load to widen its own match set.
    """

    shipment_id: Optional[str] = None
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    requiresRefrigeration: bool = False
    requiredMaxTemp: Optional[float] = None
    isHazmat: bool = False
    volumeM3: float = Field(1.0, gt=0)
    weightKg: float = Field(100.0, gt=0)
    minutesUntilSpoilage: Optional[float] = Field(None, ge=0)
    cargoValueInr: Optional[float] = Field(None, ge=0)
    radiusKm: float = Field(50.0, gt=0, le=500)
    limit: int = Field(5, ge=1, le=8)


def _bearing_deg(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    """Initial compass bearing from one point to another."""
    lat1, lat2 = math.radians(from_lat), math.radians(to_lat)
    dlng = math.radians(to_lng - from_lng)
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _route_alignment(
    candidate_heading: Optional[float],
    truck_lat: float,
    truck_lng: float,
    destination_lat: Optional[float],
    destination_lng: Optional[float],
) -> float:
    """How well a candidate is already pointed at the cargo's destination.

    Returns 0..1. A truck already travelling that way carries the load onward
    with far less disruption than one that has to turn around.

    Without a heading or a destination this returns 0.5 -- genuinely unknown,
    not the flattering 0.8 that used to be hardcoded for every candidate.
    """
    if candidate_heading is None or destination_lat is None or destination_lng is None:
        return 0.5

    desired = _bearing_deg(truck_lat, truck_lng, destination_lat, destination_lng)
    difference = abs((candidate_heading - desired + 180.0) % 360.0 - 180.0)
    return max(0.0, 1.0 - (difference / 180.0))


@router.post("/candidates")
async def get_ranked_candidates(
    req: MatchRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Rank compatible rescuers, with a real price for each."""
    started = time.perf_counter()

    requires_refrigeration = req.requiresRefrigeration
    required_max_temp = req.requiredMaxTemp
    is_hazmat = req.isHazmat
    volume_m3 = req.volumeM3
    weight_kg = req.weightKg
    cargo_value = req.cargoValueInr
    destination_lat = destination_lng = None
    exclude: List[str] = []

    if req.shipment_id:
        shipment = await db.get(Shipment, req.shipment_id)
        if not shipment or shipment.owner_company_id != principal.get("company_id"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shipment not found or does not belong to your company",
            )
        requires_refrigeration = shipment.requires_refrigeration
        required_max_temp = shipment.required_max_temp_c
        is_hazmat = shipment.is_hazmat
        volume_m3 = shipment.volume_m3
        weight_kg = shipment.weight_kg
        cargo_value = shipment.value_inr
        destination_lat = shipment.destination_lat
        destination_lng = shipment.destination_lng
        # The stranded truck cannot rescue itself.
        if shipment.truck_id:
            exclude.append(shipment.truck_id)

    raw = await find_candidates(
        session=db,
        lat=req.lat,
        lng=req.lng,
        requires_refrigeration=requires_refrigeration,
        required_max_temp=required_max_temp,
        is_hazmat=is_hazmat,
        volume_m3=volume_m3,
        weight_kg=weight_kg,
        radius_km=req.radiusKm,
        limit=req.limit,
        exclude_truck_ids=exclude,
    )

    if not raw:
        elapsed = time.perf_counter() - started
        MATCH_LATENCY.observe(elapsed)
        # Refusing to force an incompatible match is the correct outcome, and
        # the explanation is what makes it actionable.
        return {
            "candidateCount": 0,
            "durationMs": round(elapsed * 1000, 2),
            "candidates": [],
            "noMatchExplanation": await explain_no_match(
                db,
                req.lat,
                req.lng,
                requires_refrigeration,
                required_max_temp,
                is_hazmat,
                volume_m3,
                weight_kg,
                req.radiusKm,
            ),
        }

    company_ids = [c["companyId"] for c in raw]
    trust_scores = await trust_scores_for(db, company_ids)
    acceptance_rates = await acceptance_rates_for(db, company_ids)

    enriched: List[Dict[str, Any]] = []
    for candidate in raw:
        road = await get_road_eta(
            (candidate["latitude"], candidate["longitude"]), (req.lat, req.lng)
        )
        eta_minutes = road["etaMinutes"]
        distance_km = road["distanceKm"]

        alignment = _route_alignment(
            candidate.get("headingDeg"),
            candidate["latitude"],
            candidate["longitude"],
            destination_lat,
            destination_lng,
        )
        trust = trust_scores.get(candidate["companyId"], 75.0)

        score = calculate_rescue_score(
            {
                "etaMinutes": eta_minutes,
                "maxAcceptableEtaMinutes": MAX_ACCEPTABLE_ETA_MINUTES,
                # Every candidate reaching this point passed the compatibility
                # filter in the repository, so this is now a fact rather than
                # an assumption -- incompatible trucks were never returned.
                "fullyCompatible": True,
                "requiredVolumeM3": volume_m3,
                "availableVolumeM3": candidate["availableVolumeM3"],
                "companyTrustScore": trust,
                "historicalAcceptanceRate": acceptance_rates.get(
                    candidate["companyId"], 0.5
                ),
                "routeAlignmentScore": alignment,
                "positionIsLive": candidate["positionIsLive"],
            }
        )

        quote = calculate_price(
            distance_km=distance_km,
            requires_refrigeration=requires_refrigeration,
            is_hazmat=is_hazmat,
            weight_kg=weight_kg,
            volume_m3=volume_m3,
            minutes_until_spoilage=req.minutesUntilSpoilage,
            num_compatible_nearby=len(raw),
            cargo_value_inr=cargo_value,
            carrier_trust_score=trust,
        )

        margin = (
            classify_safety_margin(eta_minutes, req.minutesUntilSpoilage)
            if req.minutesUntilSpoilage is not None
            else None
        )

        enriched.append(
            {
                "truckId": candidate["truckId"],
                "companyId": candidate["companyId"],
                "registrationNumber": candidate["registrationNumber"],
                "distanceKm": distance_km,
                "etaMinutes": eta_minutes,
                "isRoadAware": road["isRoadAware"],
                "positionIsLive": candidate["positionIsLive"],
                "lastSeenAt": candidate["lastSeenAt"],
                "score": score.total,
                "reasons": score.reasons,
                "trustScore": trust,
                "spoilageMargin": margin.to_dict() if margin else None,
                # The owner is asking, so the owner's price is what comes back.
                "price": quote.owner_view(),
                "_quote": quote,
            }
        )

    enriched.sort(key=lambda c: c["score"], reverse=True)
    elapsed = time.perf_counter() - started
    MATCH_LATENCY.observe(elapsed)

    for item in enriched:
        item.pop("_quote", None)

    return {
        "candidateCount": len(enriched),
        "durationMs": round(elapsed * 1000, 2),
        "candidates": enriched,
    }
