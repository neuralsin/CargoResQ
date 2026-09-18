"""
Escrow state machine with atomic double-entry ledger postings (Phase 5.2).

Two properties this module must guarantee:

1. A state change and its ledger postings either both persist or neither does.
2. For any escrow, the sum of debits equals the sum of credits, at every point
   in the lifecycle -- not only at the end.

The previous implementation posted a single pair of entries at RELEASED and
nothing at any other state, so between acceptance and release no money was
recorded as held. An escrow that holds nothing is a promise, not an escrow.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.events import EventEnvelope
from shared.observability import ESCROW_DISPUTES, logger

from .models import Escrow, LedgerEntry

VALID_TRANSITIONS = {
    "INITIATED": {"ACCEPTED", "CANCELLED"},
    "ACCEPTED": {"IN_TRANSIT", "CANCELLED"},
    "IN_TRANSIT": {"PENDING_VERIFICATION", "CANCELLED"},
    "PENDING_VERIFICATION": {"RELEASED", "DISPUTED"},
    "RELEASED": set(),
    "DISPUTED": set(),
    "CANCELLED": set(),
}

# Chart of accounts. Deliberately small and explicit.
ACC_PAYER_PAYABLE = "payer_payable"            # what the cargo owner owes
ACC_ESCROW_LIABILITY = "escrow_liability"      # funds held by the platform
ACC_CARRIER_RECEIVABLE = "carrier_receivable"  # what the rescuer is owed
ACC_PLATFORM_FEE = "platform_fee_income"


class IllegalTransition(Exception):
    pass


class EscrowNotFound(Exception):
    pass


@dataclass(frozen=True)
class Posting:
    """One journal entry: lines whose debits and credits must sum equal."""

    posting_type: str
    lines: Sequence[Tuple[str, float, float, str]]  # (account, debit, credit, memo)

    def validate(self) -> None:
        debits = round(sum(line[1] for line in self.lines), 2)
        credits = round(sum(line[2] for line in self.lines), 2)
        if debits != credits:
            raise ValueError(
                f"Unbalanced {self.posting_type} posting: "
                f"debits {debits} != credits {credits}"
            )


def _hold_posting(amount: float) -> Posting:
    """Funds committed by the owner and held by the platform."""
    return Posting(
        "HOLD",
        [
            (ACC_PAYER_PAYABLE, amount, 0.0, "Owner commits rescue fee"),
            (ACC_ESCROW_LIABILITY, 0.0, amount, "Funds held in escrow"),
        ],
    )


def _release_posting(amount: float, carrier_payout: float) -> Posting:
    """Held funds discharged to the rescuer, with the platform fee split out."""
    fee = round(amount - carrier_payout, 2)
    lines = [
        (ACC_ESCROW_LIABILITY, amount, 0.0, "Release held funds"),
        (ACC_CARRIER_RECEIVABLE, 0.0, carrier_payout, "Rescuer payout"),
    ]
    if fee > 0:
        lines.append((ACC_PLATFORM_FEE, 0.0, fee, "Platform fee"))
    return Posting("RELEASE", lines)


def _reversal_posting(amount: float, reason: str) -> Posting:
    """Held funds returned to the owner when a rescue does not complete."""
    return Posting(
        "CANCEL",
        [
            (ACC_ESCROW_LIABILITY, amount, 0.0, f"Reverse hold: {reason}"),
            (ACC_PAYER_PAYABLE, 0.0, amount, f"Owner obligation released: {reason}"),
        ],
    )


def _write(session: AsyncSession, escrow_id: str, posting: Posting) -> None:
    posting.validate()
    ref = f"pst_{uuid.uuid4().hex[:12]}"
    session.add_all(
        [
            LedgerEntry(
                escrow_id=escrow_id,
                posting_ref=ref,
                posting_type=posting.posting_type,
                account=account,
                debit_inr=debit,
                credit_inr=credit,
                memo=memo,
            )
            for account, debit, credit, memo in posting.lines
        ]
    )


async def create_escrow(
    session: AsyncSession,
    amount_inr: float,
    owner_company_id: Optional[str] = None,
    carrier_company_id: Optional[str] = None,
    carrier_payout_inr: Optional[float] = None,
    incident_id: Optional[str] = None,
    match_id: Optional[str] = None,
    commit: bool = True,
) -> Escrow:
    escrow = Escrow(
        match_id=match_id,
        incident_id=incident_id,
        owner_company_id=owner_company_id,
        carrier_company_id=carrier_company_id,
        amount_inr=amount_inr,
        carrier_payout_inr=(
            carrier_payout_inr if carrier_payout_inr is not None else amount_inr
        ),
        state="INITIATED",
    )
    session.add(escrow)
    if commit:
        await session.commit()
        await session.refresh(escrow)
    else:
        await session.flush()
    return escrow


async def transition(
    session: AsyncSession,
    escrow_id: str,
    new_state: str,
    reason: Optional[str] = None,
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    commit: bool = True,
) -> Escrow:
    escrow = await session.get(Escrow, escrow_id)
    if not escrow:
        raise EscrowNotFound(f"Escrow {escrow_id} not found")

    allowed = VALID_TRANSITIONS.get(escrow.state, set())
    if new_state not in allowed:
        raise IllegalTransition(
            f"Illegal transition: {escrow.state} -> {new_state} not allowed. "
            f"Valid: {sorted(allowed)}"
        )

    previously_held = escrow.state in {"ACCEPTED", "IN_TRANSIT", "PENDING_VERIFICATION"}
    escrow.state = new_state
    if reason:
        escrow.state_reason = reason

    payout = escrow.carrier_payout_inr
    if payout is None:
        payout = escrow.amount_inr

    # The state change and its postings share one commit, so an escrow can
    # never be RELEASED without the matching ledger entries existing.
    if new_state == "ACCEPTED":
        _write(session, escrow_id, _hold_posting(escrow.amount_inr))
    elif new_state == "RELEASED":
        _write(session, escrow_id, _release_posting(escrow.amount_inr, payout))
    elif new_state == "CANCELLED":
        # Only reverse a hold that was actually posted. Cancelling straight
        # from INITIATED means nothing was ever held.
        if previously_held:
            _write(
                session,
                escrow_id,
                _reversal_posting(escrow.amount_inr, reason or "cancelled"),
            )
    elif new_state == "DISPUTED":
        ESCROW_DISPUTES.inc()
        logger.warn("escrow_disputed", escrow_id=escrow_id, amount_inr=escrow.amount_inr)

    if commit:
        await session.commit()
        await session.refresh(escrow)
    else:
        await session.flush()

    if event_publisher:
        envelope = EventEnvelope(
            eventType="escrow.state_changed",
            producer="escrow-ledger",
            payload={
                "escrowId": escrow_id,
                "incidentId": escrow.incident_id,
                "ownerCompanyId": escrow.owner_company_id,
                "carrierCompanyId": escrow.carrier_company_id,
                "state": new_state,
            },
        )
        await event_publisher("escrow.state_changed", envelope.to_dict())

    return escrow


@dataclass(frozen=True)
class VerificationVerdict:
    """The outcome of checking recorded conditions against the cargo's spec."""

    verdict: str  # PASS | FAIL | INSUFFICIENT_DATA
    reason: str
    readings_count: int
    breach_count: int
    peak_temp_c: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "readingsCount": self.readings_count,
            "breachCount": self.breach_count,
            "peakTempC": self.peak_temp_c,
        }


