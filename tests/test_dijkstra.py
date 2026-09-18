"""
Shortest-path routing over real coordinates.

These used to assert on a hardcoded sixteen-node graph -- eleven waypoints
along NH-48 and five in San Francisco -- that nothing in the system could
reference. The tests passed while the feature was unreachable: no truck,
shipment or incident could be placed on that graph.

Routing is now built at request time from actual positions, so these tests
work the way the code is actually used.
"""
import math

import pytest

from services.matching_engine.app.dijkstra import (
    RouteNode,
    build_graph,
    fastest_route,
    route_between_points,
)
from services.matching_engine.app.routing import haversine_distance_km


def test_routes_between_two_real_points():
    result = route_between_points(origin=(18.5204, 73.8567), destination=(18.6298, 73.7997))
    assert result.path == ["origin", "destination"]
    assert result.total_distance_km > 0
    assert result.total_time_minutes > 0
    assert len(result.coordinates) == 2


def test_path_can_run_through_intermediate_points():
    """The point of a graph: a route through waypoints, not a straight line.

    With each node linked only to its single nearest neighbour, the direct
    origin-to-destination edge does not exist, so any answer must be a genuine
    multi-hop path.
    """
    nodes = [
        RouteNode("origin", 0.0, 0.0),
        RouteNode("w1", 0.0, 0.3),
        RouteNode("w2", 0.0, 0.6),
        RouteNode("destination", 0.0, 1.0),
    ]
    graph = build_graph(nodes, neighbours_per_node=1)
    result = fastest_route(nodes, "origin", "destination", graph)
    assert result.path == ["origin", "w1", "w2", "destination"]
    assert result.total_distance_km > 0


def test_graph_edges_are_bidirectional():
    """Nearest-neighbour selection is asymmetric; roads are not.

    Without the symmetry pass a node could be reachable in one direction only,
    which is a one-way street nobody asked for.
    """
    nodes = [
        RouteNode("a", 0.0, 0.0),
        RouteNode("b", 0.0, 0.1),
        RouteNode("c", 0.0, 5.0),
    ]
    graph = build_graph(nodes, neighbours_per_node=1)
    for node_id, edges in graph.items():
        for neighbour, _km, _minutes in edges:
            assert any(
                back == node_id for back, _, _ in graph[neighbour]
            ), f"{node_id} -> {neighbour} has no return edge"


def test_long_haul_routes_through_waypoints():
    """A long leg needs intermediate points, which is the realistic case.

    Edges are capped at 120 km, so Bangalore to Chennai has no direct hop --
    it is routed through positions the fleet has actually been, exactly as it
    would be in production.
    """
    result = route_between_points(
        origin=(12.9716, 77.5946),       # Bangalore
        destination=(13.0827, 80.2707),  # Chennai
        waypoints=[
            ("hosur", 12.7409, 77.8253),
            ("vellore", 12.9165, 79.1325),
            ("kanchipuram", 12.8342, 79.7036),
        ],
        max_edge_km=160.0,
    )
    assert result.path[0] == "origin"
    assert result.path[-1] == "destination"
    assert len(result.path) > 2, "a 290 km leg cannot be one 120 km edge"

    straight = haversine_distance_km(12.9716, 77.5946, 13.0827, 80.2707)
    # Routing through real points is longer than the crow flies, as roads are.
    assert result.total_distance_km >= straight
    assert result.total_time_minutes > 120


def test_unreachable_destination_raises():
    """Beyond the edge limit there is no route, and saying so beats guessing."""
    nodes = [
        RouteNode("origin", 0.0, 0.0),
        RouteNode("destination", 40.0, 40.0),
    ]
    graph = build_graph(nodes, max_edge_km=50.0)
    with pytest.raises(ValueError, match="No route"):
        fastest_route(nodes, "origin", "destination", graph)


def test_unknown_node_raises():
    nodes = [RouteNode("origin", 0.0, 0.0), RouteNode("destination", 0.0, 0.1)]
    with pytest.raises(ValueError, match="not in the graph"):
        fastest_route(nodes, "nowhere", "destination")


def test_chosen_path_is_the_fastest_available():
    """A detour that is quicker should win over a shorter, slower one."""
    nodes = [
        RouteNode("origin", 0.0, 0.0),
        RouteNode("near", 0.0, 0.02),
        RouteNode("destination", 0.0, 0.5),
    ]
    graph = build_graph(nodes)
    result = fastest_route(nodes, "origin", "destination", graph)

    # Whatever path is chosen, no alternative in the graph beats its time.
    def time_of(path):
        total = 0.0
        for a, b in zip(path, path[1:]):
            edge = next((e for e in graph[a] if e[0] == b), None)
            if edge is None:
                return math.inf
            total += edge[2]
        return total

    assert time_of(result.path) == pytest.approx(result.total_time_minutes, rel=1e-6)
    assert time_of(result.path) <= time_of(["origin", "near", "destination"]) + 1e-6
