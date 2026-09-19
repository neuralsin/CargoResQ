"""
Rescue offer API: the two-way handshake.

Two things are load-bearing here.

First, a rescue binds only when both sides have said yes. There is no endpoint
that binds a rescue on one party's say-so.

Second, price visibility. The owner and the carrier get *different response
models* -- not one model with fields stripped out conditionally. A carrier
cannot be shown the owner's total or the platform's margin, because the model
used to build their response has no field to put them in. Getting that wrong
would require adding a field to the wrong class, which is visible in review;
forgetting a conditional filter is not.

The carrier does see their own payout. A carrier cannot consent to a job whose
compensation is hidden from them, and a handshake where one side is blind is
not a handshake.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.auth import actor_id_of, get_current_principal
from services.core_api.app.events.producer import event_producer
from services.core_api.app.models import Company, Shipment, Truck
from services.telemetry.app.models import TruckLiveState
from services.matching_engine.app.repository import find_candidates
from services.matching_engine.app.routing import get_road_eta, haversine_distance_km
from services.matching_engine.app.scoring import calculate_rescue_score
from services.pricing_engine.app.pricing import calculate_price
from shared.database import get_db
from shared.observability import logger

from .authz import load_incident_for_owner
from .models import Incident, IncidentState
from .offer_models import DEFAULT_OFFER_TTL_SECONDS, OfferState, RescueOffer
from .offer_service import (
    MAX_OFFERS_PER_ROUND,
    OfferError,
    cancel_bound_offer,
    carrier_accept,
    carrier_decline,
    create_offer_round,
    owner_confirm,
    owner_withdraw,
)
from .reputation import acceptance_rates_for, get_reputation, trust_scores_for

router = APIRouter(prefix="/api/v1", tags=["rescue-offers"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class FanOutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    radius_km: float = Field(50.0, gt=0, le=500)
    max_candidates: int = Field(5, ge=1, le=MAX_OFFERS_PER_ROUND)
    ttl_seconds: int = Field(DEFAULT_OFFER_TTL_SECONDS, ge=60, le=3600)


class ReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(None, max_length=256)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Required, unlike decline. Abandoning a rescue the owner is relying on
    #: needs a stated reason -- it goes on the record and into reputation.
    reason: str = Field(..., min_length=3, max_length=256)


# ---------------------------------------------------------------------------
# Response projections -- separate classes, by design
# ---------------------------------------------------------------------------


def _common_offer_fields(offer: RescueOffer) -> Dict[str, Any]:
    """Facts about the job itself, safe for either party."""
    return {
        "id": offer.id,
        "incidentId": offer.incident_id,
        "state": offer.state,
        "offerRound": offer.offer_round,
        "etaMinutes": offer.eta_minutes,
        "distanceKm": offer.distance_km,
        "expiresAt": offer.expires_at.isoformat() if offer.expires_at else None,
        "carrierAccepted": offer.carrier_accepted_at is not None,
        "ownerConfirmed": offer.owner_confirmed_at is not None,
        "bound": offer.is_bound,
        "createdAt": offer.created_at.isoformat() if offer.created_at else None,
    }


def owner_view(offer: RescueOffer, reputation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the cargo owner sees: the full price, and who they are trusting."""
    payload = _common_offer_fields(offer)
    payload.update(
        {
            "carrierCompanyId": offer.carrier_company_id,
            "carrierTruckId": offer.carrier_truck_id,
            "priceTotalInr": offer.price_total_inr,
            "carrierPayoutInr": offer.carrier_payout_inr,
            "platformFeeInr": offer.platform_fee_inr,
            "priceBreakdown": offer.price_breakdown_json,
            "rescueScore": offer.rescue_score,
            "scoreReasons": offer.score_reasons_json,
            "escrowId": offer.escrow_id,
            # The reputation of the company being accepted, shown at the
            # moment of the decision rather than buried on another screen.
            "carrierReputation": reputation,
        }
    )
    return payload


def carrier_view(offer: RescueOffer) -> Dict[str, Any]:
    """What the helping carrier sees.

    Their payout, the job's physical parameters, and nothing about what the
    cargo is worth to its owner or what the platform takes.
    """
    payload = _common_offer_fields(offer)
    payload.update(
        {
            "ownerCompanyId": offer.owner_company_id,
            "truckId": offer.carrier_truck_id,
            "payoutInr": offer.carrier_payout_inr,
            "currency": offer.currency,
        }
    )
    return payload


def driver_view(offer: RescueOffer) -> Dict[str, Any]:
    """What the assigned driver sees: where to go, and when."""
    payload = _common_offer_fields(offer)
    payload.update({"truckId": offer.carrier_truck_id})
    return payload



