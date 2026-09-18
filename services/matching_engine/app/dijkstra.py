"""
Shortest-path routing over a graph built from real entities.

This used to be a textbook Dijkstra over a hardcoded sixteen-node graph --
eleven waypoints along NH-48 plus five in San Francisco -- with static numbers
standing in for live congestion. Nothing in the system referenced those node
ids: no truck, no shipment and no incident could be placed on that graph, and
the only way to reach it was an endpoint that existed to display a shortest
distance. It computed a real answer to an invented question.

Routing belongs inside matching, not on a screen. What matters operationally
is how long a rescuer takes to arrive, so this module exists as the fallback
for when the road-routing service is unreachable: it builds a graph at request
time from the actual incident, the actual candidate trucks, and the waypoints
those trucks have actually driven through, then finds the fastest path across
it.

With no road network available, a graph over known positions still beats a
straight line, because it can route around the fact that two points either
side of a river are not two minutes apart.
"""
import heapq
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .routing import haversine_distance_km

#: Typical achievable speeds, km/h, by how far apart two points are. Short
#: hops are urban and slow; long ones are mostly highway.
def _assumed_speed_kph(distance_km: float) -> float:
    if distance_km <= 5.0:
        return 22.0
    if distance_km <= 25.0:
        return 35.0
    if distance_km <= 80.0:
        return 50.0
    return 62.0


@dataclass(frozen=True)
class RouteNode:
    """A real place: an incident, a truck, or a waypoint from ping history."""

    id: str
    lat: float
    lng: float
    kind: str = "waypoint"
    label: Optional[str] = None


@dataclass
class RouteResult:
    origin_id: str
    destination_id: str
    total_distance_km: float
    total_time_minutes: float
    path: List[str] = field(default_factory=list)
    coordinates: List[Tuple[float, float]] = field(default_factory=list)
    method: str = "dijkstra_over_known_positions"

    def to_dict(self) -> dict:
        return {
            "originId": self.origin_id,
            "destinationId": self.destination_id,
            "totalDistanceKm": round(self.total_distance_km, 2),
            "totalTimeMinutes": round(self.total_time_minutes, 1),
            "path": self.path,
            "coordinates": [[lat, lng] for lat, lng in self.coordinates],
            "method": self.method,
        }


def build_graph(
    nodes: Sequence[RouteNode],
    max_edge_km: float = 120.0,
    neighbours_per_node: int = 6,
) -> Dict[str, List[Tuple[str, float, float]]]:
    """Connect each node to its nearest neighbours.

    A complete graph would make Dijkstra pointless -- the direct edge always
    wins, so the answer collapses back to straight-line distance. Limiting
    each node to its nearest few neighbours is what lets a path through an
    intermediate point beat the direct hop.

    Returns an adjacency list of (neighbour_id, distance_km, minutes).
    """
    graph: Dict[str, List[Tuple[str, float, float]]] = {node.id: [] for node in nodes}

    for node in nodes:
        distances = []
        for other in nodes:
            if other.id == node.id:
                continue
            km = haversine_distance_km(node.lat, node.lng, other.lat, other.lng)
            if km <= max_edge_km:
                distances.append((km, other.id))
        distances.sort()
        for km, other_id in distances[:neighbours_per_node]:
            minutes = (km / _assumed_speed_kph(km)) * 60.0
            graph[node.id].append((other_id, km, minutes))

    # Undirected: if A reaches B, B reaches A. Nearest-neighbour selection is
    # not symmetric on its own, which would otherwise create one-way roads.
    for node_id, edges in list(graph.items()):
        for neighbour_id, km, minutes in edges:
            if not any(dest == node_id for dest, _, _ in graph[neighbour_id]):
                graph[neighbour_id].append((node_id, km, minutes))

    return graph


def fastest_route(
    nodes: Sequence[RouteNode],
    origin_id: str,
    destination_id: str,
    graph: Optional[Dict[str, List[Tuple[str, float, float]]]] = None,
) -> RouteResult:
    """Dijkstra, minimising travel time across the built graph."""
    index = {node.id: node for node in nodes}
    if origin_id not in index:
        raise ValueError(f"Origin {origin_id!r} is not in the graph")
    if destination_id not in index:
        raise ValueError(f"Destination {destination_id!r} is not in the graph")

    graph = graph if graph is not None else build_graph(nodes)

    # (cumulative_minutes, node_id, cumulative_km, path)
    queue: List[Tuple[float, str, float, List[str]]] = [(0.0, origin_id, 0.0, [origin_id])]
    best_time: Dict[str, float] = {origin_id: 0.0}

    while queue:
        elapsed, node_id, travelled, path = heapq.heappop(queue)

        if node_id == destination_id:
            return RouteResult(
                origin_id=origin_id,
                destination_id=destination_id,
                total_distance_km=travelled,
                total_time_minutes=elapsed,
                path=path,
                coordinates=[(index[n].lat, index[n].lng) for n in path],
            )

        if elapsed > best_time.get(node_id, math.inf):
            continue

        for neighbour_id, km, minutes in graph.get(node_id, []):
            next_time = elapsed + minutes
            if next_time < best_time.get(neighbour_id, math.inf):
                best_time[neighbour_id] = next_time
                heapq.heappush(
                    queue, (next_time, neighbour_id, travelled + km, path + [neighbour_id])
                )

    raise ValueError(
        f"No route between {origin_id!r} and {destination_id!r} within the graph's edge limit"
    )


def route_between_points(
    origin: Tuple[float, float],
    destination: Tuple[float, float],
    waypoints: Sequence[Tuple[str, float, float]] = (),
    max_edge_km: float = 120.0,
) -> RouteResult:
    """Convenience wrapper: route from one coordinate to another.

    `waypoints` are real intermediate positions -- typically places the fleet
    has actually driven through, taken from ping history -- which is what
    gives the graph any shape beyond a straight line.
    """
    nodes = [
        RouteNode("origin", origin[0], origin[1], kind="origin"),
        RouteNode("destination", destination[0], destination[1], kind="destination"),
    ]
    nodes.extend(RouteNode(wid, lat, lng) for wid, lat, lng in waypoints)
    graph = build_graph(nodes, max_edge_km=max_edge_km)
    return fastest_route(nodes, "origin", "destination", graph)
