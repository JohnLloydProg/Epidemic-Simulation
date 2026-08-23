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

    choice = input(f"Are you sure you want to delete {sim_groups_dict[simulation_group]}? [y/n]")
    if (choice == "n"):
        exit()
    
    collection = db.collection(sim_groups_dict[simulation_group])
    docs = collection.list_documents()

    for sim in docs:
        print(f"Simulation ID: {sim.id}")
        sim_ref = collection.document(sim.id)
        sim_ref.delete()

    print(f"you finished deleting simulation {sim_groups_dict[simulation_group]}")