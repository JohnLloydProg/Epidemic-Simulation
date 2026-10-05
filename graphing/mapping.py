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
    if (start_node == end_node):
        return []

    open_set = []
    heapq.heappush(open_set, State(start_node, 0, None, None))

    TRANSFER_PENALTY = 120

    visited = {}

    while open_set:
        current_state:State = heapq.heappop(open_set)
        current_node:Node = current_state.node
        current_route:Route | None = current_state.route
        
        if current_node == end_node:
            # Reconstruct and return the raw path
            path = []
            curr = current_state
            while curr is not None:
                path.append((curr.node, curr.route))
                curr = curr.previous_state
            return path[::-1]
            
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

    return []