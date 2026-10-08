"""
congestion.py — an abstracted, mesoscopic road-congestion model for the vehicles in the simulation.

Every road vehicle (private car, jeepney, bus, tricycle) still moves edge by edge with the existing events. This
module decides HOW LONG each edge takes, and WHETHER the vehicle may enter it yet. Rail and transfer edges and
walking are not affected.

The mechanism (each part follows a published model; see docs/congestion_model.md for the full write-up)
----------------------------------------------------------------------------------------------------
Each direction of a city edge is a "link" with a moving part and a queue at its downstream end, the segment
structure of mesoscopic dynamic traffic assignment models such as DynaMIT (Ben-Akiva et al., 2001) and of
MATSim's queue simulation (Gawron, 1998; Cetin et al., 2003; Horni et al., 2016).

1. Moving part — speed from density (Greenshields, 1935; the "modified Greenshields" form used for segment
   speeds in DYNASMART and DynaMIT, Mahmassani, 2001; Ben-Akiva et al., 2001):

        rho  = background occupancy + (PCU already on the link) / (jam storage of the link)
        v    = v_min + (v_free - v_min) * max(0, 1 - rho**alpha) ** beta          alpha = beta = 1: Greenshields
        time = length / min(v, the vehicle's own speed)

   The speed is fixed when the vehicle enters the link (event-based, like the DTA models above).

2. Queue part — flow capacity at the link's exit (point queue, Vickrey 1969; MATSim's flow capacity):

        exit time = max(entry time + moving time,  previous vehicle's exit + PCU * 3600 / capacity)
        capacity  = lanes * Greenshields maximum flow (v_free * k_jam / 4) * case capacity multiplier,
                    and at a signalised junction at most lanes * saturation flow (HCM base 1,900 pcu/h/lane) * g/C

   Vehicles leave a link in order, no faster than its capacity, so dense jeepney corridors form queues.

3. Spillback (optional, CONGESTION_SPILLBACK) — storage capacity (MATSim): a link holds at most
   lanes * length / 7.5 m PCU. A vehicle whose next link is full waits at the node and keeps occupying its current
   link, so queues grow backwards. As in MATSim, after CONGESTION_STUCK_S seconds (MATSim default 10 s) one waiting
   vehicle is let in per link. Off by default: with the current transit schedule the corridors around Lawton and
   the northern bridges are loaded far past capacity and spillback locks them up; without it, queues stay on the
   overloaded link (a point queue), which still gives the delay.

4. Background traffic — the OD demand only creates trips that start or end in the study area (through trips are
   dropped), so part of the real traffic on Manila's roads is missing. It can be added as a background occupancy
   (share of jam density) by hour of day (CONGESTION_BACKGROUND, default 0), fitted with
   tools/calibrate_congestion.py so simulated private cars reproduce TomTom's 2025 Manila rush-hour speeds
   (17.2 km/h morning, 13.8 km/h evening). Background traffic lowers speeds and takes storage space; it does not
   take exit capacity (its delay at intersections is part of the calibration).

Vehicle sizes are passenger car units (PCU): car 1.0; jeepney 1.3 and bus 2.5 (MUCEP, JICA/ALMEC 2015);
tricycle 0.535 (Raymundo, Vergel & Gaspay, 2024, urban local roads in Metro Manila).
Free-flow speeds follow the urban speed limits of JMC 2018-001 / RA 4136: through streets and boulevards
40 km/h, city streets 30 km/h, crowded streets 20 km/h.

Config keys (JSON named by CONFIG_FILE_NAME), all optional — the defaults below are used otherwise
    "CONGESTION":                true          false = old behaviour (fixed speeds, no interaction)
    "CONGESTION_ROAD_CLASSES":   {highway: {"lanes": .., "free_kmh": ..}}   merged into the defaults
    "CONGESTION_PCU":            {"private": 1.0, "jeep": 1.3, "bus": 2.5, "tricycle": 0.535}
    "CONGESTION_SAT_FLOW":       1900          pcu/h/lane at signals (HCM base saturation flow)
    "CONGESTION_SIGNAL_G_C":     0.45          green share of the cycle for each approach of a signal
    "CONGESTION_JAM_SPACING_M":  7.5           road length per stopped PCU (jam density 133 pcu/km/lane)
    "CONGESTION_MIN_KMH":        5             speed at jam density
    "CONGESTION_ALPHA", "CONGESTION_BETA": 1.0, 1.0
    "CONGESTION_SPILLBACK":      false         true = full links block entry (see note 3 above)
    "CONGESTION_STUCK_S":        10
    "CONGESTION_BACKGROUND":     [24 values]   background occupancy (0-0.9) by hour of day, hour 0 first (default 0)
    "CONGESTION_LOG_INTERVAL_S": 300           period of congestion_timeseries.csv (in each metrics run folder)

A case file's "capacity_multipliers" ({edge_id: factor}) scales an edge's capacity and storage (e.g. 0.5 = one
of two lanes closed), so "limit traffic" interventions can be saved per case.
"""
from __future__ import annotations
import csv
import logging
import math
from pathlib import Path

