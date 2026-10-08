"""
calibrate_congestion.py — fit the background traffic (CONGESTION_BACKGROUND) so simulated private cars reach a
target speed, by running the simulation without a window.

Default target: TomTom Traffic Index 2025, Manila — 17.2 km/h morning rush hour (34 min 53 s per 10 km).
Evening rush hour: 13.8 km/h; all-day average: 18.9 km/h (31 min 45 s per 10 km).

    python tools/calibrate_congestion.py                       # 06:00-09:00 run, target 17.2 km/h
    python tools/calibrate_congestion.py --target 13.8 --start 17 --hours 3
    python tools/calibrate_congestion.py --transit-interval-scale 3   # test: transit dispatched 3x less often

It uses the config named by CONFIG_FILE_NAME (case, OD settings) and prints the background occupancy to put into
"CONGESTION_BACKGROUND" for those hours. Each try is a full run, so expect a few minutes. Calibration runs are
not logged as metrics runs (no folders in results/<case>/logs).
"""
import argparse
import os
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)                                    # config, sim_data and od_bundle paths are relative to the repo
sys.path.insert(0, str(ROOT))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

from dotenv import load_dotenv
load_dotenv()
import pygame as pg
pg.init()

import configuration as config
import simulation as sim_module
from transport import congestion


def run_once(background:float, args) -> tuple[float, float]:
    """One run with `background` in the simulated hours. Returns (private car km/h, all vehicles km/h)."""
    config.init()
    profile = list(config.get('CONGESTION_BACKGROUND', congestion.BACKGROUND))
    for hour in range(args.start, args.start + args.hours):
        profile[hour % 24] = background
    overrides = {'CONGESTION': True, 'CONGESTION_BACKGROUND': profile, 'SIM_START_HOUR': args.start,
                 'OD_DURATION_HOURS': args.hours}
    original_init = config.init

    def init_with_overrides(*a, **k):
        original_init(*a, **k)
        for key, value in overrides.items():
            config.put(key, value)
    config.init = init_with_overrides
    try:
        sim = sim_module.Simulation(headless=True)
    finally:
        config.init = original_init
    sim.metrics.finished = True               # calibration runs are not metrics runs: no log folders
    if args.transit_interval_scale != 1:
        for route in sim.routes:
            route.spawn_time = int(route.spawn_time * args.transit_interval_scale)
            route.peak_spawn = int(route.peak_spawn * args.transit_interval_scale)
    time, end = sim.start_time, sim.start_time + args.hours * 3600
    measure_from = sim.start_time + args.warmup
    sim.started = True
    measuring = False
    while time < end:
        hour = (time // 3600) % 24
        sim.peak_hour = (9 >= hour >= 6) or (20 >= hour >= 17)
        if not measuring and time >= measure_from:
            congestion.start_measure()
            measuring = True
        sim.handle_events(time)              # not sim.advance(): calibration runs are not logged as metrics runs
        time += sim.time_step
    car, everyone = congestion.measured_speed('private'), congestion.measured_speed()
    return (float(car) if car != '' else float('nan')), (float(everyone) if everyone != '' else float('nan'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', type=float, default=17.2, help='private car speed to reach, km/h')
    ap.add_argument('--start', type=int, default=6, help='first simulated hour')
    ap.add_argument('--hours', type=int, default=3, help='simulated hours')
    ap.add_argument('--warmup', type=int, default=1800, help='seconds at the start that are not measured')
    ap.add_argument('--iterations', type=int, default=6)
    ap.add_argument('--transit-interval-scale', type=float, default=1.0,
                    help='experiment only: multiply every transit dispatch interval (2 = half as many vehicles)')
    args = ap.parse_args()

    lo, hi = 0.0, 0.8
    car_lo, all_lo = run_once(lo, args)
    print(f"background {lo:.3f}: private cars {car_lo:.2f} km/h, all vehicles {all_lo:.2f} km/h", flush=True)
    if car_lo <= args.target:
        print(f"\nEven with no background traffic cars average {car_lo:.2f} km/h, at or below the target "
              f"{args.target} km/h: the simulated vehicles alone are congesting the network. Check the transit "
              f"schedule warnings in the log (links whose scheduled jeepneys/buses exceed capacity) before calibrating.")
        return
    best = (lo, car_lo)
    for _ in range(args.iterations):
        mid = (lo + hi) / 2
        car, everyone = run_once(mid, args)
        print(f"background {mid:.3f}: private cars {car:.2f} km/h, all vehicles {everyone:.2f} km/h", flush=True)
        if abs(car - args.target) < abs(best[1] - args.target):
            best = (mid, car)
        if car > args.target:
            lo = mid
        else:
            hi = mid
    print(f"\nUse background {best[0]:.3f} for hours {args.start:02d}-{(args.start + args.hours - 1) % 24:02d} "
          f"in CONGESTION_BACKGROUND (cars {best[1]:.2f} km/h vs target {args.target} km/h).")


if __name__ == '__main__':
    main()
