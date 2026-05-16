from typing import Literal
import random


class Establishment:
    id:int = 0
    no_agents:int = 0
    no_infected_agents:int = 0
    max_contact_rate:float = 10.0
    max_capacity:int = 100

    def __init__(self, node, max_capacity, max_contact_rate):
        self.node = node
        self.id = Establishment.id
        Establishment.id += 1

        self.max_contact_rate = max_contact_rate
        self.max_capacity = max_capacity
    
    def add_agent(self, agent):
        self.no_agents += 1
        if (agent.SEIR_compartment == 'I'):
            self.no_infected_agents += 1
    
    def remove_agent(self, agent):
        self.no_agents -= 1
        if (agent.SEIR_compartment == 'I'):
            self.no_infected_agents -= 1
    
    def contact_rate(self) -> float:
        return self.max_contact_rate * (self.no_agents / self.max_capacity)
    
    def infected_density(self) -> float:
        if (self.no_agents == 0):
            return 0
        return self.no_infected_agents / self.no_agents


class Household(Establishment):
    def __init__(self, node, max_contact_rate:float=10.0):
        resident_count = random.choices([1, 2, 3, 4, 5], weights=[0.45, 0.35, 0.05, 0.05, 0.05])[0]
        super().__init__(node, resident_count, max_contact_rate)
        self.resident_count:int = resident_count
        self.resident_agents = []


class Firm(Establishment):
    essential:bool
    resident_agents:list
    
    def __init__(self, node, size:Literal['micro', 'small', 'medium', 'large'], max_contact_rate:float=4.0):
        if (size == 'micro'):
            max_capacity = random.randrange(1, 9)
        elif (size == 'small'):
            max_capacity = random.randrange(10, 99, 5)
        elif (size == 'medium'):
            max_capacity = random.randrange(100, 199, 10)
        elif (size == 'large'):
            max_capacity = random.randrange(200, 500, 50)
        else:
            raise ValueError(f"Firm size must be 'small', 'medium' or 'large'. Received {size}")
        super().__init__(node, max_capacity, max_contact_rate)
        self.resident_agents = []
        self.essential = random.random() < 0.3
