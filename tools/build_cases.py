"""
build_cases.py — create the intervention cases from a setup file instead of editing them by hand in the simulation.

    python tools/build_cases.py interventions.json

Run it from the repository folder with the same CONFIG_FILE_NAME (.env) as the program (or pass --config).
It loads each scenario baseline case, applies one intervention with the same functions the in-sim editors use
(E route edit, X road closure, H trip limits), and saves the result like Ctrl+S does. Nothing is simulated here;
the routing cache is computed the first time a case is opened or run.

SETUP FILE — the same rule is applied to every scenario, so the scenarios stay comparable:
{
  "scenarios": ["scenario1_HighOrigin_baseline", "scenario2_HighDestination_baseline",
                "scenario3_HighThrough_baseline"],
  "interventions": {
    "rerouting":   {"reroute_share": 0.5},
    "roadclosure": {"close_share": 0.5},
    "triplimit":   {"trip_factor": 0.5}
  }
}
Output: one case per scenario and intervention, named after the scenario with "_baseline" replaced by
"_intervention_<name>", e.g. scenario1_HighOrigin_intervention_rerouting. Its runs are logged in
sim_data/results/<that name>/logs/.

THE RULES (the hotspots are the parent case's hotspot barangays)
  reroute_share  Of the jeepney/bus routes that pass through a hotspot AND can be rerouted, this share is rerouted
                 around the hotspots, starting with the routes that drive the longest distance inside them. A route only avoids hotspots
                 it does not start or end in (it still has to reach its own terminal). Each stretch through a
                 hotspot is replaced by the shortest drivable road path outside the hotspots (one-way roads
                 respected); a stretch with no way around is kept, so a route counts as rerouted when at least one
                 of its hotspot stretches moved. Both directions of the route are re-derived. Routes where nothing
                 can move (no legal way around on one-way roads) are not reroutable and not counted.
  close_share    This share of the CLOSABLE road segments inside the hotspots is closed (closable = closing it alone
                 does not cut part of the network off, e.g. not a dead-end street), starting with the segments used
                 by the most transit routes (then the higher road class, then the longer segment). A segment that
                 would cut the network once others are closed is skipped, so every node stays reachable. Routes that
                 used a closed segment detour around it (transport/closures.py). As in the program, a closed road is
                 closed to everyone, walkers included.
  trip_factor    Trips to and from every hotspot are multiplied by this factor (case "od_scaling"; 0.5 = half).
The script prints, per case, what it changed (and when a share could not be reached, why).

Optional extra, hand-written cases can be added in the same file:
  "cases": [ {"name": "...", "parent": "<case>", "reroute": [{"route": "LTFRB_PUJ1677", "avoid": ["Barangay 669"]}],
              "close_roads": [{"road": "Taft Avenue", "in": ["Barangay 669"]}, 1234],
              "remove_routes": ["..."], "limit_trips": {"Barangay 649": 0.5}, "description": "..."} ]
  ("hotspots" can be used in place of a barangay list.)
"""
from __future__ import annotations
import argparse, json, math, os, sys
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROAD_RANK = {'trunk': 0, 'trunk_link': 1, 'primary': 2, 'primary_link': 3, 'secondary': 4, 'secondary_link': 5,
             'tertiary': 6, 'tertiary_link': 7, 'unclassified': 8, 'residential': 9}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('setup', help='setup .json file (see above)')
    ap.add_argument('--config', default=None, help='simulation config .json (default: CONFIG_FILE_NAME / .env)')
    ap.add_argument('--only', nargs='*', help='build only these case names')
    args = ap.parse_args()

    setup_path = Path(args.setup).resolve()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
    except ImportError:
        pass
    if args.config:
        os.environ['CONFIG_FILE_NAME'] = args.config
    if not os.environ.get('CONFIG_FILE_NAME'):
        sys.exit("No config: set CONFIG_FILE_NAME (as for the program) or pass --config.")
    import configuration as config
    config.init(os.environ['CONFIG_FILE_NAME'])

    setup = json.load(open(setup_path, encoding='utf-8'))
    specs = []
    for parent in setup.get('scenarios', []):
        stem = parent[:-len('_baseline')] if parent.endswith('_baseline') else parent
        for name, rule in setup.get('interventions', {}).items():
            specs.append({'name': f'{stem}_intervention_{name}', 'parent': parent, 'rule': rule})
    specs += setup.get('cases', [])
    if args.only:
        specs = [s for s in specs if s['name'] in args.only]

    for spec in specs:
        print(f"\n== {spec['name']}  (from {spec.get('parent', '00_baseline')})")
        path, notes = build_case(spec)
        for note in notes:
            print('   ' + note)
        print(f'   saved {path}')


