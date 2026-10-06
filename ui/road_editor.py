"""
road_editor.py — close and reopen roads (before the simulation starts; press Reset to change later).

X ............ road closure mode
    hover a road ..... highlight it: name, type, length and how many routes use it
    click a road ..... close it / reopen it. Routes using it detour around it at once (or are removed if
                       there is no way around; see transport/closures.py)
    C ................ reopen every road closed in this session
    Enter ............ rebuild the routing cache and OD demand now
    S ................ save everything as a case file (same as S in the other modes)
    X / Esc .......... leave the mode (rebuilds if anything changed)
Closed roads are drawn in red at all times. Roads closed by the case file itself are drawn dark red; they
cannot be reopened here (edit the case's "closed_edges" instead).
Rebuilding takes about a minute, so it runs once when you leave the mode, press Enter, or press Play.
"""
from __future__ import annotations
import math

import pygame as pg

from transport.closures import close_edge, reopen_edge, update_routes, finish_network_changes

CLICK_TOLERANCE_PX = 6
PICK_RADIUS_PX = 10


def _dist_to_segment(p, a, b) -> float:
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class RoadEditor:
    LOCKED_MSG = "The simulation has started. Press Reset to go back to the start and change road closures."

    def __init__(self, sim):
        self.sim = sim
        self.active = False
        self.hover = None
        self.status = ''
        self._down = None
        self.font = pg.font.Font(None, 20)
        self.small = pg.font.Font(None, 17)

    @property
    def camera(self):
        return self.sim.graph.camera

    def _other_editors(self):
        return [e for e in (getattr(self.sim, 'editor', None), getattr(self.sim, 'zone_editor', None),
                            getattr(self.sim, 'facility_editor', None)) if e is not None]

    def _over_button(self, pos) -> bool:
        return any(b.rect.collidepoint(pos) for b in self.sim.buttons.values())

    def _edge_at(self, pos):
        best, best_d = None, PICK_RADIUS_PX
        candidates = list(self.sim.graph.edges.values()) + list(self.sim.closed_edges.values())
        for edge in candidates:
            if edge.id[0] != 'city':
                continue
            d = _dist_to_segment(pos, self.camera.to_screen(edge.nodes[0].pos), self.camera.to_screen(edge.nodes[1].pos))
            if d < best_d:
                best, best_d = edge, d
        return best

    def _routes_using(self, edge) -> int:
        ids = set()
        for route_id, (spawn, path) in self.sim.route_loaded.items():
            intended = self.sim.route_edits.get(route_id, (spawn, path))[1]
            if any(e.id == edge.id for e in intended):
                ids.add(route_id)
        return len(ids)

    @staticmethod
    def _road_label(edge) -> str:
        name = getattr(edge, 'road_name', None)
        name = name if isinstance(name, str) and name else 'unnamed road'
        return f"{name} (edge {edge.id[1]}, {edge.distance} m)"

    def _rebuild(self, reason:str = ''):
        if not self.sim.network_dirty:
            return
        self._show("Rebuilding the routing cache and OD demand for the road changes...")
        self.status = (reason + " " if reason else "") + "Done: " + finish_network_changes(self.sim, self._progress)

    def _show(self, text:str):
        window = pg.display.get_surface()
        if window is None:
            return
        h = window.get_height()
        pg.draw.rect(window, (30, 30, 30), pg.Rect(0, h - 48, window.get_width(), 48))
        window.blit(self.font.render(text, True, (255, 255, 255)), (10, h - 42))
        pg.display.update()

    def _progress(self, done:int, total:int):
        pg.event.pump()
        if done == total or done % 10 == 0:
            self._show(f"Rebuilding routing cache: {done}/{total} origins...")

    # ------------------------------------------------------------------ input
    def handle_event(self, event, time:int) -> bool:
        if event.type == pg.KEYDOWN and event.key == pg.K_x:
            if self.active:
                self.active = False
                if not self.sim.started:
                    self._rebuild()
            else:
                self.active = True
                for other in self._other_editors():          # one edit mode at a time
                    other.active = False
                    if hasattr(other, '_clear_selection'):
                        other._clear_selection()
                self.status = (self.LOCKED_MSG if self.sim.started
                               else "ROAD CLOSURE MODE — click a road to close it (click again to reopen).")
            return True
        if not self.active:
            return False
        if any(getattr(o, 'active', False) for o in self._other_editors()):
            self.active = False                               # E or H took over
            return False

        if event.type == pg.MOUSEMOTION:
            self.hover = self._edge_at(event.pos)
            return False
        if event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
            self.active = False
            if not self.sim.started:
                self._rebuild()
            return True
        if self.sim.started:
            return False
        if event.type == pg.KEYDOWN and event.key in (pg.K_RETURN, pg.K_KP_ENTER):
            self._rebuild()
            return True
        if event.type == pg.KEYDOWN and event.key == pg.K_c:
            for edge_id in list(self.sim.closed_edges):
                reopen_edge(self.sim, edge_id)
            result = update_routes(self.sim)
            self.status = f"All roads reopened. {self._route_note(result)} Leave the mode or press Enter to rebuild."
            return True
        if event.type == pg.KEYDOWN and event.key == pg.K_s:
            from transport.route_editor import save_case, RouteChangeError
            self._rebuild()
            try:
                self.status = f"Saved case: {save_case(self.sim)}"
            except RouteChangeError as e:
                self.status = str(e)
            return True
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            self._down = event.pos
            return False
        if event.type == pg.MOUSEBUTTONUP and event.button == 1 and self._down is not None:
            moved = math.hypot(event.pos[0] - self._down[0], event.pos[1] - self._down[1])
            self._down = None
            if moved <= CLICK_TOLERANCE_PX and not self._over_button(event.pos):
                self._toggle(event.pos)
            return False
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 3:
            return True
        return False

    @staticmethod
    def _route_note(result:dict) -> str:
        parts = []
        if result['detoured']:
            parts.append(f"{len(result['detoured'])} route(s) detour around closed roads")
        if result['removed']:
            parts.append(f"{len(result['removed'])} removed (no way around)")
        return (", ".join(parts) + ".") if parts else "No route uses a closed road."

    def _toggle(self, pos):
        edge = self._edge_at(pos)
        if edge is None:
            self.status = "No road under the click — zoom in a little."
            return
        if edge.id in self.sim.closed_edges:
            reopen_edge(self.sim, edge.id)
            verb = "Reopened"
        else:
            close_edge(self.sim, edge)
            verb = "Closed"
        result = update_routes(self.sim)
        self.status = (f"{verb} {self._road_label(edge)}. {self._route_note(result)} "
                       f"{len(self.sim.closed_edges)} road(s) closed.")

    # ------------------------------------------------------------------ drawing
    def draw(self, window:pg.Surface):
        if self.active and any(getattr(o, 'active', False) for o in self._other_editors()):
            self.active = False                               # E or H took over
        cam = self.camera
        width = max(4, cam.scale(6))
        for a, b in getattr(self.sim.graph, 'case_closed_edges', {}).values():
            pg.draw.line(window, (120, 0, 0), cam.to_screen(a), cam.to_screen(b), width)
        for edge in self.sim.closed_edges.values():
            a, b = cam.to_screen(edge.nodes[0].pos), cam.to_screen(edge.nodes[1].pos)
            pg.draw.line(window, (220, 0, 0), a, b, width)
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            pg.draw.line(window, (255, 255, 255), (mx - 4, my - 4), (mx + 4, my + 4), 2)
            pg.draw.line(window, (255, 255, 255), (mx - 4, my + 4), (mx + 4, my - 4), 2)
        if not self.active:
            return
        if self.sim.started:
            self.status = self.LOCKED_MSG
        elif self.status == self.LOCKED_MSG:
            self.status = "ROAD CLOSURE MODE — click a road to close it (click again to reopen)."
        if self.hover is not None:
            edge = self.hover
            pg.draw.line(window, (255, 170, 0), cam.to_screen(edge.nodes[0].pos), cam.to_screen(edge.nodes[1].pos), width + 3)
            closed = edge.id in self.sim.closed_edges
            lines = [self._road_label(edge) + ("  — CLOSED" if closed else ""),
                     f"type: {getattr(edge, 'highway', None) or 'n/a'} · routes using it: {self._routes_using(edge)}"]
            surfaces = [self.small.render(t, True, (20, 20, 20)) for t in lines]
            w = max(s.get_width() for s in surfaces) + 12
            h = sum(s.get_height() + 3 for s in surfaces) + 8
            mx, my = pg.mouse.get_pos()
            box = pg.Rect(min(mx + 14, window.get_width() - w - 4), min(my + 14, window.get_height() - 52 - h), w, h)
            pg.draw.rect(window, (255, 255, 240), box)
            pg.draw.rect(window, (80, 80, 80), box, 1)
            y = box.top + 5
            for s in surfaces:
                window.blit(s, (box.left + 6, y))
                y += s.get_height() + 3

        h = window.get_height()
        pg.draw.rect(window, (30, 30, 30), pg.Rect(0, h - 48, window.get_width(), 48))
        window.blit(self.font.render(self.status[:170], True, (255, 255, 255)), (10, h - 42))
        pending = " | changes not rebuilt yet" if self.sim.network_dirty else ""
        help_text = ("X / Esc close" if self.sim.started else
                     "X close (rebuilds) | click a road: close/reopen | C reopen all | Enter rebuild now | S save case" + pending)
        window.blit(self.font.render(help_text, True, (180, 180, 180)), (10, h - 22))
