"""
Case manager — open and save test cases from inside the simulation window.

Keys:
    O ................ open the case list (also the "Cases" button)
    Ctrl+S ........... save the current setup as a case (S in the route / hotspot / road / facility modes too)

In the case list:
    Up/Down, click ... choose a case               Enter, double-click ... open it
    Delete ........... delete it (asks first)      Ctrl+S ........ save as...
    Esc / click outside ... close
In "Save case as":
    type a name, Enter saves (asks before replacing an existing case), Esc cancels

Opening a case reloads the network, routes, hotspots and OD agents of that case and starts at the beginning.
Its routing cache is read from sim_data/cache/ when one exists (the "cache ready" tag), otherwise it is computed
once and stored there. Saving also stores the routing cache in memory for the saved case, so re-opening it is
quick. File work is in case_files.py.
"""
from __future__ import annotations
import logging
import time

import pygame as pg

import case_files

LOGGER = logging.getLogger('CaseManager')

PANEL_W = 780
ROW_H = 40
MAX_ROWS = 10
TOAST_S = 6
NAME_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-. ')

BG = (34, 36, 40)
ROW_SEL = (60, 90, 140)
ROW_HOVER = (50, 54, 62)
TEXT = (235, 235, 235)
MUTED = (160, 165, 175)
GOOD = (120, 210, 140)
WARN = (240, 190, 90)
BAD = (240, 110, 100)


