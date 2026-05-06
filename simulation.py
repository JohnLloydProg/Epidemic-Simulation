from objects import InitialParameters, Status
from const import ENHANCED_CQ, MODIFIED_ENHANCED_CQ, GENERAL_CQ, MODIFIED_GENERAL_CQ
from multiprocessing import Process
from graphing.mapping import load_graph
from graphing.graph import RegionGraph
from agents.agent import Agent, WorkingAgent, next_occurrence_of_hour, handle_agent_events
from transport.transportation import Transportation, handle_route_events, handle_transportation_events
from routing_table import build_routing_cache
from time import time_ns
from datetime import datetime
from dotenv import load_dotenv
import manager
import random
import pygame as pg
import logging
import math
import os
import sys

LOGGER = logging.getLogger('Simulation')

def printProgressBar (iteration, total, prefix = '', suffix = '', decimals = 1, length = 100, fill = '█', printEnd = "\r"):
    """
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        length      - Optional  : character length of bar (Int)
        fill        - Optional  : bar fill character (Str)
        printEnd    - Optional  : end character (e.g. "\r", "\r\n") (Str)
    """
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    print(f'\r{prefix} |{bar}| {percent}% {suffix}', end = printEnd)
    if iteration == total: 
        print()

def daily_work(agents:list[WorkingAgent], time:int) -> set[int]:
    will_work = set()
    for agent in agents:
        if (agent.SEIR_compartment == 'D' or (agent.SEIR_compartment == 'I' and agent.symptomatic)):
            continue
        work_event = manager.Event(manager.AGENT_GO_WORK, agent)
        manager.emit(next_occurrence_of_hour(time, agent.working_hours[0] - 1.5), work_event)
        will_work.add(agent.id)
    return will_work

def generate_status(agents:list[Agent], time:int) -> Status:
    seir = {compartment:0 for compartment in Simulation.compartments}
    for agent in agents:
        seir[agent.SEIR_compartment] += 1
    status = Status(time, seir)
    return status

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


