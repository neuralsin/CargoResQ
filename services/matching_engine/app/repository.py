"""
Matching Engine Candidate Repository (Phase 3).
PostGIS-backed spatial query using GIST index, ST_DWithin, and ST_Distance.
Also includes a dialect-aware fallback for local SQLite testing.
"""
from typing import List, Dict, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from .routing import haversine_distance_km


POSTGIS_MATCH_QUERY = text("""
    SELECT
        t.id,
        t.company_id,
        t.refrigerated,
        t.min_temp_c,
        t.hazmat_certified,
        t.max_volume_m3,
        t.max_weight_kg,
        ST_Distance(t.location, ST_MakePoint(:lng, :lat)::geography) / 1000.0 AS distance_km
    FROM trucks t
    WHERE t.status = 'idle'
      AND ST_DWithin(t.location, ST_MakePoint(:lng, :lat)::geography, :radius_m)
      AND (:requires_refrigeration = false OR t.refrigerated = true)
      AND (:requires_refrigeration = false OR t.min_temp_c <= :required_max_temp)
      AND (:is_hazmat = false OR t.hazmat_certified = true)
      AND t.max_volume_m3 >= :volume_m3
      AND t.max_weight_kg >= :weight_kg
    ORDER BY distance_km ASC
    LIMIT :limit
""")


async def find_candidates(
    session: AsyncSession,
    lat: float,
    lng: float,
    shipment: Dict[str, Any],
    radius_km: float = 50.0,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Finds compatible idle rescue trucks within the specified radius.
    """
    bind = session.bind
    dialect_name = bind.dialect.name if bind else "postgresql"

    requires_refrig = bool(
        shipment.get("requiresRefrigeration", shipment.get("requires_refrigeration", False))
    )
    req_max_temp = shipment.get("requiredMaxTemp", shipment.get("required_max_temp_c", 25.0))
    if req_max_temp is None:
        req_max_temp = 25.0

    is_hazmat = bool(shipment.get("isHazmat", shipment.get("is_hazmat", False)))
    vol_m3 = float(shipment.get("volumeM3", shipment.get("volume_m3", 1.0)))
    wt_kg = float(shipment.get("weightKg", shipment.get("weight_kg", 100.0)))

    if dialect_name != "sqlite":
        # PostGIS Execution (Phase 3.2 1:1)
        result = await session.execute(
            POSTGIS_MATCH_QUERY,
            {
                "lat": lat,
                "lng": lng,
                "radius_m": radius_km * 1000.0,
                "requires_refrigeration": requires_refrig,
                "required_max_temp": req_max_temp,
                "is_hazmat": is_hazmat,
                "volume_m3": vol_m3,
                "weight_kg": wt_kg,
                "limit": limit,
            },
        )
        return [dict(row._mapping) for row in result]

    # SQLite fallback query for lightweight local testing
    sqlite_query = text("""
        SELECT
            t.id,
            t.company_id,
            t.latitude,
            t.longitude,
            t.refrigerated,
            t.min_temp_c,
            t.hazmat_certified,
            t.max_volume_m3,
            t.max_weight_kg
        FROM trucks t
        WHERE t.status = 'idle'
    """)
    rows = (await session.execute(sqlite_query)).fetchall()
    candidates = []
    for r in rows:
        mapping = dict(r._mapping)
        # Compatibility filters
        if requires_refrig and not mapping.get("refrigerated"):
            continue
        if requires_refrig and mapping.get("min_temp_c") is not None and mapping["min_temp_c"] > req_max_temp:
            continue
        if is_hazmat and not mapping.get("hazmat_certified"):
            continue
        if mapping.get("max_volume_m3", 0) < vol_m3 or mapping.get("max_weight_kg", 0) < wt_kg:
            continue

        t_lat = mapping.get("latitude", lat)
        t_lng = mapping.get("longitude", lng)
        dist = haversine_distance_km(lat, lng, t_lat, t_lng)
        if dist <= radius_km:
            mapping["distance_km"] = round(dist, 2)
            candidates.append(mapping)

    candidates.sort(key=lambda c: c["distance_km"])
    return candidates[:limit]
