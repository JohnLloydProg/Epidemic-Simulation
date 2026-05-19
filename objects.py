import matplotlib.pyplot as plt
import numpy as np
import os


class Status:
    def __init__(self, time:int, SEIR_compartments:dict[str, int], active_cases:list[tuple[int, int]]):
        self.time = time
        self.SEIR_compartments = SEIR_compartments
        self.active_cases = active_cases

    # Returns the time in (day, hour, minute)
    def get_formatted_time(self) -> tuple[int, int, int]:
        minute = self.time % 60
        hour = (self.time // 60) % 24
        day = self.time // (60 * 24)
        return (day, hour, minute)

    def display_report(self):
        x = list(self.SEIR_compartments.keys())
        y = list(self.SEIR_compartments.values())

        x_np = np.array(x)
        y_np = np.array(y)

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

        ax1.bar(x_np, y_np)
        ax1.set_title('SEIR Distribution (Bar)')
        ax1.set_ylabel('Number of Agents')
        ax1.set_xlabel('Compartment')

        x_active = []
        y_active = []

        for case in self.active_cases:
            x_active.append(case[0])
            y_active.append(case[1])

        # --- Graph 2: Line Chart ---
        # Adding a marker 'o' makes the data points clearly visible on the line
        ax2.plot(x_active, y_active, marker='o', linestyle='-')
        ax2.set_title('SEIR Distribution (Line)')
        ax2.set_ylabel('Number of Agents')
        ax2.set_xlabel('Compartment')

        # tight_layout() prevents the labels from overlapping
        plt.tight_layout()

        # Render the window
        plt.show()

class Disease:
    def __init__(self, config:dict):
        self.incubation_period_in_hours = (config.get('INCUBATION_PERIOD_IN_HOURS_SHAPE', 165.84), config.get('INCUBATION_PERIOD_IN_HOURS_RATE', 25.2))
        self.infected_duration_in_hours = (config.get('INFECTED_DURATION_IN_HOURS_SHAPE', 7.11), config.get('INFECTED_DURATION_IN_HOURS_RATE', 0.037))
        self.chance_per_contact_on_household = (config.get('CHANCE_PER_CONTACT_ON_HOUSEHOLD_MEAN', 0.20), config.get('CHANCE_PER_CONTACT_ON_HOUSEHOLD_STD', 0.0332))
        self.chance_per_contact_on_firm_work = (config.get('CHANCE_PER_CONTACT_ON_FIRM_WORK_MEAN', 0.01), config.get('CHANCE_PER_CONTACT_ON_FIRM_WORK_STD', 0.0051))
        self.chance_per_contact_on_firm_retail = (config.get('CHANCE_PER_CONTACT_ON_FIRM_RETAIL_MEAN', 0.06), config.get('CHANCE_PER_CONTACT_ON_FIRM_RETAIL_STD', 0.0179))
        self.chance_per_contact_on_transport = (config.get('CHANCE_PER_CONTACT_ON_TRANSPORT_MEAN', 0.01), config.get('CHANCE_PER_CONTACT_ON_TRANSPORT_STD', 0.0051))
    
    def sample_infection_household_CPC(self) -> float:
        result = np.random.normal(loc=self.chance_per_contact_on_household[0], scale=self.chance_per_contact_on_household[1])
        return max(0.0, min(1.0, result))

    def sample_infection_firm_work_CPC(self) -> float:
        result = np.random.normal(loc=self.chance_per_contact_on_firm_work[0], scale=self.chance_per_contact_on_firm_work[1])
        return max(0.0, min(1.0, result))

    def sample_infection_firm_retail_CPC(self) -> float:
        result = np.random.normal(loc=self.chance_per_contact_on_firm_retail[0], scale=self.chance_per_contact_on_firm_retail[1])
        return max(0.0, min(1.0, result))

    def sample_infection_transport_CPC(self) -> float:
        result = np.random.normal(loc=self.chance_per_contact_on_transport[0], scale=self.chance_per_contact_on_transport[1])
        return max(0.0, min(1.0, result))

    def sample_incubation_period(self) -> int:
        result = np.random.gamma(shape=self.incubation_period_in_hours[0], scale=1.0/self.incubation_period_in_hours[1]) * 60
        return result

    def sample_infected_duration(self) -> int:
        result = np.random.gamma(shape=self.infected_duration_in_hours[0], scale=1.0/self.infected_duration_in_hours[1]) * 60
        return result

