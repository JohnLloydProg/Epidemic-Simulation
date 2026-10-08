import configuration as config
from functools import lru_cache
from graphing.core import Node, Edge
from graphing.graph import Graph, RegionGraph
from transport.transportation import Route, TrainRoute, JeepRoute, BusRoute
import pandas as pd
import heapq
import logging
import os

LOGGER = logging.getLogger('Mapping')


class State:
    def __init__(self, node:Node, cost:float, route:Route | None, previous_state):
        self.node = node
        self.cost = cost
        self.route = route
        self.previous_state = previous_state

    def __lt__(self, other:'State'):
        return self.cost < other.cost


def can_drive(edge:Edge, from_node:Node) -> bool:
    """Vehicles (private cars, tricycles) may use the edge leaving from_node: two-way, or one-way in this direction.
    Walkers ignore one-way roads."""
    start = getattr(edge, 'oneway_from', None)
    return start is None or start is from_node


@lru_cache(maxsize=None, typed=False)
def shortest_drive_path(start_id: tuple[str, int], end_id: tuple[str, int], city:RegionGraph) -> list[Edge]:
    """Shortest road path for a vehicle: city roads only, one-way roads only in their direction. [] if none."""
    start, end = city.nodes.get(start_id), city.nodes.get(end_id)
    if start is None or end is None:
        raise ValueError("Start or end node ID not in graph.")
    if start is end:
        return []
    distances, previous, pq, counter = {start_id: 0.0}, {}, [(0.0, 0, start)], 1
    while pq:
        dist, _, node = heapq.heappop(pq)
        if node is end:
            break
        if dist > distances.get(node.id, float('inf')):
            continue
        for edge in node.edges:
            neighbor = edge.get_adjacent_node(node)
            if neighbor.id[0] != city.layer or not can_drive(edge, node):
                continue
            new_dist = dist + edge.distance
            if new_dist < distances.get(neighbor.id, float('inf')):
                distances[neighbor.id], previous[neighbor.id] = new_dist, (node, edge)
                heapq.heappush(pq, (new_dist, counter, neighbor))
                counter += 1
    if end_id not in previous:
        return []
    path, node = [], end
    while node is not start:
        node, edge = previous[node.id]
        path.append(edge)
    return path[::-1]


@lru_cache(maxsize=None, typed=False)
def shortest_edge_path(start_id: tuple[str, int], end_id: tuple[str, int], city:RegionGraph, railway:Graph) -> list[Edge]:
    total_nodes = city.nodes.copy()
    total_nodes.update(railway.nodes)
    total_edges = city.edges.copy()
    total_edges.update(railway.edges)

    if start_id not in total_nodes or end_id not in total_nodes:
        raise ValueError("Start or end node ID not in graph.")

    # Initialize distances and previous edge mapping
    distances: dict[int, float] = {node: float('inf') for node in total_nodes}
    previous_edge: dict[int, int | None] = {node: None for node in total_nodes}

    distances[start_id] = 0
    pq: list[tuple[float, int]] = [(0, start_id)]  # (distance, node_id)

    while pq:
        dist, current_id = heapq.heappop(pq)
        current_node = total_nodes.get(current_id)

        if dist > distances[current_id]:
            continue

        for edge in current_node.edges:
            neighbor = edge.get_adjacent_node(current_node)
            if (neighbor.id[0] != city.layer):
                continue
            new_dist = dist + edge.distance

            if new_dist < distances[neighbor.id]:
                distances[neighbor.id] = new_dist
                previous_edge[neighbor.id] = edge.id
                heapq.heappush(pq, (new_dist, neighbor.id))

    # Reconstruct path as list of edge IDs
    path: list[Edge] = []
    current = end_id

    while current != start_id:
        edge_id = previous_edge[current]
        if edge_id is None:
            return []  # no path

        edge = total_edges.get(edge_id)
        path.append(edge)
        # move to the other node in the edge
        current = edge.nodes[0].id if edge.nodes[1].id == current else edge.nodes[1].id

    path.reverse()
    return path


_route_index: dict[int, dict] = {}

