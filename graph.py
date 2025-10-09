

class Node:
    id:int
    edges:list['Edge']

    def __init__(self, id:int):
        self.id = id
    
    def get_edges(self) -> list['Edge']:
        return self.edges
    
    def adjacent_nodes(self) -> list['Node']:
        return [edge.nodes[0] if edge.nodes[1] == self else edge.nodes[1] for edge in self.edges]


class Edge:
    nodes:tuple['Node', 'Node']
    weight:float

    def __init__(self, node_a:'Node', node_b:'Node', weight:float=0):
        self.nodes = (node_a, node_b)
        self.weight = weight


class Graph:
    nodes:list['Node']
    edges:list['Edge']
