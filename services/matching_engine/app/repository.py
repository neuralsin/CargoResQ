"""
Finding compatible spare capacity near a breakdown.

This is the cross-carrier part of the product: the search deliberately spans
every company on the network, because the whole premise is that a competitor's
idle reefer eight kilometres away beats your own one that is four hours out.

Position comes from `truck_live_state` when the truck has reported recently,
falling back to the registration coordinates otherwise. The previous version
read `trucks.latitude/longitude` only, which no code path ever updated -- so
matching ranked the fleet by where each truck was first registered.

The PostGIS branch was also unreachable: it selected `trucks.location`, a
column that exists only inside a migration and is not on the model, so the
Postgres path raised on every call and only the SQLite fallback ever ran.
"""
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Truck, TruckStatus
from services.telemetry.app.models import TruckLiveState

from .routing import haversine_distance_km

#: A position older than this is treated as unknown for matching purposes.
#: Dispatching to where a truck was six hours ago is worse than not matching.
LIVE_POSITION_MAX_AGE_MINUTES = 30.0


def _bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """A cheap pre-filter so the haversine pass is not run over the whole fleet.

    One degree of latitude is ~111 km everywhere; longitude shrinks with the
    cosine of the latitude.
    """
    import math

    lat_delta = radius_km / 111.0
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    lng_delta = radius_km / (111.0 * cos_lat)
    return (lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta)


def _is_compatible(
    truck: Truck,
    requires_refrigeration: bool,
    required_max_temp: Optional[float],
    is_hazmat: bool,
    volume_m3: float,
    weight_kg: float,
    ignore_capacity: bool = False,
) -> tuple[bool, List[str]]:
    """Check one truck against the cargo, and say what failed.

    Returning the reasons matters: "no compatible truck" is a legitimate and
    important answer, and an operator needs to know whether the problem is
    refrigeration, certification or simply size.
    """
    failures: List[str] = []

    if requires_refrigeration:
        if not truck.refrigerated:
            failures.append("not refrigerated")
        elif required_max_temp is not None and truck.min_temp_c is not None:
            if truck.min_temp_c > required_max_temp:
                failures.append(
                    f"cannot hold {required_max_temp}C (floor {truck.min_temp_c}C)"
                )
    if is_hazmat and not truck.hazmat_certified:
        failures.append("not hazmat certified")
    # Capacity is skipped when planning a split: a truck that can take half
    # the pallets is not an incompatible truck, it is half the answer. The
    # temperature and hazmat checks above are never skipped, because carrying
    # part of a load badly is no better than carrying all of it badly.
    if not ignore_capacity:
        if truck.max_volume_m3 < volume_m3:
            failures.append(f"volume {truck.max_volume_m3}m3 < {volume_m3}m3 required")
        if truck.max_weight_kg < weight_kg:
            failures.append(f"payload {truck.max_weight_kg}kg < {weight_kg}kg required")

    return (not failures), failures


