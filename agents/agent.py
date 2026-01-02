from typing import Callable, Literal
import event
from functools import lru_cache
from objects import InitialParameters
from graphing.graph import Graph
from agents.sector import Household, Firm
from agents.core import Establishment
from graphing.core import Edge
import random
import math

@lru_cache(maxsize=128, typed=False)
def compute_for_chance_of_infection(chance_per_contact:float, contact_rate:float, infected_density:float, duration:int) -> float:
    total_probability_of_infection = chance_per_contact * contact_rate * infected_density * duration
    chance_of_not_infected = math.exp(-total_probability_of_infection)
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
    started_travelling:int = 0
    destination:Establishment = None
    current_establishment:Establishment
    commuting:bool = True
    method:str = 'walking'
    arrival_time:int = 0
    travel_time:int = 0
    time_infected:int = 0
    path:list[int]
    current_edge:Edge = None
    state:str = 'home'

    def __init__(self, graph:Graph, household:Household, compartment:str='S'):
        self.household = household
        self.current_establishment = household
        self.current_node = household.node
        self.current_establishment.no_agents += 1
        if (compartment == 'I'):
            self.current_establishment.no_infected_agents += 1
        self.SEIR_compartment = compartment
        self.speed = random.randint(200, 300)
        self.graph = graph
        self.id = Agent.id
        Agent.id += 1
    
    def set_state(self, state:Literal['home', 'travelling', 'working', 'consuming']):
        self.state = state
    
    def set_path(self, path:list[int], destination:'Establishment', time:int, initial_parameters:InitialParameters):
        self.destination = destination
        self.current_establishment.no_agents -= 1
        if (self.SEIR_compartment == 'I'):
            self.current_establishment.no_infected_agents -= 1
        self.contacted_agents = []

        # Check for infection before leaving the establishment
        if (self.SEIR_compartment == 'S'):
            chance_infection = compute_for_chance_of_infection(initial_parameters.sample_infection_establishment_CPC(), self.current_establishment.contact_rate(), self.current_establishment.infected_density(), time - self.arrival_time)
            if (random.random() <= chance_infection):
                self.SEIR_compartment = 'E'
                self.time_infected = time
                infection_event = event.AgentEvent(event.AGENT_INFECTED, self)
                event.emit(time + math.ceil(initial_parameters.sample_incubation_period()), infection_event)
        
        self.path = path.copy()
    
    def go_home(self, time:int, initial_parameters:InitialParameters):
        self.set_path(self.graph.shortest_edge_path(self.current_establishment.node.id, self.household.node.id), self.household, time, initial_parameters)
        self.set_state('travelling')
        traverse_event = event.AgentEvent(event.AGENT_TRAVERSE, self)
        event.emit(time + 1, traverse_event)

    def edge_infection(self, time:int, initial_parameters:InitialParameters, time_step:int):
        if (self.SEIR_compartment != 'S' or not self.current_edge):
            return

        if (time + time_step < self.started_travelling + self.travel_time):
            edge_infection_event = event.AgentEvent(event.EDGE_INFECTION, self)
            event.emit(time + time_step, edge_infection_event)
        
        chance_infection = compute_for_chance_of_infection(initial_parameters.sample_infection_edge_CPC(), self.current_edge.contact_rate(self), self.current_edge.infected_density(), time_step)
        if (random.random() <= chance_infection):
            self.SEIR_compartment = 'E'
            self.time_infected = time
            infection_event = event.AgentEvent(event.AGENT_INFECTED, self)
            event.emit(time + math.ceil(initial_parameters.sample_incubation_period()), infection_event)

    def traverse_graph(self, time:int, time_step:int):
        assert self.state == 'travelling', "Can't traverse if not travelling"
        
        if (self.current_edge):
            if (time - self.started_travelling >= self.travel_time):
                nodes = self.current_edge.nodes
                self.current_node = nodes[0] if self.current_node == nodes[1] else nodes[1]
                self.current_edge.no_agents -= 1
                if (self.SEIR_compartment == 'I'):
                    self.current_edge.no_infected_agents -= 1
                self.current_edge = None
            else:
                return
        
        # Implement Transportation Method if commuting.
        if (self.current_edge == None and self.path and self.destination):
            self.current_edge = self.graph.get_edge(self.path.pop(0))
            self.current_edge.no_agents += 1
            if (self.SEIR_compartment == 'I'):
                self.current_edge.no_infected_agents += 1
            self.started_travelling = time
            self.travel_time = math.ceil(self.current_edge.distance / self.speed)
            edge_infection_event = event.AgentEvent(event.EDGE_INFECTION, self)
            traversal_event = event.AgentEvent(event.AGENT_TRAVERSE, self)
            event.emit(time + time_step, edge_infection_event)
            event.emit(time + self.travel_time, traversal_event)
            return

        if (self.current_node == self.destination.node):
            self.current_establishment = self.destination
            self.current_establishment.no_agents += 1
            if (self.SEIR_compartment == 'I'):
                self.current_establishment.no_infected_agents += 1
            self.arrival_time = time
            if (self.destination == self.household):
                self.set_state('home')
                if (isinstance(self, WorkingAgent)):
                    go_work_event = event.AgentEvent(event.AGENT_GO_WORK, self)
                    event.emit(next_occurrence_of_hour(time, self.working_hours[0] - 1), go_work_event)
            elif (isinstance(self, WorkingAgent) and self.destination == self.firm):
                self.set_state('working')
                go_home_event = event.AgentEvent(event.AGENT_GO_HOME, self)
                event.emit(next_occurrence_of_hour(time, self.working_hours[1]), go_home_event)


class WorkingAgent(Agent):
    def __init__(self, graph:Graph, household:Household, firm:Firm, working_hours:tuple[int, int], compartment:str = 'S'):
        super().__init__(graph, household, compartment)
        self.firm = firm
        self.working_hours = working_hours
    
    def go_work(self, time:int, initial_parameters:InitialParameters):
        self.set_path(self.graph.shortest_edge_path(self.household.node.id, self.firm.node.id), self.firm, time, initial_parameters)
        self.set_state('travelling')
        traversal_event = event.AgentEvent(event.AGENT_TRAVERSE, self)
        event.emit(time + 1, traversal_event)