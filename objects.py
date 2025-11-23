class Status:
    def __init__(self, time:int, SEIR_compartments:dict[str, int], ):
        self.time = time
        self.SEIR_compartments = SEIR_compartments
    
    # Returns the time in (day, hour, minute)
    def get_formatted_time(self) -> tuple[int, int, int]:
        minute = self.time % 60
        hour = (self.time // 60) % 24
        day = self.time // (60 * 24)
        return (day, hour, minute)

class InitialParameters:
    income_tax:float = 0.1
    vat:float = 0.12

    def __init__(self, duration:int, no_per_comparment:dict[str, int], chance_per_contact:float=0.1):
        self.duration = duration
        self.no_per_compartment = no_per_comparment
        self.chance_per_contact = chance_per_contact

