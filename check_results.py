import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import matplotlib.pyplot as plt
import math
from dotenv import load_dotenv
import os


class DataPoint:
    susceptible:int
    exposed:int
    infected:int
    removed:int
    dead:int
    time:int

    def __init__(self, time, data:dict):
        self.susceptible = data["S"]
        self.exposed = data["E"]
        self.infected = data["I"]
        self.removed = data["R"]
        self.dead = data["D"]
        self.time = time

class DataPointAverage:
    _susceptible:list[int]
    _exposed:list[int]
    _infected:list[int]
    _removed:list[int]
    _dead:list[int]

    def __init__(self, point:DataPoint):
        self._susceptible = [point.susceptible]
        self._exposed = [point.exposed]
        self._infected = [point.infected]
        self._removed = [point.removed]
        self._dead = [point.dead]
    
    def add_point(self, point:DataPoint):
        self._susceptible.append(point.susceptible)
        self._exposed.append(point.exposed)
        self._infected.append(point.infected)
        self._removed.append(point.removed)
        self._dead.append(point.dead)
    
    @property
    def susceptible(self):
        if (not self._susceptible):
            return 0

        return sum(self._susceptible)/len(self._susceptible)

    @property
    def exposed(self):
        if (not self._exposed):
            return 0
        
        return sum(self._exposed)/len(self._exposed)

    @property
    def infected(self):
        if (not self._infected):
            return 0
        
        return sum(self._infected)/len(self._infected)

    @property
    def removed(self):
        if (not self._removed):
            return 0
        
        return sum(self._removed)/len(self._removed)

    @property
    def dead(self):
        if (not self._dead):
            return 0
        
        return sum(self._dead)/len(self._dead)


if __name__ == "__main__":
    load_dotenv()
    cred = credentials.Certificate(os.environ['CERT_FILE_NAME'])
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    sim_groups = db.collections()
    sim_groups_dict = {}

    for i, sim_group in enumerate(sim_groups):
        print(f'[{i}] {sim_group.id}')
        sim_groups_dict[i] = sim_group.id
    simulation_group = int(input("Enter the simulation group name: "))
    
    collection = db.collection(sim_groups_dict[simulation_group])
    docs = collection.list_documents()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.canvas.manager.set_window_title(collection.id)

    average_cases:dict[int, DataPointAverage] = {}
    
    for sim in docs:
        print(f"Simulation ID: {sim.id}")
        sim_ref = collection.document(sim.id)
        sim_data = sim_ref.get().to_dict()
        active_cases:list[DataPoint] = []
        for time, data in sim_data.items():
            active_cases.append(DataPoint(int(time), data))
        
        active_cases.sort(key=lambda d: d.time)

        x_active = []
        y_active = []

        for case in active_cases:
            x_active.append(case.time)
            y_active.append(case.infected)
            if (case.time not in average_cases):
                average_cases[case.time] = DataPointAverage(case)
            else:
                average_cases[case.time].add_point(case)
        ax1.plot(x_active, y_active, marker='o', linestyle='-', label=sim.id)
    
    ave_x = list(average_cases.keys())

    ave_y = [dailyAverage.exposed for dailyAverage in average_cases.values()]
    ax2.plot(ave_x, ave_y, label="Exposed")

    ave_y = [dailyAverage.infected for dailyAverage in average_cases.values()]
    ax2.plot(ave_x, ave_y, label="Infected")

    ave_y = [dailyAverage.removed for dailyAverage in average_cases.values()]
    ax2.plot(ave_x, ave_y, label="Removed")



    # --- Graph 2: Line Chart ---
    # Adding a marker 'o' makes the data points clearly visible on the line
    ax1.set_title('Simulation Disease Spread (Line)')
    ax1.set_ylabel('Active Cases')
    ax1.set_xlabel('Days')

    ax2.set_title('Average Simulation Disease Spread (Line)')
    ax2.set_ylabel('Active Cases')
    ax2.set_xlabel('Days')
    ax2.legend()

    plt.tight_layout()
    plt.show()
