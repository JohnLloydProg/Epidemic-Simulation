import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import matplotlib.pyplot as plt
import pandas as pd
import math
from dotenv import load_dotenv
import numpy as np
from sklearn.metrics import (
    mean_squared_error,
    mean_absolute_error,
    r2_score
)
import os


# ---------------------------------------------------------------------------
# Validation constants
# (pulled out of compute_metrics() so the new run-convergence analysis can
#  reuse exactly the same numbers instead of redefining them)
# ---------------------------------------------------------------------------
REAL_POPULATION = 1846513          # DOH population
ASCERTAINMENT_RATE = 0.08
REPORTING_LAG_DAYS = -14           # fixed lag (days). Set to None to auto-detect via cross-correlation.
MAX_LAG = 30
SIM_START_DATE = "2021-02-15"      # timestep 0 of the simulation ~= this real-world calendar date


class DataPoint:
    susceptible: int
    exposed: int
    infected: int
    removed: int
    dead: int
    time: int

    def __init__(self, time, data: dict):
        self.susceptible = data["S"]
        self.exposed = data["E"]
        self.infected = data["I"]
        self.removed = data["R"]
        self.dead = data["D"]
        self.time = time


class DataPointAverage:
    _susceptible: list[int]
    _exposed: list[int]
    _infected: list[int]
    _removed: list[int]
    _dead: list[int]

    def __init__(self, point: DataPoint):
        self._susceptible = [point.susceptible]
        self._exposed = [point.exposed]
        self._infected = [point.infected]
        self._removed = [point.removed]
        self._dead = [point.dead]

    def add_point(self, point: DataPoint):
        self._susceptible.append(point.susceptible)
        self._exposed.append(point.exposed)
        self._infected.append(point.infected)
        self._removed.append(point.removed)
        self._dead.append(point.dead)

    @property
    def susceptible(self):
        if not self._susceptible:
            return 0
        return sum(self._susceptible) / len(self._susceptible)

    @property
    def exposed(self):
        if not self._exposed:
            return 0
        return sum(self._exposed) / len(self._exposed)

    @property
    def infected(self):
        if not self._infected:
            return 0
        return sum(self._infected) / len(self._infected)

    @property
    def removed(self):
        if not self._removed:
            return 0
        return sum(self._removed) / len(self._removed)

    @property
    def dead(self):
        if not self._dead:
            return 0
        return sum(self._dead) / len(self._dead)


# ---------------------------------------------------------------------------
# Real-world (DOH) data loading
# ---------------------------------------------------------------------------
def load_real_data():
    df_doh = pd.read_csv('manila_daily_confirmed.csv')

    df = (
        df_doh[["date", "TOTAL_CONFIRMED"]]
        .rename(columns={"TOTAL_CONFIRMED": "I"})
        .copy()
    )
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] >= SIM_START_DATE].reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Shared metric helpers
# ---------------------------------------------------------------------------
def infection_rate_series(counts, population, ascertainment_rate=1.0):
    """Cases per 100k population, optionally correcting for under-ascertainment."""
    return (np.asarray(counts, dtype=float) / ascertainment_rate) / population * 100000


def apply_lag(real, sim, lag_days):
    n = len(real)
    if lag_days > 0:
        return real[: n - lag_days], sim[lag_days:]
    elif lag_days < 0:
        lag = -lag_days
        return real[lag:], sim[: n - lag]
    return real, sim


def cross_correlation_at_lag(real, sim, max_lag):
    n = len(real)
    lags = range(-max_lag, max_lag + 1)
    scores = []

    for lag in lags:
        if lag < 0:
            r = real[-lag:]
            s = sim[: n + lag]
        elif lag > 0:
            r = real[: n - lag]
            s = sim[lag:]
        else:
            r = real
            s = sim

        if len(r) < 3:
            scores.append(np.nan)
            continue

        scores.append(np.corrcoef(r, s)[0, 1])

    scores = np.array(scores)
    best_idx = np.nanargmax(scores)
    return list(lags)[best_idx], scores[best_idx]


