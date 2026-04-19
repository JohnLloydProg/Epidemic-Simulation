from graphing.core import Edge, Node
from graphing.graph import Graph


class Route:
    id:int = 0

    def __init__(self, spawn_node:Node, path:list[Edge], graph:Graph, transport_class:type['Transportation']):
        self.path = path
        self.graph = graph
        self.id = Route.id
        self.transport_class = transport_class
        self.spawn_node = spawn_node
        Route.id += 1
    
    def __str__(self):
        return f"Route {self.id} from {self.spawn_node.id} to {self.path[-1].get_adjacent_node(self.path[-1].nodes[0]).id if self.path else self.spawn_node.id} through {[edge.id for edge in self.path]}"
    
    def generate_transportation(self, max_passenger:int) -> 'Transportation':
        return self.transport_class(max_passenger=max_passenger, current_node=self.spawn_node, route=self)

    def next_edge(self, current_edge:Edge) -> Edge | None:
        if (len(self.path) == 0):
            return None
        if (current_edge is None):
            return self.path[0]
        index= self.path.index(current_edge)
        return self.path[index + 1] if index + 1 < len(self.path) else None
    
    def get_nodes(self) -> set[int]:
        nodes = set()
        for edge_id in self.path:
            edge = self.graph.get_edge(edge_id)
            nodes.update({node.id for node in edge.nodes})
        return nodes


class Transportation:
    agents:list
    current_edge:Edge = None
    speed:int

    def __init__(self, max_passenger:int, method:str, current_node:Node, route:Route):
        self.route = route
        self.agents = []
        self.current_node = current_node
        self.max_passenger = max_passenger
        self.method = method
    
    def is_full(self) -> bool:
        return len(self.agents) >= self.max_passenger
    
    def transport(self, current_time:int):
        if (self.current_edge):
            self.current_node = self.current_edge.get_adjacent_node(self.current_node)
            self.current_edge = None
        else:
            self.current_edge = self.route.next_edge(self.current_edge)

            travel_time = self.current_edge.distance / self.speed
            # Schedule arrival event after travel_time


class PublicTransportation(Transportation):
    speed:int = 5

    def __init__(self, max_passenger:int, current_node:Node, route:Route):
        super().__init__(max_passenger=max_passenger, method='public', current_node=current_node, route=route)


class RailTransportation(Transportation):
    speed:int = 10

    def __init__(self, max_passenger:int, current_node:Node, route:Route):
        super().__init__(max_passenger=max_passenger, method='rail', current_node=current_node, route=route)


if __name__ == '__main__':
    pass
