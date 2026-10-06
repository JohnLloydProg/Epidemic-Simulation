"""
route_editor.py — change the path of a jeepney or bus route BEFORE the simulation starts.

Routes can only be edited before the simulation starts (Play not yet pressed, no agents added). Once it has
started, press the Reset button: it goes back to the start time (SIM_START_HOUR), re-queues the OD agents and
keeps the edits made so far, so you can change more.

Press E to open edit mode, then:
    left-click a route ........ select it (its start is marked with a square). Where several routes overlap,
                                a dropdown lists them: hover a row to see that route on the map, click it
                                (or Up/Down + Enter) to choose, type to filter by id or name, mouse
                                wheel to scroll, Esc to cancel
    left-click nodes .......... waypoints of the new path, in order:
                                  1st  = a stop ON the route where the detour leaves it
                                  last = another stop on the route  -> only that section is replaced
                                         any other node             -> the route now ends there (new terminal)
                                Roads between waypoints follow the shortest road path.
    right-click / Backspace ... undo the last waypoint
    Enter ..................... apply the change
    R ......................... restore the selected route to its original path
    S ......................... save all edits (and the hotspots, see ui/zone_editor.py) as a new case file
    Esc ....................... deselect, or close edit mode
Dragging still pans the map; only a click without dragging counts as a pick.

Applying (or restoring) a change updates both directions of the route and rebuilds the whole routing cache
with the routes as they now are, so every trip is planned on the current network before the run starts.
When OD demand is on (OD_BUNDLE_DIR), the OD matrix is also recomputed for the new routes
(agents/od_routes.py) and the queued OD agents are replaced.
The rebuild stays in memory; press S and run the saved case to get a cache file for the edited routes.
"""
from __future__ import annotations
import json
import logging
import math

import pygame as pg

from graphing.core import Edge, Node
from graphing.mapping import shortest_edge_path
from transport.transportation import set_route_group_path
import configuration as config

LOGGER = logging.getLogger('RouteEditor')

CLICK_TOLERANCE_PX = 6      # mouse may move this much between press and release and still count as a click
PICK_RADIUS_PX = 14         # how close a click must be to a route or node


class RouteChangeError(ValueError):
    pass


# =============================================================================================== core
def route_group(routes:list, route_id:str) -> list:
    """Route objects sharing a route_id: [forward] or [forward, reverse] (the loader adds them in that order)."""
    return [r for r in routes if getattr(r, 'route_id', None) == route_id]


def chain_end(spawn:Node, path:list[Edge]) -> Node:
    """Last node of a path; raises if the edges do not form a connected chain from spawn."""
    node = spawn
    for edge in path:
        try:
            node = edge.get_adjacent_node(node)
        except ValueError:
            raise RouteChangeError(f"Edge {edge.id} does not connect to node {node.id}; the path is broken.")
    return node


def road_path(a:Node, b:Node, city, railway) -> list[Edge]:
    """Shortest road path between two city nodes."""
    if a is b:
        return []
    path = shortest_edge_path(a.id, b.id, city, railway)
    if not path:
        raise RouteChangeError(f"No road connects node {a.id[1]} to node {b.id[1]}.")
    return list(path)


def detour_path(route, waypoints:list[Node], city, railway) -> tuple[Node, list[Edge]]:
    """New (spawn_node, path) for a route given clicked waypoints (see module docstring)."""
    if len(waypoints) < 2:
        raise RouteChangeError("Click at least two nodes: where the detour starts and where it ends.")
    nodes = route.ordered_nodes
    if waypoints[0] not in nodes:
        raise RouteChangeError("The first waypoint must be a stop on the selected route.")

    # Waypoints clicked against the route's direction: flip them so the detour runs the route's way.
    ia = nodes.index(waypoints[0])
    if waypoints[-1] in nodes[:ia]:
        waypoints = list(reversed(waypoints))
        ia = nodes.index(waypoints[0])

    detour = []
    for a, b in zip(waypoints, waypoints[1:]):
        detour += road_path(a, b, city, railway)

    last = waypoints[-1]
    ib = next((i for i in range(ia + 1, len(nodes)) if nodes[i] is last), None)
    if ib is not None:
        new_path = route.path[:ia] + detour + route.path[ib:]        # replace one section
    else:
        new_path = route.path[:ia] + detour                          # new terminal at `last`
    if not new_path:
        raise RouteChangeError("The new path is empty.")
    chain_end(route.spawn_node, new_path)
    return route.spawn_node, new_path


