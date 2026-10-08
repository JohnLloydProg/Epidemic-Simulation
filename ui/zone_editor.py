"""
zone_editor.py — show the barangay zones and choose hotspots.

Z ............ show / hide the zone overlay (hotspots are always tinted red, even with the overlay hidden)
H ............ hotspot mode (only before the simulation starts, like route editing; press Reset to change later)
    hover a zone ..... highlight it and show its name, role, district and population
    click a zone ..... make it a hotspot / remove it
    0-9 (hovering) ... trip limit for that barangay: 0 = no trips, 1-9 = 10%-90% of its trips (to and from it)
    Backspace ........ remove the hovered barangay's trip limit
    T (hovering) ..... switch the barangay's tricycles off / on (routing and OD are rebuilt when you leave
                       this mode or press Play, since that takes about a minute)
    C ................ clear all hotspots
    S ................ save hotspots and route edits as a case file (same as S in route edit mode)
    H / Esc .......... leave hotspot mode
Dragging still pans the map; only a click without dragging toggles a zone.

Hotspots are stored on each zone (region.is_hotspot) and saved to the case file's "hotspots" list (PSGC codes),
which the data loader reads at start-up. With OD demand on, every change recomputes the OD matrix: a hotspot's
attraction is multiplied by HOTSPOT_ATTRACTION (agents/od_demand.py), so fewer trips end there, and the queued
OD agents are replaced. The tooltip shows the OD agents arriving in and leaving each zone.
Trip limits are the case file's "od_scaling" ({psgc: factor}): every trip starting or ending in the barangay is
multiplied by the factor and the rest are simply not made (unlike a hotspot, whose trips go elsewhere).
While the simulation runs, the HUD and the occupancy graph (G) show the hotspot metrics (metrics.py).
"""
from __future__ import annotations
import math

import pygame as pg

CLICK_TOLERANCE_PX = 6

COLORS = {                      # fill RGBA per zone kind
    'hotspot': (230, 40, 40, 95),
    'od': (60, 140, 230, 38),
    'connector': (150, 150, 150, 28),
}
HOVER_OUTLINE = (255, 170, 0)


def point_in_polygon(x:float, y:float, polygon:list) -> bool:
    """Ray casting; polygon is a list of (x, y)."""
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def hotspot_zones(city) -> list:
    return [region for region in getattr(city, 'zones', {}).values() if getattr(region, 'is_hotspot', False)]


