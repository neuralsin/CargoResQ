"""
Accepting telemetry from driver devices.

Batched, because a phone in a truck spends a lot of its life without signal
and has to queue. Idempotent, because a device that does not receive our
acknowledgement will retry the same batch -- deduplication is a unique index
on (device_id, client_ping_id) with an ON CONFLICT DO NOTHING insert, which
needs no read-then-write and cannot race with itself.

Ingest also maintains `truck_live_state`, which is what makes the fleet
actually move: matching reads that table, so until pings started landing here
the system ranked every truck by the coordinates it was registered with.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Driver, Shipment, Truck
from shared.database import IS_SQLITE
from shared.events import EventEnvelope
from shared.observability import logger

from .alerts import auto_resolve, record_finding
from .detectors import (
    detect_reefer_fault,
    detect_temperature_excursion,
    dwell_state,
    is_backfill,
    run_ingest_detectors,
)
from .models import AlertType, CargoReading, TelemetryPing, TruckLiveState

MAX_BATCH_SIZE = 500
#: A device clock running ahead is a bug or a spoof; either way, reject it.
MAX_CLOCK_SKEW_MINUTES = 5.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class IngestResult(Dict[str, Any]):
    pass


async def ingest_pings(
    session: AsyncSession,
    driver: Driver,
    device_id: str,
    pings: List[Dict[str, Any]],
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """Store a batch of position fixes and run live detection on the newest.

    Detection runs once, against the most recent live fix in the batch --
    running it for every ping in a replayed queue would produce a burst of
    alerts describing conditions that have long since passed.
    """
    if not driver.assigned_truck_id:
        return {"accepted": 0, "duplicates": 0, "rejected": [{"reason": "no_truck_assigned"}]}

    truck = await session.get(Truck, driver.assigned_truck_id)
    if truck is None or truck.company_id != driver.company_id:
        return {"accepted": 0, "duplicates": 0, "rejected": [{"reason": "truck_not_found"}]}

    received = _now()
    horizon = received + timedelta(minutes=MAX_CLOCK_SKEW_MINUTES)

    rows: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for index, raw in enumerate(pings[:MAX_BATCH_SIZE]):
        recorded = _aware(raw.get("recorded_at"))
        if recorded is None:
            rejected.append({"index": index, "reason": "missing recorded_at"})
            continue
        if recorded > horizon:
            rejected.append({"index": index, "reason": "recorded_at is in the future"})
            continue

        rows.append(
            {
                "truck_id": truck.id,
                "driver_id": driver.id,
                "company_id": driver.company_id,
                "shipment_id": raw.get("shipment_id"),
                "incident_id": raw.get("incident_id"),
                "device_id": device_id,
                "client_ping_id": str(raw["client_ping_id"]),
                "recorded_at": recorded,
                "received_at": received,
                "latitude": float(raw["latitude"]),
                "longitude": float(raw["longitude"]),
                "accuracy_m": raw.get("accuracy_m"),
                "speed_kph": raw.get("speed_kph"),
                "heading_deg": raw.get("heading_deg"),
                "altitude_m": raw.get("altitude_m"),
                "battery_pct": raw.get("battery_pct"),
                "is_charging": raw.get("is_charging"),
                "network_type": raw.get("network_type"),
                "provider": raw.get("provider"),
                "mock_location": bool(raw.get("mock_location", False)),
                "is_backfill": is_backfill(recorded, received),
                "app_version": raw.get("app_version"),
            }
        )

    if not rows:
        return {"accepted": 0, "duplicates": 0, "rejected": rejected}

    # A retried batch must not create duplicates. The unique index does the
    # work; the insert simply declines to fight it.
    before = await _count_for_device(session, device_id)
    if IS_SQLITE:
        stmt = sqlite_insert(TelemetryPing).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["device_id", "client_ping_id"])
    else:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(TelemetryPing).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["device_id", "client_ping_id"])
    await session.execute(stmt)
    await session.commit()

    after = await _count_for_device(session, device_id)
    accepted = after - before
    duplicates = len(rows) - accepted

    live_rows = [r for r in rows if not r["is_backfill"]]
    findings_raised = 0
    if live_rows:
        newest = max(live_rows, key=lambda r: r["recorded_at"])
        findings_raised = await _apply_live_state(
            session, truck, driver, newest, event_publisher
        )

    logger.info(
        "telemetry_batch_ingested",
        truck_id=truck.id,
        accepted=accepted,
        duplicates=duplicates,
        backfill=len(rows) - len(live_rows),
    )

    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "backfilled": len(rows) - len(live_rows),
        "alertsRaised": findings_raised,
    }


async def _count_for_device(session: AsyncSession, device_id: str) -> int:
    from sqlalchemy import func

    return (
        await session.scalar(
            select(func.count())
            .select_from(TelemetryPing)
            .where(TelemetryPing.device_id == device_id)
        )
    ) or 0


async def _apply_live_state(
    session: AsyncSession,
    truck: Truck,
    driver: Driver,
    ping: Dict[str, Any],
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]],
) -> int:
    """Update the truck's current state and run on-ingest detectors."""
    live = await session.get(TruckLiveState, truck.id)
    previous: Optional[Dict[str, Any]] = None

    if live is None:
        live = TruckLiveState(truck_id=truck.id, company_id=truck.company_id)
        session.add(live)
    elif live.latitude is not None:
        previous = {
            "latitude": live.latitude,
            "longitude": live.longitude,
            "recorded_at": live.last_recorded_at,
        }

    findings = run_ingest_detectors(ping, previous)

    # A fix that looks spoofed or physically impossible is recorded and
    # flagged, not discarded.
    #
    # Dropping it meant the truck froze on the dispatcher's map at its last
    # believed position. On an emulator, or any handset with developer options
    # enabled, Android reports every fix as mocked -- so the truck stopped
    # moving for good, while the operator had no idea the feed had stalled.
    # A flagged position the dispatcher can question beats a stale one they
    # cannot.
    suspect_finding = next(
        (
            f
            for f in findings
            if f.alert_type in {AlertType.IMPOSSIBLE_JUMP, AlertType.MOCK_LOCATION}
        ),
        None,
    )

    moving, dwell_started, anchor_lat, anchor_lng = dwell_state(
        live.latitude, live.longitude, live.dwell_started_at, ping
    )
    was_dwelling = live.dwell_started_at is not None

    live.latitude = ping["latitude"]
    live.longitude = ping["longitude"]
    live.speed_kph = ping.get("speed_kph")
    live.heading_deg = ping.get("heading_deg")
    live.is_moving = moving
    live.dwell_started_at = dwell_started
    live.dwell_anchor_lat = anchor_lat
    live.dwell_anchor_lng = anchor_lng
    live.position_suspect = suspect_finding is not None
    live.suspect_reason = suspect_finding.alert_type.value if suspect_finding else None

    truck.latitude = ping["latitude"]
    truck.longitude = ping["longitude"]
    position_updated = True

    if moving and was_dwelling:
        await auto_resolve(
            session, truck.id, AlertType.DWELL, "Truck started moving again", commit=False
        )

    live.last_ping_id = None
    live.last_recorded_at = ping["recorded_at"]
    live.last_received_at = ping["received_at"]
    live.battery_pct = ping.get("battery_pct")
    live.is_charging = ping.get("is_charging")

    # A ping arriving is itself the resolution of a staleness alert.
    await auto_resolve(
        session, truck.id, AlertType.GPS_STALE, "Telemetry resumed", commit=False
    )

    if position_updated and event_publisher:
        try:
            rec_at = ping.get("recorded_at")
            await event_publisher(
                "telemetry.location",
                {
                    "companyId": truck.company_id,
                    "truckId": truck.id,
                    "driverId": driver.id,
                    "latitude": float(ping["latitude"]),
                    "longitude": float(ping["longitude"]),
                    "speedKph": ping.get("speed_kph"),
                    "headingDeg": ping.get("heading_deg"),
                    "batteryPct": ping.get("battery_pct"),
                    "registrationNumber": truck.registration_number,
                    # Carried through so the dispatcher's map can mark the
                    # position as unverified rather than silently trusting it.
                    "positionSuspect": bool(live.position_suspect),
                    "suspectReason": live.suspect_reason,
                    "recordedAt": rec_at.isoformat() if hasattr(rec_at, "isoformat") else str(rec_at),
                },
            )
        except Exception:
            pass

    for finding in findings:
        alert, should_notify = await record_finding(
            session,
            finding,
            company_id=truck.company_id,
            truck_id=truck.id,
            driver_id=driver.id,
            commit=False,
        )
        if should_notify and event_publisher:
            await _publish_alert(event_publisher, alert)

    await session.commit()
    return len(findings)



