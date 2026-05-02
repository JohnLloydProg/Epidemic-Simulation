from functools import lru_cache
from graphing.core import Node, Edge
from graphing.graph import Graph, RegionGraph
from transport.transportation import Route, TrainRoute
from sim_event import manager
import pandas as pd
import heapq
import random
import math


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


def shortest_path(start_node:Node, end_node:Node, routes:list[Route]) -> list[tuple[Node, Route | None]]:
    if (start_node == end_node):
        return []

    open_set = []
    heapq.heappush(open_set, State(start_node, 0, None, None))

    TRANSFER_PENALTY = 5.0

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
                walk_cost = edge.distance / 75
                heapq.heappush(open_set, State(neighbor_node, current_state.cost + walk_cost, None, current_state))
                
            # 2. Board available routes at this node
            for route in routes:
                if current_node in route.ordered_nodes:
                    heapq.heappush(open_set, State(current_node, current_state.cost + TRANSFER_PENALTY, route, current_state))

        # Scenario B: Riding
        else:
            # 1. Stay on vehicle
            for idx, node in enumerate(current_route.ordered_nodes):
                if (current_node == node and idx + 1 < len(current_route.ordered_nodes)):
                    neighbor_node = current_route.ordered_nodes[idx + 1]
                    edge_to_take = current_route.path[idx]

                    ride_cost = edge_to_take.distance / current_route.transport_class.speed
                    heapq.heappush(open_set, State(neighbor_node, current_state.cost + ride_cost, current_route, current_state))
                
            # 2. Alight (Switch to walking)
            heapq.heappush(open_set, State(current_node, current_state.cost, None, current_state))

    return []


