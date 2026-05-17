from graphing.core import Edge, Node
from graphing.graph import Graph
from objects import InitialParameters
import pygame as pg
import numpy as np
import math
import random
import logging
import manager
import traceback

LOGGER = logging.getLogger('Transportation')


class Route:
    id:int = 0
    spawn_time:int
    ordered_nodes:list[Node]
    transportations:list['RoutedTransportation']
    expected_speed:int = 150

    def __init__(self, spawn_node:Node, path:list[Edge], graph:Graph, spawn_time:int, peak_spawn:int):
        self.path = path
        self.graph = graph
        self.id = Route.id
        self.spawn_time = spawn_time
        self.spawn_node = spawn_node
        self.peak_spawn = peak_spawn
        self.transportations = []
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
    
    def generate_transportation(self, current_time:int, is_peak_hours:bool) ->list['RoutedTransportation']:
        spawn_interval = self.spawn_time if not is_peak_hours else self.peak_spawn
        manager.emit(current_time + spawn_interval, manager.Event(manager.TRANSPORTATION_SPAWN, self))
        _transportations = []
        for i in range(random.randint(2, 3)):
            if (random.random() < 0.5):
                passenger = random.choice([(10, 10), (12, 12), (15, 15), (15, 20) ])
                max_passenger = passenger[1]
                suggested_passenger = passenger[0]
                expected_contact_rate = 3.5
                method = 'jeep'
            else:
                max_passenger = 50
                suggested_passenger = 40
                expected_contact_rate = 4.5
                method = 'bus'
            transportation = RoutedTransportation(method, self.expected_speed, max_passenger, suggested_passenger, self.spawn_node, self)
            transportation.expected_contact_rate = expected_contact_rate
            _transportations.append(transportation)
            self.transportations.append(transportation)
        return _transportations

    def get_average_occupancy(self) -> float:
        occupancies = [transportation.occupancy() for transportation in self.transportations]
        return round(sum(occupancies)/len(occupancies), 2) if occupancies else 0

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
        average_occupancy = self.get_average_occupancy()

        points = [(node.pos[0] + x_offset, node.pos[1] + y_offset) for node in self.ordered_nodes]
        pg.draw.lines(window, (255, int(255*(1 - average_occupancy)), 0), False, points, 2)


class TrainRoute(Route):
    expected_speed:int = 600

    def __init__(self, spawn_node:Node, path:list[Edge], graph:Graph, spawn_time:int, peak_spawn:int):
        super().__init__(spawn_node, path, graph, spawn_time, peak_spawn)

    def generate_transportation(self, current_time, is_peak_hours:bool):
        spawn_interval = self.spawn_time if not is_peak_hours else self.peak_spawn
        manager.emit(current_time + spawn_interval, manager.Event(manager.TRANSPORTATION_SPAWN, self))
        transportation = RoutedTransportation('rail', self.expected_speed, 2000, 1300, self.spawn_node, self)
        transportation.expected_contact_rate = 8.5
        self.transportations.append(transportation)
        return [transportation]


class Transportation:
    id:int = 0
    agents:list
    no_infected_agents:int = 0
    current_edge:Edge = None

    def __init__(self, method:str, speed:float, max_passenger:int, current_node:Node, path:list[Edge]=[]):
        self.method = method
        self.current_node = current_node
        self.speed = speed
        self.path = path
        self.id = Transportation.id
        self.agents = []
        self.max_passenger = max_passenger
        self.path = path.copy()
        Transportation.id += 1
    
    def is_full(self) -> bool:
        return len(self.agents) >= self.max_passenger

    def occupancy(self) -> float:
        return len(self.agents) / self.max_passenger
    
    def transport(self, current_time:int):
        self.current_edge = self.path.pop(0)
        travel_time = self.current_edge.distance / self.speed
        manager.emit(current_time + math.ceil(travel_time), manager.Event(manager.PRIVATE_TRANSPORTATION_ARRIVED, self))


