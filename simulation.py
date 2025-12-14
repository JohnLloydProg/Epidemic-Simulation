from objects import InitialParameters, Status
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, partial
from multiprocessing import Process, Pool
from graphing.mapping import load_graph
from graphing.graph import Graph
from graphing.core import Edge
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

@lru_cache(maxsize=None, typed=False)
def get_contact_duration(L: float, r: float, agent_1:Agent, agent_2:Agent) -> float:
    te1 = agent_1.started_travelling + L / agent_1.speed
    te2 = agent_2.started_travelling + L / agent_2.speed
    valid_start = max(agent_1.started_travelling, agent_2.started_travelling)
    valid_end = min(te1, te2)
    
    if valid_start >= valid_end:
        return 0.0 
    
    dv = agent_1.speed - agent_2.speed
    
    C = agent_1.speed * agent_1.started_travelling - agent_2.speed * agent_2.started_travelling
    
    if abs(dv) < 1e-9:
        dist = abs(agent_1.speed * (agent_2.started_travelling - agent_1.started_travelling))
        return (valid_end - valid_start) if dist <= r else 0.0

    t_sol_1 = (C + r) / dv
    t_sol_2 = (C - r) / dv
    
    t_contact_start = min(t_sol_1, t_sol_2)
    t_contact_end = max(t_sol_1, t_sol_2)

    final_start = max(valid_start, t_contact_start)
    final_end = min(valid_end, t_contact_end)
    
    return max(0.0, final_end - final_start)


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
            agent.go_work(self.time, self.initial_parameters)
        
    def go_home(self, agents:list[Agent]):
        for agent in agents:
            agent.go_home(self.time, self.initial_parameters)
        
    def traverse(self, agents:list[Agent]):
        for agent in agents:
            agent.traverse_graph(self.time, self.initial_parameters)
        
    def infected(self, agents:list[Agent]):
        for agent in agents:
            agent.SEIR_compartment = 'I'

    def contact_pairing(self, edges:list[Edge]):
        for edge in edges:
            positions = []
            for agent in edge.agents:
                positions.append((agent, agent.speed * (self.time - agent.started_travelling)))

            positions.sort(key=lambda x: x[1])
            for i in range(len(positions)):
                agent:Agent = positions[i][0]
                pos_1 = positions[i][1]
                for j in range(i+1, len(positions)):
                    contacted_agent:Agent = positions[j][0]
                    pos_2 = positions[j][1]
                    if (contacted_agent.SEIR_compartment not in ['I', 'S'] and agent.SEIR_compartment not in ['I', 'S']):
                        continue
                    if pos_2 - pos_1 > self.initial_parameters.contact_range:
                        break
                    if (get_contact_duration(edge.distance, self.initial_parameters.contact_range, agent, contacted_agent) > 3):
                        if (contacted_agent not in agent.contacted_agents and contacted_agent.SEIR_compartment == 'I'):
                            agent.contacted_agents.append(contacted_agent)
                        if (agent not in contacted_agent.contacted_agents and agent.SEIR_compartment == 'I'):
                            contacted_agent.contacted_agents.append(agent)

            
    
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
                        agent_executor.map(self.go_work, agent_batches)
                    elif (event.type == sim_event.AGENT_TRAVERSE):
                        agent_executor.map(self.traverse, agent_batches)
                    elif (event.type == sim_event.AGENT_GO_HOME):
                        agent_executor.map(self.go_home, agent_batches)
                    elif (event.type == sim_event.AGENT_INFECTED):
                        agent_executor.map(self.infected, agent_batches)

                edge_batches = [self.graph.edges[i:i+10] for i in range(0, len(self.graph.edges), 10)]
                if (not self.headless):
                    if (time_ns() - simultation_time >= self.simulation_ns_per_time_unit):
                        edge_executor.map(self.contact_pairing, edge_batches)
                        simultation_time = time_ns()
                        self.time += 2
                else:
                    edge_executor.map(self.contact_pairing, edge_batches)
                    self.time += 2
                
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
    print(datetime.now().isoformat())
    Simulation(InitialParameters(365, {'S':250000, 'E':0, 'I':1000, 'R':0})).run()
    print(datetime.now().isoformat())
