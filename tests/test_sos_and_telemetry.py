"""
SOS broadcast and telemetry.

Neither of these existed. SOS was a label on the breakdown endpoint, and
telemetry was absent entirely -- `trucks.latitude/longitude` were written once
at registration and never updated by any code path, so the fleet was frozen
where it was created while the UI called the view live.
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
            "max_volume_m3": 20.0,
            "max_weight_kg": 6000.0,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _driver_with_truck(client, owner, name, truck_id):
    email = f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}@example.com"
    password = "DriverPassword123!"
    res = await client.post(
        "/api/v1/driver/register",
        headers=owner.headers,
        json={
            "name": name,
            "email": email,
            "password": password,
            "assigned_truck_id": truck_id,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return {
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
        "id": body["driver_id"],
        "password": password,
    }


def _ping(lat, lng, *, seconds_ago=0, **extra):
    at = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    payload = {
        "client_ping_id": uuid.uuid4().hex[:16],
        "recorded_at": at.isoformat(),
        "latitude": lat,
        "longitude": lng,
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pings_move_the_truck(client: AsyncClient, new_company, region):
    """The fleet must actually move. Previously nothing ever updated position."""
    owner = await new_company("Moving Fleet")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Ping Driver", truck_id)

    res = await client.post(
        "/api/v1/telemetry/pings",
        headers=driver["headers"],
        json={
            "device_id": "dev-" + uuid.uuid4().hex[:8],
            "pings": [
                _ping(region["lat"] + 0.01, region["lng"] + 0.01, speed_kph=48.0,
                      heading_deg=90.0, battery_pct=76.0),
            ],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["accepted"] == 1

    live = await client.get(f"/api/v1/telemetry/trucks/{truck_id}/live", headers=owner.headers)
    body = live.json()
    assert body["hasFix"] is True
    assert body["latitude"] == pytest.approx(region["lat"] + 0.01)
    assert body["speedKph"] == 48.0
    assert body["batteryPct"] == 76.0


@pytest.mark.asyncio
async def test_a_retried_batch_does_not_duplicate(client: AsyncClient, new_company, region):
    """Devices retry when they miss an acknowledgement. That must be safe."""
    owner = await new_company("Retry Fleet")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Retry Driver", truck_id)

    device = "dev-" + uuid.uuid4().hex[:8]
    batch = {"device_id": device, "pings": [_ping(region["lat"], region["lng"])]}

    first = await client.post("/api/v1/telemetry/pings", headers=driver["headers"], json=batch)
    second = await client.post("/api/v1/telemetry/pings", headers=driver["headers"], json=batch)

    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 1


@pytest.mark.asyncio
async def test_no_fix_is_reported_as_no_fix(client: AsyncClient, new_company, region):
    """A registration coordinate is not a position, and must not look like one."""
    owner = await new_company("Silent Fleet")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])

    live = await client.get(f"/api/v1/telemetry/trucks/{truck_id}/live", headers=owner.headers)
    assert live.json()["hasFix"] is False


@pytest.mark.asyncio
async def test_a_mocked_location_is_flagged_but_still_shown(
    client: AsyncClient, new_company, region
):
    """A spoofed position raises an alarm and is still recorded.

    Discarding it looked safer but was worse in practice: the truck froze on
    the dispatcher's map at its last believed position with no indication the
    feed had stalled. Android reports every fix as mocked on an emulator or
    with developer options on, so in testing the fleet simply stopped moving.

    The dispatcher is better served by a position they are told to doubt than
    by a stale one presented as current.
    """
    owner = await new_company("Spoof Fleet")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Spoof Driver", truck_id)
    device = "dev-" + uuid.uuid4().hex[:8]

    await client.post(
        "/api/v1/telemetry/pings",
        headers=driver["headers"],
        json={"device_id": device, "pings": [_ping(region["lat"], region["lng"], speed_kph=40.0)]},
    )
    await client.post(
        "/api/v1/telemetry/pings",
        headers=driver["headers"],
        json={
            "device_id": device,
            "pings": [_ping(region["lat"] + 5.0, region["lng"] + 5.0, mock_location=True)],
        },
    )

    alerts = await client.get("/api/v1/telemetry/alerts", headers=owner.headers)
    assert "MOCK_LOCATION" in {a["type"] for a in alerts.json()}

    live = await client.get(f"/api/v1/telemetry/trucks/{truck_id}/live", headers=owner.headers)
    body = live.json()
    # The truck moved on the map...
    assert body["latitude"] == pytest.approx(region["lat"] + 5.0)
    # ...and carries the warning that says why it should not be trusted.
    assert body["positionSuspect"] is True
    assert body["suspectReason"] == "MOCK_LOCATION"


@pytest.mark.asyncio
async def test_a_clean_fix_clears_the_suspect_flag(client: AsyncClient, new_company, region):
    """Once honest fixes resume, the warning goes away."""
    owner = await new_company("Recovering Fleet")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Recovering Driver", truck_id)
    device = "dev-" + uuid.uuid4().hex[:8]

    await client.post(
        "/api/v1/telemetry/pings",
        headers=driver["headers"],
        json={
            "device_id": device,
            "pings": [_ping(region["lat"], region["lng"], mock_location=True)],
        },
    )
    await client.post(
        "/api/v1/telemetry/pings",
        headers=driver["headers"],
        json={"device_id": device, "pings": [_ping(region["lat"] + 0.01, region["lng"])]},
    )

    live = await client.get(f"/api/v1/telemetry/trucks/{truck_id}/live", headers=owner.headers)
    assert live.json()["positionSuspect"] is False


@pytest.mark.asyncio
async def test_one_dwell_is_one_alert(client: AsyncClient, new_company, region):
    """Six hours of standing still is one alert, not hundreds."""
    from services.telemetry.app.alerts import record_finding
    from services.telemetry.app.detectors import detect_dwell
    from shared.database import async_session

    owner = await new_company("Dwell Fleet")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])

    stopped_at = datetime.now(timezone.utc) - timedelta(hours=6)
    async with async_session() as session:
        for minute in range(0, 360, 5):  # a detection every five minutes
            now = stopped_at + timedelta(minutes=minute + 60)
            finding = detect_dwell(truck_id, stopped_at, now)
            if finding is None:
                continue
            await record_finding(
                session, finding, company_id=owner.company_id, truck_id=truck_id
            )

    alerts = await client.get(
        f"/api/v1/telemetry/alerts?type=DWELL&truck_id={truck_id}", headers=owner.headers
    )
    body = alerts.json()
    assert len(body) == 1, f"expected one dwell episode, got {len(body)}"
    assert body[0]["occurrenceCount"] > 10, "the episode should record every sighting"


@pytest.mark.asyncio
async def test_temperature_excursion_uses_the_shipments_limit(
    client: AsyncClient, new_company, region
):
    """The limit comes from the shipment, never from the reading's payload."""
    owner = await new_company("Cold Chain Owner")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Cold Driver", truck_id)

    shipment = await client.post(
        "/api/v1/shipments",
        headers=owner.headers,
        json={
            "truck_id": truck_id,
            "cargo_type": "Vaccines",
            "requires_refrigeration": True,
            "required_max_temp_c": 8.0,
            "volume_m3": 2.0,
            "weight_kg": 400.0,
            "value_inr": 250000.0,
        },
    )
    shipment_id = shipment.json()["id"]

    res = await client.post(
        "/api/v1/telemetry/readings",
        headers=driver["headers"],
        json={
            "sensor_id": "sensor-" + uuid.uuid4().hex[:8],
            "readings": [
                {
                    "client_reading_id": uuid.uuid4().hex[:16],
                    "shipment_id": shipment_id,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "temperature_c": 12.5,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["alertsRaised"] == 1

    alerts = await client.get(
        "/api/v1/telemetry/alerts?type=TEMP_EXCURSION", headers=owner.headers
    )
    found = [a for a in alerts.json() if a["shipmentId"] == shipment_id]
    assert found
    assert found[0]["lastValue"]["limitC"] == 8.0
    assert found[0]["lastValue"]["temperatureC"] == 12.5


@pytest.mark.asyncio
async def test_recorded_conditions_drive_escrow_settlement(
    client: AsyncClient, new_company, region
):
    """Settlement reads what the platform recorded, not what a party asserts."""
    from services.escrow_ledger.app.escrow_service import create_escrow, transition
    from shared.database import async_session

    owner = await new_company("Settlement Owner")
    rescuer = await new_company("Settlement Rescuer")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Settle Driver", truck_id)

    shipment = await client.post(
        "/api/v1/shipments",
        headers=owner.headers,
        json={
            "truck_id": truck_id,
            "cargo_type": "Insulin",
            "requires_refrigeration": True,
            "required_max_temp_c": 8.0,
            "volume_m3": 2.0,
            "weight_kg": 400.0,
            "value_inr": 250000.0,
        },
    )
    shipment_id = shipment.json()["id"]
    incident = await client.post(
        "/api/v1/incidents",
        headers=owner.headers,
        json={"shipment_id": shipment_id, "lat": region["lat"], "lng": region["lng"]},
    )
    incident_id = incident.json()["id"]

    # In-spec readings, recorded by the platform.
    sensor = "sensor-" + uuid.uuid4().hex[:8]
    await client.post(
        "/api/v1/telemetry/readings",
        headers=driver["headers"],
        json={
            "sensor_id": sensor,
            "readings": [
                {
                    "client_reading_id": uuid.uuid4().hex[:16],
                    "shipment_id": shipment_id,
                    "recorded_at": (
                        datetime.now(timezone.utc) - timedelta(minutes=m)
                    ).isoformat(),
                    "temperature_c": temp,
                }
                for m, temp in [(9, 4.1), (6, 4.4), (3, 5.0)]
            ],
        },
    )

    async with async_session() as session:
        escrow = await create_escrow(
            session,
            amount_inr=8000.0,
            carrier_payout_inr=7040.0,
            owner_company_id=owner.company_id,
            carrier_company_id=rescuer.company_id,
            incident_id=incident_id,
        )
        escrow_id = escrow.id
        await transition(session, escrow_id, "ACCEPTED")
        await transition(session, escrow_id, "IN_TRANSIT")
        await transition(session, escrow_id, "PENDING_VERIFICATION")

    settled = await client.post(f"/api/v1/escrow/{escrow_id}/verify", headers=owner.headers)
    body = settled.json()
    assert body["verification"]["verdict"] == "PASS"
    assert body["verification"]["readingsCount"] == 3
    assert body["state"] == "RELEASED"


# ---------------------------------------------------------------------------
# SOS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sos_reaches_own_company_in_full(client: AsyncClient, new_company, region):
    owner = await new_company("SOS Owner")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "SOS Driver", truck_id)

    res = await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "MEDICAL",
            "severity": "CRITICAL",
            "latitude": region["lat"],
            "longitude": region["lng"],
            "landmark_note": "km 42 marker, southbound",
            "condition": {
                "persons_affected": 1,
                "is_conscious": True,
                "is_breathing": True,
                "severe_bleeding": False,
                "note": "Chest pain, sweating",
            },
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "BROADCASTING"
    assert body["condition"]["note"] == "Chest pain, sweating"
    # The scope boundary is restated on every payload.
    assert body["psapDispatched"] is False
    assert body["psapIntegration"] == "none"
    assert any(n["number"] == "112" for n in body["emergencyNumbers"])


@pytest.mark.asyncio
async def test_repeated_taps_do_not_create_repeated_alerts(
    client: AsyncClient, new_company, region
):
    """A panicking driver taps five times. That is one emergency."""
    owner = await new_company("Panic Owner")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Panic Driver", truck_id)

    request_id = uuid.uuid4().hex
    payload = {
        "category": "ACCIDENT",
        "severity": "CRITICAL",
        "latitude": region["lat"],
        "longitude": region["lng"],
        "client_request_id": request_id,
    }
    first = await client.post("/api/v1/sos", headers=driver["headers"], json=payload)
    second = await client.post("/api/v1/sos", headers=driver["headers"], json=payload)

    assert first.json()["id"] == second.json()["id"]
    assert second.json()["deduplicated"] is True

    active = await client.get("/api/v1/sos/active", headers=owner.headers)
    assert len([a for a in active.json() if a["id"] == first.json()["id"]]) == 1


@pytest.mark.asyncio
async def test_nearby_carrier_gets_a_redacted_alert(client: AsyncClient, new_company, region):
    """The cross-carrier broadcast must not leak who or what is involved."""
    owner = await new_company("Broadcast Owner")
    neighbour = await new_company("Nearby Carrier")

    owner_truck = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Broadcast Driver", owner_truck)

    # The neighbour must have a *live* position to be considered nearby.
    neighbour_truck = await _truck(
        client, neighbour, lat=region["lat"] + 0.05, lng=region["lng"] + 0.05
    )
    neighbour_driver = await _driver_with_truck(
        client, neighbour, "Neighbour Driver", neighbour_truck
    )
    await client.post(
        "/api/v1/telemetry/pings",
        headers=neighbour_driver["headers"],
        json={
            "device_id": "dev-" + uuid.uuid4().hex[:8],
            "pings": [_ping(region["lat"] + 0.05, region["lng"] + 0.05)],
        },
    )

    sos = await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "ACCIDENT",
            "severity": "CRITICAL",
            "latitude": region["lat"],
            "longitude": region["lng"],
            "condition": {"persons_affected": 2, "note": "Driver trapped, bleeding"},
        },
    )
    sos_id = sos.json()["id"]

    nearby = await client.get("/api/v1/sos/nearby", headers=neighbour.headers)
    found = [a for a in nearby.json() if a["sosId"] == sos_id]
    assert found, "a nearby carrier should have been told"

    alert = found[0]
    assert "condition" not in alert
    assert "driverId" not in alert
    assert "companyId" not in alert
    assert "latitude" not in alert, "exact position is withheld before acknowledgement"
    assert alert["approxLat"] == pytest.approx(round(region["lat"], 2))
    assert "MEDICAL_TRANSPORT" in alert["assistanceNeeded"]
    assert alert["psapDispatched"] is False


@pytest.mark.asyncio
async def test_acknowledging_reveals_the_exact_location(
    client: AsyncClient, new_company, region
):
    """Once committed, a responder needs to actually find the scene."""
    owner = await new_company("Ack Owner")
    neighbour = await new_company("Ack Neighbour")
    owner_truck = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Ack Driver", owner_truck)

    n_truck = await _truck(client, neighbour, lat=region["lat"] + 0.05, lng=region["lng"] + 0.05)
    n_driver = await _driver_with_truck(client, neighbour, "Ack Neighbour Driver", n_truck)
    await client.post(
        "/api/v1/telemetry/pings",
        headers=n_driver["headers"],
        json={
            "device_id": "dev-" + uuid.uuid4().hex[:8],
            "pings": [_ping(region["lat"] + 0.05, region["lng"] + 0.05)],
        },
    )

    sos = await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "MECHANICAL",
            "severity": "HIGH",
            "latitude": region["lat"],
            "longitude": region["lng"],
        },
    )
    sos_id = sos.json()["id"]

    before = await client.get(f"/api/v1/sos/{sos_id}", headers=neighbour.headers)
    assert "latitude" not in before.json()

    ack = await client.post(f"/api/v1/sos/{sos_id}/acknowledge", headers=neighbour.headers)
    assert ack.status_code == 200

    after = await client.get(f"/api/v1/sos/{sos_id}", headers=neighbour.headers)
    body = after.json()
    assert body["latitude"] == pytest.approx(region["lat"])
    assert body["locationPrecision"] == "exact"
    # Medical detail still requires an explicit share.
    assert body.get("medicalShared") is False


