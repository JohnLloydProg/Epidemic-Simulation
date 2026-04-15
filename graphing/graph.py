from functools import lru_cache
from graphing.core import Node, Edge, Region
from agents.core import Firm, Household
import pygame as pg
import heapq
import random


class Graph:
    nodes:dict[int, 'Node']
    edges:dict[int, 'Edge']
    start_drag:tuple[int, int] = None
    x_temp_offset:int = None
    y_temp_offset:int = None
    y_offset:int = 0
    x_offset:int = 0

    def __init__(self, layer:str):
        self.layer = layer
        self.nodes = {}
        self.edges = {}

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

    def get_edge(self, id) -> Edge:
        return self.edges.get(id)
                
    def get_node(self, id:int) -> Node:
        return self.nodes.get(id)

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


class RegionGraph(Graph):
    regions:dict[int, 'Region']

    def __init__(self, layer:str):
        super().__init__(layer)
        self.regions = {}
    
    def add_region(self, node_ids:list[int], no_households:int, no_firms:int):
        region_nodes = []

        for id in node_ids:
            region_nodes.append(self.nodes.get(id))

        region = Region(region_nodes)
        for _ in range(no_households):
            region.add_household()
        
        for _ in range(no_firms):
            region.add_firm()

        self.regions[region.id] = region
    
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
    

