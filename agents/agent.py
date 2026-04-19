from typing import Literal
from sim_event import manager
from functools import lru_cache
from objects import InitialParameters
from transport.transportation import Transportation, Route
from transport.checkpoint import Checkpoint, generate_checkpoints
from const import QUARANTINE_CR_PERCENTAGE
from graphing.graph import Graph
from graphing.core import Node, Edge
from graphing.mapping import shortest_path
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
def get_edges_from_nodes(nodes:list[Node], graph:Graph) -> list[Edge]:
    walk_edges = []
    for i in range(len(nodes) - 1):
        # Find the physical edge connecting node i to node i+1
        node_a = nodes[i]
        node_b = nodes[i+1]
        for edge in node_a.edges:
            if edge.get_adjacent_node(node_a) == node_b:
                walk_edges.append(edge)
                break


class Agent:
    id:int = 0
    destination:Establishment = None
    current_establishment:Establishment
    commuting:bool
    arrival_time:int = 0
    checkpoints:list[Checkpoint]
    current_node:Node
    transportation:Transportation = None
    symptomatic:bool = False
    tested:bool = False
    state:str = 'home'

    def __init__(self, graph:Graph, household:Household, compartment:str='S'):
        self.SEIR_compartment = compartment
        self.household = household
        self.current_establishment = household
        self.current_establishment.add_agent(self)
        self.commuting = random.random() < 0.8
        self.graph = graph
        self.current_node = household.node
        self.id = Agent.id
        Agent.id += 1
    
    def ride_transportation(self, transportation:Transportation):
        if (not transportation.is_full()):
            self.transportation = transportation
            transportation.agents.append(self)
            self.current_node.agents.remove(self)
            self.current_node = None
    
    def alight_transportation(self):
        if (self.transportation):
            self.transportation.agents.remove(self)
            self.transportation = None
    
    def set_state(self, state:Literal['home', 'travelling', 'waiting', 'working', 'consuming']):
        self.state = state
    
    def check_for_infection(self, chance_per_contact:float, contact_rate:float, infected_density:float, duration:int, time:int, incubation_period:int):
        if (self.SEIR_compartment != 'S'):
            return
        
        chance_infection = compute_for_chance_of_infection(chance_per_contact, contact_rate, infected_density, duration)
        if (random.random() <= chance_infection):
            self.SEIR_compartment = 'E'
            infection_event = manager.AgentEvent(manager.AGENT_INFECTED, self)
            manager.emit(time + incubation_period, infection_event)

    def set_checkpoints(self, destination:Establishment, routes:list[Route], time:int):
        self.destination = destination
        raw_path = shortest_path(self.current_establishment.node, destination.node, routes)
        if (raw_path): 
            self.checkpoints = generate_checkpoints(raw_path)
            self.set_state('travelling')
        self.current_node.agents.append(self)
        self.move(time)
    
    def move(self, time:int):
        if (not self.checkpoints):
            self.current_establishment = self.destination
            self.destination.add_agent(self)
            return

        current_checkpoint = self.checkpoints[0]

        if (current_checkpoint.mode == 'walk'):
            self.current_node.agents.remove(self)
            self.current_node = None
            total_distance = sum(edge.distance for edge in get_edges_from_nodes(self.current_checkpoint.path_nodes))
            walking_time = math.ceil(total_distance / 1)  # Assuming walking speed is 1 unit per time

            # emit arrival event at the end of walking time
        elif (current_checkpoint.mode == 'ride'):
            self.set_state('waiting')

class WorkingAgent(Agent):
    firm:Firm = None
    errand_run:bool = False

    def __init__(self, graph:Graph, household:Household, working_hours:tuple[int, int], compartment:str = 'S'):
        super().__init__(graph, household, compartment)
        self.working_hours = working_hours
