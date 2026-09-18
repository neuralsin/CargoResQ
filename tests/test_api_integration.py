import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from main import app
from shared.database import init_database
from shared.rbac import Role
from services.core_api.app.auth import create_access_token


@pytest_asyncio.fixture
async def client():
    from shared.database import engine, Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_and_metrics(client: AsyncClient):
    res = await client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"

    metrics_res = await client.get("/metrics")
    assert metrics_res.status_code == 200
    assert b"matching_duration_seconds" in metrics_res.content


import uuid

@pytest.mark.asyncio
async def test_full_auth_and_fleet_workflow(client: AsyncClient):
    uid = uuid.uuid4().hex[:6]
    # 1. Register Carrier A
    reg_a = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"FastTrack Logistics {uid}",
            "email": f"ops_{uid}@fasttrack.com",
            "password": "Password123!",
            "role": "CARRIER_OWNER",
        },
    )
    assert reg_a.status_code == 201
    token_a = reg_a.json()["access_token"]
    company_a_id = reg_a.json()["company_id"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 2. Register Carrier B
    reg_b = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"BlueDart Express {uid}",
            "email": f"ops_{uid}@bluedart.com",
            "password": "Password123!",
            "role": "CARRIER_OWNER",
        },
    )
    assert reg_b.status_code == 201
    token_b = reg_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 3. Add Truck for Carrier A
    truck_res = await client.post(
        "/api/v1/trucks",
        headers=headers_a,
        json={
            "registration_number": f"KA-01-{uid}",
            "latitude": 12.9716,
            "longitude": 77.5946,
            "status": "idle",
            "refrigerated": True,
            "min_temp_c": -5.0,
            "hazmat_certified": True,
            "max_volume_m3": 12.0,
            "max_weight_kg": 5000.0,
        },
    )
    assert truck_res.status_code == 201
    truck_id = truck_res.json()["id"]

    # 4. Check Query-level Isolation: Carrier B MUST NOT see Carrier A's truck
    list_b = await client.get("/api/v1/trucks", headers=headers_b)
    assert list_b.status_code == 200
    assert len(list_b.json()) == 0

    # Carrier A sees their own truck
    list_a = await client.get("/api/v1/trucks", headers=headers_a)
    assert list_a.status_code == 200
    assert len(list_a.json()) == 1
    assert list_a.json()[0]["id"] == truck_id

    # 5. Create Shipment for Carrier A
    shipment_res = await client.post(
        "/api/v1/shipments",
        headers=headers_a,
        json={
            "truck_id": truck_id,
            "cargo_type": "Pharmaceuticals",
            "requires_refrigeration": True,
            "required_max_temp_c": 4.0,
            "is_hazmat": False,
            "volume_m3": 4.0,
            "weight_kg": 1200.0,
            "value_inr": 850000.0,
        },
    )
    assert shipment_res.status_code == 201
    shipment_id = shipment_res.json()["id"]

    # 6. Pricing Calculation
    price_res = await client.post(
        "/api/v1/pricing/calculate",
        json={
            "distance_km": 25.0,
            "cargo_class": "refrigerated",
            "hours_to_spoilage": 1.5,
            "num_compatible_nearby": 2,
        },
    )
    assert price_res.status_code == 200
    price_data = price_res.json()
    assert price_data["base_rate_inr"] == 1625.0  # 25 km * 65 INR
    assert price_data["urgency_premium_inr"] > 0
    assert price_data["total_inr"] > 1625.0

    # 7. Candidate Matching
    match_res = await client.post(
        "/api/v1/matching/candidates",
        json={
            "lat": 12.9720,
            "lng": 77.5950,
            "requiresRefrigeration": True,
            "requiredMaxTemp": 4.0,
            "isHazmat": False,
            "volumeM3": 4.0,
            "weightKg": 1200.0,
            "radiusKm": 50.0,
            "limit": 5,
        },
    )
    assert match_res.status_code == 200
    candidates = match_res.json()["candidates"]
    assert len(candidates) >= 1
    assert any(c["truckId"] == truck_id for c in candidates)
    matched_c = next(c for c in candidates if c["truckId"] == truck_id)
    assert matched_c["score"] > 0
    assert len(matched_c["reasons"]) > 0

    # 8. Report Breakdown
    breakdown_res = await client.post(
        "/api/v1/breakdowns",
        headers=headers_a,
        json={
            "shipment_id": shipment_id,
            "lat": 12.9720,
            "lng": 77.5950,
            "hours_to_spoilage": 1.5,
            "client_request_id": f"req_{uid}",
        },
    )
    assert breakdown_res.status_code == 200
    assert breakdown_res.json()["wasProcessed"] is True

    # Duplicate breakdown test (Idempotency Phase 18.1)
    dup_res = await client.post(
        "/api/v1/breakdowns",
        headers=headers_a,
        json={
            "shipment_id": shipment_id,
            "lat": 12.9720,
            "lng": 77.5950,
            "hours_to_spoilage": 1.5,
            "client_request_id": f"req_{uid}",
        },
    )
    assert dup_res.status_code == 200
    assert dup_res.json()["wasProcessed"] is False