import configuration as config
import manager

LOGGER = logging.getLogger('Congestion')

# ------------------------------------------------------------------------------------------------- defaults
# lanes: per carriageway direction, used when the edge data has no OSM "lanes" (OSM draws Manila's avenues as two
# one-way carriageways, so a trunk edge is one direction). free_kmh: JMC 2018-001 urban speed limits.
ROAD_CLASSES = {
    'trunk':          {'lanes': 3, 'free_kmh': 40},
    'primary':        {'lanes': 2, 'free_kmh': 40},
    'secondary':      {'lanes': 2, 'free_kmh': 30},
    'tertiary':       {'lanes': 2, 'free_kmh': 30},
    'unclassified':   {'lanes': 1, 'free_kmh': 30},
    'residential':    {'lanes': 1, 'free_kmh': 20},
    'trunk_link':     {'lanes': 1, 'free_kmh': 30},
    'primary_link':   {'lanes': 1, 'free_kmh': 30},
    'secondary_link': {'lanes': 1, 'free_kmh': 30},
    'tertiary_link':  {'lanes': 1, 'free_kmh': 30},
    '_default':       {'lanes': 1, 'free_kmh': 30},
}
MAJOR_ROADS = {'trunk', 'primary', 'secondary', 'tertiary'}
PCU = {'private': 1.0, 'car': 1.0, 'jeep': 1.3, 'jeepney': 1.3, 'bus': 2.5, 'tricycle': 0.535}
# Background occupancy by hour (share of jam density taken by traffic that is not simulated). Zero by default: it is
# a calibration term, fitted with tools/calibrate_congestion.py so private cars match TomTom Traffic Index 2025 for
# Manila (17.2 km/h morning rush, 13.8 km/h evening rush). Reference fit on the baseline case with transit dispatched
# 3x less often (so no corridor is loaded past capacity by the schedule alone): 0.04 for 06-08, 0.33 for 17-19.
BACKGROUND = [0.0] * 24

MOTORISED_LAYER = 'city'          # rail ('railway') and 'transfer' edges are never congested


