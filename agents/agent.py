from graph import Node


class Agent:
    residence_node:Node
    SEIR_compartment:str

    def __init__(self, residence_node:Node, compartment:str='S'):
        self.residence_node = residence_node
        self.SEIR_compartment = compartment


class Working(Agent):
    pass