def compute_error_metrics(real_rate, sim_rate, lag_days):
    """Validation metrics between two aligned infection-rate series (per-100k)."""
    real, sim = apply_lag(real_rate, sim_rate, lag_days)

    mask = real != 0
    mape = np.mean(np.abs((real[mask] - sim[mask]) / real[mask])) * 100
    rmse = np.sqrt(mean_squared_error(real, sim))
    mae = mean_absolute_error(real, sim)
    r2 = r2_score(real, sim)
    pearson_r = np.corrcoef(real, sim)[0, 1]
    real_peak_idx = int(np.argmax(real))
    sim_peak_idx = int(np.argmax(sim))

    return {
        "MAPE": mape,
        "RMSE": rmse,
        "MAE": mae,
        "R2": r2,
        "Pearson": pearson_r,
        "peak_date_error": sim_peak_idx - real_peak_idx,
        "n": len(real),
    }


def dtw_distance(real, sim):
    n, m = len(real), len(sim)
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0.0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d = abs(real[i - 1] - sim[j - 1])
            cost[i, j] = d + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])

    raw_distance = cost[n, m]
    normalized_distance = raw_distance / (n + m)
    return raw_distance, normalized_distance


# ---------------------------------------------------------------------------
# Full-ensemble validation report (same numbers the original script printed,
# just wired to load_real_data()/sim_population instead of the undefined
# df_doh_plot / df_sim_plot globals)
# ---------------------------------------------------------------------------
def compute_metrics(df_average: pd.DataFrame, sim_population: float):
    df_real = load_real_data()
    df_sim = df_average[["timestep", "I"]].copy()

    n = min(len(df_real), len(df_sim))
    df_real = df_real.iloc[:n]
    df_sim = df_sim.iloc[:n]

    real_rate = infection_rate_series(df_real["I"].values, REAL_POPULATION, ASCERTAINMENT_RATE)
    sim_rate = infection_rate_series(df_sim["I"].values, sim_population)

    if REPORTING_LAG_DAYS is None:
        lag_used, lag_corr = cross_correlation_at_lag(real_rate, sim_rate, MAX_LAG)
        lag_source = "auto-detected via cross-correlation"
    else:
        lag_used, lag_source = REPORTING_LAG_DAYS, "manually set"
        _, lag_corr = cross_correlation_at_lag(real_rate, sim_rate, MAX_LAG)

    metrics = compute_error_metrics(real_rate, sim_rate, lag_used)
    real_aligned, sim_aligned = apply_lag(real_rate, sim_rate, lag_used)
    dtw_raw, dtw_normalized = dtw_distance(real_aligned, sim_aligned)

    print("=" * 40)
    print("Model Validation Results (full ensemble average)")
    print("=" * 40)
    print(f"Real Population        : {REAL_POPULATION:,}")
    print(f"Simulation Population  : {sim_population:,.0f}")
    print(f"Ascertainment Rate Used: {ASCERTAINMENT_RATE:.2%}")

    print()
    print("--- Reporting lag alignment ---")
    print(f"Lag applied         : {lag_used:+d} days ({lag_source})")
    print(f"Cross-corr @ that lag: {lag_corr:.4f}")
    print(f"Aligned series length: {metrics['n']} points (from {len(real_rate)} original)")

    print()
    print("--- Magnitude metrics (secondary) ---")
    print(f"RMSE    : {metrics['RMSE']:.8f}")
    print(f"MAE     : {metrics['MAE']:.8f}")
    print(f"MAPE    : {metrics['MAPE']:.2f}%")
    print(f"R²      : {metrics['R2']:.4f}")

    print()
    print("--- Shape / timing metrics (primary) ---")
    print(f"Pearson (post-alignment)  : {metrics['Pearson']:.4f}")
    print(f"Peak-date error            : {metrics['peak_date_error']:+d} days (post-alignment)")
    print(f"DTW distance (raw)        : {dtw_raw:.4f}")
    print(f"DTW distance (normalized) : {dtw_normalized:.6f}")

    return metrics


