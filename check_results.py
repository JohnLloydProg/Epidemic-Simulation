import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import matplotlib.pyplot as plt


if __name__ == "__main__":
    cred = credentials.Certificate('epidemicsimulation-firebase-adminsdk-fbsvc-81103feabb.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    simulation_group = input("Enter the simulation group name: ")
    collection = db.collection(simulation_group)
    docs = collection.list_documents()
    
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
        plt.plot(x_active, y_active, marker='o', linestyle='-')


    # --- Graph 2: Line Chart ---
    # Adding a marker 'o' makes the data points clearly visible on the line
    plt.suptitle('SEIR Distribution (Line)')
    plt.ylabel('Number of Agents')
    plt.xlabel('Compartment')
    plt.show()
