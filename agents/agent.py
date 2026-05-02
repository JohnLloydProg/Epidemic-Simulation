from typing import Literal
from sim_event import manager
from functools import lru_cache
from objects import InitialParameters
from transport.transportation import Transportation, Route
from transport.checkpoint import Checkpoint, generate_checkpoints
from const import QUARANTINE_CR_PERCENTAGE
from graphing.graph import Graph, RegionGraph
from graphing.core import Node, Edge
from graphing.mapping import shortest_edge_path
from agents.core import Household, Firm
from agents.core import Establishment
import logging
import random
import math

logger = logging.getLogger(__name__)

@lru_cache(maxsize=128, typed=False)
def compute_for_chance_of_infection(chance_per_contact:float, contact_rate:float, infected_density:float, duration:int) -> float:
    force_of_infection = chance_per_contact * contact_rate * infected_density * duration
    try:
        chance_of_not_infected = math.exp(-force_of_infection)
    except OverflowError:
        chance_of_not_infected = 0.0
    return round(1 - chance_of_not_infected, 4)

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
    arrival_time:int = 0
    boarding_time:int = 0
    checkpoints:list[Checkpoint]
    current_node:Node = None
    transportation:Transportation = None
    symptomatic:bool = False
    tested:bool = False
    state:str = 'home'
    checkpoint_log:list[tuple[int, Checkpoint]]

    def __init__(self, city:RegionGraph, railway:Graph, household:Household, compartment:str='S'):
        self.SEIR_compartment = compartment
        self.household = household
        self.current_establishment = household
        self.current_establishment.add_agent(self)
        self.commuting = random.random() < 0.8
        self.city = city
        self.railway = railway
        self.id = Agent.id
        Agent.id += 1
    
    def ride_transportation(self, transportation:Transportation, time:int):
        if (not transportation.is_full()):
            self.transportation = transportation
            self.boarding_time = time
            transportation.agents.append(self)
            if (self.SEIR_compartment == 'I'):
                transportation.no_infected_agents += 1
    
    def alight_transportation(self):
        if (self.transportation):
            self.transportation.agents.remove(self)
            if (self.SEIR_compartment == 'I'):
                self.transportation.no_infected_agents -= 1
            self.transportation = None
    
    def set_state(self, state:Literal['home', 'travelling', 'waiting', 'working', 'consuming']):
        self.state = state
    
    def check_for_infection(self, chance_per_contact:float, incubation_period:int, contact_rate:float, infected_density:float, duration:int, time:int):
        if (self.SEIR_compartment != 'S'):
            return
        
        chance_infection = compute_for_chance_of_infection(chance_per_contact, contact_rate, infected_density, duration)
        if (random.random() <= chance_infection):
            self.SEIR_compartment = 'E'
            infection_event = manager.AgentEvent(manager.AGENT_INFECTED, self)
            manager.emit(time + incubation_period, infection_event)

    def set_checkpoints(self, destination:Establishment, routing_cache:dict, time:int):
        self.checkpoint_log = []
        self.current_establishment.remove_agent(self)
        self.destination = destination
        self.current_node = self.current_establishment.node
        self.current_node.agents.append(self)
        if (self.current_node.id == destination.node.id):
            self.current_establishment = self.destination
            self.current_establishment.add_agent(self)
            
            if (isinstance(self.destination, Firm)):
                next_event = manager.AgentEvent(manager.AGENT_GO_HOME, self)
                if (isinstance(self, WorkingAgent) and self.destination == self.firm):
                    manager.emit(next_occurrence_of_hour(time, self.working_hours[1]), next_event)
                    self.set_state('working')
                else:
                    manager.emit(time + random.randint(30, 120), next_event)
                    self.set_state('consuming')
            elif (isinstance(self.destination, Household)):
                self.set_state('home')
            self.destination.add_agent(self)
            self.current_node.agents.remove(self)
            self.current_node = None
        else:
            cached_checkpoint = routing_cache.get((self.current_node.id, destination.node.id), [])
            if (cached_checkpoint): 
                self.checkpoints = list(cached_checkpoint)
                self.copy_checkpoints = self.checkpoints.copy()
                self.set_state('travelling')
                self.move(time)
            else:
                print('NO DATA ON CACHE')
            

    def arrival(self, time:int):
        finished_checkpoint = self.checkpoints.pop(0)
        self.current_node = finished_checkpoint.end_node
        self.current_node.agents.append(self)
        self.checkpoint_log.append((time, finished_checkpoint, len(self.checkpoints)))
        if (self.checkpoints):
            self.move(time)
            return

        if (self.current_node == self.destination.node):
            self.arrival_time = time
            self.current_establishment = self.destination
            self.current_establishment.add_agent(self)
            if (isinstance(self.destination, Firm)):
                next_event = manager.AgentEvent(manager.AGENT_GO_HOME, self)
                if (isinstance(self, WorkingAgent) and self.destination == self.firm):
                    manager.emit(next_occurrence_of_hour(time, self.working_hours[1]), next_event)
                    self.set_state('working')
                else:
                    manager.emit(time + random.randint(30, 120), next_event)
                    self.set_state('consuming')
            elif (isinstance(self.destination, Household)):
                self.set_state('home')
            self.destination.add_agent(self)
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

            manager.emit(time + round(walking_time), manager.AgentEvent(manager.AGENT_ARRIVAL, self))
        elif (current_checkpoint.mode == 'ride'):
            self.set_state('waiting')

class WorkingAgent(Agent):
    firm:Firm = None
    errand_run:bool = False

    def __init__(self, city:RegionGraph, railway:Graph, household:Household, working_hours:tuple[int, int], compartment:str = 'S'):
        super().__init__(city, railway, household, compartment)
        self.working_hours = working_hours
