from functools import lru_cache
from graphing.core import Node, Edge
from agents.sector import Firm, Household
import event
import pygame as pg
import heapq
import random


class Region:
    id:int = 0
    firms:list[Firm]
    households:list[Household]
    
    def __init__(self, nodes:list[Node]):
        self.nodes = nodes
        self.firms = []
        self.households = []
        self.id = Region.id
        Region.id += 1
    
    def add_firm(self):
        connected_nodes = list(filter(lambda node: len(node.edges) > 0, self.nodes))
        firm = Firm(random.choice(connected_nodes), random.choices(['micro', 'small', 'medium', 'large'], weights=[0.84, 0.13, 0.02, 0.01])[0])
        self.firms.append(firm)

    def add_household(self):
        connected_nodes = list(filter(lambda node: len(node.edges) > 0, self.nodes))
        household = Household(random.choice(connected_nodes))
        self.households.append(household)


class Graph():
    nodes:dict[int, 'Node']
    edges:dict[int, 'Edge']
    regions:dict[int, 'Region']
    start_drag:tuple[int, int] = None
    x_temp_offset:int = None
    y_temp_offset:int = None
    y_offset:int = 0
    x_offset:int = 0

    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.regions = {}

    def add_node(self, node:Node):
        self.nodes[node.id] = node
    
    def add_edge(self, distance:int, *args):
        if (len(args) != 2):
            raise ValueError(f"Expected 2 arguments received {len(args)}")
        node_1 = None
        node_2 = None
        if (isinstance(args[0], int) and isinstance(args[1], int)):
            id_1, id_2 = args[0], args[1]
            if (id_1 == id_2):
                raise ValueError("You can't add an edge connecting the same nodes")
            node_1 = self.nodes.get(id_1)
            node_2 = self.nodes.get(id_2)
        elif (isinstance(args[0], Node) and isinstance(args[1], Node)):
            node_1, node_2 = args[0], args[1]
            if (node_1 == node_2):
                raise ValueError("You can't add an edge connecting the same nodes")

        if (not node_1 or not node_2):
            raise ValueError(f"No node_1 or node_2")    
        
        edge = Edge(node_1, node_2, distance)
        self.edges[edge.id] = edge
        node_1.edges.append(edge)
        node_2.edges.append(edge)
    
    def add_region(self, node_ids:list[int], no_households:int, no_firms:int):
        region_nodes = []

        for id in node_ids:
            region_nodes.append(self.nodes.get(id))

        region = Region(region_nodes)
        for _ in range(no_households):
            region.add_household()
        
        for _ in range(no_firms):
            region.add_firm()
        
        for firm in region.firms:
            event.emit(24 * 60, event.FirmEvent(event.FIRM_ACTIVITY_COLLECTION, firm))

        self.regions[region.id] = region

    def get_edge(self, id) -> Edge:
        return self.edges.get(id)
                
    def get_node(self, id:int) -> Node:
        return self.nodes.get(id)

    def get_firms(self) -> list[Firm]:
        firms = []
        for region in self.regions.values():
            firms.extend(region.firms)
        return firms

    def get_households(self) -> list[Household]:
        households = []
        for region in self.regions.values():
            households.extend(region.households)
        return households
    
    @lru_cache(maxsize=None, typed=False)
    def shortest_edge_path(self, start_id: int, end_id: int) -> list[int]:
        if start_id not in self.nodes or end_id not in self.nodes:
            raise ValueError("Start or end node ID not in graph.")

        # Initialize distances and previous edge mapping
        distances: dict[int, float] = {node: float('inf') for node in self.nodes}
        previous_edge: dict[int, int | None] = {node: None for node in self.nodes}

        distances[start_id] = 0
        pq: list[tuple[float, int]] = [(0, start_id)]  # (distance, node_id)

        while pq:
            dist, current_id = heapq.heappop(pq)
            current_node = next(n for n in self.nodes.values() if n.id == current_id)

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
            edge = next(e for e in self.edges.values() if e.id == edge_id)
            # move to the other node in the edge
            current = edge.nodes[0].id if edge.nodes[1].id == current else edge.nodes[1].id

        path.reverse()
        return path

    def map_dragging(self, event:pg.event.Event):
        if (event.type == pg.MOUSEBUTTONDOWN):
            self.start_drag = event.pos
        elif (event.type == pg.MOUSEBUTTONUP and self.y_temp_offset and self.x_temp_offset):
            self.x_offset = self.x_temp_offset
            self.y_offset = self.y_temp_offset
            self.start_drag = None
            self.x_temp_offset = None
            self.y_temp_offset = None
        elif (event.type == pg.MOUSEMOTION and self.start_drag):
            self.x_temp_offset = self.x_offset + (event.pos[0] - self.start_drag[0])
            self.y_temp_offset = self.y_offset + (event.pos[1] - self.start_drag[1])
        

    def draw(self, window:pg.Surface, font:pg.font.Font):
        x_offset = self.x_offset if self.x_temp_offset == None else self.x_temp_offset
        y_offset = self.y_offset if self.y_temp_offset == None else self.y_temp_offset
        
        for edge in self.edges.values():
            edge.draw(window, x_offset, y_offset)

        for node in self.nodes.values():
            node.draw(window, font, x_offset, y_offset)

