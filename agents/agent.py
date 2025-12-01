from typing import Callable, Literal
import event
from functools import lru_cache
from objects import InitialParameters
from graphing.graph import Graph
from agents.sector import Household, Firm
from agents.core import Establishment
from graphing.core import Edge
import random

@lru_cache(maxsize=128, typed=False)
def compute_for_chance_of_infection(number_of_infected_contacts:int, chance_per_contact:float):
    chance_of_not_per_contact = 1 - chance_per_contact
    chance_of_not_infected = 1
    for _ in range(number_of_infected_contacts):
        chance_of_not_infected *= chance_of_not_per_contact
    return round(1 - chance_of_not_infected, 4)


@lru_cache(maxsize=128, typed=False)
def next_occurrence_of_hour(current_time, target_hour):
    MIN_PER_DAY = 1440
    current_minute_within_day = current_time % MIN_PER_DAY
    target_minute_within_day = target_hour * 60

    if target_minute_within_day > current_minute_within_day:
        # occurs later today
        return current_time - current_minute_within_day + target_minute_within_day
    else:
        # occurs tomorrow
        return (current_time - current_minute_within_day +
                MIN_PER_DAY + target_minute_within_day)


class Agent:
    id:int = 0
    infected_contacts:int = 0
    started_travelling:int = 0
    destination:Establishment = None
    current_establishment:Establishment
    travel_time:int = 0
    time_infected:int = 0
    path:list[int]
    current_edge:Edge = None
    state:str = 'home'

    def __init__(self, graph:Graph, household:Household, compartment:str='S'):
        self.household = household
        self.current_establishment = household
        self.current_node = household.node
        if (compartment == 'I'):
            self.current_establishment.no_infected += 1
        self.SEIR_compartment = compartment
        self.speed = random.randint(1, 10)
        self.graph = graph
        self.id = Agent.id
        Agent.id += 1
    
    def set_state(self, state:Literal['home', 'travelling', 'working', 'consuming']):
        self.state = state
    
    def set_path(self, path:list[int], destination:'Establishment'):
        self.destination = destination
        if (self.SEIR_compartment == 'I'):
            self.current_establishment.no_infected -= 1
        elif (self.SEIR_compartment == 'S'):
            self.infected_contacts = self.current_establishment.no_infected
        self.path = path.copy()
    
    def time_event(self, time:int, initial_parameter:InitialParameters):
        if (self.SEIR_compartment == 'E' and time - self.time_infected >= initial_parameter.incubation_period):
            self.SEIR_compartment = 'I'
    
    def go_home(self, time:int):
        self.set_path(self.graph.shortest_edge_path(self.current_establishment.node.id, self.household.node.id), self.household)
        self.set_state('travelling')
        event.emit(time + 1, event.AGENT_TRAVERSE, self)

    def traverse_graph(self, time:int, initial_parameters:InitialParameters):
        assert self.state == 'travelling', "Can't traverse if not travelling"
        
        if (self.current_edge):
            if (time - self.started_travelling >= self.travel_time):
                nodes = self.current_edge.nodes
                self.current_node = nodes[0] if self.current_node == nodes[1] else nodes[1]
                if (self.SEIR_compartment == 'I'):
                    self.current_edge.no_infected -= 1
                elif (self.SEIR_compartment == 'S'):
                    self.infected_contacts += self.current_edge.no_infected
                self.current_edge = None
            else:
                return
        
        if (self.current_edge == None and self.path and self.destination):
            self.current_edge = self.graph.get_edge(self.path.pop(0))
            if (self.SEIR_compartment == 'I'):
                self.current_edge.no_infected += 1
            self.started_travelling = time
            self.travel_time = round(self.current_edge.distance / self.speed)
            event.emit(time + self.travel_time, event.AGENT_TRAVERSE, self)
            return

        if (self.current_node == self.destination.node):
            self.current_establishment = self.destination
            if (self.SEIR_compartment == 'I'):
                self.destination.no_infected += 1
            if (self.destination == self.household):
                self.set_state('home')
                if (isinstance(self, WorkingAgent)):
                    event.emit(next_occurrence_of_hour(time, self.working_hours[0]), event.AGENT_GO_WORK, self)
            elif (isinstance(self, WorkingAgent) and self.destination == self.firm):
                self.set_state('working')
                event.emit(next_occurrence_of_hour(time, self.working_hours[1]), event.AGENT_GO_HOME, self)
            if (self.SEIR_compartment == 'S'):
                chance_infection = compute_for_chance_of_infection(self.infected_contacts, initial_parameters.chance_per_contact)
                if (random.random() <= chance_infection):
                    self.SEIR_compartment = 'E'
                    self.time_infected = time
                    event.emit(time + initial_parameters.incubation_period, event.AGENT_INFECTED, self)


class WorkingAgent(Agent):
    def __init__(self, graph:Graph, household:Household, firm:Firm, working_hours:tuple[int, int], compartment:str = 'S'):
        super().__init__(graph, household, compartment)
        self.firm = firm
        self.working_hours = working_hours
    
    def ready_for_work(self):
        event.emit((self.working_hours[0] - 1)*60, event.AGENT_GO_WORK, self)
    
    def go_work(self, time:int):
        self.set_path(self.graph.shortest_edge_path(self.household.node.id, self.firm.node.id), self.firm)
        self.set_state('travelling')
        event.emit(time + 1, event.AGENT_TRAVERSE, self)