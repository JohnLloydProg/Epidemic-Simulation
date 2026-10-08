"""
Loads the simulation environment from the sim_data/ folder instead of the Excel files in map/.

    sim_data/
    ├── base/                       shared by every case (never edited by a case)
    │   ├── meta.json               units, CRS, UTM -> simulation coordinate transform
    │   ├── network_nodes.parquet   city + railway nodes (metres, y pointing down)
    │   ├── network_edges.parquet   city, railway and transfer edges (length in metres)
    │   ├── zones.json              barangays (role, PSGC, population, anchor node, nodes, outline) + gateways
    │   └── transit_routes.json     jeepney / bus / train routes as ordered edge lists (intervals in seconds)
    ├── cases/<case>.json           only the changes from base (hotspots, closures, route changes, ...)
    ├── cache/                      routing caches, one per case + base version
    └── results/<case_id>/          outputs of each run

Config keys (in the JSON file named by CONFIG_FILE_NAME):
    "DATA_DIR":  "sim_data"             turns this loader on
    "CASE_FILE": "00_baseline.json"     which case in sim_data/cases/ to open at start-up
                                        (other cases can be opened in the program: O, see ui/case_manager.py)
"""
import hashlib
import json
import logging
import os
import re
from pathlib import Path

import pandas as pd

import configuration as config
from graphing.core import Edge
from graphing.graph import Graph, RegionGraph
from transport.transportation import BusRoute, JeepRoute, TrainRoute

LOGGER = logging.getLogger('DataLoader')

ROUTE_CLASSES = {'jeepney': JeepRoute, 'jeep': JeepRoute, 'bus': BusRoute, 'train': TrainRoute, 'rail': TrainRoute}

EMPTY_CASE = {
    'case_id': '00_baseline', 'description': '', 'hotspots': [], 'closed_edges': [], 'capacity_multipliers': {},
    'disabled_routes': [], 'transit_overrides': {}, 'added_routes': [], 'od_scaling': {},
}


# --------------------------------------------------------------------------- paths
CASE_ENV = 'SIM_CASE_FILE'      # case opened in the running program (set by set_case_file); inherited by the
                                # routing-cache worker processes so they load the same case


def data_dir() -> Path:
    return Path(config.get('DATA_DIR', 'sim_data'))


def cases_dir() -> Path:
    return data_dir() / 'cases'


def case_file_name() -> str:
    """File name (in sim_data/cases/) of the case that is open: the one opened in the program, else CASE_FILE."""
    return os.environ.get(CASE_ENV) or config.get('CASE_FILE', '00_baseline.json')


def set_case_file(file_name:str):
    """Make `file_name` the open case for this process and any worker process started after this."""
    os.environ[CASE_ENV] = file_name
    config.put('CASE_FILE', file_name)


def case_path(file_name:str | None = None) -> Path:
    return cases_dir() / (file_name or case_file_name())


def read_case(path:Path) -> dict:
    with open(path, encoding='utf-8') as f:
        return {**EMPTY_CASE, **json.load(f)}


def load_case() -> dict:
    path = case_path()
    if not path.exists():
        LOGGER.warning(f"Case file '{path}' not found, running base data with no changes.")
        return dict(EMPTY_CASE)
    return read_case(path)


def results_dir() -> Path:
    path = data_dir() / 'results' / load_case()['case_id']
    path.mkdir(parents=True, exist_ok=True)
    return path


def _base_digest() -> 'hashlib._Hash':
    digest = hashlib.sha1()
    base = data_dir() / 'base'
    for name in ('network_nodes.parquet', 'network_edges.parquet', 'zones.json', 'transit_routes.json'):
        digest.update((base / name).read_bytes())
    return digest


def cache_file(path:Path | None = None, base_digest=None, create_dir:bool = True) -> Path:
    """Routing-cache file for a case (default: the open one). The name changes whenever the base data or the
    case file changes, so a stale cache is never reused (old caches can simply be deleted).
    base_digest: _base_digest() computed once, when looking up many cases."""
    path = Path(path) if path else case_path()
    digest = (base_digest or _base_digest()).copy()
    case_id = EMPTY_CASE['case_id']
    if path.exists():
        digest.update(path.read_bytes())
        case_id = read_case(path)['case_id']
    cache = data_dir() / 'cache' / f"routing_{case_id}_{digest.hexdigest()[:10]}.pkl"
    if create_dir:
        cache.parent.mkdir(parents=True, exist_ok=True)
    return cache


