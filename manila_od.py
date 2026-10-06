"""
manila_od.py — produce the base City of Manila OD matrix and adjusted OD matrices from a model bundle.

The bundle (folder "od_bundle") is exported once from the Colab notebook (Cell 15). After that this file
runs on any computer with Python 3.10+, NumPy and pandas — no OSM, GIS or Colab needed.

------------------------------------------------------------------------------------------------
QUICK START (edit the USER SETTINGS block below, then run:  python manila_od.py)
------------------------------------------------------------------------------------------------
Or from the command line:
    python manila_od.py --bundle od_bundle --name base                      # base matrix
    python manila_od.py --bundle od_bundle --name ermita_half --settings my_settings.json
Or from Python (e.g. inside a Pygame app):
    from manila_od import ODModel
    m = ODModel.load("od_bundle")
    base = m.base()
    run = m.run({"attraction": {"Barangay 669": 0.5}})
    run.save("runs", name="ermita_half")
    pairs = run.top_pairs(share=0.8, districts=["Port Area", "Ermita", "Malate"], selection_mode="within")

    # Route changes (needs routes.csv and neighbors.csv in the bundle, see ROUTES below)
    print(m.route_table())                                        # routes and the zones they serve
    run = m.run({"routes": {"remove": ["Route A"],
                            "add": {"New Ermita loop": ["Barangay 669", "Barangay 670", "Barangay 676"]}}})

------------------------------------------------------------------------------------------------
SETTINGS YOU CAN CHANGE (dict or .json file; anything not given keeps the base value)
------------------------------------------------------------------------------------------------
Model weights (change the baseline -> beta is recalibrated to the Pasig screen line by default):
  "purpose_shares":      {"work": 32.9, "shopping": 14.8, ...}   partial updates allowed (%)
  "purpose_facilities":  {"work": ["work", "gov", "mall"], ...}   which facility categories serve a purpose
  "size_exponent":       0.5        facility size = floor area ** exponent (0 = count only)
  "max_floor_m2":        50000      cap on one facility's floor area
  "default_levels":      1          floors assumed when OSM has none
  "min_share_of_avg":    0.05       minimum attraction share of a barangay (fraction of the average)
  "use_transfers", "transfer_penalty_min", "pt_share", "max_transfers"   transfer penalty settings
  "category_weights":    {"mall": 2}  multiplier on one facility category's size (default 1 for all)
                                    note: a weight shifts attraction between categories that serve the SAME
                                    purpose (e.g. malls vs shops in "shopping"); a purpose's total stays its share
  "route_exponent":      0.3        Manila attraction x (1 + routes through the barangay) ** exponent (0 = off)
  "route_mode_weights":  {"train": 3}   how much one route of a mode counts (modes not listed count 1)
Scenario (interventions -> beta stays at the base value by default):
  "attraction":     {"Barangay 669": 0.5}     multiplier on a zone's attraction (0 = closed)
  "extra_minutes":  {"Barangay 306": 10}      extra minutes to reach a zone
  "zone_facilities": {"Barangay 669": {"shop": 50, "mall": 0}}
                    number of facilities of a category counted in one barangay (only that barangay changes).
                    Fewer than it has: the category's size there shrinks in proportion (n / current count,
                    i.e. removing facilities of average size). 0 removes the category from the barangay.
                    More than it has: extra facilities of the category's citywide median size are added.
                    See m.facility_counts() for the current numbers. Manila's total attraction stays fixed,
                    so trips are redistributed, not removed. A barangay never drops below min_share_of_avg.
Calibration:
  "recalibrate":    true / false               override the automatic choice above
Export (what .save() writes as the pairs CSV):
  "export": {"mode": "top" | "full", "top_share": 0.8, "scope": "touch_manila" | "within_manila" | "all",
             "zones": [...], "districts": [...], "selection_mode": "within" | "touch",
             "include_intrazonal": false}

------------------------------------------------------------------------------------------------
ROUTES (optional bundle files, needed only for the "routes" setting)
------------------------------------------------------------------------------------------------
  routes.csv     columns: route_id, relation, mode, zone    one row per (OSM route relation, zone it serves)
                 route_id = the line you remove/add (both directions of a line share one route_id);
                 relation = one OSM relation (one direction). Routes per barangay count route_ids.
  neighbors.csv  columns: zone_a, zone_b        tricycle: one ride links zone_a with each touching zone_b
                                                (and those zones with each other), as in the notebook
Route changes are applied as a difference in transfers: transfers = bundle transfers + (transfers with your
routes - transfers with the bundle routes). The base matrix stays exactly the notebook's and only pairs your
change affects move.
Run m.check_routes() once to see how closely routes.csv reproduces transit_hops.npy.
"""
from __future__ import annotations
import argparse, copy, json, os
import numpy as np
import pandas as pd

