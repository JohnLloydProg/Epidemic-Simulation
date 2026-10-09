import configuration as config
from transport import congestion
from graphing.core import Edge, Node
from graphing.graph import Graph
import pygame as pg
import math
import random
import logging
import manager

LOGGER = logging.getLogger('Transportation')


class Route:
    id:int = 0
    spawn_time:int
    ordered_nodes:list[Node]
    transportations:list['RoutedTransportation']
    expected_speed:int = 5
    capacity_ratio:int = 1
    

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
    
    def generate_transportation(self, current_time:int) -> list['RoutedTransportation']:
        """"""
        pass

    def get_average_occupancy(self) -> float:
        occupancies = [transportation.occupancy() for transportation in self.transportations]
        return round(sum(occupancies)/len(occupancies), 2) if occupancies else 0

    def set_path(self, spawn_node:Node, path:list[Edge]):
        """Give the route a new path (only done before the simulation starts)."""
        self.spawn_node = spawn_node
        self.path = list(path)
        self.ordered_nodes = self.generate_ordered_nodes()

    def next_edge(self, path_index:int) -> Edge | None:
        if (len(self.path) == 0):
            return None
        if (path_index < 0):
            return self.path[0]
        return self.path[path_index + 1] if path_index + 1 < len(self.path) else None

    def draw(self, window:pg.Surface, graph:Graph):
        average_occupancy = self.get_average_occupancy()
        points = [graph.camera.to_screen(node.pos) for node in self.ordered_nodes]
        pg.draw.lines(window, (255, int(255 * (1 - average_occupancy)), 0), False, points, graph.camera.scale(2))


def set_route_group_path(routes:list[Route], route_id:str, spawn_node:Node, path:list[Edge]) -> list[Route]:
    """Give every direction of a route a new path: the first (forward) direction gets `path` from
    `spawn_node`, the reverse direction runs it backwards. Returns the routes that were changed."""
    group = [route for route in routes if getattr(route, 'route_id', None) == route_id]
    if not group:
        raise ValueError(f"Route '{route_id}' not found.")
    end = spawn_node
    for edge in path:
        end = edge.get_adjacent_node(end)       # raises if the edges do not form a chain
    # same rule as the data loader: each direction follows one-way roads (transport/oneway_routes.py)
    from transport.oneway_routes import route_directions
    city = group[0].graph if group[0].graph.layer == 'city' else None
    directions = route_directions(spawn_node, path, city, len(group) > 1)
    for route, (spawn, edges) in zip(group, directions):
        route.set_path(spawn, edges)
    return group


def set_route_group_paths(routes:list[Route], route_id:str, directions:list[tuple]) -> list[Route]:
    """Give each direction of a route exactly the given (spawn_node, path), in order (forward, return), without
    re-deriving any of them — used for return trips stored in a case file (e.g. a reroute around hotspots, whose
    return trip must avoid the same hotspots). Directions not given keep their path."""
    group = [route for route in routes if getattr(route, 'route_id', None) == route_id]
    if not group:
        raise ValueError(f"Route '{route_id}' not found.")
    for route, (spawn, path) in zip(group, directions):
        end = spawn
        for edge in path:
            end = edge.get_adjacent_node(end)   # raises if the edges do not form a chain
        route.set_path(spawn, path)
    return group


class JeepRoute(Route):
    def __init__(self, spawn_node:Node, path:list[Edge], graph:Graph, spawn_time:int, peak_spawn:int):
        super().__init__(spawn_node, path, graph, spawn_time, peak_spawn)
    
    def generate_transportation(self, current_time) -> list['RoutedTransportation']:
        _transportations = []
        for i in range(random.randint(1, 2)):
            passenger = random.choice([(10, 10), (12, 12), (15, 15), (15, 20) ])
            transportation = RoutedTransportation('jeep', self.expected_speed, (0, 0, 255), passenger[1], self.capacity_ratio, passenger[0], 0, self.spawn_node, self)
            _transportations.append(transportation)
            self.transportations.append(transportation)
        return _transportations


class BusRoute(Route):
    def __init__(self, spawn_node:Node, path:list[Edge], graph:Graph, spawn_time:int, peak_spawn:int):
        super().__init__(spawn_node, path, graph, spawn_time, peak_spawn)
    
    def generate_transportation(self, current_time) -> list['RoutedTransportation']:
        transportation = RoutedTransportation('bus', self.expected_speed, (255, 0, 0), 50, self.capacity_ratio, 40, 0, self.spawn_node, self)
        self.transportations.append(transportation)
        return [transportation]


