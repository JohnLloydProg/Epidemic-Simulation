from agents.agent import WorkingAgent, Agent
from agents.core import Firm
from objects import InitialParameters
import functools
import sim_event.manager as manager
import random
import time

from transport.transportation import Transportation

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
def agent_arrival(agents:list[Agent], time:int):
    for agent in agents:
        agent.current_node = agent.checkpoints[0].end_node
        agent.current_node.agents.append(agent)
        agent.checkpoints.pop(0)
        agent.move(time)

@try_catch_wrapper
def transport_arrived(transportations:list[Transportation], time:int):
    for transport in transportations:
        for agent in list(transport.agents):
            if(transport.current_node == agent.checkpoints[0].end_node):
                agent.alight_transportation()
                agent.current_node = transport.current_node
                agent.current_node.agents.append(agent)
                agent.checkpoints.pop(0)
                agent.move(time)

        for agent in list(transport.current_node.agents):
            current_leg = agent.checkpoints[0]
            if (current_leg.mode == 'ride' and current_leg.route == transport.route):
                if (not transport.is_full()):
                    agent.ride_transportation(transport)
                    agent.set_state('travelling')
        
        transport.transport(time)

