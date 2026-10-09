from dotenv import load_dotenv
import json
import pygame as pg
load_dotenv()
pg.init()
import configuration as config
from graphing.graph import RegionGraph
from agents.agent import Agent, handle_agent_events
from transport.transportation import Transportation, RoutedTransportation, handle_route_events, handle_transportation_events, BusRoute, JeepRoute, TrainRoute
from ui.button import ButtonBehavior, ToolbarButton, layout_row, draw_toolbar
from transport.route_editor import RouteEditor
from ui.zone_editor import ZoneEditor
from ui.metrics_panel import MetricsPanel
from metrics import MetricsTracker
from ui.road_editor import RoadEditor
from ui.facility_editor import FacilityEditor
from ui.case_manager import CaseManager
from ui.view_filter import ViewFilter
from ui.renderer import SceneRenderer
from ui.filter_panel import FilterPanel
from transport.closures import init_closures, _clear_search_caches
from transport.tricycle import build_services as build_tricycle_services
from transport import congestion
from graphing.data_loader import load_graph_from_data, load_case, data_dir, results_dir, set_case_file, case_file_name
from agents.od_demand import schedule_od_agents
from routing_table import build_routing_cache
from time import time_ns
from datetime import datetime
import manager
import random
import logging
import os
import sys
import case_files

