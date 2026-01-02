from agents.agent import WorkingAgent, Agent, compute_for_chance_of_infection
from objects import InitialParameters
import functools
from graphing.core import Edge
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
def go_work(agents:list[WorkingAgent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        # TO ADD: factor to not go to work if infected or symptomatic or scared agent
        if (agent.SEIR_compartment == 'D'):
            continue
        agent.go_work(time, initial_parameters)

@try_catch_wrapper
def go_home(agents:list[Agent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        if (agent.SEIR_compartment == 'D'):
            continue
        agent.go_home(time, initial_parameters)

@try_catch_wrapper
def traverse(agents:list[Agent], time:int, time_step:int):
    for agent in agents:
        agent.traverse_graph(time, time_step)

@try_catch_wrapper
def infected(agents:list[Agent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        agent.SEIR_compartment = 'I'
        remove_event = event.AgentEvent(event.AGENT_REMOVED, agent)
        event.emit(time + round(initial_parameters.sample_infected_duration()), remove_event)

@try_catch_wrapper
def edge_infection(agents:list[Agent], time:int, initial_parameters:InitialParameters, time_step:int):
    for agent in agents:
        agent.edge_infection(time, initial_parameters, time_step)

@try_catch_wrapper
def remove_agents(agents:list[Agent], initial_parameters:InitialParameters):
    for agent in agents:
        recover_chance = initial_parameters.sample_recovery_chance()
        # TO ADD: Recovery chance depending on age, health condition, etc.
        if (random.random() <= recover_chance):
            agent.SEIR_compartment = 'R'
        else:
            agent.SEIR_compartment = 'D'
