import configuration as config
from typing import Literal
import random
import math
import numpy as np


FIRM_INDUSTRIES_CATOGIZATION = {
    ("Agri, For & Fish", 1) : 0.0113, ("Mining & Quarrying", 2): 0.0011, ("Manufacturing", 2): 0.0829,
    ("Elec, Gas, Steam & Air", 1): 0.0014, ("Water", 1): 0.0043, ("Construction", 2): 0.0079,
    ("Wholesale & Retail", 3): 0.4445, ("Transpo & Storage", 1): 0.0109, ("Accom & Food", 1): 0.1095,
    ("ICT", 1): 0.0080, ("Finance & Insurance", 3): 0.1522, ("Real Estate", 2): 0.0188,
    ("Prof, Science, & Technical", 3): 0.0183, ("Admin & Support", 2): 0.0219, ("Education", 3): 0.0354,
    ("Human Health", 1): 0.0261, ("Arts & Entertainment", 4): 0.0104, ("Other", 3): 0.0353
    }

WEEKEND_FIRMS = {
    ("Wholesale & Retail", 3),
    ("Accom & Food", 1),
    ("Arts & Entertainment", 4),
    ("Human Health", 1),
    ("Elec, Gas, Steam & Air", 1),
    ("Water", 1),
    ("Transpo & Storage", 1),
    ("Admin & Support", 2)
}

ESSENTIAL_FRACTION = {
    "Agri, For & Fish": 1.0,
    "Mining & Quarrying": 1.0,
    "Manufacturing": 0.55,
    "Elec, Gas, Steam & Air": 1.0,
    "Water": 1.0,
    "Construction": 0.15,
    "Wholesale & Retail": 0.25,
    "Transpo & Storage": 1.0,
    "Accom & Food": 1.0,
    "ICT": 1.0,
    "Finance & Insurance": 0.35,
    "Real Estate": 0.65,
    "Prof, Science, & Technical": 0.0,
    "Admin & Support": 0.41,
    "Education": 0.0,
    "Human Health": 1.0,
    "Arts & Entertainment": 0.0,
    "Other": 0.0,
}

def generate_resident_count(max_size=10):
    r, p = 15.490, 0.8479  # fitted to Manila City's exact 2020 AHS = 3.7792
    size = np.random.negative_binomial(r, p) + 1  # +1 shift: min possible output = 1
    return min(size, max_size)


class Establishment:
    id:int = 0
    no_agents:int = 0
    susceptible_agents:set
    infected_agents:set
    no_infected_agents:float = 0
    max_contact_rate:float = 10.0
    max_capacity:int = 100

    def __init__(self, node, region, max_capacity, max_contact_rate):
        self.node = node
        self.region = region
        self.id = Establishment.id
        Establishment.id += 1
        self.base_capacity = math.ceil(max_capacity * config.get("BASE_CAPACITY_RATIO", 0.6))
        self.max_contact_rate = max_contact_rate
        self.max_capacity = max_capacity
        self.susceptible_agents = set()
        self.infected_agents = set()
    
    def add_agent(self, agent):
        if (agent.SEIR_compartment == 'D'):
            return

        self.no_agents += 1
        if (agent.SEIR_compartment == 'I'):
            self.no_infected_agents += agent.infection_multiplier
            self.infected_agents.add(agent)
        elif (agent.SEIR_compartment == 'S'):
            self.susceptible_agents.add(agent)

    def sync_agent_state(self, agent, old_compartment: str):
        """Called by the agent whenever their SEIR state changes while inside this establishment."""
        if (old_compartment == 'I'):
            if (agent in self.infected_agents):
                self.no_infected_agents -= agent.infection_multiplier
                self.infected_agents.remove(agent)
        elif (old_compartment == 'S'):
            if (agent in self.susceptible_agents):
                self.susceptible_agents.remove(agent)

        if (agent.SEIR_compartment == 'I'):
            self.no_infected_agents += agent.infection_multiplier
            self.infected_agents.add(agent)
        elif (agent.SEIR_compartment == 'S'):
            self.susceptible_agents.add(agent)

        if (self.no_infected_agents < 1e-9):
            self.no_infected_agents = 0.0
    
    def remove_agent(self, agent):
        self.no_agents -= 1
        if (agent in self.infected_agents):
            self.no_infected_agents -= agent.infection_multiplier
            self.infected_agents.remove(agent)
        elif (agent in self.susceptible_agents):
            self.susceptible_agents.remove(agent)

        if (self.no_agents <= 0):
            self.infected_agents.clear()
            self.susceptible_agents.clear()
            self.no_agents = 0
            self.no_infected_agents = 0
        elif (self.no_infected_agents < 1e-9):
            self.no_infected_agents = 0
    
    def contact_rate(self) -> float:
        if (self.base_capacity == 0):
            return 0
        return self.max_contact_rate * (self.no_agents / self.base_capacity)
    
    def infected_density(self) -> float:
        if (self.no_agents == 0):
            return 0
        return self.no_infected_agents / self.no_agents


class Household(Establishment):
    def __init__(self, node, region, max_contact_rate:float):
        resident_count = generate_resident_count()
        super().__init__(node, region, resident_count, max_contact_rate)
        self.resident_count:int = resident_count
        self.resident_agents = []


class Firm(Establishment):
    essential:bool
    industry:tuple[str, int]
    resident_agents:list
    working_agents:list
    day_workers:dict[int, list]
    max_workers:int
    testing_probability:float = 0
    
    def __init__(self, node, region, size:Literal['micro', 'small', 'medium', 'large'], max_contact_rate:float):
        if (size == 'micro'):
            max_workers = random.randrange(2, 9)
        elif (size == 'small'):
            max_workers = random.randrange(10, 99, 5)
        elif (size == 'medium'):
            max_workers = random.randrange(100, 299, 10)
        elif (size == 'large'):
            max_workers = random.randrange(300, 700, 50)
        else:
            raise ValueError(f"Firm size must be 'small', 'medium' or 'large'. Received {size}")
        self.max_workers = max_workers
        max_capacity = int(max_workers * random.uniform(1.5, 3.5))
        super().__init__(node, region, max_capacity, max_contact_rate)
        self.resident_agents = []
        self.industry = random.choices(list(FIRM_INDUSTRIES_CATOGIZATION.keys()), list(FIRM_INDUSTRIES_CATOGIZATION.values()), k=1)[0]
        self.essential = random.random() < ESSENTIAL_FRACTION[self.industry[0]]
        self.working_agents = []
        self.day_workers = {num:[] for num in range(7)}
    
    def add_agent(self, agent):
        super().add_agent(agent)
        if (agent in self.resident_agents):
            self.working_agents.append(agent)
    
    def remove_agent(self, agent):
        super().remove_agent(agent)
        if (agent in self.resident_agents and agent in self.working_agents):
            self.working_agents.remove(agent)
