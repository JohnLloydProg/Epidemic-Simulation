from typing import Literal
from functools import lru_cache
from objects import Disease
from transport.transportation import Transportation, Route, RoutedTransportation
from transport.checkpoint import Checkpoint, generate_checkpoints
from const import QUARANTINE_CR_PERCENTAGE
from graphing.graph import Graph, RegionGraph
from graphing.core import Node, Region, Edge
from graphing.mapping import shortest_edge_path
from graphing.mapping import shortest_path
from agents.core import Household, Firm
from agents.core import Establishment
import logging
import random
import math
import manager

LOGGER = logging.getLogger("Agent")
AGE_RANGE_DISTRIBUTION = {
    (0, 4):0.077, (5, 9):0.095, (10, 14):0.010,
    (15, 19):0.098, (20, 24):0.092, (25, 29):0.089,
    (30, 34):0.082, (35, 39):0.072, (40, 44):0.063,
    (45, 49):0.055, (50, 54):0.047, (55, 59):0.041,
    (60, 64):0.033, (65, 69):0.025, (70, 74):0.017,
    (75, 79):0.009, (80, 84):0.005, (85, 89):0.002,
    (90, 94):0.0005, (95, 99):0.0005, (100, 104):0.0001
}

@lru_cache(maxsize=128, typed=False)
def compute_for_chance_of_infection(chance_per_contact:float, contact_rate:float, infected_density:float, duration:int) -> float:
    force_of_infection = chance_per_contact * contact_rate * infected_density * duration
    try:
        chance_of_not_infected = math.exp(-force_of_infection)
    except OverflowError:
        chance_of_not_infected = 0.0
    return round(1 - chance_of_not_infected, 4)

@lru_cache(maxsize=128, typed=False)
def compute_mortality_rate(age:int) -> float:
    exponent = (-10.2 + (0.106 * age))
    try:
        p_mortality = 1 / (1 + math.exp(-exponent))
    except OverflowError:
        p_mortality = 0.0
    return round(p_mortality, 4)

@lru_cache(maxsize=128, typed=False)
def next_occurrence_of_hour(current_time, target_hour):
    MIN_PER_DAY = 1440
    current_minute_within_day = current_time % MIN_PER_DAY
    target_minute_within_day = target_hour * 60

    if target_minute_within_day > current_minute_within_day:
        return current_time - current_minute_within_day + target_minute_within_day
    else:
        return (current_time - current_minute_within_day +
                MIN_PER_DAY + target_minute_within_day)


