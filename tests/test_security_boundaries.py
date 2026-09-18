"""
Security boundary tests.

Each test here corresponds to a hole that was live in the codebase: privilege
escalation through self-assigned roles, unauthenticated escrow settlement,
cross-tenant incident access, and an unauthenticated WebSocket that delivered
every company's events to every client.
"""
import uuid

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient

from main import app
from services.core_api.app.auth import create_access_token


@pytest.mark.asyncio
async def test_registration_ignores_client_supplied_role(client: AsyncClient):
    """A caller must not be able to mint themselves an elevated token."""
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Escalation Attempt",
            "email": f"esc-{uuid.uuid4().hex[:8]}@example.com",
            "password": "CorrectHorseBattery1",
            "role": "ADMIN",
        },
    )
    # An unexpected field is rejected outright rather than silently dropped,
    # so the caller cannot believe they got what they asked for.
    assert res.status_code == 422

    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Ordinary Carrier",
            "email": f"ok-{uuid.uuid4().hex[:8]}@example.com",
            "password": "CorrectHorseBattery1",
        },
    )
    assert res.status_code == 201
    assert res.json()["role"] == "CARRIER_OWNER"


@pytest.mark.asyncio
async def test_escrow_routes_reject_anonymous_callers(client: AsyncClient):
    """Every escrow route moves or reveals money and must require a token."""
    for method, path, body in [
        ("post", "/api/v1/escrow/esc_anything/transition", {"new_state": "ACCEPTED"}),
        ("post", "/api/v1/escrow/esc_anything/verify", None),
        ("get", "/api/v1/escrow/esc_anything/ledger", None),
        ("get", "/api/v1/escrow/esc_anything", None),
    ]:
        res = await getattr(client, method)(path, **({"json": body} if body else {}))
        assert res.status_code == 401, f"{method.upper()} {path} returned {res.status_code}"


