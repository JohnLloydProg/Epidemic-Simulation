class Status:
    time:int

    def __init__(self):
        pass

    def set_time(self, time:int):
        self.time = time


class InitialParameters:
    duration:int
    income_tax:float = 0.1
    vat:float = 0.12
    no_per_compartment:dict[str, int]
    chance_per_contact:float

    def __init__(self, duration:int, no_per_comparment:dict[str, int], chance_per_contact:float=0.1):
        self.duration = duration
        self.no_per_compartment = no_per_comparment
        self.chance_per_contact = chance_per_contact