def load_graph() -> tuple[RegionGraph, Graph, list[Route]]:
    map_path = './map/'
    city_graph = RegionGraph('city')
    railway_graph = Graph('railway')
    graphs: list[Graph] = [city_graph, railway_graph]
    
    # Load nodes and edges for each graph
    print('Generating nodes and edges for graphs...')
    for graph in graphs:
        nodes = pd.read_excel(f"{map_path}/{graph.layer}/nodes.xlsx", index_col=0)
        for i, node_xl in nodes.iterrows():
            if (pd.isna(i)):
                print(f"Skipping node with NaN index in {graph.layer} graph.")
                continue
            graph.add_node(int(node_xl['X-Coordinate']), int(node_xl['Y-Coordinate']), i)
        
        edges = pd.read_excel(f"{map_path}/{graph.layer}/edges.xlsx", index_col=0)
        for i in range(len(edges)):
            edge_xl = edges.iloc[i]
            try:
                graph.add_edge(int(edge_xl['Distance (m)']), (graph.layer, int(edge_xl['Node 1'])), (graph.layer, int(edge_xl['Node 2'])))
            except Exception as e:
                print(f"Error adding edge {i}: {e}")
                print(f"Node 1: {(graph.layer, int(edge_xl['Node 1']))}, Node 2: {(graph.layer, int(edge_xl['Node 2']))}")
    
    # Load regions for the city graph
    print('Generating regions for city map...')
    regions = pd.read_excel(f'{map_path}/{city_graph.layer}/regions.xlsx', index_col=0)
    regions[['Map edge nodes within the region', 'Street Nodes within the Region']] = regions[['Map edge nodes within the region', 'Street Nodes within the Region']].astype(str)

    for i in range(len(regions)):
        region_xl = regions.iloc[i]
        nodes = region_xl['Map edge nodes within the region'] if region_xl['Map edge nodes within the region'] != 'nan' else ""
        nodes +=  region_xl['Street Nodes within the Region'] if region_xl['Street Nodes within the Region'] != 'nan' else ""

        nodes = nodes.strip(",")
        node_ids = [(city_graph.layer, int(node_id)) for node_id in nodes.split(",")]
        try:
            city_graph.add_region(node_ids, math.ceil(int(region_xl['Alloted Residential Units']) * 0.1), math.ceil(int(region_xl['Alloted Business Units']) * 0.1))
        except Exception as e:
            print(f"Error adding region {i}: {e}")
            print(f"Node IDs: {node_ids}")
    
    print('Generating transfer edges between city graph and railway graph...')
    # Load transfer edges between city and railway graph
    transfer_edges = pd.read_excel(f"{map_path}/transfer.xlsx", index_col=0)
    for i in range(len(transfer_edges)):
        edge_xl = transfer_edges.iloc[i]
        try:
            city_node_id = (city_graph.layer, int(edge_xl['Node 1 (Layer 1)']))
            railway_node_id = (railway_graph.layer, int(edge_xl['Node 2 (Layer 2)']))
            city_node = city_graph.get_node(city_node_id)
            railway_node = railway_graph.get_node(railway_node_id)
            if not city_node or not railway_node:
                raise ValueError(f"Invalid node IDs for transfer edge: {city_node_id}, {railway_node_id}")
            transfer_edge = Edge(city_node, railway_node, 50, ('transfer', i))
            city_graph.edges[transfer_edge.id] = transfer_edge
            railway_graph.edges[transfer_edge.id] = transfer_edge
            city_node.edges.append(transfer_edge)
            railway_node.edges.append(transfer_edge)
        except Exception as e:
            print(f"Error adding transfer edge {i}: {e}")
            print(f"City Node ID: {(city_graph.layer, int(edge_xl['City Node']))}, Railway Node ID: {(railway_graph.layer, int(edge_xl['Railway Node']))}")
    
    # Load routes for transportation
    print('Generating routes for city graph...')
    routes = []

    route_data = pd.read_excel(f"{map_path}/{city_graph.layer}/routes.xlsx", index_col=None)
    for i in range(len(route_data)):
        route_xl = route_data.iloc[i]
        node_id = (city_graph.layer, int(route_xl['Node 1']))
        reverse_node_id = (city_graph.layer, int(route_xl['Node 2']))
        node = city_graph.get_node(node_id)
        reverse_node = city_graph.get_node(reverse_node_id)
        if not node:
            print(f"Error loading route {i}: Node {node_id} not found in city graph.")
            continue
        try:
            edges = shortest_edge_path((city_graph.layer, int(route_xl['Node 1'])), (city_graph.layer, int(route_xl['Node 2'])), city_graph, railway_graph)
            reversed_edges = edges.copy()
            reversed_edges.reverse()
        except Exception as e:
            print(f"Error finding path for route {i}: {e}")
            print(f"Node 1: {(city_graph.layer, int(route_xl['Node 1']))}, Node 2: {(city_graph.layer, int(route_xl['Node 2']))}")
            continue # Interval
        route = Route(node, edges, city_graph, int(route_xl['Interval']), int(route_xl['Peak Interval']))
        return_route = Route(reverse_node, reversed_edges, city_graph, int(route_xl['Interval']), int(route_xl['Peak Interval']))
        routes.append(route)
        routes.append(return_route)

    route_data = pd.read_excel(f"{map_path}/{railway_graph.layer}/routes.xlsx", index_col=None)
    for i in range(len(route_data)):
        route_xl = route_data.iloc[i]
        node_id = (railway_graph.layer, int(route_xl['Node 1']))
        reverse_node_id = (railway_graph.layer, int(route_xl['Node 2']))
        node = railway_graph.get_node(node_id)
        reverse_node = railway_graph.get_node(reverse_node_id)
        if not node:
            print(f"Error loading route {i}: Node {node_id} not found in railway graph.")
            continue
        edge_ids = route_xl['Path'].split(',')
        edge_ids = [(railway_graph.layer, int(edge_id.strip())) for edge_id in edge_ids]
        path = [railway_graph.get_edge(edge_id) for edge_id in edge_ids]
        reverse_path = path.copy()
        reverse_path.reverse()
        route = TrainRoute(node, path, railway_graph, int(route_xl['Interval']), int(route_xl['Peak Interval']))
        return_route = TrainRoute(reverse_node, reverse_path, railway_graph, int(route_xl['Interval']), int(route_xl['Peak Interval']))
        routes.append(route)
        routes.append(return_route)
    
    print('Graph ready!')
    return (city_graph, railway_graph, routes)


if __name__ == '__main__':
    load_graph()