def cache_files_of(case_id:str) -> list[Path]:
    """Every routing-cache file written for a case id (current and stale)."""
    pattern = re.compile(rf"routing_{re.escape(case_id)}_[0-9a-f]{{10}}\.pkl")
    folder = data_dir() / 'cache'
    return [p for p in folder.glob('routing_*.pkl') if pattern.fullmatch(p.name)] if folder.exists() else []


def _edge_key(value, default_layer='city') -> tuple[str, int]:
    """Case files may write a closed edge as 1042 (city) or "railway:3"."""
    if isinstance(value, str) and ':' in value:
        layer, edge_id = value.split(':', 1)
        return (layer, int(edge_id))
    return (default_layer, int(value))


# --------------------------------------------------------------------------- loader
def oneway_start(row, node_a, node_b):
    """For a one-way road, the end vehicles enter it from (None if two-way or unknown).
    The edge's u -> v order is NOT reliable for this (in the exported data it is reversed on about half of the
    one-way roads), but the road geometry ("shape") still runs in the direction of travel, so it decides."""
    if not bool(getattr(row, 'oneway', False)) or getattr(row, 'layer', 'city') != 'city':
        return None
    shape = getattr(row, 'shape', None)
    try:
        points = json.loads(shape) if isinstance(shape, str) else list(shape)
        x, y = points[0][0], points[0][1]
    except (TypeError, ValueError, IndexError, KeyError):
        return node_a                                   # no geometry: fall back to u -> v
    ax, ay = getattr(node_a, 'precise_pos', node_a.pos)
    bx, by = getattr(node_b, 'precise_pos', node_b.pos)
    return node_a if (x - ax) ** 2 + (y - ay) ** 2 <= (x - bx) ** 2 + (y - by) ** 2 else node_b