@pytest.mark.asyncio
async def test_unrelated_company_cannot_see_an_sos(client: AsyncClient, new_company, region):
    owner = await new_company("Quiet Owner")
    stranger = await new_company("Faraway Carrier")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Quiet Driver", truck_id)

    sos = await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "MEDICAL",
            "severity": "CRITICAL",
            "latitude": region["lat"],
            "longitude": region["lng"],
        },
    )
    sos_id = sos.json()["id"]

    res = await client.get(f"/api/v1/sos/{sos_id}", headers=stranger.headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_security_alerts_are_not_broadcast_by_default(
    client: AsyncClient, new_company, region
):
    """Telling an unvetted network where a hijacking is happening is a risk."""
    owner = await new_company("Hijack Owner")
    neighbour = await new_company("Hijack Neighbour")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Hijack Driver", truck_id)

    n_truck = await _truck(client, neighbour, lat=region["lat"] + 0.02, lng=region["lng"] + 0.02)
    n_driver = await _driver_with_truck(client, neighbour, "Hijack Neighbour", n_truck)
    await client.post(
        "/api/v1/telemetry/pings",
        headers=n_driver["headers"],
        json={
            "device_id": "dev-" + uuid.uuid4().hex[:8],
            "pings": [_ping(region["lat"] + 0.02, region["lng"] + 0.02)],
        },
    )

    sos = await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "POLICE_SECURITY",
            "severity": "CRITICAL",
            "latitude": region["lat"],
            "longitude": region["lng"],
            "silent_mode": True,
        },
    )
    sos_id = sos.json()["id"]

    nearby = await client.get("/api/v1/sos/nearby", headers=neighbour.headers)
    assert not [a for a in nearby.json() if a["sosId"] == sos_id]

    # The reporting company still sees it, in full.
    active = await client.get("/api/v1/sos/active", headers=owner.headers)
    mine = [a for a in active.json() if a["id"] == sos_id]
    assert mine and mine[0]["silentMode"] is True


