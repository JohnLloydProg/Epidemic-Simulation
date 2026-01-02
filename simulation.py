from objects import InitialParameters, Status
from concurrent.futures import ThreadPoolExecutor, wait
from functools import partial
from multiprocessing import Process
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
import math
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
        sim_event.init()
        self.initial_parameters = initial_parameters
        self.time_step = int(os.environ.get('TIME_STEP', '2'))
        self.agents = []
        self.headless = headless
        self.graph = load_graph()
        self.generate_agents()
        
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 15)
    
    def generate_agents(self):
        households = []
        firms = []
        for region in self.graph.regions.values():
            households.extend(region.households)
            firms.extend(region.firms)

        for compartment in self.compartments:
            for _ in range(self.initial_parameters.no_per_compartment[compartment]):
                household = random.choice(households)
                firm = random.choice(firms)
                agent = WorkingAgent(self.graph, household, firm, (8, 17), compartment=compartment)
                if (compartment == 'I'):
                    remove_event = sim_event.AgentEvent(sim_event.AGENT_REMOVED, agent)
                    sim_event.emit(math.ceil(self.initial_parameters.sample_infected_duration()), remove_event)
                elif (compartment == 'E'):
                    infection_event = sim_event.AgentEvent(sim_event.AGENT_INFECTED, agent)
                    sim_event.emit(math.ceil(self.initial_parameters.sample_incubation_period()), infection_event)
                work_event = sim_event.AgentEvent(sim_event.AGENT_GO_WORK, agent)
                sim_event.emit((agent.working_hours[0] - 1)*60, work_event)
                self.agents.append(agent)
    
    def generate_status(self, time:int) -> Status:
        seir = {compartment:0 for compartment in self.compartments}
        for agent in self.agents:
            seir[agent.SEIR_compartment] += 1
        status = Status(time, seir)
        return status

    def handle_events(self, time:int, executor:ThreadPoolExecutor) -> list:
        futures = []

        for event in sim_event.get(time):
            if (isinstance(event, sim_event.AgentEvent)):
                event_agents = event.get_agents()
                agent_batches = [event_agents[i:i+self.batch_size] for i in range(0, len(event_agents), self.batch_size)]
                if (event.type == sim_event.AGENT_GO_WORK):
                    go_work_partial = partial(events.go_work, initial_parameters=self.initial_parameters, time=time)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(go_work_partial, agent_batch))
                elif (event.type == sim_event.AGENT_TRAVERSE):
                    traverse_partial = partial(events.traverse, time=time, time_step=self.time_step)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(traverse_partial, agent_batch))
                elif (event.type == sim_event.AGENT_GO_HOME):
                    go_home_partial = partial(events.go_home, initial_parameters=self.initial_parameters, time=time)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(go_home_partial, agent_batch))
                elif (event.type == sim_event.AGENT_INFECTED):
                    infected_partial = partial(events.infected, initial_parameters=self.initial_parameters, time=time)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(infected_partial, agent_batch))
                elif (event.type == sim_event.AGENT_REMOVED):
                    remove_agents_partial = partial(events.remove_agents, initial_parameters=self.initial_parameters)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(remove_agents_partial, agent_batch))
                elif (event.type == sim_event.EDGE_INFECTION):
                    edge_infection_partial = partial(events.edge_infection, time=time, initial_parameters=self.initial_parameters, time_step=self.time_step)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(edge_infection_partial, agent_batch))
        
        return futures
    
    def run(self):
        time = 0
        delta = 0
        draw_time = 0
        simultation_time = 0
        status = None
        simulation_day_time = time_ns()
        running = True

        printProgressBar(0, 365, prefix = 'Progress:', suffix = 'Complete', length = 50)
        with ThreadPoolExecutor() as agent_executor:
            while ((time // (60 * 24) < self.initial_parameters.duration) and running):
                minute = time % 60
                hour = (time // 60) % 24
                day = time // (60 * 24)
                time_record = time_ns()

                if (hour == 0 and minute == 0):
                    status = self.generate_status(time)
                    day_delta = (time_ns() - simulation_day_time) / (10**9)
                    printProgressBar(day, 365, prefix = 'Progress:', suffix = f'Complete {day_delta} seconds per Simulation Day', length = 50)
                    simulation_day_time = time_ns()

                if (not self.headless):
                    for event in pg.event.get():
                        if (event.type == pg.QUIT):
                            agent_executor.shutdown(cancel_futures=True)
                            running = False
                            return
                        elif (event.type == pg.KEYDOWN):
                            if (event.key == pg.K_p and status):
                                Process(None, status.display_report).start()
                        self.graph.map_dragging(event)

                    if (time_ns() - simultation_time >= self.simulation_ns_per_time_unit):
                        futures = self.handle_events(time, agent_executor)

                        wait(futures)
                        simultation_time = time_ns()
                        delta = (time_ns() - time_record) / (10**6)
                        time += self.time_step
                else:
                    futures = self.handle_events(time, agent_executor)

                    wait(futures)
                    time += self.time_step
                
                if (not self.headless and time_ns() - draw_time >= (10**9)//60):
                    draw_time = time_ns()
                    self.window.fill((255, 255, 255))
                    self.graph.draw(self.window, self.font)
                    text = self.font.render(f"time: {time} (Day {day} {hour}:{minute}) {round(delta, 2)}ms per step {len(sim_event._events.values())} events", False, (0, 0, 0))
                    pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                    self.window.blit(text, text.get_rect(topright=(1060, 20)))
                    pg.display.update()
        

if __name__ == '__main__':
    load_dotenv()
    print(datetime.now().isoformat())
    Simulation(InitialParameters(365, {'S':250000, 'E':0, 'I':1000, 'R':0, 'D':0}), False).run()
    print(datetime.now().isoformat())
