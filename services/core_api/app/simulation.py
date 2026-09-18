"""
Simulation Engine (Phase 16).
Runs deterministic, repeatable end-to-end incident lifecycles across prebuilt scenarios:
1. cold_chain_critical: Refrigerated insulin with 40 min spoilage deadline.
2. hazmat_no_match: Industrial chemical hazmat cargo where no compatible truck exists (failure path).
3. competing_rescuers: Fresh produce with multiple candidates competing on Rescue Score.

Accelerates simulated minutes into real seconds (tunable clock factor).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Callable, Awaitable, List

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "cold_chain_critical": {
        "label": "Refrigerated insulin, 40 min to spoilage",
        "cargoType": "Insulin (cold-chain)",
        "requiresRefrigeration": True,
        "requiredMaxTempC": 8.0,
        "valueInr": 480_000.0,
        "minutesUntilSpoilage": 40.0,
        "volumeM3": 3.2,
        "weightKg": 850.0,
        "lat": 12.9716,
        "lng": 77.5946,
    },
    "hazmat_no_match": {
        "label": "Hazmat cargo, no compatible truck nearby",
        "cargoType": "Industrial solvents (hazmat)",
        "isHazmat": True,
        "valueInr": 210_000.0,
        "minutesUntilSpoilage": None,
        "volumeM3": 6.0,
        "weightKg": 4200.0,
        "lat": 12.9716,
        "lng": 77.5946,
        "forceNoCandidates": True,
    },
    "competing_rescuers": {
        "label": "Fresh produce, multiple rescuers compete",
        "cargoType": "Fresh produce",
        "requiresRefrigeration": True,
        "requiredMaxTempC": 4.0,
        "valueInr": 95_000.0,
        "minutesUntilSpoilage": 90.0,
        "volumeM3": 8.5,
        "weightKg": 3000.0,
        "lat": 12.9716,
        "lng": 77.5946,
        "forceCandidateCount": 4,
    },
}

HAPPY_PATH_SCRIPT = [
    ("incident.breakdown_reported", 0),
    ("incident.triaging", 1),
    ("incident.matching", 2),
    ("candidates.discovered", 4),
    ("candidates.scored", 6),
    ("incident.rescue_offered", 6),
    ("incident.rescue_accepted", 11),
    ("incident.driver_en_route", 12),
    ("driver.arrived", 19),
    ("incident.cargo_transfer", 27),
    ("incident.rescue_in_transit", 28),
    ("incident.delivered", 48),
    ("incident.verification", 48),
    ("incident.escrow_released", 49),
]

NO_MATCH_SCRIPT = [
    ("incident.breakdown_reported", 0),
    ("incident.triaging", 1),
    ("incident.matching", 2),
    ("candidates.none_found", 4),
    ("incident.cancelled", 5),
]


async def run_simulation(
    scenario_key: str,
    emit_event: Callable[[str, Dict[str, Any]], Awaitable[None]],
    seconds_per_simulated_minute: float = 0.05,
) -> List[Dict[str, Any]]:
    """
    Executes a scenario simulation, emitting time-compressed lifecycle events.
    Returns the chronological list of emitted event records.
    """
    if scenario_key not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario_key}'. Available: {list(SCENARIOS.keys())}")

    scenario = SCENARIOS[scenario_key]
    script = NO_MATCH_SCRIPT if scenario.get("forceNoCandidates") else HAPPY_PATH_SCRIPT

    start_time = datetime.now(timezone.utc)
    last_offset = 0
    emitted_records: List[Dict[str, Any]] = []

    for event_type, minute_offset in script:
        # Sleep for the compressed interval
        delay = (minute_offset - last_offset) * seconds_per_simulated_minute
        if delay > 0:
            await asyncio.sleep(delay)
        last_offset = minute_offset

        simulated_ts = (start_time + timedelta(minutes=minute_offset)).isoformat()
        payload = {
            "scenario": scenario_key,
            "simulatedTimestamp": simulated_ts,
            "simulatedMinute": minute_offset,
            "incidentId": f"inc_sim_{scenario_key}",
            **scenario,
        }
        await emit_event(event_type, payload)
        emitted_records.append({
            "eventType": event_type,
            "minuteOffset": minute_offset,
            "timestamp": simulated_ts,
            "payload": payload,
        })

    return emitted_records
