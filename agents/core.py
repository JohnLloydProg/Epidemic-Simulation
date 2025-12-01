from multiprocessing import Value


class Establishment:
    id:int = 0

    def __init__(self, node):
        self.no_infected = Value('i', 0)
        self.node = node
        self.id = Establishment.id
        Establishment.id += 1
