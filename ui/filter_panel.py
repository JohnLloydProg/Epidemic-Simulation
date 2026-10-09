"""
filter_panel.py — the "Show on map" panel (L, or the Filters button): the view and the map filters.

    View ............. Graphics / Bare (also V, or the View button)
    Size ............. bigger / smaller roads, vehicles and people in the graphics view (also [ and ])
    People / vehicle types
                       tick to show or hide walking people, waiting people, private cars, tricycles, jeepneys,
                       buses and trains; each row shows how many there are right now (shown or not)
    Route lines ...... draw each visible route's path on the map (remembered separately for each view)
    Routes ........... every jeepney, bus and train route (both directions together), with the number of its
                       vehicles on the map now. Click a row to show/hide that route's vehicles and line;
                       Ctrl+click shows ONLY that route; hover a row to outline the route on the map.
                       "All" / "None" apply to the routes listed (so type a filter first to switch a group).
                       Click the search box and type to filter by route id or name (Esc or Enter to finish).
                       Mouse wheel scrolls the list.
    Show everything .. clears every filter

Drawing only (ui/view_filter.py, ui/renderer.py): hidden vehicles still run and are still counted in the metrics.
Clicks and the mouse wheel over the panel do not reach the map, and typed letters go to the search box while it
is active.
"""
from __future__ import annotations
import pygame as pg

from ui.button import draw_small_button
from ui.view_filter import MODES, ROUTE_COLORS, SIZE_STEPS, ROUTED_MODES, route_key, route_mode

PANEL_BG = (255, 255, 255, 236)
BORDER = (120, 120, 120)
TEXT = (20, 20, 20)
MUTED = (130, 130, 130)
ACCENT = (40, 110, 230)


