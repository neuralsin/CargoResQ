"""
The background sweeper.

Everything here is about elapsed time or the absence of data -- conditions no
request can detect, because the signal is that nothing happened. One task, one
tick, all the periodic work: staleness, dwell, offer expiry, alert
notification cooldowns.

Deliberately a single task rather than one per concern. With several the tick
work would interleave unpredictably and each would need its own failure
handling; with one, an exception in any sweep is caught in the same place and
the loop survives it. A sweeper that dies quietly is worse than no sweeper,
because the absence of alerts then looks like the absence of problems.
"""
import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.orchestrator.app.models import Incident, IncidentState
from services.orchestrator.app.offer_service import expire_due_offers
from shared.database import async_session
from shared.observability import logger

from .alerts import record_finding
from .detectors import detect_dwell, detect_gps_stale
from .ingest import _publish_alert
from .models import TruckLiveState

TICK_SECONDS = 30.0

#: Incident states in which a truck is expected to be actively watched, so
#: silence and immobility matter much sooner.
ACTIVE_INCIDENT_STATES = {
    IncidentState.BREAKDOWN_REPORTED,
    IncidentState.TRIAGING,
    IncidentState.MATCHING,
    IncidentState.RESCUE_OFFERED,
    IncidentState.RESCUE_ACCEPTED,
    IncidentState.DRIVER_EN_ROUTE,
    IncidentState.CARGO_TRANSFER,
    IncidentState.RESCUE_IN_TRANSIT,
}


async def sweep_once(
    session: AsyncSession,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> dict:
    """One pass. Returns a summary, mostly so tests can assert on it."""
    now = datetime.now(timezone.utc)

    trucks_with_incidents = await _trucks_with_active_incidents(session)

    result = await session.execute(select(TruckLiveState))
    states = list(result.scalars().all())

    stale_found = 0
    dwell_found = 0

    for state in states:
        active = state.truck_id in trucks_with_incidents

        stale = detect_gps_stale(state.truck_id, state.last_received_at, now, active)
        if stale is not None:
            alert, notify = await record_finding(
                session,
                stale,
                company_id=state.company_id,
                truck_id=state.truck_id,
                commit=False,
            )
            stale_found += 1
            if notify and event_publisher:
                await _publish_alert(event_publisher, alert)

        dwell = detect_dwell(state.truck_id, state.dwell_started_at, now, active)
        if dwell is not None:
            alert, notify = await record_finding(
                session,
                dwell,
                company_id=state.company_id,
                truck_id=state.truck_id,
                commit=False,
            )
            dwell_found += 1
            if notify and event_publisher:
                await _publish_alert(event_publisher, alert)

    await session.commit()

    expired = await expire_due_offers(session, event_publisher)

    return {
        "trucksChecked": len(states),
        "staleDetected": stale_found,
        "dwellDetected": dwell_found,
        "offersExpired": expired,
    }


async def _trucks_with_active_incidents(session: AsyncSession) -> set:
    from services.core_api.app.models import Shipment

    result = await session.execute(
        select(Shipment.truck_id)
        .join(Incident, Incident.shipment_id == Shipment.id)
        .where(Incident.state.in_(list(ACTIVE_INCIDENT_STATES)))
        .where(Shipment.truck_id.is_not(None))
    )
    return {row[0] for row in result.all()}


async def run_sweeper(
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    tick_seconds: float = TICK_SECONDS,
) -> None:
    """Loop forever, sweeping.

    Each tick is wrapped so that one bad pass cannot end the loop.
    """
    logger.info("telemetry_sweeper_started", tick_seconds=tick_seconds)
    while True:
        try:
            async with async_session() as session:
                summary = await sweep_once(session, event_publisher)
            if any(v for k, v in summary.items() if k != "trucksChecked"):
                logger.info("telemetry_sweep", **summary)
        except asyncio.CancelledError:
            logger.info("telemetry_sweeper_stopped")
            raise
        except Exception as exc:
            logger.error("telemetry_sweep_failed", error=str(exc))
        await asyncio.sleep(tick_seconds)
