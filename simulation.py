from objects import InitialParameters, Status
from const import ENHANCED_CQ, MODIFIED_ENHANCED_CQ, GENERAL_CQ, MODIFIED_GENERAL_CQ
from concurrent.futures import ThreadPoolExecutor, wait, Future
from functools import partial, lru_cache
from multiprocessing import Process
from graphing.mapping import load_graph
from graphing.graph import Graph
from agents.agent import Agent, WorkingAgent
from agents.sector import Firm, Household
from agents import events
from time import time_ns
from datetime import datetime
from dotenv import load_dotenv
import random
import pygame as pg
import event as sim_event
import logging
import math
import os
import sys

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

def daily_work(agents:list[Agent], time:int) -> set[int]:
    will_work = set()
    for agent in agents:
        if (agent.SEIR_compartment == 'D' or (agent.SEIR_compartment == 'I' and agent.symptomatic)):
            continue
        work_event = sim_event.AgentEvent(sim_event.AGENT_GO_WORK, agent)
        sim_event.emit(next_occurrence_of_hour(time, agent.working_hours[0] - 1), work_event)
        will_work.add(agent.id)
    return will_work


class Simulation:
    compartments = ['S', 'E', 'I', 'R', 'D']
    agents:list[Agent]
    graph:Graph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    infection_chances:list[float] = []
    simulation_ns_per_time_unit = (10**9)//40 # 1/<number of minutes in simulation per 1 second in real time>
    logger = logging.getLogger('simulation')
    quarantine = MODIFIED_ENHANCED_CQ
    batch_size:int = 1000
    activity = 0
    budget = 0

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        logging.basicConfig(handlers=[logging.FileHandler("logfile.txt", 'w'), logging.StreamHandler(sys.stdout)], level=logging.INFO if os.environ.get('DEBUG', 'False') == 'True' else logging.WARNING)
        self.logger.info(f'Initializing simulation with headless = {headless}...')
        sim_event.init()
        self.initial_parameters = initial_parameters
        self.time_step = int(os.environ.get('TIME_STEP', '2'))
        self.agents = []
        self.headless = headless
        self.graph = load_graph()
        self.generate_agents()
        self.logger.info(f'Simulation initialized with {len(self.agents)} agents.')
        
        if (not headless):
            pg.init()
            self.clock = pg.time.Clock()
            self.window = pg.display.set_mode((1080, 720))
            self.font = pg.font.Font(None, 15)
    
    def generate_agents(self):
        self.logger.info('generating agents...')
        for household in self.graph.get_households():
            for _ in range(household.resident_count):
                agent = WorkingAgent(self.graph, household, (8, 17))
                household.resident_agents.append(agent)
                self.agents.append(agent)
        
        self.logger.info('assigning firms to agents...')
        firms = self.graph.get_firms()
        for agent in self.agents:
            firm = random.choice(firms)
            while (len(firm.resident_agents) >= firm.max_capacity):
                firm = random.choice(firms)
            agent.firm = firm
            firm.resident_agents.append(agent)
        
        #firm analysis
        occupany_ratios = []
        for firm in firms:
            occupany_ratios.append((len(firm.resident_agents) / firm.max_capacity) * 100)
        self.logger.info(f'Firm occupancy ratios: min={min(occupany_ratios)}%, max={max(occupany_ratios)}%, avg={sum(occupany_ratios)/len(occupany_ratios)}%, std={math.sqrt(sum((x - (sum(occupany_ratios)/len(occupany_ratios)))**2 for x in occupany_ratios)/len(occupany_ratios))}%')

        self.logger.info('assigning initial infections...')
        assigned = set()
        for compartment in self.compartments:
            un_assigned_agents = list(filter(lambda agent: agent.id not in assigned, self.agents))
            agents = random.sample(un_assigned_agents, self.initial_parameters.no_per_compartment.get(compartment, 0))
            for agent in agents:
                agent.SEIR_compartment = compartment
                
                if (compartment == 'I'):
                    agent.symptomatic = random.random() < 0.6
                    remove_event = sim_event.AgentEvent(sim_event.AGENT_REMOVED, agent)
                    sim_event.emit(math.ceil(self.initial_parameters.sample_infected_duration()), remove_event)
                elif (compartment == 'E'):
                    infection_event = sim_event.AgentEvent(sim_event.AGENT_INFECTED, agent)
                    sim_event.emit(math.ceil(self.initial_parameters.sample_incubation_period()), infection_event)
                
                assigned.add(agent.id)
    
    def generate_status(self, time:int) -> Status:
        seir = {compartment:0 for compartment in self.compartments}
        for agent in self.agents:
            seir[agent.SEIR_compartment] += 1
        status = Status(time, seir)
        return status

    def handle_events(self, time:int, executor:ThreadPoolExecutor) -> list[Future]:
        futures:list[Future] = []

        for event in sim_event.get(time):
            if (isinstance(event, sim_event.AgentEvent)):
                event_agents = event.get_agents()
                agent_batches = [event_agents[i:i+self.batch_size] for i in range(0, len(event_agents), self.batch_size)]
                if (event.type == sim_event.AGENT_GO_WORK):
                    go_work_partial = partial(events.go_work, initial_parameters=self.initial_parameters, time=time, quarantine_level=self.quarantine)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(go_work_partial, agent_batch))
                elif (event.type == sim_event.AGENT_TRAVERSE):
                    traverse_partial = partial(events.traverse, time=time, time_step=self.time_step)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(traverse_partial, agent_batch))
                elif (event.type == sim_event.AGENT_GO_HOME):
                    go_home_partial = partial(events.go_home, initial_parameters=self.initial_parameters, time=time, quarantine_level=self.quarantine)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(go_home_partial, agent_batch))
                elif (event.type == sim_event.AGENT_INFECTED):
                    infected_partial = partial(events.infected, initial_parameters=self.initial_parameters, time=time)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(infected_partial, agent_batch))
                elif (event.type == sim_event.AGENT_REMOVED):
                    remove_agents_partial = partial(events.remove_agents, initial_parameters=self.initial_parameters, time=time)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(remove_agents_partial, agent_batch))
                elif (event.type == sim_event.EDGE_INFECTION and self.quarantine not in {ENHANCED_CQ, MODIFIED_ENHANCED_CQ}):
                    edge_infection_partial = partial(events.edge_infection, time=time, initial_parameters=self.initial_parameters, time_step=self.time_step, quarantine_level=self.quarantine)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(edge_infection_partial, agent_batch))
                elif (event.type == sim_event.AGENT_GO_SHOPPING):
                    go_shopping_partial = partial(events.shopping_agents, time=time, initial_parameters=self.initial_parameters, quarantine_level=self.quarantine)
                    for agent_batch in agent_batches:
                        futures.append(executor.submit(go_shopping_partial, agent_batch))
            elif (isinstance(event, sim_event.FirmEvent)):
                event_firms = event.get_firms()
                firm_batches = [event_firms[i:i+100] for i in range(0, len(event_firms), 100)]
                if (event.type == sim_event.FIRM_ACTIVITY_COLLECTION):
                    firm_activity_partial = partial(events.firm_activity_collection, time=time)
                    activity_collection:list[Future] = []
                    for firm_batch in firm_batches:
                        activity_collection.append(executor.submit(firm_activity_partial, firm_batch))
                    
                    for activity_future in activity_collection:
                        work_total, cons_total = activity_future.result()
                        self.activity += work_total + cons_total
                        self.budget += (work_total * 0.15) + (cons_total * 0.12)
        
        return futures
    
    def get_agent_states(self) -> dict[str, int]:
        states = {}
        for agent in self.agents:
            states[agent.state] = states.get(agent.state, 0) + 1
        return states
    
    def run(self):
        time = 0
        delta = 0
        draw_time = 0
        simultation_time = 0
        status = None
        simulation_day_time = time_ns()
        running = True
        states = self.get_agent_states()

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
                    
                    will_work:set[int] = set()
                    if (not self.quarantine):
                        agent_batches = [self.agents[i:i+self.batch_size] for i in range(0, len(self.agents), self.batch_size)]
                        set_for_work_partial = partial(daily_work, time=time)
                        futures:list[Future] = []
                        for agent_batch in agent_batches:
                            futures.append(agent_executor.submit(set_for_work_partial, agent_batch))

                        for future in futures:
                            will_work.update(future.result())
                    elif (self.quarantine in {ENHANCED_CQ, MODIFIED_ENHANCED_CQ}):
                        # implement covid tests
                        for firm in self.graph.get_firms():
                            if (firm.essential):
                                will_work.update(daily_work(firm.resident_agents, time))
                            else:
                                if (self.quarantine == MODIFIED_ENHANCED_CQ):
                                    max_capacity = int(0.5 * firm.max_capacity)
                                    if (len(firm.resident_agents) < max_capacity):
                                        agents = firm.resident_agents
                                    else:
                                        agents = random.sample(firm.resident_agents, max_capacity)
                                    will_work.update(daily_work(agents, time))

                    if (self.quarantine in {ENHANCED_CQ, MODIFIED_ENHANCED_CQ}):
                        compartments_not_allowed = {'D', 'I'}
                        for household in self.graph.get_households():
                            if (random.random() < 0.3):
                                agents = list(filter(lambda agent: (agent.SEIR_compartment not in compartments_not_allowed or (agent.SEIR_compartment == 'I' and not agent.symptomatic)), household.resident_agents))
                                if (not agents):
                                    continue
                                agent:WorkingAgent = random.choice(agents)
                                if (agent.id in will_work):
                                    agent.errand_run = True
                                else:
                                    suply_run = sim_event.AgentEvent(sim_event.AGENT_GO_SHOPPING, agent)
                                    sim_event.emit(time + (random.randrange(10, 21) * 60), suply_run)


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
                        states = self.get_agent_states()

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
                    states_text = self.font.render(f"States: {states}", False, (0, 0, 0))
                    self.window.blit(states_text, states_text.get_rect(topleft=(20, 40)))
                    metric_text = self.font.render(f"Activity: {self.activity}, Budget: {self.budget}", False, (0, 0, 0))
                    pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                    self.window.blit(metric_text, metric_text.get_rect(topleft=(20, 20)))
                    self.window.blit(text, text.get_rect(topright=(1060, 20)))
                    pg.display.update()
        

if __name__ == '__main__':
    load_dotenv()
    print(datetime.now().isoformat())
    Simulation(InitialParameters(365, {'I':1000}), False).run()
    print(datetime.now().isoformat())
