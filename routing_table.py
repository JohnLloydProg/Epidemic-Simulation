"""
Routing cache: the walk/ride plan (list of Checkpoints) for every pair of trip-end nodes.

Paths are computed with one search per ORIGIN (graphing.mapping.shortest_paths_from), which gives exactly the
same paths as one search per pair but is roughly 100x faster. Origins are spread over a process pool.

    build_routing_cache(...)    at start-up / when a case is opened: load the cache file for this case if it
                                is current, otherwise compute it and save it
    rebuild_routing_cache(sim)  after a route edit: recompute everything in memory with the edited routes
    save_routing_cache(...)     write the in-memory cache as the cache file of a case (done when the case is
                                saved, so opening it later does not recompute anything)

Cache file (pickle): {'format': 5, 'tricycles': settings, 'routes': {route key: (spawn id, edge ids)},
'pairs': {(origin id, destination id): [checkpoint dicts]}}. A route key is "<route_id>#<direction>"; the
stored route paths are compared with the loaded routes, so a cache never refers to a route that has changed.
Format 4 files (routes by list position) are still read.

Config keys:
    "ROUTING_PROCESSES": null    number of worker processes (null = all CPU cores, 1 = no pool)
"""
import itertools
import logging
import multiprocessing
import os
import pickle
import time

import configuration as config
from graphing.core import Node
from graphing.graph import Graph, RegionGraph
from graphing.data_loader import load_graph_from_data
from graphing.mapping import shortest_paths_from
from transport.transportation import Route, set_route_group_path
from transport.checkpoint import generate_checkpoints, Checkpoint

CACHE_FILE_NAME = 'routing_table.pkl'
CACHE_FORMAT = 5            # 5 = routes by "<route_id>#<direction>" + route paths; tricycles as "tricycle:<barangay>"
READABLE_FORMATS = (4, 5)   # 4 = routes by position in the route list
LOGGER = logging.getLogger('RoutingTable')


def get_cache_file() -> str:
    """One cache per case when using sim_data/, otherwise the old routing_table.pkl."""
    if (config.get('DATA_DIR')):
        from graphing.data_loader import cache_file
        return str(cache_file())
    return CACHE_FILE_NAME


# --------------------------------------------------------------------------- dehydrate / rehydrate
def route_keys(routes:list[Route]) -> dict[int, str]:
    """{id(route): "<route_id>#<n>"}, n = the route's direction within its route id (0 = forward). Unlike
    Route.id counters or list positions, this is the same in every process and after a case is re-opened."""
    keys, seen = {}, {}
    for i, route in enumerate(routes):
        route_id = getattr(route, 'route_id', None)
        if route_id is None:
            keys[id(route)] = f"#{i}"
            continue
        n = seen.get(route_id, 0)
        seen[route_id] = n + 1
        keys[id(route)] = f"{route_id}#{n}"
    return keys


def route_signatures(routes:list[Route]) -> dict[str, tuple]:
    """{route key: (spawn node id, (edge ids...))} — what a cache's ride legs depend on."""
    keys = route_keys(routes)
    return {keys[id(r)]: (r.spawn_node.id, tuple(e.id for e in r.path)) for r in routes}


def dehydrate_checkpoints(checkpoints:list[Checkpoint], route_index:dict) -> list[dict]:
    """Checkpoints as plain data (route_index = route_keys(routes))."""
    def route_key(route):
        if route is None:
            return None
        if getattr(route, 'mode', None) == 'tricycle':
            return f"tricycle:{route.zone}"
        return route_index[id(route)]
    return [{'mode': cp.mode,
             'start_node': cp.start_node.id if cp.start_node else None,
             'end_node': cp.end_node.id if cp.end_node else None,
             'route': route_key(cp.route)}
            for cp in checkpoints]


def dehydrate(raw_path:list, route_index:dict) -> list[dict]:
    return dehydrate_checkpoints(generate_checkpoints(raw_path), route_index)