def apply_route_change(sim, route_id:str, spawn:Node, new_path:list[Edge], on_progress=None) -> str:
    """Give route `route_id` a new path (both directions) and rebuild the routing cache.
    Only allowed before the simulation starts. on_progress(done, total) is passed to the rebuild."""
    from routing_table import rebuild_routing_cache
    if sim.started:
        raise RouteChangeError("The simulation has started — press Reset first.")
    group = route_group(sim.routes, route_id)
    if not group:
        raise RouteChangeError(f"Route '{route_id}' not found.")
    forward = group[0]
    if forward.graph.layer != 'city':
        raise RouteChangeError("Only road routes (jeepney/bus) can be edited.")
    chain_end(spawn, new_path)

    sim.route_originals.setdefault(route_id, (forward.spawn_node, list(forward.path)))
    set_route_group_path(sim.routes, route_id, spawn, new_path)
    sim.route_edits[route_id] = (spawn, list(new_path))

    sim.routing_table = rebuild_routing_cache(sim, on_progress)
    msg = (f"{route_id}: {len(new_path)} edges, {sum(e.distance for e in new_path) / 1000:.2f} km "
           f"(routing cache rebuilt: {len(sim.routing_table):,} trips")
    if hasattr(sim, 'reschedule_od_demand') and config.get('OD_BUNDLE_DIR'):
        sim.reschedule_od_demand()               # OD matrix follows the new route; agents re-queued
        msg += f"; OD recomputed: {sim.od_summary['agents_scheduled']:,} agents"
    msg += ")"
    LOGGER.info(f"Route changed — {msg}")
    return msg


def restore_route(sim, route_id:str, on_progress=None) -> str:
    if route_id not in sim.route_originals:
        return f"{route_id} has not been changed."
    spawn, path = sim.route_originals[route_id]
    return "Restored — " + apply_route_change(sim, route_id, spawn, path, on_progress)


def save_case(sim) -> str:
    """Write the current case plus all route edits (transit_overrides) and the current hotspots as a new case file."""
    from graphing.data_loader import load_case, data_dir
    case = load_case()
    hotspots = sorted(str(r.psgc) for r in getattr(sim.graph, 'zones', {}).values() if getattr(r, 'is_hotspot', False))
    if not sim.route_edits and hotspots == sorted(map(str, case.get('hotspots', []))):
        raise RouteChangeError("Nothing to save: no route edits and no hotspot changes.")
    overrides = dict(case.get('transit_overrides', {}))
    for route_id, (spawn, path) in sim.route_edits.items():
        overrides[route_id] = {**overrides.get(route_id, {}), 'start_node': spawn.id[1],
                               'edges': [edge.id[1] for edge in path]}
    stem = case['case_id'].split('_edited')[0] + '_edited'
    case_id, n = stem, 1
    while (data_dir() / 'cases' / f'{case_id}.json').exists():      # never overwrite an existing case
        n += 1
        case_id = f'{stem}_{n}'
    names = sorted(r.name for r in sim.graph.zones.values() if getattr(r, 'is_hotspot', False)) if hotspots else []
    description = (case.get('description') or '').split(' | ')[0]
    if sim.route_edits:
        description += f" | route edits: {', '.join(sorted(sim.route_edits))}"
    if names:
        description += f" | hotspots: {', '.join(names)}"
    new_case = {**case, 'case_id': case_id, 'transit_overrides': overrides, 'hotspots': hotspots,
                'description': description}
    if hotspots:                          # keep the run reproducible even if the config default changes
        new_case['hotspot_attraction'] = float(case.get('hotspot_attraction', config.get('HOTSPOT_ATTRACTION', 0.5)))
    path = data_dir() / 'cases' / f'{case_id}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(new_case, f, indent=1)
    return str(path)