def evaluate_temperature_log(
    readings: Sequence[float],
    required_max_temp_c: Optional[float],
    minimum_readings: int = 1,
) -> VerificationVerdict:
    """Decide whether a cold-chain log proves in-spec carriage.

    An empty log is INSUFFICIENT_DATA, never PASS. The previous implementation
    computed ``[t for t in log if t > limit]`` and released the funds when that
    list came back empty -- which is also the case when the log itself is
    empty, so an escrow could be released on no evidence whatsoever.
    """
    count = len(readings)
    if required_max_temp_c is None:
        return VerificationVerdict(
            "INSUFFICIENT_DATA",
            "Shipment declares no maximum temperature; cannot verify automatically.",
            count,
            0,
        )
    if count < minimum_readings:
        return VerificationVerdict(
            "INSUFFICIENT_DATA",
            f"Only {count} condition reading(s) recorded; at least "
            f"{minimum_readings} required to verify carriage.",
            count,
            0,
        )

    breaches = [t for t in readings if t > required_max_temp_c]
    peak = max(readings)
    if breaches:
        return VerificationVerdict(
            "FAIL",
            f"{len(breaches)} reading(s) above the {required_max_temp_c} C limit "
            f"(peak {peak} C).",
            count,
            len(breaches),
            peak,
        )
    return VerificationVerdict(
        "PASS",
        f"All {count} reading(s) within the {required_max_temp_c} C limit "
        f"(peak {peak} C).",
        count,
        0,
        peak,
    )


async def verify_and_release(
    session: AsyncSession,
    escrow_id: str,
    delivered_temp_log: Sequence[float],
    required_max_temp: Optional[float],
    event_publisher: Optional[Callable[[str, dict], Awaitable[None]]] = None,
) -> Tuple[Escrow, VerificationVerdict]:
    """Verify recorded conditions and settle accordingly.

    INSUFFICIENT_DATA deliberately settles nothing: the escrow stays in
    PENDING_VERIFICATION for manual review rather than defaulting to either
    party's advantage.
    """
    verdict = evaluate_temperature_log(delivered_temp_log, required_max_temp)

    if verdict.verdict == "INSUFFICIENT_DATA":
        escrow = await session.get(Escrow, escrow_id)
        if not escrow:
            raise EscrowNotFound(f"Escrow {escrow_id} not found")
        escrow.state_reason = f"MANUAL_REVIEW: {verdict.reason}"
        await session.commit()
        await session.refresh(escrow)
        logger.warn(
            "escrow_verification_insufficient_data",
            escrow_id=escrow_id,
            readings=verdict.readings_count,
        )
        return escrow, verdict

    if verdict.verdict == "FAIL":
        logger.warn(
            "temperature_breach_detected",
            escrow_id=escrow_id,
            breaches=verdict.breach_count,
            max_allowed=required_max_temp,
        )

    target = "RELEASED" if verdict.verdict == "PASS" else "DISPUTED"
    escrow = await transition(
        session,
        escrow_id,
        target,
        reason=verdict.reason,
        event_publisher=event_publisher,
    )
    return escrow, verdict


async def get_ledger_entries(session: AsyncSession, escrow_id: str) -> List[LedgerEntry]:
    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.escrow_id == escrow_id)
        .order_by(LedgerEntry.created_at, LedgerEntry.id)
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def ledger_balance(session: AsyncSession, escrow_id: str) -> dict:
    """Trial balance for one escrow. Debits must equal credits."""
    entries = await get_ledger_entries(session, escrow_id)
    debits = round(sum(e.debit_inr for e in entries), 2)
    credits = round(sum(e.credit_inr for e in entries), 2)
    return {
        "escrowId": escrow_id,
        "entryCount": len(entries),
        "totalDebitInr": debits,
        "totalCreditInr": credits,
        "balanced": debits == credits,
    }
