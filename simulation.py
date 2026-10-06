from dotenv import load_dotenv
import pygame as pg
load_dotenv()
pg.init()
import configuration as config
from graphing.graph import RegionGraph
from agents.agent import Agent, handle_agent_events
from transport.transportation import Transportation, RoutedTransportation, handle_route_events, handle_transportation_events, BusRoute, JeepRoute, TrainRoute
from ui.button import ButtonBehavior, TextButton
from transport.route_editor import RouteEditor
from ui.zone_editor import ZoneEditor, people_in_hotspots, hotspot_zones
from graphing.data_loader import load_graph_from_data, load_case, data_dir, results_dir
from agents.od_demand import schedule_od_agents
from routing_table import build_routing_cache
from time import time_ns
from datetime import datetime
import manager
import random
import logging
import os
import sys

LOGGER = logging.getLogger('Simulation')


def get_agent_states(agents:list[Agent]) -> dict[str, int]:
    states = {}
    for agent in agents:
        states[agent.state] = states.get(agent.state, 0) + 1
    return states

def get_travelling_mode(agents:list[Agent]) -> dict[str, int]:
    travel_modes = {}
    for agent in agents:
        if (agent.state != 'travelling'):
            continue
        
        if (agent.transportation):
            travel_modes[agent.transportation.method] = travel_modes.get(agent.transportation.method, 0) + 1
        else:
            travel_modes['walking'] = travel_modes.get('walking', 0) + 1
    return travel_modes

def get_transport_count(transportations:list[RoutedTransportation]):
    transport_types = {}
    for transport in transportations:
        transport_types[transport.method] = transport_types.get(transport.method, 0) + 1
    return transport_types


