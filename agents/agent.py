from typing import Callable, Literal
from objects import InitialParameters
from graphing.graph import Graph
from agents.sector import Household, Firm
from agents.core import Establishment
from graphing.core import Edge
import pygame as pg
import random


class Agent:
    id:int = 0
    infected_contacts:int = 0
    started_travelling:int = 0
    destination:Establishment = None
    current_establishment:Establishment
    time_infected:int = 0
    path:list[int]
    current_edge:Edge = None
    state:str = 'home'

    def __init__(self, graph:Graph, household:Household, compartment:str='S'):
        self.household = household
        self.current_establishment = household
        self.current_node = household.node
        household.agents.append(self)
        self.SEIR_compartment = compartment
        self.speed = random.randint(1, 10)
        self.graph = graph
        self.id = Agent.id
        Agent.id += 1
    
    def set_state(self, state:Literal['home', 'travelling', 'working', 'consuming']):
        self.state = state
    
    def set_path(self, path:list[int], destination:'Establishment'):
        self.destination = destination
        self.current_establishment.agents.remove(self)
        if (self.SEIR_compartment == 'S'):
            self.infected_contacts = sum(map(lambda agent: 1 if agent.SEIR_compartment == 'I' else 0, self.current_establishment.agents))
        print(f"left node with id {self.current_node.id}")
        self.path = path.copy()
    
    def time_event(self, time:int, initial_parameter:InitialParameters):
        if (self.SEIR_compartment == 'E' and time - self.time_infected >= initial_parameter.incubation_period):
            self.SEIR_compartment = 'I'

    def traverse_graph(self, time:int, compute_function:Callable, chance_per_contact:float):
        assert self.state == 'travelling', "Can't traverse if not travelling"
        # Selects next point of traversal
        if (self.current_edge == None and self.path and self.destination):
            self.current_edge = self.graph.get_edge(self.path.pop(0))
            self.current_edge.agents.append(self)
            self.started_travelling = time
            return
        
        if (self.current_edge):
            if (time - self.started_travelling >= round(self.current_edge.distance / self.speed)):
                nodes = self.current_edge.nodes
                self.current_node = nodes[0] if self.current_node == nodes[1] else nodes[1]
                self.current_edge.agents.remove(self)
                if (self.SEIR_compartment == 'S'):
                    self.infected_contacts += round(sum(map(lambda agent: 1 if agent.SEIR_compartment == 'I' else 0, self.current_edge.agents)) * 0.20)
                self.current_edge = None

        if (self.current_node == self.destination.node):
            self.current_establishment = self.destination
            self.destination.agents.append(self)
            if (self.destination == self.household):
                self.set_state('home')
            elif (isinstance(self, WorkingAgent) and self.destination == self.firm):
                self.set_state('working')
            if (self.SEIR_compartment == 'S'):
                chance_infection = compute_function(self.infected_contacts, chance_per_contact)
                print(f"Infected contacts for agent {self.id}: {self.infected_contacts} (overall {chance_infection})")
                if (random.random() <= chance_infection):
                    self.SEIR_compartment = 'E'
                    self.time_infected = time


class WorkingAgent(Agent):
    def __init__(self, graph:Graph, household:Household, firm:Firm, working_hours:tuple[int, int], compartment:str = 'S'):
        super().__init__(graph, household, compartment)
        self.firm = firm
        self.working_hours = working_hours
    
    def working(self, hour:int):
        if (hour == self.working_hours[1]):
            print(f"Agent {self.id}:Going home")
            self.set_path(self.graph.shortest_edge_path(self.firm.node.id, self.household.node.id), self.household)
            self.set_state('travelling')