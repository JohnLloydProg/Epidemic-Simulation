"""
closures.py — close and reopen roads before the simulation starts.

A closed road (city edge) is taken out of the network for everyone: walkers, private cars and transit.
Jeepney/bus routes that use it are re-routed around it: every run of closed edges in the route is replaced by
the shortest road path between the nodes on either side. A route with no way around it is removed.
Config "CLOSED_ROAD_ROUTES": "detour" (default) or "remove" (always remove routes that use a closed road,
like the data loader does for a case file's "closed_edges").

After changing closures call finish_network_changes(sim) (the road editor does this on Enter, when it is
closed, and the simulation does it before the first step) to rebuild the routing cache and the OD demand.

State kept on the simulation:
    sim.closed_edges     {edge_id: Edge}            roads closed in this session
    sim.all_routes       [Route]                    every route as loaded, in load order
    sim.route_loaded     {route_id: (spawn, path)}  each route's path as loaded from the case
    sim.removed_routes   {route_id}                 routes taken out because of closures
    sim.network_dirty    bool                       routing cache / OD not rebuilt since the last change
"""
from __future__ import annotations
import logging

import configuration as config
import manager
from graphing.core import Edge, Node
from transport.transportation import set_route_group_path

LOGGER = logging.getLogger('Closures')


def init_closures(sim):
    sim.closed_edges = {}
    sim.all_routes = list(sim.routes)
    sim.route_loaded = {}
    for route in sim.routes:
        route_id = getattr(route, 'route_id', None)
        if route.graph.layer == 'city' and route_id and route_id not in sim.route_loaded:
            sim.route_loaded[route_id] = (route.spawn_node, list(route.path))
    sim.removed_routes = set()
    sim.route_loaded_dirs = {}                  # every direction as loaded: {route_id: [(spawn, [edge ids])]}
    for route in sim.routes:
        route_id = getattr(route, 'route_id', None)
        if route.graph.layer == 'city' and route_id:
            sim.route_loaded_dirs.setdefault(route_id, []).append((route.spawn_node, [e.id for e in route.path]))
    sim._closure_touched = set()                # routes re-derived because of closures (re-check on reopen)
    sim.network_dirty = False
    sim._edge_order = {node.id: [edge.id for edge in node.edges] for node in sim.graph.nodes.values()}
    if not hasattr(sim, 'route_edits'):
        sim.route_edits = {}


def _clear_search_caches():
    from graphing.mapping import shortest_edge_path, shortest_drive_path, _route_index
    shortest_edge_path.cache_clear()
    shortest_drive_path.cache_clear()
    _route_index.clear()


def close_edge(sim, edge:Edge):
    if edge.id in sim.closed_edges:
        return
    for node in edge.nodes:
        if edge in node.edges:
            node.edges.remove(edge)
    sim.graph.edges.pop(edge.id, None)
    sim.closed_edges[edge.id] = edge


def reopen_edge(sim, edge_id):
    edge = sim.closed_edges.pop(edge_id, None)
    if edge is None:
        return
    sim.graph.edges[edge.id] = edge
    for node in edge.nodes:                    # back in the original order, so searches behave as before
        by_id = {e.id: e for e in node.edges}
        by_id[edge.id] = edge
        node.edges[:] = [by_id[i] for i in sim._edge_order.get(node.id, []) if i in by_id]


def detour(spawn:Node, path:list[Edge], closed:set, city, railway) -> list[Edge] | None:
    """Path with every run of closed edges replaced by the shortest open road path around it; None if impossible."""
    from graphing.mapping import shortest_edge_path, shortest_drive_path
    out, node, i = [], spawn, 0
    while i < len(path):
        edge = path[i]
        if edge.id not in closed:
            out.append(edge)
            node = edge.get_adjacent_node(node)
            i += 1
            continue
        start, end = node, node
        while i < len(path) and path[i].id in closed:            # skip the whole closed run
            end = path[i].get_adjacent_node(end)
            i += 1
        if start is end:
            continue
        around = shortest_drive_path(start.id, end.id, city) or shortest_edge_path(start.id, end.id, city, railway)
        if not around:
            return None
        out += list(around)
        node = end
    return out