# ============================================================================================== one case
class _Session:
    """The parts of the simulation object the editors' functions use."""
    start_time = 0
    started = False

    def __init__(self, graph, railway, routes):
        self.graph, self.railway_graph, self.routes = graph, railway, routes
        self.route_edits, self.route_originals, self.zone_facilities = {}, {}, {}


def build_case(spec: dict) -> tuple[str, list[str]]:
    from graphing.data_loader import set_case_file, load_case, load_graph_from_data, cases_dir
    from transport.closures import init_closures, update_routes
    from transport.route_editor import case_from_session
    import manager

    parent_name = spec.get('parent', '00_baseline')
    if not (cases_dir() / f'{parent_name}.json').exists():
        sys.exit(f"Parent case '{parent_name}' not found in {cases_dir()} (run tools/make_hotspot_cases.py first?)")
    set_case_file(f'{parent_name}.json')
    parent = load_case()
    manager._events.clear()
    city, railway, routes = load_graph_from_data()
    sim = _Session(city, railway, routes)
    sim.zone_facilities = dict((parent.get('od_settings') or {}).get('zone_facilities', {}))
    init_closures(sim)
    notes = []

    zones_by_name = {r.name: r for r in city.zones.values()}
    hotspots = [r.name for r in city.zones.values() if r.is_hotspot]
    zone_of_node = {n.id: r.name for r in city.zones.values() for n in r.nodes if n is not None}

    def zone_list(value) -> list:
        names = []
        for v in ([value] if isinstance(value, str) else value):
            names += hotspots if v == 'hotspots' else [v]
        bad = [n for n in names if n not in zones_by_name]
        if bad:
            sys.exit(f"{spec['name']}: unknown barangay(s) {bad}")
        if not names:
            sys.exit(f"{spec['name']}: 'hotspots' used but {parent_name} has no hotspots")
        return list(dict.fromkeys(names))

    def zone_nodes(names) -> set:
        return {n.id for name in names for n in zones_by_name[name].nodes if n is not None}

    rule = spec.get('rule', {})

    # ---------------------------------------------------------------- rerouting
    if 'reroute_share' in rule:
        hot_nodes = zone_nodes(zone_list('hotspots'))
        crossing = []                                  # (metres inside avoidable hotspots, route_id)
        for rid, (spawn, path) in sim.route_loaded.items():
            avoid = hot_nodes - _terminal_zone_nodes(spawn, path, zone_of_node, zones_by_name)
            inside = sum(e.distance for e in path if any(n.id in avoid for n in e.nodes))
            if inside > 0:
                crossing.append((inside, rid))
        crossing.sort(key=lambda t: (-t[0], t[1]))
        reroutable = [rid for _, rid in crossing
                      if _plan_reroute(sim, rid, hot_nodes, zone_of_node, zones_by_name) is not None]
        target = math.ceil(rule['reroute_share'] * len(reroutable) - 1e-9)
        done = [rid for rid in reroutable[:target]
                if _reroute(sim, rid, hot_nodes, zone_of_node, zones_by_name, notes)]
        notes.insert(0, f"rerouting: {len(crossing)} route(s) pass through hotspots, {len(reroutable)} can be "
                        f"rerouted (the others have no legal way around on one-way roads); target {target} "
                        f"({rule['reroute_share']:.0%} of reroutable), rerouted {len(done)}")
    for item in spec.get('reroute', []):               # hand-written
        avoid = zone_nodes(zone_list(item['avoid']))
        targets = ([rid for rid, (s, p) in sim.route_loaded.items() if any(n.id in avoid for e in p for n in e.nodes)]
                   if item['route'] == 'all' else [item['route']])
        for rid in targets:
            if rid not in sim.route_loaded:
                sys.exit(f"{spec['name']}: route '{rid}' not found (or not a road route)")
            if not _reroute(sim, rid, avoid, zone_of_node, zones_by_name, notes):
                notes.append(f"reroute {rid}: no way around — left unchanged")

    # ---------------------------------------------------------------- road closures
    to_close = []
    if 'close_share' in rule:
        hot_nodes = zone_nodes(zone_list('hotspots'))
        use = Counter()
        for rid, (spawn, path) in sim.route_loaded.items():
            for eid in {e.id for e in path}:
                use[eid] += 1
        inside = [e for e in city.edges.values() if e.id[0] == 'city' and any(n.id in hot_nodes for n in e.nodes)]
        candidates = [e for e in inside if not _cuts_network(city, e)]    # closable on their own
        candidates.sort(key=lambda e: (-use[e.id], ROAD_RANK.get(getattr(e, 'highway', None), 99), -e.distance, e.id))
        target = math.ceil(rule['close_share'] * len(candidates) - 1e-9)
        kept_open = 0
        for edge in candidates:
            if len(to_close) >= target:
                break
            if _cuts_network(city, edge):
                kept_open += 1
                continue
            _detach(edge)                              # tentatively, so later checks see it closed
            to_close.append(edge)
        for edge in to_close:
            _attach(edge)
        _restore_order(sim)
        notes.append(f"road closure: {len(inside)} segment(s) inside hotspots, {len(candidates)} closable without "
                     f"cutting the network; target {target} ({rule['close_share']:.0%} of closable), "
                     f"closed {len(to_close)}"
                     + (f", {kept_open} skipped (would cut the network once others were closed)" if kept_open else ""))
    for item in spec.get('close_roads', []):           # hand-written
        if isinstance(item, int):
            ids = [('city', item)]
        else:
            nodes = zone_nodes(zone_list(item['in'])) if 'in' in item else None
            ids = [e.id for e in city.edges.values() if e.id[0] == 'city' and getattr(e, 'road_name', None) == item['road']
                   and (nodes is None or any(n.id in nodes for n in e.nodes))]
            if not ids:
                sys.exit(f"{spec['name']}: no segments of '{item['road']}' found")
        for eid in ids:
            if eid not in city.edges:
                sys.exit(f"{spec['name']}: edge {eid[1]} not found")
            to_close.append(city.edges[eid])
    if to_close:
        from transport.closures import close_edge
        for edge in to_close:
            close_edge(sim, edge)
        result = update_routes(sim)
        notes.append(f"   routes detoured around closures: {len(result['detoured'])}, "
                     f"removed (no way around): {len(result['removed'])}"
                     + (f" {result['removed']}" if result['removed'] else ""))

    # ---------------------------------------------------------------- trip limits
    limits = dict(spec.get('limit_trips', {}))
    if 'trip_factor' in rule:
        limits['hotspots'] = rule['trip_factor']
    for name, factor in limits.items():
        if not 0 <= float(factor) <= 1:
            sys.exit(f"{spec['name']}: trip limit must be between 0 and 1")
        for z in zone_list(name):
            zones_by_name[z].od_scale = float(factor)
    if limits:
        notes.append("trip limit: " + ", ".join(f"{r.name} {r.od_scale:.0%}" for r in city.zones.values()
                                                if r.od_scale != 1.0))

    # ---------------------------------------------------------------- save like Ctrl+S
    case, _ = case_from_session(sim)
    case['case_id'] = spec['name']
    case['tricycle_disabled'] = parent.get('tricycle_disabled', [])       # tricycles are not loaded here
    removed = list(spec.get('remove_routes', []))
    unknown = [r for r in removed if r not in sim.route_loaded]
    if unknown:
        sys.exit(f"{spec['name']}: route(s) not found: {unknown}")
    case['disabled_routes'] = list(dict.fromkeys(case['disabled_routes'] + removed))
    for rid in removed:
        case['transit_overrides'].pop(rid, None)
    if removed:
        notes.append(f"removed route(s): {', '.join(removed)}")
    case['description'] = spec.get('description') or _describe(spec, parent_name, hotspots)
    case['built_from'] = {'parent': parent_name, 'setup': {k: v for k, v in spec.items() if k not in ('name',)}}

    path = cases_dir() / f"{spec['name']}.json"
    tmp = path.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(case, f, indent=1)
    os.replace(tmp, path)
    return str(path), notes


