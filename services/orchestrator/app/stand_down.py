"""
Taking back a breakdown.

A driver presses the breakdown button from the side of a road, often before
they know what is actually wrong. Sometimes it restarts. Sometimes it was the
wrong button. Until now the only way out of that was to reseed the database,
which is not a thing a driver can do at 2am on a highway.

Standing down is deliberately not the same as cancelling a rescue. It means
"there was never a rescue to do here": the cargo is still on its original
truck, nobody has touched it, and the shipment goes back to being in transit.
That is only true up to the moment the cargo is physically moved, so the cut
is drawn at CARGO_TRANSFER. Past that point the cargo is in somebody else's
trailer and the situation has to be resolved, not erased -- there is money
held, a carrier part-way through a job, and a load that is no longer where the
paperwork says it is.

Everything the breakdown set in motion is unwound in one transaction: live
offers are withdrawn so carriers stop holding capacity for a job that is not
happening, any bound rescue releases its truck and reverses its escrow hold,
the incident closes as CANCELLED, and the shipment returns to in_transit.
"""
from typing import Any, Awaitable, Callable, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Shipment, Truck, TruckStatus
from services.escrow_ledger.app.escrow_service import (
    IllegalTransition as EscrowIllegalTransition,
    transition as escrow_transition,
)
from shared.observability import logger

from .models import Incident, IncidentState
from .offer_models import LIVE_OFFER_STATES, OfferState, RescueOffer
from .orchestrator import advance

#: States a breakdown can still be taken back from.
#:
#: The line is drawn where the cargo stops being on its own truck. Up to and
#: including DRIVER_EN_ROUTE the rescuer is merely on their way; nothing has
#: been loaded, so there is genuinely nothing to undo in the physical world.
STANDABLE_DOWN = frozenset(
    {
        IncidentState.BREAKDOWN_REPORTED,
        IncidentState.TRIAGING,
        IncidentState.MATCHING,
        IncidentState.RESCUE_OFFERED,
        IncidentState.RESCUE_ACCEPTED,
        IncidentState.DRIVER_EN_ROUTE,
    }
)


class CannotStandDown(RuntimeError):
    """The rescue has gone too far to be treated as if it never happened."""

    def __init__(self, message: str, state: Optional[str] = None) -> None:
        super().__init__(message)
        self.state = state


async def stand_down_incident(
    session: AsyncSession,
    incident: Incident,
    actor_id: str,
    reason: str = "",
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """Undo a breakdown report and everything it started.

    Returns a summary of what was unwound, so the caller can tell the driver
    what actually happened rather than just "ok".
    """
    if incident.state not in STANDABLE_DOWN:
        raise CannotStandDown(
            "The cargo has already been handed over, so this rescue has to be "
            "completed or cancelled from the operations console.",
            state=incident.state.value,
        )

    reason = reason.strip() or "Driver stood the breakdown down"

    result = await session.execute(
        select(RescueOffer)
        .where(RescueOffer.incident_id == incident.id)
        .where(
            RescueOffer.state.in_(
                [s.value for s in LIVE_OFFER_STATES] + [OfferState.BOUND.value]
            )
        )
    )
    offers = list(result.scalars().all())

    withdrawn = 0
    escrows_reversed = 0
    trucks_released = 0

    for offer in offers:
        was_bound = offer.state == OfferState.BOUND.value
        offer.state = OfferState.CANCELLED.value if was_bound else OfferState.WITHDRAWN.value
        offer.terminal_reason = "OWNER_STOOD_DOWN"
        offer.terminal_note = reason
        offer.terminated_at = incident.updated_at
        withdrawn += 1

        if was_bound and offer.escrow_id:
            try:
                await escrow_transition(
                    session,
                    offer.escrow_id,
                    "CANCELLED",
                    reason=reason,
                    commit=False,
                )
                escrows_reversed += 1
            except EscrowIllegalTransition as exc:
                # A hold that cannot be reversed is a bookkeeping problem, not
                # a reason to keep a truck stranded under a rescue nobody
                # needs. It is logged loudly and settled by hand.
                logger.warn(
                    "stand_down_escrow_not_reversed",
                    incident_id=incident.id,
                    escrow_id=offer.escrow_id,
                    error=str(exc),
                )

        if was_bound and offer.carrier_truck_id:
            truck = await session.get(Truck, offer.carrier_truck_id)
            if truck is not None:
                truck.status = TruckStatus.idle
                trucks_released += 1

    incident = await advance(
        session,
        incident.id,
        IncidentState.CANCELLED,
        actor_id=actor_id,
        metadata={
            "reason": reason,
            "standDown": True,
            "offersWithdrawn": withdrawn,
            "escrowsReversed": escrows_reversed,
        },
        event_publisher=event_publisher,
        commit=False,
    )
    incident.assigned_truck_id = None

    shipment = await session.get(Shipment, incident.shipment_id)
    if shipment is not None:
        shipment.status = "in_transit"

    await session.commit()
    await session.refresh(incident)

    logger.info(
        "breakdown_stood_down",
        incident_id=incident.id,
        actor=actor_id,
        offers_withdrawn=withdrawn,
        escrows_reversed=escrows_reversed,
        trucks_released=trucks_released,
    )

    return {
        "incidentId": incident.id,
        "state": incident.state.value,
        "shipmentId": incident.shipment_id,
        "offersWithdrawn": withdrawn,
        "escrowsReversed": escrows_reversed,
        "trucksReleased": trucks_released,
        "reason": reason,
    }
