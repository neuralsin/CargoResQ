"""
Relay and split API.

Two answers to the same question -- "this load cannot be carried the way it
was going to be" -- that the console needs before an operator can choose.

Both are read-only until the operator commits. Asking what the options are
must never change anything, because the first thing anybody does in a crisis
is look.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.auth import actor_id_of, get_current_principal
from services.core_api.app.models import Shipment, StorageFacility
from services.matching_engine.app.repository import find_candidates
from services.matching_engine.app.routing import haversine_distance_km
from services.matching_engine.app.split import describe_shortfall, plan_split
from shared.database import get_db
from shared.observability import logger

from .authz import load_incident_for_owner
from .relay import assess_facility, commit_relay, relay_options

router = APIRouter(prefix="/api/v1", tags=["relay"])


class RelayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: str
    reason: Optional[str] = Field(None, max_length=256)


def _facility_view(
    facility: StorageFacility, distance_km: Optional[float] = None
) -> Dict[str, Any]:
    return {
        "id": facility.id,
        "name": facility.name,
        "operatorCompanyId": facility.operator_company_id,
        "latitude": facility.latitude,
        "longitude": facility.longitude,
        "address": facility.address,
        "contactPhone": facility.contact_phone,
        "refrigerated": facility.refrigerated,
        "minTempC": facility.min_temp_c,
        "maxTempC": facility.max_temp_c,
        "hazmatApproved": facility.hazmat_approved,
        "capacityM3": facility.capacity_m3,
        "availableM3": facility.available_m3,
        "handlingFeeInr": facility.handling_fee_inr,
        "storageFeeInrPerM3Day": facility.storage_fee_inr_per_m3_day,
        "open24h": facility.open_24h,
        "distanceKm": round(distance_km, 1) if distance_km is not None else None,
    }


@router.get("/storage/facilities")
async def list_facilities(
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lng: Optional[float] = Query(None, ge=-180, le=180),
    _principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Every safe-storage site on the network.

    Shared infrastructure, so it is not scoped to the caller's company -- the
    point of a relay is that somebody else's chiller will take your pallets.
    """
    result = await db.execute(
        select(StorageFacility).where(StorageFacility.active.is_(True))
    )
    facilities = list(result.scalars().all())

    views = []
    for facility in facilities:
        distance = (
            haversine_distance_km(lat, lng, facility.latitude, facility.longitude)
            if lat is not None and lng is not None
            else None
        )
        views.append(_facility_view(facility, distance))

    if lat is not None and lng is not None:
        views.sort(key=lambda f: f["distanceKm"])
    return views


@router.get("/incidents/{id}/relay-options")
async def get_relay_options(
    id: str,
    radius_km: float = Query(120.0, gt=0, le=500),
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Where this cargo could be taken instead of finishing its journey."""
    incident, shipment = await load_incident_for_owner(id, principal, db)
    options = await relay_options(db, incident, shipment, radius_km=radius_km)
    reachable = [o for o in options if o["suitable"] and o["arrivesInTime"] is not False]
    return {
        "incidentId": incident.id,
        "minutesUntilSpoilage": incident.minutes_until_spoilage,
        "currentRelayFacilityId": incident.relay_facility_id,
        "optionCount": len(options),
        "reachableCount": len(reachable),
        "options": options,
    }


@router.post("/incidents/{id}/relay")
async def relay_to_storage(
    id: str,
    req: RelayRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Redirect the cargo to a storage facility.

    The rescue itself is unchanged -- a truck still collects the load and the
    escrow still settles on delivery. Only where it is being delivered moves.
    """
    incident, shipment = await load_incident_for_owner(id, principal, db)

    facility = await db.get(StorageFacility, req.facility_id)
    if facility is None or not facility.active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Storage facility not found"
        )

    try:
        result = await commit_relay(
            db,
            incident,
            facility,
            shipment,
            actor_id=actor_id_of(principal),
            reason=req.reason or "",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That facility cannot hold this cargo: {exc}",
        )
    return result


@router.get("/incidents/{id}/split-plan")
async def get_split_plan(
    id: str,
    radius_km: float = Query(60.0, gt=0, le=500),
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """How this load could be divided when no single truck can take it.

    Always reports whether a single truck would have done, so the console can
    say "a split is not needed" rather than silently offering a worse plan.
    """
    incident, shipment = await load_incident_for_owner(id, principal, db)

    volume = shipment.volume_m3 or 0.0
    weight = shipment.weight_kg or 0.0

    # The stranded truck is not a candidate to rescue itself. It is idle,
    # compatible and zero kilometres away, so without this it wins every
    # ranking and the plan comes back saying the load should be transferred
    # into the vehicle it is already sitting in.
    exclude = [shipment.truck_id] if shipment.truck_id else None

    whole_load = await find_candidates(
        db,
        lat=incident.lat,
        lng=incident.lng,
        requires_refrigeration=shipment.requires_refrigeration,
        required_max_temp=shipment.required_max_temp_c,
        is_hazmat=shipment.is_hazmat,
        volume_m3=volume,
        weight_kg=weight,
        radius_km=radius_km,
        limit=5,
        exclude_truck_ids=exclude,
    )

    partial = await find_candidates(
        db,
        lat=incident.lat,
        lng=incident.lng,
        requires_refrigeration=shipment.requires_refrigeration,
        required_max_temp=shipment.required_max_temp_c,
        is_hazmat=shipment.is_hazmat,
        volume_m3=volume,
        weight_kg=weight,
        radius_km=radius_km,
        limit=12,
        exclude_truck_ids=exclude,
        ignore_capacity=True,
    )

    plan = plan_split(partial, volume, weight)

    payload: Dict[str, Any] = {
        "incidentId": incident.id,
        "cargoType": shipment.cargo_type,
        "volumeM3": volume,
        "weightKg": weight,
        "singleTruckAvailable": bool(whole_load),
        "singleTruckCount": len(whole_load),
        "splitRequired": not whole_load,
        "plan": plan,
    }
    if whole_load:
        payload["message"] = (
            f"{len(whole_load)} truck(s) can take the whole load, so a split is "
            "not needed."
            + (
                f" If that changes it would divide across {plan['truckCount']} trucks."
                if plan
                else ""
            )
        )
    elif plan is not None:
        payload["message"] = (
            f"No single truck fits. {plan['truckCount']} trucks can carry it "
            "between them."
        )
    else:
        payload["shortfall"] = describe_shortfall(partial, volume, weight)
        payload["message"] = (
            "No single truck fits, and no combination of nearby trucks covers "
            "the load either. Relaying it to safe storage is the remaining "
            "option."
        )

    logger.info(
        "split_plan_requested",
        incident_id=incident.id,
        single_truck=bool(whole_load),
        split_possible=plan is not None,
    )
    return payload
