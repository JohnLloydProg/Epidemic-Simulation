from objects import InitialParameters, Agent, Status


class Simulation:
    compartments = ['S', 'E', 'I', 'R']
    initial_parameters:InitialParameters
    seir_compartments:dict[str, list]

    def __init__(self, initial_parameters:InitialParameters, headless=True):
        self.initial_parameters = initial_parameters
        self.seir_compartments = {}
        for compartment in self.compartments:
            self.seir_compartments[compartment] = [Agent() for i in range(self.initial_parameters.no_per_compartment[compartment])]
    
    def generate_status(self) -> Status:
        pass

    def run(self):
        counter = 0
        while (counter // (60 * 24) < self.initial_parameters.duration):
            # Working adults go through their day
            # To go to their work and encounters other people (Base No. of contacts based on edges traveresed)
            # Upon reaching their destination check if the agent becomes exposed, infected, or such *Consider compartment time periods*
            # while working record the time the agent works. (This is recorded on the business node)
            counter += 1
            pass


if __name__ == '__main__':
    Simulation(InitialParameters(365, {'S':300, 'E':150, 'I':60, 'R':10})).run()