class ZoneEditor:
    LOCKED_MSG = "The simulation has started. Press Reset to go back to the start and change hotspots."

    def __init__(self, sim):
        self.sim = sim
        self.show = False               # zone overlay (Z)
        self.active = False             # hotspot mode (H)
        self.hover = None
        self.status = ''
        self._down = None
        self.font = pg.font.Font(None, 20)
        self.small = pg.font.Font(None, 17)
        self.zones = [r for r in getattr(sim.graph, 'zones', {}).values() if len(getattr(r, 'polygon', [])) >= 3]
        self._bbox = {id(r): (min(p[0] for p in r.polygon), min(p[1] for p in r.polygon),
                              max(p[0] for p in r.polygon), max(p[1] for p in r.polygon)) for r in self.zones}

    # ------------------------------------------------------------------ helpers
    @property
    def camera(self):
        return self.sim.graph.camera

    def zone_at(self, screen_pos):
        x, y = self.camera.to_world(screen_pos)
        for region in self.zones:
            x0, y0, x1, y1 = self._bbox[id(region)]
            if x0 <= x <= x1 and y0 <= y <= y1 and point_in_polygon(x, y, region.polygon):
                return region
        return None

    @staticmethod
    def short_name(region) -> str:
        return str(region.name).replace('Barangay ', '')

    def _over_button(self, pos) -> bool:
        return any(b.rect.collidepoint(pos) for b in self.sim.buttons.values())

    def _hotspot_text(self) -> str:
        hot = hotspot_zones(self.sim.graph)
        return f"Hotspots ({len(hot)}): " + (", ".join(self.short_name(r) for r in hot) if hot else "none")

    # ------------------------------------------------------------------ input
    def handle_event(self, event, time:int) -> bool:
        """Returns True when the event was used (the simulation should ignore it)."""
        if event.type == pg.KEYDOWN and event.key == pg.K_z:
            self.show = not self.show
            return True
        if event.type == pg.KEYDOWN and event.key == pg.K_h:
            if self.active and not self.sim.started and getattr(self.sim, 'network_dirty', False) \
                    and hasattr(self.sim, 'road_editor'):
                self.sim.road_editor._rebuild()          # tricycles were switched: rebuild once on leaving
            self.active = not self.active
            self.hover = None
            if self.active:
                editor = getattr(self.sim, 'editor', None)
                if editor is not None:            # one edit mode at a time
                    editor.active = False
                    editor._clear_selection()
                self.status = (self.LOCKED_MSG if self.sim.started
                               else "HOTSPOT MODE — click a barangay to make it a hotspot (click again to remove).")
            return True
        if not self.active:
            return False
        if getattr(getattr(self.sim, 'editor', None), 'active', False):   # route editing took over (E)
            self.active = False
            return False

        if event.type == pg.MOUSEMOTION:
            self.hover = self.zone_at(event.pos)
            return False
        if event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
            if not self.sim.started and getattr(self.sim, 'network_dirty', False) and hasattr(self.sim, 'road_editor'):
                self.sim.road_editor._rebuild()
            self.active = False
            return True
        if self.sim.started:                      # view only after the start
            return False
        digit = None
        if event.type == pg.KEYDOWN:
            if pg.K_0 <= event.key <= pg.K_9:
                digit = event.key - pg.K_0
            elif pg.K_KP0 <= event.key <= pg.K_KP9:
                digit = event.key - pg.K_KP0
        if digit is not None or (event.type == pg.KEYDOWN and event.key in (pg.K_BACKSPACE, pg.K_DELETE)):
            region = self.hover
            if region is None:
                self.status = "Hover over a barangay, then press 0-9 for its trip limit (Backspace removes it)."
                return True
            region.od_scale = 1.0 if digit is None else digit / 10
            note = ("no trip limit" if region.od_scale == 1.0 else
                    "no trips to or from it" if region.od_scale == 0 else f"trips limited to {region.od_scale:.0%}")
            self.status = f"{region.name}: {note}." + self._recompute_od()
            return True
        if event.type == pg.KEYDOWN and event.key == pg.K_t:
            from transport.tricycle import service_for
            region = self.hover
            service = service_for(region.name) if region is not None else None
            if service is None:
                self.status = "Hover over a barangay with tricycles, then press T to switch them off or on."
                return True
            service.enabled = not service.enabled
            self.sim.network_dirty = True
            self.status = (f"{region.name}: tricycles {'ON' if service.enabled else 'OFF'}. "
                           "Routing and OD are rebuilt when you leave H mode or press Play.")
            return True
        if event.type == pg.KEYDOWN and event.key == pg.K_c:
            for region in self.zones:
                region.is_hotspot = False
            self.status = "All hotspots cleared." + self._recompute_od()
            return True
        if event.type == pg.KEYDOWN and event.key == pg.K_s:
            self.sim.case_manager.open_save()           # "Save case as" box (ui/case_manager.py)
            return True
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            self._down = event.pos
            return False                          # the camera may start a drag
        if event.type == pg.MOUSEBUTTONUP and event.button == 1 and self._down is not None:
            moved = math.hypot(event.pos[0] - self._down[0], event.pos[1] - self._down[1])
            self._down = None
            if moved <= CLICK_TOLERANCE_PX and not self._over_button(event.pos):
                self._toggle(event.pos)
            return False
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 3:
            return True                           # no test agents while choosing hotspots
        return False

    def _toggle(self, pos):
        region = self.zone_at(pos)
        if region is None:
            self.status = "Click inside a barangay of the study area."
            return
        region.is_hotspot = not getattr(region, 'is_hotspot', False)
        self.status = (f"{region.name} is {'now a HOTSPOT' if region.is_hotspot else 'no longer a hotspot'}."
                       + self._recompute_od())

    def _od_on(self) -> bool:
        import configuration as config
        return bool(config.get('OD_BUNDLE_DIR')) and hasattr(self.sim, 'reschedule_od_demand')

    def _recompute_od(self) -> str:
        """Recompute the OD matrix for the current hotspots and replace the queued agents. Returns a status note."""
        if not self._od_on():
            return " " + self._hotspot_text()
        self._show("Recomputing the OD matrix for the hotspots...")
        self.sim.reschedule_od_demand()
        summary = self.sim.od_summary or {}
        to_hot = sum(summary.get('agents_by_dest_zone', {}).get(name, 0) for name in summary.get('hotspots', []))
        factor = summary.get('hotspot_attraction', 1.0)
        return (f" OD recomputed (hotspot attraction x{factor:g}): {summary.get('agents_scheduled', 0):,} agents, "
                f"{to_hot:,} ending in hotspots. " + self._hotspot_text())

    def _show(self, text:str):
        window = pg.display.get_surface()
        if window is None:
            return
        h = window.get_height()
        pg.draw.rect(window, (30, 30, 30), pg.Rect(0, h - 48, window.get_width(), 48))
        window.blit(self.font.render(text, True, (255, 255, 255)), (10, h - 42))
        pg.display.update()

    # ------------------------------------------------------------------ drawing
    def draw_zones(self, window:pg.Surface):
        """Zone fills and outlines; call before the road network so roads stay on top."""
        overlay = self.show or self.active or getattr(getattr(self.sim, 'facility_editor', None), 'active', False)
        hot = [r for r in self.zones if getattr(r, 'is_hotspot', False) or getattr(r, 'od_scale', 1.0) != 1.0]
        if not overlay and not hot:
            return
        cam = self.camera
        layer = pg.Surface(window.get_size(), pg.SRCALPHA)
        for region in (self.zones if overlay else hot):
            pts = [cam.to_screen(p) for p in region.polygon]
            kind = 'hotspot' if getattr(region, 'is_hotspot', False) else ('od' if region.role == 'od' else 'connector')
            pg.draw.polygon(layer, COLORS[kind], pts)
            outline = (200, 30, 30, 220) if kind == 'hotspot' else (90, 90, 90, 160)
            pg.draw.polygon(layer, outline, pts, 2 if kind == 'hotspot' else 1)
            if getattr(region, 'od_scale', 1.0) != 1.0:      # trip limit: purple border
                pg.draw.polygon(layer, (130, 40, 190, 230), pts, 3)
        window.blit(layer, (0, 0))

    def draw_labels(self, window:pg.Surface):
        """Zone numbers, hover highlight, tooltip and status bar; call after everything else."""
        if self.active and getattr(getattr(self.sim, 'editor', None), 'active', False):
            self.active = False                   # E switched to route editing
        if self.active and self.sim.started:
            self.status = self.LOCKED_MSG
        elif self.active and self.status == self.LOCKED_MSG:
            self.status = "HOTSPOT MODE — click a barangay to make it a hotspot (click again to remove)."
        overlay = self.show or self.active or getattr(getattr(self.sim, 'facility_editor', None), 'active', False)
        cam = self.camera
        for region in self.zones:
            hot = getattr(region, 'is_hotspot', False)
            limit = getattr(region, 'od_scale', 1.0)
            if not (overlay or hot or limit != 1.0):
                continue
            cx = sum(p[0] for p in region.polygon) / len(region.polygon)
            cy = sum(p[1] for p in region.polygon) / len(region.polygon)
            from transport.tricycle import service_for
            service = service_for(region.name)
            text = (self.short_name(region) + (f" · {limit:.0%}" if limit != 1.0 else "")
                    + (" · no trike" if service is not None and not service.enabled else "")
                    + (" · F" if region.name in getattr(self.sim, 'zone_facilities', {}) else ""))
            label = self.small.render(text, True, (170, 20, 20) if hot else (110, 30, 160) if limit != 1.0 else (60, 60, 60))
            window.blit(label, label.get_rect(center=cam.to_screen((cx, cy))))

        if not self.active:
            return
        if self.hover is not None:
            pg.draw.polygon(window, HOVER_OUTLINE, [cam.to_screen(p) for p in self.hover.polygon], 3)
            r = self.hover
            lines = [f"{r.name}" + ("  — HOTSPOT" if getattr(r, 'is_hotspot', False) else ""),
                     f"{'OD zone' if r.role == 'od' else 'connector (no OD trips)'} · {r.district or ''}",
                     f"population 2024: {int(r.population):,}" if r.population else "population: n/a",
                     ("trip limit: none (0-9 to set)" if getattr(r, 'od_scale', 1.0) == 1.0 else
                      f"trip limit: {r.od_scale:.0%} of trips to/from here")]
            from transport.tricycle import service_for
            service = service_for(r.name)
            if service is not None:
                lines.append(f"tricycles: {'on' if service.enabled else 'OFF'} (T) · territory {len(service.node_ids)} nodes")
            summary = getattr(self.sim, 'od_summary', None) or {}
            if 'agents_by_dest_zone' in summary:
                lines.append(f"OD agents ending here: {summary['agents_by_dest_zone'].get(r.name, 0):,}, "
                             f"starting here: {summary['agents_by_origin_zone'].get(r.name, 0):,}")
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
        window.blit(self.font.render(self.status, True, (255, 255, 255)), (10, h - 42))
        help_text = ("H / Esc close" if self.sim.started else
                     "H close | click: hotspot on/off | hover + 0-9: trip limit, Backspace: none | C clear hotspots | S save | " + self._hotspot_text())
        window.blit(self.font.render(help_text[:150], True, (180, 180, 180)), (10, h - 22))
