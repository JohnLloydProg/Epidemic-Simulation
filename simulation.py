from objects import Disease, Status
from const import ENHANCED_CQ, MODIFIED_ENHANCED_CQ, GENERAL_CQ, MODIFIED_GENERAL_CQ
from multiprocessing import Process
from graphing.mapping import load_graph
from graphing.graph import RegionGraph
from agents.agent import AGE_RANGE_DISTRIBUTION, Agent, WorkingAgent, next_occurrence_of_hour, handle_agent_events
from transport.transportation import Transportation, RoutedTransportation, handle_route_events, handle_transportation_events, BusRoute, JeepRoute, TrainRoute
from interventions import handle_policy_events
from routing_table import build_routing_cache
from time import time_ns
from datetime import datetime
from dotenv import load_dotenv
import interventions
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

def daily_work(agents:list[WorkingAgent], quarantine:bool, curfew:dict, time:int, config:dict) -> set[int]:
    will_work = set()
    for agent in agents:
        isolate = (agent.isolate and (random.random() < config.get('AGENT_COMPLIANCE', 0.5) or quarantine))
        dead = agent.SEIR_compartment == 'D'
        out_curfew = agent.working_hours[0] < curfew.get('start_hour', -1) or agent.working_hours[1] > curfew.get('end_hour', 24) or agent.working_hours[0] > curfew.get('end_hour', 24) or agent.working_hours[1] < curfew.get('start_hour', -1)
        if (dead or isolate or out_curfew):
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

