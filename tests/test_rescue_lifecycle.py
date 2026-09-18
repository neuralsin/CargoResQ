"""
The whole rescue, start to finish.

This is the path the product exists for: a truck breaks down, a competitor's
truck is offered the job, both sides agree, the money is held, the cargo is
carried and its condition recorded, and the funds are released only because
that record proves the cargo arrived in spec.

The individual pieces are tested elsewhere. What this file pins down is that
they stay joined together -- particularly the two places they had come apart:
the escrow not following the incident, and the rescuing driver being unable to
record the condition of cargo sitting in their own trailer.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


async def _truck(client, account, *, lat, lng, refrigerated=True):
    res = await client.post(
        "/api/v1/trucks",
        headers=account.headers,
        json={
            "registration_number": f"MH-{uuid.uuid4().hex[:8].upper()}",
            "latitude": lat,
            "longitude": lng,
            "refrigerated": refrigerated,
            "min_temp_c": -20.0,
            "max_volume_m3": 24.0,
            "max_weight_kg": 7000.0,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _driver(client, account, name, truck_id):
    res = await client.post(
        "/api/v1/driver/register",
        headers=account.headers,
        json={
            "name": name,
            "email": f"{uuid.uuid4().hex[:8]}@lifecycle.example",
            "password": "DriverPassword123!",
            "assigned_truck_id": truck_id,
        },
    )
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def _reading(client, headers, shipment_id, temperature, minutes_ago):
    return await client.post(
        "/api/v1/telemetry/readings",
        headers=headers,
        json={
            "sensor_id": "sensor-" + uuid.uuid4().hex[:8],
            "readings": [
                {
                    "client_reading_id": uuid.uuid4().hex[:16],
                    "shipment_id": shipment_id,
                    "recorded_at": (
                        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
                    ).isoformat(),
                    "temperature_c": temperature,
                }
            ],
        },
    )


async def _bound_rescue(client, new_company, region):
    """Set up a cross-carrier rescue and bind it. Returns the moving parts."""
    owner = await new_company("Stranded Carrier")
    rescuer = await new_company("Helping Carrier")

    owner_truck = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    owner_driver = await _driver(client, owner, "Stranded Driver", owner_truck)
    rescue_truck = await _truck(
        client, rescuer, lat=region["lat"] + 0.05, lng=region["lng"] + 0.05
    )
    rescue_driver = await _driver(client, rescuer, "Helping Driver", rescue_truck)

    shipment = (
        await client.post(
            "/api/v1/shipments",
            headers=owner.headers,
            json={
                "truck_id": owner_truck,
                "cargo_type": "Refrigerated insulin",
                "requires_refrigeration": True,
                "required_max_temp_c": 8.0,
                "volume_m3": 3.5,
                "weight_kg": 850.0,
                "value_inr": 480000.0,
            },
        )
    ).json()

    breakdown = await client.post(
        "/api/v1/driver/report-breakdown",
        headers=owner_driver,
        json={
            "lat": region["lat"],
            "lng": region["lng"],
            "hours_to_spoilage": 1.5,
            "client_request_id": uuid.uuid4().hex,
        },
    )
    assert breakdown.status_code == 200, breakdown.text

    incident_id = (await client.get("/api/v1/incidents", headers=owner.headers)).json()[0]["id"]

    for state in ("TRIAGING", "MATCHING"):
        res = await client.post(
            f"/api/v1/incidents/{incident_id}/advance",
            headers=owner.headers,
            json={"new_state": state},
        )
        assert res.status_code == 200, res.text

    fan = await client.post(
        f"/api/v1/incidents/{incident_id}/offers",
        headers=owner.headers,
        json={"radius_km": 60, "max_candidates": 5, "ttl_seconds": 600},
    )
    assert fan.status_code == 201, fan.text
    offer = next(
        o for o in fan.json()["offers"] if o["carrierCompanyId"] == rescuer.company_id
    )

    await client.post(f"/api/v1/offers/{offer['id']}/carrier-accept", headers=rescuer.headers)
    bound = await client.post(
        f"/api/v1/offers/{offer['id']}/owner-confirm", headers=owner.headers
    )
    assert bound.json()["bound"] is True

    return {
        "owner": owner,
        "rescuer": rescuer,
        "rescue_driver": rescue_driver,
        "incident_id": incident_id,
        "shipment_id": shipment["id"],
        "escrow_id": bound.json()["offer"]["escrowId"],
        "price": bound.json()["offer"]["priceTotalInr"],
        "payout": bound.json()["offer"]["carrierPayoutInr"],
    }


@pytest.mark.asyncio
async def test_escrow_follows_the_rescue_and_releases_on_evidence(
    client: AsyncClient, new_company, region
):
    """The full path, and the ledger balancing at every step."""
    r = await _bound_rescue(client, new_company, region)
    owner, escrow_id = r["owner"], r["escrow_id"]

    # Binding holds the money. Nothing is paid out yet.
    ledger = (
        await client.get(f"/api/v1/escrow/{escrow_id}/ledger", headers=owner.headers)
    ).json()
    assert ledger["balance"]["balanced"]
    assert {e["postingType"] for e in ledger["entries"]} == {"HOLD"}

    async def advance(state):
        res = await client.post(
            f"/api/v1/incidents/{r['incident_id']}/advance",
            headers=owner.headers,
            json={"new_state": state},
        )
        assert res.status_code == 200, res.text
        return res.json()

    await advance("DRIVER_EN_ROUTE")
    await advance("CARGO_TRANSFER")

    # The escrow follows the cargo without anybody driving it separately.
    in_transit = await advance("RESCUE_IN_TRANSIT")
    assert in_transit["escrow"]["state"] == "IN_TRANSIT"

    # Condition recorded by the rescuing driver, who is the one carrying it.
    for minutes, temperature in ((9, 4.1), (6, 4.6), (3, 5.2)):
        res = await _reading(
            client, r["rescue_driver"], r["shipment_id"], temperature, minutes
        )
        assert res.status_code == 200, res.text
        assert res.json()["accepted"] == 1

    delivered = await advance("DELIVERED")
    assert delivered["escrow"]["state"] == "PENDING_VERIFICATION"

    # Settlement reads the recorded log, not anything the caller supplies.
    settled = await client.post(
        f"/api/v1/escrow/{escrow_id}/verify", headers=owner.headers
    )
    body = settled.json()
    assert body["verification"]["verdict"] == "PASS"
    assert body["verification"]["readingsCount"] == 3
    assert body["state"] == "RELEASED"
    # And the incident ends up saying what the ledger concluded.
    assert body["incidentState"] == "ESCROW_RELEASED"

    ledger = (
        await client.get(f"/api/v1/escrow/{escrow_id}/ledger", headers=owner.headers)
    ).json()
    assert ledger["balance"]["balanced"]
    accounts = {e["account"]: (e["debitInr"], e["creditInr"]) for e in ledger["entries"]}
    assert accounts["carrier_receivable"] == (0.0, r["payout"])
    assert accounts["platform_fee_income"][1] == pytest.approx(
        r["price"] - r["payout"], abs=0.01
    )


@pytest.mark.asyncio
async def test_a_cold_chain_breach_disputes_instead_of_paying(
    client: AsyncClient, new_company, region
):
    """Cargo that arrived out of spec must not release the funds."""
    r = await _bound_rescue(client, new_company, region)
    owner, escrow_id = r["owner"], r["escrow_id"]

    for state in ("DRIVER_EN_ROUTE", "CARGO_TRANSFER", "RESCUE_IN_TRANSIT"):
        await client.post(
            f"/api/v1/incidents/{r['incident_id']}/advance",
            headers=owner.headers,
            json={"new_state": state},
        )

    # One reading above the 8C limit is enough.
    for minutes, temperature in ((9, 4.1), (6, 11.4), (3, 5.0)):
        await _reading(client, r["rescue_driver"], r["shipment_id"], temperature, minutes)

    await client.post(
        f"/api/v1/incidents/{r['incident_id']}/advance",
        headers=owner.headers,
        json={"new_state": "DELIVERED"},
    )

    settled = await client.post(
        f"/api/v1/escrow/{escrow_id}/verify", headers=owner.headers
    )
    body = settled.json()
    assert body["verification"]["verdict"] == "FAIL"
    assert body["verification"]["breachCount"] == 1
    assert body["state"] == "DISPUTED"
    assert body["incidentState"] == "DISPUTED"

    # Nobody was paid, and the books still balance.
    ledger = (
        await client.get(f"/api/v1/escrow/{escrow_id}/ledger", headers=owner.headers)
    ).json()
    assert ledger["balance"]["balanced"]
    assert "RELEASE" not in {e["postingType"] for e in ledger["entries"]}


@pytest.mark.asyncio
async def test_the_rescuing_driver_may_record_the_cargo_they_carry(
    client: AsyncClient, new_company, region
):
    """The cargo is in their trailer; they are the one who can read the gauge.

    This was refused because the shipment belongs to another company, which
    meant that on a cross-carrier rescue -- the entire point of the product --
    no condition data could be recorded and the escrow could never release.
    """
    r = await _bound_rescue(client, new_company, region)

    accepted = await _reading(
        client, r["rescue_driver"], r["shipment_id"], 4.4, minutes_ago=2
    )
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] == 1

    # An unrelated carrier still cannot write to that log.
    stranger = await new_company("Unrelated Carrier")
    stranger_truck = await _truck(
        client, stranger, lat=region["lat"] + 0.2, lng=region["lng"] + 0.2
    )
    stranger_driver = await _driver(client, stranger, "Nosy Driver", stranger_truck)

    refused = await _reading(
        client, stranger_driver, r["shipment_id"], 2.0, minutes_ago=1
    )
    assert refused.json()["accepted"] == 0
    assert refused.json()["rejected"]


@pytest.mark.asyncio
async def test_cancelling_a_dispatched_rescue_reverses_the_hold(
    client: AsyncClient, new_company, region
):
    """A rescue that falls through must return the money, not strand it."""
    r = await _bound_rescue(client, new_company, region)
    owner, escrow_id = r["owner"], r["escrow_id"]

    await client.post(
        f"/api/v1/incidents/{r['incident_id']}/advance",
        headers=owner.headers,
        json={"new_state": "DRIVER_EN_ROUTE"},
    )

    offers = (
        await client.get(
            f"/api/v1/incidents/{r['incident_id']}/offers", headers=owner.headers
        )
    ).json()
    bound_offer = next(o for o in offers if o["state"] == "BOUND")

    cancelled = await client.post(
        f"/api/v1/offers/{bound_offer['id']}/cancel",
        headers=owner.headers,
        json={"reason": "Rescue truck suffered its own breakdown"},
    )
    assert cancelled.status_code == 200

    ledger = (
        await client.get(f"/api/v1/escrow/{escrow_id}/ledger", headers=owner.headers)
    ).json()
    assert ledger["balance"]["balanced"]
    assert "CANCEL" in {e["postingType"] for e in ledger["entries"]}

    # The incident is looking for help again rather than stuck on a rescue
    # that is not coming.
    incident = (
        await client.get(f"/api/v1/incidents/{r['incident_id']}", headers=owner.headers)
    ).json()
    assert incident["state"] == "MATCHING"