def routes_at_node(node:Node, routes:list[Route]) -> list[Route]:
    """Routes that stop at a node. Built once per route list instead of scanning every route at every step."""
    index = _route_index.get(id(routes))
    if index is None:
        index = {}
        for route in routes:
            for route_node in set(route.ordered_nodes):
                index.setdefault(route_node.id, []).append(route)
        _route_index.clear()
        _route_index[id(routes)] = index
    return index.get(node.id, [])


def shortest_path(start_node:Node, end_node:Node, routes:list[Route]) -> list[tuple[Node, Route | None]]:
    """Fastest walk/ride path between two nodes (same as shortest_paths_from with one target)."""
    if (start_node == end_node):
        return []
    return shortest_paths_from(start_node, [end_node], routes).get(end_node, [])


def shortest_paths_from(start_node:Node, targets:list[Node], routes:list[Route]) -> dict[Node, list[tuple[Node, Route | None]]]:
    """Fastest walk/ride paths from one node to many targets in a single search.

    The search runs exactly like a one-pair search, but instead of stopping at the first target it records
    the path the first time each target is reached and keeps going until every target is found. So each path
    is identical to what a separate one-pair search would return (ties included), at a fraction of the cost.
    Targets that cannot be reached are missing from the result."""
    remaining = set(targets)
    remaining.discard(start_node)
    found = {}

    open_set = []
    heapq.heappush(open_set, State(start_node, 0, None, None))

    TRANSFER_PENALTY = 120
    # tricycle boarding cost in the search = expected wait + the fare expressed in seconds (transport/tricycle.py)
    TRICYCLE_WAIT = float(config.get('TRICYCLE_WAIT_S', 120)) + float(config.get('TRICYCLE_FARE_S', 300))
    from transport.tricycle import services_at_node as tricycle_services_at_node

    visited = {}

    while open_set and remaining:
        current_state:State = heapq.heappop(open_set)
        current_node:Node = current_state.node
        current_route:Route | None = current_state.route

        if current_node in remaining:
            # First time this target is reached: reconstruct and keep the raw path
            path = []
            curr = current_state
            while curr is not None:
                path.append((curr.node, curr.route))
                curr = curr.previous_state
            found[current_node] = path[::-1]
            remaining.discard(current_node)
            # no 'continue': searches to other targets pass through this node

        state_key = (current_node.id, current_route.id if current_route else None)
        if state_key in visited and visited[state_key] <= current_state.cost:
            continue
        visited[state_key] = current_state.cost

        # Scenario A: Walking
        if current_route is None:
            # 1. Walk to neighbors
            for edge in current_node.edges:
                neighbor_node = edge.get_adjacent_node(current_node)
                walk_cost = edge.distance / 2
                heapq.heappush(open_set, State(neighbor_node, current_state.cost + walk_cost, None, current_state))

            # 2. Board available routes at this node
            for route in routes_at_node(current_node, routes):
                heapq.heappush(open_set, State(current_node, current_state.cost + TRANSFER_PENALTY, route, current_state))

            # 3. Hail a tricycle of any barangay whose territory includes this node (transport/tricycle.py)
            for service in tricycle_services_at_node(current_node):
                heapq.heappush(open_set, State(current_node, current_state.cost + TRICYCLE_WAIT, service, current_state))

        # Scenario C: Riding a tricycle (any allowed road inside its territory)
        elif getattr(current_route, 'mode', None) == 'tricycle':
            for edge in current_node.edges:
                if current_route.allows(edge) and can_drive(edge, current_node):
                    neighbor_node = edge.get_adjacent_node(current_node)
                    heapq.heappush(open_set, State(neighbor_node, current_state.cost + edge.distance / current_route.expected_speed, current_route, current_state))
            heapq.heappush(open_set, State(current_node, current_state.cost, None, current_state))     # alight

        # Scenario B: Riding
        else:
            # 1. Stay on vehicle
            for idx, node in enumerate(current_route.ordered_nodes):
                if (current_node == node and idx + 1 < len(current_route.ordered_nodes)):
                    neighbor_node = current_route.ordered_nodes[idx + 1]
                    edge_to_take = current_route.path[idx]

                    ride_cost = edge_to_take.distance / current_route.expected_speed
                    heapq.heappush(open_set, State(neighbor_node, current_state.cost + ride_cost, current_route, current_state))

            # 2. Alight (Switch to walking)
            heapq.heappush(open_set, State(current_node, current_state.cost, None, current_state))

    return found
