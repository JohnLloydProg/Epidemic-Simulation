"""
Turns the City of Manila OD model (manila_od.py + od_bundle/) into agents for this simulation.

How trips become agents
  1. The OD matrix (person-trips per day, 919 zones) comes from manila_od: the base matrix, or a scenario
     when the case file has "od_settings" (e.g. {"attraction": {"Barangay 669": 0.5}}).
  2. Case "od_scaling" ({psgc: factor}) multiplies every trip that starts or ends in that barangay.
  3. Trip ends are mapped to nodes:
       - study barangays whose role is in OD_TRIP_END_ROLES (default ["od"]) -> their anchor node
       - zones outside the study area -> the nearest gateway node (major road entering the area)
       - other study barangays (connectors) -> dropped
     Kept trips: study <-> study, and study <-> outside if OD_EXTERNAL_TRIPS. Outside <-> outside (through
     trips) and trips whose two ends land on the same node are dropped.
  4. Only the share of daily trips that falls in the simulated window is kept (OD_HOUR_PROFILE), divided by
     OD_TRIPS_PER_AGENT, then turned into whole agents (OD_SAMPLING).
  5. Each agent gets a departure second inside the window and is scheduled as an AGENT_SPAWN event.
  6. Agents of a study barangay start/end at one of the zone's nodes, every node equally likely
     (OD_SPAWN_AT = "zone_nodes"), and are routed directly between their own nodes. Only nodes that can walk
     to the zone's anchor are used, so an agent never starts on an isolated piece of road.
     Gateway trips start/end at the gateway node.

Config keys (JSON file named by CONFIG_FILE_NAME)
  "OD_BUNDLE_DIR":       "od_bundle"   turns OD demand on (omit it to keep the old right-click-only behaviour)
  "SIM_START_HOUR":      6             clock time when the simulation starts
  "OD_DURATION_HOURS":   3             hours of departures generated, starting at SIM_START_HOUR
  "OD_HOUR_PROFILE":     [24 weights]  relative trips per hour of day, hour 0 first (default: uniform)
  "OD_TRIPS_PER_AGENT":  1             1 = full scale; >1 thins demand (vehicles then look emptier than reality)
  "OD_SAMPLING":         "sample"      "sample" (multinomial, keeps small flows) or "round" (deterministic)
  "OD_SEED":             42
  "OD_EXTERNAL_TRIPS":   true
  "OD_TRIP_END_ROLES":   ["od"]
  "OD_SPAWN_AT":         "zone_nodes"  or "anchor" (every agent at the zone's anchor node)
  "OD_INTRAZONAL":       false         also spawn trips within one barangay (needs "zone_nodes")
  "HOTSPOT_ATTRACTION":  0.5           OD attraction multiplier for hotspot barangays (1 = no effect, 0 = no trips
                                       end there); a case file's "hotspot_attraction" overrides it

Hotspots (case "hotspots", or chosen with H in the simulation) lower their barangay's attraction in the OD model,
so fewer trips end there and the model sends them to other destinations instead (total trips unchanged).
This is a scenario setting: beta is not recalibrated. It multiplies with any "attraction" in "od_settings".
"""
from __future__ import annotations
import logging
import json
from pathlib import Path

import numpy as np
import pandas as pd

import configuration as config
import manager

LOGGER = logging.getLogger('ODDemand')


class TripSpec:
    """One agent waiting to be spawned (the payload of an AGENT_SPAWN event)."""
    __slots__ = ('origin', 'destination', 'origin_zone', 'destination_zone', 'kind', 'depart')

    def __init__(self, origin, destination, origin_zone, destination_zone, kind, depart):
        self.origin, self.destination = origin, destination
        self.origin_zone, self.destination_zone = origin_zone, destination_zone
        self.kind, self.depart = kind, depart


