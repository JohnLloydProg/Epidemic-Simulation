from agents.core import Establishment
from typing import Literal
import random


class Household(Establishment):
    def __init__(self, node, max_contact_rate:float=10.0):
        resident_count = random.choices([1, 2, 3, 4, 5], weights=[0.45, 0.35, 0.05, 0.05, 0.05])[0]
        super().__init__(node, resident_count, max_contact_rate)
        self.resident_count:int = resident_count
        self.resident_agents = []


class Firm(Establishment):
    worked_agents:list
    customers:list
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
        self.worked_agents = []
        self.customers = []
        self.resident_agents = []
        self.essential = random.random() < 0.3
    
    def attend(self, agent):
        self.worked_agents.append(agent)
    
    def serve(self, agent):
        self.customers.append(agent)
    
    def get_activity_total(self) -> tuple[int, int]:
        total_activity = (sum(map(lambda agent: agent.minimum_salary * 1.5, self.worked_agents)), sum(map(lambda agent: agent.minimum_salary * 1.5 * 0.75, self.customers)))
        self.worked_agents.clear()
        return total_activity