def rehydrate_cache(dehydrated_cache:dict, city:RegionGraph, railway:Graph, routes:list[Route]) -> dict[tuple, list[Checkpoint]]:
    def node(node_id):
        if node_id is None:
            return None
        return city.get_node(node_id) if node_id[0] == 'city' else railway.get_node(node_id)

    from transport.tricycle import service_for

    keys = route_keys(routes)
    by_key = {keys[id(route)]: route for route in routes}

    def route(key):
        if key is None:
            return None
        if isinstance(key, str) and key.startswith('tricycle:'):
            return service_for(key[len('tricycle:'):])
        if isinstance(key, int):                # format 4: position in the route list
            return routes[key]
        return by_key[key]

    routing_cache = {}
    for key, pickled_checkpoints in dehydrated_cache.items():
        routing_cache[key] = [Checkpoint(mode=cp['mode'], start_node=node(cp['start_node']), end_node=node(cp['end_node']),
                                         route=route(cp['route']))
                              for cp in pickled_checkpoints]
    return routing_cache


def paths_from_origin(start:Node, targets:list[Node], routes:list[Route], route_index:dict) -> dict:
    found = shortest_paths_from(start, targets, routes)
    return {(start.id, target.id): dehydrate(found.get(target, []), route_index)
            for target in targets if target is not start}


# --------------------------------------------------------------------------- worker processes
worker_city:RegionGraph = None
worker_routes:list[Route] = None
worker_targets:list[Node] = None
worker_route_index:dict = None


def init_worker(target_ids:list, route_edits:dict, closed_ids=(), removed_ids=(), tricycle_off=()):
    """Each worker loads the map itself, then repeats the main process's road closures, removed routes and
    route paths, so its network (and its route list order) is the same as the main process's."""
    global worker_city, worker_routes, worker_targets, worker_route_index
    from transport.closures import apply_in_worker
    city, _, routes = load_graph_from_data()
    routes = apply_in_worker(city, routes, closed_ids, removed_ids, route_edits)
    from transport.tricycle import build_services
    from graphing.data_loader import data_dir
    build_services(city, data_dir(), tricycle_off)             # same services, same order as the main process
    worker_city, worker_routes = city, routes
    worker_targets = [city.get_node(i) for i in target_ids]
    worker_route_index = route_keys(routes)


def compute_origin(start_id) -> dict:
    return paths_from_origin(worker_city.get_node(start_id), worker_targets, worker_routes, worker_route_index)


# --------------------------------------------------------------------------- compute
def compute_cache(nodes:list[Node], routes:list[Route], route_edits:dict | None = None, on_progress=None,
                  closed_ids=(), removed_ids=(), tricycle_off=None) -> dict:
    """Plain-data cache for every ordered pair of `nodes`.
    route_edits: {route_id: (spawn_node_id, [edge_ids][, return_trip])} already applied to `routes` (return_trip =
    (spawn_node_id, [edge_ids]) of the second direction, kept exactly; see closures.route_state); workers re-apply them,
    after closing `closed_ids` and dropping the routes in `removed_ids` (see transport/closures.py).
    on_progress(done, total) is called after each origin (e.g. to keep a window responsive)."""
    if tricycle_off is None:
        from transport.tricycle import disabled_psgc
        tricycle_off = disabled_psgc()
    nodes = list(dict.fromkeys(node for node in nodes if node.edges))
    ids = [node.id for node in nodes]
    total = len(ids)
    processes = config.get('ROUTING_PROCESSES') or os.cpu_count() or 1
    LOGGER.info(f"Computing routes from {total} origins to {total} destinations "
                f"({total * (total - 1):,} pairs, {processes} process(es))...")
    started = time.time()
    dehydrated = {}

    if processes <= 1:
        route_index = route_keys(routes)
        for done, start in enumerate(nodes, 1):
            dehydrated.update(paths_from_origin(start, nodes, routes, route_index))
            if on_progress:
                on_progress(done, total)
    else:
        # 'spawn' = fresh worker processes on every OS (forking a process that has pygame running can hang)
        context = multiprocessing.get_context('spawn')
        with context.Pool(processes, initializer=init_worker, initargs=(ids, route_edits or {}, list(closed_ids), list(removed_ids), list(tricycle_off))) as pool:
            for done, result in enumerate(pool.imap_unordered(compute_origin, ids, chunksize=2), 1):
                dehydrated.update(result)
                if on_progress:
                    on_progress(done, total)

    missing = sum(1 for cps in dehydrated.values() if not cps)
    LOGGER.info(f"Routes computed in {time.time() - started:.1f} s" + (f" ({missing} pairs have no path)" if missing else ''))
    return dehydrated