@pytest.mark.asyncio
async def test_incident_orchestration_and_escrow_rbac(client: AsyncClient):
    # 1. Create incident
    inc_res = await client.post(
        "/api/v1/incidents",
        json={
            "shipment_id": "shp_inc_test",
            "lat": 12.97,
            "lng": 77.59,
            "minutes_until_spoilage": 50.0,
            "actor_id": "sensor_01",
        },
    )
    assert inc_res.status_code == 201
    inc_id = inc_res.json()["id"]

    # 2. Advance through lifecycle
    adv_res = await client.post(
        f"/api/v1/incidents/{inc_id}/advance",
        json={"new_state": "TRIAGING", "actor_id": "operator_jane"},
    )
    assert adv_res.status_code == 200
    assert adv_res.json()["state"] == "TRIAGING"

    # 3. Check timeline
    timeline_res = await client.get(f"/api/v1/incidents/{inc_id}/timeline")
    assert timeline_res.status_code == 200
    events = timeline_res.json()
    assert len(events) == 2

    # 4. Check SLA endpoint
    sla_res = await client.get(f"/api/v1/incidents/{inc_id}/sla")
    assert sla_res.status_code == 200
    assert "match" in sla_res.json()

    # 5. Create Escrow
    escrow_res = await client.post(
        "/api/v1/escrow",
        json={"match_id": "match_99", "amount_inr": 7200.0},
    )
    assert escrow_res.status_code == 201
    escrow_id = escrow_res.json()["id"]

    # 6. Phase 19 RBAC Test: DRIVER token should get 403 on escrow release
    driver_token = create_access_token("driver@truck.com", "comp_driver", role="DRIVER")
    finance_token = create_access_token("cfo@fin.com", "comp_fin", role="FINANCE")

    forbidden_res = await client.post(
        f"/api/v1/escrow/{escrow_id}/release",
        headers={"Authorization": f"Bearer {driver_token}"},
    )
    assert forbidden_res.status_code == 403

    # Fast forward states for valid release: ACCEPTED -> IN_TRANSIT -> PENDING_VERIFICATION
    await client.post(f"/api/v1/escrow/{escrow_id}/transition", json={"new_state": "ACCEPTED"})
    await client.post(f"/api/v1/escrow/{escrow_id}/transition", json={"new_state": "IN_TRANSIT"})
    await client.post(f"/api/v1/escrow/{escrow_id}/transition", json={"new_state": "PENDING_VERIFICATION"})

    # Authorized release with FINANCE token
    auth_release_res = await client.post(
        f"/api/v1/escrow/{escrow_id}/release",
        headers={"Authorization": f"Bearer {finance_token}"},
    )
    assert auth_release_res.status_code == 200
    assert auth_release_res.json()["state"] == "RELEASED"

    # Verify double-entry ledger entries exist
    ledger_res = await client.get(f"/api/v1/escrow/{escrow_id}/ledger")
    assert ledger_res.status_code == 200
    entries = ledger_res.json()
    assert len(entries) == 2
