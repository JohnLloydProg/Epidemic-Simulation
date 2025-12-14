import matplotlib.pyplot as plt
import numpy as np


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwds):
        if cls not in cls._instances:
            cls._instances[cls] = super().__class__(cls, *args, **kwds)
        return cls._instances[cls]


class Status:
    def __init__(self, time:int, SEIR_compartments:dict[str, int]):
        self.time = time
        self.SEIR_compartments = SEIR_compartments
    
    # Returns the time in (day, hour, minute)
    def get_formatted_time(self) -> tuple[int, int, int]:
        minute = self.time % 60
        hour = (self.time // 60) % 24
        day = self.time // (60 * 24)
        return (day, hour, minute)

    def display_report(self):
        x = []
        y = []
        for item, value in self.SEIR_compartments.items():
            x.append(item)
            y.append(value)

        x_np = np.array(x)
        y_np = np.array(y)
        plt.bar(x_np, y_np)
        plt.show()

class InitialParameters:
    income_tax:float = 0.1
    vat:float = 0.12
    contact_range:int = 4

    def __init__(self, duration:int, no_per_comparment:dict[str, int], chance_per_contact_on_edge:tuple[float, float]=(0.04, 0.02), chance_per_contact_on_establishment:tuple[float, float]=(0.1, 0.05), incubation_period_in_hours:tuple[int, int]=(12, 5)):
        self.incubation_period_in_hours = incubation_period_in_hours
        self.duration = duration
        self.no_per_compartment = no_per_comparment
        self.chance_per_contact_on_edge = chance_per_contact_on_edge
        self.chance_per_contact_on_establishment = chance_per_contact_on_establishment
    
    def sample_infection_establishment_CPC(self) -> float:
        return np.random.normal(loc=self.chance_per_contact_on_establishment[0], scale=self.chance_per_contact_on_establishment[1])
    
    def sample_infection_edge_CPC(self) -> float:
        return np.random.normal(loc=self.chance_per_contact_on_edge[0], scale=self.chance_per_contact_on_edge[1])

    def sample_incubation_period(self) -> int:
        return np.random.normal(loc=self.incubation_period_in_hours[0], scale=self.incubation_period_in_hours[1]) * 60

