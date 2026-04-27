from graphing.core import Edge, Node
from graphing.graph import Graph
from sim_event import manager
import pygame as pg
import numpy as np
import math
import random


class Route:
    id:int = 0
    spawn_time:int
    ordered_nodes:list[Node]

    def __init__(self, spawn_node:Node, path:list[Edge], graph:Graph, transport_class:type['Transportation'], spawn_time:int):
        self.path = path
        self.graph = graph
        self.id = Route.id
        self.transport_class = transport_class
        self.spawn_time = spawn_time
        self.spawn_node = spawn_node
        Route.id += 1
        self.ordered_nodes = self.generate_ordered_nodes()

    def generate_ordered_nodes(self) -> list[Node]:
        nodes = [self.spawn_node]
        current = self.spawn_node
        for edge in self.path:
            current = edge.get_adjacent_node(current)
            nodes.append(current)
        return nodes
    
    def __str__(self):
        return f"Route {self.id} from {self.spawn_node.id} to {self.path[-1].get_adjacent_node(self.path[-1].nodes[1]).id if self.path else self.spawn_node.id}"
    
    def generate_transportation(self, current_time:int) -> 'Transportation':
        manager.emit(current_time + self.spawn_time, manager.RouteEvent(manager.TRANSPORTATION_SPAWN, self))
        return self.transport_class(current_node=self.spawn_node, route=self)

    def next_edge(self, current_edge:Edge) -> Edge | None:
        if (len(self.path) == 0):
            return None
        if (current_edge is None):
            return self.path[0]
        index = self.path.index(current_edge)
        return self.path[index + 1] if index + 1 < len(self.path) else None

    def draw(self, window:pg.Rect, graph:Graph):
        x_offset = graph.x_offset if graph.x_temp_offset == None else graph.x_temp_offset
        y_offset = graph.y_offset if graph.y_temp_offset == None else graph.y_temp_offset

        points = [(node.pos[0] + x_offset, node.pos[1] + y_offset) for node in self.ordered_nodes]
        pg.draw.lines(window, (200, 0, 0), False, points, 2)


class Transportation:
    agents:list
    current_edge:Edge = None
    id:int = 0
    speed:int

    def __init__(self, max_passenger:int, method:str, current_node:Node, route:Route):
        self.route = route
        self.agents = []
        self.current_node = current_node
        self.max_passenger = max_passenger
        self.method = method
        self.id = Transportation.id
        Transportation.id += 1
    
    def is_full(self) -> bool:
        return len(self.agents) >= self.max_passenger

    def occupancy(self) -> float:
        return (len(self.agents) / self.max_passenger)*100
    
    def transport(self, current_time:int):
        next_edge = self.route.next_edge(self.current_edge)
        if (not next_edge):
            manager.emit(current_time + 1, manager.TransportationEvent(manager.TRANSPORTATION_DESPAWN, self))
            return
        self.current_edge = next_edge
        travel_time = self.current_edge.distance / self.speed
        manager.emit(current_time + math.ceil(travel_time), manager.TransportationEvent(manager.TRANSPORTATION_ARRIVED, self))


class PublicTransportation(Transportation):
    speed:int = 150

    def __init__(self, current_node:Node, route:Route):
        super().__init__(max_passenger=random.choice([15, 20, 25, 30, 35, 40]), method='public', current_node=current_node, route=route)
        self.speed = np.random.normal(loc=PublicTransportation.speed, scale=20)


class RailTransportation(Transportation):
    speed:int = 600

    def __init__(self, current_node:Node, route:Route):
        super().__init__(max_passenger=1500, method='rail', current_node=current_node, route=route)


if __name__ == '__main__':
    pass
