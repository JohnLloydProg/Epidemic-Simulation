"""
Routing cache: the walk/ride plan (list of Checkpoints) for every pair of trip-end nodes.

Paths are computed with one search per ORIGIN (graphing.mapping.shortest_paths_from), which gives exactly the
same paths as one search per pair but is roughly 100x faster. Origins are spread over a process pool.

    build_routing_cache(...)    at start-up: load the cache file for this case, or compute and save it
    rebuild_routing_cache(sim)  after a route edit: recompute everything in memory with the edited routes
                                (not saved; save the edits as a case file to get a cache file for them)

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
CACHE_FORMAT = 2            # 2 = routes stored by their position in the route list
LOGGER = logging.getLogger('RoutingTable')


def get_cache_file() -> str:
    """One cache per case when using sim_data/, otherwise the old routing_table.pkl."""
    if (config.get('DATA_DIR')):
        from graphing.data_loader import cache_file
        return str(cache_file())
    return CACHE_FILE_NAME


# --------------------------------------------------------------------------- dehydrate / rehydrate
def dehydrate(raw_path:list, route_index:dict) -> list[dict]:
    """Checkpoints as plain data. Routes are stored by their position in the route list, which is the
    same in every process that loads the same data (Route.id counters are not)."""
    return [{'mode': cp.mode,
             'start_node': cp.start_node.id if cp.start_node else None,
             'end_node': cp.end_node.id if cp.end_node else None,
             'route': route_index[id(cp.route)] if cp.route else None}
            for cp in generate_checkpoints(raw_path)]


def rehydrate_cache(dehydrated_cache:dict, city:RegionGraph, railway:Graph, routes:list[Route]) -> dict[tuple, list[Checkpoint]]:
    def node(node_id):
        if node_id is None:
            return None
        return city.get_node(node_id) if node_id[0] == 'city' else railway.get_node(node_id)

    routing_cache = {}
    for key, pickled_checkpoints in dehydrated_cache.items():
        routing_cache[key] = [Checkpoint(mode=cp['mode'], start_node=node(cp['start_node']), end_node=node(cp['end_node']),
                                         route=routes[cp['route']] if cp['route'] is not None else None)
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


def init_worker(target_ids:list, route_edits:dict, closed_ids=(), removed_ids=()):
    """Each worker loads the map itself, then repeats the main process's road closures, removed routes and
    route paths, so its network (and its route list order) is the same as the main process's."""
    global worker_city, worker_routes, worker_targets, worker_route_index
    from transport.closures import apply_in_worker
    city, _, routes = load_graph_from_data()
    routes = apply_in_worker(city, routes, closed_ids, removed_ids, route_edits)
    worker_city, worker_routes = city, routes
    worker_targets = [city.get_node(i) for i in target_ids]
    worker_route_index = {id(route): i for i, route in enumerate(routes)}


def compute_origin(start_id) -> dict:
    return paths_from_origin(worker_city.get_node(start_id), worker_targets, worker_routes, worker_route_index)


# --------------------------------------------------------------------------- compute
def compute_cache(nodes:list[Node], routes:list[Route], route_edits:dict | None = None, on_progress=None,
                  closed_ids=(), removed_ids=()) -> dict:
    """Plain-data cache for every ordered pair of `nodes`.
    route_edits: {route_id: (spawn_node_id, [edge_ids])} already applied to `routes`; workers re-apply them,
    after closing `closed_ids` and dropping the routes in `removed_ids` (see transport/closures.py).
    on_progress(done, total) is called after each origin (e.g. to keep a window responsive)."""
    nodes = list(dict.fromkeys(node for node in nodes if node.edges))
    ids = [node.id for node in nodes]
    total = len(ids)
    processes = config.get('ROUTING_PROCESSES') or os.cpu_count() or 1
    LOGGER.info(f"Computing routes from {total} origins to {total} destinations "
                f"({total * (total - 1):,} pairs, {processes} process(es))...")
    started = time.time()
    dehydrated = {}

    if processes <= 1:
        route_index = {id(route): i for i, route in enumerate(routes)}
        for done, start in enumerate(nodes, 1):
            dehydrated.update(paths_from_origin(start, nodes, routes, route_index))
            if on_progress:
                on_progress(done, total)
    else:
        # 'spawn' = fresh worker processes on every OS (forking a process that has pygame running can hang)
        context = multiprocessing.get_context('spawn')
        with context.Pool(processes, initializer=init_worker, initargs=(ids, route_edits or {}, list(closed_ids), list(removed_ids))) as pool:
            for done, result in enumerate(pool.imap_unordered(compute_origin, ids, chunksize=2), 1):
                dehydrated.update(result)
                if on_progress:
                    on_progress(done, total)

    missing = sum(1 for cps in dehydrated.values() if not cps)
    LOGGER.info(f"Routes computed in {time.time() - started:.1f} s" + (f" ({missing} pairs have no path)" if missing else ''))
    return dehydrated


def build_routing_cache(nodes:list[Node], city:RegionGraph, railway:Graph, routes:list[Route]) -> dict[tuple, list[Checkpoint]]:
    """At start-up: load this case's cache file if it is current, otherwise compute it and save it."""
    cache_path = get_cache_file()
    if os.path.exists(cache_path):
        LOGGER.info(f"Found existing {cache_path}! Loading from disk...")
        with open(cache_path, 'rb') as f:
            stored = pickle.load(f)
        if isinstance(stored, dict) and stored.get('format') == CACHE_FORMAT:
            return rehydrate_cache(stored['pairs'], city, railway, routes)
        LOGGER.info("Cache file is in an older format; rebuilding it.")

    dehydrated = compute_cache(nodes, routes)
    LOGGER.info(f"Saving routing cache to {cache_path}...")
    with open(cache_path, 'wb') as f:
        pickle.dump({'format': CACHE_FORMAT, 'pairs': dehydrated}, f)
    return rehydrate_cache(dehydrated, city, railway, routes)


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
