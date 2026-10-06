"""
od_routes.py — make the OD matrix follow the simulation's transit routes.

The OD library (manila_od.py) models a route by the zones it serves:
  * routes per barangay raise its attraction  ((1 + routes) ** route_exponent)
  * one line links all its zones with one vehicle, so fewer transfers -> lower cost between them.
Its lines come from OpenStreetMap (routes.csv); the simulation's routes come from the Sakay GTFS feed. The two
sets do not share ids and do not match closely enough to pair them up, so a simulation route change is applied
the same way the library applies its own route changes — as a difference on top of the bundle:

  routes per zone = bundle count + (zones the changed routes serve now) - (zones they served in the base data)
  transfers       = bundle transfers + transfers(bundle lines + changed routes now)
                                     - transfers(bundle lines + changed routes as in the base data)

"Base data" is sim_data/base/transit_routes.json, so in-session edits and saved cases (transit_overrides,
disabled_routes, added_routes) are handled the same way. Only routes whose served zones changed count; with no
such change the OD matrix is exactly the library's.

Zones served by a simulation route = the OD zones of the nodes on its path: study barangays from zones.json,
any other node (the network's buffer around the study area) by the bundle zone polygon it lies in (zones.gpkg). The part of
a line outside the simulated network is unknown, so a change only alters which zones the line links inside the
simulated area — the same for the old and the new version, so it cancels out of the difference.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from manila_od import ODModel, fewest_vehicles

LOGGER = logging.getLogger('ODRoutes')

MODE_MAP = {'jeepney': 'share_taxi', 'jeep': 'share_taxi', 'bus': 'bus'}   # simulation mode -> OD mode


class RouteChange:
    """One simulation route whose served zones differ from the base data (zone rows of the OD model)."""
    __slots__ = ('route_id', 'mode', 'old', 'new')

    def __init__(self, route_id, mode, old, new):
        self.route_id, self.mode, self.old, self.new = route_id, mode, list(old), list(new)

    def describe(self, names) -> dict:
        old, new = set(self.old), set(self.new)
        return {'route_id': self.route_id,
                'zones_removed': [str(names[r]) for r in sorted(old - new)],
                'zones_added': [str(names[r]) for r in sorted(new - old)]}


class SimRouteODModel(ODModel):
    """ODModel that also applies simulation route changes (see module docstring).
    Set .sim_changes, then call run() with a "routes" key in the settings (an empty dict is fine)."""

    def __init__(self, bundle_dir: str):
        super().__init__(bundle_dir)
        self.sim_changes: list[RouteChange] = []

    def _sim_counts(self, cfg: dict, which: str) -> np.ndarray:
        weights = cfg.get('route_mode_weights', {}) or {}
        counts = np.zeros(len(self.names))
        for change in self.sim_changes:
            rows = getattr(change, which)
            if rows:
                counts[rows] += float(weights.get(change.mode, 1.0))
        return counts

    def route_counts(self, cfg: dict, change: dict | None = None) -> np.ndarray:
        counts = super().route_counts(cfg, change)
        if change is None or not self.sim_changes:          # None = the bundle baseline (used for calibration)
            return counts
        return counts + self._sim_counts(cfg, 'new') - self._sim_counts(cfg, 'old')

    def hops_for(self, change: dict, cfg: dict) -> np.ndarray:
        if not self.sim_changes:
            return super().hops_for(change, cfg)
        mx = int(cfg['max_transfers'])
        n = len(self.names)
        now = fewest_vehicles(n, self._groups(self.routes_after(change)) + [c.new for c in self.sim_changes if c.new], mx + 1)
        ref = fewest_vehicles(n, self._groups(self.lines) + [c.old for c in self.sim_changes if c.old], mx + 1)
        tr = lambda h: np.clip(h - 1, 0, mx)
        t = np.clip(tr(self.hops) + tr(now) - tr(ref), 0, mx)
        h = (t + 1).astype(float)
        np.fill_diagonal(h, np.diag(self.hops))
        return h


# --------------------------------------------------------------------------- simulation routes -> OD zones
def _gpkg_polygons(path: Path) -> dict:
    """{zone name: [(outer ring, [holes]), ...]} from a GeoPackage, read with sqlite3 (no GIS packages needed).
    Rings are (n, 2) arrays in the layer's CRS. Handles Polygon and MultiPolygon (2D, Z or M)."""
    import sqlite3, struct
    con = sqlite3.connect(str(path))
    table, column = con.execute("SELECT table_name, column_name FROM gpkg_geometry_columns").fetchone()
    rows = con.execute(f'SELECT zone, "{column}" FROM "{table}"').fetchall()
    con.close()

    def polygons(blob: bytes):
        flags = blob[3]
        envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
        data, pos = blob, 8 + envelope

        def geometry(pos):
            little = data[pos] == 1
            e = '<' if little else '>'
            gtype = struct.unpack_from(e + 'I', data, pos + 1)[0]
            pos += 5
            base = (gtype & 0xFFFF) % 1000
            zm = (gtype & 0xFFFF) // 1000                 # ISO WKB: 1 = Z, 2 = M, 3 = ZM; EWKB uses the high bits
            dims = 2 + (zm in (1, 2)) + (zm == 3) * 2 + bool(gtype & 0x80000000) + bool(gtype & 0x40000000)
            if base == 3:
                n_rings = struct.unpack_from(e + 'I', data, pos)[0]; pos += 4
                rings = []
                for _ in range(n_rings):
                    n = struct.unpack_from(e + 'I', data, pos)[0]; pos += 4
                    pts = np.frombuffer(data, dtype=e + 'f8', count=n * dims, offset=pos).reshape(n, dims)[:, :2]
                    pos += 8 * n * dims
                    rings.append(pts)
                return [(rings[0], rings[1:])], pos
            if base == 6:
                n_parts = struct.unpack_from(e + 'I', data, pos)[0]; pos += 4
                out = []
                for _ in range(n_parts):
                    part, pos = geometry(pos)
                    out += part
                return out, pos
            raise ValueError(f"zones.gpkg: unsupported geometry type {gtype}")
        return geometry(pos)[0]

    return {str(name): polygons(blob) for name, blob in rows if blob}


