import pygame as pg

class Node:
    id:int = 0
    edges:list['Edge']
    radius = 20
    pos:tuple[int, int]

    def __init__(self, x:int, y:int):
        self.id = Node.id
        Node.id += 1
        self.pos = (x, y)
    
    def draw(self, window:pg.Surface, font:pg.font.Font):
        pg.draw.circle(window, (255, 0, 0), self.pos, self.radius)
        pg.draw.circle(window, (0, 0, 0), self.pos, self.radius, 2)
        text = font.render(str(self.id), False, (0, 0, 0))
        window.blit(text, text.get_rect(center=self.pos))


class Edge:
    id:int = 0
    nodes:tuple['Node', 'Node']
    weight:float

    def __init__(self, node_a:'Node', node_b:'Node', weight:float=0):
        self.id = Edge.id
        Edge.id += 1
        self.nodes = (node_a, node_b)
        self.weight = weight
    
    def draw(self, window:pg.Surface):
        pg.draw.line(window, (0, 0, 0), self.nodes[0].pos, self.nodes[1].pos, 4)


class Graph:
    nodes:list['Node']
    edges:list['Edge']

    def __init__(self):
        self.nodes = []
        self.edges = []

    def add_node(self, node:Node):
        self.nodes.append(node)
    
    def add_edge(self, weight:float, *args):
        if (len(args) != 2):
            raise ValueError(f"Expected 2 arguments received {len(args)}")
        if (isinstance(args[0], int) and isinstance(args[0], int)):
            id_1, id_2 = args[0], args[1]
            if (id_1 == id_2):
                raise ValueError("You can't add an edge connecting the same nodes")
            node_1 = None
            node_2 = None
            for node in self.nodes:
                if (id_1 == node.id):
                    node_1 = node
                elif (id_2 == node.id):
                    node_2 = node
            if (not node_1 or not node_2):
                raise ValueError(f"No node has the id of {id_1}/{id_2}")
            self.edges.append(Edge(node_1, node_2, weight))
        elif (isinstance(args[0], Node) and isinstance(args[1], Node)):
            node_1, node_2 = args[0], args[1]
            if (node_1 == node_2):
                raise ValueError("You can't add an edge connecting the same nodes")
            self.edges.append(Edge(node_1, node_2, weight))

    def get_edge(self, id:int) -> Edge:
        for edge in self.edges:
            if (id == edge.id):
                return edge

    def get_node(self, id:int) -> Node:
        for node in self.nodes:
            if (id == node.id):
                return node

    def get_edges(self, node:Node) -> list[Edge]:
        return filter(lambda edge: node in edge.nodes, self.edges)

    def adjacent_nodes(self, node:Node) -> list[Node]:
        adjacent_nodes:list[Node] = []
        for edge in self.edges:
            if edge.nodes[0] == node:
                adjacent_nodes.append(edge.nodes[1])
            elif edge.nodes[1] == node:
                adjacent_nodes.append(edge.nodes[0])
        return adjacent_nodes

    def draw(self, window:pg.Surface, font:pg.font.Font):
        for edge in self.edges:
            edge.draw(window)

        for node in self.nodes:
            node.draw(window, font)
