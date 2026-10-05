import pygame as pg
import random


class Node:
    radius:int = 10
    agents:list
    edges:list['Edge']
    max_agents:int = 200

    def __init__(self, x:int, y:int, id:tuple[str, int]):
        self.id = id
        self.edges = []
        self.agents = []
        self.pos = (x, y)
    
    def draw(self, window:pg.Surface, font:pg.font.Font, camera):
        pos = camera.to_screen(self.pos)
        radius = camera.scale(self.radius, minimum=2)
        load = min(len(self.agents) / self.max_agents, 1)
        pg.draw.circle(window, (int(255 * load), 255 - int(255 * load), 0), pos, radius)
        pg.draw.circle(window, (0, 0, 0), pos, radius, camera.scale(2))
        if camera.show_labels():
            text = font.render(str(self.id[1]), False, (0, 0, 0))
            window.blit(text, text.get_rect(center=pos))


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
    
    def draw(self, window:pg.Surface, camera):
        pg.draw.line(window, (0, 0, 0), camera.to_screen(self.nodes[0].pos), camera.to_screen(self.nodes[1].pos), camera.scale(2))


class Region:
    id:int = 0
    name:str
    
    def __init__(self, nodes:list[Node], outline_nodes:list[Node], name:str = None):
        self.nodes = nodes
        self.outline_nodes = outline_nodes
        self.name = name
        self.id = Region.id
        Region.id += 1

    def draw(self, window:pg.Surface, font:pg.font.Font, x_offset:int, y_offset:int):
        if (len(self.outline_nodes) < 3):
            return
        
        points = [(node.pos[0] + x_offset, node.pos[1] + y_offset) for node in self.outline_nodes]
        text_surface = font.render(self.name, False, (0, 0, 0))
        text_rect = text_surface.get_rect()
        text_rect.center = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
        window.blit(text_surface, text_rect)
        pg.draw.polygon(window, (200, 200, 200), points)
        pg.draw.polygon(window, (0, 0, 0), points, 2)
        