"""
Recording alerts as episodes.

The deduplication contract, in three layers:

1. an episode is identified by `dedupe_key`, which names the *occurrence*
   rather than the observation;
2. a finding for an already-open episode updates that row instead of
   inserting a new one, so six hours of dwell is one row;
3. a human is notified at most once per cooldown, and immediately on
   escalation, so detection frequency and notification frequency are
   independent.

Auto-resolution matters as much as opening: a resolved alert leaves the
partial unique index, which is what lets a genuinely new episode open later
under the same key shape.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.observability import logger

from .detectors import Finding
from .models import AlertSeverity, AlertStatus, AlertType, TelemetryAlert

SEVERITY_RANK = {
    AlertSeverity.INFO.value: 0,
    AlertSeverity.WARN.value: 1,
    AlertSeverity.CRITICAL.value: 2,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def record_finding(
    session: AsyncSession,
    finding: Finding,
    *,
    company_id: str,
    truck_id: str,
    driver_id: Optional[str] = None,
    shipment_id: Optional[str] = None,
    incident_id: Optional[str] = None,
    commit: bool = True,
) -> Tuple[TelemetryAlert, bool]:
    """Open or update an alert episode.

    Returns (alert, should_notify). `should_notify` is the throttling
    decision, deliberately separate from whether anything was recorded.
    """
    now = _now()

    existing = await session.execute(
        select(TelemetryAlert)
        .where(TelemetryAlert.dedupe_key == finding.dedupe_key)
        .where(
            TelemetryAlert.status.in_(
                [AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value]
            )
        )
    )
    alert = existing.scalars().first()

    if alert is None:
        alert = TelemetryAlert(
            company_id=company_id,
            truck_id=truck_id,
            driver_id=driver_id,
            shipment_id=shipment_id,
            incident_id=incident_id,
            status=AlertStatus.OPEN.value,
            opened_at=now,
            last_seen_at=now,
            occurrence_count=1,
            peak_severity=finding.severity.value,
            first_value_json=finding.values,
            last_value_json=finding.values,
            last_notified_at=now,
            **finding.as_alert_fields(),
        )
        session.add(alert)
        if commit:
            await session.commit()
            await session.refresh(alert)
        else:
            await session.flush()
        logger.info(
            "telemetry_alert_opened",
            alert_type=finding.alert_type.value,
            truck_id=truck_id,
            severity=finding.severity.value,
        )
        return alert, True

    # Same episode, seen again.
    alert.last_seen_at = now
    alert.occurrence_count += 1
    alert.last_value_json = finding.values

    escalated = SEVERITY_RANK[finding.severity.value] > SEVERITY_RANK[alert.peak_severity]
    if escalated:
        alert.severity = finding.severity.value
        alert.peak_severity = finding.severity.value

    last_notified = _aware(alert.last_notified_at)
    cooled_down = last_notified is None or (
        now - last_notified
    ) >= timedelta(seconds=alert.notify_cooldown_s)

    should_notify = escalated or cooled_down
    if should_notify:
        alert.last_notified_at = now

    if commit:
        await session.commit()
        await session.refresh(alert)
    else:
        await session.flush()
    return alert, should_notify


async def auto_resolve(
    session: AsyncSession,
    truck_id: str,
    alert_type: AlertType,
    note: str,
    commit: bool = True,
) -> int:
    """Close open episodes of one type for one truck.

    Called when the underlying condition clears -- the truck moves, the ping
    arrives, the temperature comes back into range. Resolving drops the row
    out of the partial unique index so a later recurrence is a new episode.
    """
    now = _now()
    result = await session.execute(
        select(TelemetryAlert)
        .where(TelemetryAlert.truck_id == truck_id)
        .where(TelemetryAlert.alert_type == alert_type.value)
        .where(
            TelemetryAlert.status.in_(
                [AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value]
            )
        )
    )
    alerts = list(result.scalars().all())
    for alert in alerts:
        alert.status = AlertStatus.AUTO_RESOLVED.value
        alert.resolved_at = now
        alert.resolution_note = note

    if alerts and commit:
        await session.commit()
    elif alerts:
        await session.flush()

    if alerts:
        logger.info(
            "telemetry_alerts_auto_resolved",
            truck_id=truck_id,
            alert_type=alert_type.value,
            count=len(alerts),
        )
    return len(alerts)


async def list_alerts(
    session: AsyncSession,
    company_id: str,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    alert_type: Optional[str] = None,
    truck_id: Optional[str] = None,
    limit: int = 50,
) -> List[TelemetryAlert]:
    stmt = select(TelemetryAlert).where(TelemetryAlert.company_id == company_id)
    if status:
        stmt = stmt.where(TelemetryAlert.status == status)
    if severity:
        stmt = stmt.where(TelemetryAlert.severity == severity)
    if alert_type:
        stmt = stmt.where(TelemetryAlert.alert_type == alert_type)
    if truck_id:
        stmt = stmt.where(TelemetryAlert.truck_id == truck_id)
    stmt = stmt.order_by(TelemetryAlert.opened_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


def alert_view(alert: TelemetryAlert) -> Dict[str, Any]:
    return {
        "id": alert.id,
        "type": alert.alert_type,
        "severity": alert.severity,
        "peakSeverity": alert.peak_severity,
        "status": alert.status,
        "truckId": alert.truck_id,
        "shipmentId": alert.shipment_id,
        "incidentId": alert.incident_id,
        "openedAt": alert.opened_at.isoformat() if alert.opened_at else None,
        "lastSeenAt": alert.last_seen_at.isoformat() if alert.last_seen_at else None,
        # Shown because it is the difference between "the truck paused" and
        # "the truck has been stationary for six hours".
        "occurrenceCount": alert.occurrence_count,
        "firstValue": alert.first_value_json,
        "lastValue": alert.last_value_json,
        "acknowledgedAt": (
            alert.acknowledged_at.isoformat() if alert.acknowledged_at else None
        ),
        "resolvedAt": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolutionNote": alert.resolution_note,
    }
