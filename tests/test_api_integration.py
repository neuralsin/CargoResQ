import pytest
from httpx import AsyncClient

from services.core_api.app.auth import create_access_token
from shared.rbac import Role

# The `client` fixture lives in conftest.py and points at a throwaway test
# database. This module used to define its own fixture that ran drop_all
# against the *live* engine, which destroyed the repository's cargoresq.db on
# every test run and left every other test file dependent on this one having
# executed first.


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

    # 6. Pricing
    price_res = await client.post(
        "/api/v1/pricing/quote",
        headers=headers_a,
        json={
            "distance_km": 25.0,
            "requires_refrigeration": True,
            "weight_kg": 850.0,
            "volume_m3": 3.5,
            "minutes_until_spoilage": 90.0,
            "num_compatible_nearby": 2,
            "cargo_value_inr": 850000.0,
        },
    )
    assert price_res.status_code == 200
    price_data = price_res.json()
    assert price_data["base_rate_inr"] == 1625.0  # 25 km at INR 65/km
    assert price_data["urgency_multiplier"] > 1.0
    assert price_data["total_inr"] > price_data["subtotal_inr"]
    # The carrier keeps the large majority of what the owner pays.
    assert price_data["carrier_payout_inr"] / price_data["total_inr"] > 0.85
    # And the price never runs away from ordinary freight for the same leg.
    assert price_data["total_inr"] <= price_data["normal_freight_inr"] * 3.0

    # 7. Candidate Matching
    match_res = await client.post(
        "/api/v1/matching/candidates",
        headers=headers_a,
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
async def test_incident_orchestration_and_escrow_settlement(
    client: AsyncClient, new_company
):
    """The lifecycle end to end, as an authorised owner.

    The previous version of this test walked the escrow state machine through
    the *unauthenticated* /transition route and then asserted that /release
    was guarded -- proving the guard worked while demonstrating the bypass
    that made it irrelevant.
    """
    owner = await new_company("Orchestration Owner")
    rescuer = await new_company("Orchestration Rescuer")

    truck = await client.post(
        "/api/v1/trucks",
        headers=owner.headers,
        json={
            "registration_number": f"KA-05-{uuid.uuid4().hex[:6].upper()}",
            "latitude": 12.97,
            "longitude": 77.59,
            "refrigerated": True,
            "min_temp_c": -18.0,
            "max_volume_m3": 14.0,
            "max_weight_kg": 4000.0,
        },
    )
    assert truck.status_code == 201

    shipment = await client.post(
        "/api/v1/shipments",
        headers=owner.headers,
        json={
            "truck_id": truck.json()["id"],
            "cargo_type": "Vaccine consignment",
            "requires_refrigeration": True,
            "required_max_temp_c": 8.0,
            "volume_m3": 4.0,
            "weight_kg": 900.0,
            "value_inr": 500000.0,
        },
    )
    assert shipment.status_code == 201
    shipment_id = shipment.json()["id"]

    inc_res = await client.post(
        "/api/v1/incidents",
        headers=owner.headers,
        json={
            "shipment_id": shipment_id,
            "lat": 12.97,
            "lng": 77.59,
            "minutes_until_spoilage": 50.0,
        },
    )
    assert inc_res.status_code == 201
    inc_id = inc_res.json()["id"]

    adv_res = await client.post(
        f"/api/v1/incidents/{inc_id}/advance",
        headers=owner.headers,
        json={"new_state": "TRIAGING"},
    )
    assert adv_res.status_code == 200
    assert adv_res.json()["state"] == "TRIAGING"

    timeline_res = await client.get(
        f"/api/v1/incidents/{inc_id}/timeline", headers=owner.headers
    )
    assert timeline_res.status_code == 200
    events = timeline_res.json()
    assert len(events) == 2
    # The audit trail records the authenticated principal, not a request field.
    assert all(e["actorId"] for e in events)

    sla_res = await client.get(f"/api/v1/incidents/{inc_id}/sla", headers=owner.headers)
    assert sla_res.status_code == 200
    assert "match" in sla_res.json()

    # Escrows are created by the rescue handshake, not over HTTP.
    from shared.database import async_session
    from services.escrow_ledger.app.escrow_service import create_escrow, transition

    async with async_session() as session:
        escrow = await create_escrow(
            session,
            amount_inr=7200.0,
            carrier_payout_inr=6336.0,
            owner_company_id=owner.company_id,
            carrier_company_id=rescuer.company_id,
            incident_id=inc_id,
        )
        escrow_id = escrow.id
        await transition(session, escrow_id, "ACCEPTED")
        await transition(session, escrow_id, "IN_TRANSIT")
        await transition(session, escrow_id, "PENDING_VERIFICATION")

    # A driver token cannot settle an escrow.
    driver_token = create_access_token(
        "driver@truck.example",
        owner.company_id,
        role=Role.DRIVER.value,
        principal_type="driver",
        principal_id="drv_x",
    )
    forbidden = await client.post(
        f"/api/v1/escrow/{escrow_id}/verify",
        headers={"Authorization": f"Bearer {driver_token}"},
    )
    assert forbidden.status_code == 403

    # The owner can, and with no recorded condition data it must not pay out.
    settle = await client.post(
        f"/api/v1/escrow/{escrow_id}/verify", headers=owner.headers
    )
    assert settle.status_code == 200
    assert settle.json()["verification"]["verdict"] == "INSUFFICIENT_DATA"
    assert settle.json()["state"] == "PENDING_VERIFICATION"

    # The ledger shows the hold, and it balances.
    ledger_res = await client.get(
        f"/api/v1/escrow/{escrow_id}/ledger", headers=owner.headers
    )
    assert ledger_res.status_code == 200
    body = ledger_res.json()
    assert body["balance"]["balanced"]
    assert {e["postingType"] for e in body["entries"]} == {"HOLD"}

    # The rescuer sees the escrow but not the owner's total or the fee.
    carrier_view = await client.get(
        f"/api/v1/escrow/{escrow_id}", headers=rescuer.headers
    )
    assert carrier_view.status_code == 200
    assert "amountInr" not in carrier_view.json()
    assert carrier_view.json()["payoutInr"] == 6336.0