# ---------------------------------------------------------------------------
# NEW: percentage error as a function of the number of averaged runs
# ---------------------------------------------------------------------------
def compute_error_vs_num_runs(run_infected_series: list[pd.Series], run_populations: list[float],
                               lag_days=REPORTING_LAG_DAYS) -> pd.DataFrame:
    df_real = load_real_data()
    df_wide = pd.concat(run_infected_series, axis=1)
    df_wide.columns = [f"run_{i}" for i in range(len(run_infected_series))]
    df_wide = df_wide.sort_index()

    n_runs = df_wide.shape[1]
    rows = []

    for k in range(1, n_runs + 1):
        cum_avg_I = df_wide.iloc[:, :k].mean(axis=1, skipna=True).sort_index()

        n = min(len(df_real), len(cum_avg_I))
        real_counts = df_real["I"].values[:n]
        sim_counts = cum_avg_I.values[:n]

        sim_population = float(np.mean(run_populations[:k]))

        real_rate = infection_rate_series(real_counts, REAL_POPULATION, ASCERTAINMENT_RATE)
        sim_rate = infection_rate_series(sim_counts, sim_population)

        metrics = compute_error_metrics(real_rate, sim_rate, lag_days)
        if (k != 1):
            if rows[-1]["MAPE"] != 0:
                metrics["MAPE %"] = ((metrics["MAPE"] - rows[-1]["MAPE"]) / rows[-1]["MAPE"]) * 100
            else:
                metrics["MAPE %"] = 0

            if rows[-1]["RMSE"] != 0:
                metrics["RMSE %"] = ((metrics["RMSE"] - rows[-1]["RMSE"]) / rows[-1]["RMSE"]) * 100
            else:
                metrics["RMSE %"] = 0

            if rows[-1]["MAE"] != 0:
                metrics["MAE %"] = ((metrics["MAE"] - rows[-1]["MAE"]) / rows[-1]["MAE"]) * 100
            else:
                metrics["MAE %"] = 0
            
            if rows[-1]["R2"] != 0:
                metrics["R2 %"] = ((metrics["R2"] - rows[-1]["R2"]) / rows[-1]["R2"]) * 100
            else:
                metrics["R2 %"] = 0
            
            if rows[-1]["Pearson"] != 0:
                metrics["Pearson %"] = ((metrics["Pearson"] - rows[-1]["Pearson"]) / rows[-1]["Pearson"]) * 100
            else:
                metrics["Pearson %"] = 0
        
        rows.append({"num_runs": k, **metrics})

    return pd.DataFrame(rows)


