from typing import Literal
import random


FIRM_INDUSTRIES_CATOGIZATION = {
    ("Agri, For &wdasdw Fish", 1) : 0.0113, ("Mining & Quarrying", 2): 0.0011, ("Manufacturing", 2): 0.0829,
    ("Elec, Gas, Steam & Air", 1): 0.0014, ("Water", 1): 0.0043, ("Construction", 2): 0.0079,
    ("Wholesale & Retail", 3): 0.4445, ("Transpo & Storage", 1): 0.0109, ("Accom & Food", 1): 0.1095,
    ("ICT", 1): 0.0080, ("Finance & Insurance", 3): 0.1522, ("Real Estate", 2): 0.0188,
    ("Prof, Science, & Technical", 3): 0.0183, ("Admin & Support", 2): 0.0219, ("Education", 3): 0.0354,
    ("Human Health", 1): 0.0261, ("Arts & Entertainment", 4): 0.0104, ("Other", 3): 0.0353
    }


class Establishment:
    id:int = 0
    no_agents:int = 0
    no_infected_agents:float = 0
    max_contact_rate:float = 10.0
    max_capacity:int = 100

    def __init__(self, node, region, max_capacity, max_contact_rate):
        self.node = node
        self.region = region
        self.id = Establishment.id
        Establishment.id += 1

        self.max_contact_rate = max_contact_rate
        self.max_capacity = max_capacity
    
    def add_agent(self, agent):
        self.no_agents += 1
        if (agent.SEIR_compartment == 'I'):
            self.no_infected_agents += 1 if not agent.masked else 0.5
    
    def remove_agent(self, agent):
        self.no_agents -= 1
        if (agent.SEIR_compartment == 'I'):
            self.no_infected_agents -= 1 if not agent.masked else 0.5
    
    def contact_rate(self) -> float:
        return self.max_contact_rate * (self.no_agents / self.max_capacity)
    
    def infected_density(self) -> float:
        if (self.no_agents == 0):
            return 0
        return self.no_infected_agents / self.no_agents


class Household(Establishment):
    def __init__(self, node, region, max_contact_rate:float):
        resident_count = random.choices([1, 2, 3, 4, 5], weights=[0.45, 0.35, 0.05, 0.05, 0.05])[0]
        super().__init__(node, region, resident_count, max_contact_rate)
        self.resident_count:int = resident_count
        self.resident_agents = []


class Firm(Establishment):
    essential:bool
    industry:tuple[str, int]
    resident_agents:list
    
    def __init__(self, node, region, size:Literal['micro', 'small', 'medium', 'large'], max_contact_rate:float):
        if (size == 'micro'):
            max_capacity = random.randrange(2, 9)
        elif (size == 'small'):
            max_capacity = random.randrange(10, 99, 5)
        elif (size == 'medium'):
            max_capacity = random.randrange(100, 299, 10)
        elif (size == 'large'):
            max_capacity = random.randrange(300, 700, 50)
        else:
            raise ValueError(f"Firm size must be 'small', 'medium' or 'large'. Received {size}")
        super().__init__(node, region, max_capacity, max_contact_rate)
        self.resident_agents = []
        self.industry = random.choices(FIRM_INDUSTRIES_CATOGIZATION.keys(), FIRM_INDUSTRIES_CATOGIZATION.values(), k=1)[0]
        self.essential = self.industry[1] == 1
