"""
The two-way rescue handshake.

A rescue binds only when both the stranded cargo owner and the helping carrier
have said yes. The two acceptances are independent columns rather than steps
in a line, so "carrier accepts, then owner confirms" and "owner pre-confirms,
then carrier accepts" are literally the same code path.

Exclusivity is enforced by the database. Two carriers racing to accept the
same incident is resolved by a partial unique index, not by a read-then-write
in Python that loses the race under concurrency.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Company, Driver, Shipment, Truck, TruckStatus
from services.escrow_ledger.app.escrow_service import create_escrow, transition as escrow_transition
from shared.events import EventEnvelope
from shared.observability import logger

from .models import Incident, IncidentState
from .offer_models import (
    DEFAULT_OFFER_TTL_SECONDS,
    LIVE_OFFER_STATES,
    OFFER_TRANSITIONS,
    OfferState,
    RescueOffer,
)
from .orchestrator import advance

MAX_OFFERS_PER_ROUND = 8


class OfferError(Exception):
    """Base class for handshake failures, each carrying a stable code."""

    code = "offer_error"
    http_status = 400

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context = context


class OfferNotFound(OfferError):
    code = "offer_not_found"
    http_status = 404


class OfferExpired(OfferError):
    code = "offer_expired"
    http_status = 409


class OfferNotLive(OfferError):
    code = "offer_not_live"
    http_status = 409


class AlreadyBound(OfferError):
    code = "already_bound"
    http_status = 409


class AnotherOfferConfirmed(OfferError):
    code = "another_offer_confirmed"
    http_status = 409


class NoCandidates(OfferError):
    code = "no_candidates"
    http_status = 404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands back naive datetimes; compare them in UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_expired(offer: RescueOffer, now: Optional[datetime] = None) -> bool:
    expires = _as_aware(offer.expires_at)
    return expires is not None and (now or _now()) > expires


async def _expire_if_due(session: AsyncSession, offer: RescueOffer) -> RescueOffer:
    """Expiry is enforced lazily on every read and every mutation.

    The background sweeper exists for tidiness and events, but correctness
    cannot depend on it having run -- an offer that is past its deadline must
    behave as expired the instant anyone looks at it.
    """
    if offer.state in {s.value for s in LIVE_OFFER_STATES} and is_expired(offer):
        offer.state = OfferState.EXPIRED.value
        offer.terminal_reason = "EXPIRED"
        offer.terminated_at = _now()
        await session.flush()
    return offer


def _transition(offer: RescueOffer, new_state: OfferState) -> None:
    current = OfferState(offer.state)
    if new_state not in OFFER_TRANSITIONS[current]:
        raise OfferNotLive(
            f"Offer is {current.value}; cannot move to {new_state.value}",
            offer_id=offer.id,
            state=current.value,
        )
    offer.state = new_state.value


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


async def create_offer_round(
    session: AsyncSession,
    incident: Incident,
    shipment: Shipment,
    candidates: Sequence[Dict[str, Any]],
    actor_id: str,
    ttl_seconds: int = DEFAULT_OFFER_TTL_SECONDS,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> List[RescueOffer]:
    """Offer the job to several carriers at once, on one shared deadline.

    Each candidate carries its own quote: price depends on the candidate's
    distance and reputation, so the same incident is genuinely worth different
    amounts from different trucks.
    """
    if not candidates:
        raise NoCandidates(
            "No compatible truck was found for this cargo. "
            "The system will not force an incompatible match.",
            incident_id=incident.id,
        )

    existing = await session.execute(
        select(RescueOffer.offer_round)
        .where(RescueOffer.incident_id == incident.id)
        .order_by(RescueOffer.offer_round.desc())
        .limit(1)
    )
    last_round = existing.scalar_one_or_none() or 0
    offer_round = last_round + 1

    expires_at = _now() + timedelta(seconds=ttl_seconds)
    offers: List[RescueOffer] = []

    for candidate in candidates[:MAX_OFFERS_PER_ROUND]:
        quote = candidate["quote"]
        offer = RescueOffer(
            incident_id=incident.id,
            shipment_id=shipment.id,
            offer_round=offer_round,
            owner_company_id=shipment.owner_company_id,
            carrier_company_id=candidate["companyId"],
            carrier_truck_id=candidate["truckId"],
            carrier_driver_id=candidate.get("driverId"),
            state=OfferState.PENDING.value,
            price_total_inr=quote.total_inr,
            carrier_payout_inr=quote.carrier_payout_inr,
            platform_fee_inr=quote.platform_fee_inr,
            price_breakdown_json=quote.to_dict(),
            eta_minutes=candidate.get("etaMinutes"),
            distance_km=candidate.get("distanceKm"),
            rescue_score=candidate.get("score"),
            score_reasons_json=candidate.get("reasons", []),
            expires_at=expires_at,
            intra_company=candidate["companyId"] == shipment.owner_company_id,
        )
        session.add(offer)
        offers.append(offer)

    await session.flush()

    if incident.state in {IncidentState.TRIAGING, IncidentState.MATCHING}:
        await advance(
            session,
            incident.id,
            IncidentState.RESCUE_OFFERED,
            actor_id=actor_id,
            metadata={"offerCount": len(offers), "offerRound": offer_round},
            event_publisher=None,
            commit=False,
        )

    await session.commit()
    for offer in offers:
        await session.refresh(offer)

    logger.info(
        "offer_round_created",
        incident_id=incident.id,
        offer_round=offer_round,
        count=len(offers),
    )

    if event_publisher:
        for offer in offers:
            await _publish(event_publisher, "offer.created", offer)

    return offers


# ---------------------------------------------------------------------------
# The handshake
# ---------------------------------------------------------------------------


async def carrier_accept(
    session: AsyncSession,
    offer: RescueOffer,
    actor_id: str,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Tuple[RescueOffer, bool]:
    """The helping carrier says yes. Returns (offer, bound)."""
    await _expire_if_due(session, offer)
    if offer.state == OfferState.EXPIRED.value:
        raise OfferExpired("This offer has expired", offer_id=offer.id)

    if offer.carrier_accepted_at is None:
        offer.carrier_accepted_at = _now()
        offer.carrier_accepted_by = actor_id
        if offer.state == OfferState.PENDING.value:
            _transition(offer, OfferState.CARRIER_ACCEPTED)

    return await _try_bind(session, offer, event_publisher, "offer.carrier_accepted")


async def owner_confirm(
    session: AsyncSession,
    offer: RescueOffer,
    actor_id: str,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Tuple[RescueOffer, bool]:
    """The stranded owner says yes. Returns (offer, bound)."""
    await _expire_if_due(session, offer)
    if offer.state == OfferState.EXPIRED.value:
        raise OfferExpired("This offer has expired", offer_id=offer.id)

    if offer.owner_confirmed_at is None:
        # The database enforces one confirmed offer per incident, but a clear
        # error beats an integrity violation surfacing as a 500.
        conflict = await session.execute(
            select(RescueOffer)
            .where(RescueOffer.incident_id == offer.incident_id)
            .where(RescueOffer.id != offer.id)
            .where(RescueOffer.owner_confirmed_at.is_not(None))
            .where(RescueOffer.state.in_([s.value for s in LIVE_OFFER_STATES] + ["BOUND"]))
        )
        other = conflict.scalars().first()
        if other is not None:
            raise AnotherOfferConfirmed(
                "You have already confirmed another offer for this incident. "
                "Withdraw it first if you want to switch.",
                offer_id=offer.id,
                confirmed_offer_id=other.id,
            )

        offer.owner_confirmed_at = _now()
        offer.owner_confirmed_by = actor_id
        if offer.state == OfferState.PENDING.value:
            _transition(offer, OfferState.OWNER_CONFIRMED)

    return await _try_bind(session, offer, event_publisher, "offer.owner_confirmed")


async def _try_bind(
    session: AsyncSession,
    offer: RescueOffer,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]],
    partial_event: str,
) -> Tuple[RescueOffer, bool]:
    """Bind the rescue if and only if both parties have agreed.

    Everything from here -- the offer state, the escrow, the sibling offers,
    the truck's status and the incident's state -- commits together or not at
    all. A half-bound rescue is worse than no rescue: the owner believes help
    is coming and the carrier does not know they are committed.
    """
    if not offer.both_parties_agreed:
        await session.commit()
        await session.refresh(offer)
        if event_publisher:
            await _publish(event_publisher, partial_event, offer)
        return offer, False

    _transition(offer, OfferState.BOUND)
    offer.bound_at = _now()

    escrow = await create_escrow(
        session,
        amount_inr=offer.price_total_inr,
        carrier_payout_inr=offer.carrier_payout_inr,
        owner_company_id=offer.owner_company_id,
        carrier_company_id=offer.carrier_company_id,
        incident_id=offer.incident_id,
        commit=False,
    )
    offer.escrow_id = escrow.id
    # Both parties have committed, so the funds are held immediately -- this
    # is what posts the balanced HOLD entries.
    await escrow_transition(session, escrow.id, "ACCEPTED", commit=False)

    # Every other offer for this incident is now moot. Telling the losing
    # carriers promptly matters: they are holding a truck for us.
    await session.execute(
        update(RescueOffer)
        .where(RescueOffer.incident_id == offer.incident_id)
        .where(RescueOffer.id != offer.id)
        .where(RescueOffer.state.in_([s.value for s in LIVE_OFFER_STATES]))
        .values(
            state=OfferState.SUPERSEDED.value,
            terminal_reason="SUPERSEDED",
            terminated_at=_now(),
        )
    )

    truck = await session.get(Truck, offer.carrier_truck_id)
    if truck is not None:
        truck.status = TruckStatus.dispatched_rescue

    await advance(
        session,
        offer.incident_id,
        IncidentState.RESCUE_ACCEPTED,
        actor_id=offer.owner_confirmed_by or "system",
        # Deliberately no price in the metadata: incident_events is exposed by
        # the timeline endpoint, and the carrier can read that.
        metadata={
            "assignedTruckId": offer.carrier_truck_id,
            "offerId": offer.id,
            "escrowId": escrow.id,
            "carrierCompanyId": offer.carrier_company_id,
        },
        event_publisher=None,
        commit=False,
    )

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        winner = await session.execute(
            select(RescueOffer.id)
            .where(RescueOffer.incident_id == offer.incident_id)
            .where(RescueOffer.state == OfferState.BOUND.value)
        )
        won = winner.scalar_one_or_none()
        logger.warn("offer_bind_race_lost", offer_id=offer.id, winner_id=won)
        raise AlreadyBound(
            "Another carrier was bound to this incident first.",
            offer_id=offer.id,
            bound_offer_id=won,
        ) from exc

    await session.refresh(offer)
    logger.info(
        "rescue_bound",
        offer_id=offer.id,
        incident_id=offer.incident_id,
        carrier=offer.carrier_company_id,
        escrow_id=offer.escrow_id,
    )

    if event_publisher:
        await _publish(event_publisher, "offer.bound", offer)
    return offer, True


# ---------------------------------------------------------------------------
# Withdrawal, decline, cancellation
# ---------------------------------------------------------------------------


async def carrier_decline(
    session: AsyncSession,
    offer: RescueOffer,
    reason: Optional[str],
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> RescueOffer:
    await _expire_if_due(session, offer)
    _transition(offer, OfferState.DECLINED)
    offer.terminal_reason = "DECLINED"
    offer.terminal_note = reason
    offer.terminated_at = _now()
    await session.commit()
    await session.refresh(offer)
    if event_publisher:
        await _publish(event_publisher, "offer.declined", offer)
    return offer


async def owner_withdraw(
    session: AsyncSession,
    offer: RescueOffer,
    reason: Optional[str],
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> RescueOffer:
    await _expire_if_due(session, offer)
    _transition(offer, OfferState.WITHDRAWN)
    offer.terminal_reason = "WITHDRAWN"
    offer.terminal_note = reason
    offer.terminated_at = _now()
    await session.commit()
    await session.refresh(offer)
    if event_publisher:
        await _publish(event_publisher, "offer.withdrawn", offer)
    return offer


async def cancel_bound_offer(
    session: AsyncSession,
    offer: RescueOffer,
    reason: str,
    actor_id: str,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> RescueOffer:
    """Unwind a rescue that was bound but cannot complete.

    Releases the truck, reverses the escrow hold and returns the incident to
    matching so another carrier can be found. Without this path a failed
    rescue left the escrow stuck and the truck marked as dispatched forever.
    """
    if offer.state != OfferState.BOUND.value:
        raise OfferNotLive("Only a bound rescue can be cancelled", offer_id=offer.id)

    _transition(offer, OfferState.CANCELLED)
    offer.terminal_reason = "CANCELLED_AFTER_BIND"
    offer.terminal_note = reason
    offer.terminated_at = _now()

    if offer.escrow_id:
        await escrow_transition(
            session, offer.escrow_id, "CANCELLED", reason=reason, commit=False
        )

    truck = await session.get(Truck, offer.carrier_truck_id)
    if truck is not None:
        truck.status = TruckStatus.idle

    incident = await session.get(Incident, offer.incident_id)
    if incident is not None and incident.state == IncidentState.RESCUE_ACCEPTED:
        await advance(
            session,
            incident.id,
            IncidentState.MATCHING,
            actor_id=actor_id,
            metadata={"cancelledOfferId": offer.id, "reason": reason},
            event_publisher=None,
            commit=False,
        )
        incident.assigned_truck_id = None

    await session.commit()
    await session.refresh(offer)
    logger.warn("rescue_cancelled_after_bind", offer_id=offer.id, reason=reason)
    if event_publisher:
        await _publish(event_publisher, "offer.cancelled", offer)
    return offer


async def expire_due_offers(
    session: AsyncSession,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> int:
    """Sweep offers past their deadline. Returns how many were expired."""
    now = _now()
    result = await session.execute(
        select(RescueOffer)
        .where(RescueOffer.state.in_([s.value for s in LIVE_OFFER_STATES]))
        .where(RescueOffer.expires_at < now)
    )
    due = list(result.scalars().all())
    for offer in due:
        offer.state = OfferState.EXPIRED.value
        offer.terminal_reason = "EXPIRED"
        offer.terminated_at = now
    if due:
        await session.commit()
        logger.info("offers_expired", count=len(due))
        if event_publisher:
            for offer in due:
                await _publish(event_publisher, "offer.expired", offer)
    return len(due)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def _publish(
    event_publisher: Callable[[str, dict], Awaitable[None]],
    event_type: str,
    offer: RescueOffer,
) -> None:
    """Emit an offer event.

    The payload carries both company ids so the projection layer can build a
    different view for each side. It deliberately does not carry the price:
    the projector reads that from the offer when building the owner's view,
    so a price can never reach the carrier's channel by accident.
    """
    envelope = EventEnvelope(
        eventType=event_type,
        producer="orchestrator",
        payload={
            "offerId": offer.id,
            "incidentId": offer.incident_id,
            "state": offer.state,
            "ownerCompanyId": offer.owner_company_id,
            "carrierCompanyId": offer.carrier_company_id,
            "carrierTruckId": offer.carrier_truck_id,
            "carrierDriverId": offer.carrier_driver_id,
            "etaMinutes": offer.eta_minutes,
            "distanceKm": offer.distance_km,
            "expiresAt": offer.expires_at.isoformat() if offer.expires_at else None,
        },
    )
    await event_publisher(event_type, envelope.to_dict())