@pytest.mark.asyncio
async def test_cancelling_under_duress_does_not_cancel(
    client: AsyncClient, new_company, region
):
    """A wrong PIN must look like success and quietly escalate.

    Under a hijacking the instruction a driver is given is "cancel it". The
    device has to be able to appear to comply.
    """
    owner = await new_company("Duress Owner")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Duress Driver", truck_id)

    sos = await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "POLICE_SECURITY",
            "severity": "CRITICAL",
            "latitude": region["lat"],
            "longitude": region["lng"],
        },
    )
    sos_id = sos.json()["id"]

    res = await client.post(
        f"/api/v1/sos/{sos_id}/cancel",
        headers=driver["headers"],
        json={"pin": "definitely-not-the-pin"},
    )
    # The device is told it worked.
    assert res.status_code == 200
    assert res.json()["status"] == "CANCELLED"

    # It did not.
    active = await client.get("/api/v1/sos/active", headers=owner.headers)
    still_live = [a for a in active.json() if a["id"] == sos_id]
    assert still_live, "a duress cancellation must not actually cancel"
    assert still_live[0]["status"] == "ESCALATED"
    assert still_live[0]["silentMode"] is True


@pytest.mark.asyncio
async def test_correct_pin_really_cancels(client: AsyncClient, new_company, region):
    owner = await new_company("Real Cancel Owner")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Real Cancel Driver", truck_id)

    sos = await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "MECHANICAL",
            "severity": "MODERATE",
            "latitude": region["lat"],
            "longitude": region["lng"],
        },
    )
    sos_id = sos.json()["id"]

    res = await client.post(
        f"/api/v1/sos/{sos_id}/cancel",
        headers=driver["headers"],
        json={"pin": driver["password"], "reason": "False alarm"},
    )
    assert res.status_code == 200

    active = await client.get("/api/v1/sos/active", headers=owner.headers)
    assert not [a for a in active.json() if a["id"] == sos_id]


