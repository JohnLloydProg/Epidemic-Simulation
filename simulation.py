from objects import InitialParameters, Status
from graph import Graph, Node, Edge
from agents.agent import Agent
import pygame as pg
import random


class Simulation:
    compartments = ['S', 'E', 'I', 'R']
    initial_parameters:InitialParameters
    seir_compartments:dict[str, list]
    graph:Graph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        self.initial_parameters = initial_parameters
        self.seir_compartments = {}
        self.headless = headless
        
        self.graph = Graph()
        self.graph.add_node(Node(50, 50))
        self.graph.add_node(Node(50, 100))
        self.graph.add_edge(0.5, 0, 1)

        for compartment in self.compartments:
            self.seir_compartments[compartment] = [Agent() for i in range(self.initial_parameters.no_per_compartment[compartment])]
        
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 25)
    
    def roll_for_infection(self, agent:Agent, chance_of_infection:float=0.5):
        if (agent.SEIR_compartment != 'S'):
            raise RuntimeError(f"Agent is not under the S compartment. Can't roll for infection.")
        
        if (random.random() <= chance_of_infection):
            agent.SEIR_compartment = self.compartments[1]
    
    def generate_status(self) -> Status:
        pass

    def run(self):
        counter = 0
        running = True
        while ((counter // (60 * 24) < self.initial_parameters.duration) and running):
            if (not self.headless):
                for event in pg.event.get():
                    if event.type == pg.QUIT:
                        running = False 
                        break
            # Working adults go through their day
            # To go to their work and encounters other people (Base No. of contacts based on edges traveresed)
            # Upon reaching their destination check if the agent becomes exposed, infected, or such *Consider compartment time periods*
            # while working record the time the agent works. (This is recorded on the business node)
            if (not self.headless):
                self.window.fill((255, 255, 255))
                self.graph.draw(self.window, self.font)
                pg.display.update()
                self.clock.tick(60)
            counter += 1
            pass


if __name__ == '__main__':
    Simulation(InitialParameters(365, {'S':300, 'E':150, 'I':60, 'R':10}), False).run()