async def find_candidates(
    session: AsyncSession,
    lat: float,
    lng: float,
    requires_refrigeration: bool = False,
    required_max_temp: Optional[float] = None,
    is_hazmat: bool = False,
    volume_m3: float = 1.0,
    weight_kg: float = 100.0,
    radius_km: float = 50.0,
    limit: int = 5,
    exclude_truck_ids: Optional[List[str]] = None,
    ignore_capacity: bool = False,
) -> List[Dict[str, Any]]:
    """Return compatible idle trucks within the radius, nearest first.

    Deliberately not filtered by company: a rescue that can only come from
    your own fleet is the problem this platform exists to solve.
    """
    from datetime import datetime, timedelta, timezone

    min_lat, max_lat, min_lng, max_lng = _bounding_box(lat, lng, radius_km)
    fresh_after = datetime.now(timezone.utc) - timedelta(
        minutes=LIVE_POSITION_MAX_AGE_MINUTES
    )

    stmt = (
        select(Truck, TruckLiveState)
        .outerjoin(TruckLiveState, TruckLiveState.truck_id == Truck.id)
        .where(Truck.status == TruckStatus.idle)
    )
    if exclude_truck_ids:
        stmt = stmt.where(Truck.id.not_in(exclude_truck_ids))

    rows = (await session.execute(stmt)).all()

    candidates: List[Dict[str, Any]] = []
    for truck, live in rows:
        # Prefer a recent telemetry fix; fall back to the registered position.
        position_is_live = False
        truck_lat, truck_lng = truck.latitude, truck.longitude
        if live is not None and live.latitude is not None and live.last_received_at:
            seen = live.last_received_at
            seen = seen if seen.tzinfo else seen.replace(tzinfo=timezone.utc)
            if seen >= fresh_after:
                truck_lat, truck_lng = live.latitude, live.longitude
                position_is_live = True

        if not (min_lat <= truck_lat <= max_lat and min_lng <= truck_lng <= max_lng):
            continue

        distance_km = haversine_distance_km(lat, lng, truck_lat, truck_lng)
        if distance_km > radius_km:
            continue

        compatible, failures = _is_compatible(
            truck,
            requires_refrigeration,
            required_max_temp,
            is_hazmat,
            volume_m3,
            weight_kg,
            ignore_capacity=ignore_capacity,
        )
        if not compatible:
            continue

        candidates.append(
            {
                "truckId": truck.id,
                "companyId": truck.company_id,
                "registrationNumber": truck.registration_number,
                "latitude": truck_lat,
                "longitude": truck_lng,
                "positionIsLive": position_is_live,
                "lastSeenAt": (
                    live.last_received_at.isoformat()
                    if live is not None and live.last_received_at
                    else None
                ),
                "straightLineKm": round(distance_km, 2),
                "refrigerated": truck.refrigerated,
                "minTempC": truck.min_temp_c,
                "hazmatCertified": truck.hazmat_certified,
                "availableVolumeM3": truck.max_volume_m3,
                "availableWeightKg": truck.max_weight_kg,
                "headingDeg": live.heading_deg if live is not None else None,
            }
        )

    candidates.sort(key=lambda c: c["straightLineKm"])
    return candidates[:limit]


async def explain_no_match(
    session: AsyncSession,
    lat: float,
    lng: float,
    requires_refrigeration: bool,
    required_max_temp: Optional[float],
    is_hazmat: bool,
    volume_m3: float,
    weight_kg: float,
    radius_km: float,
) -> Dict[str, Any]:
    """Say why nothing matched.

    "No rescue available" is a real outcome and a better one than forcing an
    incompatible truck onto temperature-sensitive cargo. But an operator
    staring at an empty list needs to know whether to widen the radius or
    stop looking for a reefer.
    """
    min_lat, max_lat, min_lng, max_lng = _bounding_box(lat, lng, radius_km)
    rows = (await session.execute(select(Truck))).scalars().all()

    in_radius = 0
    idle_in_radius = 0
    reasons: Dict[str, int] = {}

    for truck in rows:
        if not (
            min_lat <= truck.latitude <= max_lat and min_lng <= truck.longitude <= max_lng
        ):
            continue
        if haversine_distance_km(lat, lng, truck.latitude, truck.longitude) > radius_km:
            continue
        in_radius += 1
        if truck.status != TruckStatus.idle:
            reasons[f"busy ({truck.status.value})"] = (
                reasons.get(f"busy ({truck.status.value})", 0) + 1
            )
            continue
        idle_in_radius += 1
        _, failures = _is_compatible(
            truck, requires_refrigeration, required_max_temp, is_hazmat, volume_m3, weight_kg
        )
        for failure in failures:
            reasons[failure] = reasons.get(failure, 0) + 1

    return {
        "trucksInRadius": in_radius,
        "idleInRadius": idle_in_radius,
        "radiusKm": radius_km,
        "blockers": [
            {"reason": reason, "truckCount": count}
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])
        ],
    }