# --------------------------------------------------------------------------- helpers
def zone_points(zones: pd.DataFrame) -> np.ndarray:
    """(n, 2) UTM coordinates of the bundle's zone points (x/y columns or a WKT 'POINT (x y)' column)."""
    cols = {c.lower(): c for c in zones.columns}
    for xc, yc in (('pt_x', 'pt_y'), ('x', 'y'), ('point_x', 'point_y'), ('easting', 'northing')):
        if xc in cols and yc in cols:
            return zones[[cols[xc], cols[yc]]].to_numpy(float)
    for c in ('point', 'geometry', 'wkt'):
        if c in cols:
            xy = zones[cols[c]].astype(str).str.extract(r'\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)')
            if xy.notna().all().all():
                return xy.astype(float).to_numpy()
    raise ValueError(f"No point coordinates in the bundle's zones.csv (columns: {list(zones.columns)}). "
                     "They are needed to send outside trips to the nearest gateway.")


def utm_to_sim(xy: np.ndarray, meta: dict) -> np.ndarray:
    t = meta['transform']
    return np.column_stack([xy[:, 0] - t['origin_x'], t['origin_y'] - xy[:, 1]])


def whole_agents(expected: np.ndarray, method: str, rng) -> np.ndarray:
    total = int(round(expected.sum()))
    if total == 0:
        return np.zeros(expected.size, dtype=np.int64)
    if method == 'round':
        n = np.floor(expected).astype(np.int64)
        short = total - int(n.sum())
        if short > 0:
            n[np.argsort(-(expected - n))[:short]] += 1
        return n
    if method == 'sample':
        return rng.multinomial(total, expected / expected.sum())
    raise ValueError("OD_SAMPLING must be 'sample' or 'round'")


def spawn_nodes(region, city, railway) -> list:
    """Nodes of a zone an agent can start or end at: on the road network and able to walk to the anchor."""
    from graphing.mapping import shortest_edge_path
    anchor = region.anchor_node
    nodes = [node for node in region.nodes
             if node is not None and node.edges
             and (node is anchor or shortest_edge_path(node.id, anchor.id, city, railway))]
    return nodes or [anchor]


def load_od(bundle: Path, od_settings: dict, city=None, routes=None,
            data_dir: Path | None = None) -> tuple[np.ndarray, pd.DataFrame, list]:
    """OD matrix (trips/day), the bundle's zone table and the simulation route changes applied to it.
    With `routes`, the matrix follows the simulation's routes (agents/od_routes.py). Uses manila_od; if
    base_config.json is missing, falls back to the stored base matrix (base case only)."""
    if (bundle / 'base_config.json').exists():
        from agents.od_routes import SimRouteODModel, sim_route_changes
        model = SimRouteODModel.load(str(bundle))
        settings = dict(od_settings)
        changes = []
        if routes is not None:
            if model.has_routes:
                changes = sim_route_changes(model, city, routes, data_dir)
                model.sim_changes = changes
                if changes:
                    settings.setdefault('routes', {})
            else:
                LOGGER.warning("The OD bundle has no routes.csv: the OD matrix ignores route changes.")
        result = model.run(settings) if settings else model.base()
        return np.array(result.od, dtype=float), model.zones, [c.describe(model.names) for c in changes]
    if od_settings:
        raise FileNotFoundError(f"'{bundle / 'base_config.json'}' is missing; it is needed for 'od_settings' scenarios.")
    LOGGER.warning(f"'{bundle / 'base_config.json'}' missing: using od_matrix_base_reference.npy (base matrix only).")
    return (np.load(bundle / 'od_matrix_base_reference.npy').astype(float),
            pd.read_csv(bundle / 'zones.csv'), [])


# --------------------------------------------------------------------------- main entry
def hotspot_attraction(city, case: dict) -> tuple[dict, float]:
    """({barangay name: multiplier} for the hotspot zones, the multiplier used)."""
    factor = float(case.get('hotspot_attraction', config.get('HOTSPOT_ATTRACTION', 0.5)))
    if factor < 0:
        raise ValueError('HOTSPOT_ATTRACTION / hotspot_attraction must be >= 0')
    names = sorted(r.name for r in getattr(city, 'zones', {}).values() if getattr(r, 'is_hotspot', False))
    return ({name: factor for name in names} if factor != 1.0 else {}), factor