LOGGER = logging.getLogger('Simulation')


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

    def __init__(self, headless:bool = False):
        """headless=True: load everything but do not open the interactive loop (tools/run_headless.py)."""
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
        self.show_congestion = False    # K: colour the roads by congestion (transport/congestion.py)
        self.metrics = MetricsTracker(self)     # thesis metrics (metrics.py); graph and saving in ui/metrics_panel.py

        """Load the case (network, routes, routing cache, OD agents)"""
        set_case_file(case_file_name())          # worker processes follow the case opened in the program
        self.load_case_data()

        LOGGER.info(f'Simulation initialized with {len(self.agents)} agents.')
        
        """Mainly for visualization purposes"""
        self.play = False
        self.clock = pg.time.Clock()
        self.fullscreen = False                  # F11 / toolbar button; the window can also be resized
        self._windowed_size = (1080, 720)
        self.window = pg.display.set_mode(self._windowed_size, pg.RESIZABLE)
        self._last_size = self.window.get_size()
        self.font = pg.font.Font(None, 15)
        self.view_filter = ViewFilter(config.get('GRAPHICS_VIEW', True), config.get('GRAPHICS_SCALE', 2.0))   # V / [ ]
        self.renderer = SceneRenderer(self)                                 # ui/renderer.py draws the map
        self.railway_graph.camera = self.graph.camera   # one shared view for both layers
        all_nodes = list(self.graph.nodes.values()) + list(self.railway_graph.nodes.values())
        self.graph.camera.fit([node.pos for node in all_nodes], self.window.get_size())
        self.create_ui_elements()
        if (config.get('FULLSCREEN', False) and not headless):
            self.set_fullscreen(True)
        self.case_manager = CaseManager(self)    # O: open a case, Ctrl+S: save one (ui/case_manager.py)
        self.create_editors()
        self.metrics_panel = MetricsPanel(self)  # G: occupancy graph, M: save metrics
        self.filter_panel = FilterPanel(self)    # L: show/hide people, vehicle types and routes on the map

        if (not headless):
            self.run()

    def load_case_data(self, on_progress=None):
        """Load the open case: network, routes, tricycles, routing cache (from its cache file when there is a
        current one) and the OD agents. on_progress(done, total) is called while a routing cache is computed."""
        environment = load_graph_from_data()
        congestion.reset()
        self.graph = environment[0]
        self.railway_graph = environment[1]
        self.routes = environment[2]
        congestion.check_transit_load(self.routes)
        init_closures(self)
        # tricycles: one on-demand service per barangay, territory = it + the barangays touching it
        build_tricycle_services(self.graph, data_dir(), load_case().get('tricycle_disabled', []))
        # facility numbers per barangay (OD "zone_facilities"); edited in facility mode (F)
        self.zone_facilities = json.loads(json.dumps((load_case().get('od_settings') or {}).get('zone_facilities', {})))
        for route in self.routes:
            manager.emit(self.start_time + 3, manager.Event(manager.TRANSPORTATION_SPAWN, route))

        """Build routing cache for agents"""
        # Only nodes where trips can start or end are cached (other pairs are computed on demand if ever needed)
        nodes = self.trip_end_nodes()
        LOGGER.info(f'Routing cache covers {len(nodes)} trip-end nodes of {len(self.graph.nodes)}.')
        self.routing_table = build_routing_cache(nodes, self.graph, self.railway_graph, self.routes, on_progress)

        """Schedule agents from the OD matrix (only when OD_BUNDLE_DIR is set in the config)"""
        self.schedule_od_demand()
        pg.display.set_caption(f"Simulation — {load_case()['case_id']}")

    def create_editors(self):
        self.editor = RouteEditor(self)
        self.zone_editor = ZoneEditor(self)
        self.road_editor = RoadEditor(self)
        self.facility_editor = FacilityEditor(self)
        self.case_fingerprint = case_files.session_fingerprint(self)   # "unsaved changes" = differs from this

    def _clear_session(self):
        """Forget everything of the open case: queued events, agents, vehicles, edits and search caches."""
        manager._events.clear()
        self.agents.clear()
        self.transportations.clear()
        Agent.id = 0
        Transportation.id = 0
        self.od_summary = None
        self.od_spawned = self.od_failed = 0
        for attr in ('route_edits', 'route_originals'):
            self.__dict__.pop(attr, None)
        _clear_search_caches()

    def open_case(self, file_name:str, on_progress=None):
        """Replace the running case with sim_data/cases/<file_name>, back at the start time (the view is kept).
        If it cannot be loaded, the previous case is loaded again and the error is raised."""
        self.end_metrics_run('case closed')         # finish the logs of the case being closed
        previous = case_file_name()
        camera = self.graph.camera
        view = (camera.zoom, camera.x_offset, camera.y_offset, camera._home)
        LOGGER.info(f"Opening case {file_name}...")
        self._clear_session()
        set_case_file(file_name)
        try:
            self.load_case_data(on_progress)
        except Exception:
            LOGGER.exception(f"Could not open case {file_name}; going back to {previous}.")
            self._clear_session()
            set_case_file(previous)
            self.load_case_data(on_progress)
            self._after_open(view)
            raise
        self._after_open(view)

    def _after_open(self, view):
        camera = self.graph.camera
        camera.zoom, camera.x_offset, camera.y_offset, camera._home = view
        self.railway_graph.camera = camera
        self.create_editors()
        self.metrics.reset()
        self.play = False
        self.started = False
        self._reset_requested = True             # run() sets its clock back to start_time

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
                                                 results_dir(), routes=self.routes,
                                                 zone_facilities=getattr(self, 'zone_facilities', None))

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
        events, then queues the route spawns and the OD agents again (same OD_SEED -> same agents).
        The metrics run so far is ended first (its logs are kept)."""
        self.end_metrics_run('reset')
        self.metrics.reset()
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
        congestion.reset()
        self.schedule_od_demand()
        self.play = False
        self.started = False
        self._reset_requested = True        # run() sets its clock back to start_time
        LOGGER.info('Simulation reset.')

    def end_metrics_run(self, reason:str):
        """End the metrics run (writes its summary files and closes its logs) if one is going."""
        if (self.metrics.ready and not self.metrics.finished):
            self.metrics.end_run(self.metrics.last_time, reason)

    def advance(self, time:int) -> bool:
        """One simulation step at `time`. Returns True when the metrics run has just reached METRICS_RUN_HOURS."""
        hour = (time // 3600) % 24
        self.peak_hour = (9 >= hour >= 6) or (20 >= hour >= 17)
        self.started = True
        self.handle_events(time)
        return self.metrics.step(time)

    def metrics_lines(self, time:int) -> list[str]:
        """HUD lines for the thesis metrics (metrics.py)."""
        m = self.metrics
        if (not m.ready):
            return [f"Metrics: a {m.run_hours:g}-hour logged run starts when you press Play."]
        if (m.finished):
            return [f"Logged run finished - logs in {m.folder}. Press Reset to start a new run."]
        elapsed = (m.last_time - m.start_time) / 3600
        live = ', '.join(f"{mode} {n}" for mode, n in m.live_modes.most_common()) or '-'
        means = ', '.join(f"{mode} {v:.0f}" for mode, v in m.mean_travel_by_mode().items()) or '-'
        hour = (time // 3600) % 24
        lines = [f"Logging: {elapsed:.1f} of {m.run_hours:g} simulated hours -> {m.folder}",
                 f"Moving now: {live} | waiting for a ride: {m.waiting}",
                 f"Trips completed: {m.n_completed:,} ({m.per_hour.get(hour, 0):,} this hour) | avg travel min by mode: {means}"]
        if (m.hotspots):
            now = sum(m.now.get(z, 0) for z in m.hotspots)
            lines.append(f"Hotspots ({len(m.hotspots)}): {now} people now | {m.person_minutes(m.hotspots):,.0f} person-min | "
                         f"trips to hotspots: {m.trips_to_hotspot_arrived:,} arrived of {m.trips_to_hotspot_scheduled:,} started | "
                         f"people who entered: {len(m.hot_visitors):,}")
        lines += self.congestion_lines()
        return lines

    def congestion_lines(self) -> list[str]:
        """HUD line for the road congestion model (transport/congestion.py)."""
        if (not congestion.enabled()):
            return []
        snap = congestion.last_snapshot()
        if (not snap):
            return ["Congestion (K: map): no vehicles on the roads yet"]
        speeds = ', '.join(f"{mode} {snap['speed_' + mode]}" for mode in ('private', 'jeep', 'bus', 'tricycle') if snap['speed_' + mode] != '')
        return [f"Congestion (K: map): background {snap['background']:.0%} of jam density, {snap['links_over_critical']} roads past "
                f"critical density, {snap['waiting']} vehicles held by full roads" + (f" | road km/h: {speeds}" if speeds else '')]

    def create_ui_elements(self):
        """Create UI elements such as buttons"""
        self.buttons['play'] = ToolbarButton(0, 0, lambda: setattr(self, 'play', not self.play),
                                             label=lambda: "Pause" if self.play else "Play",
                                             icon=lambda: 'pause' if self.play else 'play',
                                             style=lambda: 'warning' if self.play else 'primary',
                                             widest=("Play", "Pause"), tooltip="Start / pause the simulation")
        self.buttons['reset'] = ToolbarButton(0, 0, self.reset, "Reset", 'reset',
                                              tooltip="Back to the start time (keeps route and hotspot edits)")
        self.buttons['cases'] = ToolbarButton(0, 0, lambda: self.case_manager.open_browser(), "Cases", 'folder',
                                              tooltip="Open a case (O)  ·  save with Ctrl+S")
        self.buttons['view'] = ToolbarButton(0, 0, lambda: self.view_filter.toggle_view(),
                                             label=lambda: "Graphics" if self.view_filter.graphics else "Bare",
                                             icon='eye', widest=("Graphics", "Bare"),
                                             tooltip="Switch between graphics and bare view (V)  ·  size: [ ]")
        self.buttons['filters'] = ToolbarButton(0, 0, lambda: self.filter_panel.toggle(), "Filters", 'filter',
                                                style=lambda: 'active' if self.filter_panel.visible else 'neutral',
                                                badge=lambda: self.view_filter.is_filtered(),
                                                tooltip="Show / hide vehicle types, people and routes (L)")
        self.buttons['screen'] = ToolbarButton(0, 0, lambda: self.set_fullscreen(not self.fullscreen),
                                               label=lambda: "Exit full screen" if self.fullscreen else "Full screen",
                                               icon=lambda: 'shrink' if self.fullscreen else 'expand',
                                               widest=("Exit full screen", "Full screen"),
                                               tooltip="Full screen on / off (F11)")
        layout_row(list(self.buttons.values()), 16, 14, gap=8)

    # ------------------------------------------------------------------ window size
    def set_fullscreen(self, on:bool):
        """Full screen on the current display, or back to the resizable window; the map keeps its centre."""
        if (on == self.fullscreen):
            return
        if (on):
            self._windowed_size = self.window.get_size()
            sizes = pg.display.get_desktop_sizes() or [self._windowed_size]
            pg.display.set_mode(sizes[0], pg.FULLSCREEN)
        else:
            pg.display.set_mode(self._windowed_size, pg.RESIZABLE)
        self.fullscreen = on
        self._sync_window()

    def _sync_window(self):
        """After the window changed size: keep the same map point in the middle and refit the home view (0 key)."""
        surface = pg.display.get_surface()
        if (surface is None):
            return
        self.window = surface
        new = surface.get_size()
        old = self._last_size
        if (new == old):
            return
        camera = self.graph.camera
        center = camera.to_world((old[0] / 2, old[1] / 2))
        zoom = camera.zoom
        all_nodes = list(self.graph.nodes.values()) + list(self.railway_graph.nodes.values())
        camera.fit([node.pos for node in all_nodes], new)             # new home view for this size
        camera.zoom = zoom
        camera.x_offset = new[0] / 2 - center[0] * zoom
        camera.y_offset = new[1] / 2 - center[1] * zoom
        self._last_size = new

    def handle_events(self, time:int):
        """Event based handling"""
        for event in manager.get(time):
            handle_agent_events(event, time, self)
            handle_transportation_events(event, time, self)
            handle_route_events(event, time, self)
        congestion.tick(time)
    
    def run(self):
        time = self.start_time
        delta = 0
        draw_time = 0
        simultation_time = 0
        running = True

        LOGGER.info('Starting simulation...')
        while (running):
            time_record = time_ns()

            for event in pg.event.get():
                consumed = []
                if (event.type == pg.QUIT):
                    self.end_metrics_run('program closed')
                    return
                if (event.type in (pg.VIDEORESIZE, pg.WINDOWSIZECHANGED)):
                    self._sync_window()
                    continue
                if (event.type == pg.KEYDOWN and event.key == pg.K_F11):
                    self.set_fullscreen(not self.fullscreen)
                    continue
                if (self.case_manager.handle_event(event)):    # case list / save box (modal while open)
                    continue
                if (self._reset_requested):                   # a case was just opened: drop this frame's events
                    continue
                if (self.filter_panel.handle_event(event)):   # clicks/wheel over the filter panel, search typing
                    continue
                if (self.editor.handle_event(event, time)):
                    continue
                if (self.zone_editor.handle_event(event, time)):
                    continue
                if (self.road_editor.handle_event(event, time)):
                    continue
                if (self.facility_editor.handle_event(event, time)):
                    continue
                if (self.metrics_panel.handle_event(event, time)):
                    continue
                if (event.type == pg.QUIT):
                    running = False
                    return
                elif (event.type == pg.KEYDOWN):
                    if (event.key == pg.K_k):
                        self.show_congestion = not self.show_congestion
                    elif (event.key == pg.K_v):
                        self.view_filter.toggle_view()
                    elif (event.key == pg.K_l):
                        self.filter_panel.toggle()
                    elif (event.key == pg.K_b):                       # real map under the graphics view
                        self.view_filter.real_map = not self.view_filter.real_map
                    elif (event.key in (pg.K_LEFTBRACKET, pg.K_RIGHTBRACKET)):   # graphics size (ui/view_filter.py)
                        self.view_filter.change_size(1 if event.key == pg.K_RIGHTBRACKET else -1)
                    elif (event.key == pg.K_UP and self.simulation_multiplier < 30):
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

            """Handle events and update agent states"""
            if (time_ns() - simultation_time >= self.simulation_ns_per_time_unit and self.play):
                if (not self.started and self.network_dirty):     # road changes not rebuilt yet
                    self.road_editor._rebuild()
                if (self.advance(time)):                          # METRICS_RUN_HOURS reached: logs complete
                    self.play = False
                    self.metrics_panel.flash(f"{self.metrics.run_hours:g}-hour run complete - logs saved to {self.metrics.folder}", 15000)
                simultation_time = time_ns()
                delta = (time_ns() - time_record) / (10**6)
                time += self.time_step
            
            """Visualization and metrics. Here the drawing is done."""
            if (time_ns() - draw_time >= (10**9)//60):
                draw_time = time_ns()
                self.draw_frame(time, delta)
                pg.display.update()
    

    def draw_frame(self, time:int, delta:float = 0):
        """Draw one frame into the window (the map through ui/renderer.py, in the graphics or bare view)."""
        second = time % 60
        minute = (time // 60) % 60
        hour = (time // 3600) % 24
        day = time // (3600 * 24)
        window = self.window
        self.renderer.draw_background(window)              # real map in the graphics view (ui/basemap.py)
        self.zone_editor.draw_zones(window)
        self.renderer.draw_network(window, self.font)
        self.renderer.draw_routes(window)
        if (self.show_congestion and congestion.enabled()):
            congestion.draw(window, self.graph.camera, time)
        self.renderer.draw_movers(window, time)            # vehicles and people, filtered (ui/view_filter.py)
        self.renderer.draw_zone_labels(window)             # barangay names above the traffic (graphics view)
        self.road_editor.draw(window)
        self.editor.draw(window)
        self.zone_editor.draw_labels(window)
        self.facility_editor.draw(window)

        lines = [(f"OD agents spawned: {self.od_spawned:,}/{self.od_summary['agents_scheduled']:,}, active: {len(self.agents):,}, failed: {self.od_failed}", (0, 0, 0))] if self.od_summary else []
        lines += [(line, (180, 0, 0) if line.startswith('Hotspots') else (0, 0, 0)) for line in self.metrics_lines(time)]
        surfaces = [self.font.render(line, True, color) for line, color in lines]
        if (surfaces):                                      # readable over the map: a soft panel behind the text
            box = pg.Rect(12, 56, max(s.get_width() for s in surfaces) + 16, 18 * len(surfaces) + 6)
            back = pg.Surface(box.size, pg.SRCALPHA)
            pg.draw.rect(back, (255, 255, 255, 200), back.get_rect(), border_radius=8)
            window.blit(back, box.topleft)
            for i, surf in enumerate(surfaces):
                window.blit(surf, surf.get_rect(topleft=(20, 60 + 18 * i)))

        clock_text = self.font.render(f"time: {time} (Day {day} {str(hour).zfill(2)}:{str(minute).zfill(2)}:{str(second).zfill(2)}) {self.simulation_multiplier}x {round(delta, 2)}ms per step {len(manager._events.values())} events", True, (0, 0, 0))
        clock_rect = clock_text.get_rect(topright=(window.get_width() - 20, 20))
        back = pg.Surface(clock_rect.inflate(12, 26).size, pg.SRCALPHA)
        pg.draw.rect(back, (255, 255, 255, 200), back.get_rect(), border_radius=8)
        window.blit(back, clock_rect.inflate(12, 26).move(0, 9).topleft)
        self.metrics_panel.draw(window, time)
        self.filter_panel.draw(window)
        self.renderer.draw_attribution(window)
        pg.draw.circle(window, (0, 255, 0), pg.mouse.get_pos(), 5)
        window.blit(clock_text, clock_rect)

        draw_toolbar(self.window, list(self.buttons.values()))
        self.case_manager.draw(self.window)


if __name__ == '__main__':
    LOGGER.info(f"Simulation Start: {datetime.now().isoformat()}")
    Simulation()
    LOGGER.info(f"Simulation End: {datetime.now().isoformat()}")