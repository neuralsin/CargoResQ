"""
Dijkstra's Shortest & Fastest Path Algorithm (Phase 15+).
Computes optimal corridor routing based on distance, road quality, and live congestion weights.
"""
import heapq
import math
from typing import Dict, List, Tuple, Any, Optional

# Standard Highway Network Graph (NH-48 Corridor + Key Logistics Hubs)
CORRIDOR_NODES: Dict[str, Tuple[float, float, str]] = {
    "PUN_DEP": (18.5204, 73.8567, "Pune Serum Logistics Depot"),
    "STR_TOL": (17.6805, 74.0183, "Satara Toll Checkpoint"),
    "KOL_HUB": (16.7050, 74.2433, "Kolhapur Cold Vault"),
    "BEL_BYP": (15.8497, 74.4977, "Belgaum NH-48 Bypass"),
    "HUB_JNC": (15.3647, 75.1240, "Hubli Logistics Junction"),
    "DAV_CHK": (14.4644, 75.9218, "Davanagere Corridor Node"),
    "TUM_IND": (13.3409, 77.1006, "Tumkur Industrial Park"),
    "BLR_HUB": (12.9716, 77.5946, "Bangalore Distribution Hub"),
    "VLR_BYP": (12.9165, 79.1325, "Vellore Bypass"),
    "KNC_EST": (12.8342, 79.7036, "Kanchipuram East"),
    "CHN_TRM": (13.0827, 80.2707, "Chennai Port Terminal"),
    # Urban Clinic & Cold Depot Nodes (Matching Desktop Reference 1:1)
    "SF_ORIGIN": (37.7915, -122.3934, "144 Spear St Health Care Depot"),
    "SF_WAY_1": (37.7845, -122.4014, "Mission & 4th Corridor"),
    "SF_WAY_2": (37.7765, -122.4172, "Market & 10th Interchange"),
    "SF_WAY_3": (37.7682, -122.4265, "Mission District Reefer Dock"),
    "SF_DEST": (37.7562, -122.4208, "Central General Clinical Terminal"),
}

# Adjacency list: (neighbor, distance_km, avg_speed_kmh, congestion_multiplier)
CORRIDOR_EDGES: Dict[str, List[Tuple[str, float, float, float]]] = {
    # NH-48 Corridor
    "PUN_DEP": [("STR_TOL", 112.0, 65.0, 1.0)],
    "STR_TOL": [("PUN_DEP", 112.0, 60.0, 1.1), ("KOL_HUB", 124.0, 70.0, 1.0)],
    "KOL_HUB": [("STR_TOL", 124.0, 70.0, 1.0), ("BEL_BYP", 108.0, 75.0, 1.0)],
    "BEL_BYP": [("KOL_HUB", 108.0, 75.0, 1.0), ("HUB_JNC", 98.0, 75.0, 1.05)],
    "HUB_JNC": [("BEL_BYP", 98.0, 75.0, 1.05), ("DAV_CHK", 142.0, 80.0, 1.0)],
    "DAV_CHK": [("HUB_JNC", 142.0, 80.0, 1.0), ("TUM_IND", 195.0, 80.0, 1.0)],
    "TUM_IND": [("DAV_CHK", 195.0, 80.0, 1.0), ("BLR_HUB", 72.0, 50.0, 1.3)],
    "BLR_HUB": [
        ("TUM_IND", 72.0, 50.0, 1.3),
        ("VLR_BYP", 210.0, 70.0, 1.1),
    ],
    "VLR_BYP": [("BLR_HUB", 210.0, 70.0, 1.1), ("KNC_EST", 68.0, 65.0, 1.05)],
    "KNC_EST": [("VLR_BYP", 68.0, 65.0, 1.05), ("CHN_TRM", 74.0, 55.0, 1.25)],
    "CHN_TRM": [("KNC_EST", 74.0, 55.0, 1.25)],

    # Urban Rapid Medical Relay Corridor (SF Healthcare 1:1)
    "SF_ORIGIN": [("SF_WAY_1", 1.8, 30.0, 1.2)],
    "SF_WAY_1": [("SF_ORIGIN", 1.8, 30.0, 1.2), ("SF_WAY_2", 2.2, 35.0, 1.1)],
    "SF_WAY_2": [("SF_WAY_1", 2.2, 35.0, 1.1), ("SF_WAY_3", 2.4, 40.0, 1.0)],
    "SF_WAY_3": [("SF_WAY_2", 2.4, 40.0, 1.0), ("SF_DEST", 2.0, 32.0, 1.15)],
    "SF_DEST": [("SF_WAY_3", 2.0, 32.0, 1.15)],
}


def dijkstra_fastest_route(
    start_node: str,
    end_node: str,
    custom_edges: Optional[Dict[str, List[Tuple[str, float, float, float]]]] = None,
) -> Dict[str, Any]:
    """
    Executes Dijkstra's algorithm prioritizing minimum travel time (minutes).
    Weight = (distance_km / avg_speed_kmh) * 60 * congestion_multiplier
    """
    graph = custom_edges or CORRIDOR_EDGES

    if start_node not in CORRIDOR_NODES or end_node not in CORRIDOR_NODES:
        raise ValueError(f"Start ({start_node}) or End ({end_node}) not recognized in road network")

    # Priority queue stores: (cumulative_time_mins, current_node, cumulative_dist_km, path_nodes)
    pq: List[Tuple[float, str, float, List[str]]] = [(0.0, start_node, 0.0, [start_node])]
    visited: Dict[str, float] = {}

    best_time = float("inf")
    best_dist = 0.0
    best_path: List[str] = []

    while pq:
        curr_time, node, curr_dist, path = heapq.heappop(pq)

        if node in visited and visited[node] <= curr_time:
            continue
        visited[node] = curr_time

        if node == end_node:
            best_time = curr_time
            best_dist = curr_dist
            best_path = path
            break

        for neighbor, dist_km, speed_kmh, congestion in graph.get(node, []):
            edge_time_mins = (dist_km / max(speed_kmh, 10.0)) * 60.0 * congestion
            new_time = curr_time + edge_time_mins
            new_dist = curr_dist + dist_km

            if neighbor not in visited or new_time < visited[neighbor]:
                heapq.heappush(pq, (new_time, neighbor, new_dist, path + [neighbor]))

    if not best_path:
        raise ValueError(f"No valid route found between {start_node} and {end_node}")

    # Build detailed itinerary & coordinates
    itinerary: List[Dict[str, Any]] = []
    coordinates: List[Tuple[float, float]] = []

    for n in best_path:
        lat, lng, name = CORRIDOR_NODES[n]
        coordinates.append((lat, lng))
        itinerary.append({
            "nodeId": n,
            "name": name,
            "lat": lat,
            "lng": lng,
        })

    return {
        "startNode": start_node,
        "endNode": end_node,
        "totalTimeMinutes": round(best_time, 1),
        "totalDistanceKm": round(best_dist, 2),
        "nodeCount": len(best_path),
        "path": best_path,
        "itinerary": itinerary,
        "coordinates": coordinates,
        "algorithm": "Dijkstra (Weighted Congestion Multiplier)",
    }
