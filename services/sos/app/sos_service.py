"""
Raising, broadcasting and resolving an SOS.

Two audiences, two payloads. The reporting company sees everything, including
the driver's condition. Nearby carriers see a redacted form: enough to decide
whether they can help and get there, and nothing about who the driver is,
what the load is, or what it is worth. That split is not a display convention
-- it is built by two separate functions, so the redacted form has no field to
put a name or a cargo value in.

Scope, stated plainly: this does not dispatch emergency services. It tells the
driver's own company, it tells nearby carriers who might physically help, it
invokes whatever outbound channels the operator configured, and it hands the
device the numbers to dial. The call is placed by the phone.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Company, Driver, Shipment, Truck
from services.matching_engine.app.routing import haversine_distance_km
from services.telemetry.app.models import TruckLiveState
from shared.events import EventEnvelope
from shared.observability import logger

from .models import (
    DEFAULT_BROADCAST_RADIUS_KM,
    EMERGENCY_NUMBERS,
    SOS_TRANSITIONS,
    SosAlert,
    SosBroadcast,
    SosCategory,
    SosEvent,
    SosResponse,
    SosSeverity,
    SosStatus,
)
from .notifiers import NotifyTarget, configured_notifiers

#: How long an unanswered CRITICAL alert waits before the radius widens and
#: the outbound adapters fire.
AUTO_ESCALATE_SECONDS = {
    SosSeverity.CRITICAL: 180,
    SosSeverity.HIGH: 420,
    SosSeverity.MODERATE: 900,
}

#: Coarse enough that a redacted broadcast does not pinpoint a vehicle that
#: may be the target of a hijacking, precise enough to decide whether you are
#: close enough to help.
REDACTED_COORD_PLACES = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SosError(Exception):
    code = "sos_error"
    http_status = 400

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context = context


class SosNotFound(SosError):
    code = "sos_not_found"
    http_status = 404


class IllegalSosTransition(SosError):
    code = "illegal_transition"
    http_status = 409


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------


async def raise_sos(
    session: AsyncSession,
    *,
    driver: Optional[Driver],
    company_id: str,
    created_by_type: str,
    created_by_id: str,
    category: SosCategory,
    severity: SosSeverity,
    latitude: float,
    longitude: float,
    accuracy_m: Optional[float] = None,
    location_source: str = "gps",
    landmark_note: Optional[str] = None,
    condition: Optional[Dict[str, Any]] = None,
    silent_mode: bool = False,
    network_broadcast: Optional[bool] = None,
    client_request_id: Optional[str] = None,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Tuple[SosAlert, bool]:
    """Record an SOS and fan it out. Returns (alert, was_created).

    Idempotent on client_request_id: a driver in trouble taps the button
    repeatedly, and five alerts help nobody. Deliberately not rate-limited --
    throttling a panic button is the wrong failure mode.
    """
    if client_request_id:
        existing = await session.execute(
            select(SosAlert).where(SosAlert.client_request_id == client_request_id)
        )
        found = existing.scalars().first()
        if found is not None:
            return found, False

    condition = condition or {}

    # Hijack and security alerts are not broadcast to the wider network by
    # default: telling an unvetted set of nearby companies "there is a truck
    # being taken, here is where" is itself a risk.
    if network_broadcast is None:
        network_broadcast = category != SosCategory.POLICE_SECURITY

    alert = SosAlert(
        company_id=company_id,
        driver_id=driver.id if driver else None,
        truck_id=driver.assigned_truck_id if driver else None,
        category=category.value,
        severity=severity.value,
        status=SosStatus.NEW.value,
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy_m,
        location_source=location_source,
        landmark_note=landmark_note,
        persons_affected=condition.get("persons_affected"),
        is_conscious=condition.get("is_conscious"),
        is_breathing=condition.get("is_breathing"),
        is_trapped=condition.get("is_trapped"),
        is_mobile=condition.get("is_mobile"),
        severe_bleeding=condition.get("severe_bleeding"),
        condition_note=condition.get("note"),
        vitals_json=condition.get("vitals"),
        silent_mode=silent_mode,
        broadcast_radius_km=DEFAULT_BROADCAST_RADIUS_KM.get(category, 25.0),
        network_broadcast=network_broadcast,
        reported_at=_now(),
        auto_escalate_at=_now()
        + timedelta(seconds=AUTO_ESCALATE_SECONDS.get(severity, 600)),
        created_by_type=created_by_type,
        created_by_id=created_by_id,
        client_request_id=client_request_id,
    )
    session.add(alert)
    await session.flush()

    await _log_event(
        session,
        alert,
        "sos.raised",
        created_by_type,
        created_by_id,
        None,
        SosStatus.NEW.value,
        {"category": category.value, "severity": severity.value},
    )

    recipients = await _compute_audience(session, alert)
    for company_id_, tier, distance in recipients:
        session.add(
            SosBroadcast(
                sos_id=alert.id,
                recipient_company_id=company_id_,
                tier=tier,
                channel="WS",
                adapter_name="-",
                redaction_level="FULL" if tier == "OWN_COMPANY" else "REDACTED",
                distance_km_at_send=distance,
                adapter_status="SENT",
            )
        )

    alert.status = SosStatus.BROADCASTING.value
    alert.broadcast_at = _now()
    await session.commit()
    await session.refresh(alert)

    logger.warn(
        "sos_raised",
        sos_id=alert.id,
        category=alert.category,
        severity=alert.severity,
        recipients=len(recipients),
    )

    if event_publisher:
        await _publish(event_publisher, "sos.raised", alert, recipients)

    return alert, True


async def _compute_audience(
    session: AsyncSession, alert: SosAlert
) -> List[Tuple[str, str, Optional[float]]]:
    """Decide who is told, server-side, and record it.

    Returns (company_id, tier, distance_km). The reporting company always.
    Nearby carriers only when the category permits it and the severity
    warrants it -- proximity is computed from live telemetry, so "nearby"
    means where a truck actually is, not where it was registered.
    """
    audience: List[Tuple[str, str, Optional[float]]] = [
        (alert.company_id, "OWN_COMPANY", None)
    ]

    if not alert.network_broadcast or alert.severity == SosSeverity.MODERATE.value:
        return audience

    result = await session.execute(
        select(Truck.company_id, TruckLiveState.latitude, TruckLiveState.longitude)
        .join(TruckLiveState, TruckLiveState.truck_id == Truck.id)
        .where(Truck.company_id != alert.company_id)
        .where(TruckLiveState.latitude.is_not(None))
    )

    nearest: Dict[str, float] = {}
    for company_id, lat, lng in result.all():
        km = haversine_distance_km(alert.latitude, alert.longitude, lat, lng)
        if km <= alert.broadcast_radius_km:
            if company_id not in nearest or km < nearest[company_id]:
                nearest[company_id] = km

    for company_id, km in sorted(nearest.items(), key=lambda kv: kv[1]):
        audience.append((company_id, "NETWORK", round(km, 2)))

    return audience


# ---------------------------------------------------------------------------
# Projections -- two audiences, two builders
# ---------------------------------------------------------------------------


def full_view(alert: SosAlert, responses: Optional[List[SosResponse]] = None) -> Dict[str, Any]:
    """Everything, for the reporting company and platform operations."""
    return {
        "id": alert.id,
        "category": alert.category,
        "severity": alert.severity,
        "status": alert.status,
        "companyId": alert.company_id,
        "driverId": alert.driver_id,
        "truckId": alert.truck_id,
        "latitude": alert.latitude,
        "longitude": alert.longitude,
        "accuracyM": alert.accuracy_m,
        "locationSource": alert.location_source,
        "landmarkNote": alert.landmark_note,
        "condition": {
            "personsAffected": alert.persons_affected,
            "isConscious": alert.is_conscious,
            "isBreathing": alert.is_breathing,
            "isTrapped": alert.is_trapped,
            "isMobile": alert.is_mobile,
            "severeBleeding": alert.severe_bleeding,
            "note": alert.condition_note,
            "vitals": alert.vitals_json,
        },
        "silentMode": alert.silent_mode,
        "ackCount": alert.ack_count,
        "responderCount": alert.responder_count,
        "reportedAt": alert.reported_at.isoformat() if alert.reported_at else None,
        "firstAckAt": alert.first_ack_at.isoformat() if alert.first_ack_at else None,
        "resolvedAt": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolutionCode": alert.resolution_code,
        "responses": [_response_view(r) for r in (responses or [])],
        # Restated on every payload so no client can present this as a
        # dispatch to the emergency services.
        "psapDispatched": False,
        "psapIntegration": "none",
        "emergencyNumbers": EMERGENCY_NUMBERS,
    }


def redacted_view(
    alert: SosAlert, distance_km: Optional[float] = None
) -> Dict[str, Any]:
    """What a nearby carrier sees before they have acknowledged.

    Built from scratch rather than by removing fields, so there is nowhere to
    accidentally leave a driver's name or a cargo value.
    """
    return {
        "sosId": alert.id,
        "category": alert.category,
        "severity": alert.severity,
        "status": alert.status,
        "approxLat": round(alert.latitude, REDACTED_COORD_PLACES),
        "approxLng": round(alert.longitude, REDACTED_COORD_PLACES),
        "locationPrecision": "~1km",
        "distanceKm": distance_km,
        "landmarkNote": alert.landmark_note,
        "personsAffected": alert.persons_affected,
        "assistanceNeeded": _assistance_needed(alert),
        "reportedAt": alert.reported_at.isoformat() if alert.reported_at else None,
        "ackCount": alert.ack_count,
        "psapDispatched": False,
        "emergencyNumbers": EMERGENCY_NUMBERS,
    }


def _assistance_needed(alert: SosAlert) -> List[str]:
    """What kind of help would actually be useful, without saying why."""
    needs = {
        SosCategory.MEDICAL.value: ["MEDICAL_TRANSPORT", "FIRST_AID"],
        SosCategory.ACCIDENT.value: ["MEDICAL_TRANSPORT", "SCENE_ASSISTANCE"],
        SosCategory.FIRE.value: ["FIRE_SUPPRESSION", "EVACUATION"],
        SosCategory.POLICE_SECURITY.value: ["SECURITY_PRESENCE"],
        SosCategory.MECHANICAL.value: ["TOWING", "ROADSIDE_REPAIR"],
        SosCategory.CARGO_RISK.value: ["CARGO_TRANSFER", "COLD_STORAGE"],
    }
    return needs.get(alert.category, ["GENERAL_ASSISTANCE"])


def _response_view(response: SosResponse) -> Dict[str, Any]:
    return {
        "id": response.id,
        "companyId": response.responder_company_id,
        "action": response.action,
        "etaMinutes": response.eta_minutes,
        "distanceKm": response.distance_km,
        "note": response.note,
        "at": response.created_at.isoformat() if response.created_at else None,
    }


# ---------------------------------------------------------------------------
# Responding and resolving
# ---------------------------------------------------------------------------


async def acknowledge(
    session: AsyncSession,
    alert: SosAlert,
    company_id: str,
    actor_id: str,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> SosAlert:
    session.add(
        SosResponse(
            sos_id=alert.id,
            responder_company_id=company_id,
            action="ACKNOWLEDGE",
        )
    )
    alert.ack_count += 1
    if alert.first_ack_at is None:
        alert.first_ack_at = _now()
    if alert.status in {SosStatus.BROADCASTING.value, SosStatus.UNANSWERED.value}:
        _transition(alert, SosStatus.ACKNOWLEDGED)

    await _log_event(
        session, alert, "sos.acknowledged", "company", actor_id, None, alert.status, {}
    )
    await session.commit()
    await session.refresh(alert)
    if event_publisher:
        await _publish(event_publisher, "sos.acknowledged", alert, [])
    return alert


async def respond(
    session: AsyncSession,
    alert: SosAlert,
    company_id: str,
    actor_id: str,
    action: str,
    eta_minutes: Optional[float] = None,
    truck_id: Optional[str] = None,
    note: Optional[str] = None,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> SosAlert:
    distance = None
    if truck_id:
        live = await session.get(TruckLiveState, truck_id)
        if live and live.latitude is not None:
            distance = round(
                haversine_distance_km(
                    alert.latitude, alert.longitude, live.latitude, live.longitude
                ),
                2,
            )

    session.add(
        SosResponse(
            sos_id=alert.id,
            responder_company_id=company_id,
            responder_truck_id=truck_id,
            action=action,
            eta_minutes=eta_minutes,
            distance_km=distance,
            note=note,
        )
    )

    if action == "EN_ROUTE":
        alert.responder_count += 1
        if alert.status in {
            SosStatus.ACKNOWLEDGED.value,
            SosStatus.BROADCASTING.value,
            SosStatus.UNANSWERED.value,
        }:
            _transition(alert, SosStatus.RESPONDER_EN_ROUTE)
    elif action == "ON_SCENE":
        if alert.first_on_scene_at is None:
            alert.first_on_scene_at = _now()
        if alert.status in {
            SosStatus.RESPONDER_EN_ROUTE.value,
            SosStatus.ACKNOWLEDGED.value,
        }:
            _transition(alert, SosStatus.ON_SCENE)

    await _log_event(
        session, alert, f"sos.{action.lower()}", "company", actor_id, None, alert.status, {}
    )
    await session.commit()
    await session.refresh(alert)
    if event_publisher:
        await _publish(event_publisher, "sos.responder_update", alert, [])
    return alert


async def set_status(
    session: AsyncSession,
    alert: SosAlert,
    new_status: SosStatus,
    actor_type: str,
    actor_id: str,
    resolution_code: Optional[str] = None,
    resolution_note: Optional[str] = None,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> SosAlert:
    previous = alert.status
    _transition(alert, new_status)

    if new_status == SosStatus.RESOLVED:
        alert.resolved_at = _now()
        alert.resolution_code = resolution_code
        alert.resolution_note = resolution_note
    elif new_status == SosStatus.CLOSED:
        alert.closed_at = _now()

    await _log_event(
        session,
        alert,
        f"sos.{new_status.value.lower()}",
        actor_type,
        actor_id,
        previous,
        new_status.value,
        {"resolutionCode": resolution_code} if resolution_code else {},
    )
    await session.commit()
    await session.refresh(alert)
    if event_publisher:
        await _publish(event_publisher, "sos.status_changed", alert, [])
    return alert


def _transition(alert: SosAlert, new_status: SosStatus) -> None:
    current = SosStatus(alert.status)
    if new_status not in SOS_TRANSITIONS[current]:
        raise IllegalSosTransition(
            f"SOS is {current.value}; cannot move to {new_status.value}",
            sos_id=alert.id,
        )
    alert.status = new_status.value


async def cancel(
    session: AsyncSession,
    alert: SosAlert,
    actor_id: str,
    pin_ok: bool,
    reason: Optional[str],
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Tuple[SosAlert, bool]:
    """Cancel an SOS, if the driver can prove it is really them.

    Under duress the instruction a driver is given is "cancel it". A wrong PIN
    on a CRITICAL alert therefore does not cancel: it switches the alert to
    silent mode and escalates, while returning exactly what a successful
    cancellation returns, so the device can appear to comply.

    Returns (alert, actually_cancelled).
    """
    critical = alert.severity == SosSeverity.CRITICAL.value

    if not pin_ok and critical:
        alert.silent_mode = True
        if alert.status in {SosStatus.BROADCASTING.value, SosStatus.NEW.value}:
            alert.status = SosStatus.ESCALATED.value
        await _log_event(
            session,
            alert,
            "sos.duress_suspected",
            "driver",
            actor_id,
            None,
            alert.status,
            {"reason": "cancellation attempted without a valid PIN"},
        )
        await session.commit()
        await session.refresh(alert)
        logger.warn("sos_duress_cancel_refused", sos_id=alert.id)
        if event_publisher:
            await _publish(event_publisher, "sos.duress_suspected", alert, [])
        return alert, False

    if not pin_ok:
        raise SosError("Incorrect PIN", sos_id=alert.id)

    previous = alert.status
    alert.status = SosStatus.CANCELLED.value
    alert.resolution_code = "CANCELLED_BY_DRIVER"
    alert.resolution_note = reason
    alert.closed_at = _now()
    await _log_event(
        session, alert, "sos.cancelled", "driver", actor_id, previous, alert.status, {}
    )
    await session.commit()
    await session.refresh(alert)
    if event_publisher:
        await _publish(event_publisher, "sos.status_changed", alert, [])
    return alert, True


# ---------------------------------------------------------------------------
# Outbound adapters and escalation
# ---------------------------------------------------------------------------


async def fire_outbound_notifiers(
    session: AsyncSession,
    alert: SosAlert,
    targets: List[NotifyTarget],
) -> int:
    """Invoke every configured adapter, recording each attempt."""
    payload = redacted_view(alert)
    sent = 0
    for adapter in configured_notifiers():
        if not adapter.supports(alert.category, alert.severity):
            continue
        for target in targets or [NotifyTarget(kind="company", value=alert.company_id)]:
            result = await adapter.notify(payload, target)
            session.add(
                SosBroadcast(
                    sos_id=alert.id,
                    recipient_company_id=target.company_id or alert.company_id,
                    tier="EXTERNAL",
                    channel=target.kind.upper()[:16],
                    adapter_name=adapter.name,
                    redaction_level="REDACTED",
                    adapter_status=result.status,
                    adapter_ref=result.ref,
                    error=result.error,
                    delivered_at=_now() if result.status == "SENT" else None,
                )
            )
            if result.status == "SENT":
                sent += 1
    await session.commit()
    return sent


async def escalate_unanswered(
    session: AsyncSession,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> int:
    """Widen and escalate alerts nobody has acknowledged in time."""
    now = _now()
    result = await session.execute(
        select(SosAlert)
        .where(SosAlert.status == SosStatus.BROADCASTING.value)
        .where(SosAlert.auto_escalate_at.is_not(None))
        .where(SosAlert.auto_escalate_at < now)
        .where(SosAlert.ack_count == 0)
    )
    stale = list(result.scalars().all())

    for alert in stale:
        alert.status = SosStatus.UNANSWERED.value
        alert.broadcast_radius_km = min(alert.broadcast_radius_km * 2, 200.0)
        await _log_event(
            session,
            alert,
            "sos.unanswered",
            "system",
            "sweeper",
            SosStatus.BROADCASTING.value,
            SosStatus.UNANSWERED.value,
            {"newRadiusKm": alert.broadcast_radius_km},
        )
        await fire_outbound_notifiers(session, alert, [])
        logger.warn("sos_unanswered_escalated", sos_id=alert.id)
        if event_publisher:
            await _publish(event_publisher, "sos.escalated", alert, [])

    if stale:
        await session.commit()
    return len(stale)


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


async def _log_event(
    session: AsyncSession,
    alert: SosAlert,
    event_type: str,
    actor_type: str,
    actor_id: str,
    previous: Optional[str],
    new: Optional[str],
    metadata: Dict[str, Any],
) -> None:
    session.add(
        SosEvent(
            sos_id=alert.id,
            type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            previous_status=previous,
            new_status=new,
            metadata_json=metadata,
        )
    )


async def _publish(
    event_publisher: Callable[[str, dict], Awaitable[None]],
    event_type: str,
    alert: SosAlert,
    recipients: List[Tuple[str, str, Optional[float]]],
) -> None:
    """Emit an SOS event.

    The payload carries no condition or vitals data at all. The projection
    layer builds the owning company's full view by reading the record; what
    travels on the bus is only what the widest audience may see.
    """
    envelope = EventEnvelope(
        eventType=event_type,
        producer="sos",
        payload={
            "sosId": alert.id,
            "companyId": alert.company_id,
            "category": alert.category,
            "severity": alert.severity,
            "status": alert.status,
            "approxLat": round(alert.latitude, REDACTED_COORD_PLACES),
            "approxLng": round(alert.longitude, REDACTED_COORD_PLACES),
            "networkRecipients": [c for c, tier, _ in recipients if tier == "NETWORK"],
            "assistanceNeeded": _assistance_needed(alert),
            "ackCount": alert.ack_count,
        },
    )
    await event_publisher(event_type, envelope.to_dict())
