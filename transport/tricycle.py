"""
tricycle.py — tricycles (TODA service) as an on-demand ride bound to barangays.

Territory follows the OD model (manila_od.py, neighbors.csv): the tricycles of barangay A carry passengers
between any two points of A and the barangays touching A. So a commuter can take a tricycle to the edge of
that territory and continue by jeepney, bus, train, foot, or another barangay's tricycle (a new boarding).

  * Boarding: at any node of the territory (tricycles are hailed, not taken at stops), after an expected wait
    (TRICYCLE_WAIT_S). Riding: along roads inside the territory that tricycles may use (not the road types in
    TRICYCLE_EXCLUDED_ROADS — by default national-highway classes, where tricycles are generally barred), at
    TRICYCLE_SPEED_MPS. Alighting: at any node.
  * The path search (graphing/mapping.py) treats a TricycleService like a route the agent can ride, so trips
    combine tricycles with everything else on their own; the routing cache stores it as "tricycle:<barangay>".
  * Phase 1 (this file): no fleet. The agent waits TRICYCLE_WAIT_S, then a tricycle carries them along the
    shortest allowed path (one passenger per vehicle, drawn as a small green dot).
  * A barangay's tricycles can be switched off (H mode, key T; case file "tricycle_disabled": [psgc, ...]).
    The OD model then drops that barangay's tricycle link from its transfer counts (agents/od_routes.py).

Config:
    "TRICYCLES":               true          tricycle service on (needs OD_BUNDLE_DIR for neighbors.csv)
    "TRICYCLE_SPEED_MPS":      5.0
    "TRICYCLE_WAIT_S":         120           expected wait to get a tricycle (the agent really waits this long)
    "TRICYCLE_FARE_S":         300           the fare as seconds of travel time, added to the search's boarding
                                             cost only. With time alone, a tricycle beats walking for anything
                                             over ~400 m, so tricycle use is far too high; calibrate this to a
                                             known tricycle mode share.
    "TRICYCLE_EXCLUDED_ROADS": ["trunk", "trunk_link", "primary", "primary_link"]
"""
from __future__ import annotations
import heapq
import json
import logging
from pathlib import Path

import configuration as config
from graphing.core import Edge, Node

LOGGER = logging.getLogger('Tricycle')

DEFAULT_EXCLUDED = ['trunk', 'trunk_link', 'primary', 'primary_link']
COLOR = (20, 160, 60)

_services:list = []
_by_node:dict = {}


class TricycleService:
    mode = 'tricycle'
    route_id = None

    def __init__(self, zone:str, psgc:str, node_ids:set, speed:float, excluded:set):
        self.zone = zone
        self.psgc = psgc
        self.id = ('tricycle', zone)             # unique among route ids (those are integers)
        self.name = f"Tricycles of {zone}"
        self.node_ids = node_ids
        self.expected_speed = speed
        self.excluded = excluded
        self.enabled = True

    def allows(self, edge:Edge) -> bool:
        return (edge.id[0] == 'city' and edge.nodes[0].id in self.node_ids and edge.nodes[1].id in self.node_ids
                and getattr(edge, 'highway', None) not in self.excluded)

    def path(self, start:Node, end:Node) -> list[Edge]:
        """Shortest allowed road path inside the territory ([] if there is none)."""
        if start is end:
            return []
        dist, prev, heap, counter = {start.id: 0.0}, {}, [(0.0, 0, start)], 1
        while heap:
            d, _, node = heapq.heappop(heap)
            if node is end:
                break
            if d > dist.get(node.id, float('inf')):
                continue
            for edge in node.edges:
                if not self.allows(edge):
                    continue
                nxt = edge.get_adjacent_node(node)
                nd = d + edge.distance
                if nd < dist.get(nxt.id, float('inf')):
                    dist[nxt.id], prev[nxt.id] = nd, (node, edge)
                    heapq.heappush(heap, (nd, counter, nxt))
                    counter += 1
        if end.id not in prev:
            return []
        path, node = [], end
        while node is not start:
            node, edge = prev[node.id]
            path.append(edge)
        return path[::-1]

    def __repr__(self):
        return self.name


def is_tricycle(route) -> bool:
    return getattr(route, 'mode', None) == 'tricycle'


def services_at_node(node:Node) -> list:
    return [s for s in _by_node.get(node.id, ()) if s.enabled]


def service_for(zone:str):
    return next((s for s in _services if s.zone == zone), None)