async def enriched_owner_view(db: AsyncSession, offer: RescueOffer) -> Dict[str, Any]:
    """The owner's offer view, plus who and what they would be accepting.

    An offer identified only by two opaque ids is not a decision anybody can
    make. The owner is choosing between companies and between trucks, so the
    company's name and the truck's actual specification travel with the offer
    rather than having to be looked up one screen at a time.
    """
    reputation = await get_reputation(db, offer.carrier_company_id)
    payload = owner_view(offer, reputation.to_public_dict() if reputation else None)

    company = await db.get(Company, offer.carrier_company_id)
    payload["carrierCompanyName"] = company.name if company else "Unknown carrier"

    truck = await db.get(Truck, offer.carrier_truck_id) if offer.carrier_truck_id else None
    payload["carrierTruck"] = (
        {
            "id": truck.id,
            "registrationNumber": truck.registration_number,
            "status": truck.status.value,
            "refrigerated": truck.refrigerated,
            "minTempC": truck.min_temp_c,
            "maxVolumeM3": truck.max_volume_m3,
            "maxWeightKg": truck.max_weight_kg,
            "latitude": truck.latitude,
            "longitude": truck.longitude,
        }
        if truck
        else None
    )
    return payload


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


async def _load_offer_for_party(
    offer_id: str, principal: Dict[str, Any], db: AsyncSession
) -> tuple[RescueOffer, str]:
    """Return (offer, 'owner' | 'carrier' | 'driver').

    Anyone else gets 404: confirming that an offer id exists tells a stranger
    that a rescue is in progress and who is involved.
    """
    offer = await db.get(RescueOffer, offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")

    company_id = principal.get("company_id")
    is_driver = principal.get("principal_type") == "driver"

    if company_id == offer.owner_company_id and not is_driver:
        return offer, "owner"
    if company_id == offer.carrier_company_id:
        if is_driver:
            if offer.carrier_driver_id and offer.carrier_driver_id == principal.get(
                "principal_id"
            ):
                return offer, "driver"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found"
            )
        return offer, "carrier"

    logger.warn("offer_access_denied", offer_id=offer_id, company_id=company_id)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")


