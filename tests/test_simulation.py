import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from main import app
from services.core_api.app.simulation import run_simulation, SCENARIOS


@pytest.mark.asyncio
async def test_cold_chain_critical_simulation_runs_to_completion():
    emitted = []

    async def mock_emitter(event_type: str, payload: dict):
        emitted.append((event_type, payload))

    # Fast clock speed: 0.001s per simulated minute
    records = await run_simulation(
        "cold_chain_critical",
        emit_event=mock_emitter,
        seconds_per_simulated_minute=0.001,
    )

    event_names = [e[0] for e in emitted]
    assert "incident.breakdown_reported" in event_names
    assert "incident.rescue_offered" in event_names
    assert "incident.rescue_accepted" in event_names
    assert "incident.delivered" in event_names
    assert "incident.escrow_released" in event_names

    # Verify cold chain insulin payload parameters
    first_payload = emitted[0][1]
    assert first_payload["cargoType"] == "Insulin (cold-chain)"
    assert first_payload["requiresRefrigeration"] is True
    assert first_payload["requiredMaxTempC"] == 8.0
    assert first_payload["minutesUntilSpoilage"] == 40.0


@pytest.mark.asyncio
async def test_hazmat_no_match_scenario_exercises_failure_path():
    emitted = []

    async def mock_emitter(event_type: str, payload: dict):
        emitted.append((event_type, payload))

    records = await run_simulation(
        "hazmat_no_match",
        emit_event=mock_emitter,
        seconds_per_simulated_minute=0.001,
    )

    event_names = [e[0] for e in emitted]
    assert "incident.breakdown_reported" in event_names
    assert "candidates.none_found" in event_names
    assert "incident.cancelled" in event_names
    assert "incident.escrow_released" not in event_names


@pytest.mark.asyncio
async def test_simulation_api_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Fetch scenarios
        scenarios_res = await client.get("/api/v1/simulation/scenarios")
        assert scenarios_res.status_code == 200
        scenarios = scenarios_res.json()
        assert len(scenarios) == 3
        keys = [s["key"] for s in scenarios]
        assert "cold_chain_critical" in keys
        assert "hazmat_no_match" in keys
        assert "competing_rescuers" in keys

        # 2. Trigger simulation
        run_res = await client.post(
            "/api/v1/simulation/run",
            json={"scenario": "competing_rescuers", "seconds_per_simulated_minute": 0.001},
        )
        assert run_res.status_code == 200
        assert run_res.json()["status"] == "simulation_started"
