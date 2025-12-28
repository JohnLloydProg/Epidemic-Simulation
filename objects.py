import matplotlib.pyplot as plt
import numpy as np
import os


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

    def __init__(self, duration:int, no_per_comparment:dict[str, int]):
        self.incubation_period_in_hours = (int(os.environ.get('INCUBATION_PERIOD_IN_HOURS_MEAN', 12)), int(os.environ.get('INCUBATION_PERIOD_IN_HOURS_STD', 5)))
        self.infected_duration_in_hours = (int(os.environ.get('INFECTED_DURATION_IN_HOURS_MEAN', 168)), int(os.environ.get('INFECTED_DURATION_IN_HOURS_STD', 24)))
        self.duration = duration
        self.no_per_compartment = no_per_comparment
        self.chance_per_contact_on_edge = (float(os.environ.get('CHANCE_PER_CONTACT_ON_EDGE_MEAN', 0.04)), float(os.environ.get('CHANCE_PER_CONTACT_ON_EDGE_STD', 0.02)))
        self.chance_per_contact_on_establishment = (float(os.environ.get('CHANCE_PER_CONTACT_ON_ESTABLISHMENT_MEAN', 0.1)), float(os.environ.get('CHANCE_PER_CONTACT_ON_ESTABLISHMENT_STD', 0.05)))
        self.recovery_chance = (float(os.environ.get('RECOVERY_CHANCE_MEAN', 0.9)), float(os.environ.get('RECOVERY_CHANCE_STD', 0.05)))
    
    def sample_infection_establishment_CPC(self) -> float:
        return np.random.normal(loc=self.chance_per_contact_on_establishment[0], scale=self.chance_per_contact_on_establishment[1])
    
    def sample_infection_edge_CPC(self) -> float:
        return np.random.normal(loc=self.chance_per_contact_on_edge[0], scale=self.chance_per_contact_on_edge[1])

    def sample_incubation_period(self) -> int:
        return np.random.normal(loc=self.incubation_period_in_hours[0], scale=self.incubation_period_in_hours[1]) * 60

    def sample_infected_duration(self) -> int:
        return np.random.normal(loc=self.infected_duration_in_hours[0], scale=self.infected_duration_in_hours[1]) * 60
    
    def sample_recovery_chance(self) -> float:
        return np.random.normal(loc=self.recovery_chance[0], scale=self.recovery_chance[1])

