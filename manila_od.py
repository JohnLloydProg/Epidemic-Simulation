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
Scenario (interventions -> beta stays at the base value by default):
  "attraction":     {"Barangay 669": 0.5}     multiplier on a zone's attraction (0 = closed)
  "extra_minutes":  {"Barangay 306": 10}      extra minutes to reach a zone
Calibration:
  "recalibrate":    true / false               override the automatic choice above
Export (what .save() writes as the pairs CSV):
  "export": {"mode": "top" | "full", "top_share": 0.8, "scope": "touch_manila" | "within_manila" | "all",
             "zones": [...], "districts": [...], "selection_mode": "within" | "touch",
             "include_intrazonal": false}
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
    # "export": {"mode": "top", "top_share": 0.8, "districts": ["Port Area", "Ermita", "Malate"]},
}
# =============================================================================================

WEIGHT_KEYS = {"purpose_shares", "purpose_facilities", "size_exponent", "max_floor_m2", "default_levels",
               "min_share_of_avg", "use_transfers", "transfer_penalty_min", "pt_share", "max_transfers"}
SCENARIO_KEYS = {"attraction", "extra_minutes"}
OTHER_KEYS = {"recalibrate", "export", "name"}
EXPORT_DEFAULT = {"mode": "top", "top_share": 0.8, "scope": "touch_manila", "zones": [], "districts": [],
                  "selection_mode": "within", "include_intrazonal": False}


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


def _merge(base: dict, changes: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in changes.items():
        if k in ("purpose_shares", "purpose_facilities", "export") and isinstance(v, dict):
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
        self._base_result = None

    @classmethod
    def load(cls, bundle_dir: str = BUNDLE_DIR) -> "ODModel":
        return cls(bundle_dir)

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
        cats = set(self.fac["category"])
        for p, cl in settings.get("purpose_facilities", {}).items():
            if set(cl) - cats:
                raise ValueError(f"purpose '{p}': unknown facility categories {sorted(set(cl) - cats)}; "
                                 f"available: {sorted(cats)}")
        if any(v < 0 for v in settings.get("purpose_shares", {}).values()):
            raise ValueError("purpose_shares must be >= 0")
        ex = settings.get("export", {})
        if set(ex) - set(EXPORT_DEFAULT):
            raise ValueError(f"Unknown export option(s): {sorted(set(ex) - set(EXPORT_DEFAULT))}")

    def attraction(self, cfg: dict) -> np.ndarray:
        """Manila attraction (Klinkhardt-style purpose shares x facility size) + external zones."""
        f = self.fac
        floor = (f["area_m2"] * f["levels"].fillna(cfg["default_levels"]).clip(lower=1)).clip(upper=cfg["max_floor_m2"])
        size = floor ** cfg["size_exponent"] if cfg["size_exponent"] > 0 else pd.Series(1.0, index=f.index)
        n_int = int(self.INT.sum())
        S = (pd.DataFrame({"row": f["zone_row"], "cat": f["category"], "size": size})
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
        return A

    def cost(self, cfg: dict) -> np.ndarray:
        if not cfg["use_transfers"]:
            return self.T.copy()
        transfers = np.clip(self.hops - 1, 0, cfg["max_transfers"])
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
        A, C = self.attraction(cfg), self.cost(cfg)
        beta = self.calibrate(A, C, cfg) if recalibrate else cfg["beta"]
        for name, mult in cfg.get("attraction", {}).items():          # scenario on top
            A[self.names == name] *= mult
        for name, extra in cfg.get("extra_minutes", {}).items():
            j = np.flatnonzero(self.names == name)
            C[:, j] += extra
            C[j, j] -= extra
        od = prod_constrained(self.P, A, C, beta)
        return ODResult(self, od, cfg, settings, A, beta, recalibrated=recalibrate, base_od=self.base().od)


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
