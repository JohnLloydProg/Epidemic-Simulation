import pygame as pg
from agents.core import Firm, Household
import random


class Node:
    radius:int = 10
    agents:list
    edges:list['Edge']
    max_agents:int = 300

    def __init__(self, x:int, y:int, id:tuple[str, int]):
        self.id = id
        self.edges = []
        self.agents = []
        self.pos = (x, y)
    
    def draw(self, window:pg.Surface, font:pg.font.Font, x_offset:int, y_offset:int):
        pg.draw.circle(window, (int(255 * min(len(self.agents)/self.max_agents, 1)), 255 - int(255 * min(len(self.agents)/self.max_agents, 1)), 0), (self.pos[0] + x_offset, self.pos[1] + y_offset), self.radius)
        pg.draw.circle(window, (0, 0, 0), (self.pos[0] + x_offset, self.pos[1] + y_offset), self.radius, 2)
        text = font.render(str(self.id[1]), False, (0, 0, 0))
        window.blit(text, text.get_rect(center=(self.pos[0] + x_offset, self.pos[1] + y_offset)))


class Edge:
    no_vehicles = 0

    def __init__(self, node_a:'Node', node_b:'Node', distance:int,  id:tuple[str, int]):
        self.id = id
        self.nodes = (node_a, node_b)
        self.distance = distance
    
    def get_adjacent_node(self, current_node:Node) -> Node:
        if (current_node not in self.nodes):
            raise ValueError(f"Current node {current_node.id} is not part of this edge {self.nodes[0].id}-{self.nodes[1].id}.")
        
        return self.nodes[0] if current_node == self.nodes[1] else self.nodes[1]
    
    def draw(self, window:pg.Surface, x_offset:int, y_offset:int):
        node1_pos = self.nodes[0].pos
        node2_pos = self.nodes[1].pos
        pg.draw.line(window, (0, 0, 0), (node1_pos[0] + x_offset, node1_pos[1] + y_offset), (node2_pos[0] + x_offset, node2_pos[1] + y_offset), 2)


class Region:
    id:int = 0
    firms:list[Firm]
    households:list[Household]
    
    def __init__(self, nodes:list[Node]):
        self.nodes = nodes
        self.firms = []
        self.households = []
        self.id = Region.id
        Region.id += 1
    
    def add_firm(self, contact_rate:float):
        connected_nodes = list(filter(lambda node: len(node.edges) > 0, self.nodes))
        firm = Firm(random.choice(connected_nodes), self, random.choices(['micro', 'small', 'medium', 'large'], weights=[0.84, 0.13, 0.02, 0.01])[0], max_contact_rate=contact_rate)
        self.firms.append(firm)

    def add_household(self, contact_rate:float):
        connected_nodes = list(filter(lambda node: len(node.edges) > 0, self.nodes))
        household = Household(random.choice(connected_nodes), self, max_contact_rate=contact_rate)
        self.households.append(household)
        