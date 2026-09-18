"""
Escrow State Machine with Atomic Double-Entry Ledger Postings (Phase 5.2).
Transactions are fully atomic — state change and balanced ledger entries either
both persist or neither does.
"""
from typing import List, Optional, Callable, Awaitable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from .models import Escrow, LedgerEntry
from shared.events import EventEnvelope
from shared.observability import ESCROW_DISPUTES, logger

VALID_TRANSITIONS = {
    "INITIATED": {"ACCEPTED"},
    "ACCEPTED": {"IN_TRANSIT"},
    "IN_TRANSIT": {"PENDING_VERIFICATION"},
    "PENDING_VERIFICATION": {"RELEASED", "DISPUTED"},
}


class IllegalTransition(Exception):
    pass


class EscrowNotFound(Exception):
    pass


async def create_escrow(
    session: AsyncSession,
    match_id: str,
    amount_inr: float,
) -> Escrow:
    escrow = Escrow(match_id=match_id, amount_inr=amount_inr, state="INITIATED")
    session.add(escrow)
    await session.commit()
    await session.refresh(escrow)
    return escrow


async def transition(
    session: AsyncSession,
    escrow_id: str,
    new_state: str,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Escrow:
    escrow = await session.get(Escrow, escrow_id)
    if not escrow:
        raise EscrowNotFound(f"Escrow {escrow_id} not found")

    allowed = VALID_TRANSITIONS.get(escrow.state, set())
    if new_state not in allowed:
        raise IllegalTransition(
            f"Illegal transition: {escrow.state} -> {new_state} not allowed. Valid: {allowed}"
        )

    # Atomic write: transition state and post balancing ledger entries
    escrow.state = new_state
    if new_state == "RELEASED":
        payer_entry = LedgerEntry(
            escrow_id=escrow_id,
            account="payer_hold",
            debit_inr=escrow.amount_inr,
            credit_inr=0.0,
        )
        payee_entry = LedgerEntry(
            escrow_id=escrow_id,
            account="payee_receivable",
            debit_inr=0.0,
            credit_inr=escrow.amount_inr,
        )
        session.add_all([payer_entry, payee_entry])

    elif new_state == "DISPUTED":
        ESCROW_DISPUTES.inc()
        logger.warn("escrow_disputed", escrow_id=escrow_id, amount_inr=escrow.amount_inr)

    await session.commit()
    await session.refresh(escrow)

    if event_publisher:
        envelope = EventEnvelope(
            eventType="escrow.state_changed",
            producer="escrow-ledger",
            payload={"escrowId": escrow_id, "state": new_state, "amountInr": escrow.amount_inr},
        )
        await event_publisher("escrow.state_changed", envelope.to_dict())

    return escrow


async def verify_and_release(
    session: AsyncSession,
    escrow_id: str,
    delivered_temp_log: List[float],
    required_max_temp: float,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Escrow:
    """
    Checks cold-chain log against maximum allowable temperature.
    If any reading breached the threshold, marks as DISPUTED; otherwise transitions to RELEASED.
    """
    breached = [t for t in delivered_temp_log if t > required_max_temp]
    if breached:
        logger.warn(
            "temperature_breach_detected",
            escrow_id=escrow_id,
            breached_readings=breached,
            max_allowed=required_max_temp,
        )
        return await transition(session, escrow_id, "DISPUTED", event_publisher=event_publisher)
    return await transition(session, escrow_id, "RELEASED", event_publisher=event_publisher)


async def get_ledger_entries(session: AsyncSession, escrow_id: str) -> List[LedgerEntry]:
    stmt = select(LedgerEntry).where(LedgerEntry.escrow_id == escrow_id).order_by(LedgerEntry.created_at)
    res = await session.execute(stmt)
    return list(res.scalars().all())
