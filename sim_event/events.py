from agents.agent import WorkingAgent, Agent
from agents.core import Firm
from objects import InitialParameters
from transport.transportation import Route, RoutedTransportation, Transportation
from simulation import next_occurrence_of_hour
import functools
import sim_event.manager as manager
import random
import time
import traceback
import logging

logger = logging.getLogger('events')

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
            traceback.print_exc()
            result = None
        return result
    return wrapper

@try_catch_wrapper
def go_work(agents:list[WorkingAgent], routing_cache:dict, time:int, initial_parameters:InitialParameters):
    for agent in agents:
        agent.check_for_infection(
            initial_parameters.sample_infection_establishment_CPC(),
            initial_parameters.sample_incubation_period(),
            agent.current_establishment.contact_rate(), 
            agent.current_establishment.infected_density(),
            time - agent.arrival_time, time
            )
        if (agent.commuting):
            agent.set_checkpoints(agent.firm, routing_cache, time)
        else:
            agent.set_path(agent.firm, time)

@try_catch_wrapper
def go_home(agents:list[Agent],  routing_cache:dict, time:int, initial_parameters:InitialParameters):
    for agent in agents:
        agent.check_for_infection(
            initial_parameters.sample_infection_establishment_CPC(),
            initial_parameters.sample_incubation_period(),
            agent.current_establishment.contact_rate(), 
            agent.current_establishment.infected_density(),
            time - agent.arrival_time, time
            )
        if (agent.commuting):
            agent.set_checkpoints(agent.household, routing_cache, time)
        else:
            agent.set_path(agent.household, time)

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
        agent.arrival(time)

@try_catch_wrapper
def transportation_spawn(routes:list[Route], is_peak_hours:bool, time:int) -> list[RoutedTransportation]:
    transportations = []
    for route in routes:
        transports = route.generate_transportation(current_time=time, is_peak_hours=is_peak_hours)
        for transport in transports:
            for agent in list(transport.current_node.agents):
                if (agent.state != 'waiting'):
                    continue

                current_leg = agent.checkpoints[0]
                if (current_leg.mode == 'ride' and current_leg.end_node in transport.route.ordered_nodes):
                    current_index = transport.route.ordered_nodes.index(transport.current_node)
                    for node in transport.route.ordered_nodes[current_index:]:
                        if (not transport.is_full() and current_leg.end_node == node):
                            agent.ride_transportation(transport, time)
                            agent.set_state('travelling')
                            break
            transport.transport(time)
        transportations.extend(transports)
    return transportations

@try_catch_wrapper
def transport_arrived(transportations:list[RoutedTransportation], time:int, initial_parameters:InitialParameters):
    for transport in transportations:
        transport.current_node = transport.current_edge.get_adjacent_node(transport.current_node)
        for agent in list(transport.agents):
            if(transport.current_node == agent.checkpoints[0].end_node):
                agent.alight_transportation()
                agent.check_for_infection(
                    initial_parameters.sample_infection_establishment_CPC(),
                    initial_parameters.sample_incubation_period(),
                    transport.get_contact_rate(), 
                    transport.get_infected_density(),
                    time - agent.boarding_time, time
                    )
                agent.arrival(time)

        for agent in list(transport.current_node.agents):
            if (agent.state != 'waiting'):
                continue

            current_leg = agent.checkpoints[0]
            if (current_leg.mode == 'ride' and current_leg.end_node in transport.route.ordered_nodes):
                current_index = transport.route.ordered_nodes.index(transport.current_node)
                for node in transport.route.ordered_nodes[current_index:]:
                    if (not transport.is_full() and current_leg.end_node == node):
                        agent.ride_transportation(transport, time)
                        agent.set_state('travelling')
                        break
            
        transport.transport(time)

@try_catch_wrapper
def private_transportation_arrived(transportations:list[Transportation], time:int):
    for transport in transportations:
        transport.current_node = transport.current_edge.get_adjacent_node(transport.current_node)
        agent:Agent = transport.agents[0]  # Private transportation can only have one agent
        if (transport.current_node == agent.destination.node):
            agent.alight_transportation()
            agent.arrival(time, transport.current_node)
        else:
            transport.transport(time)
        