# =============================================================================================
# USER SETTINGS — used when you run this file directly without command-line arguments
# =============================================================================================
BUNDLE_DIR  = "od_bundle"         # folder exported from the notebook (Cell 15)
OUTPUT_DIR  = "runs"              # where results are written
OUTPUT_NAME = "base"              # <- name used for the output files, e.g. "ermita_half"
SETTINGS    = {                   # changes from the base model; {} = base matrix
    # "attraction": {"Barangay 669": 0.5},
    # "purpose_shares": {"shopping": 20},
    # "routes": {"remove": ["bus 12"], "add": {"New loop": ["Barangay 669", "Barangay 670"]}},
    # "category_weights": {"mall": 2},
    # "export": {"mode": "top", "top_share": 0.8, "districts": ["Port Area", "Ermita", "Malate"]},
}
# =============================================================================================

WEIGHT_KEYS = {"purpose_shares", "purpose_facilities", "size_exponent", "max_floor_m2", "default_levels",
               "min_share_of_avg", "use_transfers", "transfer_penalty_min", "pt_share", "max_transfers",
               "route_exponent", "route_mode_weights", "category_weights"}
SCENARIO_KEYS = {"attraction", "extra_minutes", "routes", "zone_facilities"}
OTHER_KEYS = {"recalibrate", "export", "name"}
EXPORT_DEFAULT = {"mode": "top", "top_share": 0.8, "scope": "touch_manila", "zones": [], "districts": [],
                  "selection_mode": "within", "include_intrazonal": False}
ROUTE_OPS = {"remove", "add"}


# --------------------------------------------------------------------------------------------- helpers
def integerize_rows(od: np.ndarray) -> np.ndarray:
    """Whole-number trips; each row sums exactly to its rounded total (largest remainder)."""
    floor = np.floor(od).astype(np.int64)
    short = np.rint(od.sum(axis=1)).astype(np.int64) - floor.sum(axis=1)
    rema = od - floor
    for i in np.flatnonzero(short > 0):
        floor[i, np.argsort(-rema[i])[:short[i]]] += 1
    return floor


def prod_constrained(P, A, C, beta):
    """T_ij = P_i * A_j * exp(-beta*C_ij) / sum_k A_k * exp(-beta*C_ik)."""
    F = A[None, :] * np.exp(-beta * C)
    return P[:, None] * F / F.sum(axis=1, keepdims=True)


def fewest_vehicles(n: int, groups: list, max_vehicles: int) -> np.ndarray:
    """Fewest vehicles needed between every pair of zones (same result as the notebook's Cell 10b).
    groups: lists of zone rows; one vehicle (a route relation or a tricycle ride) links all zones in a group.
    Pairs needing more than max_vehicles get max_vehicles + 1 (the cost caps transfers anyway)."""
    groups = [np.asarray(g, int) for g in groups if len(g) > 0]
    S = np.zeros((n, len(groups)), np.float32)                     # zone x vehicle incidence
    for r, z in enumerate(groups):
        S[z, r] = 1.0
    one = (S @ S.T) > 0                                            # reachable with one vehicle
    np.fill_diagonal(one, True)
    one_f = one.astype(np.float32)
    hops = np.full((n, n), max_vehicles + 1, np.int64)
    hops[one] = 1
    reach = one_f
    for k in range(2, max_vehicles + 1):
        reach = ((reach @ one_f) > 0).astype(np.float32)
        hops[(reach > 0) & (hops > k)] = k
    np.fill_diagonal(hops, 0)
    return hops