@pytest.mark.asyncio
async def test_emergency_numbers_are_served_with_a_clear_disclaimer(client: AsyncClient):
    res = await client.get("/api/v1/sos/emergency-numbers")
    body = res.json()
    assert any(n["number"] == "112" and n["primary"] for n in body["numbers"])
    assert "not an emergency service" in body["notice"]


@pytest.mark.asyncio
async def test_sos_puts_the_driver_on_the_map_immediately(
    client: AsyncClient, new_company, region
):
    """An emergency must not wait for the next telemetry batch.

    A driver who has never gone on duty still has a GPS fix in their hand when
    they press SOS, and the alert carries it. Making the dispatcher wait for
    the telemetry service to spin up and produce a ping is exactly the wrong
    behaviour in the one situation where seconds matter.
    """
    owner = await new_company("Instant Map Owner")
    truck_id = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Instant Driver", truck_id)

    # No pings have ever been sent, so there is no fix yet.
    before = await client.get(
        f"/api/v1/telemetry/trucks/{truck_id}/live", headers=owner.headers
    )
    assert before.json()["hasFix"] is False

    sos_lat = region["lat"] + 0.02
    sos_lng = region["lng"] + 0.02
    await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "MEDICAL",
            "severity": "CRITICAL",
            "latitude": sos_lat,
            "longitude": sos_lng,
        },
    )

    after = await client.get(
        f"/api/v1/telemetry/trucks/{truck_id}/live", headers=owner.headers
    )
    body = after.json()
    assert body["hasFix"] is True
    assert body["latitude"] == pytest.approx(sos_lat)
    assert body["longitude"] == pytest.approx(sos_lng)

    # And the console's fleet view shows it, which is what draws the marker.
    fleet = await client.get("/api/v1/telemetry/fleet/live", headers=owner.headers)
    entry = next(t for t in fleet.json() if t["truckId"] == truck_id)
    assert entry["positionIsLive"] is True
    assert entry["latitude"] == pytest.approx(sos_lat)


