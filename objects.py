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

    def __init__(self, duration:int, no_per_comparment:dict[str, int], chance_per_contact:float=0.1, incubation_period_in_hours:int=1):
        self.incubation_period = incubation_period_in_hours * 60
        self.duration = duration
        self.no_per_compartment = no_per_comparment
        self.chance_per_contact = chance_per_contact