# ------------------------------------------------------------------------------------------------- settings
class Settings:
    def __init__(self):
        get = lambda key, default: _cfg(key, default)
        self.enabled = bool(get('CONGESTION', True))
        classes = {k: dict(v) for k, v in ROAD_CLASSES.items()}
        for name, values in (get('CONGESTION_ROAD_CLASSES', {}) or {}).items():
            classes[name] = {**classes.get(name, classes['_default']), **values}
        self.classes = classes
        self.pcu = {**PCU, **(get('CONGESTION_PCU', {}) or {})}
        self.sat_flow = float(get('CONGESTION_SAT_FLOW', 1900))
        self.g_c = float(get('CONGESTION_SIGNAL_G_C', 0.45))
        self.jam_spacing = float(get('CONGESTION_JAM_SPACING_M', 7.5))
        self.v_min = float(get('CONGESTION_MIN_KMH', 5)) / 3.6
        self.alpha = float(get('CONGESTION_ALPHA', 1.0))
        self.beta = float(get('CONGESTION_BETA', 1.0))
        self.spillback = bool(get('CONGESTION_SPILLBACK', False))
        self.stuck_s = float(get('CONGESTION_STUCK_S', 10))
        background = list(get('CONGESTION_BACKGROUND', BACKGROUND))
        if len(background) != 24:
            raise ValueError("CONGESTION_BACKGROUND needs 24 values (hour 0 to 23).")
        self.background = [min(max(float(b), 0.0), 0.9) for b in background]
        self.log_interval = int(get('CONGESTION_LOG_INTERVAL_S', 300))


def _cfg(key, default):
    try:
        return config.get(key, default)
    except ValueError:              # configuration not loaded (e.g. a test importing this module)
        return default


# ------------------------------------------------------------------------------------------------- links
class Link:
    """One direction of a city edge."""
    __slots__ = ('edge', 'from_node', 'length', 'lanes', 'v_free', 'capacity_pcu_h', 'storage_pcu',
                 'vehicles', 'pcu', 'last_exit', 'forced_at', 'passed', 'veh_m', 'veh_s', 'delay_s',
                 'max_rho', 'max_queue')

    def __init__(self, edge, from_node, s:Settings):
        road = edge.highway if isinstance(edge.highway, str) else None
        cls = s.classes.get(road, s.classes['_default'])
        multiplier = max(float(getattr(edge, 'capacity_multiplier', 1.0) or 0.0), 0.05)
        self.edge, self.from_node = edge, from_node
        self.length = max(float(edge.distance), 1.0)
        self.lanes = _lanes(edge, cls)
        self.v_free = float(cls['free_kmh']) / 3.6
        # capacity of one lane: Greenshields' maximum flow v_free * k_jam / 4 (the same curve as the speeds), and at
        # a signalised junction at most the HCM saturation flow times the green ratio
        k_jam = 1000.0 / s.jam_spacing                                    # pcu/km/lane
        per_lane = (3.6 * self.v_free) * k_jam / 4.0
        if is_signal(edge.get_adjacent_node(from_node)):
            per_lane = min(per_lane, s.sat_flow * s.g_c)
        self.capacity_pcu_h = self.lanes * per_lane * multiplier
        # always room for one bus, so short links between close intersections still work (MATSim does the same)
        self.storage_pcu = max(self.lanes * self.length / s.jam_spacing * multiplier, 2.5)
        self.vehicles = {}            # vehicle -> pcu
        self.pcu = 0.0
        self.last_exit = -math.inf
        self.forced_at = -math.inf
        self.passed = 0               # statistics
        self.veh_m = 0.0
        self.veh_s = 0.0
        self.delay_s = 0.0
        self.max_rho = 0.0
        self.max_queue = 0


def _lanes(edge, cls) -> float:
    """OSM lanes of the edge when the data has them (one-way: all of them; two-way: half), else the class default."""
    raw = getattr(edge, 'lanes', None)
    try:
        values = [float(v) for v in str(raw).replace(';', '|').split('|') if v.strip()]
        lanes = max(values)
        if getattr(edge, 'oneway_from', None) is None:
            lanes = lanes / 2
        return max(lanes, 1.0)
    except (TypeError, ValueError):
        return max(float(cls['lanes']), 1.0)


_signals = {}