def _offer_error(exc: OfferError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.code, "message": str(exc), **exc.context},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/incidents/{id}/offers", status_code=status.HTTP_201_CREATED)
async def fan_out_offers(
    id: str,
    req: FanOutRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Offer the rescue to the best compatible carriers, on one deadline."""
    incident, shipment = await load_incident_for_owner(id, principal, db)

    raw = await find_candidates(
        session=db,
        lat=incident.lat,
        lng=incident.lng,
        requires_refrigeration=shipment.requires_refrigeration,
        required_max_temp=shipment.required_max_temp_c,
        is_hazmat=shipment.is_hazmat,
        volume_m3=shipment.volume_m3,
        weight_kg=shipment.weight_kg,
        radius_km=req.radius_km,
        limit=req.max_candidates,
        exclude_truck_ids=[shipment.truck_id] if shipment.truck_id else None,
    )
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "no_candidates",
                "message": (
                    "No compatible truck is available nearby. The system will not "
                    "force an incompatible match onto this cargo."
                ),
            },
        )

    company_ids = [c["companyId"] for c in raw]
    trust_scores = await trust_scores_for(db, company_ids)
    acceptance = await acceptance_rates_for(db, company_ids)

    candidates: List[Dict[str, Any]] = []
    for candidate in raw:
        road = await get_road_eta(
            (candidate["latitude"], candidate["longitude"]), (incident.lat, incident.lng)
        )
        trust = trust_scores.get(candidate["companyId"], 75.0)
        score = calculate_rescue_score(
            {
                "etaMinutes": road["etaMinutes"],
                "maxAcceptableEtaMinutes": 90.0,
                "fullyCompatible": True,
                "requiredVolumeM3": shipment.volume_m3,
                "availableVolumeM3": candidate["availableVolumeM3"],
                "companyTrustScore": trust,
                "historicalAcceptanceRate": acceptance.get(candidate["companyId"], 0.5),
                "routeAlignmentScore": 0.5,
                "positionIsLive": candidate["positionIsLive"],
            }
        )
        quote = calculate_price(
            distance_km=road["distanceKm"],
            requires_refrigeration=shipment.requires_refrigeration,
            is_hazmat=shipment.is_hazmat,
            weight_kg=shipment.weight_kg,
            volume_m3=shipment.volume_m3,
            minutes_until_spoilage=incident.minutes_until_spoilage,
            num_compatible_nearby=len(raw),
            cargo_value_inr=shipment.value_inr,
            carrier_trust_score=trust,
        )
        candidates.append(
            {
                "truckId": candidate["truckId"],
                "companyId": candidate["companyId"],
                "etaMinutes": road["etaMinutes"],
                "distanceKm": road["distanceKm"],
                "score": score.total,
                "reasons": score.reasons,
                "quote": quote,
            }
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)

    try:
        offers = await create_offer_round(
            session=db,
            incident=incident,
            shipment=shipment,
            candidates=candidates,
            actor_id=actor_id_of(principal),
            ttl_seconds=req.ttl_seconds,
            event_publisher=event_producer.publish,
        )
    except OfferError as exc:
        raise _offer_error(exc)

    views = [await enriched_owner_view(db, offer) for offer in offers]
    return {"offerRound": offers[0].offer_round, "offers": views}


@router.get("/incidents/{id}/offers")
async def list_incident_offers(
    id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """The owner's view of every offer on their incident."""
    await load_incident_for_owner(id, principal, db)
    result = await db.execute(
        select(RescueOffer)
        .where(RescueOffer.incident_id == id)
        .order_by(RescueOffer.offer_round.desc(), RescueOffer.rescue_score.desc())
    )
    offers = list(result.scalars().all())
    return [await enriched_owner_view(db, offer) for offer in offers]


@router.get("/offers/inbox")
async def carrier_inbox(
    state: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Offers waiting for this carrier -- or for this driver's truck."""
    company_id = principal.get("company_id")
    stmt = select(RescueOffer).where(RescueOffer.carrier_company_id == company_id)

    is_driver = principal.get("principal_type") == "driver"
    if is_driver:
        stmt = stmt.where(RescueOffer.carrier_driver_id == principal.get("principal_id"))
    if state:
        stmt = stmt.where(RescueOffer.state == state)

    stmt = stmt.order_by(RescueOffer.created_at.desc()).limit(limit)
    offers = list((await db.execute(stmt)).scalars().all())
    project = driver_view if is_driver else carrier_view
    return [project(offer) for offer in offers]


@router.get("/offers/{offer_id}")
async def get_offer(
    offer_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    offer, role = await _load_offer_for_party(offer_id, principal, db)
    if role == "owner":
        return await enriched_owner_view(db, offer)
    if role == "driver":
        return driver_view(offer)
    return carrier_view(offer)


@router.post("/offers/{offer_id}/carrier-accept")
async def accept_as_carrier(
    offer_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """The helping carrier agrees. Binds only if the owner has also agreed."""
    offer, role = await _load_offer_for_party(offer_id, principal, db)
    if role not in {"carrier", "driver"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the offered carrier may accept",
        )
    try:
        offer, bound = await carrier_accept(
            db, offer, actor_id_of(principal), event_producer.publish
        )
    except OfferError as exc:
        raise _offer_error(exc)
    return {"bound": bound, "offer": carrier_view(offer)}


@router.post("/offers/{offer_id}/carrier-decline")
async def decline_as_carrier(
    offer_id: str,
    req: ReasonRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    offer, role = await _load_offer_for_party(offer_id, principal, db)
    if role not in {"carrier", "driver"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the offered carrier may decline",
        )
    try:
        offer = await carrier_decline(db, offer, req.reason, event_producer.publish)
    except OfferError as exc:
        raise _offer_error(exc)
    return carrier_view(offer)


@router.post("/offers/{offer_id}/owner-confirm")
async def confirm_as_owner(
    offer_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """The stranded owner picks this carrier. Binds if the carrier has agreed."""
    offer, role = await _load_offer_for_party(offer_id, principal, db)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the cargo owner may confirm an offer",
        )
    try:
        offer, bound = await owner_confirm(
            db, offer, actor_id_of(principal), event_producer.publish
        )
    except OfferError as exc:
        raise _offer_error(exc)
    return {"bound": bound, "offer": await enriched_owner_view(db, offer)}


@router.post("/offers/{offer_id}/owner-withdraw")
async def withdraw_as_owner(
    offer_id: str,
    req: ReasonRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    offer, role = await _load_offer_for_party(offer_id, principal, db)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the cargo owner may withdraw an offer",
        )
    try:
        offer = await owner_withdraw(db, offer, req.reason, event_producer.publish)
    except OfferError as exc:
        raise _offer_error(exc)
    return owner_view(offer)


@router.post("/offers/{offer_id}/cancel")
async def cancel_rescue(
    offer_id: str,
    req: CancelRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Unwind a bound rescue. Either party may do this, and it is recorded."""
    offer, role = await _load_offer_for_party(offer_id, principal, db)
    if role not in {"owner", "carrier"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a party to the rescue may cancel it",
        )
    try:
        offer = await cancel_bound_offer(
            db, offer, req.reason, actor_id_of(principal), event_producer.publish
        )
    except OfferError as exc:
        raise _offer_error(exc)
    return owner_view(offer) if role == "owner" else carrier_view(offer)


@router.get("/companies/{company_id}/reputation")
async def company_reputation(
    company_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """A company's track record, computed from recorded behaviour.

    Returns 404 for a company that does not exist, rather than inventing a
    plausible-looking scorecard for it.
    """
    company = await db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    reputation = await get_reputation(db, company_id)
    if reputation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    payload = reputation.to_public_dict()
    payload["companyName"] = company.name
    return payload


@router.get("/rescues/active")
async def active_rescues(
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Rescues this company is currently performing for somebody else.

    The owner's console follows a rescue through their own incident. The
    rescuing carrier had no equivalent: their side of a bound rescue appeared
    as one line in an offer inbox, with no indication of where the stranded
    truck was, how far their own truck still had to go, or what state the job
    was in. Both halves of a two-party job need a live view of it.

    Carrier projection throughout -- payout, never the owner's price, and the
    cargo without its declared value.
    """
    company_id = principal.get("company_id")

    result = await db.execute(
        select(RescueOffer, Shipment, Incident)
        .join(Shipment, Shipment.id == RescueOffer.shipment_id)
        .join(Incident, Incident.id == RescueOffer.incident_id)
        .where(RescueOffer.carrier_company_id == company_id)
        .where(RescueOffer.state == OfferState.BOUND.value)
        .where(
            Incident.state.not_in(
                [
                    IncidentState.CANCELLED.value,
                    IncidentState.ESCROW_RELEASED.value,
                    IncidentState.DISPUTED.value,
                ]
            )
        )
        .order_by(RescueOffer.bound_at.desc())
    )

    rescues: List[Dict[str, Any]] = []
    for offer, shipment, incident in result.all():
        our_truck = (
            await db.get(Truck, offer.carrier_truck_id)
            if offer.carrier_truck_id
            else None
        )
        their_truck = (
            await db.get(Truck, shipment.truck_id) if shipment.truck_id else None
        )
        owner = await db.get(Company, offer.owner_company_id)

        ours = await _live_position(db, our_truck) if our_truck else None
        theirs = await _live_position(db, their_truck) if their_truck else None

        distance_km = None
        if ours and theirs and None not in (
            ours["latitude"], ours["longitude"],
            theirs["latitude"], theirs["longitude"],
        ):
            distance_km = round(
                haversine_distance_km(
                    ours["latitude"], ours["longitude"],
                    theirs["latitude"], theirs["longitude"],
                ),
                2,
            )

        rescues.append(
            {
                "offerId": offer.id,
                "incidentId": incident.id,
                "incidentState": incident.state.value,
                "customerCompany": owner.name if owner else "Customer",
                "payoutInr": offer.carrier_payout_inr,
                "etaMinutes": offer.eta_minutes,
                "ourTruck": (
                    {
                        "id": our_truck.id,
                        "registrationNumber": our_truck.registration_number,
                        **(ours or {}),
                    }
                    if our_truck
                    else None
                ),
                "strandedTruck": (
                    {
                        "id": their_truck.id,
                        "registrationNumber": their_truck.registration_number,
                        **(theirs or {}),
                    }
                    if their_truck
                    else None
                ),
                "breakdownLat": incident.lat,
                "breakdownLng": incident.lng,
                "distanceKm": distance_km,
                "minutesUntilSpoilage": incident.minutes_until_spoilage,
                # What is being moved, not what it is worth.
                "cargo": {
                    "type": shipment.cargo_type,
                    "requiresRefrigeration": shipment.requires_refrigeration,
                    "requiredMaxTempC": shipment.required_max_temp_c,
                    "isHazmat": shipment.is_hazmat,
                    "volumeM3": shipment.volume_m3,
                    "weightKg": shipment.weight_kg,
                },
                "relayFacilityId": incident.relay_facility_id,
            }
        )

    return rescues


async def _live_position(db: AsyncSession, truck: Truck) -> Dict[str, Any]:
    """A truck's current position, honest about how fresh it is."""
    live = await db.get(TruckLiveState, truck.id)
    if live is not None and live.latitude is not None:
        return {
            "latitude": live.latitude,
            "longitude": live.longitude,
            "speedKph": live.speed_kph,
            "positionIsLive": _position_is_live(live.last_received_at),
            "lastSeenAt": (
                live.last_received_at.isoformat() if live.last_received_at else None
            ),
        }
    return {
        "latitude": truck.latitude,
        "longitude": truck.longitude,
        "speedKph": None,
        "positionIsLive": False,
        "lastSeenAt": None,
    }


def _position_is_live(seen_at) -> bool:
    if seen_at is None:
        return False
    seen = seen_at if seen_at.tzinfo else seen_at.replace(tzinfo=timezone.utc)
    return seen >= datetime.now(timezone.utc) - timedelta(minutes=10)