class RoutedTransportation(Transportation):
    expected_contact_rate:float = 5.0

    def __init__(self, method:str, speed:float, max_passenger:int, suggested_passenger:int, current_node:Node, route:Route):
        super().__init__(method=method, speed=speed, max_passenger=max_passenger, current_node=current_node)
        self.route = route
        self.suggested_passenger = suggested_passenger
    
    def get_contact_rate(self) -> float:
        return self.expected_contact_rate * (len(self.agents)/self.suggested_passenger)
    
    def get_infected_density(self) -> float:
        if (self.agents):
            return self.no_infected_agents / len(self.agents)
        else:
            return 0
    
    def transport(self, current_time:int):
        next_edge = self.route.next_edge(self.current_edge)
        if (not next_edge):
            manager.emit(current_time + 1, manager.Event(manager.TRANSPORTATION_DESPAWN, self))
            return
        self.current_edge = next_edge
        travel_time = self.current_edge.distance / self.speed
        manager.emit(current_time + math.ceil(travel_time), manager.Event(manager.TRANSPORTATION_ARRIVED, self))


def handle_route_events(event:manager.Event, transportations:list[Transportation], is_peak_hours:bool, time:int):
    routes:list[Route] = event.get_objects()
    if (event.type == manager.TRANSPORTATION_SPAWN):
        LOGGER.debug(f"Handling transportation spawn for {len(routes)} routes at time {time}.")
        for route in routes:
            transports = route.generate_transportation(current_time=time, is_peak_hours=is_peak_hours)
            for transport in transports:
                for agent in list(transport.current_node.agents):
                    if (agent.state != 'waiting'):
                        continue

                    current_leg = agent.checkpoints[0]
                    if (current_leg.mode == 'ride' and current_leg.end_node in transport.route.ordered_nodes):
                        current_index = transport.route.ordered_nodes.index(transport.current_node)
                        for node in transport.route.ordered_nodes[current_index:]:
                            if (not transport.is_full() and current_leg.end_node == node):
                                agent.ride_transportation(transport, time)
                                agent.set_state('travelling')
                                break
                transport.transport(time)
            transportations.extend(transports)


def handle_transportation_events(event:manager.Event, transportations:list[Transportation], initial_parameters:InitialParameters, time:int):
    _transportations:list[Transportation] = event.get_objects()
    if (event.type == manager.TRANSPORTATION_ARRIVED):
        LOGGER.debug(f"Handling transportation arrival for {len(event.get_objects())} transportations at time {time}.")
        for transport in _transportations:
            transport.current_node = transport.current_edge.get_adjacent_node(transport.current_node)
            for agent in list(transport.agents):
                if (agent.state != 'travelling'):
                    transport.agents.remove(agent)
                    continue

                if (transport.current_node.id == agent.checkpoints[0].end_node.id):
                    agent.alight_transportation()
                    agent.arrival(time)

            for agent in list(transport.current_node.agents):
                if (agent.state != 'waiting'):
                    continue

                current_leg = agent.checkpoints[0]
                if (current_leg.mode == 'ride' and current_leg.end_node in transport.route.ordered_nodes):
                    current_index = transport.route.ordered_nodes.index(transport.current_node)
                    for node in transport.route.ordered_nodes[current_index:]:
                        if (not transport.is_full() and current_leg.end_node == node and not agent.transportation):
                            agent.ride_transportation(transport, time)
                            agent.set_state('travelling')
                            break
                
            transport.transport(time)
    elif (event.type == manager.PRIVATE_TRANSPORTATION_ARRIVED):
        LOGGER.debug(f"Handling private transportation arrival for {len(event.get_objects())} transportations at time {time}.")
        for transport in _transportations:
            if (not transport.agents):
                continue

            transport.current_node = transport.current_edge.get_adjacent_node(transport.current_node)
            agent = transport.agents[0]
            if (transport.current_node.id == agent.destination.node.id):
                agent.alight_transportation()
                agent.arrival(time, transport.current_node)
            else:
                transport.transport(time)
    elif (event.type == manager.TRANSPORTATION_DESPAWN):
        LOGGER.debug(f"Handling transportation despawn for {len(event.get_objects())} transportations at time {time}.")
        for transport in _transportations:
            transportations.remove(transport)
