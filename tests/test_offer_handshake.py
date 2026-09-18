"""
The two-way rescue handshake.

The property under test: a rescue binds only when both the stranded cargo
owner and the helping carrier have agreed, and neither party alone can commit
the other. Previously a single unauthenticated call set an incident to
RESCUE_ACCEPTED and assigned another company's truck to it.
"""
import uuid

import pytest
from httpx import AsyncClient


async def _truck(client, account, *, lat, lng, refrigerated=True, min_temp=-20.0,
                 volume=24.0, weight=7000.0):
    res = await client.post(
        "/api/v1/trucks",
        headers=account.headers,
        json={
            "registration_number": f"MH-{uuid.uuid4().hex[:8].upper()}",
            "latitude": lat,
            "longitude": lng,
            "refrigerated": refrigerated,
            "min_temp_c": min_temp,
            "max_volume_m3": volume,
            "max_weight_kg": weight,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _stranded(client, owner, *, lat, lng):
    """An owner with a broken-down reefer and an open incident."""
    truck_id = await _truck(client, owner, lat=lat, lng=lng)
    shipment = await client.post(
        "/api/v1/shipments",
        headers=owner.headers,
        json={
            "truck_id": truck_id,
            "cargo_type": "Refrigerated insulin",
            "requires_refrigeration": True,
            "required_max_temp_c": 8.0,
            "volume_m3": 3.5,
            "weight_kg": 850.0,
            "value_inr": 480000.0,
        },
    )
    assert shipment.status_code == 201, shipment.text
    incident = await client.post(
        "/api/v1/incidents",
        headers=owner.headers,
        json={
            "shipment_id": shipment.json()["id"],
            "lat": lat,
            "lng": lng,
            "minutes_until_spoilage": 90.0,
        },
    )
    assert incident.status_code == 201, incident.text
    incident_id = incident.json()["id"]
    await client.post(
        f"/api/v1/incidents/{incident_id}/advance",
        headers=owner.headers,
        json={"new_state": "TRIAGING"},
    )
    await client.post(
        f"/api/v1/incidents/{incident_id}/advance",
        headers=owner.headers,
        json={"new_state": "MATCHING"},
    )
    return incident_id, shipment.json()["id"]


async def _fan_out(client, owner, incident_id):
    res = await client.post(
        f"/api/v1/incidents/{incident_id}/offers",
        headers=owner.headers,
        json={"radius_km": 60, "max_candidates": 5, "ttl_seconds": 300},
    )
    return res


@pytest.mark.asyncio
async def test_neither_party_alone_can_bind(client: AsyncClient, new_company, region):
    owner = await new_company("Stranded Owner")
    rescuer = await new_company("Willing Rescuer")
    await _truck(client, rescuer, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    res = await _fan_out(client, owner, incident_id)
    assert res.status_code == 201, res.text
    offer_id = res.json()["offers"][0]["id"]

    # The carrier agrees. That alone must not bind anything.
    accept = await client.post(
        f"/api/v1/offers/{offer_id}/carrier-accept", headers=rescuer.headers
    )
    assert accept.status_code == 200, accept.text
    assert accept.json()["bound"] is False
    assert accept.json()["offer"]["state"] == "CARRIER_ACCEPTED"

    incident = await client.get(f"/api/v1/incidents/{incident_id}", headers=owner.headers)
    assert incident.json()["state"] != "RESCUE_ACCEPTED"

    # The owner confirms. Now, and only now, it binds.
    confirm = await client.post(
        f"/api/v1/offers/{offer_id}/owner-confirm", headers=owner.headers
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["bound"] is True
    assert confirm.json()["offer"]["state"] == "BOUND"

    incident = await client.get(f"/api/v1/incidents/{incident_id}", headers=owner.headers)
    assert incident.json()["state"] == "RESCUE_ACCEPTED"
    assert incident.json()["assignedTruckId"]


@pytest.mark.asyncio
async def test_handshake_works_in_either_order(client: AsyncClient, new_company, region):
    """Owner-first must behave exactly like carrier-first."""
    owner = await new_company("Preconfirm Owner")
    rescuer = await new_company("Preconfirm Rescuer")
    await _truck(client, rescuer, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    offer_id = (await _fan_out(client, owner, incident_id)).json()["offers"][0]["id"]

    confirm = await client.post(
        f"/api/v1/offers/{offer_id}/owner-confirm", headers=owner.headers
    )
    assert confirm.json()["bound"] is False
    assert confirm.json()["offer"]["state"] == "OWNER_CONFIRMED"

    accept = await client.post(
        f"/api/v1/offers/{offer_id}/carrier-accept", headers=rescuer.headers
    )
    assert accept.json()["bound"] is True


@pytest.mark.asyncio
async def test_binding_supersedes_every_sibling_offer(client: AsyncClient, new_company, region):
    """The losing carriers are told promptly -- they are holding a truck for us."""
    owner = await new_company("Multi Owner")
    a = await new_company("Rescuer A")
    b = await new_company("Rescuer B")
    await _truck(client, a, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    await _truck(client, b, lat=region["lat"] + 0.09, lng=region["lng"] + 0.09)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    offers = (await _fan_out(client, owner, incident_id)).json()["offers"]
    assert len(offers) == 2, "both carriers should have been offered the job"

    winner = next(o for o in offers if o["carrierCompanyId"] == a.company_id)
    loser = next(o for o in offers if o["carrierCompanyId"] == b.company_id)

    await client.post(f"/api/v1/offers/{winner['id']}/carrier-accept", headers=a.headers)
    bound = await client.post(
        f"/api/v1/offers/{winner['id']}/owner-confirm", headers=owner.headers
    )
    assert bound.json()["bound"] is True

    stale = await client.get(f"/api/v1/offers/{loser['id']}", headers=b.headers)
    assert stale.json()["state"] == "SUPERSEDED"


@pytest.mark.asyncio
async def test_owner_cannot_confirm_two_carriers(client: AsyncClient, new_company, region):
    owner = await new_company("Double Confirm Owner")
    a = await new_company("Double A")
    b = await new_company("Double B")
    await _truck(client, a, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    await _truck(client, b, lat=region["lat"] + 0.09, lng=region["lng"] + 0.09)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    offers = (await _fan_out(client, owner, incident_id)).json()["offers"]

    first = await client.post(
        f"/api/v1/offers/{offers[0]['id']}/owner-confirm", headers=owner.headers
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/offers/{offers[1]['id']}/owner-confirm", headers=owner.headers
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "another_offer_confirmed"


@pytest.mark.asyncio
async def test_carrier_never_sees_the_owners_price(client: AsyncClient, new_company, region):
    """Price visibility is the mechanism, not a UI convention."""
    owner = await new_company("Price Owner")
    rescuer = await new_company("Price Rescuer")
    await _truck(client, rescuer, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    offers = (await _fan_out(client, owner, incident_id)).json()["offers"]
    offer = offers[0]

    # The owner sees the whole arithmetic, because they are paying it.
    assert offer["priceTotalInr"] > 0
    assert offer["platformFeeInr"] > 0
    assert "priceBreakdown" in offer

    carrier_side = await client.get(
        f"/api/v1/offers/{offer['id']}", headers=rescuer.headers
    )
    body = carrier_side.json()
    assert "priceTotalInr" not in body
    assert "platformFeeInr" not in body
    assert "priceBreakdown" not in body
    # They do see what they earn -- consent to a job needs that much.
    assert body["payoutInr"] == offer["carrierPayoutInr"]
    assert body["payoutInr"] < offer["priceTotalInr"]


@pytest.mark.asyncio
async def test_offer_shows_the_carriers_reputation_at_decision_time(
    client: AsyncClient, new_company, region
):
    """The owner is deciding whether to trust a stranger; show them who."""
    owner = await new_company("Reputation Owner")
    rescuer = await new_company("Reputation Rescuer")
    await _truck(client, rescuer, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    offer = (await _fan_out(client, owner, incident_id)).json()["offers"][0]
    reputation = offer["carrierReputation"]
    assert reputation is not None
    assert reputation["companyId"] == rescuer.company_id
    # A brand-new carrier is presented as unproven, not as five stars.
    assert reputation["isNewCounterparty"] is True
    assert reputation["ratingCount"] == 0
    assert any(s["kind"] == "neutral" for s in reputation["signals"])


@pytest.mark.asyncio
async def test_third_party_cannot_touch_an_offer(client: AsyncClient, new_company, region):
    owner = await new_company("Private Owner")
    rescuer = await new_company("Private Rescuer")
    stranger = await new_company("Nosy Stranger")
    await _truck(client, rescuer, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    offer_id = (await _fan_out(client, owner, incident_id)).json()["offers"][0]["id"]

    assert (
        await client.get(f"/api/v1/offers/{offer_id}", headers=stranger.headers)
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/offers/{offer_id}/carrier-accept", headers=stranger.headers
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/offers/{offer_id}/owner-confirm", headers=stranger.headers
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_binding_creates_a_held_escrow(client: AsyncClient, new_company, region):
    """Both parties committing is what puts the money in escrow."""
    owner = await new_company("Escrow Owner")
    rescuer = await new_company("Escrow Rescuer")
    await _truck(client, rescuer, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    offer = (await _fan_out(client, owner, incident_id)).json()["offers"][0]
    await client.post(f"/api/v1/offers/{offer['id']}/carrier-accept", headers=rescuer.headers)
    bound = await client.post(
        f"/api/v1/offers/{offer['id']}/owner-confirm", headers=owner.headers
    )
    escrow_id = bound.json()["offer"]["escrowId"]
    assert escrow_id

    ledger = await client.get(f"/api/v1/escrow/{escrow_id}/ledger", headers=owner.headers)
    assert ledger.status_code == 200
    body = ledger.json()
    assert body["balance"]["balanced"], "the hold must balance"
    assert {e["postingType"] for e in body["entries"]} == {"HOLD"}


@pytest.mark.asyncio
async def test_no_compatible_truck_is_reported_not_forced(
    client: AsyncClient, new_company, region
):
    """Refusing a bad match is the correct outcome, and must be explained."""
    owner = await new_company("Hazmat Owner")
    other = await new_company("Dry Van Only")
    # A truck that is nearby but cannot carry the cargo.
    await _truck(client, other, refrigerated=False, min_temp=None,
                 lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    res = await _fan_out(client, owner, incident_id)
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "no_candidates"


@pytest.mark.asyncio
async def test_cancelling_a_bound_rescue_unwinds_it(client: AsyncClient, new_company, region):
    owner = await new_company("Cancel Owner")
    rescuer = await new_company("Cancel Rescuer")
    truck_id = await _truck(client, rescuer, lat=region["lat"] + 0.06, lng=region["lng"] + 0.06)
    incident_id, _ = await _stranded(client, owner, lat=region["lat"], lng=region["lng"])

    offer = (await _fan_out(client, owner, incident_id)).json()["offers"][0]
    await client.post(f"/api/v1/offers/{offer['id']}/carrier-accept", headers=rescuer.headers)
    bound = await client.post(
        f"/api/v1/offers/{offer['id']}/owner-confirm", headers=owner.headers
    )
    escrow_id = bound.json()["offer"]["escrowId"]

    cancelled = await client.post(
        f"/api/v1/offers/{offer['id']}/cancel",
        headers=rescuer.headers,
        json={"reason": "Rescue truck suffered its own breakdown"},
    )
    assert cancelled.status_code == 200

    # The hold is reversed and the books still balance.
    ledger = await client.get(f"/api/v1/escrow/{escrow_id}/ledger", headers=owner.headers)
    body = ledger.json()
    assert body["balance"]["balanced"]
    assert "CANCEL" in {e["postingType"] for e in body["entries"]}

    # The truck is released, and the incident is looking for help again.
    trucks = await client.get("/api/v1/trucks", headers=rescuer.headers)
    freed = next(t for t in trucks.json() if t["id"] == truck_id)
    assert freed["status"] == "idle"

    incident = await client.get(f"/api/v1/incidents/{incident_id}", headers=owner.headers)
    assert incident.json()["state"] == "MATCHING"