class Simulation:
    compartments = ['S', 'E', 'I', 'R', 'D']
    layer = 'city'
    agents:list[Agent]
    transportations:list[Transportation]
    graph:RegionGraph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    routing_table:dict[tuple, list]
    simulation_multiplier = 5
    simulation_ns_per_time_unit = (10**9)//simulation_multiplier # 1/<number of minutes in simulation per 1 second in real time>
    quarantine = None
    peak_hour:bool = False

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        """Initialized logging and event manager"""
        logging.basicConfig(handlers=[logging.FileHandler("logfile.txt", 'w'), logging.StreamHandler(sys.stdout)], level=logging.INFO if os.environ.get('DEBUG', 'False') == 'True' else logging.DEBUG)
        LOGGER.info(f'Initializing simulation with headless = {headless}...')
        manager.init()
        
        """Initialize simulation parameters"""
        self.initial_parameters = initial_parameters
        self.time_step = int(os.environ.get('TIME_STEP', '2'))
        self.agents = []
        self.transportations = []
        self.headless = headless

        """Load environment and initialize route spawning events"""
        environment = load_graph()
        self.graph = environment[0]
        self.railway_graph = environment[1]
        self.routes = environment[2]
        for route in self.routes:
            manager.emit(3, manager.Event(manager.TRANSPORTATION_SPAWN, route))

        """Build routing cache for agents"""
        establishment = self.graph.get_firms()
        establishment.extend(self.graph.get_households())
        self.routing_table = build_routing_cache(establishment, self.graph, self.railway_graph, self.routes)

        """Generate agents"""
        self.generate_agents()
        LOGGER.info(f'Simulation initialized with {len(self.agents)} agents.')
        
        """Mainly for visualization purposes"""
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 15)
        
        self.run()
    
    def generate_agents(self):
        """Generate agents based on households"""
        LOGGER.info('generating agents...')
        for household in self.graph.get_households():
            for _ in range(household.resident_count):
                agent = WorkingAgent(self.graph, self.railway_graph, household, (8, 17))
                household.resident_agents.append(agent)
                self.agents.append(agent)
        
        """Assign firms to agents"""
        LOGGER.info('assigning firms to agents...')
        firms = self.graph.get_firms()
        for agent in self.agents:
            firm = random.choice(firms)
            tries = 0
            while (len(firm.resident_agents) >= firm.max_capacity and tries < 4):
                firm = random.choice(firms)
                tries += 1
            agent.firm = firm
            firm.resident_agents.append(agent)
        
        """Firm occupancy ratios"""
        occupany_ratios = []
        for firm in firms:
            occupany_ratios.append((len(firm.resident_agents) / firm.max_capacity) * 100)
        LOGGER.info(f'Firm occupancy ratios: min={min(occupany_ratios)}%, max={max(occupany_ratios)}%, avg={sum(occupany_ratios)/len(occupany_ratios)}%, std={math.sqrt(sum((x - (sum(occupany_ratios)/len(occupany_ratios)))**2 for x in occupany_ratios)/len(occupany_ratios))}%')

        """Assign initial SEIR compartments to agents. Assignment here is done randomly"""
        LOGGER.info('assigning initial infections...')
        assigned = set()
        for compartment in self.compartments:
            un_assigned_agents = list(filter(lambda agent: agent.id not in assigned, self.agents))
            if (len(un_assigned_agents) == 0):
                break
            agents = random.sample(un_assigned_agents, self.initial_parameters.no_per_compartment.get(compartment, 0))
            for agent in agents:
                agent.SEIR_compartment = compartment
                
                if (compartment == 'I'):
                    agent.symptomatic = random.random() < 0.6
                    remove_event = manager.Event(manager.AGENT_REMOVED, agent)
                    manager.emit(math.ceil(self.initial_parameters.sample_infected_duration()), remove_event)
                elif (compartment == 'E'):
                    infection_event = manager.Event(manager.AGENT_INFECTED, agent)
                    manager.emit(math.ceil(self.initial_parameters.sample_incubation_period()), infection_event)
                
                assigned.add(agent.id)

    def handle_events(self, time:int):
        for event in manager.get(time):
            handle_agent_events(event, self.routing_table, self.initial_parameters, time)
            handle_transportation_events(event, self.transportations, self.initial_parameters, time)
            handle_route_events(event, self.transportations, self.peak_hour, time)        
    
    def run(self):
        time = 0
        delta = 0
        draw_time = 0
        simultation_time = 0
        status = None
        simulation_day_time = time_ns()
        running = True
        states = get_agent_states(self.agents)

        printProgressBar(0, 365, prefix = 'Progress:', suffix = 'Complete', length = 50)
        while ((time // (60 * 24) < self.initial_parameters.duration) and running):
            minute = time % 60
            hour = (time // 60) % 24
            day = time // (60 * 24)
            time_record = time_ns()
            self.peak_hour = (8 >= hour >= 6) or (20 >= hour >= 17)

            """Daily routines and quarantine measures. Here daily status generation occurs"""
            if (hour == 0 and minute == 0):
                status = generate_status(self.agents, time)
                day_delta = (time_ns() - simulation_day_time) / (10**9)
                printProgressBar(day, 365, prefix = 'Progress:', suffix = f'Complete {day_delta} seconds per Simulation Day', length = 50)
                simulation_day_time = time_ns()
                
                will_work:set[int] = set()
                if (not self.quarantine):
                    will_work.update(daily_work(self.agents, time))

                elif (self.quarantine in {ENHANCED_CQ, MODIFIED_ENHANCED_CQ}):
                    # implement covid tests
                    for firm in self.graph.get_firms():
                        if (firm.essential):
                            will_work.update(daily_work(firm.resident_agents, time))
                        else:
                            if (self.quarantine == MODIFIED_ENHANCED_CQ):
                                max_capacity = int(0.5 * firm.max_capacity)
                                if (len(firm.resident_agents) < max_capacity):
                                    agents = firm.resident_agents
                                else:
                                    agents = random.sample(firm.resident_agents, max_capacity)
                                will_work.update(daily_work(agents, time))

                """Errand runs for agents"""
                if (self.quarantine in {ENHANCED_CQ, MODIFIED_ENHANCED_CQ}):
                    compartments_not_allowed = {'D', 'I'}
                    for household in self.graph.get_households():
                        if (random.random() < 0.3):
                            agents = list(filter(lambda agent: (agent.SEIR_compartment not in compartments_not_allowed or (agent.SEIR_compartment == 'I' and not agent.symptomatic)), household.resident_agents))
                            if (not agents):
                                continue
                            agent:WorkingAgent = random.choice(agents)
                            if (agent.id in will_work):
                                agent.errand_run = True
                            else:
                                suply_run = manager.Event(manager.AGENT_GO_SHOPPING, agent)
                                manager.emit(time + (random.randrange(10, 21) * 60), suply_run)

            if (not self.headless):
                """Pygame event handling"""
                for event in pg.event.get():
                    if (event.type == pg.QUIT):
                        running = False
                        return
                    elif (event.type == pg.KEYDOWN):
                        if (event.key == pg.K_p and status):
                            Process(None, status.display_report).start()
                        elif (event.key == pg.K_UP and self.simulation_multiplier < 30):
                            self.simulation_multiplier += 1
                            self.simulation_ns_per_time_unit = (10**9)//self.simulation_multiplier
                        elif (event.key == pg.K_DOWN and self.simulation_multiplier > 1):
                            self.simulation_multiplier -= 1
                            self.simulation_ns_per_time_unit = (10**9)//self.simulation_multiplier
                            
                    self.graph.map_dragging(event)

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
                    text = self.font.render(f"time: {time} (Day {day} {hour}:{minute}) {self.simulation_multiplier}x {round(delta, 2)}ms per step {len(manager._events.values())} events", False, (0, 0, 0))
                    state_text = ''
                    for state in ['home', 'travelling', 'waiting', 'working', 'consuming']:
                        state_text += f'{state}: {states.get(state, 0)}, '
                    states_text = self.font.render(f"States: {state_text}", False, (0, 0, 0))
                    travel_text = self.font.render(f"Travel modes: {travel_modes}", False, (0, 0, 0))
                    self.window.blit(states_text, states_text.get_rect(topleft=(20, 40)))
                    self.window.blit(travel_text, travel_text.get_rect(topleft=(20, 60)))
                    occupancies = {}
                    for transpo in self.transportations:
                        if (transpo.method in occupancies):
                            occupancies[transpo.method] = round((occupancies[transpo.method] + transpo.occupancy()) / 2, 2)
                        else:
                            occupancies[transpo.method] = transpo.occupancy()
                    metric_text = self.font.render(f"Transportation: {len(self.transportations)}, avg. occupancy: {occupancies}", False, (0, 0, 0))
                    pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                    self.window.blit(metric_text, metric_text.get_rect(topleft=(20, 20)))
                    self.window.blit(text, text.get_rect(topright=(1060, 20)))
                    pg.display.update()
            else:
                self.handle_events(time)
                time += self.time_step
    

if __name__ == '__main__':
    load_dotenv()
    print(datetime.now().isoformat())
    Simulation(InitialParameters(365, {'I':2000}), False)
    print(datetime.now().isoformat())
