from typing import Literal
from sim_event import manager
from functools import lru_cache
from objects import InitialParameters
from const import QUARANTINE_CR_PERCENTAGE
from graphing.graph import Graph
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


class Agent:
    id:int = 0
    destination:Establishment = None
    current_establishment:Establishment
    commuting:bool
    arrival_time:int = 0
    path:list[int]
    symptomatic:bool = False
    tested:bool = False
    state:str = 'home'

    def __init__(self, graph:Graph, household:Household, compartment:str='S'):
        self.household = household
        self.current_establishment = household
        self.current_establishment.add_agent(self)
        self.SEIR_compartment = compartment
        self.commuting = random.random() < 0.8
        self.graph = graph
        self.id = Agent.id
        Agent.id += 1
    
    def set_state(self, state:Literal['home', 'travelling', 'working', 'consuming']):
        self.state = state
    
    def check_for_infection(self, chance_per_contact:float, contact_rate:float, infected_density:float, duration:int, time:int, incubation_period:int):
        if (self.SEIR_compartment != 'S'):
            return
        
        chance_infection = compute_for_chance_of_infection(chance_per_contact, contact_rate, infected_density, duration)
        if (random.random() <= chance_infection):
            self.SEIR_compartment = 'E'
            infection_event = manager.AgentEvent(manager.AGENT_INFECTED, self)
            manager.emit(time + incubation_period, infection_event)
    
    def set_path(self, path:list[int], destination:'Establishment', time:int, initial_parameters:InitialParameters, quarantine_level:int):
        self.destination = destination
        self.current_establishment.remove_agent(self)
        self.check_for_infection(
            initial_parameters.sample_infection_establishment_CPC(), self.current_establishment.contact_rate() * QUARANTINE_CR_PERCENTAGE.get(quarantine_level, 1), 
            self.current_establishment.infected_density(), time - self.arrival_time, math.ceil(initial_parameters.sample_incubation_period())
            )
        
        self.path = path.copy()


class WorkingAgent(Agent):
    firm:Firm = None
    errand_run:bool = False

    def __init__(self, graph:Graph, household:Household, working_hours:tuple[int, int], compartment:str = 'S'):
        super().__init__(graph, household, compartment)
        self.working_hours = working_hours
