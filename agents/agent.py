import configuration as config
from typing import Literal
from transport.transportation import Transportation, Route, RoutedTransportation
from transport.checkpoint import Checkpoint, generate_checkpoints
from graphing.graph import Graph, RegionGraph
from graphing.core import Node, Edge
from graphing.mapping import shortest_edge_path, shortest_drive_path
from graphing.mapping import shortest_path
import logging
import random
import math
import manager
import pygame as pg

LOGGER = logging.getLogger("Agent")

class Agent:
    id:int = 0
    commuting:bool
    private:str
    arrival_time:int = 0
    boarding_time:int = 0
    checkpoints:list[Checkpoint]
    current_node:Node = None
    transportation:Transportation = None
    path:list[Edge]
    current_edge:Edge = None
    full_counter:int = 0
    state:str = 'home'
    origin_zone:str = None          # set for agents spawned from the OD matrix
    destination_zone:str = None
    trip_kind:str = None            # 'internal', 'inbound' or 'outbound'

    def __init__(self, city:RegionGraph, railway:Graph, origin:Node, destination:Node):
        self.origin_node = origin
        self.destination_node = destination
        self.commuting = random.random() < 0.8
        if (not self.commuting):
            self.private = 'car'
        self.city = city
        self.railway = railway
        self.path = []
        self.id = Agent.id
        Agent.id += 1

        self.modes_used = set()         # modes this trip used (metrics.py: main mode of the trip)
        self.spawn_time = None          # set by metrics.trip_started for OD agents

    
    def ride_transportation(self, transportation:Transportation, time:int):
        if (isinstance(transportation, RoutedTransportation) and transportation.is_full()):
            return

        self.modes_used.add(transportation.method)
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
            self.current_node.agents.remove(self)
            self.current_node = None
            simulation.agents.remove(self)
            simulation.metrics.trip_completed(self, time)
        else:
            # private cars follow one-way roads; if no legal drive exists, the agent takes public transport instead
            path:list[Edge] = list(shortest_drive_path(self.current_node.id, self.destination_node.id, self.city))
            if (not path):
                self.current_node.agents.remove(self)
                self.commuting = True
                self.set_checkpoints(simulation.routing_table, simulation.routes, time, simulation)
                return

            if (self.current_node not in path[0].nodes or self.destination_node not in path[-1].nodes):
                raise ValueError(f"Invalid path: {[(edge.nodes[0].id, edge.nodes[1].id) for edge in path]} for current node {self.current_node.id} and destination node {self.destination_node.id}.")
            
            transport = Transportation(method='private', speed=7, color=(255, 255, 0), current_node=self.current_node, path=list(path))
            self.ride_transportation(transport, time)
            self.set_state('travelling')
            transport.transport(time)
            simulation.transportations.append(transport)

    def set_checkpoints(self, routing_cache:dict, routes:list[Route], time:int, simulation=None):
        self.current_node = self.origin_node
        self.current_node.agents.append(self)
        if (self.current_node.id == self.destination_node.id):
            self.current_node.agents.remove(self)
            self.current_node = None
            simulation.agents.remove(self)
            simulation.metrics.trip_completed(self, time)
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

            self.set_state('travelling')
            self.move(time, simulation)

            

    def arrival(self, time:int, current_node:Node=None, simulation=None):
        self.current_edge = None
        if (self.commuting and self.state == 'travelling'):
            finished_checkpoint = self.checkpoints.pop(0)
            self.current_node = finished_checkpoint.end_node
            self.current_node.agents.append(self)
            if (self.checkpoints):
                self.move(time, simulation)
        elif (current_node):
            self.current_node = current_node
            self.current_node.agents.append(self)
        
        if (self.current_node == self.destination_node):
            self.current_node.agents.remove(self)
            self.current_node = None
            simulation.agents.remove(self)
            simulation.metrics.trip_completed(self, time)
    
    def move(self, time:int, simulation=None):
        if (not self.checkpoints):
            return

        current_checkpoint = self.checkpoints[0]

        if (current_checkpoint.mode == 'walk'):
            self.modes_used.add('walking')
            
            if (current_checkpoint.start_node == current_checkpoint.end_node or current_checkpoint.start_node.id[0] != current_checkpoint.end_node.id[0]):
                walking_time = 40
                manager.emit(time + walking_time, manager.Event(manager.AGENT_ARRIVAL, self))
            else:
                self.path = list(shortest_edge_path(current_checkpoint.start_node.id, current_checkpoint.end_node.id, self.city, self.railway))
                if (not self.path):
                    simulation.agents.remove(self)
                    simulation.metrics.trip_failed(self)
                    return
                self.walk(time)
            self.set_state('travelling')
            
        elif (current_checkpoint.mode == 'ride'):
            self.set_state('waiting')
            if (getattr(current_checkpoint.route, 'mode', None) == 'tricycle'):     # hail a tricycle
                manager.emit(time + int(config.get('TRICYCLE_WAIT_S', 120)), manager.Event(manager.TRICYCLE_PICKUP, self))

    def update_position(self, time:int):
        if (not self.current_edge):
            return self.current_node.pos
    
        travel_time = math.ceil(self.current_edge.distance / 2)  # Assuming walking speed is 1 unit per time
        time_elapsed = time - self.start_time
        if (time_elapsed >= travel_time):
            return self.current_edge.get_adjacent_node(self.current_node).pos
        else:
            start_pos = self.current_node.pos
            end_pos = self.current_edge.get_adjacent_node(self.current_node).pos
            progress_ratio = time_elapsed / travel_time
            new_x = start_pos[0] + (end_pos[0] - start_pos[0]) * progress_ratio
            new_y = start_pos[1] + (end_pos[1] - start_pos[1]) * progress_ratio
            return (new_x, new_y)

    def walk(self, time:int):
        if (self.current_edge):
            self.current_node = self.current_edge.get_adjacent_node(self.current_node)
        self.current_edge = self.path.pop(0)
        walking_time = math.ceil(self.current_edge.distance / 2)  # Assuming walking speed is 1 unit per time
        if (not self.path):
            event = manager.Event(manager.AGENT_ARRIVAL, self)
        else:
            event = manager.Event(manager.AGENT_WALK, self)
        self.start_time = time
        manager.emit(time + walking_time, event)
    
    def draw(self, window, camera, time:int):
        if (self.state == 'travelling' and self.current_edge and self.path):
            pos = self.update_position(time)
            pg.draw.circle(window, (200, 200, 200), camera.to_screen(pos), 5)


