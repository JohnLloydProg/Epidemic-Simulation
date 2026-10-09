"""
view_filter.py — what is shown on the map: the view (graphics or bare) and the filters on people, vehicle types
and transit routes. Drawing only; the simulation and its metrics are never affected.

Used by ui/renderer.py (drawing) and ui/filter_panel.py (the panel that changes it, key L).

Categories (MODES):
    walking     people walking a leg of their trip
    waiting     people waiting at a node for a ride or transfer (bare view: the node's load colour)
    car         private cars
    tricycle    tricycles
    jeepney     jeepneys            routed: each route can also be hidden on its own
    bus         buses               routed
    train       LRT trains          routed
A routed vehicle is shown only when its mode is on AND its route is not hidden. Route lines (the path of each
route drawn on the map) follow the same rule and can be switched off altogether, separately for each view.

Size (graphics view): one multiplier for roads, rails, stations, vehicles and people, so they read well on the
whole-map view and in recordings. [ / ] or the panel's Size row change it (SIZE_STEPS); config "GRAPHICS_SCALE"
sets the starting value (default 2.0 = twice the real-world proportions).
"""
from __future__ import annotations

# key, label, colour in the bare view (as the vehicles/people are drawn there), sprite in the graphics view
MODES = [
    ('walking',  'People walking',  (200, 200, 200), 'person'),
    ('waiting',  'People waiting',  (230, 120, 0),   'person'),
    ('car',      'Private cars',    (255, 255, 0),   'car'),
    ('tricycle', 'Tricycles',       (20, 160, 60),   'tricycle'),
    ('jeepney',  'Jeepneys',        (0, 0, 255),     'jeepney'),
    ('bus',      'Buses',           (255, 0, 0),     'bus'),
    ('train',    'Trains (LRT)',    (0, 255, 0),     'train'),
]
MODE_LABELS = {key: label for key, label, _, _ in MODES}
ROUTED_MODES = ('jeepney', 'bus', 'train')

# colour of each routed mode's route lines in the graphics view and in the route list
ROUTE_COLORS = {'jeepney': (40, 110, 230), 'bus': (220, 50, 50), 'train': (20, 150, 70)}

_ROUTE_MODE = {'jeepney': 'jeepney', 'jeep': 'jeepney', 'bus': 'bus', 'train': 'train', 'rail': 'train'}
_VEHICLE_MODE = {'private': 'car', 'car': 'car', 'tricycle': 'tricycle', 'jeep': 'jeepney', 'jeepney': 'jeepney',
                 'bus': 'bus', 'rail': 'train', 'train': 'train'}


SIZE_STEPS = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0)
DEFAULT_SIZE = 2.0


def route_mode(route) -> str:
    """'jeepney', 'bus' or 'train' for a transit route."""
    mode = _ROUTE_MODE.get(str(getattr(route, 'mode', '')).lower())
    if mode:
        return mode
    name = type(route).__name__.lower()
    return 'train' if 'train' in name else 'bus' if 'bus' in name else 'jeepney'


def route_key(route) -> str:
    """Both directions of a route share this key (the route_id of the data)."""
    rid = getattr(route, 'route_id', None)
    return str(rid) if rid is not None else f"route-{route.id}"


def vehicle_mode(vehicle) -> str:
    """Category (MODES key) of a moving Transportation."""
    route = getattr(vehicle, 'route', None)
    if route is not None:
        return route_mode(route)
    return _VEHICLE_MODE.get(str(getattr(vehicle, 'method', '')).lower(), 'car')


class ViewFilter:
    def __init__(self, graphics:bool = True, size:float = DEFAULT_SIZE):
        self.graphics = bool(graphics)
        try:
            size = float(size)
        except (TypeError, ValueError):
            size = DEFAULT_SIZE
        self.size = min(SIZE_STEPS, key=lambda step: abs(step - size))
        self.modes = {key: True for key, _, _, _ in MODES}
        self.hidden_routes:set[str] = set()
        self.real_map = True            # graphics view: street map + real barangay boundaries (B; ui/basemap.py)
        self.zone_labels = True         # graphics view: barangay names
        self._route_lines = {'bare': True, 'graphics': False}   # graphics view: roads stay readable by default

    # ---------------------------------------------------------------- view
    @property
    def view(self) -> str:
        return 'graphics' if self.graphics else 'bare'

    def toggle_view(self):
        self.graphics = not self.graphics

    @property
    def route_lines(self) -> bool:
        return self._route_lines[self.view]

    @route_lines.setter
    def route_lines(self, value:bool):
        self._route_lines[self.view] = bool(value)

    def change_size(self, direction:int):
        """One step bigger (+1) or smaller (-1) in SIZE_STEPS."""
        index = SIZE_STEPS.index(self.size) + (1 if direction > 0 else -1)
        self.size = SIZE_STEPS[max(0, min(len(SIZE_STEPS) - 1, index))]

    # ---------------------------------------------------------------- filters
    def mode_on(self, mode:str) -> bool:
        return self.modes.get(mode, True)

    def toggle_mode(self, mode:str):
        self.modes[mode] = not self.modes.get(mode, True)

    def route_on(self, key:str, mode:str) -> bool:
        return self.mode_on(mode) and key not in self.hidden_routes

    def route_visible(self, route) -> bool:
        return self.route_on(route_key(route), route_mode(route))

    def vehicle_visible(self, vehicle, mode:str | None = None) -> bool:
        mode = mode or vehicle_mode(vehicle)
        if not self.mode_on(mode):
            return False
        route = getattr(vehicle, 'route', None)
        return route is None or route_key(route) not in self.hidden_routes

    def is_filtered(self) -> bool:
        """True when anything is hidden (shown on the closed panel as a reminder)."""
        return not all(self.modes.values()) or bool(self.hidden_routes)

    def show_all(self):
        self.modes = {key: True for key in self.modes}
        self.hidden_routes.clear()
