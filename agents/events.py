from agents.agent import WorkingAgent, Agent
from objects import InitialParameters
from functools import lru_cache
from graphing.core import Edge
import event
import random


@lru_cache(maxsize=None, typed=False)
def get_contact_duration(L: float, r: float, started_travelling_1:int, speed_1:int, started_travelling_2:int, speed_2:int) -> float:
    te1 = started_travelling_1 + L / speed_1
    te2 = started_travelling_2 + L / speed_2
    valid_start = max(started_travelling_1, started_travelling_2)
    valid_end = min(te1, te2)
    
    if valid_start >= valid_end:
        return 0.0 
    
    dv = speed_1 - speed_2
    
    C = speed_1 * started_travelling_1 - speed_2 * started_travelling_2
    
    if abs(dv) < 1e-9:
        dist = abs(speed_1 * (started_travelling_2 - started_travelling_1))
        return (valid_end - valid_start) if dist <= r else 0.0

    t_sol_1 = (C + r) / dv
    t_sol_2 = (C - r) / dv
    
    t_contact_start = min(t_sol_1, t_sol_2)
    t_contact_end = max(t_sol_1, t_sol_2)

    final_start = max(valid_start, t_contact_start)
    final_end = min(valid_end, t_contact_end)
    
    return max(0.0, final_end - final_start)


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

def contact_pairing(edges:list[Edge], time:int, initial_parameters:InitialParameters):
    agent_under_investigation = set(['I', 'S'])
    positions = []
    for edge in edges:
        positions.clear()
        for agent in edge.agents:
            positions.append((agent, agent.speed * (time - agent.started_travelling)))
        
        positions.sort(key=lambda x: x[1])
        for i in range(len(positions)):
            agent:Agent = positions[i][0]
            pos_1 = positions[i][1]
            for j in range(i+1, len(positions)):
                contacted_agent:Agent = positions[j][0]
                pos_2 = positions[j][1]
                if (contacted_agent.SEIR_compartment not in agent_under_investigation and agent.SEIR_compartment not in agent_under_investigation):
                    continue
                if pos_2 - pos_1 > initial_parameters.contact_range:
                    break
                if (get_contact_duration(edge.distance, initial_parameters.contact_range, agent.started_travelling, agent.speed, contacted_agent.started_travelling, contacted_agent.speed) > 3):
                    if (contacted_agent not in agent.contacted_agents and contacted_agent.SEIR_compartment == 'I'):
                        agent.contacted_agents.append(contacted_agent)
                    if (agent not in contacted_agent.contacted_agents and agent.SEIR_compartment == 'I'):
                        contacted_agent.contacted_agents.append(agent)