@pytest.mark.asyncio
async def test_sos_reports_who_can_help_and_how_soon(
    client: AsyncClient, new_company, region
):
    """The dispatcher needs names and ETAs, not "an alert was broadcast".

    While waiting, the useful question is which carriers are close enough to
    help and how long each would take -- and then, as replies arrive, which of
    them has actually committed.
    """
    owner = await new_company("Waiting Owner")
    near = await new_company("Close Carrier")
    far_ish = await new_company("Further Carrier")

    owner_truck = await _truck(client, owner, lat=region["lat"], lng=region["lng"])
    driver = await _driver_with_truck(client, owner, "Waiting Driver", owner_truck)

    # Two carriers at different distances, both reporting live positions.
    for account, offset, name in ((near, 0.03, "Near Driver"), (far_ish, 0.12, "Far Driver")):
        truck_id = await _truck(
            client, account, lat=region["lat"] + offset, lng=region["lng"] + offset
        )
        helper = await _driver_with_truck(client, account, name, truck_id)
        await client.post(
            "/api/v1/telemetry/pings",
            headers=helper["headers"],
            json={
                "device_id": "dev-" + uuid.uuid4().hex[:8],
                "pings": [_ping(region["lat"] + offset, region["lng"] + offset)],
            },
        )

    sos = await client.post(
        "/api/v1/sos",
        headers=driver["headers"],
        json={
            "category": "ACCIDENT",
            "severity": "CRITICAL",
            "latitude": region["lat"],
            "longitude": region["lng"],
        },
    )
    assert sos.status_code == 201, sos.text
    sos_id = sos.json()["id"]

    board = sos.json()["responders"]
    assert len(board) == 2, "both nearby carriers should be listed"

    # Sorted by how soon they could arrive, with an ETA for each.
    assert board[0]["companyName"] == "Close Carrier"
    assert board[0]["distanceKm"] < board[1]["distanceKm"]
    assert board[0]["etaMinutes"] > 0
    assert board[0]["etaIsEstimate"] is True
    assert board[0]["status"] == "NOTIFIED"

    # The nearer carrier commits, with their own ETA.
    await client.post(f"/api/v1/sos/{sos_id}/acknowledge", headers=near.headers)
    await client.post(
        f"/api/v1/sos/{sos_id}/respond",
        headers=near.headers,
        json={"action": "EN_ROUTE", "eta_minutes": 9.0},
    )

    active = await client.get("/api/v1/sos/active", headers=owner.headers)
    alert = next(a for a in active.json() if a["id"] == sos_id)
    assert alert["respondersEnRoute"] == 1

    committed = next(r for r in alert["responders"] if r["companyId"] == near.company_id)
    assert committed["status"] == "EN_ROUTE"
    # Their stated ETA replaces our estimate, and is marked as theirs.
    assert committed["etaMinutes"] == 9.0
    assert committed["etaIsEstimate"] is False
    # A committed responder sorts above one that has only been notified.
    assert alert["responders"][0]["companyId"] == near.company_id


