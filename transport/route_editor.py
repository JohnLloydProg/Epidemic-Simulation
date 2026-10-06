"""
route_editor.py — change the path of a jeepney or bus route BEFORE the simulation starts.

Routes can only be edited before the simulation starts (Play not yet pressed, no agents added). Once it has
started, press the Reset button: it goes back to the start time (SIM_START_HOUR), re-queues the OD agents and
keeps the edits made so far, so you can change more.

Press E to open edit mode, then:
    left-click a route ........ select it (its start is marked with a square)
    left-click nodes .......... waypoints of the new path, in order:
                                  1st  = a stop ON the route where the detour leaves it
                                  last = another stop on the route  -> only that section is replaced
                                         any other node             -> the route now ends there (new terminal)
                                Roads between waypoints follow the shortest road path.
    right-click / Backspace ... undo the last waypoint
    Enter ..................... apply the change
    R ......................... restore the selected route to its original path
    F ......................... clear the whole routing cache (every trip is planned fresh; slower)
    S ......................... save all edits as a new case file in sim_data/cases/
    Esc ....................... deselect, or close edit mode
Dragging still pans the map; only a click without dragging counts as a pick.

Applying a change updates both directions of the route and drops the cached trip plans that used it
(they are recomputed when an agent first needs them).
"""
from __future__ import annotations
import json
import logging
import math

import pygame as pg

from graphing.core import Edge, Node
from graphing.mapping import shortest_edge_path, _route_index

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


def drop_cached_trips(sim, changed_routes:set, everything:bool = False) -> int:
    """Remove cached trip plans that use the changed routes (or all of them). Returns how many were dropped."""
    _route_index.clear()                      # stop -> routes index used by shortest_path
    table = sim.routing_table
    if everything:
        dropped = len(table)
        table.clear()
        return dropped
    stale = [key for key, cps in table.items() if any(cp.route in changed_routes for cp in cps)]
    for key in stale:
        del table[key]
    return len(stale)


def apply_route_change(sim, route_id:str, spawn:Node, new_path:list[Edge]) -> str:
    """Give route `route_id` a new path (both directions). Only allowed before the simulation starts."""
    if sim.started:
        raise RouteChangeError("The simulation has started — press Reset first.")
    group = route_group(sim.routes, route_id)
    if not group:
        raise RouteChangeError(f"Route '{route_id}' not found.")
    forward = group[0]
    if forward.graph.layer != 'city':
        raise RouteChangeError("Only road routes (jeepney/bus) can be edited.")
    end = chain_end(spawn, new_path)

    sim.route_originals.setdefault(route_id, (forward.spawn_node, list(forward.path)))
    forward.set_path(spawn, new_path)
    for reverse in group[1:]:
        reverse.set_path(end, list(reversed(new_path)))
    sim.route_edits[route_id] = (spawn, list(new_path))

    dropped = drop_cached_trips(sim, set(group))
    msg = (f"{route_id}: {len(new_path)} edges, {sum(e.distance for e in new_path) / 1000:.2f} km "
           f"({dropped} cached trips will be re-planned)")
    LOGGER.info(f"Route changed — {msg}")
    return msg


def restore_route(sim, route_id:str) -> str:
    if route_id not in sim.route_originals:
        return f"{route_id} has not been changed."
    spawn, path = sim.route_originals[route_id]
    return "Restored — " + apply_route_change(sim, route_id, spawn, path)


def save_case(sim) -> str:
    """Write the current case plus all route edits as a new case file (transit_overrides)."""
    from graphing.data_loader import load_case, data_dir
    if not sim.route_edits:
        raise RouteChangeError("No route changes to save.")
    case = load_case()
    overrides = dict(case.get('transit_overrides', {}))
    for route_id, (spawn, path) in sim.route_edits.items():
        overrides[route_id] = {**overrides.get(route_id, {}), 'start_node': spawn.id[1],
                               'edges': [edge.id[1] for edge in path]}
    case_id = case['case_id'] if case['case_id'].endswith('_edited') else case['case_id'] + '_edited'
    new_case = {**case, 'case_id': case_id, 'transit_overrides': overrides,
                'description': (case.get('description') or '') + f" | route edits: {', '.join(sorted(sim.route_edits))}"}
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

    def _nearest_route(self, pos):
        best, best_d = None, PICK_RADIUS_PX
        for route in self._editable_routes():
            pts = [self.camera.to_screen(n.pos) for n in route.ordered_nodes]
            for a, b in zip(pts, pts[1:]):
                d = _dist_to_segment(pos, a, b)
                if d < best_d:
                    best, best_d = route, d
        return best

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
            drop_cached_trips(self.sim, set(), everything=True)
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
            self.status = restore_route(self.sim, self.route.route_id)
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
            self.route = self._nearest_route(pos)
            if self.route is not None:
                self.status = (f"Selected {self.route.route_id} ({getattr(self.route, 'name', '')[:50]}). "
                               "Click the stop where the detour starts.")
            return
        if not self.waypoints:
            node = self._nearest_node(pos, self.route.ordered_nodes)
            if node is None:
                other = self._nearest_route(pos)
                if other is not None and other is not self.route:
                    self.route = other
                    self.status = f"Selected {other.route_id}. Click the stop where the detour starts."
                else:
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
            self.status = apply_route_change(self.sim, self.route.route_id, spawn, path)
            self.waypoints, self.segments = [], []
        except RouteChangeError as e:
            self.status = str(e)

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

        h = window.get_height()
        pg.draw.rect(window, (30, 30, 30), pg.Rect(0, h - 48, window.get_width(), 48))
        window.blit(self.font.render(self.status, True, (255, 255, 255)), (10, h - 42))
        help_text = ("E / Esc close" if self.sim.started else
                     "E close | click: select / waypoint | Enter apply | Backspace undo | R restore | F clear cache | S save case | Esc deselect")
        window.blit(self.font.render(help_text, True, (180, 180, 180)), (10, h - 22))
