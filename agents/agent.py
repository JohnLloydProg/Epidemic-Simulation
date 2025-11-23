from typing import Callable, Literal
from graph import Graph, Edge, Node
import pygame as pg
import random


class Agent:
    id:int = 0
    infected_contacts:int = 0
    started_travelling:int = 0
    destination_node:Node = None
    path:list[int]
    current_edge:Edge = None
    state:str = 'home'

    def __init__(self, graph:Graph, residence_node:Node, compartment:str='S'):
        self.residence_node = residence_node
        self.current_node = residence_node
        self.current_node.agents.append(self)
        self.SEIR_compartment = compartment
        self.graph = graph
        self.id = Agent.id
        Agent.id += 1
    
    def set_state(self, state:Literal['home', 'travelling', 'working', 'consuming']):
        self.state = state
    
    def set_path(self, path:list[int], destination_node:Node):
        self.destination_node = destination_node
        self.current_node.agents.remove(self)
        if (self.SEIR_compartment == 'S'):
            self.infected_contacts = sum(map(lambda agent: 1 if agent.SEIR_compartment == 'I' else 0, self.current_node.agents))
        print(f"left node with id {self.current_node.id}")
        self.path = path.copy()

    def traverse_graph(self, time:int, compute_function:Callable, chance_per_contact:float) -> bool:
        assert self.state == 'travelling', "Can't traverse if not travelling"
        # Selects next point of traversal
        if (self.current_edge == None and self.path and self.destination_node):
            self.current_edge = self.graph.get_edge(self.path.pop(0))
            self.current_edge.agents.append(self)
            self.started_travelling = time
            return False
        
        if (self.current_edge):
            if (time - self.started_travelling >= self.current_edge.travelling_time):
                nodes = self.current_edge.nodes
                self.current_node = nodes[0] if self.current_node == nodes[1] else nodes[1]
                self.current_edge.agents.remove(self)
                if (self.SEIR_compartment == 'S'):
                    self.infected_contacts += sum(map(lambda agent: 1 if agent.SEIR_compartment == 'I' else 0, self.current_edge.agents))
                self.current_edge = None

        if (self.current_node == self.destination_node):
            self.current_node.agents.append(self)
            if (self.current_node == self.residence_node):
                self.set_state('home')
            if (self.SEIR_compartment == 'S'):
                chance_infection = compute_function(self.infected_contacts, chance_per_contact)
                print(f"Infected contacts for agent {self.id}: {self.infected_contacts} (overall {chance_infection})")
                if (random.random() <= chance_infection):
                    self.SEIR_compartment = 'E'
            return True
        return False
            

class Household:
    pass


class Firm:
    agents:list[Agent]

    def __init__(self, node:Node):
        self.agents = []
        self.node = node

    def clock_in(self, agent:Agent):
        self.agents.append(agent)
        agent.set_state('working')


class WorkingAgent(Agent):
    def __init__(self, graph:Graph, residence_node:Node, firm:Firm, working_hours:tuple[int, int], compartment:str = 'S'):
        super().__init__(graph, residence_node, compartment)
        self.firm = firm
        self.working_hours = working_hours
    
    def traverse_graph(self, time, compute_function, chance_per_contact):
        if (super().traverse_graph(time, compute_function, chance_per_contact)):
            if (self.current_node == self.firm.node):
                self.set_state('working')
    
    def working(self, hour:int):
        if (hour == self.working_hours[1]):
            print(f"Agent {self.id}:Going home")
            self.set_path(self.graph.shortest_edge_path(self.firm.node.id, self.residence_node.id), self.residence_node)
            self.set_state('travelling')