"""
Incident Orchestrator Core Engine (Phase 11.3).
Coordinates lifecycle state progression, audit trail logging, and event fan-out.
"""
from typing import Optional, Dict, Any, Callable, Awaitable, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from .models import Incident, IncidentEvent, IncidentState, TRANSITIONS
from shared.events import EventEnvelope
from shared.observability import logger


class IllegalTransition(Exception):
    pass


class IncidentNotFound(Exception):
    pass


async def create_incident(
    session: AsyncSession,
    shipment_id: str,
    lat: float,
    lng: float,
    minutes_until_spoilage: Optional[float] = None,
    actor_id: Optional[str] = None,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Incident:
    incident = Incident(
        shipment_id=shipment_id,
        state=IncidentState.BREAKDOWN_REPORTED,
        lat=lat,
        lng=lng,
        minutes_until_spoilage=minutes_until_spoilage,
    )
    session.add(incident)
    await session.flush()

    # Log initial breakdown event
    audit_event = IncidentEvent(
        incident_id=incident.id,
        type="incident.breakdown_reported",
        actor_id=actor_id,
        previous_state=IncidentState.NORMAL.value,
        new_state=IncidentState.BREAKDOWN_REPORTED.value,
        metadata_json={
            "shipmentId": shipment_id,
            "lat": lat,
            "lng": lng,
            "minutesUntilSpoilage": minutes_until_spoilage,
        },
    )
    session.add(audit_event)
    await session.commit()
    await session.refresh(incident)

    logger.info("incident_created", incident_id=incident.id, shipment_id=shipment_id)

    if event_publisher:
        envelope = EventEnvelope(
            eventType="incident.breakdown_reported",
            producer="orchestrator",
            payload={
                "incidentId": incident.id,
                "shipmentId": shipment_id,
                "lat": lat,
                "lng": lng,
                "minutesUntilSpoilage": minutes_until_spoilage,
                "state": incident.state.value,
            },
        )
        await event_publisher("incident.breakdown_reported", envelope.to_dict())

    return incident


async def advance(
    session: AsyncSession,
    incident_id: str,
    new_state: IncidentState,
    actor_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    commit: bool = True,
) -> Incident:
    """Validate and advance the incident lifecycle.

    The state update and its append-only audit event are always written
    together. Pass ``commit=False`` to fold this into a larger unit of work --
    binding a rescue has to move the incident, create the escrow and expire
    the sibling offers atomically, and a commit in the middle of that would
    leave a half-bound rescue behind if the rest failed.
    """
    incident = await session.get(Incident, incident_id)
    if not incident:
        raise IncidentNotFound(f"Incident {incident_id} not found")

    allowed_states = TRANSITIONS.get(incident.state, set())
    if new_state not in allowed_states:
        raise IllegalTransition(
            f"Illegal lifecycle transition: {incident.state.value} -> {new_state.value} not allowed. "
            f"Valid next states: {[s.value for s in allowed_states]}"
        )

    previous_state = incident.state
    incident.state = new_state

    # Update assigned truck if present in metadata
    if metadata and "assignedTruckId" in metadata:
        incident.assigned_truck_id = metadata["assignedTruckId"]

    audit_event = IncidentEvent(
        incident_id=incident_id,
        type=f"incident.{new_state.value.lower()}",
        actor_id=actor_id,
        previous_state=previous_state.value,
        new_state=new_state.value,
        metadata_json=metadata or {},
    )
    session.add(audit_event)
    if commit:
        await session.commit()
        await session.refresh(incident)
    else:
        await session.flush()

    logger.info(
        "incident_advanced",
        incident_id=incident_id,
        prev=previous_state.value,
        new=new_state.value,
        actor=actor_id,
    )

    if event_publisher:
        envelope = EventEnvelope(
            eventType=f"incident.{new_state.value.lower()}",
            producer="orchestrator",
            payload={
                "incidentId": incident_id,
                "previousState": previous_state.value,
                "newState": new_state.value,
                **(metadata or {}),
            },
        )
        await event_publisher(f"incident.{new_state.value.lower()}", envelope.to_dict())

    return incident


async def get_incident_timeline(session: AsyncSession, incident_id: str) -> List[IncidentEvent]:
    stmt = (
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.created_at.asc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())
