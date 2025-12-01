from objects import InitialParameters, Status
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process
from graphing.mapping import load_graph
from graphing.graph import Graph
from agents.agent import Agent, WorkingAgent
from time import time_ns, sleep
from datetime import datetime
import random
import pygame as pg
import event as sim_event


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
    agents:list[Agent]
    graph:Graph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    infection_chances:list[float] = []
    simulation_ns_per_time_unit = (10**9)//40 # 1/<number of minutes in simulation per 1 second in real time>
    batch_size:int = 1000

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        self.initial_parameters = initial_parameters
        self.agents = []
        self.headless = headless
        self.graph = load_graph()
        self.generate_agents()
        self.time = 360
        
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

        for compartment in self.compartments:
            for _ in range(self.initial_parameters.no_per_compartment[compartment]):
                household = random.choice(households)
                firm = random.choice(firms)
                agent = WorkingAgent(self.graph, household, firm, (8, 17), compartment=compartment)
                self.agents.append(agent)
    
    def generate_status(self, time:int) -> Status:
        seir = {compartment:0 for compartment in self.compartments}
        for agent in self.agents:
            seir[agent.SEIR_compartment] += 1
        status = Status(time, seir)
        return status

    def go_work(self, agents:list[WorkingAgent]):
        for agent in agents:
            agent.go_work(self.time)
        
    def go_home(self, agents:list[Agent]):
        for agent in agents:
            agent.go_home(self.time)
        
    def traverse(self, agents:list[Agent]):
        for agent in agents:
            agent.traverse_graph(self.time, self.initial_parameters)
        
    def infected(self, agents:list[Agent]):
        for agent in agents:
            agent.SEIR_compartment = 'I'
    
    def run(self):
        draw_time = 0
        simultation_time = 0
        running = True

        printProgressBar(0, 365, prefix = 'Progress:', suffix = 'Complete', length = 50)
        with ThreadPoolExecutor() as executor:
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
                                status = self.generate_status(self.time)
                                Process(None, status.display_report).start()
                        self.graph.map_dragging(event)

                if (hour == 0 and minute == 0):
                    printProgressBar(day, 365, prefix = 'Progress:', suffix = 'Complete', length = 50)
                
                for event in sim_event.get(self.time):
                    batches = [event.agents[i:i+self.batch_size] for i in range(0, len(event.agents), self.batch_size)]
                    if (event.type == sim_event.AGENT_GO_WORK):
                        executor.map(self.go_work, batches)
                    elif (event.type == sim_event.AGENT_TRAVERSE):
                        executor.map(self.traverse, batches)
                    elif (event.type == sim_event.AGENT_GO_HOME):
                        executor.map(self.go_home, batches)
                    elif (event.type == sim_event.AGENT_INFECTED):
                        executor.map(self.infected, batches)
                    
                
                if (not self.headless and time_ns() - draw_time >= (10**9)//60):
                    draw_time = time_ns()
                    self.window.fill((255, 255, 255))
                    self.graph.draw(self.window, self.font)
                    delta = (time_ns() - time_record) / (10**6)
                    text = self.font.render(f"time: {self.time} (Day {day} {hour}:{minute}) {round(delta, 2)}ms per step {len(sim_event._events.get(self.time, []))} events", False, (0, 0, 0))
                    pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                    self.window.blit(text, text.get_rect(topright=(1060, 20)))
                    pg.display.update()

                if (not self.headless):
                    while not (time_ns() - simultation_time >= self.simulation_ns_per_time_unit):
                        sleep(0.0001)
                    simultation_time = time_ns()
                    self.time += 1
                else:
                    self.time += 1


        

if __name__ == '__main__':
    print(datetime.now().isoformat())
    Simulation(InitialParameters(365, {'S':200000, 'E':0, 'I':50000, 'R':0}, chance_per_contact=0.2), False).run()
    print(datetime.now().isoformat())
