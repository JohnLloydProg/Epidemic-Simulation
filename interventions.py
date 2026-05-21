from agents.core import Firm, Household
from agents.agent import WorkingAgent
from transport.transportation import Route
import manager
import logging
import random

LOGGER = logging.getLogger('Interventions')


class Policy:
    def __init__(self, start_time:int, end_time:None|int=None):
        self.start_time = start_time
        self.end_time = end_time
        self.is_active = False
    
    def implement(self, simulation):
        self.is_active = True
        LOGGER.info(f"Implementing {str(self)}...")

    def revert(self, simulation):
        self.is_active = False
        LOGGER.info(f"Reverting {str(self)}...")

    def __str__(self):
        return f"Policy(start_time={self.start_time}, end_time={self.end_time}, is_active={self.is_active})"


class LimitTranspoCapacity(Policy):
    routes:list[Route]

    def __init__(self, start_time:int, routes:list[Route], new_capacity_ratio:float, end_time:None|int=None):
        super().__init__(start_time, end_time)
        self.routes = routes
        self.new_capacity_ratio = new_capacity_ratio
    
    def implement(self, simulation):
        super().implement(simulation)
        for route in self.routes:
            route.capacity_ratio = self.new_capacity_ratio

    def revert(self, simulation):
        super().revert(simulation)
        for route in self.routes:
            route.capacity_ratio = 1

    def __str__(self):
        return f"LimitCapacity(start_time={self.start_time}, end_time={self.end_time}, is_active={self.is_active}, routes={[route.id for route in self.routes]}, new_capacity_ratio={self.new_capacity_ratio})"


class RouteReduction(Policy):
    def __init__(self, start_time:int, routes:list[Route], end_time:None|int=None):
        super().__init__(start_time, end_time)
        self.removed_routes = routes
        self.removed_routing = {}
    
    def implement(self, simulation):
        super().implement(simulation)
        current_routes:list[Route] = list(simulation.routes)
        for route in current_routes:
            if (route in self.removed_routes):
                simulation.routes.remove(route)

        keys_to_pop = []
        for key, checkpoints in simulation.routing_table.items():
            for checkpoint in checkpoints:
                if (checkpoint.route in self.removed_routes):
                    self.removed_routing[key] = simulation.routing_table[key]
                    keys_to_pop.append(key)
                    break
        
        for key in keys_to_pop:
            simulation.routing_table.pop(key)

        for route in self.removed_routes:
            manager.cancel(manager.TRANSPORTATION_SPAWN, route)
            
    def revert(self, simulation):
        super().revert(simulation)
        simulation.routing_table.update(self.removed_routing)

        for route in self.removed_routes:
            simulation.routes.append(route)
            manager.emit(self.end_time + 2, manager.Event(manager.TRANSPORTATION_SPAWN, route))
    
    def __str__(self):
        return f"RouteReduction(start_time={self.start_time}, end_time={self.end_time}, routes={[route.id for route in self.removed_routes]})"


class MandatoryMask(Policy):
    def __init__(self, start_time:int, firms:list[Firm], end_time:None|int=None):
        super().__init__(start_time, end_time)
        self.firms = firms
        self.original_contact_rates = {firm.id:firm.max_contact_rate for firm in self.firms}

    def implement(self, simulation):
        super().implement(simulation)
        for firm in self.firms:
            firm.max_contact_rate = firm.max_contact_rate * 0.5
        self.original_contact_rates['JEEP'] = simulation.config['CONTACT_RATES']['JEEP']
        self.original_contact_rates['BUS'] = simulation.config['CONTACT_RATES']['BUS']
        self.original_contact_rates['TRAIN'] = simulation.config['CONTACT_RATES']['TRAIN']
        simulation.config['CONTACT_RATES']['JEEP'] = simulation.config['CONTACT_RATES']['JEEP'] * 0.5
        simulation.config['CONTACT_RATES']['BUS'] = simulation.config['CONTACT_RATES']['BUS'] * 0.5
        simulation.config['CONTACT_RATES']['TRAIN'] = simulation.config['CONTACT_RATES']['TRAIN'] * 0.5
    
    def revert(self, simulation):
        super().revert(simulation)
        for firm in self.firms:
            firm.max_contact_rate = self.original_contact_rates[firm.id]
        simulation.config['CONTACT_RATES']['JEEP'] = self.original_contact_rates['JEEP']
        simulation.config['CONTACT_RATES']['BUS'] = self.original_contact_rates['BUS']
        simulation.config['CONTACT_RATES']['TRAIN'] = self.original_contact_rates['TRAIN']
    
    def __str__(self):
        return f"MandatoryMask(start_time={self.start_time}, end_time={self.end_time}, firms={[firm.id for firm in self.firms]})"


class TravelDistanceLimitation(Policy):
    def __init__(self, start_time:int, max_travel_distance:int, end_time:None|int=None):
        super().__init__(start_time, end_time)
        self.max_travel_distance = max_travel_distance
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.max_travel_distance = self.max_travel_distance

    def revert(self, simulation):
        super().revert(simulation)
        simulation.max_travel_distance = None


class EssentialCompanyOnly(Policy):
    def __init__(self, start_time:int, end_time:None|int=None):
        super().__init__(start_time, end_time)
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.essential_only = True

    def revert(self, simulation):
        super().revert(simulation)
        simulation.essential_only = False


class LimitCompanyCapacity(Policy):
    def __init__(self, start_time:int, capacity_ratio:float, end_time:None|int=None):
        super().__init__(start_time, end_time)
        self.capacity_ratio = capacity_ratio
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.company_capacity_ratio = self.capacity_ratio
    
    def revert(self, simulation):
        super().revert(simulation)
        simulation.company_capacity_ratio = 1


class EnforceQuaratine(Policy):
    def __init__(self, start_time:int, end_time:None|int=None):
        super().__init__(start_time, end_time)
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.quarantine = True
    
    def revert(self, simulation):
        super().revert(simulation)
        simulation.quarantine = False


class DesignatedPerson(Policy):
    def __init__(self, start_time:int, end_time:None|int=None):
        super().__init__(start_time, end_time)
    
    def implement(self, simulation):
        super().implement(simulation)
        households:list[Household] = simulation.graph.get_households()
        for house in households:
            designated = random.choice([agent for agent in house.resident_agents if (isinstance(agent, WorkingAgent))])
            simulation.designated_persons.append(designated)
    
    def revert(self, simulation):
        super().revert(simulation)
        simulation.designated_persons.clear()


def handle_policy_events(simulation, event:manager.Event, time:int):
    policies:list[Policy] = event.get_objects()
    if (event.type == manager.IMPLEMENT_POLICY):
        for policy in policies:
            policy.implement(simulation)
            if (policy.end_time):
                manager.emit(policy.end_time, manager.Event(manager.REVERT_POLICY, policy))
    elif (event.type == manager.REVERT_POLICY):
        for policy in policies:
            policy.revert(simulation)
