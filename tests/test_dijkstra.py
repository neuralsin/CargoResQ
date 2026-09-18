import pytest
from services.matching_engine.app.dijkstra import dijkstra_fastest_route, CORRIDOR_NODES


def test_dijkstra_urban_sf_route():
    res = dijkstra_fastest_route("SF_ORIGIN", "SF_DEST")
    assert res["startNode"] == "SF_ORIGIN"
    assert res["endNode"] == "SF_DEST"
    assert res["totalDistanceKm"] > 8.0
    assert res["totalTimeMinutes"] > 10.0
    assert len(res["path"]) == 5
    assert res["path"][0] == "SF_ORIGIN"
    assert res["path"][-1] == "SF_DEST"
    assert len(res["coordinates"]) == 5


def test_dijkstra_nh48_corridor_route():
    # Pune to Bangalore via Satara, Kolhapur, Belgaum, Hubli, Davanagere, Tumkur
    res = dijkstra_fastest_route("PUN_DEP", "BLR_HUB")
    assert res["startNode"] == "PUN_DEP"
    assert res["endNode"] == "BLR_HUB"
    assert res["totalDistanceKm"] > 800.0  # Approx 840 km
    assert "STR_TOL" in res["path"]
    assert "HUB_JNC" in res["path"]
    assert len(res["itinerary"]) >= 7


def test_dijkstra_invalid_node_raises_error():
    with pytest.raises(ValueError):
        dijkstra_fastest_route("INVALID_NODE", "SF_DEST")