class TrainRoute(Route):
    expected_speed:int = 10

    def __init__(self, spawn_node:Node, path:list[Edge], graph:Graph, spawn_time:int, peak_spawn:int):
        super().__init__(spawn_node, path, graph, spawn_time, peak_spawn)

    def generate_transportation(self, current_time) -> list['RoutedTransportation']:
        absolute_max = 1200
        hour_of_day = (current_time // 3600) % 24
        if 7 <= hour_of_day <= 9 or 17 <= hour_of_day <= 19:
            external_load_percentage = random.uniform(0.80, 0.95)
        else:
            external_load_percentage = random.uniform(0.30, 0.60)
            
        seats_taken = int(absolute_max * external_load_percentage)

        transportation = RoutedTransportation('rail', self.expected_speed, (0, 255, 0), absolute_max, self.capacity_ratio, 900, seats_taken, self.spawn_node, self)
        self.transportations.append(transportation)
        return [transportation]


class Transportation:
    id:int = 0
    agents:list
    current_edge:Edge = None
    travel_time:float = None      # seconds the current edge takes (set by transport/congestion.py)
    waiting:bool = False          # True while waiting at a node for a full road (spillback)

    def __init__(self, method:str, speed:float, color:tuple, current_node:Node, path:list[Edge]=[]):
        self.method = method
        self.current_node = current_node
        self.speed = speed
        self.color = color
        self.path = path
        self.id = Transportation.id
        self.agents = []
        self.path = path
        Transportation.id += 1
    
    def transport(self, current_time:int):
        # transport/congestion.py decides how long the road takes, or that it is full (then wait at the node)
        travel_time = congestion.try_enter(self, self.path[0], self.current_node, current_time)
        if (travel_time is None):
            self.waiting = True
            congestion.retry_later(self, current_time)
            return
        self.waiting = False
        self.current_edge = self.path.pop(0)
        self.travel_time = travel_time
        self.start_travel = current_time
        manager.emit(current_time + math.ceil(travel_time), manager.Event(manager.PRIVATE_TRANSPORTATION_ARRIVED, self))

    def update_position(self, current_time:int):
        if (not self.current_edge or self.waiting):
            return self.current_node.pos
        travel_time = self.travel_time or self.current_edge.distance / self.speed
        time_elapsed = current_time - self.start_travel
        if (time_elapsed >= travel_time):
            return self.current_edge.get_adjacent_node(self.current_node).pos
        else:
            start_pos = self.current_node.pos
            end_pos = self.current_edge.get_adjacent_node(self.current_node).pos
            progress_ratio = time_elapsed / travel_time
            new_x = start_pos[0] + (end_pos[0] - start_pos[0]) * progress_ratio
            new_y = start_pos[1] + (end_pos[1] - start_pos[1]) * progress_ratio
            return (new_x, new_y)

    def draw(self, window:pg.Rect, camera, current_time:int):
        try:
            pos = camera.to_screen(self.update_position(current_time))
            pg.draw.circle(window, self.color, pos, 5)
        except Exception as e:
            pass


class RoutedTransportation(Transportation):
    def __init__(self, method:str, speed:float, color:tuple, max_passenger:int, capacity_ratio:float, suggested_passenger:int, external_passenger:int, current_node:Node, route:Route):
        super().__init__(method=method, speed=speed, color=color, current_node=current_node)
        self.route = route
        self.max_passenger = max_passenger
        self.suggested_passenger = suggested_passenger
        self.external_passenger = external_passenger
        self.capacity_ratio = capacity_ratio
        self.path_index = -1

    def is_full(self) -> bool:
        return (len(self.agents) + self.external_passenger) >= int(self.max_passenger * self.capacity_ratio)

    def occupancy(self) -> float:
        return (len(self.agents) + self.external_passenger) / self.max_passenger
    
    def transport(self, current_time:int):
        next_edge = self.route.next_edge(self.path_index)
        if (not next_edge):
            self.path_index += 1
            manager.emit(current_time + 1, manager.Event(manager.TRANSPORTATION_DESPAWN, self))
            return
        travel_time = congestion.try_enter(self, next_edge, self.current_node, current_time)
        if (travel_time is None):                   # next road is full: wait at this stop and try again
            self.waiting = True
            congestion.retry_later(self, current_time)
            return
        self.waiting = False
        self.path_index += 1
        self.current_edge = next_edge
        self.travel_time = travel_time
        self.start_travel = current_time
        manager.emit(current_time + math.ceil(travel_time), manager.Event(manager.TRANSPORTATION_ARRIVED, self))


def handle_route_events(event:manager.Event, time:int, simulation):
    routes:list[Route] = event.get_objects()
    if (event.type == manager.TRANSPORTATION_SPAWN):
        LOGGER.debug(f"Handling transportation spawn for {len(routes)} routes at time {time}.")
        for route in routes:
            transports = route.generate_transportation(current_time=time)
            for transport in transports:
                for agent in list(transport.current_node.agents):
                    if (agent.state != 'waiting'):
                        transport.current_node.agents.remove(agent)
                        continue

                    current_leg = agent.checkpoints[0]
                    if (current_leg.mode == 'ride' and current_leg.end_node in transport.route.ordered_nodes):
                        current_index = transport.path_index + 1
                        for node in transport.route.ordered_nodes[current_index:]:
                            if (not transport.is_full() and current_leg.end_node == node):
                                agent.ride_transportation(transport, time)
                                break
                transport.transport(time)
            simulation.transportations.extend(transports)
            spawn_interval = route.spawn_time if not simulation.peak_hour else route.peak_spawn
            manager.emit(time + spawn_interval, manager.Event(manager.TRANSPORTATION_SPAWN, route))


def handle_transportation_events(event:manager.Event, time:int, simulation):
    _transportations:list[Transportation] = event.get_objects()
    if (event.type == manager.TRANSPORTATION_ARRIVED):
        LOGGER.debug(f"Handling transportation arrival for {len(event.get_objects())} transportations at time {time}.")
        for transport in _transportations:
            transport.current_node = transport.current_edge.get_adjacent_node(transport.current_node)
            transpo_agents = list(transport.agents)
            for agent in transpo_agents:
                if (agent.state != 'travelling'):
                    transport.agents.remove(agent)
                    agent.transportation = None
                    continue

                if (transport.current_node.id == agent.checkpoints[0].end_node.id):
                    agent.alight_transportation()
                    agent.arrival(time, simulation=simulation)
            
            getting_off_external = int(transport.external_passenger * random.uniform(0.2, 0.5))
            transport.external_passenger -= getting_off_external

            node_agents = list(transport.current_node.agents)
            for agent in node_agents:
                if (agent.state != 'waiting'):
                    transport.current_node.agents.remove(agent)
                    continue

                current_leg = agent.checkpoints[0]
                if (getattr(current_leg.route, 'mode', None) == 'tricycle'):
                    continue                                   # waiting for a tricycle, not this vehicle
                if (current_leg.mode == 'ride' and current_leg.end_node in transport.route.ordered_nodes):
                    current_index = transport.path_index + 1
                    for node in transport.route.ordered_nodes[current_index:]:
                        if (not transport.is_full() and current_leg.end_node == node and not agent.transportation):
                            agent.ride_transportation(transport, time)
                            break
                
            transport.transport(time)
    elif (event.type == manager.PRIVATE_TRANSPORTATION_ARRIVED):
        LOGGER.debug(f"Handling private transportation arrival for {len(event.get_objects())} transportations at time {time}.")
        for transport in _transportations:
            if (not transport.agents):
                manager.emit(time + 1, manager.Event(manager.TRANSPORTATION_DESPAWN, transport))
                continue

            transport.current_node = transport.current_edge.get_adjacent_node(transport.current_node)
            agent = transport.agents[0]
            if (transport.method == 'tricycle'):              # transport/tricycle.py: drop off at the leg's end
                if (transport.path):
                    transport.transport(time)
                else:
                    agent.alight_transportation()
                    agent.arrival(time, simulation=simulation)
                    manager.emit(time + 1, manager.Event(manager.TRANSPORTATION_DESPAWN, transport))
                continue
            if (transport.current_node.id == agent.destination_node.id):
                agent.alight_transportation()
                agent.arrival(time, transport.current_node, simulation)
                manager.emit(time + 1, manager.Event(manager.TRANSPORTATION_DESPAWN, transport))
            else:
                if (transport.path):
                    transport.transport(time)
                else:
                    agent.alight_transportation()
                    agent.arrival(time, agent.destination_node, simulation)
                    manager.emit(time + 1, manager.Event(manager.TRANSPORTATION_DESPAWN, transport))
    elif (event.type == manager.TRANSPORTATION_DESPAWN):
        LOGGER.debug(f"Handling transportation despawn for {len(event.get_objects())} transportations at time {time}.")
        for transport in _transportations:
            congestion.leave(transport)
            if (transport in simulation.transportations):
                simulation.transportations.remove(transport)
    elif (event.type == manager.VEHICLE_ENTER_RETRY):
        for transport in _transportations:
            if (transport in simulation.transportations and transport.waiting):
                transport.transport(time)