def cache_is_current(stored, routes:list[Route]) -> bool:
    """True if a loaded cache file matches the tricycle settings and (format 5) the routes as loaded now."""
    from transport.tricycle import settings_signature, disabled_psgc
    if not isinstance(stored, dict) or stored.get('format') not in READABLE_FORMATS:
        return False
    if stored.get('tricycles') != settings_signature(disabled_psgc()):
        return False
    if stored['format'] >= 5 and stored.get('routes') != route_signatures(routes):
        return False
    return True


def build_routing_cache(nodes:list[Node], city:RegionGraph, railway:Graph, routes:list[Route],
                        on_progress=None) -> dict[tuple, list[Checkpoint]]:
    """At start-up: load this case's cache file if it is current, otherwise compute it and save it."""
    cache_path = get_cache_file()
    if os.path.exists(cache_path):
        LOGGER.info(f"Found existing {cache_path}! Loading from disk...")
        try:
            with open(cache_path, 'rb') as f:
                stored = pickle.load(f)
        except Exception as e:                  # damaged / half-written file
            LOGGER.warning(f"Could not read {cache_path} ({e}); rebuilding it.")
            stored = None
        if cache_is_current(stored, routes):
            return rehydrate_cache(stored['pairs'], city, railway, routes)
        LOGGER.info("Cache file is older or was built with other routes/tricycle settings; rebuilding it.")

    dehydrated = compute_cache(nodes, routes, on_progress=on_progress)
    write_cache_file(cache_path, dehydrated, routes)
    return rehydrate_cache(dehydrated, city, railway, routes)


def write_cache_file(cache_path, dehydrated:dict, routes:list[Route]):
    from transport.tricycle import settings_signature, disabled_psgc
    LOGGER.info(f"Saving routing cache to {cache_path}...")
    tmp = f"{cache_path}.tmp"
    with open(tmp, 'wb') as f:                  # write then rename: never leaves a half-written cache
        pickle.dump({'format': CACHE_FORMAT, 'tricycles': settings_signature(disabled_psgc()),
                     'routes': route_signatures(routes), 'pairs': dehydrated}, f)
    os.replace(tmp, cache_path)


def save_routing_cache(cache_path, routing_table:dict, routes:list[Route]) -> int:
    """Write an in-memory routing cache (e.g. after route edits / closures) as a case's cache file.
    The caller makes sure the cache matches `routes` and the current tricycle settings. Returns the pair count."""
    index = route_keys(routes)
    dehydrated = {key: dehydrate_checkpoints(checkpoints, index) for key, checkpoints in routing_table.items()}
    write_cache_file(cache_path, dehydrated, routes)
    return len(dehydrated)


def rebuild_routing_cache(sim, on_progress=None) -> dict[tuple, list[Checkpoint]]:
    """After a route edit: recompute the whole cache with the routes as they are now (kept in memory only)."""
    from graphing.mapping import _route_index
    _route_index.clear()                    # stop -> routes lookup, rebuilt from the edited routes
    if hasattr(sim, 'route_loaded'):         # closures.py keeps the full picture: edits, detours, removals
        from transport.closures import route_state
        edits, closed_ids, removed_ids = route_state(sim)
    else:
        edits = {route_id: (spawn.id, [edge.id for edge in path])
                 for route_id, (spawn, path) in getattr(sim, 'route_edits', {}).items()}
        closed_ids, removed_ids = [], []
    dehydrated = compute_cache(sim.trip_end_nodes(), sim.routes, edits, on_progress, closed_ids, removed_ids)
    return rehydrate_cache(dehydrated, sim.graph, sim.railway_graph, sim.routes)
