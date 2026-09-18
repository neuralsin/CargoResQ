import pytest
from starlette.testclient import TestClient
from main import app
from services.realtime_gateway.app.connection_manager import manager


def test_websocket_ping_pong_and_broadcast():
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        # Test ping/pong keep-alive
        websocket.send_text("ping")
        data = websocket.receive_text()
        assert data == "pong"


@pytest.mark.asyncio
async def test_websocket_broadcast():
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        # Broadcast an event through the connection manager
        test_event = {
            "type": "incident.breakdown_reported",
            "payload": {"incidentId": "inc_ws_test", "status": "BREAKDOWN_REPORTED"},
        }
        await manager.broadcast(test_event)

        received = websocket.receive_json()
        assert received["type"] == "incident.breakdown_reported"
        assert received["payload"]["incidentId"] == "inc_ws_test"