# =============================================================================================== UI
def _dist_to_segment(p, a, b) -> float:
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


MODE_COLORS = {'jeepney': (40, 110, 230), 'jeep': (40, 110, 230), 'bus': (220, 50, 50)}


class RoutePicker:
    """Dropdown listing the routes under a click, so overlapping routes can be told apart.
    Hover a row to highlight that route on the map; click a row (or Up/Down + Enter) to choose it;
    type to filter by route id or name (Backspace deletes); the mouse wheel scrolls long lists;
    Esc or a click outside closes it."""
    ROW_H = 22
    MAX_ROWS = 12
    PAD = 6

    def __init__(self, routes:list, pos:tuple, window_size:tuple, font):
        self.all_routes = routes
        self.all_labels = [f"{getattr(r, 'route_id', '?')}  ·  {getattr(r, 'name', '')}"[:70] for r in routes]
        self.query = ''
        self.routes, self.labels = list(routes), list(self.all_labels)
        self.font = font
        self.hover = 0
        self.scroll = 0
        width = max(font.size(label)[0] for label in self.all_labels) + 2 * self.PAD + 18
        width = max(width, font.size(f"{len(routes)} routes here — type to filter, Esc to cancel")[0] + 2 * self.PAD)
        height = (min(len(routes), self.MAX_ROWS) + 1) * self.ROW_H + self.PAD   # fixed size while filtering
        x = min(pos[0] + 8, window_size[0] - width - 4)       # keep the box on screen
        y = min(pos[1] + 8, window_size[1] - 52 - height)     # stay above the status bar
        self.rect = pg.Rect(max(4, x), max(4, y), width, height)

    @property
    def header(self) -> str:
        if self.query:
            return f"Filter: {self.query}_   ({len(self.routes)} of {len(self.all_routes)})"
        return f"{len(self.all_routes)} routes here — type to filter, Esc to cancel"

    @property
    def visible(self) -> int:
        return min(len(self.routes), self.MAX_ROWS)

    def _apply_filter(self):
        words = self.query.lower().split()
        keep = [i for i, label in enumerate(self.all_labels) if all(w in label.lower() for w in words)]
        self.routes = [self.all_routes[i] for i in keep]
        self.labels = [self.all_labels[i] for i in keep]
        self.hover, self.scroll = 0, 0

    def hovered_route(self):
        return self.routes[self.hover] if 0 <= self.hover < len(self.routes) else None

    def _row_at(self, pos):
        if not self.rect.collidepoint(pos):
            return None
        i = (pos[1] - self.rect.top - self.ROW_H) // self.ROW_H
        return self.scroll + i if 0 <= i < self.visible and self.scroll + i < len(self.routes) else None

    def _scroll_to_hover(self):
        if self.hover < self.scroll:
            self.scroll = self.hover
        elif self.hover >= self.scroll + self.visible:
            self.scroll = self.hover - self.visible + 1

    def handle_event(self, event):
        """Returns ('choose', route), ('close', None) or (None, None) while the picker stays open."""
        if event.type == pg.MOUSEMOTION:
            row = self._row_at(event.pos)
            if row is not None:
                self.hover = row
        elif event.type == pg.MOUSEWHEEL:
            self.scroll = max(0, min(len(self.routes) - self.visible, self.scroll - event.y))
        elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            row = self._row_at(event.pos)
            if row is not None:
                return 'choose', self.routes[row]
            if not self.rect.collidepoint(event.pos):
                return 'close', None
        elif event.type == pg.MOUSEBUTTONDOWN and event.button == 3:
            return 'close', None
        elif event.type == pg.KEYDOWN:
            if event.key == pg.K_ESCAPE:
                return 'close', None
            if event.key in (pg.K_RETURN, pg.K_KP_ENTER):
                return 'choose', self.hovered_route()
            if event.key in (pg.K_DOWN, pg.K_UP):
                if self.routes:
                    self.hover = (self.hover + (1 if event.key == pg.K_DOWN else -1)) % len(self.routes)
                    self._scroll_to_hover()
            elif event.key == pg.K_BACKSPACE:
                self.query = self.query[:-1]
                self._apply_filter()
            elif getattr(event, 'unicode', '') and event.unicode.isprintable():
                self.query += event.unicode
                self._apply_filter()
        return None, None

    def draw(self, window:pg.Surface):
        pg.draw.rect(window, (250, 250, 250), self.rect)
        pg.draw.rect(window, (60, 60, 60), self.rect, 1)
        window.blit(self.font.render(self.header, True, (90, 90, 90)), (self.rect.left + self.PAD, self.rect.top + 4))
        if not self.routes:
            window.blit(self.font.render("No route matches — Backspace to edit the filter", True, (150, 60, 60)),
                        (self.rect.left + self.PAD, self.rect.top + self.ROW_H + 4))
        for i in range(self.visible):
            index = self.scroll + i
            route = self.routes[index]
            row = pg.Rect(self.rect.left + 1, self.rect.top + (i + 1) * self.ROW_H, self.rect.width - 2, self.ROW_H)
            if index == self.hover:
                pg.draw.rect(window, (255, 225, 120), row)
            color = MODE_COLORS.get(getattr(route, 'mode', ''), (120, 120, 120))
            pg.draw.rect(window, color, pg.Rect(row.left + self.PAD, row.centery - 5, 10, 10))
            window.blit(self.font.render(self.labels[index], True, (20, 20, 20)), (row.left + self.PAD + 16, row.top + 4))
        if len(self.routes) > self.visible:                    # scroll bar
            track = pg.Rect(self.rect.right - 5, self.rect.top + self.ROW_H, 3, self.visible * self.ROW_H)
            size = max(10, track.height * self.visible // len(self.routes))
            top = track.top + (track.height - size) * self.scroll // max(1, len(self.routes) - self.visible)
            pg.draw.rect(window, (170, 170, 170), pg.Rect(track.left, top, 3, size))


class RouteEditor:
    LOCKED_MSG = "The simulation has started. Press Reset to go back to the start and change routes."

    def __init__(self, sim):
        self.sim = sim
        sim.route_edits = {}           # route_id -> (spawn_node, path) currently applied
        sim.route_originals = {}       # route_id -> (spawn_node, path) as loaded
        self.active = False
        self.route = None              # selected forward-direction Route
        self.waypoints:list[Node] = []
        self.segments:list[list[Edge]] = []   # preview: road path between consecutive waypoints
        self.status = ''
        self._down = None
        self.picker:RoutePicker | None = None
        self.font = pg.font.Font(None, 20)

    # ------------------------------------------------------------------ helpers
    @property
    def camera(self):
        return self.sim.graph.camera

    def _editable_routes(self):
        seen = set()
        for r in self.sim.routes:
            rid = getattr(r, 'route_id', None)
            if r.graph.layer == 'city' and rid not in seen:
                seen.add(rid)
                yield r

    def _routes_near(self, pos) -> list:
        """Every editable route passing within PICK_RADIUS_PX of pos, nearest first."""
        found = []
        for route in self._editable_routes():
            pts = [self.camera.to_screen(n.pos) for n in route.ordered_nodes]
            d = min((_dist_to_segment(pos, a, b) for a, b in zip(pts, pts[1:])), default=math.inf)
            if d < PICK_RADIUS_PX:
                found.append((round(d), getattr(route, 'mode', ''), getattr(route, 'route_id', ''), route))
        return [entry[-1] for entry in sorted(found, key=lambda e: e[:3])]

    def _pick_route(self, pos, exclude=None) -> bool:
        """Select the route at pos; open the dropdown when several overlap. False if there is none."""
        routes = [r for r in self._routes_near(pos) if r is not exclude]
        if not routes:
            return False
        if len(routes) == 1:
            self._select(routes[0])
        else:
            surface = pg.display.get_surface()
            self.picker = RoutePicker(routes, pos, surface.get_size() if surface else (1080, 720), self.font)
            self.status = f"{len(routes)} routes overlap here — choose one from the list."
        return True

    def _select(self, route):
        self.route, self.waypoints, self.segments = route, [], []
        self.status = (f"Selected {route.route_id} ({getattr(route, 'name', '')[:50]}). "
                       "Click the stop where the detour starts.")

    def _nearest_node(self, pos, candidates):
        best, best_d = None, PICK_RADIUS_PX
        for node in candidates:
            sx, sy = self.camera.to_screen(node.pos)
            d = math.hypot(sx - pos[0], sy - pos[1])
            if d < best_d:
                best, best_d = node, d
        return best

    def _over_button(self, pos) -> bool:
        return any(b.rect.collidepoint(pos) for b in self.sim.buttons.values())

    def _clear_selection(self):
        self.route, self.waypoints, self.segments = None, [], []
        self.picker = None

    # ------------------------------------------------------------------ input
    def handle_event(self, event, time:int) -> bool:
        """Returns True when the event was used by the editor (the simulation should ignore it)."""
        if event.type == pg.KEYDOWN and event.key == pg.K_e:
            self.active = not self.active
            self._clear_selection()
            if self.active:
                self.status = self.LOCKED_MSG if self.sim.started else "EDIT MODE — click a jeepney/bus route to select it."
            return True
        if not self.active:
            return False
        if self.sim.started:                  # view only: just let E / Esc close it
            if event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
                self.active = False
                return True
            return False

        if self.picker is not None:               # dropdown open: it takes clicks, the wheel and keys
            if event.type in (pg.MOUSEMOTION, pg.MOUSEWHEEL, pg.MOUSEBUTTONDOWN, pg.KEYDOWN):
                action, route = self.picker.handle_event(event)
                if action == 'choose' and route is not None:
                    self.picker = None
                    self._select(route)
                elif action == 'close':
                    self.picker = None
                    self.status = ("Click the stop where the detour starts." if self.route is not None
                                   else "Click a jeepney/bus route to select it.")
                return event.type != pg.MOUSEMOTION   # motion still reaches the camera
            return False

        if event.type == pg.KEYDOWN:
            return self._key(event.key)
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            self._down = event.pos
            return False                      # let the camera start a possible drag
        if event.type == pg.MOUSEBUTTONUP and event.button == 1 and self._down is not None:
            moved = math.hypot(event.pos[0] - self._down[0], event.pos[1] - self._down[1])
            self._down = None
            if moved <= CLICK_TOLERANCE_PX and not self._over_button(event.pos):
                self._click(event.pos)
            return False                      # the camera still needs the release to end a drag
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 3:
            self._undo()
            return True                       # don't spawn a test agent while editing
        return False

    def _key(self, key) -> bool:
        if key in (pg.K_RETURN, pg.K_KP_ENTER):
            self._apply()
        elif key == pg.K_BACKSPACE:
            self._undo()
        elif key == pg.K_ESCAPE:
            if self.route is not None:
                self._clear_selection()
                self.status = "Click a jeepney/bus route to select it."
            else:
                self.active = False
        elif key == pg.K_r and self.route is not None:
            self.waypoints, self.segments = [], []
            self._show("Restoring route and rebuilding the routing cache...")
            self.status = restore_route(self.sim, self.route.route_id, self._progress)
        elif key == pg.K_s:
            try:
                self.status = f"Saved case: {save_case(self.sim)}"
            except RouteChangeError as e:
                self.status = str(e)
        else:
            return False
        return True

    def _click(self, pos):
        if self.route is None:
            self._pick_route(pos)
            return
        if not self.waypoints:
            node = self._nearest_node(pos, self.route.ordered_nodes)
            if node is None:
                if not self._pick_route(pos, exclude=self.route):
                    self.status = "Click a node ON the selected route (zoom in if they are hard to hit)."
                return
            self.waypoints.append(node)
            self.status = "Now click the nodes the route should pass, ending on the route or at a new terminal."
            return
        node = self._nearest_node(pos, [n for n in self.sim.graph.nodes.values() if n.edges])
        if node is None:
            self.status = "No node near the click — zoom in a little."
            return
        try:
            seg = road_path(self.waypoints[-1], node, self.sim.graph, self.sim.railway_graph)
        except RouteChangeError as e:
            self.status = str(e)
            return
        self.waypoints.append(node)
        self.segments.append(seg)
        ends_on_route = node in self.route.ordered_nodes
        self.status = (f"{len(self.waypoints)} waypoints. Enter = apply "
                       f"({'replace section' if ends_on_route else 'new terminal here'}), Backspace = undo.")

    def _undo(self):
        if self.waypoints:
            self.waypoints.pop()
            if self.segments:
                self.segments.pop()
            self.status = f"{len(self.waypoints)} waypoint(s)."
        elif self.route is not None:
            self.route = None
            self.status = "Click a jeepney/bus route to select it."

    def _apply(self):
        if self.route is None:
            return
        try:
            spawn, path = detour_path(self.route, self.waypoints, self.sim.graph, self.sim.railway_graph)
            self._show("Applying route change and rebuilding the routing cache...")
            self.status = apply_route_change(self.sim, self.route.route_id, spawn, path, self._progress)
            self.waypoints, self.segments = [], []
        except RouteChangeError as e:
            self.status = str(e)

    # ------------------------------------------------------------------ rebuild progress
    def _show(self, text:str):
        """Draw a message in the status bar right away (the main loop is blocked during a rebuild)."""
        window = pg.display.get_surface()
        if window is None:
            return
        h = window.get_height()
        pg.draw.rect(window, (30, 30, 30), pg.Rect(0, h - 48, window.get_width(), 48))
        window.blit(self.font.render(text, True, (255, 255, 255)), (10, h - 42))
        pg.display.update()

    def _progress(self, done:int, total:int):
        pg.event.pump()                       # keep the window responsive while the cache is rebuilt
        if done == total or done % 10 == 0:
            self._show(f"Rebuilding routing cache: {done}/{total} origins...")

    # ------------------------------------------------------------------ drawing
    def draw(self, window:pg.Surface):
        if not self.active:
            return
        if self.sim.started:                  # Play was pressed while editing: drop the half-made edit
            if self.route is not None:
                self._clear_selection()
            self.status = self.LOCKED_MSG
        elif self.status == self.LOCKED_MSG:  # after Reset
            self.status = "EDIT MODE — click a jeepney/bus route to select it."
        cam = self.camera
        if self.route is not None:
            pts = [cam.to_screen(n.pos) for n in self.route.ordered_nodes]
            if len(pts) > 1:
                pg.draw.lines(window, (0, 170, 255), False, pts, max(4, cam.scale(5)))
            sx, sy = cam.to_screen(self.route.spawn_node.pos)
            pg.draw.rect(window, (0, 90, 200), pg.Rect(sx - 7, sy - 7, 14, 14))
        for seg_start, seg in zip(self.waypoints, self.segments):
            node = seg_start
            for edge in seg:
                nxt = edge.get_adjacent_node(node)
                pg.draw.line(window, (230, 0, 200), cam.to_screen(node.pos), cam.to_screen(nxt.pos), max(4, cam.scale(5)))
                node = nxt
        for i, node in enumerate(self.waypoints, 1):
            pos = cam.to_screen(node.pos)
            pg.draw.circle(window, (230, 0, 200), pos, 9)
            label = self.font.render(str(i), True, (255, 255, 255))
            window.blit(label, label.get_rect(center=pos))

        if self.picker is not None:
            hovered = self.picker.hovered_route()
            if hovered is not None:                # show on the map which route the hovered row is
                pts = [cam.to_screen(n.pos) for n in hovered.ordered_nodes]
                if len(pts) > 1:
                    pg.draw.lines(window, (255, 190, 0), False, pts, max(6, cam.scale(7)))
                sx, sy = cam.to_screen(hovered.spawn_node.pos)
                pg.draw.rect(window, (200, 140, 0), pg.Rect(sx - 7, sy - 7, 14, 14))
            self.picker.draw(window)

        h = window.get_height()
        pg.draw.rect(window, (30, 30, 30), pg.Rect(0, h - 48, window.get_width(), 48))
        window.blit(self.font.render(self.status, True, (255, 255, 255)), (10, h - 42))
        help_text = ("E / Esc close" if self.sim.started else
                     "E close | click: select / waypoint | Enter apply | Backspace undo | R restore | S save case | Esc deselect")
        window.blit(self.font.render(help_text, True, (180, 180, 180)), (10, h - 22))