def is_signal(node) -> bool:
    """Traffic signal at this node: the OSM tag when the node data has it (column "highway" = "traffic_signals"),
    otherwise a junction where two different named major roads (tertiary or higher) meet."""
    known = _signals.get(node.id)
    if known is not None:
        return known
    tag = getattr(node, 'osm_highway', None)
    if tag is not None:
        result = tag == 'traffic_signals'
    else:
        city = [e for e in node.edges if e.id[0] == MOTORISED_LAYER]
        names = {getattr(e, 'road_name', None) for e in city if getattr(e, 'highway', None) in MAJOR_ROADS}
        names.discard(None)
        names = {n for n in names if isinstance(n, str)}
        result = len(city) >= 3 and len(names) >= 2
    _signals[node.id] = result
    return result


class _State:
    def __init__(self):
        self.settings = None
        self.links = {}               # (edge id, from node id) -> Link
        self.on_link = {}             # vehicle -> Link it currently occupies
        self.waiting = {}             # vehicle -> time it started waiting for a full link
        self.interval = None          # running totals for the time series
        self.totals = None            # running totals since start_measure() (calibration)
        self.run_totals = None        # running totals since open_run() (summary.json)
        self.last_snapshot = None
        self.series_path = None
        self.links_path = None
        self.next_log = None
        self.next_link_dump = None


_state = _State()


def settings() -> Settings:
    if _state.settings is None:
        _state.settings = Settings()
    return _state.settings


def enabled() -> bool:
    return settings().enabled


def reset():
    """Forget every link, vehicle and statistic (new run / reset / case opened). Settings are reloaded."""
    close_run()
    _state.settings = None
    _signals.clear()
    _state.links.clear()
    _state.on_link.clear()
    _state.waiting.clear()
    _state.interval = _new_interval()
    _state.totals = _new_interval()
    _state.run_totals = _new_interval()
    _state.last_snapshot = None


def open_run(folder:Path, start_time:int):
    """Start logging into a metrics run folder (metrics.py calls this when a run's logs are opened):
    congestion_timeseries.csv every CONGESTION_LOG_INTERVAL_S, congestion_links.csv every simulated hour and
    whenever the run is saved."""
    close_run()
    s = settings()
    _state.run_totals = _new_interval()
    if not s.enabled or folder is None:
        return
    folder = Path(folder)
    _state.series_path = folder / 'congestion_timeseries.csv'
    _state.links_path = folder / 'congestion_links.csv'
    _state.next_log = start_time + s.log_interval
    _state.next_link_dump = start_time + 3600
    with open(_state.series_path, 'w', newline='') as f:
        csv.writer(f).writerow(SERIES_COLUMNS)


def close_run():
    """Stop logging (the files written so far stay)."""
    _state.series_path = _state.links_path = None
    _state.next_log = _state.next_link_dump = None


def run_settings() -> dict:
    """The congestion settings of a run, for run.log and summary.json."""
    s = settings()
    if not s.enabled:
        return {'CONGESTION': False}
    return {'CONGESTION': True, 'CONGESTION_SPILLBACK': s.spillback, 'CONGESTION_BACKGROUND': s.background,
            'CONGESTION_PCU': {k: s.pcu[k] for k in ('private', 'jeep', 'bus', 'tricycle')},
            'CONGESTION_SAT_FLOW': s.sat_flow, 'CONGESTION_SIGNAL_G_C': s.g_c,
            'CONGESTION_JAM_SPACING_M': s.jam_spacing, 'CONGESTION_MIN_KMH': round(s.v_min * 3.6, 2)}