class Agent:
    id:int = 0
    destination:Establishment = None
    current_establishment:Establishment
    commuting:bool
    private:str
    arrival_time:int = 0
    boarding_time:int = 0
    checkpoints:list[Checkpoint]
    current_node:Node = None
    transportation:Transportation = None
    consumed:bool = False
    symptomatic:bool = False
    masked:bool = False
    isolate:bool = False
    tested:bool = False
    state:str = 'home'

    def __init__(self, age:int, city:RegionGraph, railway:Graph, household:Household, compartment:str='S'):
        self.age = age
        self.SEIR_compartment = compartment
        self.household = household
        self.current_establishment = household
        self.current_establishment.add_agent(self)
        self.commuting = random.random() < 0.8
        if (not self.commuting):
            self.private = 'car' if (random.random() < 0.7) else 'bike'
        self.city = city
        self.railway = railway
        self.id = Agent.id
        Agent.id += 1
    
    def ride_transportation(self, transportation:Transportation, time:int):
        if (isinstance(transportation, RoutedTransportation) and transportation.is_full()):
            return
        
        self.transportation = transportation
        self.boarding_time = time
        transportation.agents.append(self)
        if (self.SEIR_compartment == 'I'):
            transportation.no_infected_agents += 1 if not self.masked else 0.5
        self.current_node.agents.remove(self)
        self.current_node = None
    
    def alight_transportation(self):
        if (self.transportation):
            self.transportation.agents.remove(self)
            if (self.SEIR_compartment == 'I'):
                self.transportation.no_infected_agents -= 1 if not self.masked else 0.5
            self.transportation = None
    
    def set_state(self, state:Literal['home', 'travelling', 'waiting', 'working', 'consuming']):
        self.state = state
    
    def check_for_infection(self, chance_per_contact:float, incubation_period:int, contact_rate:float, infected_density:float, duration:int, time:int):
        if (self.SEIR_compartment != 'S'):
            return
        
        chance_infection = compute_for_chance_of_infection(chance_per_contact, contact_rate, infected_density, duration)
        if (random.random() <= chance_infection):
            self.SEIR_compartment = 'E'
            infection_event = manager.Event(manager.AGENT_INFECTED, self)
            manager.emit(time + incubation_period, infection_event)
    
    def set_path(self, destination:Establishment, time:int):
        self.current_establishment.remove_agent(self)
        self.destination = destination
        self.current_node = self.current_establishment.node
        self.current_node.agents.append(self)
        if (self.current_node.id == destination.node.id):
            self.arrived_at_destination(time)
        else:
            path:list[Edge] = shortest_edge_path(self.current_node.id, self.destination.node.id, self.city, self.railway)
            if (not path):
                raise ValueError(f"No path found from node {self.current_node.id} to node {self.destination.node.id}.")

            if (self.current_node not in path[0].nodes or self.destination.node not in path[-1].nodes):
                raise ValueError(f"Invalid path: {[(edge.nodes[0].id, edge.nodes[1].id) for edge in path]} for current node {self.current_node.id} and destination node {destination.node.id}.")
            
            transport = Transportation(method='private', speed=500, current_node=self.current_node, path=list(path))
            self.ride_transportation(transport, time)
            self.set_state('travelling')
            transport.transport(time)

    def set_checkpoints(self, destination:Establishment, routing_cache:dict, routes:list[Route], time:int):
        self.current_establishment.remove_agent(self)
        self.destination = destination
        self.current_node = self.current_establishment.node
        self.current_node.agents.append(self)
        if (self.current_node.id == destination.node.id):
            self.arrived_at_destination(time)
        else:
            key = (self.current_node.id, destination.node.id)
            cached_checkpoint = routing_cache.get(key, [])
            if (cached_checkpoint): 
                self.checkpoints = list(cached_checkpoint)
                self.set_state('travelling')
                self.move(time)
            else:
                raw_path = shortest_path(self.current_node, destination.node, routes)
                if (not raw_path):
                    raise ValueError(f"Can't find path between {self.current_node.id} and {destination.node.id}")
                routing_cache[key] = generate_checkpoints(raw_path)
                self.checkpoints = list(routing_cache[key])
                self.set_state('travelling')
                self.move(time)

            

    def arrival(self, time:int, current_node:Node=None):
        if (self.commuting and self.state == 'travelling'):
            finished_checkpoint = self.checkpoints.pop(0)
            self.current_node = finished_checkpoint.end_node
            self.current_node.agents.append(self)
            if (self.checkpoints):
                self.move(time)
        elif (current_node):
            self.current_node = current_node
            self.current_node.agents.append(self)
        
        if (self.current_node == self.destination.node):
            self.arrived_at_destination(time)

    def arrived_at_destination(self, time:int):
        self.arrival_time = time
        self.current_establishment = self.destination
        self.current_establishment.add_agent(self)
        if (isinstance(self.destination, Firm)):
            if (isinstance(self, WorkingAgent) and self.destination == self.firm):
                time_out = next_occurrence_of_hour(time, self.working_hours[1] - random.gauss(0, 0.5))
                if (not self.clocked_in):
                    while (time_out - 2 < time):
                        time_out += 5
                    manager.emit(time_out-2, manager.Event(manager.AGENT_FINISHED_WORK, self))
                    self.clocked_in = True
                target_time = time_out
                next_event = manager.Event(manager.AGENT_GO_HOME, self)
                if ((self.errand_run or random.random() < 0.5)  and not self.consumed):
                    next_event = manager.Event(manager.AGENT_GO_SHOPPING, self)
                    self.consumed = False
                    mid_day_break_time = 12 - random.gauss(0, 0.5)
                    if (random.random() < 0.25 and mid_day_break_time > (time % 1440)/60):
                        target_time = next_occurrence_of_hour(time, mid_day_break_time)
                manager.emit(target_time, next_event)
                self.set_state('working')
            else:
                self.consumed = True
                if (isinstance(self, WorkingAgent) and not self.finished_work):
                    manager.emit(time + 30, manager.Event(manager.AGENT_GO_WORK, self))
                else:
                    manager.emit(time + random.randint(30, 120), manager.Event(manager.AGENT_GO_HOME, self))
                self.set_state('consuming')
        elif (isinstance(self.destination, Household)):
            self.set_state('home')
        self.current_node.agents.remove(self)
        self.current_node = None
    
    def move(self, time:int):
        if (not self.checkpoints):
            return

        current_checkpoint = self.checkpoints[0]

        if (current_checkpoint.mode == 'walk'):
            self.current_node.agents.remove(self)
            self.current_node = None
            
            if (current_checkpoint.start_node == current_checkpoint.end_node):
                walking_time = 5
            else:
                total_distance = sum(edge.distance for edge in shortest_edge_path(current_checkpoint.start_node.id, current_checkpoint.end_node.id, self.city, self.railway))
                walking_time = math.ceil(total_distance / 75)  # Assuming walking speed is 1 unit per time
            self.set_state('travelling')
            manager.emit(time + round(walking_time), manager.Event(manager.AGENT_ARRIVAL, self))
        elif (current_checkpoint.mode == 'ride'):
            self.set_state('waiting')

class WorkingAgent(Agent):
    firm:Firm = None
    errand_run:bool = False
    finished_work:bool = False
    weekend_worker:bool = False
    day_offs:list[int]
    clocked_in:bool = False

    def __init__(self, age:int, city:RegionGraph, railway:Graph, household:Household, working_hours:tuple[int, int], compartment:str = 'S'):
        super().__init__(age, city, railway, household, compartment)
        self.working_hours = working_hours


