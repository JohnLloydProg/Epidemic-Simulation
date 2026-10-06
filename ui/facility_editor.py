"""
facility_editor.py — close facilities or set how many there are, per barangay (OD library "zone_facilities").

F ............ facility mode (only before the simulation starts; press Reset to change later)
    click a barangay ...... open its facility panel (one row per category: shop, food, work, school, ...)
        checkbox .......... close the category in this barangay (0) / open it again
        -  / + ............ one fewer / one more (hold Shift for 10)
        mouse wheel ....... the same, over a row
        click the number .. type an exact count, Enter to set (Esc cancels)
        Apply ............. recompute the OD matrix and the queued OD agents
        Reset zone ........ back to the counts in the data
        Close / Esc ....... close the panel (applies pending changes)
    click another barangay  switch to it (applies pending changes)
    S ..................... save everything as a case file
    F / Esc ............... leave facility mode

How the OD model uses it (manila_od.py): a barangay's attraction is built from the size of its facilities per
category. Fewer facilities than the data shrink that category's size in proportion (removing average-sized
ones); 0 removes the category; more add facilities of the category's citywide median size. Manila's total
attraction is fixed, so trips go to other places — they are not removed (use trip limits in H mode for that).
Settings live in sim.zone_facilities {barangay name: {category: count}} and are saved as the case's
od_settings["zone_facilities"].
"""
from __future__ import annotations
import math

import pygame as pg

import configuration as config

CLICK_TOLERANCE_PX = 6


