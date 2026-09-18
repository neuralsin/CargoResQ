"""
Route-Aware Matching via OSRM (Phase 15).
Computes road-network distance and driving ETA, falling back to geodesic estimates
if the routing backend is temporarily unreachable.
"""
import math
import os
import httpx
from typing import Tuple, Dict
from shared.observability import logger

OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://router.project-osrm.org")


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance between two points on the earth in km."""
    r = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


async def get_road_eta(
    origin: Tuple[float, float],  # (lat, lng)
    dest: Tuple[float, float],    # (lat, lng)
) -> Dict[str, float]:
    """
    Fetches real road distance and driving duration from OSRM.
    If unavailable or timed out, falls back to geodesic distance with road tortuosity factor.
    """
    lat1, lon1 = origin
    lat2, lon2 = dest
    url = f"{OSRM_BASE_URL}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, params={"overview": "false"})
            if resp.status_code == 200:
                data = resp.json()
                if "routes" in data and len(data["routes"]) > 0:
                    route = data["routes"][0]
                    return {
                        "distanceKm": round(route["distance"] / 1000.0, 2),
                        "etaMinutes": round(route["duration"] / 60.0, 1),
                        "isRoadAware": True,
                    }
    except Exception as e:
        logger.debug("osrm_fallback_engaged", error=str(e))

    # Fallback: Great circle distance * 1.3 road winding factor, avg 45 km/h truck speed
    geo_km = haversine_distance_km(lat1, lon1, lat2, lon2)
    road_km = geo_km * 1.3
    eta_mins = (road_km / 45.0) * 60.0
    return {
        "distanceKm": round(road_km, 2),
        "etaMinutes": round(max(eta_mins, 1.0), 1),
        "isRoadAware": False,
    }