def handle_agent_events(event:manager.Event, routing_cache:dict, routes:list[Route], max_distance:None|int, quarantine:bool, disease:Disease, time:int):
    agents:list[Agent] = event.get_objects()
    if (event.type == manager.AGENT_ARRIVAL):
        LOGGER.debug(f"Handling agent arrival for {len(agents)} agents at time {time}.")
        for agent in agents:
            agent.arrival(time)
    elif (event.type == manager.AGENT_REMOVED):
        for agent in agents:
            mortality_rate = compute_mortality_rate(agent.age)
            # TO ADD: Recovery chance depending on age, health condition, etc.
            if (random.random() <= mortality_rate):
                agent.SEIR_compartment = 'D'
            else:
                agent.SEIR_compartment = 'R'
                agent.isolate = False
    elif (event.type == manager.AGENT_INFECTED):
        for agent in agents:
            agent.SEIR_compartment = 'I'
            agent.symptomatic = random.random() < 0.6  # 60% chance to be symptomatic
            if (agent.symptomatic):
                if (not quarantine):
                    manager.emit(time + (random.randint(24, 48)*60), manager.Event(manager.AGENT_ISOLATE, agent))
                else:
                    agent.isolate = True
            remove_event = manager.Event(manager.AGENT_REMOVED, agent)
            manager.emit(time + round(disease.sample_infected_duration()), remove_event)
    elif (event.type == manager.AGENT_GO_HOME):
        LOGGER.debug(f"Handling agent go home for {len(agents)} agents at time {time}.")
        for agent in agents:
            if (isinstance(agent, WorkingAgent) and agent.current_establishment == agent.firm):
                chance_per_contact = disease.sample_infection_firm_work_CPC()
            else:
                chance_per_contact = disease.sample_infection_firm_retail_CPC()

            agent.check_for_infection(
                chance_per_contact,
                disease.sample_incubation_period(),
                agent.current_establishment.contact_rate(), 
                agent.current_establishment.infected_density(),
                (time - agent.arrival_time)/60, time
                )
            if (agent.commuting):
                agent.set_checkpoints(agent.household, routing_cache, routes, time)
            else:
                agent.set_path(agent.household, time)
    elif (event.type == manager.AGENT_GO_SHOPPING):
        LOGGER.debug(f"Handling agent go shopping for {len(agents)} agents at time {time}.")
        for agent in agents:
            if (agent.current_establishment == agent.household):
                chance_per_contact = disease.sample_infection_household_CPC()
            else:
                chance_per_contact = disease.sample_infection_firm_work_CPC()
            agent.check_for_infection(
                chance_per_contact,
                disease.sample_incubation_period(),
                agent.current_establishment.contact_rate(), 
                agent.current_establishment.infected_density(),
                (time - agent.arrival_time)/60, time
                )
            
            if (random.random() < 0.8):
                choices:list[Firm] = [firm for firm in agent.city.get_close_firms(agent.current_establishment.region) if firm.working_agents]
            else:
                choices:list[Firm] = [firm for firm in agent.city.get_firms() if firm.working_agents]
            if (isinstance(agent, WorkingAgent) and agent.firm in choices):
                choices.remove(agent.firm)
            destination = random.choice(choices)
            if (max_distance):
                distance = sum(edge.distance for edge in shortest_edge_path(agent.current_establishment.node.id, destination.node.id, agent.city, agent.railway))
                if (distance > max_distance):
                    choices:list[Firm] = [firm for firm in agent.city.get_close_firms(agent.current_establishment.region) if firm.working_agents]
                    if (isinstance(agent, WorkingAgent) and agent.firm in choices):
                        choices.remove(agent.firm)
                    destination = random.choice(choices)


            if (agent.commuting):
                agent.set_checkpoints(destination, routing_cache, routes, time)
            else:
                agent.set_path(destination, time)
    elif (event.type == manager.AGENT_GO_WORK):
        LOGGER.debug(f"Handling agent go work for {len(agents)} agents at time {time}.")
        for agent in agents:
            if (agent.current_establishment == agent.household):
                chance_per_contact = disease.sample_infection_household_CPC()
            else:
                chance_per_contact = disease.sample_infection_firm_retail_CPC()

            agent.check_for_infection(
                chance_per_contact,
                disease.sample_incubation_period(),
                agent.current_establishment.contact_rate(), 
                agent.current_establishment.infected_density(),
                (time - agent.arrival_time)/60, time
                )
            if (agent.commuting):
                agent.set_checkpoints(agent.firm, routing_cache, routes, time)
            else:
                agent.set_path(agent.firm, time)
    elif (event.type == manager.AGENT_FINISHED_WORK):
        LOGGER.debug(f"Handling agent finished work for {len(agents)} agents at time {time}.")
        for agent in agents:
            agent.finished_work = True
    elif (event.type == manager.AGENT_ISOLATE):
        LOGGER.debug(f'Handling agent isolation for {len(agents)} agents at time {time}.')
        for agent in agents:
            agent.isolate = True
            