class Simulation:
    layer = 'city'
    agents:list[Agent]
    transportations:list[Transportation]
    graph:RegionGraph
    clock:pg.time.Clock
    window:pg.Surface
    font:pg.font.Font
    routing_table:dict[tuple, list]
    simulation_multiplier = 5
    simulation_ns_per_time_unit = (10**9)//simulation_multiplier
    buttons:dict[str, ButtonBehavior] = {}
    step_counter = 0

    def __init__(self):
        logging.basicConfig(handlers=[logging.FileHandler("logfile.txt", 'w'), logging.StreamHandler(sys.stdout)], 
                            level=logging.DEBUG if os.environ.get('DEBUG', 'False') == 'True' else logging.INFO)
        config.init()
        manager.init()
        
        """Initialize simulation parameters"""
        self.time_step = config.get('TIME_STEP', 2)
        start = int(float(config.get('SIM_START_HOUR', 0)) * 3600)
        self.start_time = start - start % self.time_step      # keep the clock on the event grid
        self.agents = []
        self.od_summary = None
        self.od_spawned = 0
        self.od_failed = 0
        self.transportations = []
        self.active_cases = []
        self.started = False            # True once time moves or an agent is added; routes are locked then
        self._reset_requested = False

        """Load environment and initialize route spawning events"""
        environment = load_graph_from_data()
        self.graph = environment[0]
        self.railway_graph = environment[1]
        self.routes = environment[2]
        for route in self.routes:
            manager.emit(self.start_time + 3, manager.Event(manager.TRANSPORTATION_SPAWN, route))

        """Build routing cache for agents"""
        # Only nodes where trips can start or end are cached (other pairs are computed on demand if ever needed)
        nodes = self.trip_end_nodes()
        LOGGER.info(f'Routing cache covers {len(nodes)} trip-end nodes of {len(self.graph.nodes)}.')
        self.routing_table = build_routing_cache(nodes, self.graph, self.railway_graph, self.routes)

        """Schedule agents from the OD matrix (only when OD_BUNDLE_DIR is set in the config)"""
        self.schedule_od_demand()

        LOGGER.info(f'Simulation initialized with {len(self.agents)} agents.')
        
        """Mainly for visualization purposes"""
        self.play = False
        self.clock = pg.time.Clock()
        self.window = pg.display.set_mode((1080, 720))
        self.font = pg.font.Font(None, 15)
        self.railway_graph.camera = self.graph.camera   # one shared view for both layers
        all_nodes = list(self.graph.nodes.values()) + list(self.railway_graph.nodes.values())
        self.graph.camera.fit([node.pos for node in all_nodes], self.window.get_size())
        self.create_ui_elements()
        self.editor = RouteEditor(self)
        self.zone_editor = ZoneEditor(self)
        
        self.run()

    def trip_end_nodes(self) -> list:
        """Nodes where agents can start or end a trip: every node of the OD barangays (OD_TRIP_END_ROLES),
        the gateways, and the zone anchors (used by right-click test agents)."""
        roles = set(config.get('OD_TRIP_END_ROLES', ['od']))
        nodes = []
        for region in getattr(self.graph, 'zones', {}).values():
            if region.role in roles:
                nodes += [node for node in region.nodes if node is not None and node.edges]
        nodes += getattr(self.graph, 'gateway_nodes', []) + getattr(self.graph, 'anchor_nodes', [])
        if not nodes:                                   # old map data without zones: keep every node
            return [node for node in self.graph.nodes.values() if node.edges]
        return list(dict.fromkeys(nodes))

    def schedule_od_demand(self):
        """Schedule agents from the OD matrix (only when OD_BUNDLE_DIR is set in the config)"""
        self.od_spawned = 0
        self.od_failed = 0
        if (config.get('OD_BUNDLE_DIR')):
            self.od_summary = schedule_od_agents(self.graph, self.railway_graph, load_case(), data_dir(), self.start_time,
                                                 results_dir(), routes=self.routes)

    def reschedule_od_demand(self):
        """Recompute the OD matrix for the current routes and replace the queued OD agents (before the start only)."""
        for target in list(manager._events):
            kept = [event for event in manager._events[target] if event.type != manager.AGENT_SPAWN]
            if kept:
                manager._events[target] = kept
            else:
                del manager._events[target]
        self.schedule_od_demand()

    def reset(self):
        """Back to the start time, keeping the current route edits: removes all agents, vehicles and pending
        events, then queues the route spawns and the OD agents again (same OD_SEED -> same agents)."""
        manager._events.clear()
        self.agents.clear()
        self.transportations.clear()
        for node in list(self.graph.nodes.values()) + list(self.railway_graph.nodes.values()):
            node.agents.clear()
        for route in self.routes:
            route.transportations.clear()
            manager.emit(self.start_time + 3, manager.Event(manager.TRANSPORTATION_SPAWN, route))
        Agent.id = 0
        Transportation.id = 0
        self.schedule_od_demand()
        self.play = False
        self.started = False
        self._reset_requested = True        # run() sets its clock back to start_time
        LOGGER.info('Simulation reset.')

    def create_ui_elements(self):
        """Create UI elements such as buttons"""
        self.buttons['play'] = TextButton(20, 20, 100, 30, lambda: setattr(self, 'play', not self.play), (255, 0, 0), "Play")
        self.buttons['reset'] = TextButton(130, 20, 100, 30, self.reset, (200, 200, 200), "Reset")

    def handle_events(self, time:int):
        """Event based handling"""
        for event in manager.get(time):
            handle_agent_events(event, time, self)
            handle_transportation_events(event, time, self)
            handle_route_events(event, time, self)
    
    def run(self):
        time = self.start_time
        delta = 0
        draw_time = 0
        simultation_time = 0
        running = True
        states = get_agent_states(self.agents)
        travel_modes = {}

        LOGGER.info('Starting simulation...')
        while (running):
            second = time % 60
            minute = (time // 60) % 60
            hour = (time // 3600) % 24
            day = time // (3600 * 24)
            time_record = time_ns()
            self.peak_hour = (9 >= hour >= 6) or (20 >= hour >= 17)

            for event in pg.event.get():
                consumed = []
                if (self.editor.handle_event(event, time)):
                    continue
                if (self.zone_editor.handle_event(event, time)):
                    continue
                if (event.type == pg.QUIT):
                    running = False
                    return
                elif (event.type == pg.KEYDOWN):
                    if (event.key == pg.K_UP and self.simulation_multiplier < 30):
                        self.simulation_multiplier += 1
                    elif (event.key == pg.K_DOWN and self.simulation_multiplier > 1):
                        self.simulation_multiplier -= 1
                    self.simulation_ns_per_time_unit = (10**9)//self.simulation_multiplier
                elif (event.type == pg.MOUSEBUTTONDOWN and event.button == 3):
                    nodes = getattr(self.graph, 'anchor_nodes', None) or list(filter(lambda n: n.edges, self.graph.nodes.values()))
                    self.started = True
                    agent = Agent(self.graph, self.railway_graph, random.choice(nodes), random.choice(nodes))
                    self.agents.append(agent)
                    if (agent.commuting):
                        agent.set_checkpoints(self.routing_table, self.routes, time, self)
                    else:
                        agent.set_path(time, self)
                    print("Generating an agent")

                for button in self.buttons.values():
                    button.clicked(event, consumed)

                self.graph.camera.handle_event(event)

            if (self._reset_requested):
                self._reset_requested = False
                time = self.start_time
                states = get_agent_states(self.agents)
                travel_modes = {}

            """Handle events and update agent states"""
            if (time_ns() - simultation_time >= self.simulation_ns_per_time_unit and self.play):
                self.started = True
                self.handle_events(time)
                states = get_agent_states(self.agents)

                travel_modes = get_travelling_mode(self.agents)
                simultation_time = time_ns()
                delta = (time_ns() - time_record) / (10**6)
                time += self.time_step
            
            """Visualization and metrics. Here the drawing is done."""
            if (time_ns() - draw_time >= (10**9)//60):
                draw_time = time_ns()
                self.window.fill((255, 255, 255))
                self.zone_editor.draw_zones(self.window)
                self.graph.draw(self.window, self.font,  self.layer)
                
                routes = sorted(self.routes, key=lambda route:route.get_average_occupancy(), reverse=True)
                for route in routes:
                    route.draw(self.window, self.graph)
                self.editor.draw(self.window)
                self.zone_editor.draw_labels(self.window)
                
                text = self.font.render(f"time: {time} (Day {day} {str(hour).zfill(2)}:{str(minute).zfill(2)}:{str(second).zfill(2)}) {self.simulation_multiplier}x {round(delta, 2)}ms per step {len(manager._events.values())} events", False, (0, 0, 0))
                
                state_text = ''
                for state in ['home', 'travelling', 'waiting', 'working', 'consuming']:
                    state_text += f'{state}: {states.get(state, 0)}, '
                states_text = self.font.render(f"States: {state_text}", False, (0, 0, 0))
                
                travel_text = self.font.render(f"Travel modes: {travel_modes}", False, (0, 0, 0))
                if (self.od_summary):
                    active = sum(1 for agent in self.agents if agent.trip_kind)
                    od_text = self.font.render(f"OD agents spawned: {self.od_spawned}/{self.od_summary['agents_scheduled']}, active: {active}, failed: {self.od_failed}", False, (0, 0, 0))
                    self.window.blit(od_text, od_text.get_rect(topleft=(20, 100)))
                hot = hotspot_zones(self.graph)
                if (hot):
                    hot_text = self.font.render(f"People in hotspots ({len(hot)} zones): {people_in_hotspots(self)}", False, (180, 0, 0))
                    self.window.blit(hot_text, hot_text.get_rect(topleft=(20, 120)))
                occupancies:dict[str, list] = {}
                for transpo in self.transportations:
                    if (isinstance(transpo, RoutedTransportation)):
                        if (transpo.method in occupancies):
                            occupancies[transpo.method].append(transpo.occupancy())
                        else:
                            occupancies[transpo.method] = [transpo.occupancy()]
                    transpo.draw(self.window, self.graph.camera, time)

                for agent in self.agents:
                    agent.draw(self.window, self.graph.camera, time)
                metric_text = self.font.render(f"Transportation Used: {len(self.transportations)}, avg. occupancy: {[(method, round(max(occupancy), 2))for method, occupancy in occupancies.items()]}", False, (0, 0, 0))
                available_transports = self.font.render(f"Live Transportation: {get_transport_count(self.transportations)}", False, (0, 0, 0))
                
                self.window.blit(states_text, states_text.get_rect(topleft=(20, 40)))
                self.window.blit(travel_text, travel_text.get_rect(topleft=(20, 60)))
                self.window.blit(available_transports, available_transports.get_rect(topleft=(20, 80)))
                pg.draw.circle(self.window, (0, 255, 0), pg.mouse.get_pos(), 5)
                self.window.blit(metric_text, metric_text.get_rect(topleft=(20, 20)))
                self.window.blit(text, text.get_rect(topright=(1060, 20)))

                for button in self.buttons.values():
                    button.draw(self.window)

                pg.display.update()
    

if __name__ == '__main__':
    LOGGER.info(f"Simulation Start: {datetime.now().isoformat()}")
    Simulation()
    LOGGER.info(f"Simulation End: {datetime.now().isoformat()}")