def load_graph_from_data() -> tuple[RegionGraph, Graph, list]:
    if (not config.__config):
        config.init()

    base = data_dir() / 'base'
    case = load_case()
    LOGGER.info(f"Loading '{base}' with case '{case['case_id']}'...")

    city = RegionGraph('city')
    railway = Graph('railway')
    graphs = {'city': city, 'railway': railway}

    # ---- nodes
    nodes = pd.read_parquet(base / 'network_nodes.parquet')
    for row in nodes.itertuples(index=False):
        graphs[row.layer].add_node(int(round(row.x)), int(round(row.y)), int(row.node_id))
        node = graphs[row.layer].get_node((row.layer, int(row.node_id)))
        node.precise_pos = (float(row.x), float(row.y))
        if getattr(row, 'name', None) and isinstance(row.name, str):
            node.name = row.name

    # ---- edges (ids are kept from the data so routes and case files can refer to them)
    closed = {_edge_key(e) for e in case['closed_edges']}
    capacity = {_edge_key(k): float(v) for k, v in case['capacity_multipliers'].items()}
    edges = pd.read_parquet(base / 'network_edges.parquet')
    skipped = 0
    city.case_closed_edges = {}               # for drawing only: {edge_id: (pos_a, pos_b)}
    for row in edges.itertuples(index=False):
        edge_id = (row.layer, int(row.edge_id))
        if edge_id in closed:
            skipped += 1
            a = graphs[row.u_layer].get_node((row.u_layer, int(row.u)))
            b = graphs[row.v_layer].get_node((row.v_layer, int(row.v)))
            if row.layer == 'city' and a is not None and b is not None:
                city.case_closed_edges[edge_id] = (a.pos, b.pos)
            continue
        node_a = graphs[row.u_layer].get_node((row.u_layer, int(row.u)))
        node_b = graphs[row.v_layer].get_node((row.v_layer, int(row.v)))
        if node_a is None or node_b is None or node_a is node_b:
            LOGGER.debug(f'Skipping edge {edge_id}: missing or identical end nodes.')
            continue
        edge = Edge(node_a, node_b, max(1, int(round(row.length_m))), edge_id)
        edge.highway = getattr(row, 'highway', None)
        edge.road_name = getattr(row, 'name', None)
        edge.oneway_from = oneway_start(row, node_a, node_b)   # one-way: the node vehicles must enter from
        edge.capacity_multiplier = capacity.get(edge_id, 1.0)
        if row.layer == 'transfer':           # transfer edges belong to both layers, like the Excel loader
            city.edges[edge_id] = edge
            railway.edges[edge_id] = edge
        else:
            graphs[row.layer].edges[edge_id] = edge
        node_a.edges.append(edge)
        node_b.edges.append(edge)
    if skipped:
        LOGGER.info(f'Closed {skipped} edge(s) for this case.')

    # ---- zones (barangays) and gateways
    with open(base / 'zones.json', encoding='utf-8') as f:
        zones = json.load(f)
    hotspots = set(map(str, case['hotspots']))
    od_scaling = {str(k): float(v) for k, v in case['od_scaling'].items()}
    city.zones = {}
    for zone in zones['zones']:
        node_ids = [('city', int(i)) for i in zone['node_ids'] if ('city', int(i)) in city.nodes]
        city.add_region(node_ids, [], zone['name'])
        region = list(city.regions.values())[-1]
        region.psgc = str(zone['psgc'])
        region.role = zone['role']
        region.district = zone.get('district')
        region.population = zone.get('population_2024')
        region.area_km2 = zone.get('area_km2')
        region.anchor_node = city.get_node(('city', int(zone['anchor_node'])))
        region.polygon = [tuple(p) for p in zone.get('outline', [])]
        region.is_hotspot = region.psgc in hotspots
        region.od_scale = od_scaling.get(region.psgc, 1.0)
        city.zones[region.psgc] = region

    city.gateway_nodes = [city.get_node(('city', int(g['node_id']))) for g in zones.get('gateways', [])]
    city.gateway_nodes = [n for n in city.gateway_nodes if n is not None and n.edges]
    anchors = [r.anchor_node for r in city.zones.values() if r.anchor_node is not None and r.anchor_node.edges]
    city.anchor_nodes = list(dict.fromkeys(anchors + city.gateway_nodes))   # zone anchors first, no duplicates

    # ---- transit routes (base, then case changes)
    with open(base / 'transit_routes.json', encoding='utf-8') as f:
        route_defs = {r['route_id'] + '|' + r.get('layer', 'city'): r for r in json.load(f)['routes']}

    def matching(route_id):
        return [k for k in route_defs if k.split('|')[0] == route_id]

    for route_id in case['disabled_routes']:
        for key in matching(route_id):
            route_defs.pop(key)
    for route_id, changes in case['transit_overrides'].items():
        for key in matching(route_id):
            route_defs[key] = {**route_defs[key], **changes}
    for route in case['added_routes']:
        route_defs[route['route_id'] + '|' + route.get('layer', 'city')] = route

    routes = []
    dropped = []
    for key, rd in route_defs.items():
        graph = graphs[rd.get('layer', 'city')]
        route_cls = ROUTE_CLASSES.get(rd['mode'])
        path = [graph.get_edge((graph.layer, int(e))) for e in rd['edges']]
        start = graph.get_node((graph.layer, int(rd['start_node'])))
        if route_cls is None or start is None or not path or any(e is None for e in path):
            dropped.append(rd['route_id'])          # uses a closed/missing edge, or unknown mode
            continue
        try:
            end = start
            for edge in path:                       # also checks the edges form a connected chain
                end = edge.get_adjacent_node(end)
        except ValueError:
            dropped.append(rd['route_id'])
            continue

        interval, peak = int(rd['interval_s']), int(rd.get('peak_interval_s') or rd['interval_s'])
        # both directions follow one-way roads (transport/oneway_routes.py); the return trip comes from the
        # legal forward path
        from transport.oneway_routes import route_directions
        directions = route_directions(start, path, graph if graph.layer == 'city' else None,
                                      bool(rd.get('bidirectional', True)))
        for spawn, edges_in_order in directions:
            route = route_cls(spawn, edges_in_order, graph, interval, peak)
            route.route_id = rd['route_id']
            route.mode = rd['mode']
            route.name = rd.get('name', rd['route_id'])
            if rd.get('speed_mps'):
                route.expected_speed = float(rd['speed_mps'])
            routes.append(route)

    if dropped:
        LOGGER.warning(f'{len(dropped)} route(s) not loaded (closed or missing edges): {", ".join(sorted(set(dropped))[:10])}'
                       + (' ...' if len(set(dropped)) > 10 else ''))
    LOGGER.info(f'Loaded {len(city.nodes)} city nodes, {len(railway.nodes)} stations, {len(city.zones)} zones, '
                f'{len(city.gateway_nodes)} gateways, {len(routes)} route directions.')
    return city, railway, routes