def _describe(spec, parent_name, hotspots) -> str:
    rule = spec.get('rule', {})
    parts = []
    if 'reroute_share' in rule:
        parts.append(f"{rule['reroute_share']:.0%} of the routes through hotspots rerouted around them")
    if 'close_share' in rule:
        parts.append(f"{rule['close_share']:.0%} of the road segments in hotspots closed")
    if 'trip_factor' in rule:
        parts.append(f"trips to and from hotspots limited to {rule['trip_factor']:.0%}")
    return f"{spec['name']}: {parent_name} with " + ('; '.join(parts) or 'hand-written changes') \
        + f" (hotspots: {', '.join(hotspots)})"


# ============================================================================================== rerouting
def _terminal_zone_nodes(spawn, path, zone_of_node, zones_by_name) -> set:
    end = spawn
    for e in path:
        end = e.get_adjacent_node(end)
    zones = {zone_of_node.get(spawn.id), zone_of_node.get(end.id)} - {None}
    return {n.id for z in zones for n in zones_by_name[z].nodes if n is not None}


def _plan_reroute(sim, rid, avoid_nodes, zone_of_node, zones_by_name):
    """(spawn, old path, new path, blocked edge ids) for one route avoiding avoid_nodes (minus the zones of its own
    terminals), or None when none of its hotspot stretches can move. Changes nothing."""
    from transport.closures import _clear_search_caches
    spawn, path = sim.route_edits.get(rid, sim.route_loaded[rid])
    avoid = avoid_nodes - _terminal_zone_nodes(spawn, path, zone_of_node, zones_by_name)
    blocked = {e.id: e for e in sim.graph.edges.values() if any(n.id in avoid for n in e.nodes)}
    if not any(e.id in blocked for e in path):
        return None
    for e in blocked.values():
        _detach(e)
    _clear_search_caches()
    try:
        new_path = _drive_detour(spawn, path, set(blocked), sim.graph)
    finally:
        for e in blocked.values():
            _attach(e)
        _restore_order(sim)
        _clear_search_caches()
    if new_path is None:
        return None
    return spawn, path, new_path, set(blocked)