def _merge(base: dict, changes: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in changes.items():
        if k in ("purpose_shares", "purpose_facilities", "export", "route_mode_weights",
                 "category_weights") and isinstance(v, dict):
            out[k] = {**out.get(k, {}), **v}
        else:
            out[k] = copy.deepcopy(v)
    return out


# --------------------------------------------------------------------------------------------- model
class ODModel:
    """Loads a bundle and produces base or adjusted OD matrices."""

    def __init__(self, bundle_dir: str):
        b = bundle_dir
        self.bundle_dir = b
        self.zones = pd.read_csv(os.path.join(b, "zones.csv"))
        self.T = np.load(os.path.join(b, "travel_time.npy"))
        self.hops = np.load(os.path.join(b, "transit_hops.npy"))
        self.X = np.load(os.path.join(b, "pasig_crossings_per_trip.npy"))
        self.fac = pd.read_csv(os.path.join(b, "facilities.csv"))
        with open(os.path.join(b, "base_config.json")) as f:
            self.base_config = json.load(f)
        self.base_config.setdefault("export", dict(EXPORT_DEFAULT))
        n = len(self.zones)
        assert self.T.shape == self.hops.shape == self.X.shape == (n, n), "bundle matrices do not match zones.csv"
        self.INT = (self.zones["kind"] == "barangay").to_numpy()
        self.P = self.zones["P"].to_numpy(float)
        self.A_ext = np.where(self.INT, 0.0, self.zones["A"].to_numpy(float))
        self.names = self.zones["zone"].astype(str).to_numpy()
        self.row_of = {z: i for i, z in enumerate(self.names)}
        self._base_result = None
        self._load_routes()

    @classmethod
    def load(cls, bundle_dir: str = BUNDLE_DIR) -> "ODModel":
        return cls(bundle_dir)

    # ---- routes
    def _load_routes(self):
        rp, np_ = os.path.join(self.bundle_dir, "routes.csv"), os.path.join(self.bundle_dir, "neighbors.csv")
        self.has_routes = os.path.exists(rp)
        self.lines, self.tricycle_groups = {}, []
        self._hops_bundle_routes = None
        if not self.has_routes:
            return
        r = pd.read_csv(rp, dtype=str, keep_default_na=False)
        if "relation" not in r:
            r["relation"] = r["route_id"]
        if "mode" not in r:
            r["mode"] = ""
        bad = set(r["zone"]) - set(self.names)
        if bad:
            raise ValueError(f"routes.csv: zone(s) not in zones.csv: {sorted(bad)[:10]}")
        for rid, g in r.groupby("route_id", sort=False):
            groups = [[self.row_of[z] for z in dict.fromkeys(gg["zone"])] for _, gg in g.groupby("relation", sort=False)]
            self.lines[rid] = {"mode": g["mode"].iloc[0], "groups": groups}
        if os.path.exists(np_):
            nb = pd.read_csv(np_, dtype=str, keep_default_na=False)
            for a, g in nb.groupby("zone_a", sort=False):              # tricycle: zone_a + its neighbours
                self.tricycle_groups.append([self.row_of[a]] + [self.row_of[b] for b in g["zone_b"]])

    @staticmethod
    def _add_entry(v):
        """An "add" value is a zone list or {"zones": [...], "mode": "..."}."""
        return (list(v["zones"]), v.get("mode", "")) if isinstance(v, dict) else (list(v), "")

    def routes_after(self, change: dict | None = None) -> dict:
        """{route_id: {"mode", "groups"}} after applying {"remove": [...], "add": {...}}."""
        change = change or {}
        out = {k: {"mode": v["mode"], "groups": [list(g) for g in v["groups"]]} for k, v in self.lines.items()}
        for rid in change.get("remove", []):
            out.pop(rid)
        for rid, v in change.get("add", {}).items():
            zl, mode = self._add_entry(v)
            old_mode = self.lines.get(rid, {}).get("mode", "")
            out[rid] = {"mode": mode or old_mode, "groups": [[self.row_of[z] for z in dict.fromkeys(zl)]]}
        return out

    @staticmethod
    def _zones_of(line):
        return sorted(set().union(*map(set, line["groups"])))

    def route_table(self) -> pd.DataFrame:
        """Routes in the bundle: route_id, mode, OSM relations (directions), zones served."""
        self._need_routes()
        return pd.DataFrame([{"route_id": rid, "mode": v["mode"], "relations": len(v["groups"]),
                              "n_zones": len(self._zones_of(v)), "zones": ", ".join(self.names[self._zones_of(v)])}
                             for rid, v in self.lines.items()])

    def route_counts(self, cfg: dict, change: dict | None = None) -> np.ndarray:
        """Weighted number of routes (route_ids) serving each zone; tricycle links are not counted."""
        w = cfg.get("route_mode_weights", {}) or {}
        cnt = np.zeros(len(self.names))
        for v in self.routes_after(change).values():
            cnt[self._zones_of(v)] += float(w.get(v["mode"], 1.0))
        return cnt

    def routes_per_zone(self, change: dict | None = None) -> pd.DataFrame:
        """Routes serving each Manila barangay (raw count and the weighted count used in attraction)."""
        self._need_routes()
        return pd.DataFrame({"zone": self.names, "district": self.zones["district"],
                             "routes": self.route_counts({}, change).astype(int),
                             "weighted": self.route_counts(self.base_config, change)})[self.INT].reset_index(drop=True)

    def _groups(self, lines):
        return [g for v in lines.values() for g in v["groups"]] + self.tricycle_groups

    def _bundle_route_hops(self, max_v):
        if self._hops_bundle_routes is None or self._hops_bundle_routes[0] != max_v:
            self._hops_bundle_routes = (max_v, fewest_vehicles(len(self.names), self._groups(self.lines), max_v))
        return self._hops_bundle_routes[1]

    def hops_for(self, change: dict, cfg: dict) -> np.ndarray:
        """Vehicles between zones after a route change (difference in transfers applied to the bundle)."""
        mx = int(cfg["max_transfers"])
        new = fewest_vehicles(len(self.names), self._groups(self.routes_after(change)), mx + 1)
        old = self._bundle_route_hops(mx + 1)
        tr = lambda h: np.clip(h - 1, 0, mx)
        t = np.clip(tr(self.hops) + tr(new) - tr(old), 0, mx)
        h = (t + 1).astype(float)
        np.fill_diagonal(h, np.diag(self.hops))
        return h

    def check_routes(self) -> dict:
        """How well routes.csv + neighbors.csv reproduce transit_hops.npy (on the transfers the cost uses)."""
        self._need_routes()
        mx = int(self.base_config["max_transfers"])
        h = self._bundle_route_hops(mx + 1)
        a, b = np.clip(self.hops - 1, 0, mx), np.clip(h - 1, 0, mx)
        off = ~np.eye(len(self.names), dtype=bool)
        ii = np.ix_(self.INT, self.INT)
        return {"pairs_matching": float((a == b)[off].mean()),
                "manila_pairs_matching": float((a[ii] == b[ii])[~np.eye(self.INT.sum(), dtype=bool)].mean())}

    def _need_routes(self):
        if not self.has_routes:
            raise ValueError("This bundle has no routes.csv — re-export it with the updated notebook (Cell 15).")

    # ---- inputs
    def _check(self, settings: dict):
        unknown = set(settings) - WEIGHT_KEYS - SCENARIO_KEYS - OTHER_KEYS
        if unknown:
            raise ValueError(f"Unknown setting(s): {sorted(unknown)}")
        for key in ("attraction", "extra_minutes"):
            bad = set(settings.get(key, {})) - set(self.names)
            if bad:
                raise ValueError(f"'{key}': zone(s) not found (check spelling in zones.csv): {sorted(bad)}")
            if any(v < 0 for v in settings.get(key, {}).values()):
                raise ValueError(f"'{key}': values must be >= 0")
        if "routes" in settings:
            self._need_routes()
            rc = settings["routes"]
            if set(rc) - ROUTE_OPS:
                raise ValueError(f"'routes': unknown option(s) {sorted(set(rc) - ROUTE_OPS)}; use 'remove' and/or 'add'")
            bad = set(rc.get("remove", [])) - set(self.lines)
            if bad:
                raise ValueError(f"'routes' remove: route_id(s) not found: {sorted(bad)} (see route_table())")
            for rid, v in rc.get("add", {}).items():
                if isinstance(v, dict) and set(v) - {"zones", "mode"}:
                    raise ValueError(f"'routes' add '{rid}': use a zone list or {{'zones': [...], 'mode': '...'}}")
                zl = self._add_entry(v)[0]
                badz = set(zl) - set(self.names)
                if badz:
                    raise ValueError(f"'routes' add '{rid}': zone(s) not found: {sorted(badz)}")
                if len(set(zl)) < 2:
                    raise ValueError(f"'routes' add '{rid}': a route needs at least 2 zones")
        cats = set(self.fac["category"])
        for p, cl in settings.get("purpose_facilities", {}).items():
            if set(cl) - cats:
                raise ValueError(f"purpose '{p}': unknown facility categories {sorted(set(cl) - cats)}; "
                                 f"available: {sorted(cats)}")
        if any(v < 0 for v in settings.get("purpose_shares", {}).values()):
            raise ValueError("purpose_shares must be >= 0")
        zf = settings.get("zone_facilities", {})
        bad = set(zf) - set(self.names[self.INT])
        if bad:
            raise ValueError(f"'zone_facilities': Manila barangay(s) not found (check spelling in zones.csv): {sorted(bad)}")
        for zone, w in zf.items():
            if not isinstance(w, dict):
                raise ValueError(f"'zone_facilities' '{zone}': use {{category: number}}, e.g. {{'shop': 50}}")
            if set(w) - cats:
                raise ValueError(f"'zone_facilities' '{zone}': unknown categories {sorted(set(w) - cats)}; available: {sorted(cats)}")
            if any((not isinstance(v, (int, float))) or v < 0 for v in w.values()):
                raise ValueError(f"'zone_facilities' '{zone}': numbers of facilities must be >= 0")
        cw = settings.get("category_weights", {})
        if set(cw) - cats:
            raise ValueError(f"category_weights: unknown categories {sorted(set(cw) - cats)}; available: {sorted(cats)}")
        if any(v < 0 for v in cw.values()):
            raise ValueError("category_weights must be >= 0")
        if settings.get("route_exponent", 0) < 0 or any(v < 0 for v in settings.get("route_mode_weights", {}).values()):
            raise ValueError("route_exponent and route_mode_weights must be >= 0")
        if settings.get("route_exponent", 0) > 0 or settings.get("route_mode_weights"):
            self._need_routes()
        ex = settings.get("export", {})
        if set(ex) - set(EXPORT_DEFAULT):
            raise ValueError(f"Unknown export option(s): {sorted(set(ex) - set(EXPORT_DEFAULT))}")

    def attraction(self, cfg: dict, route_change: dict | None = None,
                   zone_facilities: dict | None = None) -> np.ndarray:
        """Manila attraction (Klinkhardt-style purpose shares x weighted facility size) x route factor + externals.
        Route factor = (1 + weighted routes through the barangay) ** route_exponent; Manila total unchanged."""
        f = self.fac
        floor = (f["area_m2"] * f["levels"].fillna(cfg["default_levels"]).clip(lower=1)).clip(upper=cfg["max_floor_m2"])
        size = floor ** cfg["size_exponent"] if cfg["size_exponent"] > 0 else pd.Series(1.0, index=f.index)
        cw = cfg.get("category_weights", {}) or {}
        if cw:
            size = size * f["category"].map(cw).fillna(1.0).to_numpy(float)
        rows, cats_, sizes = f["zone_row"].to_numpy(), f["category"].to_numpy(), np.asarray(size, float)
        if zone_facilities:                                             # per-barangay scenario: set counts
            sizes = sizes.copy()
            extra_r, extra_c, extra_s = [], [], []
            for zone, counts in zone_facilities.items():
                row = int(np.flatnonzero(self.names == zone)[0])
                for cat, n in counts.items():
                    here = (rows == row) & (cats_ == cat)
                    have = int(here.sum())
                    if have > 0:
                        sizes[here] *= n / have
                    elif n > 0:                                         # add facilities of median size
                        extra_r.append(row); extra_c.append(cat)
                        extra_s.append(n * float(np.median(np.asarray(size, float)[cats_ == cat])))
            if extra_r:
                rows = np.concatenate([rows, extra_r]); cats_ = np.concatenate([cats_, extra_c])
                sizes = np.concatenate([sizes, extra_s])
        n_int = int(self.INT.sum())
        S = (pd.DataFrame({"row": rows, "cat": cats_, "size": sizes})
             .pivot_table(index="row", columns="cat", values="size", aggfunc="sum")
             .reindex(index=np.flatnonzero(self.INT), fill_value=0).fillna(0))
        shares = {p: v for p, v in cfg["purpose_shares"].items() if v > 0}
        tot = sum(shares.values())
        idx = np.zeros(n_int)
        for p, v in shares.items():
            cols = [c for c in cfg["purpose_facilities"].get(p, []) if c in S.columns]
            s = S[cols].sum(axis=1).to_numpy(float) if cols else np.zeros(n_int)
            if s.sum() > 0:
                idx += (v / tot) * s / s.sum()
        idx = idx / idx.sum()
        idx = np.maximum(idx, cfg["min_share_of_avg"] / n_int)
        A = self.A_ext.copy()
        A[self.INT] = cfg["manila_attraction_total"] * idx / idx.sum()
        g = float(cfg.get("route_exponent", 0) or 0)
        if g > 0:
            self._need_routes()
            a = A[self.INT] * (1.0 + self.route_counts(cfg, route_change)[self.INT]) ** g
            A[self.INT] = cfg["manila_attraction_total"] * a / a.sum()
        return A

    def facility_counts(self, zones=None) -> pd.DataFrame:
        """Number of facilities per Manila barangay and category (from facilities.csv)."""
        t = pd.crosstab(self.fac["zone_row"], self.fac["category"]).reindex(np.flatnonzero(self.INT), fill_value=0)
        t.index = self.names[t.index]
        t.index.name = "zone"
        return t.loc[list(zones)] if zones is not None else t

    def cost(self, cfg: dict, hops: np.ndarray | None = None) -> np.ndarray:
        if not cfg["use_transfers"]:
            return self.T.copy()
        hops = self.hops if hops is None else hops
        transfers = np.clip(hops - 1, 0, cfg["max_transfers"])
        return self.T + cfg["pt_share"] * cfg["transfer_penalty_min"] * transfers

    def crossings(self, od: np.ndarray, cfg: dict) -> float:
        return float((od * self.X).sum() * (1 - cfg["walk_share"]))

    def calibrate(self, A, C, cfg, lo=1e-3, hi=1.0) -> float:
        """Find beta so that motorized Pasig crossings equal the screen-line target (bisection in log beta)."""
        f = lambda b: self.crossings(prod_constrained(self.P, A, C, b), cfg) - cfg["target_crossings"]
        if not f(hi) < 0 < f(lo):
            raise RuntimeError("Screen-line target outside the model's range — check the settings.")
        a, b = np.log(lo), np.log(hi)
        for _ in range(80):
            m = 0.5 * (a + b)
            a, b = (m, b) if f(np.exp(m)) > 0 else (a, m)
        return float(np.exp(0.5 * (a + b)))

    # ---- runs
    def base(self) -> "ODResult":
        if self._base_result is None:
            cfg = copy.deepcopy(self.base_config)
            A, C = self.attraction(cfg), self.cost(cfg)
            od = prod_constrained(self.P, A, C, cfg["beta"])
            self._base_result = ODResult(self, od, cfg, {}, A, cfg["beta"], recalibrated=False, base_od=None)
            self._base_C = C
        return self._base_result

    def run(self, settings: dict | str | None = None, recalibrate: bool | None = None) -> "ODResult":
        """settings: dict or path to a .json file with the changes from the base model."""
        if isinstance(settings, str):
            with open(settings) as fh:
                settings = json.load(fh)
        settings = dict(settings or {})
        self._check(settings)
        cfg = _merge(self.base_config, settings)
        weights_changed = bool(WEIGHT_KEYS & set(settings))
        if recalibrate is None:
            recalibrate = settings.get("recalibrate", weights_changed)
        base = self.base()
        # baseline for these weights (bundle routes); reused from the base when nothing changed (fast)
        A = self.attraction(cfg) if weights_changed else base.A.copy()
        C = self.cost(cfg) if weights_changed else self._base_C.copy()
        beta = self.calibrate(A, C, cfg) if recalibrate else cfg["beta"]
        route_attr = "routes" in settings and float(cfg.get("route_exponent", 0) or 0) > 0
        if "routes" in settings:                                       # route scenario on top
            C = self.cost(cfg, hops=self.hops_for(settings["routes"], cfg))
        if route_attr or settings.get("zone_facilities"):              # attraction scenarios on top
            A = self.attraction(cfg, route_change=settings["routes"] if route_attr else None,
                                zone_facilities=settings.get("zone_facilities"))
        for name, mult in cfg.get("attraction", {}).items():          # scenario on top
            A[self.names == name] *= mult
        for name, extra in cfg.get("extra_minutes", {}).items():
            j = np.flatnonzero(self.names == name)
            C[:, j] += extra
            C[j, j] -= extra
        od = prod_constrained(self.P, A, C, beta)
        return ODResult(self, od, cfg, settings, A, beta, recalibrated=recalibrate, base_od=base.od)


# --------------------------------------------------------------------------------------------- results
class ODResult:
    def __init__(self, model, od, cfg, changes, A, beta, recalibrated, base_od):
        self.model, self.od, self.cfg, self.changes = model, od, cfg, changes
        self.A, self.beta, self.recalibrated, self.base_od = A, beta, recalibrated, base_od

    def summary(self) -> dict:
        m, od, INT = self.model, self.od, self.model.INT
        return {"beta_per_min": self.beta, "recalibrated": bool(self.recalibrated),
                "total_trips": float(od.sum()), "pasig_crossings": m.crossings(od, self.cfg),
                "arrivals_in_manila": float(od[:, INT].sum()),
                "manila_trips_staying_in_manila": float(od[np.ix_(INT, INT)].sum() / od[INT].sum())}

    def compare(self) -> pd.DataFrame:
        """Arrivals per zone versus the base matrix."""
        base = self.base_od if self.base_od is not None else self.od
        z = self.model.zones
        df = pd.DataFrame({"zone": z["zone"], "district": z["district"], "kind": z["kind"],
                           "arrivals_base": base.sum(0).round(1), "arrivals": self.od.sum(0).round(1)})
        df["change"] = (df["arrivals"] - df["arrivals_base"]).round(1)
        df["change_pct"] = (100 * df["change"] / df["arrivals_base"].clip(lower=1)).round(2)
        return df

    def top_pairs(self, share=None, scope=None, zones=None, districts=None, selection_mode=None,
                  include_intrazonal=None, mode=None) -> pd.DataFrame:
        """OD pairs as a list with whole-number trips. Unset options use cfg['export']."""
        ex = {**EXPORT_DEFAULT, **self.cfg.get("export", {})}
        mode = mode or ex["mode"]
        share = 1.0 if mode == "full" else (share if share is not None else ex["top_share"])
        scope = "all" if mode == "full" and scope is None else (scope or ex["scope"])
        zones = ex["zones"] if zones is None else zones
        districts = ex["districts"] if districts is None else districts
        selection_mode = selection_mode or ex["selection_mode"]
        intra = True if mode == "full" else (ex["include_intrazonal"] if include_intrazonal is None else include_intrazonal)
        m, INT, n = self.model, self.model.INT, len(self.model.names)
        mask = {"touch_manila": INT[:, None] | INT[None, :], "within_manila": INT[:, None] & INT[None, :],
                "all": np.ones((n, n), bool)}[scope]
        sel = (m.zones["zone"].isin(zones) | m.zones["district"].isin(districts)).to_numpy()
        bad = (set(zones) - set(m.zones["zone"])) | (set(districts) - set(m.zones["district"]))
        if bad:
            raise ValueError(f"Selection not found: {sorted(bad)}")
        if sel.any():
            mask &= (sel[:, None] & sel[None, :]) if selection_mode == "within" else (sel[:, None] | sel[None, :])
        M = np.where(mask, self.od, 0.0)
        if not intra:
            np.fill_diagonal(M, 0)
        flat = M.ravel(); order = np.argsort(flat)[::-1]
        k = int((flat > 0).sum()) if share >= 1 else int(np.searchsorted(np.cumsum(flat[order]) / flat.sum(), share) + 1)
        keep = order[:k]
        kept = np.zeros(flat.size); kept[keep] = flat[keep]
        kept_int = integerize_rows(kept.reshape(M.shape))
        oi, di = np.unravel_index(keep, M.shape)
        df = pd.DataFrame({"origin": m.names[oi], "origin_row": oi, "destination": m.names[di], "dest_row": di,
                           "trips": flat[keep].round(2), "trips_int": kept_int[oi, di],
                           "driving_time_min": m.T[oi, di].round(1)})
        df["cum_share"] = (flat[keep].cumsum() / flat.sum()).round(4)
        return df

    def save(self, out_dir: str = OUTPUT_DIR, name: str = OUTPUT_NAME) -> str:
        """Writes <name>_od_matrix.npy, <name>_od_pairs.csv, <name>_arrivals_vs_base.csv, <name>_settings.json."""
        folder = os.path.join(out_dir, name)
        os.makedirs(folder, exist_ok=True)
        np.save(os.path.join(folder, f"{name}_od_matrix.npy"), self.od)
        self.top_pairs().to_csv(os.path.join(folder, f"{name}_od_pairs.csv"), index=False)
        self.compare().to_csv(os.path.join(folder, f"{name}_arrivals_vs_base.csv"), index=False)
        with open(os.path.join(folder, f"{name}_settings.json"), "w") as fh:
            json.dump({"name": name, "changes_from_base": self.changes, "settings_used": self.cfg,
                       "summary": self.summary()}, fh, indent=2, default=float)
        return folder


# --------------------------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="Produce base or adjusted Manila OD matrices.")
    ap.add_argument("--bundle", default=BUNDLE_DIR)
    ap.add_argument("--out", default=OUTPUT_DIR)
    ap.add_argument("--name", default=None, help="name used for the output files")
    ap.add_argument("--settings", default=None, help=".json file with changes from the base (omit for base)")
    args = ap.parse_args()

    model = ODModel.load(args.bundle)
    settings = args.settings if args.settings else (SETTINGS if args.name is None else {})
    if isinstance(settings, str):
        with open(settings) as fh:
            settings = json.load(fh)
    name = args.name or settings.get("name") or OUTPUT_NAME
    settings = {k: v for k, v in settings.items() if k != "name"}
    res = model.base() if not settings else model.run(settings)
    folder = res.save(args.out, name)
    s = res.summary()
    print(f"[{name}] beta={s['beta_per_min']:.4f} (recalibrated: {s['recalibrated']}) | "
          f"trips={s['total_trips']:,.0f} | Pasig crossings={s['pasig_crossings']:,.0f} | "
          f"arrivals in Manila={s['arrivals_in_manila']:,.0f}")
    print("saved to", folder)


if __name__ == "__main__":
    main()
