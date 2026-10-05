from dotenv import load_dotenv
load_dotenv()
import configuration as config
from graphing.mapping import load_graph
from graphing.graph import RegionGraph
from agents.agent import Agent, handle_agent_events
from transport.transportation import Transportation, RoutedTransportation, handle_route_events, handle_transportation_events, BusRoute, JeepRoute, TrainRoute
from routing_table import build_routing_cache
from time import time_ns
from datetime import datetime
import manager
import random
import pygame as pg
import logging
import math
import os
import sys
import uuid
import json

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

LOGGER = logging.getLogger('Simulation')


def get_agent_states(agents:list[Agent]) -> dict[str, int]:
    states = {}
    for agent in agents:
        states[agent.state] = states.get(agent.state, 0) + 1
    return states

def get_travelling_mode(agents:list[Agent]) -> dict[str, int]:
    travel_modes = {}
    for agent in agents:
        if (agent.state != 'travelling'):
            continue
        
        if (agent.transportation):
            travel_modes[agent.transportation.method] = travel_modes.get(agent.transportation.method, 0) + 1
        else:
            travel_modes['walking'] = travel_modes.get('walking', 0) + 1
    return travel_modes

def get_transport_count(transportations:list[RoutedTransportation]):
    transport_types = {}
    for transport in transportations:
        transport_types[transport.method] = transport_types.get(transport.method, 0) + 1
    return transport_types