async def _may_report_condition(
    session: AsyncSession, driver: Driver, shipment: Shipment
) -> bool:
    """Whether this driver is entitled to record this cargo's condition.

    Normally that means their own company's load. But during a cross-carrier
    rescue the cargo is physically in *another* company's truck, and that
    driver is the only person who can read the gauge.

    Refusing them was a real hole: on the exact scenario this product exists
    for, no condition data could ever be recorded, so verification always
    concluded INSUFFICIENT_DATA and the escrow could never release. The
    carrier did the work and the funds stayed stuck.

    Permission is scoped to a rescue that is actually bound -- being offered
    a job, or having finished one, is not authority to write to its log.
    """
    if shipment.owner_company_id == driver.company_id:
        return True

    from services.orchestrator.app.offer_models import OfferState, RescueOffer

    bound = await session.execute(
        select(RescueOffer)
        .where(RescueOffer.shipment_id == shipment.id)
        .where(RescueOffer.carrier_company_id == driver.company_id)
        .where(RescueOffer.state == OfferState.BOUND.value)
    )
    offer = bound.scalars().first()
    if offer is None:
        return False

    # And only the driver whose truck is on the job.
    if offer.carrier_truck_id and driver.assigned_truck_id:
        return offer.carrier_truck_id == driver.assigned_truck_id
    return True


