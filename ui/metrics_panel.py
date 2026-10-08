"""
metrics_panel.py — line graph of zone occupancy over time (metrics.py), drawn over the map.

G ............ show / hide the graph
M ............ write summary.json, zones.csv and occupancy.png of the run so far into its log folder
               (the run keeps logging; see metrics.py for every file written)

One line per hotspot zone (or, with no hotspots, the busiest zones so far), the occupancy threshold as a dashed
line (METRICS_OCCUPANCY_THRESHOLD), and a legend with each zone's people now, peak and minutes above the threshold.
"""
import pygame as pg
from metrics import clock, PLOT_COLORS

COLORS = PLOT_COLORS


def nice_step(raw:float) -> float:
    """1, 2 or 5 times a power of ten, at least raw."""
    power = 10 ** max(0, len(str(int(max(raw, 1)))) - 1)
    for m in (1, 2, 5, 10):
        if m * power >= raw:
            return m * power
    return 10 * power


class MetricsPanel:
    WIDTH, HEIGHT = 470, 280

    def __init__(self, sim):
        self.sim = sim
        self.visible = True
        self.font = pg.font.Font(None, 16)
        self.small = pg.font.Font(None, 14)
        self.message = ''
        self.message_until = 0

    def handle_event(self, event, time:int) -> bool:
        if event.type != pg.KEYDOWN or event.mod & (pg.KMOD_CTRL | pg.KMOD_META):
            return False
        if event.key == pg.K_g:
            self.visible = not self.visible
            return True
        if event.key == pg.K_m:
            m = self.sim.metrics
            if m.finished:
                self.flash(f"This run is complete; its logs are in {m.folder}. Press Reset for a new run.")
            else:
                folder = m.save(m.last_time if m.last_time is not None else time)
                self.flash(f"Summary saved to {folder} (logging continues)" if folder
                           else "Nothing recorded yet - press Play first.")
            return True
        return False

    def flash(self, text:str, ms:int = 5000):
        self.message = text
        self.message_until = pg.time.get_ticks() + ms

    def draw(self, window:pg.Surface, time:int):
        if self.message and pg.time.get_ticks() < self.message_until:
            surf = self.font.render(self.message, True, (0, 90, 0))
            window.blit(surf, surf.get_rect(bottomleft=(20, window.get_height() - 10)))
        if not self.visible:
            return
        m = self.sim.metrics
        x0 = window.get_width() - self.WIDTH - 10
        y0 = window.get_height() - self.HEIGHT - 10
        panel = pg.Surface((self.WIDTH, self.HEIGHT), pg.SRCALPHA)
        panel.fill((255, 255, 255, 230))
        pg.draw.rect(panel, (120, 120, 120), panel.get_rect(), 1)

        title = 'Hotspot occupancy' if m.ready and m.hotspots else 'Busiest zones (no hotspots set)'
        panel.blit(self.font.render(f"{title}   [G hide, M save]", True, (0, 0, 0)), (8, 6))
        if not m.ready or len(m.samples_t) < 2:
            panel.blit(self.small.render("The graph starts when the simulation runs.", True, (90, 90, 90)), (8, 30))
            window.blit(panel, (x0, y0))
            return

        zones = m.plot_zones()
        legend_h = 14 * len(zones) + 4
        left, top, right = 40, 26, self.WIDTH - 10
        bottom = self.HEIGHT - 22 - legend_h
        t0, t1 = m.samples_t[0], max(m.samples_t[-1], m.samples_t[0] + 60)
        top_value = max([max(m.samples[z]) for z in zones] + [m.threshold or 0, 5])
        step = nice_step(top_value / 4)
        ymax = step * (int(top_value // step) + 1)

        def px(t, v):
            return (left + (right - left) * (t - t0) / (t1 - t0), bottom - (bottom - top) * v / ymax)

        # axes, grid and labels
        for i in range(int(ymax // step) + 1):
            v = step * i
            y = px(t0, v)[1]
            pg.draw.line(panel, (225, 225, 225), (left, y), (right, y))
            label = self.small.render(str(int(v)), True, (80, 80, 80))
            panel.blit(label, label.get_rect(midright=(left - 4, y)))
        for i in range(5):
            t = t0 + (t1 - t0) * i / 4
            x = px(t, 0)[0]
            label = self.small.render(clock(t), True, (80, 80, 80))
            rect = label.get_rect(midtop=(x, bottom + 3))
            rect.right = min(rect.right, self.WIDTH - 2)
            panel.blit(label, rect)
        pg.draw.line(panel, (0, 0, 0), (left, top), (left, bottom))
        pg.draw.line(panel, (0, 0, 0), (left, bottom), (right, bottom))

        # threshold (dashed)
        if m.threshold is not None and m.threshold <= ymax:
            y = px(t0, m.threshold)[1]
            x = left
            while x < right:
                pg.draw.line(panel, (0, 0, 0), (x, y), (min(x + 6, right), y), 1)
                x += 10
            label = self.small.render(f"threshold {m.threshold}", True, (0, 0, 0))
            panel.blit(label, label.get_rect(bottomright=(right, y - 1)))

        # one line per zone (at most ~400 points so drawing stays cheap on long runs)
        stride = max(1, len(m.samples_t) // 400)
        for k, z in enumerate(zones):
            color = COLORS[k % len(COLORS)]
            pts = [px(m.samples_t[i], m.samples[z][i]) for i in range(0, len(m.samples_t), stride)]
            pts.append(px(m.samples_t[-1], m.samples[z][-1]))
            if len(pts) > 1:
                pg.draw.lines(panel, color, False, pts, 2)

        # legend
        y = bottom + 18
        for k, z in enumerate(zones):
            color = COLORS[k % len(COLORS)]
            pg.draw.line(panel, color, (10, y + 5), (24, y + 5), 3)
            text = (f"{z}: now {m.now.get(z, 0)}, peak {m.peak[z]} at {clock(m.peak_time[z])}, "
                    f"{m.minutes_above(z):g} min above")
            panel.blit(self.small.render(text, True, (0, 0, 0)), (30, y))
            y += 14
        window.blit(panel, (x0, y0))
