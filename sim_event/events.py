from agents.agent import WorkingAgent, Agent
from agents.core import Firm
from objects import InitialParameters
import functools
import sim_event.manager as manager
import random
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
        traversal_event = manager.AgentEvent(manager.AGENT_TRAVERSE, agent)
        manager.emit(time + 1, traversal_event)

@try_catch_wrapper
def go_home(agents:list[Agent], time:int, initial_parameters:InitialParameters, quarantine_level:int):
    for agent in agents:
        agent.set_path(agent.graph.shortest_edge_path(agent.current_establishment.node.id, agent.household.node.id), agent.household, time, initial_parameters, quarantine_level)
        agent.set_state('travelling')
        traverse_event = manager.AgentEvent(manager.AGENT_TRAVERSE, agent)
        manager.emit(time + 1, traverse_event)

@try_catch_wrapper
def infected(agents:list[Agent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        agent.SEIR_compartment = 'I'
        agent.symptomatic = random.random() < 0.6  # 60% chance to be symptomatic
        remove_event = manager.AgentEvent(manager.AGENT_REMOVED, agent)
        manager.emit(time + round(initial_parameters.sample_infected_duration()), remove_event)

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
def shopping_agents(agents:list[Agent], time:int, initial_parameters:InitialParameters, quarantine_level:int):
    for agent in agents:
        firms = agent.graph.get_firms()
        if (isinstance(agent, WorkingAgent)):
            firms.remove(agent.firm)
        destination:Firm = random.choice(firms)
        agent.set_path(agent.graph.shortest_edge_path(agent.current_establishment.node.id, destination.node.id), destination, time, initial_parameters, quarantine_level)
        agent.set_state('travelling')
        traverse_event = manager.AgentEvent(manager.AGENT_TRAVERSE, agent)
        manager.emit(time + 1, traverse_event)