def handle_agent_events(event:manager.Event, time:int, simulation):
    agents:list[Agent] = event.get_objects()
    if (event.type == manager.AGENT_ARRIVAL):
        LOGGER.debug(f"Handling agent arrival for {len(agents)} agents at time {time}.")
        for agent in agents:
            agent.arrival(time, simulation=simulation)
    elif (event.type == manager.AGENT_WALK):
        LOGGER.debug(f"Handling agent walk for {len(agents)} agents at time {time}.")
        for agent in agents:
            agent.walk(time)
    elif (event.type == manager.AGENT_SPAWN):
        spawn_agents(agents, time, simulation)
    elif (event.type == manager.TRICYCLE_PICKUP):
        from transport.tricycle import start_ride
        for agent in agents:
            start_ride(agent, time, simulation)


def spawn_agents(specs:list, time:int, simulation):
    """Creates agents from OD trip specs (agents/od_demand.py) and starts their trips."""
    for spec in specs:
        agent = Agent(simulation.graph, simulation.railway_graph, spec.origin, spec.destination)
        agent.origin_zone = spec.origin_zone
        agent.destination_zone = spec.destination_zone
        agent.trip_kind = spec.kind
        simulation.agents.append(agent)
        simulation.metrics.trip_started(agent, time)
        try:
            if (agent.commuting):
                agent.set_checkpoints(simulation.routing_table, simulation.routes, time, simulation)
            else:
                agent.set_path(time, simulation)
        except ValueError as error:
            while (agent in spec.origin.agents):
                spec.origin.agents.remove(agent)
            if (agent in simulation.agents):
                simulation.agents.remove(agent)
            simulation.od_failed += 1
            simulation.metrics.spawn_failed(agent)
            LOGGER.warning(f"Agent {agent.id} ({spec.origin_zone} -> {spec.destination_zone}) not spawned: {error}")
            continue
        simulation.od_spawned += 1
