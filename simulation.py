from objects import InitialParameters, Status
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process, Condition, Value, Queue, Lock, Manager
from functools import  partial
from graphing.mapping import load_graph
from graphing.graph import Graph, GraphDrawing
from agents.agent import Agent, WorkingAgent
from time import time_ns, sleep
from pympler import asizeof
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
    working_agents:list[WorkingAgent]
    non_working_agents:list[Agent]
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    infection_chances:list[float] = []
    simulation_ns_per_time_unit = (10**9)//40 # 1/<number of minutes in simulation per 1 second in real time>
    batch_size:int = 1000
    batches:list[list]

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        self.initial_parameters = initial_parameters
        self.working_agents = []    
        self.non_working_agents = []
        self.headless = headless
        self.manager = Manager()
        self.graph = load_graph()
        self.batches = [[]]
        self.total_workers = 3
        
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 15)
    
    def generate_agents(self) -> list[Agent]:
        households = []
        firms = []
        for region in self.graph.get_regions():
            households.extend(region.households)
            firms.extend(region.firms)

        counter = 0
        batches = [[]]
        batch_size = round(sum(self.initial_parameters.no_per_compartment.values())/self.total_workers)
        for compartment in self.compartments:
            for _ in range(self.initial_parameters.no_per_compartment[compartment]):
                household = random.choice(households)
                firm = random.choice(firms)
                agent = WorkingAgent(self.graph, household, firm, (8, 17), compartment=compartment)
                self.working_agents.append(agent)
                batches[-1].append(agent)
                counter += 1
                if (counter == batch_size):
                    batches.append([])
                    counter = 0
        return batches
    
    def generate_status(self, time:int, queue:Queue) -> Status:
        seir = {compartment:0 for compartment in self.compartments}
        for i in range(self.total_workers):
            for agent in queue.get():
                seir[agent.SEIR_compartment] += 1

        status = Status(time, seir)
        return status
            
    
    def main_run(self):
        time = Value('i', 360)
        cond = Condition()
        counter = Value('i', 0)
        draw_time = 0
        running = True

        batches = self.generate_agents()
        agents_queue = Queue()
        lock = Lock()
        workers = [Process(target=run, args=(batches[i], self.graph, self.initial_parameters, cond, time, counter, self.total_workers, agents_queue, lock)) for i in range(self.total_workers)]

        for worker in workers:
            worker.start()
        
        simultation_time = 0

        sleep(5)
        print('simulation starting')
        graph_drawing = GraphDrawing(self.graph)
        latest_status:Status = None
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
                        if (event.key == pg.K_p and latest_status):
                            Process(None, latest_status.display_report).start()
                    graph_drawing.map_dragging(event)
                
            with cond:
                with lock:
                    counter.value = 0
                cond.notify_all()


                cond.wait()
                with lock:
                    time.value += 1
            
            if (hour == 0 and minute == 0):
                for edge in self.graph.get_edges():
                    print (f"Edge {edge.id}: {edge.no_infected.value}")

            while not (time_ns() - simultation_time >= self.simulation_ns_per_time_unit):
                sleep(0.0001)
            simultation_time = time_ns()
            
            if (not self.headless and time_ns() - draw_time >= (10**9)//60):
                draw_time = time_ns()
                self.window.fill((255, 255, 255))
                graph_drawing.draw(self.window, self.font)
                delta = (time_ns() - time_record) / (10**6)
                text = self.font.render(f"time: {time.value} (Day {day} {hour}:{minute}) {round(delta, 2)}ms per step {len(sim_event._events.values())} events", False, (0, 0, 0))
                pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                self.window.blit(text, text.get_rect(topright=(1060, 20)))
                pg.display.update()
        
        for worker in workers:
            worker.terminate()
            worker.join()

def go_work(agent:WorkingAgent, time:int):
    agent.go_work(time)
    
def go_home(agent:Agent, time:int):
    agent.go_home(time)
    
def traverse(agent:Agent, time:int, initial_parameters:InitialParameters):
    agent.traverse_graph(time, initial_parameters)
    
def infected(agent:Agent):
    agent.SEIR_compartment = 'I'

def run(agents:list[Agent], graph:Graph, initial_parameters:InitialParameters, cond, shared_time, counter, total:int, agents_queue:Queue, lock):
    batch_size = 1000

    batches = [[]]
    batch_counter = 0
    for agent in agents:
        batches[-1].append(agent)
        batch_counter += 1
        if (isinstance(agent, WorkingAgent)):
            agent.ready_for_work()
        if (batch_counter == batch_size):
            batches.append([])
            batch_counter = 0

    with ThreadPoolExecutor() as executor:
        while ((shared_time.value // (60 * 24) < initial_parameters.duration)):
            with cond:
                cond.wait()

            minute = shared_time.value % 60
            hour = (shared_time.value // 60) % 24
            day = shared_time.value // (60 * 24)

            go_work_f = partial(go_work, time=shared_time.value)
            go_home_f = partial(go_home, time=shared_time.value)
            traverse_f = partial(traverse, time=shared_time.value, initial_parameters=initial_parameters)
        
            time_record = time_ns()
            for event in sim_event.get(shared_time.value):
                if (event.type == sim_event.AGENT_GO_WORK):
                    executor.map(go_work_f, event.agents)
                elif (event.type == sim_event.AGENT_TRAVERSE):
                    executor.map(traverse_f, event.agents)
                elif (event.type == sim_event.AGENT_GO_HOME):
                    executor.map(go_home_f, event.agents)
                elif (event.type == sim_event.AGENT_INFECTED):
                    executor.map(infected, event.agents)
            
            if (hour == 0 and minute == 0):
                agents_to_send = []
                for batch in batches:
                    agents_to_send.extend(batch)
                agents_queue.put(agents_to_send)

            with lock:
                counter.value += 1
            if (counter.value == total):
                with cond:
                    cond.notify()


        

if __name__ == '__main__':
    Simulation(InitialParameters(365, {'S':200000, 'E':0, 'I':50000, 'R':0}, chance_per_contact=0.2), False).main_run()
