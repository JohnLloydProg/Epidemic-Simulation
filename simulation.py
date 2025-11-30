from objects import InitialParameters, Status
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.managers import BaseManager
from multiprocessing import Process, Condition, Value
from functools import lru_cache
from graphing.mapping import load_graph
from graphing.graph import Graph, GraphDrawing
from agents.agent import Agent, WorkingAgent
from time import time_ns, sleep
from pympler import asizeof
import random
import pygame as pg

@lru_cache(maxsize=128, typed=False)
def next_occurrence_of_hour(current_time, target_hour):
    MIN_PER_DAY = 1440
    current_minute_within_day = current_time % MIN_PER_DAY
    target_minute_within_day = target_hour * 60

    if target_minute_within_day > current_minute_within_day:
        # occurs later today
        return current_time - current_minute_within_day + target_minute_within_day
    else:
        # occurs tomorrow
        return (current_time - current_minute_within_day +
                MIN_PER_DAY + target_minute_within_day)


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


class GraphManager(BaseManager):
    pass

GraphManager.register('Graph', load_graph)


class Simulation:
    compartments = ['S', 'E', 'I', 'R']
    working_agents:list[WorkingAgent]
    non_working_agents:list[Agent]
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
        self.graph_manager = GraphManager()
        self.batches = [[]]
        self.graph_manager.start()
        self.total_workers = 3
        
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 15)
    
    def generate_agents(self) -> list[Agent]:
        graph:Graph = self.graph_manager.Graph()
        households = []
        firms = []
        for region in graph.get_regions():
            households.extend(region.households)
            firms.extend(region.firms)

        counter = 0
        batches = [[]]
        batch_size = round(sum(self.initial_parameters.no_per_compartment.values())/self.total_workers)
        for compartment in self.compartments:
            for _ in range(self.initial_parameters.no_per_compartment[compartment]):
                household = random.choice(households)
                firm = random.choice(firms)
                agent = WorkingAgent(graph, household, firm, (8, 17), compartment=compartment)
                self.working_agents.append(agent)
                batches[-1].append(agent)
                counter += 1
                if (counter == batch_size):
                    batches.append([])
                    counter = 0
        return batches
    
    def generate_status(self, time:int) -> Status:
        seir = {compartment:0 for compartment in self.compartments}
        for working_agent in self.working_agents:
            seir[working_agent.SEIR_compartment] += 1
        for non_working_agent in self.non_working_agents:
            seir[non_working_agent.SEIR_compartment] += 1

        status = Status(time, seir)
        return status
            
    
    def main_run(self):
        time = Value('i', 360)
        cond = Condition()
        counter = Value('i', 0)
        draw_time = 0
        running = True

        graph:Graph = self.graph_manager.Graph()
        batches = self.generate_agents()
        workers = [Process(target=run, args=(batches[i], graph, self.initial_parameters, cond, time, counter, self.total_workers)) for i in range(self.total_workers)]
    
        for worker in workers:
            worker.start()
        
        simultation_time = 0

        sleep(5)
        print('simulation starting')
        graph_drawing = GraphDrawing(graph)
        while (running):
            minute = time.value % 60
            hour = (time.value // 60) % 24
            day = time.value // (60 * 24)
            time_record = time_ns()
            if (not self.headless):
                for event in pg.event.get():
                    if (event.type == pg.QUIT):
                        running = False 
                        break
                    elif (event.type == pg.KEYDOWN):
                        if (event.key == pg.K_p):
                            status = self.generate_status(time.value)
                            Process(None, status.display_report).start()
                    graph_drawing.map_dragging(event)
                
            with cond:
                counter.value = 0
                cond.notify_all()

                cond.wait()
                time.value += 1
            sleep(0.0001)
            simultation_time = time_ns()
            
            if (not self.headless and time_ns() - draw_time >= (10**9)//60):
                draw_time = time_ns()
                self.window.fill((255, 255, 255))
                graph_drawing.draw(self.window, self.font)
                delta = (time_ns() - time_record) / (10**6)
                text = self.font.render(f"time: {time.value} (Day {day} {hour}:{minute}) {round(delta, 2)}ms per step", False, (0, 0, 0))
                pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                self.window.blit(text, text.get_rect(topright=(1060, 20)))
                pg.display.update()
        
        for worker in workers:
            worker.terminate()
            worker.join()

def run(agents:list[Agent], graph:Graph, initial_parameters:InitialParameters, cond, shared_time, counter, total:int,):
    batch_size = 1000
    time = shared_time.value

    batches = [[]]
    batch_counter = 0
    for agent in agents:
        batches[-1].append(agent)
        batch_counter += 1
        if (batch_counter == batch_size):
            batches.append([])
            batch_counter = 0

    with ThreadPoolExecutor() as executor:
        while ((time // (60 * 24) < initial_parameters.duration)):
            time = shared_time.value
            with cond:
                cond.wait()

            minute = time % 60
            hour = (time // 60) % 24
            day = time // (60 * 24)
            
            def handle(agents:list[Agent]):
                for agent in agents:
                    if (agent.state == 'home'):
                        if (isinstance(agent, WorkingAgent)):
                            if (hour == agent.working_hours[0] - 1):
                                agent.set_path(graph.shortest_edge_path(agent.household.node.id, agent.firm.node.id), agent.firm)
                                agent.set_state('travelling')
                    elif (agent.state == 'travelling'):
                        agent.traverse_graph(time, initial_parameters.chance_per_contact)
                    elif (agent.state == 'working' and isinstance(agent, WorkingAgent)):
                        agent.working(hour)
                    agent.time_event(time, initial_parameters)
        
            time_record = time_ns()
            executor.map(handle, batches)

            with cond:
                counter.value += 1
                if (counter.value == total):
                    cond.notify()


        

if __name__ == '__main__':
    Simulation(InitialParameters(365, {'S':250000, 'E':0, 'I':1000, 'R':0}, chance_per_contact=0.2), False).main_run()