def services() -> list:
    return list(_services)


def set_services(items:list):
    global _services, _by_node
    _services = list(items)
    _by_node = {}
    for service in _services:                     # fixed order -> same search results in every process
        for node_id in sorted(service.node_ids):
            _by_node.setdefault(node_id, []).append(service)


def enabled() -> bool:
    return bool(config.get('TRICYCLES', True)) and bool(config.get('OD_BUNDLE_DIR'))


def settings_signature(disabled=()) -> dict:
    """Everything that changes tricycle routing (stored with the routing cache to know when to rebuild)."""
    if not enabled():
        return {'tricycles': False}
    return {'tricycles': True, 'speed': float(config.get('TRICYCLE_SPEED_MPS', 5.0)),
            'wait': float(config.get('TRICYCLE_WAIT_S', 120)),
            'fare_s': float(config.get('TRICYCLE_FARE_S', 300)),
            'excluded': sorted(config.get('TRICYCLE_EXCLUDED_ROADS', DEFAULT_EXCLUDED)),
            'disabled': sorted(disabled)}


def build_services(city, data_dir:Path, disabled_psgc=()) -> list:
    """One service per study barangay: its nodes plus those of the barangays touching it (neighbors.csv);
    nodes outside the study barangays are placed by the bundle's zone polygons."""
    if not enabled():
        set_services([])
        return []
    import pandas as pd
    from agents.od_routes import SimRouteODModel, node_zone_rows
    bundle = Path(config.get('OD_BUNDLE_DIR', 'od_bundle'))
    if not (bundle / 'neighbors.csv').exists():
        LOGGER.warning(f"'{bundle / 'neighbors.csv'}' not found: no tricycles.")
        set_services([])
        return []
    model = SimRouteODModel.load(str(bundle))
    zone_of = node_zone_rows(model, city, Path(data_dir))
    nodes_in = {}
    for node_id, row in zone_of.items():
        nodes_in.setdefault(model.names[row], set()).add(node_id)
    nb = pd.read_csv(bundle / 'neighbors.csv', dtype=str)
    touching = {}
    for a, b in zip(nb['zone_a'], nb['zone_b']):
        touching.setdefault(a, set()).add(b)
    speed = float(config.get('TRICYCLE_SPEED_MPS', 5.0))
    excluded = set(config.get('TRICYCLE_EXCLUDED_ROADS', DEFAULT_EXCLUDED))
    disabled = {str(p) for p in disabled_psgc}
    items = []
    for region in sorted(city.zones.values(), key=lambda r: r.name):
        territory = {region.name} | touching.get(region.name, set())
        node_ids = set().union(*(nodes_in.get(z, set()) for z in territory))
        node_ids = {i for i in node_ids if city.get_node(i) is not None}
        if not node_ids:
            continue
        service = TricycleService(region.name, str(region.psgc), node_ids, speed, excluded)
        service.enabled = str(region.psgc) not in disabled
        items.append(service)
    set_services(items)
    LOGGER.info(f"{len(items)} tricycle services ({sum(not s.enabled for s in items)} switched off).")
    return items


def disabled_psgc() -> list:
    return sorted(s.psgc for s in _services if not s.enabled)


# --------------------------------------------------------------------------- riding (phase 1: no fleet)
def start_ride(agent, time:int, simulation):
    """Called TRICYCLE_WAIT_S after the agent started waiting: a tricycle carries them to the leg's end."""
    from transport.transportation import Transportation
    from transport.checkpoint import Checkpoint
    if agent.state != 'waiting' or not getattr(agent, 'checkpoints', None):
        return
    leg = agent.checkpoints[0]
    if not is_tricycle(leg.route):
        return
    path = leg.route.path(leg.start_node, leg.end_node) if leg.route.enabled else []
    if not path:                                  # e.g. the service was switched off: walk this leg instead
        agent.checkpoints[0] = Checkpoint('walk', leg.start_node, leg.end_node, None)
        agent.move(time, simulation)
        return
    vehicle = Transportation(method='tricycle', speed=leg.route.expected_speed, color=COLOR,
                             current_node=agent.current_node, path=list(path))
    vehicle.service = leg.route
    agent.ride_transportation(vehicle, time)
    simulation.transportations.append(vehicle)
    vehicle.transport(time)


def ride_distance(checkpoint) -> float:
    return sum(edge.distance for edge in checkpoint.route.path(checkpoint.start_node, checkpoint.end_node))
