import pygame as pg
import heapq

class Node:
    id:int = 0
    radius:int = 20
    agents:list
    edges:list['Edge']

    def __init__(self, x:int, y:int):
        self.id = Node.id
        Node.id += 1
        self.agents = []
        self.edges = []
        self.pos = (x, y)
    
    def draw(self, window:pg.Surface, font:pg.font.Font):
        pg.draw.circle(window, (255, 0, 0), self.pos, self.radius)
        pg.draw.circle(window, (0, 0, 0), self.pos, self.radius, 2)
        text = font.render(str(self.id), False, (0, 0, 0))
        window.blit(text, text.get_rect(center=self.pos))


class Edge:
    id:int = 0
    agents:list

    def __init__(self, node_a:'Node', node_b:'Node', travelling_time:int):
        self.id = Edge.id
        Edge.id += 1
        self.nodes = (node_a, node_b)
        self.agents = []
        self.travelling_time = travelling_time
    
    def draw(self, window:pg.Surface):
        pg.draw.line(window, (0, 0, 0), self.nodes[0].pos, self.nodes[1].pos, 4)


class Region:
    id:int = 0
    
    def __init__(self, nodes:list[Node], no_resi):
        self.nodes = nodes


class Graph:
    nodes:list['Node']
    edges:list['Edge']
    regions:list['Region']

    def __init__(self):
        self.nodes = []
        self.edges = []

    def add_node(self, node:Node):
        self.nodes.append(node)
    
    def add_edge(self, travelling_time:int, *args):
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
            self.edges.append(Edge(node_1, node_2, travelling_time))
        elif (isinstance(args[0], Node) and isinstance(args[1], Node)):
            node_1, node_2 = args[0], args[1]
            if (node_1 == node_2):
                raise ValueError("You can't add an edge connecting the same nodes")
            edge = Edge(node_1, node_2, travelling_time)
            self.edges.append(edge)
            node_1.edges.append(edge)
            node_2.edges.append(edge)

    def get_edge(self, *args) -> Edge:
        if (len(args) == 1):
            if (not isinstance(args[0], int)):
                raise ValueError("Argument has to be an integer corresponding to the id")
            id = args[0]
            for edge in self.edges:
                if (id == edge.id):
                    return edge
        elif (len(args) == 2):
            if (not isinstance(args[0], Node) or not isinstance(args[0], Node)):
                raise ValueError("Argument has to be nodes of connected by an edge")
            node_1 = args[0]
            node_2 = args[1]
            for edge in self.edges:
                if (node_1 in edge.nodes and node_2 in edge.nodes):
                    return edge
    def get_node(self, id:int) -> Node:
        for node in self.nodes:
            if (id == node.id):
                return node

    def get_edges(self, node:Node) -> list[Edge]:
        return filter(lambda edge: node in edge.nodes, self.edges)
    
    def shortest_edge_path(self, start_id: int, end_id: int) -> list[int]:
        if start_id not in [n.id for n in self.nodes] or end_id not in [n.id for n in self.nodes]:
            raise ValueError("Start or end node ID not in graph.")

        # Initialize distances and previous edge mapping
        distances: dict[int, float] = {node.id: float('inf') for node in self.nodes}
        previous_edge: dict[int, int | None] = {node.id: None for node in self.nodes}

        distances[start_id] = 0
        pq: list[tuple[float, int]] = [(0, start_id)]  # (distance, node_id)

        while pq:
            dist, current_id = heapq.heappop(pq)
            current_node = next(n for n in self.nodes if n.id == current_id)

            if dist > distances[current_id]:
                continue

            for edge in current_node.edges:
                neighbor = edge.nodes[0] if edge.nodes[1] == current_node else edge.nodes[1]
                new_dist = dist + edge.travelling_time

                if new_dist < distances[neighbor.id]:
                    distances[neighbor.id] = new_dist
                    previous_edge[neighbor.id] = edge.id
                    heapq.heappush(pq, (new_dist, neighbor.id))

        # Reconstruct path as list of edge IDs
        path: list[int] = []
        current = end_id

        while current != start_id:
            edge_id = previous_edge[current]
            if edge_id is None:
                return []  # no path

            path.append(edge_id)
            edge = next(e for e in self.edges if e.id == edge_id)
            # move to the other node in the edge
            current = edge.nodes[0].id if edge.nodes[1].id == current else edge.nodes[1].id

        path.reverse()
        return path

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

