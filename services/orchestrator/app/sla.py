"""
SLA Tracking & Performance Benchmarking (Phase 18.5).
Calculates exact interval durations across the incident lifecycle against SLA targets.
"""
from datetime import datetime
from typing import List, Dict, Any, Optional

SLA_TARGETS_SECONDS = {
    "match": 120,        # Breakdown to rescue offered < 2 mins
    "accept": 300,       # Rescue offered to accepted < 5 mins
    "arrival": 2700,     # Driver en route to arrival < 45 mins
    "resolution": 7200,  # Full incident resolution < 2 hours
}


def _parse_ts(val: Any) -> Optional[datetime]:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def compute_sla(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Computes SLA performance:
    - match: breakdown_reported -> rescue_offered
    - accept: rescue_offered -> rescue_accepted
    - arrival: driver_en_route -> driver_arrived (or cargo_transfer)
    - resolution: breakdown_reported -> escrow_released
    """
    ts: Dict[str, datetime] = {}
    for e in events:
        parsed = _parse_ts(e.get("created_at") or e.get("timestamp"))
        if parsed:
            event_type = e.get("type", "")
            # Only record first occurrence
            if event_type not in ts:
                ts[event_type] = parsed

    def delta(a_type: str, b_type: str) -> Optional[float]:
        if a_type in ts and b_type in ts:
            return round((ts[b_type] - ts[a_type]).total_seconds(), 2)
        return None

    match_sec = delta("incident.breakdown_reported", "incident.rescue_offered")
    accept_sec = delta("incident.rescue_offered", "incident.rescue_accepted")
    # arrival delta can use driver.arrived or incident.cargo_transfer
    arrival_sec = delta("incident.driver_en_route", "driver.arrived")
    if arrival_sec is None:
        arrival_sec = delta("incident.driver_en_route", "incident.cargo_transfer")
    resolution_sec = delta("incident.breakdown_reported", "incident.escrow_released")

    def format_sla_row(actual_sec: Optional[float], target_sec: int) -> Dict[str, Any]:
        if actual_sec is None:
            return {"targetSec": target_sec, "actualSec": None, "met": None, "status": "PENDING"}
        return {
            "targetSec": target_sec,
            "actualSec": actual_sec,
            "met": actual_sec <= target_sec,
            "status": "MET" if actual_sec <= target_sec else "BREACHED",
        }

    return {
        "match": format_sla_row(match_sec, SLA_TARGETS_SECONDS["match"]),
        "accept": format_sla_row(accept_sec, SLA_TARGETS_SECONDS["accept"]),
        "arrival": format_sla_row(arrival_sec, SLA_TARGETS_SECONDS["arrival"]),
        "resolution": format_sla_row(resolution_sec, SLA_TARGETS_SECONDS["resolution"]),
    }