def run_summary() -> dict:
    """Totals since open_run(), for summary.json: mean road speed by mode and road delay."""
    if not enabled():
        return {'enabled': False}
    iv = _state.run_totals
    modes = sorted(iv['m'])
    worst = sorted((l for l in _state.links.values() if l.passed), key=lambda l: -l.delay_s)[:10]
    return {'enabled': True, 'spillback': settings().spillback,
            'mean_speed_kmh': {**{m: _speed(iv, m) for m in modes}, 'all': _speed(iv)},
            'vehicle_km': round(sum(iv['m'].values()) / 1000, 1),
            'vehicle_hours': round(sum(iv['s'].values()) / 3600, 1),
            'delay_vehicle_hours': round(iv['delay'] / 3600, 1),
            'worst_links': [{'edge_id': l.edge.id[1], 'from_node': l.from_node.id[1],
                             'road': l.edge.road_name if isinstance(getattr(l.edge, 'road_name', None), str) else '',
                             'delay_vehicle_hours': round(l.delay_s / 3600, 2),
                             'mean_speed_kmh': round(3.6 * l.veh_m / l.veh_s, 1) if l.veh_s else None}
                            for l in worst]}


def _link(edge, from_node) -> Link:
    key = (edge.id, from_node.id)
    link = _state.links.get(key)
    if link is None:
        link = _state.links[key] = Link(edge, from_node, settings())
    return link


def is_congestible(edge) -> bool:
    return edge.id[0] == MOTORISED_LAYER and enabled()


