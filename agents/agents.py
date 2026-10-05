"""
agents.py — turn a manila_od OD result into individual agents for the simulation.

Each agent = one representative trip with origin, destination, departure time and start/end
coordinates (EPSG:32651 metres, same as zones.csv). One agent stands for `trips_per_agent` real trips.

    from manila_od import ODModel
    from agents import make_agents, world_to_screen

    m = ODModel.load("od_bundle")
    run = m.run({"attraction": {"Barangay 669": 0.5}})
    pairs = run.top_pairs(mode="full", districts=["Port Area", "Ermita", "Malate"], selection_mode="touch")
    agents = make_agents(pairs, m.zones, trips_per_agent=100, method="sample", jitter_m=150, seed=42)
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def zone_xy(zones: pd.DataFrame) -> np.ndarray:
    """(n_zones, 2) array of zone point coordinates from zones.csv (x/y columns or a WKT 'POINT (x y)' column)."""
    cols = {c.lower(): c for c in zones.columns}
    for xc, yc in (("x", "y"), ("point_x", "point_y"), ("easting", "northing")):
        if xc in cols and yc in cols:
            return zones[[cols[xc], cols[yc]]].to_numpy(float)
    for c in ("point", "geometry", "wkt"):
        if c in cols:
            xy = zones[cols[c]].astype(str).str.extract(r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)")
            if xy.notna().all().all():
                return xy.astype(float).to_numpy()
    raise ValueError(f"No point coordinates found in zones.csv; columns are {list(zones.columns)}")


def agent_counts(trips: np.ndarray, trips_per_agent: float, method: str, rng) -> np.ndarray:
    """How many agents each OD pair gets."""
    expected = trips / trips_per_agent
    total = int(round(expected.sum()))
    if method == "round":            # deterministic; small pairs (< ~0.5 agent) tend to vanish
        n = np.floor(expected).astype(np.int64)
        short = total - n.sum()
        if short > 0:
            n[np.argsort(-(expected - n))[:short]] += 1
        return n
    if method == "sample":           # random; every pair keeps a chance proportional to its trips
        return rng.multinomial(total, expected / expected.sum())
    raise ValueError("method must be 'round' or 'sample'")


def make_agents(pairs: pd.DataFrame, zones: pd.DataFrame, trips_per_agent: float = 100,
                method: str = "sample", hour_profile=None, start_hour: int = 0,
                jitter_m: float = 0.0, mode_shares: dict | None = None, seed: int = 0) -> pd.DataFrame:
    """
    pairs:          output of ODResult.top_pairs(...) (needs origin_row, dest_row, trips)
    trips_per_agent scale: 1 agent = this many daily trips
    method:         'sample' (multinomial, random) or 'round' (largest remainder, deterministic)
    hour_profile:   weights per hour starting at start_hour, e.g. 24 values for a full day.
                    None = uniform. Replace with an observed time-of-day profile for your study area.
    jitter_m:       random offset (metres) around zone points so agents don't stack on one pixel
    mode_shares:    optional {"private": 0.3, "public": 0.7}; random assignment, placeholder for a real mode-choice model
    """
    rng = np.random.default_rng(seed)
    n = agent_counts(pairs["trips"].to_numpy(float), trips_per_agent, method, rng)
    idx = np.repeat(np.arange(len(pairs)), n)
    k = idx.size
    o = pairs["origin_row"].to_numpy()[idx]
    d = pairs["dest_row"].to_numpy()[idx]

    xy = zone_xy(zones)
    oxy, dxy = xy[o].copy(), xy[d].copy()
    if jitter_m > 0:
        oxy += rng.normal(0, jitter_m, (k, 2))
        dxy += rng.normal(0, jitter_m, (k, 2))

    p = np.ones(24) if hour_profile is None else np.asarray(hour_profile, float)
    hours = rng.choice(p.size, size=k, p=p / p.sum())
    depart_min = (start_hour + hours) * 60 + rng.uniform(0, 60, k)        # minutes after midnight

    names = zones["zone"].astype(str).to_numpy()
    df = pd.DataFrame({"origin": names[o], "origin_row": o, "destination": names[d], "dest_row": d,
                       "depart_min": depart_min.round(2),
                       "ox": oxy[:, 0], "oy": oxy[:, 1], "dx": dxy[:, 0], "dy": dxy[:, 1],
                       "trips_represented": trips_per_agent})
    if "driving_time_min" in pairs:
        df["driving_time_min"] = pairs["driving_time_min"].to_numpy()[idx]
    if mode_shares:
        keys = list(mode_shares)
        w = np.array([mode_shares[x] for x in keys], float)
        df["mode"] = rng.choice(keys, size=k, p=w / w.sum())
    df = df.sort_values("depart_min", kind="stable").reset_index(drop=True)
    df.insert(0, "agent_id", np.arange(len(df)))
    return df


def world_to_screen(x, y, bounds, size, margin=20):
    """Map EPSG:32651 metres to Pygame pixels. bounds = (xmin, ymin, xmax, ymax); size = (width, height)."""
    xmin, ymin, xmax, ymax = bounds
    w, h = size
    s = min((w - 2 * margin) / (xmax - xmin), (h - 2 * margin) / (ymax - ymin))
    sx = margin + (np.asarray(x) - xmin) * s
    sy = h - margin - (np.asarray(y) - ymin) * s          # flip: screen y grows downward
    return sx, sy


if __name__ == "__main__":
    from manila_od import ODModel
    m = ODModel.load("od_bundle")
    pairs = m.base().top_pairs(mode="full", scope="touch_manila")
    agents = make_agents(pairs, m.zones, trips_per_agent=100, method="sample", jitter_m=150, seed=42)
    print(agents.head())
    print("agents:", len(agents), "| trips represented:", int(agents["trips_represented"].sum()),
          "| trips in pairs:", round(pairs["trips"].sum()))
    agents.to_csv("agents_base.csv", index=False)