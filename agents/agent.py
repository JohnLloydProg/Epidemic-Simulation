import configuration as config
from typing import Literal
from transport.transportation import Transportation, Route, RoutedTransportation
from transport.checkpoint import Checkpoint, generate_checkpoints
from graphing.graph import Graph, RegionGraph
from graphing.core import Node, Edge
from graphing.mapping import shortest_edge_path
from graphing.mapping import shortest_path
import logging
import random
import math
import manager

LOGGER = logging.getLogger("Agent")

def compute_checkpoint_distance(checkpoint:'Checkpoint', city:'RegionGraph', railway:'Graph') -> float:
    """Computes the real distance traveled for a single checkpoint leg, using the same
    edges the agent actually moves along (never a separate/approximate estimate)."""
    if (checkpoint.start_node == checkpoint.end_node):
        return 0

    if (checkpoint.mode == 'walk'):
        try:
            path = shortest_edge_path(checkpoint.start_node.id, checkpoint.end_node.id, city, railway)
            return sum(edge.distance for edge in path)
        except ValueError:
            LOGGER.warning(f"Could not resolve walk distance from {checkpoint.start_node.id} to {checkpoint.end_node.id}.")
            return 0
    else:  # 'ride'
        route = checkpoint.route
        if (not route):
            return 0
        try:
            start_index = route.ordered_nodes.index(checkpoint.start_node)
            end_index = route.ordered_nodes.index(checkpoint.end_node)
        except ValueError:
            LOGGER.warning(f"Could not locate ride leg {checkpoint.start_node.id} -> {checkpoint.end_node.id} on route {route.id}.")
            return 0
        low, high = min(start_index, end_index), max(start_index, end_index)
        return sum(edge.distance for edge in route.path[low:high])


class Agent:
    id:int = 0
    commuting:bool
    private:str
    arrival_time:int = 0
    boarding_time:int = 0
    checkpoints:list[Checkpoint]
    current_node:Node = None
    transportation:Transportation = None
    full_counter:int = 0
    state:str = 'home'

    def __init__(self, city:RegionGraph, railway:Graph, origin:Node, destination:Node):
        self.origin_node = origin
        self.destination_node = destination
        self.commuting = random.random() < 0.8
        if (not self.commuting):
            self.private = 'car'
        self.city = city
        self.railway = railway
        self.id = Agent.id
        Agent.id += 1

        """Daily tracking metrics (reset each simulation day)"""
        self.daily_trips = 0
        self.daily_distance = 0
        self.daily_rides = {}

    
    def ride_transportation(self, transportation:Transportation, time:int):
        if (isinstance(transportation, RoutedTransportation) and transportation.is_full()):
            return

        self.daily_rides[transportation.method] = self.daily_rides.get(transportation.method, 0) + 1
        self.transportation = transportation
        self.boarding_time = time
        transportation.agents.append(self)
        self.set_state('travelling')
        self.current_node.agents.remove(self)
        self.current_node = None
    
    def alight_transportation(self):
        if (self.transportation):
            self.transportation.agents.remove(self)
            self.transportation = None
    
    def set_state(self, state:Literal['home', 'travelling', 'waiting', 'working', 'consuming']):
        self.state = state
    
    def set_path(self, time:int, simulation=None):
        self.current_node = self.origin_node
        self.current_node.agents.append(self)
        if (self.current_node.id == self.destination_node.id):
            self.arrived_at_destination(time, simulation)
        else:
            path:list[Edge] = shortest_edge_path(self.current_node.id, self.destination_node.id, self.city, self.railway)
            if (not path):
                raise ValueError(f"No path found from node {self.current_node.id} to node {self.destination_node.id}.")

            self.daily_distance += sum(edge.distance for edge in path)

            if (self.current_node not in path[0].nodes or self.destination_node not in path[-1].nodes):
                raise ValueError(f"Invalid path: {[(edge.nodes[0].id, edge.nodes[1].id) for edge in path]} for current node {self.current_node.id} and destination node {self.destination_node.id}.")
            
            transport = Transportation(method='private', speed=500, current_node=self.current_node, path=list(path))
            self.ride_transportation(transport, time)
            self.set_state('travelling')
            transport.transport(time)

    def set_checkpoints(self, routing_cache:dict, routes:list[Route], time:int, simulation=None):
        self.current_node = self.origin_node
        self.current_node.agents.append(self)
        if (self.current_node.id == self.destination_node.id):
            self.arrived_at_destination(time, simulation)
        else:
            key = (self.current_node.id, self.destination_node.id)
            cached_checkpoint = routing_cache.get(key, [])
            if (cached_checkpoint):
                self.checkpoints = list(cached_checkpoint)
            else:
                raw_path = shortest_path(self.current_node, self.destination_node, routes)
                if (not raw_path):
                    raise ValueError(f"Can't find path between {self.current_node.id} and {self.destination_node.id}")
                routing_cache[key] = generate_checkpoints(raw_path)
                self.checkpoints = list(routing_cache[key])

            self.daily_distance += sum(compute_checkpoint_distance(checkpoint, self.city, self.railway) for checkpoint in self.checkpoints)
            self.set_state('travelling')
            self.move(time)

            

    def arrival(self, time:int, current_node:Node=None, simulation=None):
        if (self.commuting and self.state == 'travelling'):
            finished_checkpoint = self.checkpoints.pop(0)
            self.current_node = finished_checkpoint.end_node
            self.current_node.agents.append(self)
            if (self.checkpoints):
                self.move(time)
        elif (current_node):
            self.current_node = current_node
            self.current_node.agents.append(self)
        
        if (self.current_node == self.destination_node):
            self.arrived_at_destination(time, simulation)

    def arrived_at_destination(self, time:int, simulation=None):
        self.daily_trips += 1

        self.arrival_time = time
        self.current_node.agents.remove(self)
        self.current_node = None
    
    def move(self, time:int):
        if (not self.checkpoints):
            return

        current_checkpoint = self.checkpoints[0]

        if (current_checkpoint.mode == 'walk'):
            self.daily_rides['walking'] = self.daily_rides.get('walking', 0) + 1

            self.current_node.agents.remove(self)
            self.current_node = None
            
            if (current_checkpoint.start_node == current_checkpoint.end_node):
                walking_time = 5
            else:
                total_distance = sum(edge.distance for edge in shortest_edge_path(current_checkpoint.start_node.id, current_checkpoint.end_node.id, self.city, self.railway))
                walking_time = math.ceil(total_distance / 5)  # Assuming walking speed is 1 unit per time
            self.set_state('travelling')
            manager.emit(time + walking_time + config.get("TIME_STEP", 2), manager.Event(manager.AGENT_ARRIVAL, self))
        elif (current_checkpoint.mode == 'ride'):
            self.set_state('waiting')


def handle_agent_events(event:manager.Event, time:int, simulation):
    agents:list[Agent] = event.get_objects()
    if (event.type == manager.AGENT_ARRIVAL):
        LOGGER.debug(f"Handling agent arrival for {len(agents)} agents at time {time}.")
        for agent in agents:
            agent.arrival(time, simulation=simulation)
    elif (event.type == manager.AGENT_SPAWN):
        pass
