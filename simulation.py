from objects import InitialParameters, Status
from concurrent.futures import ThreadPoolExecutor, wait
from functools import partial
from multiprocessing import Process, Pool
from graphing.mapping import load_graph
from graphing.graph import Graph
from agents.agent import Agent, WorkingAgent
from agents import events
from time import time_ns
from datetime import datetime
from dotenv import load_dotenv
import random
import pygame as pg
import event as sim_event
import os


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
    compartments = ['S', 'E', 'I', 'R', 'D']
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
        self.time_step = int(os.environ.get('TIME_STEP', '2'))
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
            
    
    def run(self):
        delta = 0
        draw_time = 0
        simultation_time = 0
        running = True

        printProgressBar(0, 365, prefix = 'Progress:', suffix = 'Complete', length = 50)
        with ThreadPoolExecutor() as agent_executor, ThreadPoolExecutor() as edge_executor:
            while ((self.time // (60 * 24) < self.initial_parameters.duration) and running):
                minute = self.time % 60
                hour = (self.time // 60) % 24
                day = self.time // (60 * 24)
                time_record = time_ns()
                futures = []

                if (not self.headless):
                    for event in pg.event.get():
                        if (event.type == pg.QUIT):
                            agent_executor.shutdown(cancel_futures=True)
                            edge_executor.shutdown(cancel_futures=True)
                            running = False
                            return
                        elif (event.type == pg.KEYDOWN):
                            if (event.key == pg.K_p):
                                status = self.generate_status(self.time)
                                Process(None, status.display_report).start()
                        self.graph.map_dragging(event)

                if (hour == 0 and minute == 0):
                    printProgressBar(day, 365, prefix = 'Progress:', suffix = 'Complete', length = 50)
                 
                for event in sim_event.get(self.time):
                    agent_batches = [event.agents[i:i+self.batch_size] for i in range(0, len(event.agents), self.batch_size)]
                    if (event.type == sim_event.AGENT_GO_WORK):
                        go_work_partial = partial(events.go_work, initial_parameters=self.initial_parameters, time=self.time)
                        for agent_batch in agent_batches:
                            futures.append(agent_executor.submit(go_work_partial, agent_batch))
                    elif (event.type == sim_event.AGENT_TRAVERSE):
                        traverse_partial = partial(events.traverse, initial_parameters=self.initial_parameters, time=self.time)
                        for agent_batch in agent_batches:
                            futures.append(agent_executor.submit(traverse_partial, agent_batch))
                    elif (event.type == sim_event.AGENT_GO_HOME):
                        go_home_partial = partial(events.go_home, initial_parameters=self.initial_parameters, time=self.time)
                        for agent_batch in agent_batches:
                            futures.append(agent_executor.submit(go_home_partial, agent_batch))
                    elif (event.type == sim_event.AGENT_INFECTED):
                        infected_partial = partial(events.infected, initial_parameters=self.initial_parameters, time=self.time)
                        for agent_batch in agent_batches:
                            futures.append(agent_executor.submit(infected_partial, agent_batch))
                    elif (event.type == sim_event.AGENT_REMOVED):
                        remove_agents_partial = partial(events.remove_agents, initial_parameters=self.initial_parameters)
                        for agent_batch in agent_batches:
                            futures.append(agent_executor.submit(remove_agents_partial, agent_batch))

                edge_batches = [self.graph.edges[i:i+10] for i in range(0, len(self.graph.edges), 10)]
                contact_pairing_partial = partial(events.contact_pairing, initial_parameters=self.initial_parameters, time=self.time)
                if (not self.headless):
                    if (time_ns() - simultation_time >= self.simulation_ns_per_time_unit):
                        for edge_batch in edge_batches:
                            futures.append(edge_executor.submit(contact_pairing_partial, edge_batch))
                        wait(futures)
                        simultation_time = time_ns()
                        self.time += self.time_step
                else:
                    for edge_batch in edge_batches:
                        futures.append(edge_executor.submit(contact_pairing_partial, edge_batch))
                    wait(futures)
                    self.time += self.time_step
                
                delta = (time_ns() - time_record) / (10**6)
                
                if (not self.headless and time_ns() - draw_time >= (10**9)//60):
                    draw_time = time_ns()
                    self.window.fill((255, 255, 255))
                    self.graph.draw(self.window, self.font)
                    text = self.font.render(f"time: {self.time} (Day {day} {hour}:{minute}) {round(delta, 2)}ms per step {len(sim_event._events.values())} events", False, (0, 0, 0))
                    pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                    self.window.blit(text, text.get_rect(topright=(1060, 20)))
                    pg.display.update()
        

if __name__ == '__main__':
    load_dotenv()
    print(datetime.now().isoformat())
    Simulation(InitialParameters(365, {'S':250000, 'E':0, 'I':1000, 'R':0, 'D':0}), False).run()
    print(datetime.now().isoformat())
