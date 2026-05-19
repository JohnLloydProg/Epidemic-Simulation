import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import matplotlib.pyplot as plt
import math
from dotenv import load_dotenv
import os


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

    average_cases:dict[int, list[int]] = {}
    
    for sim in docs:
        print(f"Simulation ID: {sim.id}")
        sim_ref = collection.document(sim.id)
        sim_data = sim_ref.get().to_dict()
        active_cases:list[tuple[int, int]] = []
        for time, data in sim_data.items():
            active_cases.append((int(time), data['I']))
        
        active_cases.sort(key=lambda d: d[0])

        x_active = []
        y_active = []

        for case in active_cases:
            x_active.append(case[0])
            y_active.append(case[1])
            if (case[0] not in average_cases):
                average_cases[case[0]] = [case[1]]
            else:
                average_cases[case[0]].append(case[1])
        ax1.plot(x_active, y_active, marker='o', linestyle='-', label=sim.id)
    
    ave_x = list(average_cases.keys())
    ave_y = [sum(daily_cases)/len(daily_cases) for daily_cases in average_cases.values()]
    ax2.plot(ave_y)

    # --- Graph 2: Line Chart ---
    # Adding a marker 'o' makes the data points clearly visible on the line
    ax1.set_title('Simulation Disease Spread (Line)')
    ax1.set_ylabel('Active Cases')
    ax1.set_xlabel('Days')

    ax2.set_title('Average Simulation Disease Spread (Line)')
    ax2.set_ylabel('Active Cases')
    ax2.set_xlabel('Days')

    plt.tight_layout()
    plt.show()