class CaseManager:
    def __init__(self, sim):
        self.sim = sim
        self.mode = None                # None | 'browse' | 'save'
        self.items: list[case_files.CaseInfo] = []
        self.sel = 0
        self.scroll = 0
        self.hover = None
        self.confirm = None             # ('open' | 'delete' | 'overwrite', value)
        self.message, self.message_color = '', MUTED
        self.text = ''
        self.fresh = False              # the suggested name is replaced by the first key typed
        self.from_browser = False
        self.dirty = False
        self._last_click = (None, 0.0)
        self._rects: dict = {}
        self.toast, self.toast_color, self.toast_until = '', GOOD, 0.0
        self.font = pg.font.Font(None, 24)
        self.normal = pg.font.Font(None, 20)
        self.small = pg.font.Font(None, 17)

    # ------------------------------------------------------------------ state
    @property
    def active(self) -> bool:
        return self.mode is not None

    def _typing_elsewhere(self) -> bool:
        """Another panel is reading letters (route filter, facility number): leave O / S to it."""
        editor = getattr(self.sim, 'editor', None)
        facility = getattr(self.sim, 'facility_editor', None)
        return (getattr(editor, 'picker', None) is not None) or (getattr(facility, 'typing', None) is not None)

    def _check_dirty(self) -> bool:
        try:
            self.dirty = case_files.session_fingerprint(self.sim) != getattr(self.sim, 'case_fingerprint', None)
        except Exception:
            LOGGER.exception("Could not compare the session with the open case")
            self.dirty = True
        return self.dirty

    def _say(self, text:str, color=MUTED):
        self.message, self.message_color = text, color

    def notify(self, text:str, color=GOOD):
        """Short message at the bottom of the window (after the panel closes)."""
        self.toast, self.toast_color, self.toast_until = text, color, time.time() + TOAST_S

    def open_browser(self):
        self.mode = 'browse'
        self.confirm = None
        self._check_dirty()
        self._refresh()
        current = next((i for i, c in enumerate(self.items) if c.is_open), 0)
        self.sel = current
        self._scroll_to_sel()
        self._say("Enter: open   Del: delete   Ctrl+S: save as   Esc: close"
                  + ("   — this session has unsaved changes" if self.dirty else ""), WARN if self.dirty else MUTED)

    def open_save(self, from_browser:bool = False):
        from transport.route_editor import next_case_name
        from graphing.data_loader import load_case
        self.mode = 'save'
        self.confirm = None
        self.from_browser = from_browser
        self._check_dirty()
        current = load_case()['case_id']
        self.text = next_case_name(current) if self.dirty else current
        self.fresh = True
        self._say("Type a name and press Enter. Esc cancels."
                  + ("" if self.dirty else "  (No changes since the case was opened or saved.)"))

    def close(self):
        self.mode = None
        self.confirm = None

    def _refresh(self):
        name = self.items[self.sel].file_name if self.items and 0 <= self.sel < len(self.items) else None
        self.items = case_files.list_cases()
        self.sel = next((i for i, c in enumerate(self.items) if c.file_name == name), min(self.sel, max(0, len(self.items) - 1)))

    def _scroll_to_sel(self):
        if self.sel < self.scroll:
            self.scroll = self.sel
        elif self.sel >= self.scroll + MAX_ROWS:
            self.scroll = self.sel - MAX_ROWS + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.items) - MAX_ROWS)))

    # ------------------------------------------------------------------ actions
    def _busy(self, text:str):
        """Draw a message right away (the main loop is blocked while a case loads or saves)."""
        window = pg.display.get_surface()
        if window is None:
            return
        w, h = window.get_size()
        box = pg.Rect(0, 0, min(w - 40, 620), 64)
        box.center = (w // 2, h // 2)
        pg.draw.rect(window, BG, box, border_radius=8)
        pg.draw.rect(window, ROW_SEL, box, 2, border_radius=8)
        label = self.font.render(text, True, TEXT)
        window.blit(label, label.get_rect(center=box.center))
        pg.display.update()

    def _progress(self, done:int, total:int):
        pg.event.pump()
        if done == total or done % 10 == 0:
            self._busy(f"Computing routing cache: {done}/{total} origins...")

    def _request_open(self):
        if not self.items:
            return
        case = self.items[self.sel]
        if case.is_open and not self.dirty:
            self._say(f"{case.case_id} is already open.", MUTED)
            return
        if self.dirty:
            verb = "reload" if case.is_open else "open"
            self.confirm = ('open', case.file_name)
            self._say(f"Discard unsaved changes and {verb} {case.case_id}?  Enter = yes, Esc = no", WARN)
            return
        self._open(case.file_name)

    def _open(self, file_name:str):
        case = next((c for c in self.items if c.file_name == file_name), None)
        label = case.case_id if case else file_name
        self._busy(f"Opening {label}..." + ("" if case and case.has_cache else " (computing its routing cache)"))
        try:
            self.sim.open_case(file_name, self._progress)
        except Exception as e:
            LOGGER.exception(f"Could not open {file_name}")
            self.confirm = None
            self._refresh()
            self._say(f"Could not open {label}: {e}", BAD)
            return
        self.close()
        summary = getattr(self.sim, 'od_summary', None)
        extra = f", {summary['agents_scheduled']:,} OD agents" if summary else ''
        self.notify(f"Opened case {label} ({len(self.sim.routing_table):,} cached trips{extra}).")

    def _request_delete(self):
        if not self.items:
            return
        case = self.items[self.sel]
        if case.is_open:
            self._say("That case is open. Open another case before deleting it.", BAD)
            return
        self.confirm = ('delete', case.file_name)
        self._say(f"Delete {case.case_id} and its routing cache?  Enter = yes, Esc = no", BAD)

    def _delete(self, file_name:str):
        try:
            removed = case_files.delete(file_name)
        except Exception as e:
            self._say(str(e), BAD)
            return
        self.confirm = None
        self._refresh()
        self._scroll_to_sel()
        self._say(f"Deleted {file_name}" + (f" and {removed} cache file(s)." if removed else "."), GOOD)

    def _request_save(self):
        try:
            name = case_files.clean_name(self.text)
        except ValueError as e:
            self._say(str(e), BAD)
            return
        if case_files.case_exists(name) and self.confirm != ('overwrite', name):
            self.confirm = ('overwrite', name)
            from graphing.data_loader import load_case
            what = "the open case" if name == load_case()['case_id'] else "the existing case"
            self._say(f"Replace {what} '{name}'?  Enter = yes, Esc = no", WARN)
            return
        self._save(name, overwrite=self.confirm == ('overwrite', name))

    def _save(self, name:str, overwrite:bool):
        self._busy(f"Saving {name}...")
        try:
            path, note = case_files.save(self.sim, name, overwrite, self._progress)
        except Exception as e:
            LOGGER.exception("Could not save the case")
            self.confirm = None
            self._say(f"Not saved: {e}", BAD)
            return
        self.close()
        self.notify(f"Saved {path} — {note}.", GOOD if 'NOT' not in note and 'not' not in note else WARN)

    # ------------------------------------------------------------------ input
    def handle_event(self, event) -> bool:
        if self.mode is None:
            if event.type == pg.KEYDOWN and not self._typing_elsewhere():
                ctrl = event.mod & (pg.KMOD_CTRL | pg.KMOD_META)
                if event.key == pg.K_s and ctrl:
                    self.open_save()
                    return True
                if event.key == pg.K_o and not ctrl:
                    self.open_browser()
                    return True
            return False
        if self.mode == 'browse':
            self._browse_event(event)
        else:
            self._save_event(event)
        return True                         # the panel is modal: nothing else gets the event

    def _confirm_key(self, event) -> bool:
        """Enter/Y accepts, Esc/N cancels a pending question. True if the key was used."""
        if self.confirm is None or event.type != pg.KEYDOWN:
            return False
        if event.key in (pg.K_RETURN, pg.K_KP_ENTER, pg.K_y):
            kind, value = self.confirm
            if kind == 'open':
                self._open(value)
            elif kind == 'delete':
                self._delete(value)
            elif kind == 'overwrite':
                self._save(value, overwrite=True)
            return True
        if event.key in (pg.K_ESCAPE, pg.K_n):
            self.confirm = None
            self._say("Cancelled.", MUTED)
            return True
        return event.key not in (pg.K_UP, pg.K_DOWN)       # other keys wait for the answer

    def _browse_event(self, event):
        if self._confirm_key(event):
            return
        if event.type == pg.KEYDOWN:
            ctrl = event.mod & (pg.KMOD_CTRL | pg.KMOD_META)
            if event.key in (pg.K_ESCAPE, pg.K_o):
                self.close()
            elif event.key == pg.K_s and ctrl:
                self.open_save(from_browser=True)
            elif event.key in (pg.K_DOWN, pg.K_UP) and self.items:
                self.sel = (self.sel + (1 if event.key == pg.K_DOWN else -1)) % len(self.items)
                self.confirm = None
                self._scroll_to_sel()
            elif event.key in (pg.K_PAGEDOWN, pg.K_PAGEUP) and self.items:
                step = MAX_ROWS if event.key == pg.K_PAGEDOWN else -MAX_ROWS
                self.sel = max(0, min(len(self.items) - 1, self.sel + step))
                self._scroll_to_sel()
            elif event.key in (pg.K_RETURN, pg.K_KP_ENTER):
                self._request_open()
            elif event.key in (pg.K_DELETE, pg.K_BACKSPACE):
                self._request_delete()
        elif event.type == pg.MOUSEMOTION:
            self.hover = self._row_at(event.pos)
        elif event.type == pg.MOUSEWHEEL:
            self.scroll = max(0, min(self.scroll - event.y, max(0, len(self.items) - MAX_ROWS)))
        elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            panel = self._rects.get('panel')
            if panel is not None and not panel.collidepoint(event.pos):
                self.close()
                return
            if self._rects.get('save') and self._rects['save'].collidepoint(event.pos):
                self.open_save(from_browser=True)
                return
            row = self._row_at(event.pos)
            if row is None:
                return
            last_row, last_t = self._last_click
            self.sel = row
            if self.confirm is not None and self.confirm[1] != self.items[row].file_name:
                self.confirm = None
            if last_row == row and time.time() - last_t < 0.4:
                self._request_open()
                self._last_click = (None, 0.0)
            else:
                self._last_click = (row, time.time())

    def _save_event(self, event):
        if self.confirm is not None and event.type == pg.KEYDOWN:
            if event.key in (pg.K_RETURN, pg.K_KP_ENTER, pg.K_ESCAPE):
                self._confirm_key(event)
                return
            self.confirm = None                 # typing again: change the name instead
            self._say("Type a name and press Enter. Esc cancels.")
        if event.type != pg.KEYDOWN:
            if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                panel = self._rects.get('panel')
                if panel is not None and not panel.collidepoint(event.pos):
                    self._leave_save()
            return
        if event.key == pg.K_ESCAPE:
            self._leave_save()
        elif event.key in (pg.K_RETURN, pg.K_KP_ENTER):
            self._request_save()
        elif event.key == pg.K_BACKSPACE:
            self.text = '' if self.fresh else self.text[:-1]
            self.fresh = False
        elif event.unicode and event.unicode in NAME_CHARS:
            self.text = (event.unicode if self.fresh else self.text + event.unicode)[:60]
            self.fresh = False
            if self.message_color == BAD:
                self._say("Type a name and press Enter. Esc cancels.")

    def _leave_save(self):
        if self.from_browser:
            self.open_browser()
        else:
            self.close()

    def _row_at(self, pos):
        for i, rect in self._rects.get('rows', []):
            if rect.collidepoint(pos):
                return i
        return None

    # ------------------------------------------------------------------ drawing
    def _fit(self, text:str, font, width:int) -> str:
        if font.size(text)[0] <= width:
            return text
        while text and font.size(text + '…')[0] > width:
            text = text[:-1]
        return text + '…'

    def draw_case_label(self, window:pg.Surface):
        """Name of the open case (top right, under the clock) and any short message."""
        from graphing.data_loader import load_case
        w, h = window.get_size()
        label = self.small.render(f"Case: {load_case()['case_id']}   (O: cases, Ctrl+S: save)", True, (40, 40, 120))
        window.blit(label, label.get_rect(topright=(w - 20, 38)))
        if self.toast and time.time() < self.toast_until and self.mode is None:
            text = self.normal.render(self._fit(self.toast, self.normal, w - 60), True, self.toast_color)
            box = text.get_rect(midbottom=(w // 2, h - 60)).inflate(24, 14)
            pg.draw.rect(window, BG, box, border_radius=6)
            window.blit(text, text.get_rect(center=box.center))

    def draw(self, window:pg.Surface):
        self.draw_case_label(window)
        if self.mode is None:
            self._rects = {}
            return
        shade = pg.Surface(window.get_size(), pg.SRCALPHA)
        shade.fill((0, 0, 0, 110))
        window.blit(shade, (0, 0))
        if self.mode == 'browse':
            self._draw_browser(window)
        else:
            self._draw_save(window)

    def _draw_browser(self, window:pg.Surface):
        w, h = window.get_size()
        width = min(PANEL_W, w - 32)
        rows = min(MAX_ROWS, max(1, len(self.items)))
        height = 56 + rows * ROW_H + 44
        panel = pg.Rect((w - width) // 2, max(16, (h - height) // 2), width, height)
        self._rects = {'panel': panel, 'rows': []}
        pg.draw.rect(window, BG, panel, border_radius=8)
        window.blit(self.font.render(f"Test cases ({len(self.items)})", True, TEXT), (panel.x + 16, panel.y + 14))
        save = self.normal.render("Save as…  (Ctrl+S)", True, TEXT)
        save_rect = save.get_rect(topright=(panel.right - 16, panel.y + 16)).inflate(16, 8)
        pg.draw.rect(window, ROW_HOVER, save_rect, border_radius=4)
        window.blit(save, save.get_rect(center=save_rect.center))
        self._rects['save'] = save_rect

        top = panel.y + 48
        if not self.items:
            window.blit(self.normal.render("No case files in sim_data/cases/ yet — save one with Ctrl+S.", True, MUTED),
                        (panel.x + 16, top + 12))
        for slot, i in enumerate(range(self.scroll, min(len(self.items), self.scroll + MAX_ROWS))):
            case = self.items[i]
            row = pg.Rect(panel.x + 8, top + slot * ROW_H, width - 16, ROW_H - 2)
            self._rects['rows'].append((i, row))
            if i == self.sel:
                pg.draw.rect(window, ROW_SEL, row, border_radius=4)
            elif i == self.hover:
                pg.draw.rect(window, ROW_HOVER, row, border_radius=4)
            tag, color = ("cache ready", GOOD) if case.has_cache else ("no cache yet", WARN)
            right = f"{case.modified:%b %d %H:%M}"
            tag_s = self.small.render(tag, True, color)
            time_s = self.small.render(right, True, MUTED)
            window.blit(time_s, time_s.get_rect(topright=(row.right - 10, row.y + 5)))
            window.blit(tag_s, tag_s.get_rect(topright=(row.right - 10, row.y + 21)))
            name = case.case_id + ("   (open" + (", unsaved changes)" if self.dirty else ")") if case.is_open else "")
            text_w = row.width - 20 - max(tag_s.get_width(), time_s.get_width()) - 16
            window.blit(self.normal.render(self._fit(name, self.normal, text_w), True, TEXT), (row.x + 10, row.y + 4))
            desc = case.description or case.file_name
            window.blit(self.small.render(self._fit(desc, self.small, text_w), True, MUTED), (row.x + 10, row.y + 22))
        if len(self.items) > MAX_ROWS:             # scroll bar
            track = pg.Rect(panel.right - 6, top, 3, MAX_ROWS * ROW_H)
            size = max(12, track.height * MAX_ROWS // len(self.items))
            pos = track.y + (track.height - size) * self.scroll // max(1, len(self.items) - MAX_ROWS)
            pg.draw.rect(window, MUTED, pg.Rect(track.x, pos, 3, size))
        footer = self.small.render(self._fit(self.message, self.small, width - 32), True, self.message_color)
        window.blit(footer, (panel.x + 16, panel.bottom - 28))

    def _draw_save(self, window:pg.Surface):
        w, h = window.get_size()
        width = min(560, w - 32)
        panel = pg.Rect((w - width) // 2, (h - 150) // 2, width, 150)
        self._rects = {'panel': panel}
        pg.draw.rect(window, BG, panel, border_radius=8)
        window.blit(self.font.render("Save case as", True, TEXT), (panel.x + 16, panel.y + 14))
        box = pg.Rect(panel.x + 16, panel.y + 48, width - 32, 34)
        pg.draw.rect(window, (250, 250, 250), box, border_radius=4)
        shown = self._fit(self.text, self.font, box.width - 70)
        if self.fresh and shown:                    # suggested name: shown selected
            sel = self.font.render(shown, True, (255, 255, 255))
            pg.draw.rect(window, ROW_SEL, sel.get_rect(topleft=(box.x + 8, box.y + 8)).inflate(4, 4))
            window.blit(sel, (box.x + 8, box.y + 8))
        else:
            text = self.font.render(shown + ('|' if int(time.time() * 2) % 2 else ' '), True, (20, 20, 20))
            window.blit(text, (box.x + 8, box.y + 8))
        ext = self.small.render(".json", True, (120, 120, 120))
        window.blit(ext, ext.get_rect(midright=(box.right - 8, box.centery)))
        window.blit(self.small.render(self._fit(self.message, self.small, width - 32), True, self.message_color),
                    (panel.x + 16, panel.y + 94))
        window.blit(self.small.render("Saved with its routing cache, so opening it later is quick.", True, MUTED),
                    (panel.x + 16, panel.y + 118))
