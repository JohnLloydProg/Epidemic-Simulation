

class Establishment:
    id:int = 0
    agents:list

    def __init__(self, node):
        self.node = node
        self.id = Establishment.id
        self.agents = []
        Establishment.id += 1
