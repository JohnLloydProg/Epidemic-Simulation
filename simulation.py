from objects import InitialParameters, Status
from graphing.graph import Graph, Region
from graphing.core import Node
from agents.agent import Agent, WorkingAgent
from agents.sector import Firm, Household
from time import time_ns, sleep
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

    # Add regions (no overlapping)
    regions = [
        [n0, n4, n1], [n1, n4, n2, n5], [n0, n4, n3, n7],
        [n7, n4, n5, n8], [n5, n6, n8], [n5, n2, n6],
        [n7, n8, n9], [n3, n7, n9]
    ]

    for region in regions:
        region = Region(region)
        for i in range(random.randint(5, 10)):
            region.add_household()
        for i in range(random.randint(1, 5)):
            region.add_firm()
        graph.regions.append(region)

    return graph


class Simulation:
    compartments = ['S', 'E', 'I', 'R']
    working_agents:list[WorkingAgent]
    non_working_agents:list[Agent]
    graph:Graph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    infection_chances:list[float] = []
    simulation_ns_per_time_unit = (10**9)//60 # 1/<number of minutes in simulation per 1 second in real time>

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        self.initial_parameters = initial_parameters
        self.working_agents = []
        self.non_working_agents = []
        self.headless = headless
        self.graph = create_test_graph_complex()
        self.generate_agents()
        
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 25)
    
    def generate_agents(self):
        households = []
        firms = []
        for region in self.graph.regions:
            households.extend(region.households)
            firms.extend(region.firms)

        for compartment in self.compartments:
            for _ in range(self.initial_parameters.no_per_compartment[compartment]):
                household = random.choice(households)
                firm = random.choice(firms)
                agent = WorkingAgent(self.graph, household, firm, (8, 17), compartment=compartment)
                self.working_agents.append(agent)
    
    def generate_status(self) -> Status:
        seir = {compartment:0 for compartment in self.compartments}
        for working_agent in self.working_agents:
            seir[working_agent.SEIR_compartment] += 1
        for non_working_agent in self.non_working_agents:
            seir[non_working_agent.SEIR_compartment] += 1

        status = Status(self.time, seir)
        return status

    def run(self):
        self.time = 360
        draw_time = 0
        simulation_time = 0
        running = True
        while ((self.time // (60 * 24) < self.initial_parameters.duration) and running):
            minute = self.time % 60
            hour = (self.time // 60) % 24
            day = self.time // (60 * 24)
            if (not self.headless):
                for event in pg.event.get():
                    if event.type == pg.QUIT:
                        running = False 
                        break
                    self.graph.map_dragging(event)

            if (hour == 0 and minute == 0):
                status = self.generate_status()
                status.display_report()
            
            for working_agent in self.working_agents:
                if (working_agent.state == 'home'):
                    if (hour == working_agent.working_hours[0] - 1):
                        print(f"Agent {working_agent.id}: Going to work")
                        working_agent.set_path(self.graph.shortest_edge_path(working_agent.household.node.id, working_agent.firm.node.id), working_agent.firm)
                        working_agent.set_state('travelling')
                elif (working_agent.state == 'travelling'):
                    working_agent.traverse_graph(self.time, compute_for_chance_of_infection, self.initial_parameters.chance_per_contact)
                elif (working_agent.state == 'working'):
                    working_agent.working(hour)
                working_agent.time_event(self.time, self.initial_parameters)
            for non_working_agent in self.non_working_agents:
                if (non_working_agent.state == 'home'):
                    pass
                elif (non_working_agent.state == 'travelling'):
                    non_working_agent.traverse_graph(self.time, compute_for_chance_of_infection, self.initial_parameters.chance_per_contact)
                non_working_agent.time_event(self.time, self.initial_parameters)

            if (not self.headless and time_ns() - draw_time >= (10**9)//60):
                draw_time = time_ns()
                self.window.fill((255, 255, 255))
                self.graph.draw(self.window, self.font)
                text = self.font.render(f"time: {self.time} (Day {day} {hour}:{minute})", False, (0, 0, 0))
                pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                self.window.blit(text, text.get_rect(topright=(1060, 20)))
                pg.display.update()

            if (not self.headless):
                while (not (time_ns() - simulation_time >= self.simulation_ns_per_time_unit)):
                    sleep(0.0001)
                simulation_time = time_ns()
                self.time += 1
            else:
                self.time += 1

if __name__ == '__main__':
    Simulation(InitialParameters(365, {'S':70, 'E':10, 'I':30, 'R':20}), False).run()
