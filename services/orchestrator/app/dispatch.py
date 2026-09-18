"""
Keeping the money in step with the rescue.

An incident and its escrow describe the same real-world event from two angles:
where the cargo is, and where the payment is. They are separate state machines
because they answer to different rules -- one to physical progress, the other
to accounting -- but they must never disagree about what has happened.

Making a dispatcher drive both by hand is how they drift. Somebody marks a
load delivered, forgets the escrow, and a carrier who did the work is left
looking at funds stuck in IN_TRANSIT with no way to claim them. So the escrow
follows the incident automatically, and the one transition that cannot be
automatic -- releasing the money -- is driven by recorded evidence instead.

The mapping:

    incident RESCUE_IN_TRANSIT  ->  escrow IN_TRANSIT
    incident DELIVERED          ->  escrow PENDING_VERIFICATION
    incident VERIFICATION       ->  escrow PENDING_VERIFICATION
    incident CANCELLED          ->  escrow CANCELLED  (hold reversed)

    escrow RELEASED             ->  incident ESCROW_RELEASED
    escrow DISPUTED             ->  incident DISPUTED

Settlement runs the other way on purpose. The escrow decides, from the
condition log, whether the cargo arrived in spec; the incident then records
what the escrow concluded. Letting a dispatcher set ESCROW_RELEASED directly
would be letting the party who owes the money mark their own homework.
"""
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.escrow_ledger.app.escrow_service import (
    IllegalTransition as EscrowIllegalTransition,
    transition as escrow_transition,
)
from services.escrow_ledger.app.models import Escrow
from shared.observability import logger

from .models import Incident, IncidentState

#: Which escrow state each incident state implies.
#:
#: Only forward-moving, non-settling states appear here. An escrow that is
#: already past the mapped state is left alone rather than dragged backwards.
INCIDENT_TO_ESCROW = {
    IncidentState.RESCUE_IN_TRANSIT: "IN_TRANSIT",
    IncidentState.DELIVERED: "PENDING_VERIFICATION",
    IncidentState.VERIFICATION: "PENDING_VERIFICATION",
    IncidentState.CANCELLED: "CANCELLED",
}

#: How far along the escrow lifecycle each state is, so a sync can tell
#: whether it would be moving forward or backwards.
_ESCROW_ORDER = {
    "INITIATED": 0,
    "ACCEPTED": 1,
    "IN_TRANSIT": 2,
    "PENDING_VERIFICATION": 3,
    "RELEASED": 4,
    "DISPUTED": 4,
    "CANCELLED": 4,
}


async def escrow_for_incident(
    session: AsyncSession, incident_id: str
) -> Optional[Escrow]:
    """The escrow holding this rescue's money, if one has been opened."""
    result = await session.execute(
        select(Escrow)
        .where(Escrow.incident_id == incident_id)
        .order_by(Escrow.created_at.desc())
    )
    return result.scalars().first()


async def sync_escrow_to_incident(
    session: AsyncSession,
    incident: Incident,
    reason: Optional[str] = None,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    commit: bool = True,
) -> Optional[Escrow]:
    """Move the escrow to match the incident, if it needs moving.

    Returns the escrow when it changed, else None. Deliberately forgiving:
    a rescue whose escrow cannot follow is still a rescue, and blocking the
    physical progress of a load because of a bookkeeping edge case would be
    the wrong trade.
    """
    target = INCIDENT_TO_ESCROW.get(incident.state)
    if target is None:
        return None

    escrow = await escrow_for_incident(session, incident.id)
    if escrow is None:
        return None

    current_rank = _ESCROW_ORDER.get(escrow.state, 0)
    target_rank = _ESCROW_ORDER.get(target, 0)
    if target_rank <= current_rank:
        # Already there, or further on. Nothing to do.
        return None

    # Walk the escrow forward one legal step at a time. Going from ACCEPTED
    # straight to PENDING_VERIFICATION is not a legal single transition, and
    # a rescue that was delivered did pass through transit.
    path = ["IN_TRANSIT", "PENDING_VERIFICATION"]
    moved = False
    for step in path:
        if _ESCROW_ORDER[step] <= _ESCROW_ORDER.get(escrow.state, 0):
            continue
        if _ESCROW_ORDER[step] > target_rank:
            break
        try:
            escrow = await escrow_transition(
                session,
                escrow.id,
                step,
                reason=reason,
                event_publisher=event_publisher,
                commit=False,
            )
            moved = True
        except EscrowIllegalTransition as exc:
            logger.warn(
                "escrow_sync_skipped",
                incident_id=incident.id,
                escrow_id=escrow.id,
                wanted=step,
                error=str(exc),
            )
            break

    if target == "CANCELLED":
        try:
            escrow = await escrow_transition(
                session,
                escrow.id,
                "CANCELLED",
                reason=reason or "Rescue cancelled",
                event_publisher=event_publisher,
                commit=False,
            )
            moved = True
        except EscrowIllegalTransition as exc:
            logger.warn(
                "escrow_cancel_skipped", escrow_id=escrow.id, error=str(exc)
            )

    if not moved:
        return None

    if commit:
        await session.commit()
        await session.refresh(escrow)
    else:
        await session.flush()

    logger.info(
        "escrow_synced_to_incident",
        incident_id=incident.id,
        incident_state=incident.state.value,
        escrow_id=escrow.id,
        escrow_state=escrow.state,
    )
    return escrow


async def record_settlement_on_incident(
    session: AsyncSession,
    escrow: Escrow,
    actor_id: str,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Optional[Incident]:
    """Record on the incident what the escrow concluded.

    Called after verification has settled the money. The incident is the
    operational record, so it has to end up saying the same thing the ledger
    says -- an incident still reading VERIFICATION next to a released escrow
    is the kind of disagreement that costs somebody an afternoon.
    """
    if not escrow.incident_id:
        return None
    if escrow.state not in {"RELEASED", "DISPUTED"}:
        return None

    incident = await session.get(Incident, escrow.incident_id)
    if incident is None:
        return None

    target = (
        IncidentState.ESCROW_RELEASED
        if escrow.state == "RELEASED"
        else IncidentState.DISPUTED
    )
    if incident.state == target:
        return incident

    # Import here: orchestrator imports this module for the forward sync, and
    # a module-level import each way would not resolve.
    from .models import TRANSITIONS
    from .orchestrator import advance

    # Verification settles from DELIVERED or VERIFICATION. If the incident is
    # only at DELIVERED, step it through VERIFICATION first so the audit trail
    # shows the check happening rather than a jump.
    if target not in TRANSITIONS.get(incident.state, set()):
        if IncidentState.VERIFICATION in TRANSITIONS.get(incident.state, set()):
            incident = await advance(
                session,
                incident.id,
                IncidentState.VERIFICATION,
                actor_id=actor_id,
                metadata={"escrowId": escrow.id},
                event_publisher=event_publisher,
                commit=False,
            )

    if target not in TRANSITIONS.get(incident.state, set()):
        logger.warn(
            "incident_settlement_not_recordable",
            incident_id=incident.id,
            state=incident.state.value,
            wanted=target.value,
        )
        return incident

    incident = await advance(
        session,
        incident.id,
        target,
        actor_id=actor_id,
        metadata={"escrowId": escrow.id, "escrowState": escrow.state},
        event_publisher=event_publisher,
        commit=False,
    )
    await session.commit()
    await session.refresh(incident)
    return incident
