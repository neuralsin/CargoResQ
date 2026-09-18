"""
Turning a stream of positions into things worth telling somebody.

Two rules shape this module.

**One episode, one alert.** A truck standing still for six hours is a single
dwell alert whose occurrence count climbs, not one alert per ping. The
mechanism is a `dedupe_key` that identifies the *episode* rather than the
observation, plus a partial unique index that holds only while the alert is
live -- so a genuinely new episode can open later without colliding with the
resolved one.

**Notification is throttled separately from detection.** Detection runs on
every ping; a human is told at most once per cooldown. Conflating the two
either floods the operator or loses the escalation.

Detectors that compare a new reading against the previous one run inline on
ingest, because the latency matters and the work is O(1). Detectors that are
about the *absence* of data -- staleness, dwell -- cannot run on ingest by
definition, and live in the sweeper.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.core_api.app.models import Shipment
from services.matching_engine.app.routing import haversine_distance_km

from .models import AlertSeverity, AlertType

#: Above this implied speed a position jump is not physically plausible for a
#: loaded truck. Used as a spoofing signal, with guards below.
IMPLAUSIBLE_SPEED_KPH = 120.0

#: Guards on the impossible-jump check. Without them a three-second gap with
#: 500 m of GPS error trivially implies 600 km/h, and the alert fires on
#: ordinary noise from a cheap receiver rather than on spoofing.
MIN_JUMP_INTERVAL_SECONDS = 20.0
MAX_JUMP_ACCURACY_M = 200.0

#: Below this speed the truck is considered stationary.
MOVING_SPEED_KPH = 5.0
#: How far it must move to end a dwell episode.
DWELL_BREAK_DISTANCE_M = 500.0

#: A ping older than this is a replayed offline queue, not a live fix.
BACKFILL_THRESHOLD_MINUTES = 15.0

DEFAULT_DWELL_MINUTES = 45.0
INCIDENT_DWELL_MINUTES = 20.0
DEFAULT_STALE_MINUTES = 15.0
INCIDENT_STALE_MINUTES = 5.0

BATTERY_THRESHOLDS = (20.0, 10.0, 5.0)


@dataclass
class Finding:
    """A detected condition, ready to be upserted as an alert episode."""

    alert_type: AlertType
    severity: AlertSeverity
    dedupe_key: str
    values: Dict[str, Any]
    #: Longer for low-signal conditions, shorter for urgent ones.
    notify_cooldown_s: int = 900

    def as_alert_fields(self) -> Dict[str, Any]:
        return {
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "dedupe_key": self.dedupe_key,
            "notify_cooldown_s": self.notify_cooldown_s,
        }


def _ts(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def episode_key(truck_id: str, alert_type: AlertType, token: Any) -> str:
    """Identify an episode, not an observation.

    The token is whatever makes *this* occurrence one continuous event: the
    moment a dwell began, the threshold a battery crossed, the instant of a
    single impossible jump.
    """
    return f"{truck_id}:{alert_type.value}:{token}"


def is_backfill(recorded_at: datetime, received_at: datetime) -> bool:
    """True when a ping arrived long after it was taken.

    Such pings are stored as history and used for cold-chain evidence, but
    they never drive live detection: a driver regaining signal after six hours
    offline would otherwise produce an impossible jump and hundreds of dwell
    alerts in one request.
    """
    recorded = _ts(recorded_at)
    received = _ts(received_at)
    if recorded is None or received is None:
        return False
    return (received - recorded) > timedelta(minutes=BACKFILL_THRESHOLD_MINUTES)


# ---------------------------------------------------------------------------
# On-ingest detectors: compare this reading with the last one
# ---------------------------------------------------------------------------


def detect_mock_location(ping: Dict[str, Any]) -> Optional[Finding]:
    """Android tells us directly when a location came from a mock provider.

    Far more reliable than inferring spoofing from implied speed, which has
    false positives on ordinary GPS noise. This is the primary signal; the
    speed check below is the backstop for platforms that do not report it.
    """
    if not ping.get("mock_location"):
        return None
    return Finding(
        AlertType.MOCK_LOCATION,
        AlertSeverity.CRITICAL,
        episode_key(ping["truck_id"], AlertType.MOCK_LOCATION, ping["device_id"]),
        {"deviceId": ping["device_id"], "recordedAt": str(ping["recorded_at"])},
        notify_cooldown_s=3600,
    )


def detect_impossible_jump(
    ping: Dict[str, Any], previous: Optional[Dict[str, Any]]
) -> Optional[Finding]:
    """Flag a position change no truck could have made."""
    if previous is None:
        return None

    now = _ts(ping["recorded_at"])
    before = _ts(previous.get("recorded_at"))
    if now is None or before is None:
        return None

    seconds = (now - before).total_seconds()
    if seconds < MIN_JUMP_INTERVAL_SECONDS:
        return None

    accuracy = ping.get("accuracy_m")
    if accuracy is not None and accuracy > MAX_JUMP_ACCURACY_M:
        # Too imprecise to distinguish a jump from receiver error.
        return None

    km = haversine_distance_km(
        previous["latitude"], previous["longitude"], ping["latitude"], ping["longitude"]
    )
    implied_kph = km / (seconds / 3600.0)
    if implied_kph <= IMPLAUSIBLE_SPEED_KPH:
        return None

    return Finding(
        AlertType.IMPOSSIBLE_JUMP,
        AlertSeverity.CRITICAL,
        # A jump is a single instant, not a continuing state.
        episode_key(ping["truck_id"], AlertType.IMPOSSIBLE_JUMP, int(now.timestamp())),
        {
            "impliedKph": round(implied_kph, 1),
            "distanceKm": round(km, 2),
            "intervalSeconds": round(seconds, 1),
        },
        notify_cooldown_s=1800,
    )


def detect_low_battery(ping: Dict[str, Any]) -> Optional[Finding]:
    """A dying phone means the truck is about to go dark."""
    battery = ping.get("battery_pct")
    if battery is None or ping.get("is_charging"):
        return None

    crossed = next((t for t in BATTERY_THRESHOLDS if battery <= t), None)
    if crossed is None:
        return None

    severity = AlertSeverity.CRITICAL if crossed <= 5.0 else AlertSeverity.WARN
    return Finding(
        AlertType.LOW_DEVICE_BATTERY,
        severity,
        # Keyed on the threshold, so 19% and 18% are one episode but dropping
        # to 9% opens a new, more severe one.
        episode_key(ping["truck_id"], AlertType.LOW_DEVICE_BATTERY, int(crossed)),
        {"batteryPct": battery, "threshold": crossed},
        notify_cooldown_s=1800,
    )


def detect_temperature_excursion(
    reading: Dict[str, Any],
    shipment: Optional[Shipment],
    excursion_started_at: Optional[datetime] = None,
) -> Optional[Finding]:
    """Cargo above the temperature its shipment declares.

    The limit comes from the shipment record, never from the reading's
    payload, so the party being paid cannot widen their own tolerance.
    """
    temperature = reading.get("temperature_c")
    if temperature is None or shipment is None or shipment.required_max_temp_c is None:
        return None
    if temperature <= shipment.required_max_temp_c:
        return None

    started = excursion_started_at or _ts(reading["recorded_at"])
    overshoot = temperature - shipment.required_max_temp_c
    severity = AlertSeverity.CRITICAL if overshoot >= 3.0 else AlertSeverity.WARN

    return Finding(
        AlertType.TEMP_EXCURSION,
        severity,
        episode_key(
            reading["truck_id"] or shipment.id,
            AlertType.TEMP_EXCURSION,
            f"{shipment.id}:{int(started.timestamp())}",
        ),
        {
            "temperatureC": temperature,
            "limitC": shipment.required_max_temp_c,
            "overshootC": round(overshoot, 2),
            "shipmentId": shipment.id,
        },
        notify_cooldown_s=600,
    )


def detect_reefer_fault(reading: Dict[str, Any]) -> Optional[Finding]:
    if reading.get("reefer_state") != "fault":
        return None
    return Finding(
        AlertType.REEFER_FAULT,
        AlertSeverity.CRITICAL,
        episode_key(
            reading.get("truck_id") or reading.get("shipment_id"),
            AlertType.REEFER_FAULT,
            int(_ts(reading["recorded_at"]).timestamp()) // 3600,
        ),
        {"reeferState": "fault", "shipmentId": reading.get("shipment_id")},
        notify_cooldown_s=600,
    )


def detect_door_open(
    reading: Dict[str, Any], incident_state: Optional[str], speed_kph: Optional[float]
) -> Optional[Finding]:
    """A door open in motion, outside a sanctioned transfer."""
    if not reading.get("door_open"):
        return None
    if incident_state == "CARGO_TRANSFER":
        return None
    if speed_kph is None or speed_kph <= MOVING_SPEED_KPH:
        return None

    return Finding(
        AlertType.DOOR_OPEN_UNSCHEDULED,
        AlertSeverity.WARN,
        episode_key(
            reading.get("truck_id") or reading.get("shipment_id"),
            AlertType.DOOR_OPEN_UNSCHEDULED,
            int(_ts(reading["recorded_at"]).timestamp()) // 600,
        ),
        {"speedKph": speed_kph, "shipmentId": reading.get("shipment_id")},
    )


def run_ingest_detectors(
    ping: Dict[str, Any], previous: Optional[Dict[str, Any]]
) -> List[Finding]:
    """Every detector that only needs this ping and the one before it."""
    if ping.get("is_backfill"):
        return []
    findings = [
        detect_mock_location(ping),
        detect_impossible_jump(ping, previous),
        detect_low_battery(ping),
    ]
    return [f for f in findings if f is not None]


# ---------------------------------------------------------------------------
# Sweeper detectors: about the absence of data, or elapsed time
# ---------------------------------------------------------------------------


def detect_gps_stale(
    truck_id: str,
    last_received_at: Optional[datetime],
    now: datetime,
    has_active_incident: bool = False,
) -> Optional[Finding]:
    """Silence from a truck that should be reporting.

    Impossible to detect on ingest: the signal is that nothing arrived.
    """
    last = _ts(last_received_at)
    if last is None:
        return None

    threshold = INCIDENT_STALE_MINUTES if has_active_incident else DEFAULT_STALE_MINUTES
    silent_minutes = (now - last).total_seconds() / 60.0
    if silent_minutes < threshold:
        return None

    severity = AlertSeverity.CRITICAL if has_active_incident else AlertSeverity.WARN
    return Finding(
        AlertType.GPS_STALE,
        severity,
        # Keyed on when contact was lost, so one silence is one episode.
        episode_key(truck_id, AlertType.GPS_STALE, int(last.timestamp())),
        {
            "silentMinutes": round(silent_minutes, 1),
            "thresholdMinutes": threshold,
            "lastSeenAt": last.isoformat(),
        },
    )


def detect_dwell(
    truck_id: str,
    dwell_started_at: Optional[datetime],
    now: datetime,
    has_active_incident: bool = False,
) -> Optional[Finding]:
    """A truck that has not moved for too long.

    Often the first sign of a breakdown nobody has reported yet, which is
    precisely the situation this platform exists for.
    """
    started = _ts(dwell_started_at)
    if started is None:
        return None

    threshold = INCIDENT_DWELL_MINUTES if has_active_incident else DEFAULT_DWELL_MINUTES
    stationary_minutes = (now - started).total_seconds() / 60.0
    if stationary_minutes < threshold:
        return None

    severity = (
        AlertSeverity.CRITICAL
        if stationary_minutes >= threshold * 3
        else AlertSeverity.WARN
    )
    return Finding(
        AlertType.DWELL,
        severity,
        # Keyed on when the truck stopped. Six hours of standing still is one
        # episode, not three hundred and sixty alerts.
        episode_key(truck_id, AlertType.DWELL, int(started.timestamp())),
        {
            "stationaryMinutes": round(stationary_minutes, 1),
            "thresholdMinutes": threshold,
            "since": started.isoformat(),
        },
    )


def detect_route_deviation(
    truck_id: str,
    position: tuple[float, float],
    route_points: List[tuple[float, float]],
    corridor_buffer_m: float,
    consecutive_breaches: int,
    required_breaches: int = 3,
) -> Optional[Finding]:
    """A truck outside its planned corridor.

    Requires several consecutive out-of-corridor fixes: a single wide GPS fix
    near the edge of the buffer is noise, not a deviation. Returns None when
    there is no route, rather than inventing one -- a shipment with no planned
    destination cannot deviate from it.
    """
    if not route_points:
        return None
    if consecutive_breaches < required_breaches:
        return None

    lat, lng = position
    nearest_km = min(
        haversine_distance_km(lat, lng, plat, plng) for plat, plng in route_points
    )
    off_corridor_m = nearest_km * 1000.0
    if off_corridor_m <= corridor_buffer_m:
        return None

    return Finding(
        AlertType.ROUTE_DEVIATION,
        AlertSeverity.WARN,
        episode_key(truck_id, AlertType.ROUTE_DEVIATION, int(off_corridor_m // 1000)),
        {
            "offCorridorMetres": round(off_corridor_m),
            "corridorBufferMetres": corridor_buffer_m,
            "consecutiveBreaches": consecutive_breaches,
        },
    )


def dwell_state(
    previous_lat: Optional[float],
    previous_lng: Optional[float],
    dwell_started_at: Optional[datetime],
    ping: Dict[str, Any],
) -> tuple[bool, Optional[datetime], Optional[float], Optional[float]]:
    """Work out whether the truck is moving, and when it last stopped.

    Returns (is_moving, dwell_started_at, anchor_lat, anchor_lng).
    """
    recorded = _ts(ping["recorded_at"])
    speed = ping.get("speed_kph")

    moved_far = False
    if previous_lat is not None and previous_lng is not None:
        metres = (
            haversine_distance_km(previous_lat, previous_lng, ping["latitude"], ping["longitude"])
            * 1000.0
        )
        moved_far = metres > DWELL_BREAK_DISTANCE_M

    is_moving = (speed is not None and speed > MOVING_SPEED_KPH) or moved_far

    if is_moving:
        return True, None, None, None
    if dwell_started_at is None:
        # Just came to rest: this is the start of a new dwell episode.
        return False, recorded, ping["latitude"], ping["longitude"]
    return False, _ts(dwell_started_at), previous_lat, previous_lng
