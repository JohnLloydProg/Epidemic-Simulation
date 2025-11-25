import pygame as pg


class Node:
    id:int = 0
    radius:int = 20
    edges:list['Edge']

    def __init__(self, x:int, y:int):
        self.id = Node.id
        Node.id += 1
        self.edges = []
        self.pos = (x, y)
    
    def draw(self, window:pg.Surface, font:pg.font.Font, x_offset:int, y_offset:int):
        pg.draw.circle(window, (255, 0, 0), (self.pos[0] + x_offset, self.pos[1] + y_offset), self.radius)
        pg.draw.circle(window, (0, 0, 0), (self.pos[0] + x_offset, self.pos[1] + y_offset), self.radius, 2)
        text = font.render(str(self.id), False, (0, 0, 0))
        window.blit(text, text.get_rect(center=(self.pos[0] + x_offset, self.pos[1] + y_offset)))


class Edge:
    id:int = 0
    agents:list

    def __init__(self, node_a:'Node', node_b:'Node', travelling_time:int):
        self.id = Edge.id
        Edge.id += 1
        self.nodes = (node_a, node_b)
        self.agents = []
        self.travelling_time = travelling_time
    
    def draw(self, window:pg.Surface, x_offset:int, y_offset:int):
        node1_pos = self.nodes[0].pos
        node2_pos = self.nodes[1].pos
        pg.draw.line(window, (0, 0, 0), (node1_pos[0] + x_offset, node1_pos[1] + y_offset), (node2_pos[0] + x_offset, node2_pos[1] + y_offset), 4)
        