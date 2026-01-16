

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
    
    def contact_rate(self) -> float:
        return self.max_contact_rate * (self.no_agents / self.max_capacity)
    
    def infected_density(self) -> float:
        if (self.no_agents == 0):
            return 0
        return self.no_infected_agents / self.no_agents
