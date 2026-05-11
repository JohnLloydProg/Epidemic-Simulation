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

    def __init__(self, duration:int, no_per_comparment:dict[str, int], quarantine_schedule:dict[int, int] = {}):
        self.incubation_period_in_hours = (float(os.environ.get('INCUBATION_PERIOD_IN_HOURS_SHAPE', 165.84)), float(os.environ.get('INCUBATION_PERIOD_IN_HOURS_RATE', 25.2)))
        self.infected_duration_in_hours = (float(os.environ.get('INFECTED_DURATION_IN_HOURS_SHAPE', 7.11)), float(os.environ.get('INFECTED_DURATION_IN_HOURS_RATE', 0.037)))
        self.duration = duration
        self.no_per_compartment = no_per_comparment
        self.quarantine_schedule = quarantine_schedule
        self.chance_per_contact_on_transport = (float(os.environ.get('CHANCE_PER_CONTACT_ON_TRANSPORT_MEAN', 0.03)), float(os.environ.get('CHANCE_PER_CONTACT_ON_TRANSPORT_STD', 0.00577)))
        self.chance_per_contact_on_establishment = (float(os.environ.get('CHANCE_PER_CONTACT_ON_ESTABLISHMENT_MEAN', 0.03)), float(os.environ.get('CHANCE_PER_CONTACT_ON_ESTABLISHMENT_STD', 0.00577)))
    
    def sample_infection_establishment_CPC(self) -> float:
        result = -1
        while (result < 0):
            result = np.random.normal(loc=self.chance_per_contact_on_establishment[0], scale=self.chance_per_contact_on_establishment[1])
        return result
    
    def sample_infection_transport_CPC(self) -> float:
        result = -1
        while (result < 0):
            result = np.random.normal(loc=self.chance_per_contact_on_transport[0], scale=self.chance_per_contact_on_transport[1])
        return result

    def sample_incubation_period(self) -> int:
        result = -1
        while (result <= 0):
            result = np.random.gamma(shape=self.incubation_period_in_hours[0], scale=1.0/self.incubation_period_in_hours[1]) * 60
        return result

    def sample_infected_duration(self) -> int:
        result = -1
        while (result <= 0):
            result = np.random.gamma(shape=self.infected_duration_in_hours[0], scale=1.0/self.infected_duration_in_hours[1]) * 60
        return result

