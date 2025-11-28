from objects import InitialParameters, Status
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from multiprocessing import Process
from functools import lru_cache
from graphing.mapping import load_graph
from graphing.graph import Graph
from agents.agent import Agent, WorkingAgent
from datetime import datetime
from time import time_ns, sleep
import random
import pygame as pg
        
@lru_cache(maxsize=128, typed=False)
def compute_for_chance_of_infection(number_of_infected_contacts:int, chance_per_contact:float):
    chance_of_not_per_contact = 1 - chance_per_contact
    chance_of_not_infected = 1
    for _ in range(number_of_infected_contacts):
        chance_of_not_infected *= chance_of_not_per_contact
    return round(1 - chance_of_not_infected, 4)


def printProgressBar (iteration, total, prefix = '', suffix = '', decimals = 1, length = 100, fill = '█', printEnd = "\r"):
    """
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        length      - Optional  : character length of bar (Int)
        fill        - Optional  : bar fill character (Str)
        printEnd    - Optional  : end character (e.g. "\r", "\r\n") (Str)
    """
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    print(f'\r{prefix} |{bar}| {percent}% {suffix}', end = printEnd)
    # Print New Line on Complete
    if iteration == total: 
        print()


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
    batch_size:int = 1000
    batches:list[list]

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        self.initial_parameters = initial_parameters
        self.working_agents = []    
        self.non_working_agents = []
        self.headless = headless
        self.graph = load_graph('./cities/Mandaluyong')
        self.batches = [[]]
        self.generate_agents()
        
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 15)
    
    def generate_agents(self):
        households = []
        firms = []
        for region in self.graph.regions:
            households.extend(region.households)
            firms.extend(region.firms)

        counter = 0
        for compartment in self.compartments:
            for _ in range(self.initial_parameters.no_per_compartment[compartment]):
                household = random.choice(households)
                firm = random.choice(firms)
                agent = WorkingAgent(self.graph, household, firm, (8, 17), compartment=compartment)
                self.working_agents.append(agent)
                self.batches[-1].append(agent)
                counter += 1
                if (counter == self.batch_size):
                    self.batches.append([])
                    counter = 0
    
    def generate_status(self) -> Status:
        seir = {compartment:0 for compartment in self.compartments}
        for working_agent in self.working_agents:
            seir[working_agent.SEIR_compartment] += 1
        for non_working_agent in self.non_working_agents:
            seir[non_working_agent.SEIR_compartment] += 1

        status = Status(self.time, seir)
        return status

    def handle_working_angents(self, working_agents:list[WorkingAgent]) -> int:
        hour = (self.time // 60) % 24
        smallest_travel_time = float('inf')

        for working_agent in working_agents:
            if (working_agent.state == 'home'):
                if (hour == working_agent.working_hours[0] - 1):
                    working_agent.set_path(self.graph.shortest_edge_path(working_agent.household.node.id, working_agent.firm.node.id), working_agent.firm)
                    working_agent.set_state('travelling')
            elif (working_agent.state == 'travelling'):
                working_agent.traverse_graph(self.time, compute_for_chance_of_infection, self.initial_parameters.chance_per_contact)
                if (working_agent.travel_time < smallest_travel_time):
                    smallest_travel_time = working_agent.travel_time
            elif (working_agent.state == 'working'):
                working_agent.working(hour)
            working_agent.time_event(self.time, self.initial_parameters)
        
        return smallest_travel_time if smallest_travel_time != float('inf') or smallest_travel_time > 1 else 1

    def run(self):
        self.time = 360
        draw_time = 0
        simulation_time = 0
        running = True
        printProgressBar(0, 365, prefix = 'Progress:', suffix = 'Complete', length = 50)
        while ((self.time // (60 * 24) < self.initial_parameters.duration) and running):
            minute = self.time % 60
            hour = (self.time // 60) % 24
            day = self.time // (60 * 24)
            time_record = time_ns()

            if (not self.headless):
                for event in pg.event.get():
                    if (event.type == pg.QUIT):
                        running = False 
                        break
                    elif (event.type == pg.KEYDOWN):
                        if (event.key == pg.K_p):
                            status = self.generate_status()
                            Process(None, status.display_report).start()
                    self.graph.map_dragging(event)
            
            if (hour == 0 and minute == 0):
                printProgressBar(day, 365, prefix = 'Progress:', suffix = 'Complete', length = 50)
            
            smallest_travel_time = float('inf')

            with ThreadPoolExecutor() as executor:
                smallest_travel_time = min(executor.map(self.handle_working_angents, self.batches))
            
            for non_working_agent in self.non_working_agents:
                if (non_working_agent.state == 'home'):
                    pass
                elif (non_working_agent.state == 'travelling'):
                    non_working_agent.traverse_graph(self.time, compute_for_chance_of_infection, self.initial_parameters.chance_per_contact)
                    if (non_working_agent.travel_time < smallest_travel_time):
                        smallest_travel_time = non_working_agent.travel_time
                non_working_agent.time_event(self.time, self.initial_parameters)

            if (not self.headless and time_ns() - draw_time >= (10**9)//60):
                draw_time = time_ns()
                self.window.fill((255, 255, 255))
                self.graph.draw(self.window, self.font)
                delta = (time_ns() - time_record) / (10**6)
                text = self.font.render(f"time: {self.time} (Day {day} {hour}:{minute}) {round(delta, 2)}ms per step", False, (0, 0, 0))
                pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                self.window.blit(text, text.get_rect(topright=(1060, 20)))
                pg.display.update()

            if (not self.headless):
                while (not (time_ns() - simulation_time >= self.simulation_ns_per_time_unit)):
                    sleep(0.0001)
                simulation_time = time_ns()
                if smallest_travel_time == float('inf') or smallest_travel_time <= 1:
                    self.time += 1
                else:
                    self.time += int(smallest_travel_time)
            else:
                self.time += 1 if smallest_travel_time == float('inf') or smallest_travel_time <= 1 else int(smallest_travel_time)
        

if __name__ == '__main__':
    Simulation(InitialParameters(365, {'S':250000, 'E':0, 'I':1000, 'R':0}, chance_per_contact=0.2), False).run()
