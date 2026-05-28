from agents.core import Firm, Household
from agents.agent import WorkingAgent, Agent
from transport.transportation import Route
import manager
import logging
import random
import math

LOGGER = logging.getLogger('Interventions')


class Policy:
    def __init__(self, start_time:int, end_time:None|int = None):
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
        return f"Policy(start_time={self.start_time}, end_time={self.end_time})"


class LimitTranspoCapacity(Policy):
    routes:list[Route]

    def __init__(self, start_time:int, routes:list[Route], new_capacity_ratio:float, end_time:None|int = None):
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
        return f"LimitTranspoCapacity(start_time={self.start_time}, end_time={self.end_time}, routes={[route.id for route in self.routes]}, new_capacity_ratio={self.new_capacity_ratio})"


class RouteReduction(Policy):
    def __init__(self, start_time:int, routes:list[Route], end_time:None|int = None):
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
    def __init__(self, start_time:int, end_time:None|int = None):
        super().__init__(start_time, end_time)

    def implement(self, simulation):
        super().implement(simulation)
        agents:list[Agent] = simulation.agents
        for agent in agents:
            agent.masked = True
    
    def revert(self, simulation):
        super().revert(simulation)
        agents:list[Agent] = simulation.agents
        for agent in agents:
            agent.masked = False
    
    def __str__(self):
        return f"MandatoryMask(start_time={self.start_time}, end_time={self.end_time})"


class TravelDistanceLimitation(Policy):
    def __init__(self, start_time:int, max_travel_distance:int, end_time:None|int = None):
        super().__init__(start_time, end_time)
        self.max_travel_distance = max_travel_distance
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.max_travel_distance = self.max_travel_distance

    def revert(self, simulation):
        super().revert(simulation)
        simulation.max_travel_distance = None
    
    def __str__(self):
        return f"TravelDistanceLimitation(start_time={self.start_time}, end_time={self.end_time}, max_travel_distance={self.max_travel_distance})"


class EssentialCompanyOnly(Policy):
    def __init__(self, start_time:int, end_time:None|int = None):
        super().__init__(start_time, end_time)
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.essential_only = True

    def revert(self, simulation):
        super().revert(simulation)
        simulation.essential_only = False
    
    def __str__(self):
        return f"EssentialCompanyOnly(start_time={self.start_time}, end_time={self.end_time})"


class LimitCompanyCapacity(Policy):
    def __init__(self, start_time:int, firms:list[Firm], capacity_ratio:float, end_time:None|int = None):
        super().__init__(start_time, end_time)
        self.capacity_ratio = capacity_ratio
        self.firms = firms
        self.original_capacity = {firm.id:firm.max_capacity for firm in firms}
    
    def implement(self, simulation):
        super().implement(simulation)
        for firm in self.firms:
            firm.max_capacity = math.ceil(self.capacity_ratio * firm.max_capacity)
    
    def revert(self, simulation):
        super().revert(simulation)
        for firm in self.firms:
            firm.max_capacity = self.original_capacity[firm.id]
    
    def __str__(self):
        return f"LimitCompanyCapacity(start_time={self.start_time}, end_time={self.end_time}, capacity_ratio={self.capacity_ratio})"


class EnforceQuaratine(Policy):
    def __init__(self, start_time:int, end_time:None|int = None):
        super().__init__(start_time, end_time)
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.quarantine = True
    
    def revert(self, simulation):
        super().revert(simulation)
        simulation.quarantine = False
    
    def __str__(self):
        return f"EnforceQuarantine(start_time={self.start_time}, end_time={self.end_time})"


class DesignatedPerson(Policy):
    def __init__(self, start_time:int, end_time:None|int = None):
        super().__init__(start_time, end_time)
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.designated_persons = True
        
    
    def revert(self, simulation):
        super().revert(simulation)
        simulation.designated_persons = False
    
    def __str__(self):
        return f"DesignatedPerson(start_time={self.start_time}, end_time={self.end_time})"


class Curfew(Policy):
    def __init__(self, start_time:int, curfew_start_hour:int, curfew_end_hour:int, end_time:None|int = None):
        super().__init__(start_time, end_time)
        self.curfew_start_hour = curfew_start_hour
        self.curfew_end_hour = curfew_end_hour
    
    def implement(self, simulation):
        super().implement(simulation)
        simulation.curfew = {
            "start_hour": self.curfew_start_hour,
            "end_hour": self.curfew_end_hour
        }

    
    def revert(self, simulation):
        super().revert(simulation)
        simulation.curfew = {}
    
    def __str__(self):
        return f"Curfew(start_time={self.start_time}, end_time={self.end_time})"


class BikeTranspo(Policy):
    def __init__(self, start_time:int, population_portion:float, end_time:None|int = None):
        super().__init__(start_time, end_time)
        self.population_portion = population_portion
        self.changed_agents:list[Agent] = []
    
    def implement(self, simulation):
        super().implement(simulation)
        candidates:list[Agent] = [agent for agent in simulation.agents if agent.commuting and agent.state == 'home']
        for agent in random.sample(candidates, int(self.population_portion * len(candidates))):
            agent.commuting = False
            agent.private = 'bike'
            self.changed_agents.append(agent)
    
    def revert(self, simulation):
        super().revert(simulation)
        for agent in self.changed_agents:
            agent.commuting = True
    
    def __str__(self):
        return f"BikeTranspo(start_time={self.start_time}, end_time={self.end_time}, population_portion={self.population_portion})"


class TestingKit(Policy):
    def __init__(self, start_time:int, testing_probability:float, end_time:None|int = None):
        super().__init__(start_time, end_time)
        self.testing_probability = testing_probability
    
    def implement(self, simulation):
        super().implement(simulation)
        firms:list[Firm] = simulation.graph.get_firms()
        for firm in firms:
            firm.testing_probability = self.testing_probability

    def revert(self, simulation):
        super().revert(simulation)
        firms:list[Firm] = simulation.graph.get_firms()
        for firm in firms:
            firm.testing_probability = 0


POLICY_CLASS_MAPPING = {
    'limit-transpo-capacity':LimitTranspoCapacity, 'route-reduction':RouteReduction, 'mandatory-mask':MandatoryMask,
    'travel-distance-limitation':TravelDistanceLimitation, 'essential-company-only':EssentialCompanyOnly,
    'limit-company-capacity':LimitCompanyCapacity, 'enforce-quarantine':EnforceQuaratine, 
    'designated-person':DesignatedPerson, 'curfew':Curfew, 'bike-transpo':BikeTranspo,
    'enable-worker-testing':TestingKit
}


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