@pytest.mark.asyncio
async def test_escrow_cannot_be_released_by_direct_state_change(client: AsyncClient, new_company):
    """RELEASED must go through verification, which reads recorded evidence.

    Reaching it by a direct transition is what made the authorisation on the
    old /release endpoint pointless.
    """
    from shared.database import async_session
    from services.escrow_ledger.app.escrow_service import create_escrow

    owner = await new_company("Direct Release Owner")
    rescuer = await new_company("Direct Release Rescuer")

    async with async_session() as session:
        escrow = await create_escrow(
            session,
            amount_inr=5000.0,
            owner_company_id=owner.company_id,
            carrier_company_id=rescuer.company_id,
            carrier_payout_inr=4400.0,
        )
        escrow_id = escrow.id

    res = await client.post(
        f"/api/v1/escrow/{escrow_id}/transition",
        json={"new_state": "RELEASED"},
        headers=owner.headers,
    )
    assert res.status_code == 400
    assert "cannot be set directly" in res.json()["detail"]

    # The rescuer -- the party being paid -- cannot drive the lifecycle at all.
    res = await client.post(
        f"/api/v1/escrow/{escrow_id}/transition",
        json={"new_state": "ACCEPTED"},
        headers=rescuer.headers,
    )
    assert res.status_code == 403

    # An unrelated company cannot even learn the escrow exists.
    stranger = await new_company("Unrelated Carrier")
    res = await client.get(f"/api/v1/escrow/{escrow_id}", headers=stranger.headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_escrow_verification_refuses_to_pay_on_no_evidence(
    client: AsyncClient, new_company
):
    """An empty condition log must not settle the escrow in anyone's favour.

    The previous check computed ``[t for t in log if t > limit]`` and released
    the funds when that came back empty -- which it also does when the log is
    empty, so an escrow could be released on no evidence at all.
    """
    from shared.database import async_session
    from services.escrow_ledger.app.escrow_service import create_escrow, transition

    owner = await new_company("Evidence Owner")
    rescuer = await new_company("Evidence Rescuer")

    async with async_session() as session:
        escrow = await create_escrow(
            session,
            amount_inr=9000.0,
            owner_company_id=owner.company_id,
            carrier_company_id=rescuer.company_id,
            carrier_payout_inr=7900.0,
        )
        escrow_id = escrow.id
        await transition(session, escrow_id, "ACCEPTED")
        await transition(session, escrow_id, "IN_TRANSIT")
        await transition(session, escrow_id, "PENDING_VERIFICATION")

    res = await client.post(f"/api/v1/escrow/{escrow_id}/verify", headers=owner.headers)
    assert res.status_code == 200
    body = res.json()
    assert body["verification"]["verdict"] == "INSUFFICIENT_DATA"
    assert body["state"] == "PENDING_VERIFICATION", "funds must not move without evidence"


@pytest.mark.asyncio
async def test_incidents_are_not_readable_across_tenants(client: AsyncClient, new_company):
    """Company B must not be able to read, advance or audit company A's incident."""
    alpha = await new_company("Alpha Cold Chain")
    beta = await new_company("Beta Freight")

    truck = await client.post(
        "/api/v1/trucks",
        json={
            "registration_number": f"MH-01-{uuid.uuid4().hex[:6].upper()}",
            "latitude": 18.52,
            "longitude": 73.85,
            "refrigerated": True,
            "min_temp_c": -20.0,
            "max_volume_m3": 12.0,
            "max_weight_kg": 3500.0,
        },
        headers=alpha.headers,
    )
    assert truck.status_code == 201

    shipment = await client.post(
        "/api/v1/shipments",
        json={
            "truck_id": truck.json()["id"],
            "cargo_type": "Vaccines",
            "requires_refrigeration": True,
            "required_max_temp_c": 8.0,
            "volume_m3": 3.0,
            "weight_kg": 800.0,
            "value_inr": 400000.0,
        },
        headers=alpha.headers,
    )
    assert shipment.status_code == 201

    incident = await client.post(
        "/api/v1/incidents",
        json={"shipment_id": shipment.json()["id"], "lat": 18.6, "lng": 73.9},
        headers=alpha.headers,
    )
    assert incident.status_code == 201
    incident_id = incident.json()["id"]

    # The owner can see it.
    assert (
        await client.get(f"/api/v1/incidents/{incident_id}", headers=alpha.headers)
    ).status_code == 200

    # A competitor cannot -- and gets 404, not 403, so the id is not confirmed.
    for path in (
        f"/api/v1/incidents/{incident_id}",
        f"/api/v1/incidents/{incident_id}/timeline",
        f"/api/v1/incidents/{incident_id}/sla",
    ):
        res = await client.get(path, headers=beta.headers)
        assert res.status_code == 404, f"{path} leaked to another tenant"

    res = await client.post(
        f"/api/v1/incidents/{incident_id}/advance",
        json={"new_state": "TRIAGING"},
        headers=beta.headers,
    )
    assert res.status_code == 404

    # And anonymously, not at all.
    assert (await client.get(f"/api/v1/incidents/{incident_id}")).status_code == 401


@pytest.mark.asyncio
async def test_rescue_acceptance_requires_the_handshake(client: AsyncClient, new_company):
    """One party alone must not be able to bind a rescue."""
    alpha = await new_company("Handshake Owner")
    truck = await client.post(
        "/api/v1/trucks",
        json={
            "registration_number": f"MH-02-{uuid.uuid4().hex[:6].upper()}",
            "latitude": 18.52,
            "longitude": 73.85,
            "max_volume_m3": 10.0,
            "max_weight_kg": 3000.0,
        },
        headers=alpha.headers,
    )
    shipment = await client.post(
        "/api/v1/shipments",
        json={
            "truck_id": truck.json()["id"],
            "cargo_type": "Dry goods",
            "volume_m3": 2.0,
            "weight_kg": 500.0,
            "value_inr": 100000.0,
        },
        headers=alpha.headers,
    )
    incident = await client.post(
        "/api/v1/incidents",
        json={"shipment_id": shipment.json()["id"], "lat": 18.6, "lng": 73.9},
        headers=alpha.headers,
    )
    incident_id = incident.json()["id"]

    res = await client.post(
        f"/api/v1/incidents/{incident_id}/advance",
        json={"new_state": "RESCUE_ACCEPTED"},
        headers=alpha.headers,
    )
    assert res.status_code == 400
    assert "two-way offer handshake" in res.json()["detail"]


@pytest.mark.asyncio
async def test_actor_id_cannot_be_supplied_by_the_caller(client: AsyncClient, new_company):
    """The audit trail's actor must come from the token, not the request body."""
    alpha = await new_company("Audit Integrity")
    res = await client.post(
        "/api/v1/incidents",
        json={
            "shipment_id": "shp_nonexistent",
            "lat": 18.6,
            "lng": 73.9,
            "actor_id": "somebody_else",
        },
        headers=alpha.headers,
    )
    assert res.status_code == 422


def test_websocket_requires_an_auth_frame():
    """An unauthenticated socket must be closed, not subscribed."""
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws") as ws:
            ws.send_json({"type": "ping"})
            with pytest.raises(Exception):
                ws.receive_json()


def test_websocket_rejects_an_invalid_token():
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": "not-a-real-jwt"})
            with pytest.raises(Exception):
                ws.receive_json()


def test_websocket_accepts_a_valid_token_and_scopes_its_rooms():
    token = create_access_token(
        subject="ops@scoped.example",
        company_id="comp_scoped_test",
        role="CARRIER_OWNER",
        principal_type="company",
        principal_id="comp_scoped_test",
    )
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": token})
            ack = ws.receive_json()
            assert ack["type"] == "auth.ok"
            assert ack["payload"]["companyId"] == "comp_scoped_test"
            assert "company:comp_scoped_test" in ack["payload"]["rooms"]

            # A room belonging to somebody else is refused.
            ws.send_json({"type": "subscribe", "room": "company:comp_someone_else"})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["payload"]["code"] == "forbidden_room"
