"""
Escrow state machine and ledger integrity.

The central property: for any escrow, at any point in its life, the sum of
debits equals the sum of credits. Money is held from the moment both parties
commit, not conjured into existence at release.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.escrow_ledger.app.escrow_service import (
    IllegalTransition,
    create_escrow,
    evaluate_temperature_log,
    get_ledger_entries,
    ledger_balance,
    transition,
    verify_and_release,
)
from shared.database import Base
import shared.models_registry  # noqa: F401


@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _to_pending(session, amount: float = 5400.0, payout: float = 4750.0):
    escrow = await create_escrow(session, amount_inr=amount, carrier_payout_inr=payout)
    await transition(session, escrow.id, "ACCEPTED")
    await transition(session, escrow.id, "IN_TRANSIT")
    await transition(session, escrow.id, "PENDING_VERIFICATION")
    return escrow


@pytest.mark.asyncio
async def test_funds_are_held_from_acceptance_not_only_at_release(test_session):
    """Between acceptance and release the money must be recorded as held.

    Posting only at RELEASED meant the escrow held nothing for the whole
    period it was supposed to be protecting the owner.
    """
    escrow = await create_escrow(test_session, amount_inr=5400.0, carrier_payout_inr=4750.0)
    assert escrow.state == "INITIATED"
    assert await get_ledger_entries(test_session, escrow.id) == []

    await transition(test_session, escrow.id, "ACCEPTED")
    entries = await get_ledger_entries(test_session, escrow.id)
    assert len(entries) == 2, "acceptance must post a balanced hold"
    assert {e.posting_type for e in entries} == {"HOLD"}

    balance = await ledger_balance(test_session, escrow.id)
    assert balance["balanced"]
    assert balance["totalDebitInr"] == 5400.0


@pytest.mark.asyncio
async def test_release_splits_payout_and_fee_and_stays_balanced(test_session):
    escrow = await _to_pending(test_session, amount=5400.0, payout=4750.0)
    released = await transition(test_session, escrow.id, "RELEASED")
    assert released.state == "RELEASED"

    entries = await get_ledger_entries(test_session, escrow.id)
    release_lines = [e for e in entries if e.posting_type == "RELEASE"]
    accounts = {e.account: (e.debit_inr, e.credit_inr) for e in release_lines}

    assert accounts["escrow_liability"] == (5400.0, 0.0)
    assert accounts["carrier_receivable"] == (0.0, 4750.0)
    assert accounts["platform_fee_income"] == (0.0, 650.0)

    balance = await ledger_balance(test_session, escrow.id)
    assert balance["balanced"], "debits must equal credits after release"


@pytest.mark.asyncio
async def test_cancellation_reverses_the_hold(test_session):
    """A rescue that falls through must return the held funds, not strand them."""
    escrow = await create_escrow(test_session, amount_inr=3000.0, carrier_payout_inr=2600.0)
    await transition(test_session, escrow.id, "ACCEPTED")
    cancelled = await transition(
        test_session, escrow.id, "CANCELLED", reason="rescuer broke down"
    )
    assert cancelled.state == "CANCELLED"

    entries = await get_ledger_entries(test_session, escrow.id)
    assert {e.posting_type for e in entries} == {"HOLD", "CANCEL"}
    balance = await ledger_balance(test_session, escrow.id)
    assert balance["balanced"]
    assert balance["totalDebitInr"] == balance["totalCreditInr"] == 6000.0


@pytest.mark.asyncio
async def test_cancelling_before_acceptance_posts_nothing(test_session):
    """Nothing was ever held, so there is nothing to reverse."""
    escrow = await create_escrow(test_session, amount_inr=3000.0)
    await transition(test_session, escrow.id, "CANCELLED", reason="offer withdrawn")
    assert await get_ledger_entries(test_session, escrow.id) == []


@pytest.mark.asyncio
async def test_illegal_transition_rejected(test_session):
    escrow = await create_escrow(test_session, amount_inr=3200.0)
    with pytest.raises(IllegalTransition):
        await transition(test_session, escrow.id, "RELEASED")


@pytest.mark.asyncio
async def test_terminal_states_are_terminal(test_session):
    escrow = await _to_pending(test_session)
    await transition(test_session, escrow.id, "RELEASED")
    with pytest.raises(IllegalTransition):
        await transition(test_session, escrow.id, "DISPUTED")


@pytest.mark.asyncio
async def test_temperature_breach_disputes_and_pays_nobody(test_session):
    escrow = await _to_pending(test_session, amount=4500.0, payout=4000.0)
    disputed, verdict = await verify_and_release(
        test_session,
        escrow_id=escrow.id,
        delivered_temp_log=[2.1, 3.5, 6.2, 3.8],
        required_max_temp=4.0,
    )
    assert disputed.state == "DISPUTED"
    assert verdict.verdict == "FAIL"
    assert verdict.breach_count == 1

    entries = await get_ledger_entries(test_session, escrow.id)
    assert not [e for e in entries if e.posting_type == "RELEASE"]
    assert (await ledger_balance(test_session, escrow.id))["balanced"]


@pytest.mark.asyncio
async def test_in_spec_log_releases(test_session):
    escrow = await _to_pending(test_session, amount=4500.0, payout=4000.0)
    released, verdict = await verify_and_release(
        test_session,
        escrow_id=escrow.id,
        delivered_temp_log=[2.1, 3.5, 3.9],
        required_max_temp=4.0,
    )
    assert released.state == "RELEASED"
    assert verdict.verdict == "PASS"


@pytest.mark.asyncio
async def test_empty_log_settles_nothing(test_session):
    """The bug this guards: an empty log used to satisfy the breach check.

    ``[t for t in [] if t > limit]`` is empty, which the old code read as
    "no breaches", so an escrow could be RELEASED having proved nothing.
    """
    escrow = await _to_pending(test_session, amount=4500.0, payout=4000.0)
    result, verdict = await verify_and_release(
        test_session,
        escrow_id=escrow.id,
        delivered_temp_log=[],
        required_max_temp=4.0,
    )
    assert verdict.verdict == "INSUFFICIENT_DATA"
    assert result.state == "PENDING_VERIFICATION", "must not settle either way"
    assert not [
        e for e in await get_ledger_entries(test_session, escrow.id)
        if e.posting_type == "RELEASE"
    ]


@pytest.mark.parametrize(
    "readings,limit,expected",
    [
        ([], 8.0, "INSUFFICIENT_DATA"),
        ([4.0], None, "INSUFFICIENT_DATA"),
        ([4.0, 7.9], 8.0, "PASS"),
        ([8.0], 8.0, "PASS"),
        ([8.1], 8.0, "FAIL"),
    ],
)
def test_temperature_verdict_boundaries(readings, limit, expected):
    assert evaluate_temperature_log(readings, limit).verdict == expected


def test_unbalanced_posting_is_rejected():
    """A posting that does not balance must raise, not be written."""
    from services.escrow_ledger.app.escrow_service import Posting

    bad = Posting("HOLD", [("a", 100.0, 0.0, ""), ("b", 0.0, 90.0, "")])
    with pytest.raises(ValueError, match="Unbalanced"):
        bad.validate()