async def ingest_readings(
    session: AsyncSession,
    driver: Driver,
    sensor_id: str,
    readings: List[Dict[str, Any]],
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """Store cargo-condition observations and check them against the spec."""
    received = _now()
    truck_id = driver.assigned_truck_id

    rows: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    shipments: Dict[str, Shipment] = {}

    for index, raw in enumerate(readings[:MAX_BATCH_SIZE]):
        shipment_id = raw.get("shipment_id")
        if not shipment_id:
            rejected.append({"index": index, "reason": "missing shipment_id"})
            continue

        shipment = shipments.get(shipment_id)
        if shipment is None:
            shipment = await session.get(Shipment, shipment_id)
            if shipment is None or not await _may_report_condition(
                session, driver, shipment
            ):
                rejected.append({"index": index, "reason": "shipment not found"})
                continue
            shipments[shipment_id] = shipment

        recorded = _aware(raw.get("recorded_at"))
        if recorded is None:
            rejected.append({"index": index, "reason": "missing recorded_at"})
            continue

        rows.append(
            {
                "shipment_id": shipment_id,
                "truck_id": truck_id,
                "company_id": driver.company_id,
                "incident_id": raw.get("incident_id"),
                "sensor_id": sensor_id,
                "client_reading_id": str(raw["client_reading_id"]),
                "recorded_at": recorded,
                "received_at": received,
                "temperature_c": raw.get("temperature_c"),
                "humidity_pct": raw.get("humidity_pct"),
                "door_open": raw.get("door_open"),
                "reefer_state": raw.get("reefer_state"),
                "reefer_setpoint_c": raw.get("reefer_setpoint_c"),
                "sensor_battery_pct": raw.get("sensor_battery_pct"),
                # A driver typing a number into a phone is evidence, but not
                # the same evidence as a signed feed from a telematics unit.
                "source": raw.get("source", "device"),
                "trust_level": "UNVERIFIED",
                "is_backfill": is_backfill(recorded, received),
            }
        )

    if not rows:
        return {"accepted": 0, "duplicates": 0, "rejected": rejected}

    from sqlalchemy import func

    before = (
        await session.scalar(
            select(func.count())
            .select_from(CargoReading)
            .where(CargoReading.sensor_id == sensor_id)
        )
    ) or 0

    if IS_SQLITE:
        stmt = sqlite_insert(CargoReading).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["sensor_id", "client_reading_id"])
    else:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(CargoReading).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["sensor_id", "client_reading_id"])
    await session.execute(stmt)
    await session.commit()

    after = (
        await session.scalar(
            select(func.count())
            .select_from(CargoReading)
            .where(CargoReading.sensor_id == sensor_id)
        )
    ) or 0
    accepted = after - before

    alerts_raised = 0
    live = await session.get(TruckLiveState, truck_id) if truck_id else None
    for row in rows:
        if row["is_backfill"]:
            continue
        shipment = shipments[row["shipment_id"]]
        for finding in (
            detect_temperature_excursion(row, shipment),
            detect_reefer_fault(row),
        ):
            if finding is None:
                continue
            alert, should_notify = await record_finding(
                session,
                finding,
                company_id=driver.company_id,
                truck_id=truck_id or shipment.truck_id or "",
                driver_id=driver.id,
                shipment_id=shipment.id,
                commit=False,
            )
            alerts_raised += 1
            if should_notify and event_publisher:
                await _publish_alert(event_publisher, alert)

        if row.get("temperature_c") is not None and live is not None:
            live.last_temperature_c = row["temperature_c"]
            live.last_reading_at = row["recorded_at"]

    await session.commit()
    return {
        "accepted": accepted,
        "duplicates": len(rows) - accepted,
        "rejected": rejected,
        "alertsRaised": alerts_raised,
    }


async def _publish_alert(
    event_publisher: Callable[[str, dict], Awaitable[None]], alert
) -> None:
    envelope = EventEnvelope(
        eventType="telemetry.alert",
        producer="telemetry",
        payload={
            "alertId": alert.id,
            "companyId": alert.company_id,
            "truckId": alert.truck_id,
            "shipmentId": alert.shipment_id,
            "alertType": alert.alert_type,
            "severity": alert.severity,
            "occurrenceCount": alert.occurrence_count,
            "detail": alert.last_value_json,
        },
    )
    await event_publisher("telemetry.alert", envelope.to_dict())
