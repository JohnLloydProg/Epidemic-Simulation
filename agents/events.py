from agents.agent import WorkingAgent, Agent
from simulation import next_occurrence_of_hour
from const import QUARANTINE_CR_PERCENTAGE
from agents.sector import Firm
from agents.agent import compute_for_chance_of_infection
from objects import InitialParameters
import functools
from graphing.core import Edge
import math
import event
import random
import os
import time

def timer(func):
    """A decorator that measures the execution time of a function."""
    @functools.wraps(func)  # Preserves the original function's metadata
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter() # Record the start time
        result = func(*args, **kwargs)   # Execute the original function
        end_time = time.perf_counter()   # Record the end time
        run_time = end_time - start_time
        if (run_time > 1.0):
            print(f"Function {func.__name__!r} took {run_time:.4f} seconds to complete.")
        return result
    return wrapper

def try_catch_wrapper(func):
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            print(f"Error happened in {func.__name__!r}: {e}")
            result = None
        return result
    return wrapper

@try_catch_wrapper
def go_work(agents:list[WorkingAgent], time:int, initial_parameters:InitialParameters, quarantine_level:int):
    for agent in agents:
        # TO ADD: factor to not go to work if infected or symptomatic or scared agent
        if (not agent.firm):
            raise ValueError("WorkingAgent has no assigned firm to go to work.")

        agent.set_path(agent.graph.shortest_edge_path(agent.household.node.id, agent.firm.node.id), agent.firm, time, initial_parameters, quarantine_level)
        agent.set_state('travelling')
        traversal_event = event.AgentEvent(event.AGENT_TRAVERSE, agent)
        event.emit(time + 1, traversal_event)

@try_catch_wrapper
def go_home(agents:list[Agent], time:int, initial_parameters:InitialParameters, quarantine_level:int):
    for agent in agents:
        agent.set_path(agent.graph.shortest_edge_path(agent.current_establishment.node.id, agent.household.node.id), agent.household, time, initial_parameters, quarantine_level)
        agent.set_state('travelling')
        traverse_event = event.AgentEvent(event.AGENT_TRAVERSE, agent)
        event.emit(time + 1, traverse_event)

@try_catch_wrapper
def traverse(agents:list[Agent], time:int, time_step:int):
    for agent in agents:
        assert agent.state == 'travelling', "Can't traverse if not travelling"
        
        if (agent.current_edge):
            if (time - agent.started_travelling >= agent.travel_time):
                nodes = agent.current_edge.nodes
                agent.current_node = nodes[0] if agent.current_node == nodes[1] else nodes[1]
                agent.current_edge.no_agents -= 1
                if (agent.SEIR_compartment == 'I'):
                    agent.current_edge.no_infected_agents -= 1
                agent.current_edge = None
            else:
                continue
        
        # Implement Transportation Method if commuting.
        if (agent.current_edge == None and agent.path and agent.destination):
            agent.current_edge = agent.graph.get_edge(agent.path.pop(0))
            agent.current_edge.no_agents += 1
            if (agent.SEIR_compartment == 'I'):
                agent.current_edge.no_infected_agents += 1
            agent.started_travelling = time
            agent.travel_time = math.ceil(agent.current_edge.distance / agent.speed)
            
            if (agent.commuting):
                edge_infection_event = event.AgentEvent(event.EDGE_INFECTION, agent)
                event.emit(time + time_step, edge_infection_event)
            traversal_event = event.AgentEvent(event.AGENT_TRAVERSE, agent)
            event.emit(time + agent.travel_time, traversal_event)
            continue

        if (agent.current_node == agent.destination.node):
            agent.current_establishment = agent.destination
            agent.current_establishment.no_agents += 1
            if (agent.SEIR_compartment == 'I'):
                agent.current_establishment.no_infected_agents += 1
            agent.arrival_time = time
            if (agent.destination == agent.household):
                agent.set_state('home')
            elif (isinstance(agent, WorkingAgent) and agent.destination == agent.firm):
                agent.set_state('working')
                agent.firm.attend(agent)
                
                # implement scare factor
                if (random.random() <= 0.2 or agent.errand_run):
                    next_event = event.AgentEvent(event.AGENT_GO_SHOPPING, agent)
                    agent.errand_run = False
                else:
                    next_event = event.AgentEvent(event.AGENT_GO_HOME, agent)
                event.emit(next_occurrence_of_hour(time, agent.working_hours[1]), next_event)
            elif (isinstance(agent.destination, Firm)):
                agent.set_state('consuming')
                agent.destination.serve(agent)
                go_home_event = event.AgentEvent(event.AGENT_GO_HOME, agent)
                event.emit(time + random.randrange(1, 3) * 60, go_home_event)

@try_catch_wrapper
def infected(agents:list[Agent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        agent.SEIR_compartment = 'I'
        agent.symptomatic = random.random() < 0.6  # 60% chance to be symptomatic
        remove_event = event.AgentEvent(event.AGENT_REMOVED, agent)
        event.emit(time + round(initial_parameters.sample_infected_duration()), remove_event)

@try_catch_wrapper
def edge_infection(agents:list[Agent], time:int, initial_parameters:InitialParameters, time_step:int, quarantine_level:int):
    for agent in agents:
        if (agent.SEIR_compartment != 'S' or not agent.current_edge):
            continue

        if (time + time_step < agent.started_travelling + agent.travel_time):
            edge_infection_event = event.AgentEvent(event.EDGE_INFECTION, agent)
            event.emit(time + time_step, edge_infection_event)
        
        chance_infection = compute_for_chance_of_infection(initial_parameters.sample_infection_edge_CPC(), agent.current_edge.contact_rate(agent) * QUARANTINE_CR_PERCENTAGE.get(quarantine_level, 1), agent.current_edge.infected_density(), time_step)
        if (random.random() <= chance_infection):
            agent.SEIR_compartment = 'E'
            agent.time_infected = time
            infection_event = event.AgentEvent(event.AGENT_INFECTED, agent)
            event.emit(time + math.ceil(initial_parameters.sample_incubation_period()), infection_event)

@try_catch_wrapper
def remove_agents(agents:list[Agent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        recover_chance = initial_parameters.sample_recovery_chance()
        # TO ADD: Recovery chance depending on age, health condition, etc.
        if (random.random() <= recover_chance):
            agent.SEIR_compartment = 'R'
        else:
            agent.SEIR_compartment = 'D'

@try_catch_wrapper
def firm_activity_collection(firms:list[Firm], time:int) -> tuple[int, int]:
    work_total = 0
    cons_total = 0
    for firm in firms:
        total = firm.get_activity_total()
        work_total += total[0]
        cons_total += total[1]
        next_event = event.FirmEvent(event.FIRM_ACTIVITY_COLLECTION, firm)
        event.emit(time + 24 * 60, next_event)
    return (work_total, cons_total)

@try_catch_wrapper
def shopping_agents(agents:list[Agent], time:int, initial_parameters:InitialParameters, quarantine_level:int):
    for agent in agents:
        firms = agent.graph.get_firms()
        if (isinstance(agent, WorkingAgent)):
            firms.remove(agent.firm)
        destination:Firm = random.choice(firms)
        agent.set_path(agent.graph.shortest_edge_path(agent.current_establishment.node.id, destination.node.id), destination, time, initial_parameters, quarantine_level)
        agent.set_state('travelling')
        traverse_event = event.AgentEvent(event.AGENT_TRAVERSE, agent)
        event.emit(time + 1, traverse_event)
