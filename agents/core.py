

class Establishment:
    id:int = 0
    agents:list

    def __init__(self, node):
        self.id = Establishment.id
        self.node = node
        self.agents = []
        Establishment.id += 1