def get_transport_count(transportations:list[RoutedTransportation]):
    transport_types = {}
    for transport in transportations:
        transport_types[transport.method] = transport_types.get(transport.method, 0) + 1
    return transport_types


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
    designated_persons:list[Agent]
    no_per_compartment:dict
    simulation_multiplier = 25
    simulation_ns_per_time_unit = (10**9)//simulation_multiplier
    max_travel_distance = None
    essential_only = False
    quarantine = False
    peak_hour:bool = False
    curfew:dict[str, int] = {}
    step_counter = 0

    def __init__(self, config:dict, headless=True):
        logging.basicConfig(handlers=[logging.FileHandler("logfile.txt", 'w'), logging.StreamHandler(sys.stdout)], 
                            level=logging.DEBUG if os.environ.get('DEBUG', 'False') == 'True' else logging.INFO)
        LOGGER.info(f'Initializing simulation with headless = {headless}...')
        manager.init(config)
        
        """Initialize simulation parameters"""
        self.disease = Disease(config)
        self.time_step = config['TIME_STEP']
        self.duration = config['DURATION']
        self.no_per_compartment = config.get('SEIR_COUNT', {'I':4})
        self.agents = []
        self.working_agents = []
        self.non_working_agents = []
        self.transportations = []
        self.headless = headless
        self.active_cases = []
        self.designated_persons = []
        self.collection_id = config["COLLECTION_ID"]
        self.simulation_id = str(uuid.uuid4())
        self.config = config

        """Load environment and initialize route spawning events"""
        environment = load_graph(config)
        self.graph = environment[0]
        self.railway_graph = environment[1]
        self.routes = environment[2]
        for route in self.routes:
            manager.emit(3, manager.Event(manager.TRANSPORTATION_SPAWN, route))
        
        """Loading planned policies to implement"""
        pickled_policies:list[dict] = config.get('SCHEDULED_POLICIES', [])
        for pickled_policy in pickled_policies:
            policy = self.load_policy(pickled_policy)
            manager.emit(policy.start_time, manager.Event(manager.IMPLEMENT_POLICY, policy))

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
            while (len(firm.resident_agents) >= int(firm.max_capacity * 0.85)):
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
            un_assigned_agents = list(filter(lambda agent: agent.id not in assigned, self.working_agents))
            if (len(un_assigned_agents) == 0):
                break
            agents = random.sample(un_assigned_agents, self.no_per_compartment.get(compartment, 0))
            for agent in agents:
                agent.SEIR_compartment = compartment
                
                if (compartment == 'I'):
                    agent.symptomatic = random.random() < 0.6
                    max_infection_duration = math.ceil(self.disease.sample_infected_duration())
                    duration = random.randint(0, max_infection_duration) if self.config['IS_EPOCH_RESTART'] else max_infection_duration
                    if (agent.symptomatic):
                        if (duration//60 > 48):
                            manager.emit(min(random.randint(24, 48))*60, manager.Event(manager.AGENT_ISOLATE, agent))
                        else:
                            agent.isolate = True
                    remove_event = manager.Event(manager.AGENT_REMOVED, agent)
                    manager.emit(duration, remove_event)
                elif (compartment == 'E'):
                    max_incubation_period = math.ceil(self.disease.sample_incubation_period())
                    duration = random.randint(0, max_incubation_period) if self.config['IS_EPOCH_RESTART'] else max_incubation_period
                    infection_event = manager.Event(manager.AGENT_INFECTED, agent)
                    manager.emit(duration, infection_event)
                assigned.add(agent.id)
    
    def load_policy(self, pickled_policy:dict) -> interventions.Policy:
        policy_type = pickled_policy['type']
        params:dict = pickled_policy['params']

        if ('routes' in params):
            if (params['routes'] == 'bus'):
                params['routes'] = [route for route in self.routes if (isinstance(route, BusRoute))]
            elif (params['routes'] == 'jeep'):
                params['routes'] = [route for route in self.routes if (isinstance(route, JeepRoute))]
            elif (params['routes'] == 'train'):
                params['routes'] = [route for route in self.routes if (isinstance(route, TrainRoute))]
            elif (params['routes'] == 'all'):
                params['routes'] = self.routes
        if ('firms' in params):
            param:str|int = params['firms']
            if (param == 'all'):
                params['firms'] = self.graph.get_firms()
            elif (isinstance(param, int)):
                params['firms'] = [firm for firm in self.graph.get_firms() if (firm.industry[1] == param)]
            else:
                raise ValueError('Passed parameter for firms in config is invalid!')

        _cls = interventions.POLICY_CLASS_MAPPING[policy_type]
        policy = _cls(**params)
        return policy

    def handle_events(self, time:int):
        self.step_counter += 1

        """Tick-based events"""
        if (self.step_counter == 2):
            for transportation in self.transportations:
                for agent in transportation.agents:
                    if (agent.state != 'travelling'):
                        continue

                    agent.check_for_infection(
                        self.disease.sample_infection_transport_CPC(),
                        self.disease.sample_incubation_period(),
                        transportation.get_contact_rate(), 
                        transportation.get_infected_density(),
                        4/10, time
                        )
            self.step_counter = 0

        """Event based handling"""
        for event in manager.get(time):
            handle_agent_events(event, self.routing_table, self.routes, self.max_travel_distance, self.quarantine, self.disease, time)
            handle_transportation_events(event, self.transportations, time)
            handle_route_events(event, self.transportations, self.peak_hour, time, self.config)
            handle_policy_events(self, event, time)
    
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
                doc_ref = db.collection(self.collection_id).document(self.simulation_id)
                total_population = sum(seir_data.values())
                doc_ref.set({str(day): {
                    **seir_data,
                    "Total": total_population,
                    "Vehicle_Occupancy": occupancies_data,
                    "Travelling_Agents": travelling_data
                }}, merge=True)
            except Exception as e:
                print(f"Firestore Sync Error: {e}")

        daily_hourly_occupancies = {}
        daily_hourly_travelling = {}
        last_sampled_hour = None
        
        LOGGER.info('Starting simulation...')
        while ((time // (60 * 24) < self.duration) and running):
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
                self.active_cases.append((day, status.SEIR_compartments['I']))
                day_delta = round((time_ns() - simulation_day_time) / (10**9), 2)
                
                LOGGER.info(f"Day {day}/{self.duration} completed in {day_delta} seconds.")
                simulation_day_time = time_ns()
                
                will_work:set[int] = set()
                for firm in self.graph.get_firms():
                    if (not firm.essential and self.essential_only):
                        continue
                    
                    agents = random.sample(firm.resident_agents, min(len(firm.resident_agents), firm.max_capacity))
                    will_work.update(daily_work(agents, self.quarantine, self.curfew, time, self.config))
                
                _agents = self.designated_persons if self.designated_persons else self.agents
                for agent in _agents:
                    isolate = (agent.isolate and (random.random() < config.get('AGENT_COMPLIANCE', 0.5) or self.quarantine))
                    if (random.random() < 0.3 and 65 >= agent.age >= 4 and agent.SEIR_compartment != 'D' and not isolate):
                        if (isinstance(agent, WorkingAgent) and agent.id in will_work):
                            agent.errand_run = True
                            continue

                        if (self.curfew):
                            hour = random.randrange(self.curfew['start_hour'], self.curfew['end_hour'] - 2)
                        else:
                            hour = random.randrange(10, 15)
                        manager.emit(next_occurrence_of_hour(time, hour), manager.Event(manager.AGENT_GO_SHOPPING, agent))
            
            if (status.SEIR_compartments['I'] == 0):
                running = False

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
                    metric_text = self.font.render(f"Transportation Used: {len(self.transportations)}, avg. occupancy: {[(method, round(max(occupancy), 2))for method, occupancy in occupancies.items()]}", False, (0, 0, 0))
                    available_transports = self.font.render(f"Live Transportation: {get_transport_count(self.transportations)}", False, (0, 0, 0))
                    
                    self.window.blit(states_text, states_text.get_rect(topleft=(20, 40)))
                    self.window.blit(travel_text, travel_text.get_rect(topleft=(20, 60)))
                    self.window.blit(available_transports, available_transports.get_rect(topleft=(20, 80)))
                    pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                    self.window.blit(metric_text, metric_text.get_rect(topleft=(20, 20)))
                    self.window.blit(text, text.get_rect(topright=(1060, 20)))

                    pg.display.update()

            else:
                self.handle_events(time)
                time += self.time_step
    

if __name__ == '__main__':
    load_dotenv()
    cert_path = f'/firebase_cred/{os.environ['CERT_FILE_NAME']}' if (os.environ.get('CLOUD', 'False') == 'True') else os.environ['CERT_FILE_NAME']
    cred = credentials.Certificate(cert_path)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    
    config_path = f'/firebase_cred/{os.environ['CONFIG_FILE_NAME']}' if (os.environ.get('CLOUD', 'False') == 'True') else os.environ['CONFIG_FILE_NAME']
    with open(config_path) as f:
        config = json.load(f)
    
    if (not config):
        print("Configuration file not found! Simulation won't start.")
    else:
        print(f"Simulation Start: {datetime.now().isoformat()}")
        Simulation(config, os.environ.get('HEADLESS', 'True') == 'True')
        print(f"Simulation End: {datetime.now().isoformat()}")