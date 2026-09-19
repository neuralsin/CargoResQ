"""
Cargo relay: getting the load somewhere safe when the journey is over.

A rescue assumes somebody can finish the trip. Often nobody can -- there is no
compatible truck inside the spoilage window, or the load is three hours from
its destination with ninety minutes of cold left. The cargo is then going to
spoil on the hard shoulder while a perfectly good rescue is still driving
towards it.

Relaying breaks that. Instead of completing the journey, the load is taken to
the nearest facility that can actually hold it -- a chiller, a bonded
warehouse, a hazmat-approved depot -- and the journey resumes later from
there. A pallet of vaccines sitting at 4C in a depot forty minutes away is
worth its full value; the same pallet delivered perfectly eight hours late is
worth nothing.

Two things decide whether a facility is an option:

  Can it hold this cargo?  A chiller that bottoms out at 2C is no use to a
  load that must stay below -18C, and an ordinary warehouse cannot take
  hazmat at all. These are hard filters, not preferences -- relaying to a
  facility that cannot hold the cargo just spoils it indoors.

  Can the cargo get there in time?  Measured against the same spoilage clock
  the rescue is racing. A facility that cannot be reached before the cargo is
  lost is reported rather than hidden, because an operator staring at a list
  of four options where all four are too far needs to know that is the
  situation, not see an empty list.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Company, Shipment, StorageFacility
from services.matching_engine.app.routing import haversine_distance_km
from shared.observability import logger

from .models import Incident

#: Average speed assumed for a loaded truck heading to a depot, in km/h.
#: Deliberately lower than the open-road figure used for rescue ETAs: a relay
#: leg ends in a yard, at a gate, with a turn off the highway.
RELAY_SPEED_KPH = 38.0

#: Straight-line distance understates road distance. Same 1.3 factor the
#: rescue ETA uses, for the same reason, so the two numbers are comparable.
ROAD_TORTUOSITY = 1.3

#: Minutes of slack kept between arriving and the cargo being lost. Arriving
#: with four minutes to spare is not arriving safely -- the load still has to
#: be signed in and moved inside.
SAFETY_MARGIN_MINUTES = 15.0

#: How far out it is worth looking at all.
DEFAULT_SEARCH_RADIUS_KM = 120.0


def _eta_minutes(distance_km: float) -> float:
    road_km = distance_km * ROAD_TORTUOSITY
    return round((road_km / RELAY_SPEED_KPH) * 60.0, 1)


def assess_facility(
    facility: StorageFacility, shipment: Shipment
) -> Dict[str, Any]:
    """Can this facility hold this cargo? Returns the verdict and why.

    The reasons are returned whether the answer is yes or no. An operator
    diverting a customer's load needs to be able to say what made the depot
    suitable, not just that some algorithm picked it.
    """
    blockers: List[str] = []
    reasons: List[str] = []

    volume = shipment.volume_m3 or 0.0
    if volume > facility.available_m3:
        blockers.append(
            f"Only {facility.available_m3:.0f} m3 free, cargo needs {volume:.1f} m3"
        )
    elif volume:
        reasons.append(f"{facility.available_m3:.0f} m3 free for a {volume:.1f} m3 load")

    if shipment.requires_refrigeration:
        if not facility.refrigerated:
            blockers.append("No temperature-controlled space")
        else:
            required_max = shipment.required_max_temp_c
            if required_max is not None:
                if facility.min_temp_c is None or facility.min_temp_c > required_max:
                    blockers.append(
                        f"Chills to {facility.min_temp_c}C, cargo needs {required_max}C or colder"
                    )
                else:
                    reasons.append(
                        f"Holds down to {facility.min_temp_c}C (cargo needs <={required_max}C)"
                    )

    if shipment.is_hazmat and not facility.hazmat_approved:
        blockers.append("Not licensed for hazardous goods")
    elif shipment.is_hazmat:
        reasons.append("Hazmat licensed")

    if not facility.open_24h:
        reasons.append("Daytime hours only -- confirm the gate is open")

    return {"suitable": not blockers, "blockers": blockers, "reasons": reasons}


async def relay_options(
    session: AsyncSession,
    incident: Incident,
    shipment: Shipment,
    radius_km: float = DEFAULT_SEARCH_RADIUS_KM,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    """Rank the places this cargo could be taken instead of its destination.

    Ordered by whether the cargo survives the trip first and distance second,
    so a reachable depot always outranks a closer one that is out of time.
    """
    result = await session.execute(
        select(StorageFacility).where(StorageFacility.active.is_(True))
    )
    facilities = list(result.scalars().all())

    minutes_left = incident.minutes_until_spoilage
    options: List[Dict[str, Any]] = []

    for facility in facilities:
        distance_km = haversine_distance_km(
            incident.lat, incident.lng, facility.latitude, facility.longitude
        )
        if distance_km > radius_km:
            continue

        eta = _eta_minutes(distance_km)
        assessment = assess_facility(facility, shipment)

        if minutes_left is None:
            in_time: Optional[bool] = None
            margin: Optional[float] = None
        else:
            margin = round(minutes_left - eta, 1)
            in_time = margin >= SAFETY_MARGIN_MINUTES

        volume = shipment.volume_m3 or 0.0
        storage_cost = round(
            facility.handling_fee_inr + facility.storage_fee_inr_per_m3_day * volume, 2
        )

        operator = None
        if facility.operator_company_id:
            company = await session.get(Company, facility.operator_company_id)
            operator = company.name if company else None

        options.append(
            {
                "facilityId": facility.id,
                "name": facility.name,
                "operator": operator or "Independent depot",
                "latitude": facility.latitude,
                "longitude": facility.longitude,
                "address": facility.address,
                "contactPhone": facility.contact_phone,
                "distanceKm": round(distance_km, 1),
                "etaMinutes": eta,
                "minutesToSpare": margin,
                "arrivesInTime": in_time,
                "refrigerated": facility.refrigerated,
                "minTempC": facility.min_temp_c,
                "maxTempC": facility.max_temp_c,
                "hazmatApproved": facility.hazmat_approved,
                "availableM3": facility.available_m3,
                "open24h": facility.open_24h,
                "estimatedCostInr": storage_cost,
                "handlingFeeInr": facility.handling_fee_inr,
                "storageFeeInrPerM3Day": facility.storage_fee_inr_per_m3_day,
                **assessment,
            }
        )

    def rank(option: Dict[str, Any]):
        # Unsuitable last, then out-of-time, then nearest.
        return (
            not option["suitable"],
            option["arrivesInTime"] is False,
            option["distanceKm"],
        )

    options.sort(key=rank)
    return options[:limit]


async def commit_relay(
    session: AsyncSession,
    incident: Incident,
    facility: StorageFacility,
    shipment: Shipment,
    actor_id: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Redirect this incident's cargo to a storage facility.

    Recorded on the incident and on the shipment's destination, so every
    downstream view -- the map, the rescue offer, the driver's screen -- shows
    the depot rather than a customer address nobody is driving to any more.
    """
    assessment = assess_facility(facility, shipment)
    if not assessment["suitable"]:
        raise ValueError("; ".join(assessment["blockers"]))

    incident.relay_facility_id = facility.id
    incident.relay_reason = reason or "Journey cannot be completed in time"
    incident.relay_committed_at = datetime.now(timezone.utc)

    shipment.destination_lat = facility.latitude
    shipment.destination_lng = facility.longitude
    shipment.destination_name = f"{facility.name} (relay)"

    await session.commit()
    await session.refresh(incident)

    logger.info(
        "cargo_relayed_to_storage",
        incident_id=incident.id,
        facility_id=facility.id,
        actor=actor_id,
    )

    distance_km = haversine_distance_km(
        incident.lat, incident.lng, facility.latitude, facility.longitude
    )
    return {
        "incidentId": incident.id,
        "facilityId": facility.id,
        "facilityName": facility.name,
        "latitude": facility.latitude,
        "longitude": facility.longitude,
        "distanceKm": round(distance_km, 1),
        "etaMinutes": _eta_minutes(distance_km),
        "reason": incident.relay_reason,
        "reasons": assessment["reasons"],
    }