def plot_error_vs_num_runs(df_error: pd.DataFrame):
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    fig.canvas.manager.set_window_title("Validation Error vs. Number of Averaged Runs")

    axes[0, 0].plot(df_error["num_runs"], df_error["MAPE"], marker="o", color="tab:red")
    axes[0, 0].set_title("MAPE (%)")
    axes[0, 0].set_xlabel("Number of Runs Averaged")
    axes[0, 0].set_ylabel("MAPE (%)")
    axes[0, 0].grid(True, alpha=0.3)

    axes[0, 1].plot(df_error["num_runs"], df_error["RMSE"], marker="o", color="tab:orange")
    axes[0, 1].set_title("RMSE")
    axes[0, 1].set_xlabel("Number of Runs Averaged")
    axes[0, 1].set_ylabel("RMSE (per 100k)")
    axes[0, 1].grid(True, alpha=0.3)

    axes[1, 0].plot(df_error["num_runs"], df_error["R2"], marker="o", color="tab:blue")
    axes[1, 0].set_title("R²")
    axes[1, 0].set_xlabel("Number of Runs Averaged")
    axes[1, 0].set_ylabel("R²")
    axes[1, 0].grid(True, alpha=0.3)

    axes[1, 1].plot(df_error["num_runs"], df_error["Pearson"], marker="o", color="tab:green")
    axes[1, 1].set_title("Pearson r")
    axes[1, 1].set_xlabel("Number of Runs Averaged")
    axes[1, 1].set_ylabel("Pearson r")
    axes[1, 1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    load_dotenv()
    cred = credentials.Certificate(os.environ['CERT_FILE_NAME'])
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    sim_groups = db.collections()
    sim_groups_dict = {}

    for i, sim_group in enumerate(sim_groups):
        print(f'[{i}] {sim_group.id}')
        sim_groups_dict[i] = sim_group.id
    simulation_group = int(input("Enter the simulation group name: "))

    collection = db.collection(sim_groups_dict[simulation_group])
    docs = collection.list_documents()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.canvas.manager.set_window_title(collection.id)

    average_cases: dict[int, DataPointAverage] = {}

    # NEW: keep each run's own I(t) series + its t=0 population, so we can
    # re-average over an increasing number of runs afterwards.
    run_infected_series: list[pd.Series] = []
    run_populations: list[float] = []

    for sim in docs:
        print(f"Simulation ID: {sim.id}")
        sim_ref = collection.document(sim.id)
        sim_data = sim_ref.get().to_dict()
        active_cases: list[DataPoint] = []
        for time, data in sim_data.items():
            active_cases.append(DataPoint(int(time), data))

        active_cases.sort(key=lambda d: d.time)

        x_active = []
        y_active = []

        for case in active_cases:
            x_active.append(case.time)
            y_active.append(case.infected)
            if case.time not in average_cases:
                average_cases[case.time] = DataPointAverage(case)
            else:
                average_cases[case.time].add_point(case)
        ax1.plot(x_active, y_active, marker='o', linestyle='-', label=sim.id)

        # NEW: this run's infected series (indexed by timestep) + its starting population
        run_series = pd.Series({c.time: c.infected for c in active_cases}, name=sim.id)
        run_infected_series.append(run_series)
        t0 = active_cases[0]
        run_populations.append(t0.susceptible + t0.exposed + t0.infected + t0.removed + t0.dead)

    # sorted so downstream dataframes/plots are in chronological order
    # (the original script used dict insertion order here, which isn't
    # guaranteed to be time-sorted once more than one run is involved)
    ave_x = sorted(average_cases.keys())

    ave_y = [average_cases[t].exposed for t in ave_x]
    ax2.plot(ave_x, ave_y, label="Exposed")

    ave_y = [average_cases[t].infected for t in ave_x]
    ax2.plot(ave_x, ave_y, label="Infected")

    ave_y = [average_cases[t].removed for t in ave_x]
    ax2.plot(ave_x, ave_y, label="Removed")

    # --- Graph formatting ---
    ax1.set_title('Simulation Disease Spread (Line)')
    ax1.set_ylabel('Active Cases')
    ax1.set_xlabel('Days')

    ax2.set_title('Average Simulation Disease Spread (Line)')
    ax2.set_ylabel('Active Cases')
    ax2.set_xlabel('Days')
    ax2.legend()

    plt.tight_layout()
    plt.show()

    # ---- full-ensemble validation report ----
    df_average = pd.DataFrame({
        "timestep": ave_x,
        "S": [average_cases[t].susceptible for t in ave_x],
        "E": [average_cases[t].exposed for t in ave_x],
        "I": [average_cases[t].infected for t in ave_x],
        "R": [average_cases[t].removed for t in ave_x],
        "D": [average_cases[t].dead for t in ave_x],
    })

    sim_population = df_average.iloc[0][["S", "E", "I", "R", "D"]].sum()
    compute_metrics(df_average, sim_population)

    # ---- NEW: percentage error as the number of averaged runs increases ----
    df_error_vs_runs = compute_error_vs_num_runs(run_infected_series, run_populations)
    print()
    print("=" * 40)
    print("Validation error vs. number of averaged runs")
    print("=" * 40)
    print(df_error_vs_runs.to_string(index=False))

    plot_error_vs_num_runs(df_error_vs_runs)