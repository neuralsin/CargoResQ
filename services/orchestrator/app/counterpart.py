"""
The other truck.

A rescue has two vehicles in it and, once it is bound, two drivers who each
need to know exactly one thing about the other: where they are and how long
until they meet. The stranded driver is standing beside a warming load
wondering whether help is actually coming. The rescuing driver is looking for
a truck on a hard shoulder somewhere ahead in the dark.

Both questions are the same query from opposite ends, so this answers it once
and labels which end the caller is standing at. Nothing here is estimated or
assumed: the positions come from whatever telemetry each device last sent, and
when a fix is stale it says so rather than quietly drawing a pin where the
truck used to be.

Deliberately narrow. A driver gets the counterpart's position, identity and
the physical parameters of the meeting. They do not get the price, the cargo
value, or anything else about the other company's commercial position -- the
same rule that governs every other view in the system.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Company, Driver, Shipment, Truck
from services.matching_engine.app.routing import haversine_distance_km
from services.telemetry.app.models import TruckLiveState

from .models import Incident, IncidentState
from .offer_models import OfferState, RescueOffer

#: Beyond this a position is history, not a location. Matches the freshness
#: window the matcher uses when deciding whether a truck is where it claims.
LIVE_POSITION_MAX_AGE_MINUTES = 10.0

#: Closing speed assumed when turning a distance into a meeting time. A
#: rescue leg is mostly highway but ends with a search for a stopped vehicle.
APPROACH_SPEED_KPH = 45.0

#: Straight line to road distance. The same factor used elsewhere, so the
#: number a driver sees matches the number their dispatcher sees.
ROAD_TORTUOSITY = 1.3

#: Inside this, they can see each other. Telling someone their counterpart is
#: "0.2 km away, 1 minute" is less useful than telling them to look around.
ARRIVED_RADIUS_KM = 0.4


def _is_live(seen_at: Optional[datetime]) -> bool:
    if seen_at is None:
        return False
    seen = seen_at if seen_at.tzinfo else seen_at.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=LIVE_POSITION_MAX_AGE_MINUTES
    )
    return seen >= cutoff


async def _position(
    session: AsyncSession, truck: Truck
) -> Dict[str, Any]:
    """Where a truck is, and how much to trust it.

    Falls back to the registered position when there is no recent fix, but
    never pretends the fallback is live -- a driver deciding whether to walk
    towards oncoming traffic deserves to know the pin is twenty minutes old.
    """
    live = await session.get(TruckLiveState, truck.id)
    if live is not None and live.latitude is not None and _is_live(live.last_received_at):
        return {
            "latitude": live.latitude,
            "longitude": live.longitude,
            "speedKph": live.speed_kph,
            "headingDeg": live.heading_deg,
            "batteryPct": live.battery_pct,
            "positionIsLive": True,
            "positionSuspect": bool(getattr(live, "position_suspect", False)),
            "suspectReason": getattr(live, "suspect_reason", None),
            "lastSeenAt": (
                live.last_received_at.isoformat() if live.last_received_at else None
            ),
        }

    return {
        "latitude": truck.latitude,
        "longitude": truck.longitude,
        "speedKph": None,
        "headingDeg": live.heading_deg if live is not None else None,
        "batteryPct": live.battery_pct if live is not None else None,
        "positionIsLive": False,
        "positionSuspect": False,
        "suspectReason": None,
        "lastSeenAt": (
            live.last_received_at.isoformat()
            if live is not None and live.last_received_at
            else None
        ),
    }


async def _driver_on_truck(
    session: AsyncSession, truck_id: str
) -> Optional[Driver]:
    result = await session.execute(
        select(Driver)
        .where(Driver.assigned_truck_id == truck_id)
        .where(Driver.active.is_(True))
    )
    return result.scalars().first()


async def counterpart_for_driver(
    session: AsyncSession, driver: Driver
) -> Optional[Dict[str, Any]]:
    """The other half of this driver's rescue, if they are in one.

    Returns None when the driver is not party to a bound rescue, which is the
    normal state -- most of the time there is no counterpart and the app
    should say nothing rather than invent a pin.
    """
    if not driver.assigned_truck_id:
        return None

    my_truck = await session.get(Truck, driver.assigned_truck_id)
    if my_truck is None:
        return None

    # Which side am I on? The rescuing truck is named on the offer; the
    # stranded truck is the one carrying the shipment the offer is against.
    result = await session.execute(
        select(RescueOffer, Shipment, Incident)
        .join(Shipment, Shipment.id == RescueOffer.shipment_id)
        .join(Incident, Incident.id == RescueOffer.incident_id)
        .where(RescueOffer.state == OfferState.BOUND.value)
        .where(
            or_(
                RescueOffer.carrier_truck_id == my_truck.id,
                Shipment.truck_id == my_truck.id,
            )
        )
        .order_by(RescueOffer.bound_at.desc())
    )
    row = result.first()
    if row is None:
        return None

    offer, shipment, incident = row
    if incident.state in {
        IncidentState.CANCELLED,
        IncidentState.ESCROW_RELEASED,
        IncidentState.DISPUTED,
    }:
        return None

    i_am_rescuer = offer.carrier_truck_id == my_truck.id
    other_truck_id = shipment.truck_id if i_am_rescuer else offer.carrier_truck_id
    if not other_truck_id:
        return None

    other_truck = await session.get(Truck, other_truck_id)
    if other_truck is None:
        return None

    mine = await _position(session, my_truck)
    theirs = await _position(session, other_truck)

    distance_km: Optional[float] = None
    eta_minutes: Optional[float] = None
    if None not in (
        mine["latitude"],
        mine["longitude"],
        theirs["latitude"],
        theirs["longitude"],
    ):
        distance_km = round(
            haversine_distance_km(
                mine["latitude"], mine["longitude"],
                theirs["latitude"], theirs["longitude"],
            ),
            2,
        )
        eta_minutes = round(
            (distance_km * ROAD_TORTUOSITY / APPROACH_SPEED_KPH) * 60.0, 1
        )

    company = await session.get(Company, other_truck.company_id)
    other_driver = await _driver_on_truck(session, other_truck.id)

    return {
        "role": "RESCUER" if i_am_rescuer else "STRANDED",
        "incidentId": incident.id,
        "incidentState": incident.state.value,
        "offerId": offer.id,
        # Who and what is coming, or being gone to.
        "counterpart": {
            "role": "STRANDED" if i_am_rescuer else "RESCUER",
            "companyName": company.name if company else "Unknown carrier",
            "driverName": other_driver.name if other_driver else None,
            "driverPhone": other_driver.phone if other_driver else None,
            "truckId": other_truck.id,
            "registrationNumber": other_truck.registration_number,
            "refrigerated": other_truck.refrigerated,
            "minTempC": other_truck.min_temp_c,
            **theirs,
        },
        "me": {
            "truckId": my_truck.id,
            "registrationNumber": my_truck.registration_number,
            **mine,
        },
        "distanceKm": distance_km,
        "etaMinutes": eta_minutes,
        "arrived": distance_km is not None and distance_km <= ARRIVED_RADIUS_KM,
        # The cargo itself, without its commercial value. The rescuing driver
        # needs to know what they are loading; neither driver needs to know
        # what it is worth.
        "cargo": {
            "type": shipment.cargo_type,
            "requiresRefrigeration": shipment.requires_refrigeration,
            "requiredMaxTempC": shipment.required_max_temp_c,
            "isHazmat": shipment.is_hazmat,
            "volumeM3": shipment.volume_m3,
            "weightKg": shipment.weight_kg,
        },
        "minutesUntilSpoilage": incident.minutes_until_spoilage,
    }
