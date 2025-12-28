

class Establishment:
    id:int = 0
    agents:list
    max_contact_rate:float = 10.0
    max_capacity:int = 100

    def __init__(self, node):
        self.node = node
        self.id = Establishment.id
        self.agents = []
        Establishment.id += 1
    
    def contact_rate(self) -> float:
        return self.max_contact_rate * (len(self.agents) / self.max_capacity)
    
    def infected_density(self) -> float:
        if (len(self.agents) == 0):
            return 0.0
        infected_agents = 0
        for agent in self.agents:
            if (agent.SEIR_compartment == 'I'):
                infected_agents += 1
        return infected_agents / len(self.agents)