def background(time:int) -> float:
    return settings().background[(time // 3600) % 24]


def density_ratio(link:Link, time:int) -> float:
    """Occupancy as a share of jam density: background + simulated PCU already on the link."""
    return min(background(time) + link.pcu / link.storage_pcu, 1.0)


def link_speed(link:Link, time:int) -> float:
    s = settings()
    rho = density_ratio(link, time)
    if rho <= 0:
        return link.v_free
    return s.v_min + (link.v_free - s.v_min) * max(0.0, 1.0 - rho ** s.alpha) ** s.beta


def _has_space(link:Link, time:int) -> bool:
    """MATSim rule: a link accepts a vehicle while its used storage is below its capacity."""
    return link.pcu < link.storage_pcu * (1.0 - background(time))


# ------------------------------------------------------------------------------------------------- vehicles
def vehicle_pcu(vehicle) -> float:
    return settings().pcu.get(vehicle.method, 1.0)


def try_enter(vehicle, edge, from_node, time:int) -> float | None:
    """Put the vehicle on `edge` (coming from `from_node`) at `time`.
    Returns the seconds until it reaches the far end, or None when the link is full (spillback): the vehicle
    then stays where it is and must try again (retry_later)."""
    if not is_congestible(edge):
        leave(vehicle, time)
        return edge.distance / vehicle.speed

    s = settings()
    link = _link(edge, from_node)
    if s.spillback and not _has_space(link, time):
        since = _state.waiting.setdefault(vehicle, time)
        if time - since < s.stuck_s or time - link.forced_at < s.stuck_s:
            return None
        link.forced_at = time                  # stuck too long: let it in (one vehicle per stuck time)
    _state.waiting.pop(vehicle, None)

    leave(vehicle, time)                       # leaves the link it was waiting on (if any)
    pcu = vehicle_pcu(vehicle)
    speed = min(link_speed(link, time), vehicle.speed)
    moving = link.length / speed
    headway = pcu * 3600.0 / link.capacity_pcu_h
    exit_time = max(time + moving, link.last_exit + headway)
    link.last_exit = exit_time
    link.vehicles[vehicle] = pcu
    link.pcu += pcu
    _state.on_link[vehicle] = link

    travel = exit_time - time
    free = link.length / min(link.v_free, vehicle.speed)
    link.passed += 1
    link.veh_m += link.length
    link.veh_s += travel
    link.delay_s += max(travel - free, 0.0)
    rho = density_ratio(link, time)
    link.max_rho = max(link.max_rho, rho)
    link.max_queue = max(link.max_queue, len(link.vehicles))
    _record(vehicle.method, link.length, travel, max(travel - free, 0.0))
    return travel


def leave(vehicle, time:int | None = None):
    """Take the vehicle off the link it occupies (despawn, or it moved on)."""
    link = _state.on_link.pop(vehicle, None)
    if link is not None and vehicle in link.vehicles:
        link.pcu = max(link.pcu - link.vehicles.pop(vehicle), 0.0)
    _state.waiting.pop(vehicle, None)


def retry_later(vehicle, time:int):
    """Schedule another attempt to enter the next link one time step later."""
    manager.emit(time + 1, manager.Event(manager.VEHICLE_ENTER_RETRY, vehicle))


def is_waiting(vehicle) -> bool:
    return vehicle in _state.waiting


def check_transit_load(routes, worst:int = 8) -> list:
    """Warn about links where the transit schedule alone needs more than the road's capacity (peak intervals,
    1.5 jeepneys per dispatch as in JeepRoute). Those links will queue without limit whatever else happens."""
    if not enabled():
        return []
    s = settings()
    load, count = {}, {}
    for route in routes:
        if route.graph.layer != MOTORISED_LAYER:
            continue
        mode = getattr(route, 'mode', '')
        per_dispatch = 1.5 if mode in ('jeepney', 'jeep') else 1.0
        pcu = s.pcu.get('jeep' if mode in ('jeepney', 'jeep') else mode, 1.0)
        rate = per_dispatch * 3600.0 / max(route.peak_spawn, 1) * pcu
        node = route.spawn_node
        for edge in route.path:
            key = (edge, node)
            load[key] = load.get(key, 0.0) + rate
            count[key] = count.get(key, 0) + 1
            node = edge.get_adjacent_node(node)
    rows = []
    for (edge, node), q in load.items():
        link = Link(edge, node, s)
        if q > link.capacity_pcu_h:
            name = getattr(edge, 'road_name', None)
            rows.append((q / link.capacity_pcu_h, name if isinstance(name, str) else f"edge {edge.id[1]}",
                         edge.id[1], round(q), round(link.capacity_pcu_h), count[(edge, node)]))
    rows.sort(key=lambda r: -r[0])
    if rows:
        LOGGER.warning(f"Transit schedule alone exceeds road capacity on {len(rows)} link(s) at peak (these queue "
                       f"without limit): " + "; ".join(f"{r[1]} (edge {r[2]}) {r[3]} pcu/h vs {r[4]}, {r[5]} routes, x{r[0]:.1f}"
                                                       for r in rows[:worst]))
    return rows


# ------------------------------------------------------------------------------------------------- outputs
SERIES_COLUMNS = ['time_s', 'clock', 'background_rho', 'vehicles_on_links', 'vehicles_waiting',
                  'links_past_critical_density', 'mean_speed_kmh_private', 'mean_speed_kmh_jeep', 'mean_speed_kmh_bus',
                  'mean_speed_kmh_tricycle', 'mean_speed_kmh_all', 'delay_veh_h']


def _new_interval() -> dict:
    return {'m': {}, 's': {}, 'delay': 0.0}


def _record(method, metres, seconds, delay):
    for iv in (_state.interval, _state.totals, _state.run_totals):
        iv['m'][method] = iv['m'].get(method, 0.0) + metres
        iv['s'][method] = iv['s'].get(method, 0.0) + seconds
        iv['delay'] += delay


def start_measure():
    """Restart the running totals read by measured_speed() (e.g. after a warm-up)."""
    _state.totals = _new_interval()


def measured_speed(method:str | None = None):
    """Mean speed in km/h (distance / time on links) since start_measure(), '' if nothing was measured."""
    return _speed(_state.totals, method)


def _speed(iv, method=None):
    m = sum(iv['m'].values()) if method is None else iv['m'].get(method, 0.0)
    sec = sum(iv['s'].values()) if method is None else iv['s'].get(method, 0.0)
    return round(3.6 * m / sec, 2) if sec > 0 else ''


def snapshot(time:int) -> dict:
    """Current state for the screen and the time series (speeds are for links entered since the last log)."""
    iv = _state.interval
    busy = [l for l in _state.links.values() if l.vehicles]
    over = sum(1 for l in busy if density_ratio(l, time) >= 0.5)       # past Greenshields' critical density
    return {'background': background(time), 'on_links': len(_state.on_link), 'waiting': len(_state.waiting),
            'links_over_critical': over, 'speed_private': _speed(iv, 'private'), 'speed_jeep': _speed(iv, 'jeep'),
            'speed_bus': _speed(iv, 'bus'), 'speed_tricycle': _speed(iv, 'tricycle'), 'speed_all': _speed(iv),
            'delay_veh_h': round(iv['delay'] / 3600, 2)}


def tick(time:int):
    """Called once per simulation step: writes the time series and the link table when they are due."""
    if _state.next_log is None:                       # not logging (no metrics run yet): still feed the HUD
        if time % settings().log_interval == 0:
            _state.last_snapshot = snapshot(time)
            _state.interval = _new_interval()
        return
    if time >= _state.next_log:
        snap = snapshot(time)
        clock = f"{(time // 3600) % 24:02d}:{(time // 60) % 60:02d}"
        try:
            with open(_state.series_path, 'a', newline='') as f:
                csv.writer(f).writerow([time, clock, snap['background'], snap['on_links'], snap['waiting'],
                                        snap['links_over_critical'], snap['speed_private'], snap['speed_jeep'],
                                        snap['speed_bus'], snap['speed_tricycle'], snap['speed_all'],
                                        snap['delay_veh_h']])
        except OSError as error:
            LOGGER.warning(f"Could not write {_state.series_path}: {error}")
        _state.last_snapshot = snap
        _state.interval = _new_interval()
        _state.next_log += settings().log_interval
    if _state.next_link_dump is not None and time >= _state.next_link_dump:
        write_link_table()
        _state.next_link_dump += 3600


def last_snapshot() -> dict | None:
    return _state.last_snapshot


def write_link_table(path:Path | None = None):
    """One row per link used so far: traffic, mean speed, delay, worst occupancy (for hotspot analysis)."""
    path = path or _state.links_path
    if path is None or not enabled():
        return
    rows = []
    for (edge_id, node_id), l in _state.links.items():
        if not l.passed:
            continue
        name = getattr(l.edge, 'road_name', None)
        rows.append([edge_id[1], node_id[1], name if isinstance(name, str) else '', l.edge.highway, round(l.length, 1),
                     int(l.lanes), round(l.capacity_pcu_h), round(l.storage_pcu, 1), l.passed,
                     round(3.6 * l.veh_m / l.veh_s, 2) if l.veh_s else '', round(3.6 * l.v_free, 1),
                     round(l.delay_s / 3600, 3), round(l.max_rho, 3), l.max_queue])
    rows.sort(key=lambda r: -r[11])
    try:
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['edge_id', 'from_node', 'road_name', 'highway', 'length_m', 'lanes', 'capacity_pcu_h',
                        'storage_pcu', 'vehicles_entered', 'mean_speed_kmh', 'free_speed_kmh', 'delay_veh_h',
                        'max_occupancy', 'max_vehicles'])
            w.writerows(rows)
    except OSError as error:
        LOGGER.warning(f"Could not write {path}: {error}")


# ------------------------------------------------------------------------------------------------- drawing
def link_color(rho:float) -> tuple[int, int, int]:
    """Green (free) -> yellow (critical density) -> red (jam)."""
    rho = min(max(rho, 0.0), 1.0)
    if rho < 0.5:
        return (int(510 * rho), 190, 40)
    return (255, int(190 * (1 - (rho - 0.5) * 2)), 40)


def draw(window, camera, time:int):
    """Colours every link that has simulated vehicles on it by its occupancy (incl. background)."""
    import pygame as pg
    width = camera.scale(4, minimum=2)
    for link in _state.links.values():
        if not link.vehicles:
            continue
        a, b = link.edge.nodes
        pg.draw.line(window, link_color(density_ratio(link, time)), camera.to_screen(a.pos), camera.to_screen(b.pos), width)
