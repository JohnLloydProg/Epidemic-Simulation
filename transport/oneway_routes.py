"""
oneway_routes.py — make jeepney/bus route directions obey one-way roads.

The route shapes in transit_routes.json were matched to an undirected road network, and every route is
"bidirectional" (the return trip is the same path reversed), so about 42% of their one-way traversals ran
against traffic. Each direction that uses a one-way road the wrong way is re-planned as a legal drive that
stays with the original route:

  * corridor: roads within CORRIDOR_M of the original path cost their length, others PENALTY x length
    (a divided road's other carriageway or the parallel street of a one-way pair lies inside the corridor);
  * waypoints: every WAYPOINT_EVERY_M along the original path the route must pass within SNAP_M of it, in
    order, so it keeps its course (and loops) instead of taking a shortcut;
  * flexible ends: it may start and end at any node within SNAP_M of the original start / end (the other
    carriageway's node at the network's edge).
Directions without a wrong-way edge are left exactly as they are; if no legal drive exists the original path
is kept (logged). The return trip is derived from the LEGAL forward path, in the loader and in
set_route_group_path alike, so edits, closures, saved cases and worker processes all give the same routes.

Config: "ROUTES_FOLLOW_ONEWAY": true
"""
from __future__ import annotations
import heapq
import logging

import numpy as np

import configuration as config

LOGGER = logging.getLogger('OneWayRoutes')

CORRIDOR_M = 60
SNAP_M = 100
WAYPOINT_EVERY_M = 800
PENALTY = 5


def _xy(node):
    return getattr(node, 'precise_pos', node.pos)


def _dist_to_polyline(points:np.ndarray, line:np.ndarray) -> np.ndarray:
    if len(line) < 2:
        return np.sqrt(((points - line[0]) ** 2).sum(1))
    a, b = line[:-1], line[1:]
    ab = b - a
    length = (ab ** 2).sum(1)
    length[length == 0] = 1e-9
    t = np.clip(((points[:, None, :] - a[None]) * ab[None]).sum(2) / length[None], 0, 1)
    proj = a[None] + t[..., None] * ab[None]
    return np.sqrt(((points[:, None, :] - proj) ** 2).sum(2)).min(1)


def is_legal(spawn, path) -> bool:
    from graphing.mapping import can_drive
    node = spawn
    for edge in path:
        if not can_drive(edge, node):
            return False
        node = edge.get_adjacent_node(node)
    return True


def legalize(spawn, path:list, city) -> tuple:
    """(spawn, path) of a legal drive along the route, or the input unchanged if it is legal already,
    the feature is off, or no legal drive exists."""
    if (not config.get('ROUTES_FOLLOW_ONEWAY', True) or not path or path[0].id[0] != 'city'
            or is_legal(spawn, path)):
        return spawn, list(path)
    from graphing.mapping import can_drive
    nodes = [n for n in city.nodes.values() if n.edges]
    index = {n.id: i for i, n in enumerate(nodes)}
    pos = np.array([_xy(n) for n in nodes], float)

    seq = [spawn]
    for edge in path:
        seq.append(edge.get_adjacent_node(seq[-1]))
    line = np.array([_xy(n) for n in seq], float)
    near = _dist_to_polyline(pos, line) <= CORRIDOR_M

    waypoints, run = [], 0.0
    for edge, node in zip(path, seq[1:]):
        run += edge.distance
        if run >= WAYPOINT_EVERY_M:
            waypoints.append(node)
            run = 0.0
    if not waypoints or waypoints[-1] is not seq[-1]:
        waypoints.append(seq[-1])
    targets = [set(np.flatnonzero(np.sqrt(((pos - np.array(_xy(w))) ** 2).sum(1)) <= SNAP_M)) for w in waypoints]
    goal_k = len(targets)

    # Dijkstra over (node, waypoints reached); start anywhere near the original start (prefer it)
    best, prev, heap, counter = {}, {}, [], 0
    origin = np.array(_xy(spawn))
    for i in np.flatnonzero(np.sqrt(((pos - origin) ** 2).sum(1)) <= SNAP_M):
        d0 = float(np.sqrt(((pos[i] - origin) ** 2).sum())) * PENALTY
        k0 = 1 if i in targets[0] else 0
        if d0 < best.get((i, k0), float('inf')):
            best[(i, k0)] = d0
            heapq.heappush(heap, (d0, counter, int(i), k0))
            counter += 1
    goal = None
    while heap:
        d, _, i, k = heapq.heappop(heap)
        if d > best.get((i, k), float('inf')):
            continue
        if k == goal_k:
            goal = (i, k)
            break
        node = nodes[i]
        for edge in node.edges:
            nb = edge.get_adjacent_node(node)
            if nb.id[0] != 'city' or not can_drive(edge, node) or nb.id not in index:
                continue
            j = index[nb.id]
            cost = d + edge.distance * (1 if (near[i] and near[j]) else PENALTY)
            nk = k + 1 if j in targets[k] else k
            if cost < best.get((j, nk), float('inf')):
                best[(j, nk)] = cost
                prev[(j, nk)] = ((i, k), edge)
                heapq.heappush(heap, (cost, counter, j, nk))
                counter += 1
    if goal is None:
        LOGGER.debug(f"No legal drive along the route from {spawn.id}; keeping its original path.")
        return spawn, list(path)
    out, state = [], goal
    while state in prev:
        state, edge = prev[state]
        out.append(edge)
    if not out:
        return spawn, list(path)
    return nodes[state[0]], out[::-1]


def route_directions(spawn, path:list, city, bidirectional:bool = True) -> list[tuple]:
    """[(spawn, path) forward, (spawn, path) return trip]: both legal where possible; the return trip is
    the legal forward path reversed, then made legal."""
    fwd_spawn, fwd_path = legalize(spawn, path, city) if city is not None else (spawn, list(path))
    directions = [(fwd_spawn, fwd_path)]
    if bidirectional:
        end = fwd_spawn
        for edge in fwd_path:
            end = edge.get_adjacent_node(end)
        reverse = list(reversed(fwd_path))
        directions.append(legalize(end, reverse, city) if city is not None else (end, reverse))
    return directions
