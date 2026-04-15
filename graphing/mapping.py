import pandas as pd
from graphing.core import Node
from graphing.graph import Graph
import random
"""
@lru_cache(maxsize=None, typed=False)
def shortest_edge_path(self, start_id: int, end_id: int, nodes:dict[int, Node], edges:dict[int, Edge]) -> list[int]:
    if start_id not in nodes or end_id not in nodes:
        raise ValueError("Start or end node ID not in graph.")

    # Initialize distances and previous edge mapping
    distances: dict[int, float] = {node: float('inf') for node in nodes}
    previous_edge: dict[int, int | None] = {node: None for node in nodes}

    distances[start_id] = 0
    pq: list[tuple[float, int]] = [(0, start_id)]  # (distance, node_id)

    while pq:
        dist, current_id = heapq.heappop(pq)
        current_node = nodes.get(current_id)

        if dist > distances[current_id]:
            continue

        for edge in current_node.edges:
            neighbor = edge.get_adjacent_node(current_node)
            new_dist = dist + edge.distance

            if new_dist < distances[neighbor.id]:
                distances[neighbor.id] = new_dist
                previous_edge[neighbor.id] = edge.id
                heapq.heappush(pq, (new_dist, neighbor.id))

    # Reconstruct path as list of edge IDs
    path: list[int] = []
    current = end_id

    while current != start_id:
        edge_id = previous_edge[current]
        if edge_id is None:
            return []  # no path

        path.append(edge_id)
        edge = edges.get(edge_id)
        # move to the other node in the edge
        current = edge.nodes[0].id if edge.nodes[1].id == current else edge.nodes[1].id

    path.reverse()
    return path"""

def load_graph() -> tuple[Graph]:
    city_path = './cities/Mandaluyong'
    graph = Graph()
    nodes = pd.read_excel(f"{city_path}/nodes.xlsx", index_col=0)

    for i in range(len(nodes)):
        node_xl = nodes.iloc[i]
        node = Node(node_xl['X-Coordinate'], node_xl['Y-Coordinate'])
        graph.add_node(node)
    
    print("Nodes: ", [node for node in graph.nodes])
    edges = pd.read_excel(f"{city_path}/edges.xlsx", index_col=0)
    
    for i in range(len(edges)):
        edge_xl = edges.iloc[i]
        graph.add_edge(int(edge_xl['Distance (m)']), int(edge_xl['Node 1'] - 1), int(edge_xl['Node 2'] - 1))
    
    regions = pd.read_excel(f'{city_path}/regions.xlsx', index_col=0)
    regions[['Map edge nodes within the region', 'Artifical Nodes within the region', 'Street Nodes within the Region']] = regions[['Map edge nodes within the region', 'Artifical Nodes within the region', 'Street Nodes within the Region']].astype(str)

    for i in range(len(regions)):
        region_xl = regions.iloc[i]
        nodes = region_xl['Map edge nodes within the region'] if region_xl['Map edge nodes within the region'] != 'nan' else ""
        nodes += region_xl['Artifical Nodes within the region'] if region_xl['Artifical Nodes within the region'] != 'nan' else ""
        nodes +=  region_xl['Street Nodes within the Region'] if region_xl['Street Nodes within the Region'] != 'nan' else ""

        nodes = nodes.strip(",")
        node_ids = [int(node_id) - 1 for node_id in nodes.split(",")]

        graph.add_region(node_ids, int(region_xl['Alloted Residential Units']), int(region_xl['Alloted Business Units']))

    return graph


if __name__ == '__main__':
    load_graph('./cities/Mandaluyong')