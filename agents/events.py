from agents.agent import WorkingAgent, Agent, compute_for_chance_of_infection
from objects import InitialParameters
from functools import lru_cache
from graphing.core import Edge
import event
import random
import os


def go_work(agents:list[WorkingAgent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        # TO ADD: factor to not go to work if infected or symptomatic or scared agent
        if (agent.SEIR_compartment == 'D'):
            continue
        agent.go_work(time, initial_parameters)
    
def go_home(agents:list[Agent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        if (agent.SEIR_compartment == 'D'):
            continue
        agent.go_home(time, initial_parameters)
    
def traverse(agents:list[Agent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        agent.traverse_graph(time, initial_parameters)
    
def infected(agents:list[Agent], time:int, initial_parameters:InitialParameters):
    for agent in agents:
        agent.SEIR_compartment = 'I'
        event.emit(time + round(initial_parameters.sample_infected_duration()), event.AGENT_REMOVED, agent)

def remove_agents(agents:list[Agent], initial_parameters:InitialParameters):
    for agent in agents:
        recover_chance = initial_parameters.sample_recovery_chance()
        # TO ADD: Recovery chance depending on age, health condition, etc.
        if (random.random() <= recover_chance):
            agent.SEIR_compartment = 'R'
        else:
            agent.SEIR_compartment = 'D'

def edge_infection(edges:list[Edge], time:int, initial_parameters:InitialParameters):
    for edge in edges:
        for i in range(len(edge.agents)):
            agent:Agent = edge.agents[i]
            if (agent.SEIR_compartment == 'S'):
                chance_infection = compute_for_chance_of_infection(initial_parameters.sample_infection_edge_CPC(), edge.contact_rate(agent), edge.infected_density(), int(os.environ.get('TIME_STEP', '2')))
                if (random.random() <= chance_infection):
                    agent.SEIR_compartment = 'E'
                    agent.time_infected = time
                    event.emit(time + round(initial_parameters.sample_incubation_period()), event.AGENT_INFECTED, agent)
                

        