from objects import InitialParameters, Status
from graph import Graph, Node, Edge
from agents.agent import Agent
from time import sleep
import random
import pygame as pg
        

def compute_for_chance_of_infection(number_of_infected_contacts:int, chance_per_contact:float):
    chance_of_not_per_contact = 1 - chance_per_contact
    chance_of_not_infected = 1
    for _ in range(number_of_infected_contacts):
        chance_of_not_infected *= chance_of_not_per_contact
    return round(1 - chance_of_not_infected, 4)

def create_test_graph_complex() -> Graph:
    graph = Graph()

    # Create 10 nodes with positions
    n0 = Node(100, 150)
    n1 = Node(200,  80)
    n2 = Node(350, 100)
    n3 = Node(120, 250)
    n4 = Node(250, 160)
    n5 = Node(350, 220)
    n6 = Node(450, 180)
    n7 = Node(240, 300)
    n8 = Node(390, 300)
    n9 = Node(260, 380)

    nodes = [n0, n1, n2, n3, n4, n5, n6, n7, n8, n9]
    for n in nodes:
        graph.add_node(n)

    # Add edges (no crossings)
    edges = [
        (n0, n1), (n1, n2), (n0, n4), (n1, n4), (n2, n5),
        (n4, n5), (n0, n3), (n3, n7), (n4, n7), (n5, n8),
        (n7, n8), (n5, n6), (n6, n8), (n7, n9)
    ]

    for a, b in edges:
        graph.add_edge(random.randint(10, 20), a, b)

    return graph


class Simulation:
    compartments = ['S', 'E', 'I', 'R']
    initial_parameters:InitialParameters
    seir_compartments:dict[str, list[Agent]]
    graph:Graph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    average_infection_chance:list[float] = []

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        self.initial_parameters = initial_parameters
        self.seir_compartments = {}
        self.headless = headless
        self.graph = create_test_graph_complex()

        for compartment in self.compartments:
            l = []
            for _ in range(self.initial_parameters.no_per_compartment[compartment]):
                residence_node = random.choice(self.graph.nodes)
                firm_node = random.choice(self.graph.nodes)
                while (residence_node == firm_node):
                    firm_node = random.choice(self.graph.nodes)
                agent = Agent(self.graph, residence_node, firm_node, compartment)
                agent.set_path(self.graph.shortest_edge_path(residence_node.id, firm_node.id), firm_node)
                agent.set_state('travelling')
                l.append(agent)
                print(f"Agent id {agent.id} with path: {agent.path}")
            self.seir_compartments[compartment] = l

        
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 25)
    
    def generate_status(self) -> Status:
        pass

    def run(self):
        time = 0
        running = True
        while ((time // (60 * 24) < self.initial_parameters.duration) and running):
            print(f"time: {time}")
            if (self.average_infection_chance):
                ave = round(sum(self.average_infection_chance) / len(self.average_infection_chance), 4)
                print(f'Average infection chance: {ave}')
            if (not self.headless):
                for event in pg.event.get():
                    if event.type == pg.QUIT:
                        running = False 
                        break
            
            for agents in self.seir_compartments.values():
                for agent in agents:
                    if (agent.state == 'travelling'):
                        agent.traverse_graph(time, compute_for_chance_of_infection, self.initial_parameters.chance_per_contact, self)
            
            # Working adults go through their day
            # To go to their work and encounters other people (Base No. of contacts based on edges traveresed)
            # Upon reaching their destination check if the agent becomes exposed, infected, or such *Consider compartment time periods*
            # while working record the time the agent works. (This is recorded on the business node)
            if (not self.headless):
                self.window.fill((255, 255, 255))
                self.graph.draw(self.window, self.font)
                pg.display.update()
                self.clock.tick(60)
            time += 1
            sleep(0.5)
            pass


if __name__ == '__main__':
    Simulation(InitialParameters(365, {'S':20, 'E':0, 'I':20, 'R':10}), False).run()
