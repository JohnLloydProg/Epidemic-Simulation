import pygame as pg


class Camera:
    """Maps world (map) coordinates to screen coordinates, with pan and zoom.

    screen = world * zoom + offset
    """
    MIN_ZOOM = 0.1
    MAX_ZOOM = 6.0
    ZOOM_STEP = 1.1          # zoom factor per mouse-wheel notch / key press
    LABEL_MIN_ZOOM = 0.6     # hide node labels below this zoom (they become unreadable clutter)

    def __init__(self):
        self.zoom = 1.0
        self.x_offset = 0.0
        self.y_offset = 0.0
        self._drag_start = None
        self._drag_origin = None
        self._home = (1.0, 0.0, 0.0)

    # ---------- coordinate transforms ----------
    def to_screen(self, pos:tuple[float, float]) -> tuple[float, float]:
        return (pos[0] * self.zoom + self.x_offset, pos[1] * self.zoom + self.y_offset)

    def to_world(self, pos:tuple[float, float]) -> tuple[float, float]:
        return ((pos[0] - self.x_offset) / self.zoom, (pos[1] - self.y_offset) / self.zoom)

    def scale(self, length:float, minimum:int = 1) -> int:
        """Scale a world-space length (radius, line width) to screen pixels."""
        return max(minimum, round(length * self.zoom))

    def show_labels(self) -> bool:
        return self.zoom >= self.LABEL_MIN_ZOOM

    # ---------- view control ----------
    def zoom_at(self, factor:float, anchor:tuple[float, float]):
        """Zoom by `factor`, keeping the world point under `anchor` (screen px) fixed."""
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom * factor))
        applied = new_zoom / self.zoom
        if applied == 1:
            return
        ax, ay = anchor
        self.x_offset = ax - (ax - self.x_offset) * applied
        self.y_offset = ay - (ay - self.y_offset) * applied
        self.zoom = new_zoom

    def fit(self, positions:list[tuple[float, float]], screen_size:tuple[int, int], margin:int = 40):
        """Zoom and center so every given world position is visible. Also becomes the reset (0 key) view."""
        if not positions:
            return
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        width = max(max(xs) - min(xs), 1)
        height = max(max(ys) - min(ys), 1)
        zoom = min((screen_size[0] - 2 * margin) / width, (screen_size[1] - 2 * margin) / height)
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        center_x = (min(xs) + max(xs)) / 2
        center_y = (min(ys) + max(ys)) / 2
        self.x_offset = screen_size[0] / 2 - center_x * self.zoom
        self.y_offset = screen_size[1] / 2 - center_y * self.zoom
        self._home = (self.zoom, self.x_offset, self.y_offset)

    def reset(self):
        self.zoom, self.x_offset, self.y_offset = self._home

    # ---------- input ----------
    def handle_event(self, event:pg.event.Event):
        if event.type == pg.MOUSEWHEEL:
            self.zoom_at(self.ZOOM_STEP ** event.y, pg.mouse.get_pos())

        # Left button only: pygame also reports wheel scrolls as MOUSEBUTTONDOWN (buttons 4/5),
        # which would otherwise start a phantom drag.
        elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            self._drag_start = event.pos
            self._drag_origin = (self.x_offset, self.y_offset)
        elif event.type == pg.MOUSEBUTTONUP and event.button == 1:
            self._drag_start = None
        elif event.type == pg.MOUSEMOTION and self._drag_start is not None:
            self.x_offset = self._drag_origin[0] + (event.pos[0] - self._drag_start[0])
            self.y_offset = self._drag_origin[1] + (event.pos[1] - self._drag_start[1])

        elif event.type == pg.KEYDOWN:
            surface = pg.display.get_surface()
            center = surface.get_rect().center if surface else (0, 0)
            if event.key in (pg.K_EQUALS, pg.K_PLUS, pg.K_KP_PLUS):
                self.zoom_at(self.ZOOM_STEP, center)
            elif event.key in (pg.K_MINUS, pg.K_KP_MINUS):
                self.zoom_at(1 / self.ZOOM_STEP, center)
            elif event.key in (pg.K_0, pg.K_KP0):
                self.reset()