def node_zone_rows(model: ODModel, city, data_dir: Path) -> dict:
    """OD zone row for every city node: study barangays from zones.json; any other node by the zone polygon
    it lies in (bundle zones.gpkg), or the nearest zone point if it lies in none (e.g. on the bay)."""
    from matplotlib.path import Path as MplPath
    from agents.od_demand import zone_points, utm_to_sim
    rows, study = {}, set()
    for region in city.zones.values():
        study.add(region.name)
        row = model.row_of.get(region.name)
        if row is None:
            continue
        for node in region.nodes:
            if node is not None:
                rows[node.id] = row
    with open(data_dir / 'base' / 'meta.json', encoding='utf-8') as f:
        meta = json.load(f)
    t = meta['transform']
    others = [node for node in city.nodes.values() if node.id not in rows]
    if not others:
        return rows
    sim_xy = np.array([getattr(n, 'precise_pos', n.pos) for n in others], float)
    utm = np.column_stack([sim_xy[:, 0] + t['origin_x'], t['origin_y'] - sim_xy[:, 1]])
    found = np.full(len(others), -1)

    gpkg = Path(model.bundle_dir) / 'zones.gpkg'
    if gpkg.exists():
        lo, hi = utm.min(axis=0), utm.max(axis=0)
        for name, parts in _gpkg_polygons(gpkg).items():
            row = model.row_of.get(name)
            if row is None or name in study:
                continue
            for outer, holes in parts:
                if (outer.max(axis=0) < lo).any() or (outer.min(axis=0) > hi).any():
                    continue
                inside = MplPath(outer).contains_points(utm)
                for hole in holes:
                    inside &= ~MplPath(hole).contains_points(utm)
                found[inside & (found < 0)] = row
    else:
        LOGGER.warning(f"'{gpkg}' not found: nodes outside the study barangays use the nearest zone point.")

    missing = found < 0
    if missing.any():                                     # not inside any zone: nearest zone point
        outside = np.array([name not in study for name in model.names])
        candidates = np.flatnonzero(outside)
        points = utm_to_sim(zone_points(model.zones), meta)[outside]
        d = ((sim_xy[missing][:, None, :] - points[None, :, :]) ** 2).sum(axis=2)
        found[missing] = candidates[np.argmin(d, axis=1)]
    for node, row in zip(others, found):
        rows[node.id] = int(row)
    return rows


def sim_route_changes(model: ODModel, city, routes: list, data_dir: Path) -> list[RouteChange]:
    """Simulation routes (road layer) whose served OD zones differ from sim_data/base/transit_routes.json."""
    edges = pd.read_parquet(data_dir / 'base' / 'network_edges.parquet')
    edges = edges[edges['layer'] == 'city']
    ends = {int(e): (('city', int(u)), ('city', int(v))) for e, u, v in zip(edges['edge_id'], edges['u'], edges['v'])}
    zone_of = node_zone_rows(model, city, data_dir)

    def zones(edge_ids) -> list[int]:
        rows = []
        for e in edge_ids:
            for node_id in ends.get(int(e), ()):
                if node_id in zone_of:
                    rows.append(zone_of[node_id])
        return list(dict.fromkeys(rows))

    with open(data_dir / 'base' / 'transit_routes.json', encoding='utf-8') as f:
        base = {r['route_id']: r for r in json.load(f)['routes'] if r.get('layer', 'city') == 'city'}
    now = {}
    for route in routes:                                 # first direction of each route = its definition
        route_id = getattr(route, 'route_id', None)
        if route.graph.layer == 'city' and route_id and route_id not in now:
            now[route_id] = route

    changes = []
    for route_id in list(base) + [r for r in now if r not in base]:
        old = zones(base[route_id]['edges']) if route_id in base else []
        new = zones([edge.id[1] for edge in now[route_id].path]) if route_id in now else []
        if set(old) != set(new):
            mode = (base.get(route_id) or {}).get('mode') or getattr(now.get(route_id), 'mode', '')
            changes.append(RouteChange(route_id, MODE_MAP.get(mode, mode), old, new))
    return changes