class FilterPanel:
    WIDTH = 300
    TOP = 200                   # below the HUD text lines
    ROW_H = 20
    LIST_ROW_H = 18

    def __init__(self, sim):
        self.sim = sim
        self.visible = False
        self.query = ''
        self.typing = False
        self.scroll = 0
        self.font = pg.font.Font(None, 18)
        self.small = pg.font.Font(None, 15)
        self._groups:list[tuple] = []           # (key, mode, name, label) of each route, both directions as one
        self._groups_for = None
        self._hits:list[tuple[pg.Rect, tuple]] = []
        self._list_rect = pg.Rect(0, 0, 0, 0)
        self._rows_visible = 0
        self.rect = pg.Rect(10, self.TOP, self.WIDTH, 300)

    @property
    def filter(self):
        return self.sim.view_filter

    # ------------------------------------------------------------------ routes listed
    def _route_groups(self) -> list[tuple]:
        routes = self.sim.routes
        stamp = (id(routes), len(routes))
        if stamp != self._groups_for:
            seen = {}
            for route in routes:
                key = route_key(route)
                if key not in seen:
                    name = str(getattr(route, 'name', '') or key)
                    short = key.split('_')[-1] if '_' in key else key
                    seen[key] = (key, route_mode(route), name, f"{short}  {name}")
            order = {mode: i for i, mode in enumerate(ROUTED_MODES)}
            self._groups = sorted(seen.values(), key=lambda g: (order.get(g[1], 9), g[2].lower(), g[0]))
            self._groups_for = stamp
        return self._groups

    def _listed(self) -> list[tuple]:
        query = self.query.strip().lower()
        groups = self._route_groups()
        if not query:
            return groups
        return [g for g in groups if query in g[0].lower() or query in g[2].lower() or query in g[1]]

    # ------------------------------------------------------------------ input
    def toggle(self):
        self.visible = not self.visible
        self.typing = False

    def _blocked(self, pos) -> bool:
        """Another popup is open over the panel (the route editor's route picker): let it have the click."""
        picker = getattr(getattr(self.sim, 'editor', None), 'picker', None)
        return picker is not None and picker.rect.collidepoint(pos)

    def handle_event(self, event) -> bool:
        """Returns True when the panel used the event (the simulation should ignore it)."""
        if not self.visible:
            return False
        if event.type == pg.KEYDOWN and self.typing:
            if event.key in (pg.K_ESCAPE, pg.K_RETURN, pg.K_KP_ENTER):
                self.typing = False
            elif event.key == pg.K_BACKSPACE:
                self.query = self.query[:-1]
                self.scroll = 0
            elif getattr(event, 'unicode', '') and event.unicode.isprintable() and not event.mod & (pg.KMOD_CTRL | pg.KMOD_META):
                self.query += event.unicode
                self.scroll = 0
            return True
        if event.type == pg.MOUSEWHEEL:
            pos = pg.mouse.get_pos()
            if self.rect.collidepoint(pos) and not self._blocked(pos):
                if self._list_rect.collidepoint(pos):
                    self._scroll_by(-event.y * 3)
                return True
            return False
        if event.type == pg.MOUSEBUTTONDOWN:
            if not self.rect.collidepoint(event.pos) or self._blocked(event.pos):
                self.typing = False
                return False
            if event.button == 1:
                ctrl = bool(pg.key.get_mods() & (pg.KMOD_CTRL | pg.KMOD_META))
                for rect, action in self._hits:
                    if rect.collidepoint(event.pos):
                        self._do(action, ctrl)
                        break
                else:
                    self.typing = False
            return True                          # wheel-as-button, right click etc. stay off the map
        return False

    def _scroll_by(self, rows:int):
        limit = max(0, len(self._listed()) - self._rows_visible)
        self.scroll = max(0, min(limit, self.scroll + rows))

    def _do(self, action:tuple, ctrl:bool):
        vf = self.filter
        kind = action[0]
        self.typing = kind == 'search'
        if kind == 'view':
            vf.graphics = action[1] == 'graphics'
        elif kind == 'size':
            vf.change_size(action[1])
        elif kind == 'mode':
            vf.toggle_mode(action[1])
        elif kind == 'lines':
            vf.route_lines = not vf.route_lines
        elif kind == 'all':
            vf.hidden_routes.difference_update(g[0] for g in self._listed())
        elif kind == 'none':
            vf.hidden_routes.update(g[0] for g in self._listed())
        elif kind == 'reset':
            vf.show_all()
            self.query = ''
            self.scroll = 0
        elif kind == 'route':
            key, mode = action[1], action[2]
            if ctrl:                                 # only this route
                vf.hidden_routes = {g[0] for g in self._route_groups() if g[0] != key}
                vf.modes[mode] = True
            elif key in vf.hidden_routes:
                vf.hidden_routes.discard(key)
            else:
                vf.hidden_routes.add(key)
        elif kind == 'clear':
            self.query = ''
            self.scroll = 0
            self.typing = True

    # ------------------------------------------------------------------ drawing
    def draw(self, window:pg.Surface):
        renderer = getattr(self.sim, 'renderer', None)
        if renderer is not None:
            renderer.highlight_route = None
        if not self.visible:
            if self.filter.is_filtered():         # reminder that the map is filtered
                text = self.small.render("Map filtered - press L to see the filters", True, (170, 60, 0))
                window.blit(text, (12, self.TOP))
            return
        vf = self.filter
        counts = getattr(renderer, 'counts', {}) or {}
        route_counts = getattr(renderer, 'route_counts', {}) or {}
        sprites = getattr(renderer, 'sprites', None)
        bottom = window.get_height() - 58
        self.rect = pg.Rect(10, self.TOP, self.WIDTH, max(260, bottom - self.TOP))
        mouse = pg.mouse.get_pos()
        hits = []

        panel = pg.Surface(self.rect.size, pg.SRCALPHA)
        panel.fill(PANEL_BG)
        pg.draw.rect(panel, BORDER, panel.get_rect(), 1)
        window.blit(panel, self.rect.topleft)
        x, y = self.rect.left + 8, self.rect.top + 6
        right = self.rect.right - 8

        window.blit(self.font.render("Show on map", True, TEXT), (x, y))
        hint = self.small.render("L hide", True, MUTED)
        window.blit(hint, hint.get_rect(topright=(right, y + 2)))
        y += 22

        # view switch
        window.blit(self.small.render("View (V)", True, TEXT), (x, y + 4))
        for i, (name, label) in enumerate((('graphics', 'Graphics'), ('bare', 'Bare'))):
            rect = pg.Rect(x + 70 + i * 82, y, 80, 20)
            active = vf.view == name
            draw_small_button(window, rect, label, 'accent' if active else 'neutral')
            hits.append((rect, ('view', name)))
        y += 26

        # size of roads, vehicles and people (graphics view)
        size_color = TEXT if vf.graphics else MUTED
        window.blit(self.small.render("Size  [ ]", True, size_color), (x, y + 4))
        for i, (label, step) in enumerate((('-', -1), ('+', 1))):
            rect = pg.Rect(x + 70 + i * 126, y, 34, 20)
            limit = SIZE_STEPS[0] if step < 0 else SIZE_STEPS[-1]
            draw_small_button(window, rect, icon='minus' if step < 0 else 'plus',
                              enabled=vf.graphics and vf.size != limit)
            hits.append((rect, ('size', step)))
        value = self.small.render(f"{vf.size:g}x" + ("" if vf.graphics else " (graphics)"), True, size_color)
        window.blit(value, value.get_rect(center=(x + 70 + 34 + 46, y + 10)))
        y += 28

        # modes
        for key, label, color, sprite_name in MODES:
            row = pg.Rect(self.rect.left + 2, y, self.WIDTH - 4, self.ROW_H)
            if row.collidepoint(mouse):
                pg.draw.rect(window, (245, 240, 220), row)
            on = vf.mode_on(key)
            self._checkbox(window, x, y + 3, on)
            icon = pg.Rect(x + 22, y + 2, 16, 16)
            self._mode_icon(window, sprites, key, color, sprite_name, icon)
            window.blit(self.small.render(label, True, TEXT if on else MUTED), (x + 44, y + 4))
            count = self.small.render(f"{counts.get(key, 0):,}", True, TEXT if on else MUTED)
            window.blit(count, count.get_rect(topright=(right, y + 4)))
            hits.append((row, ('mode', key)))
            y += self.ROW_H
        row = pg.Rect(self.rect.left + 2, y, self.WIDTH - 4, self.ROW_H)
        if row.collidepoint(mouse):
            pg.draw.rect(window, (245, 240, 220), row)
        self._checkbox(window, x, y + 3, vf.route_lines)
        window.blit(self.small.render(f"Route lines ({'graphics' if vf.graphics else 'bare'} view)", True, TEXT),
                    (x + 22, y + 4))
        hits.append((row, ('lines',)))
        y += self.ROW_H + 4
        pg.draw.line(window, (210, 210, 210), (x, y), (right, y))
        y += 6

        # routes
        listed = self._listed()
        groups = self._route_groups()
        shown = sum(1 for g in groups if vf.route_on(g[0], g[1]))
        window.blit(self.font.render(f"Routes  {shown}/{len(groups)} shown", True, TEXT), (x, y))
        for i, (label, action) in enumerate((('All', 'all'), ('None', 'none'))):
            rect = pg.Rect(right - 92 + i * 48, y - 2, 44, 18)
            draw_small_button(window, rect, label)
            hits.append((rect, (action,)))
        y += 22
        box = pg.Rect(x, y, right - x, 20)
        pg.draw.rect(window, (255, 255, 255), box)
        pg.draw.rect(window, ACCENT if self.typing else (150, 150, 150), box, 2 if self.typing else 1)
        if self.query or self.typing:
            caret = '|' if self.typing and (pg.time.get_ticks() // 500) % 2 == 0 else ''
            text = self.small.render(self.query + caret, True, TEXT)
        else:
            text = self.small.render("search route id or name...", True, MUTED)
        window.blit(text, (box.left + 5, box.top + 4))
        hits.append((box, ('search',)))
        if self.query:
            clear = pg.Rect(box.right - 18, box.top + 2, 16, 16)
            window.blit(self.small.render("x", True, MUTED), (clear.left + 4, clear.top + 1))
            hits.insert(0, (clear, ('clear',)))
        y += 24

        footer_h = 26
        list_rect = pg.Rect(self.rect.left + 2, y, self.WIDTH - 4, self.rect.bottom - footer_h - y)
        self._list_rect = list_rect
        self._rows_visible = max(1, list_rect.height // self.LIST_ROW_H)
        self.scroll = max(0, min(self.scroll, max(0, len(listed) - self._rows_visible)))
        if not listed:
            window.blit(self.small.render("No route matches.", True, MUTED), (x, y + 4))
        for i in range(min(self._rows_visible, len(listed) - self.scroll)):
            key, mode, name, label = listed[self.scroll + i]
            row = pg.Rect(list_rect.left, list_rect.top + i * self.LIST_ROW_H, list_rect.width - 6, self.LIST_ROW_H)
            hovered = row.collidepoint(mouse) and not self._blocked(mouse)
            if hovered:
                pg.draw.rect(window, (255, 232, 160), row)
                if renderer is not None:
                    renderer.highlight_route = key
            mode_on = vf.mode_on(mode)
            on = key not in vf.hidden_routes
            self._checkbox(window, x, row.top + 2, on, dim=not mode_on)
            pg.draw.rect(window, ROUTE_COLORS[mode], pg.Rect(x + 20, row.top + 5, 8, 8))
            color = TEXT if (on and mode_on) else MUTED
            count = route_counts.get(key, 0)
            count_text = self.small.render(str(count) if count else '', True, color)
            room = row.right - (x + 34) - count_text.get_width() - 8
            window.blit(self.small.render(self._fit(label, room), True, color), (x + 34, row.top + 3))
            window.blit(count_text, count_text.get_rect(topright=(row.right - 2, row.top + 3)))
            hits.append((row, ('route', key, mode)))
        if len(listed) > self._rows_visible:          # scroll bar
            track = pg.Rect(list_rect.right - 4, list_rect.top, 3, self._rows_visible * self.LIST_ROW_H)
            size = max(12, track.height * self._rows_visible // len(listed))
            top = track.top + (track.height - size) * self.scroll // max(1, len(listed) - self._rows_visible)
            pg.draw.rect(window, (170, 170, 170), pg.Rect(track.left, top, 3, size))

        foot_y = self.rect.bottom - footer_h + 4
        pg.draw.line(window, (210, 210, 210), (x, foot_y - 3), (right, foot_y - 3))
        reset = pg.Rect(x, foot_y, 120, 18)
        draw_small_button(window, reset, "Show everything", enabled=vf.is_filtered())
        hits.append((reset, ('reset',)))
        tip = self.small.render("Ctrl+click: only this route", True, MUTED)
        window.blit(tip, tip.get_rect(topright=(right, foot_y + 3)))
        self._hits = hits

    def _fit(self, text:str, width:int) -> str:
        if self.small.size(text)[0] <= width:
            return text
        while text and self.small.size(text + '...')[0] > width:
            text = text[:-1]
        return text + '...'

    @staticmethod
    def _checkbox(window, x, y, on:bool, dim:bool = False):
        box = pg.Rect(x, y, 14, 14)
        pg.draw.rect(window, (255, 255, 255), box)
        pg.draw.rect(window, (160, 160, 160) if dim else (70, 70, 70), box, 1)
        if on:
            color = (170, 170, 170) if dim else ACCENT
            pg.draw.lines(window, color, False, [(x + 3, y + 7), (x + 6, y + 10), (x + 11, y + 3)], 2)

    def _mode_icon(self, window, sprites, key, color, sprite_name, rect:pg.Rect):
        if self.filter.graphics and sprites is not None:
            sprite = sprites.get(sprite_name, 16 if sprite_name != 'train' else 18, 90 if sprite_name != 'train' else 0)
            if sprite is not None:
                window.blit(sprite, sprite.get_rect(center=rect.center))
                return
        if key == 'waiting':                           # bare view: node coloured by its load
            pg.draw.circle(window, (200, 120, 0), rect.center, 6)
            pg.draw.circle(window, (0, 0, 0), rect.center, 6, 1)
        else:
            pg.draw.circle(window, color, rect.center, 5)
            if color in ((255, 255, 0), (200, 200, 200)):    # light dots need an outline on white
                pg.draw.circle(window, (120, 120, 120), rect.center, 5, 1)