def test_sos_and_position_reach_the_console_live():
    """The dashboard must react the moment the transponder fires.

    Everything the console shows during an emergency arrives over this
    socket: the alert itself, and then the driver's position as it updates.
    Polling alone would leave a dispatcher staring at a stale screen while
    somebody waits on a hard shoulder.
    """
    import json
    import uuid as _uuid
    from datetime import datetime, timezone

    from starlette.testclient import TestClient

    from main import app

    with TestClient(app) as tc:
        def register(name):
            res = tc.post("/api/v1/auth/register", json={
                "name": name,
                "email": f"{_uuid.uuid4().hex[:8]}@live.example",
                "password": "TestPassword123!",
            })
            body = res.json()
            return body["company_id"], {"Authorization": f"Bearer {body['access_token']}"}

        company_id, owner = register("Live Console Co")

        truck = tc.post("/api/v1/trucks", headers=owner, json={
            "registration_number": f"MH-{_uuid.uuid4().hex[:8].upper()}",
            "latitude": 18.52, "longitude": 73.85,
            "refrigerated": True, "min_temp_c": -20.0,
            "max_volume_m3": 20.0, "max_weight_kg": 6000.0,
        }).json()
        truck_id = truck["id"]

        driver_res = tc.post("/api/v1/driver/register", headers=owner, json={
            "name": "Live Driver",
            "email": f"{_uuid.uuid4().hex[:8]}@live.example",
            "password": "DriverPassword123!",
            "assigned_truck_id": truck_id,
        }).json()
        driver = {"Authorization": f"Bearer {driver_res['access_token']}"}

        # The console connects exactly as the desktop app does.
        with tc.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": owner["Authorization"].split()[1]})
            assert ws.receive_json()["type"] == "auth.ok"

            # The driver presses SOS.
            tc.post("/api/v1/sos", headers=driver, json={
                "category": "MEDICAL", "severity": "CRITICAL",
                "latitude": 18.52, "longitude": 73.85,
            })

            seen = {}
            for _ in range(8):
                message = ws.receive_json()
                seen.setdefault(message["type"], message["payload"])
                if "sos.raised" in seen:
                    break

            assert "sos.raised" in seen, f"console never saw the SOS; got {list(seen)}"
            alert = seen["sos.raised"]
            assert alert["category"] == "MEDICAL"
            # Flagged as ours, so the console knows to take over the screen
            # rather than showing it as a neighbour's problem.
            assert alert["isOwnDriver"] is True

            # And the driver's position arrives, so the pin can move.
            tc.post("/api/v1/telemetry/pings", headers=driver, json={
                "device_id": "dev-live",
                "pings": [{
                    "client_ping_id": _uuid.uuid4().hex[:16],
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "latitude": 18.5300, "longitude": 73.8600,
                    "speed_kph": 41.0, "heading_deg": 88.0,
                }],
            })

            for _ in range(8):
                message = ws.receive_json()
                if message["type"] == "telemetry.location":
                    position = message["payload"]
                    assert position["truckId"] == truck_id
                    assert position["latitude"] == pytest.approx(18.5300)
                    assert position["longitude"] == pytest.approx(73.8600)
                    # Carried so the map can mark an unverified fix.
                    assert position["positionSuspect"] is False
                    break
            else:
                raise AssertionError("console never received a position update")