class Simulation:
    layer = 'city'
    agents:list[Agent]
    transportations:list[Transportation]
    graph:RegionGraph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    routing_table:dict[tuple, list]
    simulation_multiplier = 5
    simulation_ns_per_time_unit = (10**9)//simulation_multiplier
    step_counter = 0

    def __init__(self):
        logging.basicConfig(handlers=[logging.FileHandler("logfile.txt", 'w'), logging.StreamHandler(sys.stdout)], 
                            level=logging.DEBUG if os.environ.get('DEBUG', 'False') == 'True' else logging.INFO)
        config.init()
        manager.init()
        
        """Initialize simulation parameters"""
        self.time_step = config.get('TIME_STEP', 2)
        self.agents = []
        self.transportations = []
        self.active_cases = []

        """Load environment and initialize route spawning events"""
        environment = load_graph()
        self.graph = environment[0]
        self.railway_graph = environment[1]
        self.routes = environment[2]
        for route in self.routes:
            manager.emit(3, manager.Event(manager.TRANSPORTATION_SPAWN, route))

        """Build routing cache for agents"""
        nodes = list(self.graph.nodes.values())
        self.routing_table = build_routing_cache(nodes, self.graph, self.railway_graph, self.routes)

        LOGGER.info(f'Simulation initialized with {len(self.agents)} agents.')
        
        """Mainly for visualization purposes"""
        pg.init()
        self.clock = pg.time.Clock()
        self.window = pg.display.set_mode((1080, 720))
        self.font = pg.font.Font(None, 15)
        self.railway_graph.camera = self.graph.camera   # one shared view for both layers
        all_nodes = list(self.graph.nodes.values()) + list(self.railway_graph.nodes.values())
        self.graph.camera.fit([node.pos for node in all_nodes], self.window.get_size())
        
        self.run()

    def handle_events(self, time:int):
        """Event based handling"""
        for event in manager.get(time):
            handle_agent_events(event, time, self)
            handle_transportation_events(event, time, self)
            handle_route_events(event, time, self)
    
    def run(self):
        time = 0
        delta = 0
        draw_time = 0
        simultation_time = 0
        status = None
        simulation_day_time = time_ns()
        running = True
        states = get_agent_states(self.agents)

        LOGGER.info('Starting simulation...')
        while (running):
            second = time % 60
            minute = (time // 60) % 60
            hour = (time // 3600) % 24
            day = time // (3600 * 24)
            time_record = time_ns()
            self.peak_hour = (9 >= hour >= 6) or (20 >= hour >= 17)

            for event in pg.event.get():
                if (event.type == pg.QUIT):
                    running = False
                    return
                elif (event.type == pg.KEYDOWN):
                    if (event.key == pg.K_UP and self.simulation_multiplier < 30):
                        self.simulation_multiplier += 1
                    elif (event.key == pg.K_DOWN and self.simulation_multiplier > 1):
                        self.simulation_multiplier -= 1
                    self.simulation_ns_per_time_unit = (10**9)//self.simulation_multiplier
                elif (event.type == pg.MOUSEBUTTONDOWN and event.button == 3):
                    nodes = list(filter(lambda n: n.edges, self.graph.nodes.values()))
                    agent = Agent(self.graph, self.railway_graph, random.choice(nodes), random.choice(nodes))
                    self.agents.append(agent)
                    if (agent.commuting):
                        agent.set_checkpoints(self.routing_table, self.routes, time, self)
                    else:
                        agent.set_path(time, self)
                    print("Generating an agent")

                self.graph.camera.handle_event(event)

            """Handle events and update agent states"""
            if (time_ns() - simultation_time >= self.simulation_ns_per_time_unit):
                self.handle_events(time)
                states = get_agent_states(self.agents)

                travel_modes = get_travelling_mode(self.agents)
                simultation_time = time_ns()
                delta = (time_ns() - time_record) / (10**6)
                time += self.time_step
            
            """Visualization and metrics. Here the drawing is done."""
            if (time_ns() - draw_time >= (10**9)//60):
                draw_time = time_ns()
                self.window.fill((255, 255, 255))
                self.graph.draw(self.window, self.font,  self.layer)
                
                routes = sorted(self.routes, key=lambda route:route.get_average_occupancy(), reverse=True)
                for route in routes:
                    route.draw(self.window, self.graph)
                
                text = self.font.render(f"time: {time} (Day {day} {str(hour).zfill(2)}:{str(minute).zfill(2)}:{str(second).zfill(2)}) {self.simulation_multiplier}x {round(delta, 2)}ms per step {len(manager._events.values())} events", False, (0, 0, 0))
                
                state_text = ''
                for state in ['home', 'travelling', 'waiting', 'working', 'consuming']:
                    state_text += f'{state}: {states.get(state, 0)}, '
                states_text = self.font.render(f"States: {state_text}", False, (0, 0, 0))
                
                travel_text = self.font.render(f"Travel modes: {travel_modes}", False, (0, 0, 0))
                occupancies:dict[str, list] = {}
                for transpo in self.transportations:
                    if (transpo.method in occupancies):
                        occupancies[transpo.method].append(transpo.occupancy())
                    else:
                        occupancies[transpo.method] = [transpo.occupancy()]
                    if (isinstance(transpo, RoutedTransportation)):
                        transpo.draw(self.window, self.graph.camera, time)
                metric_text = self.font.render(f"Transportation Used: {len(self.transportations)}, avg. occupancy: {[(method, round(max(occupancy), 2))for method, occupancy in occupancies.items()]}", False, (0, 0, 0))
                available_transports = self.font.render(f"Live Transportation: {get_transport_count(self.transportations)}", False, (0, 0, 0))
                
                self.window.blit(states_text, states_text.get_rect(topleft=(20, 40)))
                self.window.blit(travel_text, travel_text.get_rect(topleft=(20, 60)))
                self.window.blit(available_transports, available_transports.get_rect(topleft=(20, 80)))
                pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                self.window.blit(metric_text, metric_text.get_rect(topleft=(20, 20)))
                self.window.blit(text, text.get_rect(topright=(1060, 20)))

                pg.display.update()
    

if __name__ == '__main__':
    LOGGER.info(f"Simulation Start: {datetime.now().isoformat()}")
    Simulation()
    LOGGER.info(f"Simulation End: {datetime.now().isoformat()}")