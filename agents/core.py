

class Establishment:
    id:int = 0
    no_infected:int = 0

    def __init__(self, node):
        self.id = Establishment.id
        self.node = node
        Establishment.id += 1