class FacilityEditor:
    LOCKED_MSG = "The simulation has started. Press Reset to go back to the start and change facilities."
    ROW_H = 24
    WIDTH = 340

    def __init__(self, sim):
        self.sim = sim
        self.active = False
        self.zone = None                     # region whose panel is open
        self.pending:dict = {}               # {category: count} for self.zone (only differences from the data)
        self.dirty = False
        self.typing = None                   # category whose count is being typed
        self.buffer = ''
        self._last:dict = {}                 # last non-zero count per category, for re-opening
        self.status = ''
        self._down = None
        self._mouse = (0, 0)                 # last mouse position seen (for the wheel)
        self._rects:dict = {}
        self.font = pg.font.Font(None, 20)
        self.small = pg.font.Font(None, 18)
        self._counts = None
        if not hasattr(sim, 'zone_facilities'):
            sim.zone_facilities = {}

    # ------------------------------------------------------------------ data
    def base_counts(self):
        """Facilities per study barangay and category in the data (DataFrame: zone x category)."""
        if self._counts is None:
            from manila_od import ODModel
            model = ODModel.load(config.get('OD_BUNDLE_DIR', 'od_bundle'))
            names = [r.name for r in self.sim.graph.zones.values() if r.name in set(model.names)]
            self._counts = model.facility_counts(names)
        return self._counts

    def categories(self) -> list:
        table = self.base_counts()
        if self.zone is None or self.zone.name not in table.index:
            return list(table.columns)
        row = table.loc[self.zone.name]
        return sorted(table.columns, key=lambda c: (-int(row[c]), c))

    def base(self, category) -> int:
        table = self.base_counts()
        return int(table.loc[self.zone.name, category]) if self.zone is not None and self.zone.name in table.index else 0

    def count(self, category) -> int:
        return int(self.pending.get(category, self.base(category)))

    def set_count(self, category, n:int):
        n = max(0, int(n))
        if n > 0:
            self._last[category] = n
        if n == self.base(category):
            self.pending.pop(category, None)
        else:
            self.pending[category] = n
        self.dirty = True
        total, base_total = sum(self.count(c) for c in self.categories()), sum(self.base(c) for c in self.categories())
        self.status = (f"{self.zone.name}: {category} {n} (data: {self.base(category)}). "
                       f"Facilities {total} of {base_total}. Apply or close the panel to recompute the OD matrix.")

    # ------------------------------------------------------------------ helpers
    @property
    def camera(self):
        return self.sim.graph.camera

    def _others(self):
        return [e for e in (getattr(self.sim, 'editor', None), getattr(self.sim, 'zone_editor', None),
                            getattr(self.sim, 'road_editor', None)) if e is not None]

    def _over_button(self, pos) -> bool:
        return any(b.rect.collidepoint(pos) for b in self.sim.buttons.values())

    def _panel_rect(self, window_size) -> pg.Rect:
        rows = len(self.categories())
        h = (rows + 4) * self.ROW_H + 16
        return pg.Rect(window_size[0] - self.WIDTH - 10, 50, self.WIDTH, h)

    def _open(self, region):
        self._commit()
        self.zone = region
        self.pending = dict(self.sim.zone_facilities.get(region.name, {}))
        self.dirty = False
        self.typing = None
        self.status = f"{region.name}: set facility numbers, then Apply (or close the panel)."

    def _commit(self):
        """Store the open panel's numbers and recompute the OD matrix if anything changed."""
        if self.typing is not None:
            self._finish_typing()
        if self.zone is None or not self.dirty:
            return
        if self.pending:
            self.sim.zone_facilities[self.zone.name] = dict(self.pending)
        else:
            self.sim.zone_facilities.pop(self.zone.name, None)
        self.dirty = False
        self.status = f"{self.zone.name}: facilities set." + self._recompute_od()

    def _recompute_od(self) -> str:
        if not (config.get('OD_BUNDLE_DIR') and hasattr(self.sim, 'reschedule_od_demand')):
            return ""
        self._show("Recomputing the OD matrix for the facility changes...")
        self.sim.reschedule_od_demand()
        summary = self.sim.od_summary or {}
        here = summary.get('agents_by_dest_zone', {}).get(self.zone.name, 0) if self.zone else 0
        return f" OD recomputed: {summary.get('agents_scheduled', 0):,} agents, {here:,} ending here."

    def _finish_typing(self, keep:bool = True):
        if keep and self.buffer:
            self.set_count(self.typing, int(self.buffer))
        self.typing, self.buffer = None, ''

    def _show(self, text:str):
        window = pg.display.get_surface()
        if window is None:
            return
        h = window.get_height()
        pg.draw.rect(window, (30, 30, 30), pg.Rect(0, h - 48, window.get_width(), 48))
        window.blit(self.font.render(text, True, (255, 255, 255)), (10, h - 42))
        pg.display.update()

    # ------------------------------------------------------------------ input
    def handle_event(self, event, time:int) -> bool:
        if event.type == pg.KEYDOWN and event.key == pg.K_f and self.typing is None:
            if self.active:
                self._close_mode()
            else:
                self.active = True
                for other in self._others():                  # one edit mode at a time
                    other.active = False
                    if hasattr(other, '_clear_selection'):
                        other._clear_selection()
                self.status = (self.LOCKED_MSG if self.sim.started else
                               "FACILITY MODE — click a barangay to see and change its facilities.")
            return True
        if not self.active:
            return False
        if any(getattr(o, 'active', False) for o in self._others()):
            self._close_mode()
            return False

        if event.type == pg.MOUSEMOTION:
            self._mouse = event.pos
        window = pg.display.get_surface()
        panel = self._panel_rect(window.get_size()) if (window and self.zone is not None) else None
        locked = self.sim.started

        if event.type == pg.KEYDOWN:
            if self.typing is not None:
                if event.key in (pg.K_RETURN, pg.K_KP_ENTER):
                    self._finish_typing()
                elif event.key == pg.K_ESCAPE:
                    self._finish_typing(keep=False)
                elif event.key == pg.K_BACKSPACE:
                    self.buffer = self.buffer[:-1]
                elif event.unicode.isdigit() and len(self.buffer) < 5:
                    self.buffer += event.unicode
                return True
            if event.key == pg.K_ESCAPE:
                if self.zone is not None:
                    self._commit()
                    self.zone = None
                else:
                    self._close_mode()
                return True
            if event.key in (pg.K_RETURN, pg.K_KP_ENTER) and not locked:
                self._commit()
                return True
            if event.key == pg.K_s and not locked:
                from transport.route_editor import save_case, RouteChangeError
                self._commit()
                try:
                    self.status = f"Saved case: {save_case(self.sim)}"
                except RouteChangeError as e:
                    self.status = str(e)
                return True
            return False

        if event.type == pg.MOUSEWHEEL and panel is not None and panel.collidepoint(self._mouse):
            if not locked:
                for cat in self.categories():
                    r = self._rects.get(('row', cat))
                    if r and r.collidepoint(self._mouse):
                        step = 10 if pg.key.get_mods() & pg.KMOD_SHIFT else 1
                        self.set_count(cat, self.count(cat) + step * (1 if event.y > 0 else -1))
            return True                                        # no zooming under the panel

        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            if panel is not None and panel.collidepoint(event.pos):
                self._panel_click(event.pos, locked)
                return True                                    # no map dragging from the panel
            self._down = event.pos
            return False
        if event.type == pg.MOUSEBUTTONUP and event.button == 1 and self._down is not None:
            moved = math.hypot(event.pos[0] - self._down[0], event.pos[1] - self._down[1])
            self._down = None
            if moved <= CLICK_TOLERANCE_PX and not self._over_button(event.pos):
                region = self.sim.zone_editor.zone_at(event.pos) if hasattr(self.sim, 'zone_editor') else None
                if region is not None and region is not self.zone:
                    if not locked:
                        self._open(region)
                    else:
                        self.zone, self.pending = region, dict(self.sim.zone_facilities.get(region.name, {}))
            return False
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 3:
            return True
        return False

    def _panel_click(self, pos, locked:bool = False):
        if self.typing is not None:
            self._finish_typing()
        shift = pg.key.get_mods() & pg.KMOD_SHIFT
        for key, rect in self._rects.items():
            if not rect.collidepoint(pos) or key[0] == 'row':
                continue
            kind, cat = key
            if locked and kind != 'close':
                return
            if kind == 'check':
                if self.count(cat) > 0:
                    self.set_count(cat, 0)
                else:
                    self.set_count(cat, self._last.get(cat) or self.base(cat) or 1)
            elif kind == 'minus':
                self.set_count(cat, self.count(cat) - (10 if shift else 1))
            elif kind == 'plus':
                self.set_count(cat, self.count(cat) + (10 if shift else 1))
            elif kind == 'number':
                self.typing, self.buffer = cat, ''
                self.status = f"Type the number of {cat} facilities for {self.zone.name}, then Enter."
            elif kind == 'apply':
                self._commit()
            elif kind == 'reset':
                self.pending, self.dirty = {}, True
                self.status = f"{self.zone.name}: back to the data's numbers (Apply to recompute)."
            elif kind == 'close':
                self._commit()
                self.zone = None
            return

    def _close_mode(self):
        if not self.sim.started:
            self._commit()
        self.active, self.zone, self.typing = False, None, None

    def _clear_selection(self):
        self.zone, self.typing = None, None

    # ------------------------------------------------------------------ drawing
    def draw(self, window:pg.Surface):
        if not self.active:
            return
        if any(getattr(o, 'active', False) for o in self._others()):
            self._close_mode()
            return
        if self.sim.started:
            self.status = self.LOCKED_MSG
        elif self.status == self.LOCKED_MSG:
            self.status = "FACILITY MODE — click a barangay to see and change its facilities."
        cam = self.camera
        if self.zone is not None:
            pg.draw.polygon(window, (0, 150, 140), [cam.to_screen(p) for p in self.zone.polygon], 3)
            self._draw_panel(window)
        h = window.get_height()
        pg.draw.rect(window, (30, 30, 30), pg.Rect(0, h - 48, window.get_width(), 48))
        window.blit(self.font.render(self.status[:170], True, (255, 255, 255)), (10, h - 42))
        changed = ", ".join(f"{z.replace('Barangay ', '')}" for z in sorted(self.sim.zone_facilities)) or "none"
        help_text = ("F / Esc close" if self.sim.started else
                     f"F close | click a barangay: its facilities | Enter apply | S save case | changed: {changed}")
        window.blit(self.font.render(help_text[:170], True, (180, 180, 180)), (10, h - 22))

    def _button(self, window, key, rect, text, enabled=True, fill=(235, 235, 235)):
        self._rects[key] = rect
        pg.draw.rect(window, fill if enabled else (245, 245, 245), rect, border_radius=3)
        pg.draw.rect(window, (120, 120, 120), rect, 1, border_radius=3)
        label = self.small.render(text, True, (20, 20, 20) if enabled else (160, 160, 160))
        window.blit(label, label.get_rect(center=rect.center))

    def _draw_panel(self, window):
        self._rects = {}
        panel = self._panel_rect(window.get_size())
        locked = self.sim.started
        pg.draw.rect(window, (252, 252, 250), panel)
        pg.draw.rect(window, (60, 60, 60), panel, 1)
        x, y = panel.left + 10, panel.top + 8
        title = self.font.render(f"{self.zone.name} — facilities" + ("  (changed)" if self.zone.name in self.sim.zone_facilities or self.pending else ""), True, (0, 90, 85))
        window.blit(title, (x, y))
        y += self.ROW_H
        window.blit(self.small.render("open   category              now / data", True, (110, 110, 110)), (x, y))
        y += self.ROW_H - 4
        for cat in self.categories():
            n, b = self.count(cat), self.base(cat)
            row = pg.Rect(panel.left + 2, y - 2, panel.width - 4, self.ROW_H)
            self._rects[('row', cat)] = row
            if cat in self.pending:
                pg.draw.rect(window, (225, 245, 240), row)
            box = pg.Rect(x + 4, y + 3, 14, 14)
            self._rects[('check', cat)] = box
            pg.draw.rect(window, (255, 255, 255), box)
            pg.draw.rect(window, (80, 80, 80), box, 1)
            if n > 0:
                pg.draw.lines(window, (0, 130, 90), False, [(box.left + 3, box.centery), (box.left + 6, box.bottom - 3), (box.right - 3, box.top + 3)], 2)
            color = (20, 20, 20) if n > 0 else (170, 60, 60) if b > 0 else (150, 150, 150)   # gray: none in the data
            window.blit(self.small.render(cat + ("  (closed)" if n == 0 and b > 0 else ""), True, color), (x + 30, y + 3))
            number = pg.Rect(panel.left + 190, y, 70, self.ROW_H - 4)
            self._rects[('number', cat)] = number
            if self.typing == cat:
                pg.draw.rect(window, (255, 250, 200), number)
                pg.draw.rect(window, (200, 160, 0), number, 1)
                text = (self.buffer or '') + '_'
            else:
                text = f"{n} / {b}"
            window.blit(self.small.render(text, True, (20, 20, 20) if n == b else (0, 110, 100)), (number.left + 4, y + 3))
            self._button(window, ('minus', cat), pg.Rect(panel.right - 66, y, 26, self.ROW_H - 4), "-", not locked and n > 0)
            self._button(window, ('plus', cat), pg.Rect(panel.right - 36, y, 26, self.ROW_H - 4), "+", not locked)
            y += self.ROW_H
        y += 6
        summary = getattr(self.sim, 'od_summary', None) or {}
        if 'agents_by_dest_zone' in summary:
            window.blit(self.small.render(f"OD agents ending here: {summary['agents_by_dest_zone'].get(self.zone.name, 0):,}", True, (60, 60, 60)), (x, y))
        y += self.ROW_H
        self._button(window, ('apply', None), pg.Rect(x, y, 90, self.ROW_H - 2), "Apply", not locked and self.dirty, (200, 235, 225))
        self._button(window, ('reset', None), pg.Rect(x + 100, y, 100, self.ROW_H - 2), "Reset zone", not locked)
        self._button(window, ('close', None), pg.Rect(x + 210, y, 90, self.ROW_H - 2), "Close")
