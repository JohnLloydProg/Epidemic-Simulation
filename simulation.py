from objects import InitialParameters, Status
from const import ENHANCED_CQ, MODIFIED_ENHANCED_CQ, GENERAL_CQ, MODIFIED_GENERAL_CQ
from multiprocessing import Process
from graphing.mapping import load_graph
from graphing.graph import RegionGraph
from agents.agent import AGE_RANGE_DISTRIBUTION, Agent, WorkingAgent, next_occurrence_of_hour, handle_agent_events
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

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

LOGGER = logging.getLogger('Simulation')

def daily_work(agents:list[WorkingAgent], time:int) -> set[int]:
    will_work = set()
    for agent in agents:
        if (agent.SEIR_compartment == 'D' or (agent.SEIR_compartment == 'I' and agent.symptomatic)):
            continue
        agent.clocked_in = False
        agent.finished_work = False
        work_event = manager.Event(manager.AGENT_GO_WORK, agent)
        manager.emit(next_occurrence_of_hour(time, agent.working_hours[0] - random.gauss(1, 0.5)), work_event)
        will_work.add(agent.id)
    return will_work

def generate_status(agents:list[Agent], time:int, active_cases:list[tuple[int, int]]) -> Status:
    seir = {compartment:0 for compartment in Simulation.compartments}
    for agent in agents:
        seir[agent.SEIR_compartment] += 1
    status = Status(time, seir, active_cases)
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
    working_agents:list[WorkingAgent]
    non_working_agents:list[Agent]
    transportations:list[Transportation]
    graph:RegionGraph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    routing_table:dict[tuple, list]
    active_cases:list[tuple[int, int]]
    simulation_multiplier = 25
    simulation_ns_per_time_unit = (10**9)//simulation_multiplier 
    quarantine = None
    peak_hour:bool = False

    def __init__(self, initial_parameters:InitialParameters, headless=True, collection_id="Simulation_Data"):
        logging.basicConfig(handlers=[logging.FileHandler("logfile.txt", 'w'), logging.StreamHandler(sys.stdout)], 
                            level=logging.DEBUG if os.environ.get('DEBUG', 'False') == 'True' else logging.INFO)
        LOGGER.info(f'Initializing simulation with headless = {headless}...')
        manager.init()
        
        """Initialize simulation parameters"""
        self.initial_parameters = initial_parameters
        self.time_step = int(os.environ.get('TIME_STEP', '2'))
        self.agents = []
        self.working_agents = []
        self.non_working_agents = []
        self.transportations = []
        self.headless = headless
        self.active_cases = []
        self.collection_id = collection_id 

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
                age_range = random.choices(list(AGE_RANGE_DISTRIBUTION.keys()), weights=list(AGE_RANGE_DISTRIBUTION.values()))[0]
                age = random.randint(age_range[0], age_range[1])
                if (random.random() < 0.947 and age >= 23 and age <= 65):
                    work_range = random.choices([(8, 17), (20, 5), (15, 23), (10, 19), (13, 22)], weights=[0.6, 0.075, 0.075, 0.125, 0.125])[0]
                    agent = WorkingAgent(age, self.graph, self.railway_graph, household, work_range)
                    self.working_agents.append(agent)
                else:
                    agent = Agent(age, self.graph, self.railway_graph, household)
                    self.non_working_agents.append(agent)
                household.resident_agents.append(agent)
                self.agents.append(agent)
        
        """Assign firms to agents"""
        LOGGER.info('assigning firms to agents...')
        firms = self.graph.get_firms()
        for agent in self.working_agents:
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

        last_logged_day = None 

        def log_data_to_firestore(day, seir_data, occupancies_data, travelling_data):
            try:
                doc_id = str(day) 
                doc_ref = db.collection(self.collection_id).document(doc_id)
                total_population = sum(seir_data.values())
                doc_ref.set({
                    **seir_data,
                    "Total": total_population,
                    "Vehicle_Occupancy": occupancies_data,
                    "Travelling_Agents": travelling_data
                }, merge=True)
            except Exception as e:
                print(f"Firestore Sync Error: {e}")

        daily_hourly_occupancies = {}
        daily_hourly_travelling = {}
        last_sampled_hour = None
        
        LOGGER.info('Starting simulation...')
        while ((time // (60 * 24) < self.initial_parameters.duration) and running):
            minute = time % 60
            hour = (time // 60) % 24
            day = time // (60 * 24)
            time_record = time_ns()
            self.peak_hour = (8 >= hour >= 6) or (20 >= hour >= 17)
            
            # --- HOURLY SNAPSHOT ---
            if minute == 58 and last_sampled_hour != hour:
                last_sampled_hour = hour
                
                # Vehicle Occupancy Tracker
                occupancy_lists = {}
                for transpo in self.transportations:
                    v_type = transpo.method
                    if v_type not in occupancy_lists:
                        occupancy_lists[v_type] = []
                    occupancy_lists[v_type].append(transpo.occupancy())
                
                hour_avg = {}
                for v_type, occ_list in occupancy_lists.items():
                    hour_avg[v_type] = round(sum(occ_list) / len(occ_list), 2) if len(occ_list) > 0 else 0
                    
                daily_hourly_occupancies[f"{hour:02d}:00"] = hour_avg
                
                # Travelling Agent Tracker
                current_states = get_agent_states(self.agents)
                daily_hourly_travelling[f"{hour:02d}:00"] = current_states.get('travelling', 0)
            
            # --- FIRESTORE LOGGING ---
            if hour == 23 and minute == 58 and last_logged_day != str(day):
                last_logged_day = str(day)
                actual_log_time = (day * 24 * 60) + (hour * 60) + minute 
                current_status = generate_status(self.agents, actual_log_time, self.active_cases)
                
                log_data_to_firestore(day, current_status.SEIR_compartments, daily_hourly_occupancies, daily_hourly_travelling)
                print(f"\nLogged Day {day} to Firestore with Hourly Occupancies and Travel Data.")
                
                # Reset for the next day
                daily_hourly_occupancies = {}
                daily_hourly_travelling = {}

            # --- DAILY ROUTINE ---
            if (hour == 0 and minute == 0):
                status = generate_status(self.agents, time, self.active_cases)
                self.active_cases.append((time, status.SEIR_compartments['I']))
                day_delta = round((time_ns() - simulation_day_time) / (10**9), 2)
                
                LOGGER.info(f"Day {day}/{self.initial_parameters.duration} completed in {day_delta} seconds.")
                simulation_day_time = time_ns()

                """Update quarantine measures if specified"""
                if (day in self.initial_parameters.quarantine_schedule):
                    self.quarantine = self.initial_parameters.quarantine_schedule[day]
                    LOGGER.info(f"Quarantine measure {self.quarantine} activated for day {day}.")

                for agent in self.non_working_agents:
                    if (random.random() < 0.3 and agent.age <= 65 and agent.age >= 4 and agent.SEIR_compartment != 'D'):
                        manager.emit(next_occurrence_of_hour(time, random.randrange(10, 15)), manager.Event(manager.AGENT_GO_SHOPPING, agent))
                
                will_work:set[int] = set()
                if (not self.quarantine):
                    will_work.update(daily_work(self.working_agents, time))

                elif (self.quarantine in {ENHANCED_CQ, MODIFIED_ENHANCED_CQ}):
                    # implement covid tests
                    for firm in self.graph.get_firms():
                        if (firm.essential):
                            will_work.update(daily_work(firm.resident_agents, time))
                        elif (self.quarantine == MODIFIED_ENHANCED_CQ):
                            max_capacity = int(0.5 * firm.max_capacity)
                            agents = random.sample(firm.resident_agents, min(len(firm.resident_agents), max_capacity))
                            will_work.update(daily_work(agents, time))

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
                    quarantine_text = self.font.render(f"Quarantine: {self.quarantine if self.quarantine else 'None'}", False, (0, 0, 0))
                    
                    state_text = ''
                    for state in ['home', 'travelling', 'waiting', 'working', 'consuming']:
                        state_text += f'{state}: {states.get(state, 0)}, '
                    states_text = self.font.render(f"States: {state_text}", False, (0, 0, 0))
                    
                    travel_text = self.font.render(f"Travel modes: {travel_modes}", False, (0, 0, 0))
                    occupancies = {}
                    for transpo in self.transportations:
                        if (transpo.method in occupancies):
                            occupancies[transpo.method] = round((occupancies[transpo.method] + transpo.occupancy()) / 2, 2)
                        else:
                            occupancies[transpo.method] = transpo.occupancy()
                            
                    metric_text = self.font.render(f"Transportation: {len(self.transportations)}, avg. occupancy: {occupancies}", False, (0, 0, 0))
                    
                    self.window.blit(states_text, states_text.get_rect(topleft=(20, 40)))
                    self.window.blit(travel_text, travel_text.get_rect(topleft=(20, 60)))
                    pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                    self.window.blit(metric_text, metric_text.get_rect(topleft=(20, 20)))
                    self.window.blit(text, text.get_rect(topright=(1060, 20)))
                    self.window.blit(quarantine_text, quarantine_text.get_rect(topright=(1060, 40)))

                    pg.display.update()

            else:
                self.handle_events(time)
                time += self.time_step
    

if __name__ == '__main__':
    cred = credentials.Certificate('epidemicsimulation-firebase-adminsdk-fbsvc-81103feabb.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    load_dotenv()
    print(f"Simulation Start: {datetime.now().isoformat()}")
    Simulation(InitialParameters(365, {'I':2000}, {2: ENHANCED_CQ}), False, "Base Sim 365")
    print(f"Simulation End: {datetime.now().isoformat()}")