def update_routes(sim) -> dict:
    """Bring every route in line with the current closures. Returns {'detoured': [...], 'removed': [...]}."""
    _clear_search_caches()
    closed = set(sim.closed_edges)
    remove_all = config.get('CLOSED_ROAD_ROUTES', 'detour') == 'remove'
    detoured, removed = [], set()
    for route_id, (spawn, loaded_path) in sim.route_loaded.items():
        group = [r for r in sim.all_routes if getattr(r, 'route_id', None) == route_id]
        spawn, intended = sim.route_edits.get(route_id, (spawn, loaded_path))
        uses_closed = (any(edge.id in closed for edge in intended)
                       or any(edge.id in closed for r in group for edge in r.path))   # incl. the return trip
        if not uses_closed and route_id not in sim._closure_touched:
            continue
        target = intended
        if any(edge.id in closed for edge in intended):
            target = None if remove_all else detour(spawn, intended, closed, sim.graph, sim.railway_graph)
            if target is None:
                removed.add(route_id)
                continue
            detoured.append(route_id)
        elif uses_closed:
            detoured.append(route_id)               # only its return trip used the closed road
        before = [(r.spawn_node, [e.id for e in r.path]) for r in group]
        set_route_group_path(sim.all_routes, route_id, spawn, target)   # re-derives both legal directions
        if [(r.spawn_node, [e.id for e in r.path]) for r in group] != before or uses_closed:
            sim._closure_touched.add(route_id)
        if any(edge.id in closed for r in group for edge in r.path):    # no way around for a direction
            detoured.remove(route_id) if route_id in detoured else None
            removed.add(route_id)

    newly_removed = removed - sim.removed_routes
    restored = sim.removed_routes - removed
    sim.removed_routes = removed
    sim.routes[:] = [r for r in sim.all_routes if getattr(r, 'route_id', None) not in removed]   # in place

    # vehicle spawns: nothing has started yet, so only the first spawn of each route is queued
    gone = {id(r) for r in sim.all_routes if getattr(r, 'route_id', None) in newly_removed}
    if gone:
        for target_time in list(manager._events):
            for event in manager._events[target_time]:
                if event.type == manager.TRANSPORTATION_SPAWN:
                    event._objects = [obj for obj in event._objects if id(obj) not in gone]
            manager._events[target_time] = [e for e in manager._events[target_time]
                                            if e.type != manager.TRANSPORTATION_SPAWN or e._objects]
            if not manager._events[target_time]:
                del manager._events[target_time]
    for route in sim.all_routes:
        if getattr(route, 'route_id', None) in restored:
            manager.emit(sim.start_time + 3, manager.Event(manager.TRANSPORTATION_SPAWN, route))

    sim.network_dirty = True
    return {'detoured': sorted(set(detoured)), 'removed': sorted(removed)}


def route_state(sim) -> tuple[dict, list, list]:
    """(route paths that differ from the loaded case {route_id: (spawn_id, [edge_ids])},
    closed edge ids, removed route ids) — what worker processes and case files need."""
    edits = {}
    for route_id, loaded in sim.route_loaded_dirs.items():
        if route_id in sim.removed_routes:
            continue
        group = [r for r in sim.all_routes if getattr(r, 'route_id', None) == route_id]
        if [(r.spawn_node, [e.id for e in r.path]) for r in group] != loaded:     # any direction changed
            edits[route_id] = (group[0].spawn_node.id, [e.id for e in group[0].path])
    return edits, sorted(sim.closed_edges), sorted(sim.removed_routes)


def apply_in_worker(city, routes:list, closed_ids, removed_ids, edits) -> list:
    """Repeat the main process's closures, removals and route paths on a freshly loaded network."""
    for edge_id in closed_ids:
        edge = city.edges.pop(tuple(edge_id), None)
        if edge is not None:
            for node in edge.nodes:
                if edge in node.edges:
                    node.edges.remove(edge)
    removed = set(removed_ids)
    routes = [r for r in routes if getattr(r, 'route_id', None) not in removed]
    for route_id, (spawn_id, edge_ids) in edits.items():
        set_route_group_path(routes, route_id, city.get_node(tuple(spawn_id)), [city.get_edge(tuple(e)) for e in edge_ids])
    return routes


def finish_network_changes(sim, on_progress=None) -> str:
    """Rebuild the routing cache and (with OD demand on) the OD agents for the current network."""
    from routing_table import rebuild_routing_cache
    _clear_search_caches()
    sim.routing_table = rebuild_routing_cache(sim, on_progress)
    msg = f"routing cache rebuilt ({len(sim.routing_table):,} trips)"
    if config.get('OD_BUNDLE_DIR') and hasattr(sim, 'reschedule_od_demand'):
        sim.reschedule_od_demand()
        msg += f", OD recomputed ({sim.od_summary['agents_scheduled']:,} agents)"
    sim.network_dirty = False
    return msg