def _reroute(sim, rid, avoid_nodes, zone_of_node, zones_by_name, notes) -> bool:
    """Apply _plan_reroute. True if the route changed."""
    from transport.transportation import set_route_group_path
    plan = _plan_reroute(sim, rid, avoid_nodes, zone_of_node, zones_by_name)
    if plan is None:
        return False
    spawn, path, new_path, blocked = plan
    set_route_group_path(sim.all_routes, rid, spawn, new_path)
    sim.route_edits[rid] = (spawn, list(new_path))
    inside = lambda p: sum(e.distance for e in p if e.id in blocked) / 1000
    km = lambda p: sum(e.distance for e in p) / 1000
    notes.append(f"   {rid}: {km(path):.2f} km -> {km(new_path):.2f} km, "
                 f"inside hotspots {inside(path):.2f} km -> {inside(new_path):.2f} km")
    return True


def _drive_detour(spawn, path, blocked: set, city):
    """Every run of blocked edges replaced by the shortest drivable road path between its two ends. A run with
    no way around keeps its original edges (the route still passes there); None only if nothing could change."""
    from graphing.mapping import shortest_drive_path
    out, node, i, changed = [], spawn, 0, False
    while i < len(path):
        if path[i].id not in blocked:
            out.append(path[i]); node = path[i].get_adjacent_node(node); i += 1
            continue
        start, end, run_start = node, node, i
        while i < len(path) and path[i].id in blocked:
            end = path[i].get_adjacent_node(end); i += 1
        if start is end:
            continue
        around = shortest_drive_path(start.id, end.id, city)
        if around:
            out += list(around); changed = True
        else:
            out += path[run_start:i]                    # no way around this stretch: keep it
        node = end
    return out if changed else None


# ============================================================================================== network helpers
def _detach(edge):
    for n in edge.nodes:
        if edge in n.edges:
            n.edges.remove(edge)


def _attach(edge):
    for n in edge.nodes:
        if edge not in n.edges:
            n.edges.append(edge)


def _restore_order(sim):
    for n in sim.graph.nodes.values():                 # original edge order, so searches behave as in the program
        order = sim._edge_order.get(n.id)
        if order:
            by_id = {x.id: x for x in n.edges}
            n.edges[:] = [by_id[i] for i in order if i in by_id]


def _cuts_network(city, edge) -> bool:
    """True if, without this edge, its two ends are no longer connected by any road (walking ignores one-ways)."""
    a, b = edge.nodes
    _detach(edge)
    try:
        seen, todo = {a.id}, deque([a])
        while todo:
            node = todo.popleft()
            if node is b:
                return False
            for e in node.edges:
                if e.id[0] != 'city':
                    continue
                nxt = e.get_adjacent_node(node)
                if nxt.id not in seen:
                    seen.add(nxt.id); todo.append(nxt)
        return True
    finally:
        _attach(edge)


if __name__ == '__main__':
    main()
