import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from services.orchestrator.app.models import Base, IncidentState
from services.orchestrator.app.orchestrator import (
    create_incident,
    advance,
    get_incident_timeline,
    IllegalTransition,
)
from services.orchestrator.app.sla import compute_sla
from services.orchestrator.app.trust import compute_trust_score


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
async def test_orchestrator_full_lifecycle_and_audit_trail(test_session):
    incident = await create_incident(
        session=test_session,
        shipment_id="ship_test_001",
        lat=12.9716,
        lng=77.5946,
        minutes_until_spoilage=45.0,
        actor_id="system_detector",
    )
    assert incident.state == IncidentState.BREAKDOWN_REPORTED

    # Advance step-by-step
    await advance(test_session, incident.id, IncidentState.TRIAGING, "ops_user")
    await advance(test_session, incident.id, IncidentState.MATCHING, "matching_svc")
    await advance(test_session, incident.id, IncidentState.RESCUE_OFFERED, "matcher")
    await advance(
        test_session,
        incident.id,
        IncidentState.RESCUE_ACCEPTED,
        "rescuer_driver",
        {"assignedTruckId": "trk_999"},
    )
    await advance(test_session, incident.id, IncidentState.DRIVER_EN_ROUTE, "rescuer_driver")
    await advance(test_session, incident.id, IncidentState.CARGO_TRANSFER, "driver")
    await advance(test_session, incident.id, IncidentState.RESCUE_IN_TRANSIT, "driver")
    await advance(test_session, incident.id, IncidentState.DELIVERED, "driver")
    await advance(test_session, incident.id, IncidentState.VERIFICATION, "inspector")
    final_incident = await advance(
        test_session, incident.id, IncidentState.ESCROW_RELEASED, "escrow_svc"
    )

    assert final_incident.state == IncidentState.ESCROW_RELEASED
    assert final_incident.assigned_truck_id == "trk_999"

    # Verify audit trail events
    events = await get_incident_timeline(test_session, incident.id)
    assert len(events) == 11
    assert events[0].type == "incident.breakdown_reported"
    assert events[-1].type == "incident.escrow_released"


@pytest.mark.asyncio
async def test_orchestrator_illegal_transition_rejected(test_session):
    incident = await create_incident(
        session=test_session,
        shipment_id="ship_test_002",
        lat=12.97,
        lng=77.59,
    )
    # Direct jump from BREAKDOWN_REPORTED to ESCROW_RELEASED must fail
    with pytest.raises(IllegalTransition):
        await advance(test_session, incident.id, IncidentState.ESCROW_RELEASED, "hacker")


def test_sla_computation():
    now = datetime.now(timezone.utc)
    mock_events = [
        {"type": "incident.breakdown_reported", "created_at": now},
        {"type": "incident.rescue_offered", "created_at": now + timedelta(seconds=65)},
        {"type": "incident.rescue_accepted", "created_at": now + timedelta(seconds=150)},
        {"type": "incident.driver_en_route", "created_at": now + timedelta(seconds=160)},
        {"type": "incident.cargo_transfer", "created_at": now + timedelta(seconds=1200)},
        {"type": "incident.escrow_released", "created_at": now + timedelta(seconds=3600)},
    ]
    sla = compute_sla(mock_events)

    # match target: 120s, actual: 65s -> MET
    assert sla["match"]["actualSec"] == 65.0
    assert sla["match"]["met"] is True

    # accept target: 300s, actual: 85s (150 - 65) -> MET
    assert sla["accept"]["actualSec"] == 85.0
    assert sla["accept"]["met"] is True

    # arrival target: 2700s, actual: 1040s (1200 - 160) -> MET
    assert sla["arrival"]["actualSec"] == 1040.0
    assert sla["arrival"]["met"] is True

    # resolution target: 7200s, actual: 3600s -> MET
    assert sla["resolution"]["actualSec"] == 3600.0
    assert sla["resolution"]["met"] is True


def test_trust_score_calculation():
    # Good company stats
    stats_good = {
        "completedRescues": 60,
        "acceptanceRate": 0.95,
        "disputeCount": 0,
    }
    trust_good = compute_trust_score(stats_good)
    assert trust_good["trustScore"] == 100.0
    assert any("completed rescues" in s for s in trust_good["signals"])
    assert any("0 disputes" in s for s in trust_good["signals"])

    # Poor company stats
    stats_poor = {
        "completedRescues": 5,
        "acceptanceRate": 0.80,  # -10 penalty
        "disputeCount": 2,       # -10 penalty
    }
    trust_poor = compute_trust_score(stats_poor)
    assert trust_poor["trustScore"] == 80.0
    assert any("Acceptance rate below" in s for s in trust_poor["signals"])
    assert any("dispute(s) on record" in s for s in trust_poor["signals"])
