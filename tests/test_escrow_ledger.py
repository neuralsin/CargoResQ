import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from services.escrow_ledger.app.models import Base
from services.escrow_ledger.app.escrow_service import (
    create_escrow,
    transition,
    verify_and_release,
    get_ledger_entries,
    IllegalTransition,
)


@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_escrow_full_happy_path_and_atomic_ledger(test_session):
    # 1. Create escrow
    escrow = await create_escrow(test_session, match_id="match_101", amount_inr=5400.0)
    assert escrow.state == "INITIATED"
    assert escrow.amount_inr == 5400.0

    # 2. Transition through valid states
    await transition(test_session, escrow.id, "ACCEPTED")
    await transition(test_session, escrow.id, "IN_TRANSIT")
    await transition(test_session, escrow.id, "PENDING_VERIFICATION")
    released_escrow = await transition(test_session, escrow.id, "RELEASED")
    assert released_escrow.state == "RELEASED"

    # 3. Check atomic double-entry postings
    entries = await get_ledger_entries(test_session, escrow.id)
    assert len(entries) == 2
    payer = next(e for e in entries if e.account == "payer_hold")
    payee = next(e for e in entries if e.account == "payee_receivable")

    assert payer.debit_inr == 5400.0
    assert payer.credit_inr == 0.0
    assert payee.debit_inr == 0.0
    assert payee.credit_inr == 5400.0


@pytest.mark.asyncio
async def test_escrow_illegal_transition_rejected(test_session):
    escrow = await create_escrow(test_session, match_id="match_102", amount_inr=3200.0)
    # INITIATED -> RELEASED is illegal
    with pytest.raises(IllegalTransition):
        await transition(test_session, escrow.id, "RELEASED")


@pytest.mark.asyncio
async def test_temperature_breach_triggers_dispute(test_session):
    escrow = await create_escrow(test_session, match_id="match_103", amount_inr=4500.0)
    await transition(test_session, escrow.id, "ACCEPTED")
    await transition(test_session, escrow.id, "IN_TRANSIT")
    await transition(test_session, escrow.id, "PENDING_VERIFICATION")

    # Max allowed 4.0 C, but a 6.2 C reading occurred (breach)
    disputed_escrow = await verify_and_release(
        test_session,
        escrow_id=escrow.id,
        delivered_temp_log=[2.1, 3.5, 6.2, 3.8],
        required_max_temp=4.0,
    )
    assert disputed_escrow.state == "DISPUTED"

    # Verify NO ledger release entries exist for disputed escrow
    entries = await get_ledger_entries(test_session, escrow.id)
    assert len(entries) == 0
