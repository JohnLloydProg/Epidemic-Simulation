from typing import Callable, Literal
from graph import Graph, Edge, Node
import pygame as pg
import random


class Agent:
    id:int = 0
    residence_node:Node
    current_node:Node
    firm_node:Node
    SEIR_compartment:str
    infected_contacts:int = 0
    started_travelling:int = 0
    destination_node:Node = None
    path:list[int] = []
    current_edge:Edge = None
    state:str = 'home'

    def __init__(self, graph:Graph, residence_node:Node, firm_node:Node, compartment:str='S'):
        self.residence_node = residence_node
        self.firm_node = firm_node
        self.current_node = residence_node
        self.current_node.agents.append(self)
        self.SEIR_compartment = compartment
        self.graph = graph
        self.residence_node
        self.id = Agent.id
        Agent.id += 1
    
    def set_state(self, state:Literal['home', 'travelling', 'working', 'consuming']):
        self.state = state
    
    def set_path(self, path:list[int], destination_node:Node):
        self.destination_node = destination_node
        self.current_node.agents.remove(self)
        print(f"left node with id {self.current_node.id}")
        self.path = path.copy()

    def traverse_graph(self, time:int, compute_function:Callable, chance_per_contact:float, simulation):
        assert self.state == 'travelling', "Can't traverse if not travelling"
        # Selects next point of traversal
        if (self.current_edge == None and self.path and self.destination_node):
            self.current_edge = self.graph.get_edge(self.path.pop(0))
            self.current_edge.agents.append(self)
            self.started_travelling = time
            print(f"Started traversing edge with id {self.current_edge.id} at time {self.started_travelling}")
            print(self.path)
            return
        
        if (self.current_edge):
            if (time - self.started_travelling >= self.current_edge.travelling_time):
                nodes = self.current_edge.nodes
                self.current_node = nodes[0] if self.current_node == nodes[1] else nodes[1]
                self.infected_contacts += sum(map(lambda agent: 1 if agent != self and agent.SEIR_compartment == 'I' else 0, self.current_edge.agents))
                self.current_edge = None

        if (self.current_node == self.destination_node):
            self.current_node.agents.append(self)
            print(f"Arrived at node with id {self.current_node.id} at time {time}")
            print(f"Number of infected contacts: {self.infected_contacts}")
            if (self.current_node == self.residence_node):
                self.set_state('home')
            elif (self.current_node == self.firm_node):
                self.set_state('working')
            if (self.SEIR_compartment == 'S'):
                chance_infection = compute_function(self.infected_contacts, chance_per_contact)
                simulation.average_infection_chance.append(chance_infection)
                if (random.random() <= chance_infection):
                    self.SEIR_compartment = 'E'
            
            


class Working(Agent):
    pass


class Household:
    pass


class Firm:
    pass