def schedule_od_agents(city, railway, case: dict, data_dir: Path, start_time: int,
                       results_dir: Path | None = None, routes: list | None = None) -> dict:
    """Builds the trip list, emits one AGENT_SPAWN event per agent and returns a summary dict.
    routes: the simulation's routes; when given, the OD matrix follows their changes from the base data."""
    bundle = Path(config.get('OD_BUNDLE_DIR', 'od_bundle'))
    od_settings = json.loads(json.dumps(case.get('od_settings') or {}))     # copy: the case stays untouched
    hot, hot_factor = hotspot_attraction(city, case)
    if hot:
        attraction = dict(od_settings.get('attraction', {}))
        for name, factor in hot.items():
            attraction[name] = attraction.get(name, 1.0) * factor
        od_settings['attraction'] = attraction
        LOGGER.info(f"Hotspots lower OD attraction x{hot_factor}: {', '.join(hot)}")
    od, zones, route_changes = load_od(bundle, od_settings, city, routes, data_dir)
    if route_changes:
        LOGGER.info(f"OD matrix follows {len(route_changes)} changed route(s): "
                    + "; ".join(f"{c['route_id']} -{len(c['zones_removed'])}/+{len(c['zones_added'])} zones" for c in route_changes))
    names = zones['zone'].astype(str).to_numpy()
    index_of = {name: i for i, name in enumerate(names)}

    # ---- study zones
    roles = set(config.get('OD_TRIP_END_ROLES', ['od']))
    regions = list(city.zones.values())
    missing = [r.name for r in regions if r.name not in index_of]
    if missing:
        LOGGER.warning(f"{len(missing)} study barangay(s) not found in the OD bundle (check names): {missing}")

    # ---- case od_scaling: multiply trips touching a barangay
    for region in regions:
        scale = getattr(region, 'od_scale', 1.0)
        if scale != 1.0 and region.name in index_of:
            i = index_of[region.name]
            od[i, :] *= scale
            od[:, i] *= scale

    # ---- map every bundle zone to a node (or drop it)
    nodes: list = []
    node_pos: dict = {}
    def node_index(node) -> int:
        if node.id not in node_pos:
            node_pos[node.id] = len(nodes)
            nodes.append(node)
        return node_pos[node.id]

    n = len(names)
    node_of = np.full(n, -1)
    is_end = np.zeros(n, bool)
    is_ext = np.zeros(n, bool)
    study_names = set()
    for region in regions:
        study_names.add(region.name)
        if region.name not in index_of:
            continue
        i = index_of[region.name]
        anchor = region.anchor_node
        if region.role in roles and anchor is not None and anchor.edges:
            node_of[i] = node_index(anchor)
            is_end[i] = True

    use_external = bool(config.get('OD_EXTERNAL_TRIPS', True))
    gateways = list(getattr(city, 'gateway_nodes', []) or [])
    if use_external and gateways:
        with open(data_dir / 'base' / 'meta.json', encoding='utf-8') as f:
            meta = json.load(f)
        outside = np.array([name not in study_names for name in names])
        pts = utm_to_sim(zone_points(zones)[outside], meta)
        gpos = np.array([getattr(g, 'precise_pos', g.pos) for g in gateways], float)
        nearest = np.argmin(((pts[:, None, :] - gpos[None, :, :]) ** 2).sum(axis=2), axis=1)
        gate_idx = np.array([node_index(g) for g in gateways])
        node_of[outside] = gate_idx[nearest]
        is_ext[outside] = True
    elif use_external:
        LOGGER.warning('OD_EXTERNAL_TRIPS is on but the map has no gateways; only internal trips are used.')

    keep = (is_end[:, None] & (is_end | is_ext)[None, :]) | (is_ext[:, None] & is_end[None, :])
    oi, di = np.nonzero(keep & (od > 0))
    on, dn = node_of[oi], node_of[di]
    spawn_at = config.get('OD_SPAWN_AT', 'zone_nodes')
    intrazonal = bool(config.get('OD_INTRAZONAL', False)) and spawn_at == 'zone_nodes'
    valid = (on != dn) | ((oi == di) & is_end[oi] & intrazonal)
    oi, di, on, dn = oi[valid], di[valid], on[valid], dn[valid]
    trips = od[oi, di]
    kind = np.where(is_ext[oi], 'inbound', np.where(is_ext[di], 'outbound', 'internal'))

    # aggregate zone pairs that share the same node pair (many outside zones use one gateway)
    frame = pd.DataFrame({'on': on, 'dn': dn, 'trips': trips, 'kind': kind,
                          'oz': np.where(is_ext[oi], 'outside', names[oi]),
                          'dz': np.where(is_ext[di], 'outside', names[di])})
    pairs = frame.groupby(['on', 'dn', 'oz', 'dz', 'kind'], as_index=False)['trips'].sum()
    daily_trips = float(pairs['trips'].sum())

    # ---- time window
    profile = np.asarray(config.get('OD_HOUR_PROFILE') or [1.0] * 24, float)
    if profile.size != 24 or profile.sum() <= 0:
        raise ValueError('OD_HOUR_PROFILE must have 24 non-negative weights (hour 0 first).')
    start_hour = start_time / 3600
    duration = int(config.get('OD_DURATION_HOURS', 24))
    hours = np.floor(start_hour).astype(int) + np.arange(duration)
    weights = profile[hours % 24]
    window_share = float(weights.sum() / profile.sum())

    # ---- whole agents
    per_agent = float(config.get('OD_TRIPS_PER_AGENT', 1))
    rng = np.random.default_rng(config.get('OD_SEED', 42))
    counts = whole_agents(pairs['trips'].to_numpy() * window_share / per_agent,
                          config.get('OD_SAMPLING', 'sample'), rng)
    rows = np.repeat(np.arange(len(pairs)), counts)
    hour_pick = rng.choice(hours, size=rows.size, p=weights / weights.sum())
    depart = (hour_pick * 3600 + rng.uniform(0, 3600, rows.size)).astype(np.int64)
    depart = np.maximum(depart, start_time)
    order = np.argsort(depart, kind='stable')
    rows, depart = rows[order], depart[order]

    schedule = pairs.iloc[rows].reset_index(drop=True)
    schedule.insert(0, 'depart_s', depart)
    # pick each agent's own node inside its zone, every node equally likely
    candidates = {}
    if spawn_at == 'zone_nodes':
        for region in regions:
            anchor = region.anchor_node
            if anchor is not None and anchor.id in node_pos and region.role in roles:
                candidates[node_pos[anchor.id]] = spawn_nodes(region, city, railway)
    elif spawn_at != 'anchor':
        raise ValueError("OD_SPAWN_AT must be 'zone_nodes' or 'anchor'")

    def pick(anchor_index: int):
        options = candidates.get(anchor_index)
        return options[rng.integers(len(options))] if options else nodes[anchor_index]

    origin_nodes, dest_nodes = [], []
    for r in schedule.itertuples(index=False):
        origin, destination = pick(r.on), pick(r.dn)
        if r.on == r.dn:                      # intrazonal: make sure the two ends differ
            for _ in range(10):
                if destination is not origin:
                    break
                destination = pick(r.dn)
        origin_nodes.append(origin.id[1])
        dest_nodes.append(destination.id[1])
        spec = TripSpec(origin, destination, r.oz, r.dz, r.kind, int(r.depart_s))
        manager.emit(int(r.depart_s), manager.Event(manager.AGENT_SPAWN, spec))

    summary = {
        'daily_trips_in_scope': round(daily_trips),
        'window_share': round(window_share, 4),
        'trips_per_agent': per_agent,
        'agents_scheduled': int(rows.size),
        'by_kind': schedule['kind'].value_counts().to_dict(),
        'spawn_at': spawn_at,
        'od_settings': od_settings,
        'route_changes': route_changes,
        'hotspots': sorted(r.name for r in regions if getattr(r, 'is_hotspot', False)),
        'hotspot_attraction': hot_factor,
        'agents_by_dest_zone': schedule['dz'].value_counts().to_dict(),
        'agents_by_origin_zone': schedule['oz'].value_counts().to_dict(),
    }
    LOGGER.info(f"OD demand: {summary['daily_trips_in_scope']:,} trips/day in scope, "
                f"{window_share:.1%} in window -> {rows.size:,} agents scheduled {summary['by_kind']}")

    if results_dir is not None:
        out = schedule.assign(origin_node=origin_nodes, dest_node=dest_nodes)
        out.rename(columns={'oz': 'origin_zone', 'dz': 'dest_zone'})[
            ['depart_s', 'origin_zone', 'dest_zone', 'origin_node', 'dest_node', 'kind']
        ].to_csv(results_dir / 'od_agents_scheduled.csv', index=False)
        with open(results_dir / 'od_demand_summary.json', 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)
    return summary
