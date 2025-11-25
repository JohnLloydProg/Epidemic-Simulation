from agents.core import Establishment


class Household(Establishment):
    def __init__(self, node):
        super().__init__(node)


class Firm(Establishment):
    def __init__(self, node):
        super().__init__(node)