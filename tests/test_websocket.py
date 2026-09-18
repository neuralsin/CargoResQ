"""
WebSocket delivery scoping.

The old gateway held one flat set of sockets and a broadcast() that sent every
event to every client. These tests pin the replacement: events go to explicitly
computed rooms, unknown event types are dropped rather than broadcast, and
payloads bound for anyone other than the cargo owner carry no commercial data.
"""
import asyncio

import pytest
from starlette.testclient import TestClient

from main import app
from services.core_api.app.auth import create_access_token
from services.realtime_gateway.app.projections import (
    OWNER_ONLY_KEYS,
    PROJECTIONS,
    project,
)
from services.realtime_gateway.app.rooms import company_room, network_room


def _token(company_id: str, principal_type: str = "company") -> str:
    return create_access_token(
        subject=f"user@{company_id}.example",
        company_id=company_id,
        role="CARRIER_OWNER" if principal_type == "company" else "DRIVER",
        principal_type=principal_type,
        principal_id=company_id,
    )


def _authenticated(tc: TestClient, company_id: str):
    ws = tc.websocket_connect("/ws").__enter__()
    ws.send_json({"type": "auth", "token": _token(company_id)})
    ack = ws.receive_json()
    assert ack["type"] == "auth.ok"
    return ws


def test_ping_pong_after_authentication():
    with TestClient(app) as tc:
        ws = _authenticated(tc, "comp_ping")
        try:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
        finally:
            ws.__exit__(None, None, None)


def test_event_reaches_only_its_own_company_room():
    """A breakdown must not appear on an unrelated carrier's socket."""
    from services.realtime_gateway.app.connection_manager import manager

    with TestClient(app) as tc:
        owner = _authenticated(tc, "comp_owner")
        stranger = _authenticated(tc, "comp_stranger")
        try:
            payload = {"type": "incident.matching", "payload": {"incidentId": "inc_1"}}
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                manager.send_to_room(company_room("comp_owner"), payload)
            )
            assert owner.receive_json() == payload

            # The stranger's socket has nothing waiting. Ask for a pong and
            # confirm that is the next thing it sees, not the event above.
            stranger.send_json({"type": "ping"})
            assert stranger.receive_json() == {"type": "pong"}
        finally:
            owner.__exit__(None, None, None)
            stranger.__exit__(None, None, None)


def test_subscribing_to_another_companys_room_is_refused():
    with TestClient(app) as tc:
        ws = _authenticated(tc, "comp_a")
        try:
            ws.send_json({"type": "subscribe", "room": company_room("comp_b")})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["payload"]["code"] == "forbidden_room"
        finally:
            ws.__exit__(None, None, None)


def test_unknown_room_is_refused():
    with TestClient(app) as tc:
        ws = _authenticated(tc, "comp_a")
        try:
            ws.send_json({"type": "subscribe", "room": "totally:made:up"})
            assert ws.receive_json()["payload"]["code"] == "forbidden_room"
        finally:
            ws.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_unregistered_event_is_dropped_not_broadcast():
    """The default for an unknown event must be silence.

    This is the inversion that makes the gateway safe: a new event added
    anywhere in the codebase is invisible until someone writes its audience
    down, rather than being broadcast to everyone by default.
    """
    deliveries = await project("some.brand.new.event", {"payload": {"secret": 1}}, None)
    assert deliveries == []


@pytest.mark.asyncio
async def test_breakdown_gives_the_network_no_commercial_detail():
    """Nearby carriers learn help is wanted; they do not learn what it is worth."""
    envelope = {
        "eventType": "breakdown.detected",
        "payload": {
            "incidentId": "inc_42",
            "shipmentId": "shp_42",
            "companyId": "comp_owner",
            "cargoType": "Refrigerated insulin",
            "valueInr": 480000.0,
            "lat": 18.52041,
            "lng": 73.85673,
            "requiresRefrigeration": True,
            "volumeM3": 3.5,
            "weightKg": 850.0,
        },
    }
    deliveries = await project("breakdown.detected", envelope, None)
    rooms = {d.room for d in deliveries}
    assert company_room("comp_owner") in rooms
    assert network_room() in rooms

    network_payload = next(d.payload for d in deliveries if d.room == network_room())
    body = network_payload["payload"]
    assert "valueInr" not in body
    assert "cargoType" not in body
    assert "companyId" not in body
    assert "shipmentId" not in body
    # Position is coarsened rather than exact.
    assert body["approxLat"] == 18.52
    assert body["approxLng"] == 73.86

    owner_payload = next(d.payload for d in deliveries if d.room == company_room("comp_owner"))
    assert owner_payload["payload"]["valueInr"] == 480000.0


@pytest.mark.asyncio
async def test_escrow_events_hide_the_price_split_from_the_carrier():
    envelope = {
        "eventType": "escrow.state_changed",
        "payload": {
            "escrowId": "esc_1",
            "incidentId": "inc_1",
            "ownerCompanyId": "comp_owner",
            "carrierCompanyId": "comp_rescuer",
            "state": "ACCEPTED",
        },
    }
    deliveries = await project("escrow.state_changed", envelope, None)
    carrier = next(
        d.payload for d in deliveries if d.room == company_room("comp_rescuer")
    )
    assert "amountInr" not in carrier["payload"]
    assert "platformFeeInr" not in carrier["payload"]
    assert carrier["payload"]["role"] == "carrier"


@pytest.mark.asyncio
async def test_no_projector_leaks_owner_only_keys_outside_the_owner_room():
    """A sweep over the whole registry, so new projectors are covered too.

    Feeds each registered projector a payload containing every sensitive key
    and asserts none of them survive into a room that is not the owner's.
    """
    sensitive = {key: "LEAKED" for key in OWNER_ONLY_KEYS}
    envelope = {
        "eventType": "probe",
        "payload": {
            "incidentId": "inc_probe",
            "escrowId": "esc_probe",
            "companyId": "comp_owner",
            "ownerCompanyId": "comp_owner",
            "carrierCompanyId": "comp_rescuer",
            "lat": 1.23456,
            "lng": 7.65432,
            **sensitive,
        },
    }
    owner_rooms = {company_room("comp_owner")}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    for topic in PROJECTIONS:
        for room, payload in await project(topic, envelope, None):
            if room in owner_rooms:
                continue
            leaked = OWNER_ONLY_KEYS.intersection(walk(payload))
            assert not leaked, f"{topic} leaked {sorted(leaked)} into room {room}"
