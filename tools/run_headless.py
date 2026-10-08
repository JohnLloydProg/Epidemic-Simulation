"""
run_headless.py — run full logged simulations without the window (much faster than the interactive program).

    python tools/run_headless.py                                  # the config's case and OD_SEED, 24 simulated hours
    python tools/run_headless.py --case 01_scenarioA.json --seeds 1 2 3 4 5
    python tools/run_headless.py --hours 6

Run it from the repository folder, with the same CONFIG_FILE_NAME (.env) as the program. Each seed is one run,
logged exactly like a run in the program (metrics.py) to sim_data/results/<case_id>/logs/<timestamp>_seed<N>/.
Use the same seeds for a control case and its intervention cases so each pair differs only by the intervention.
Hotspots, closures and other edits come from the case file (make and save them in the program first).
"""
import argparse
import os
import sys
import time as clock_time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)                                    # config, sim_data and od_bundle paths are relative to the repo
sys.path.insert(0, str(ROOT))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')  # no window
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--case', default=None, help='case file in sim_data/cases (default: the config\'s CASE_FILE)')
    ap.add_argument('--seeds', type=int, nargs='+', default=None, help='OD seeds, one run each (default: OD_SEED)')
    ap.add_argument('--hours', type=float, default=None, help='simulated hours per run (default: METRICS_RUN_HOURS or 24)')
    args = ap.parse_args()

    from simulation import Simulation
    import configuration as config

    sim = Simulation(headless=True)
    if args.case:
        sim.open_case(args.case)
    if args.hours:
        sim.metrics.run_hours = args.hours
    seeds = args.seeds or [config.get('OD_SEED', 42)]
    folders = []
    for seed in seeds:
        config.put('OD_SEED', seed)
        sim.reset()                               # re-queues the routes and the OD agents for this seed
        print(f"=== {sim.metrics._case_id()} seed {seed}: {sim.metrics.run_hours:g} simulated hours ===", flush=True)
        started = clock_time.time()
        time = sim.start_time
        while not sim.advance(time):
            time += sim.time_step
        folders.append(sim.metrics.folder)
        print(f"=== seed {seed} done in {(clock_time.time() - started) / 60:.1f} min -> {sim.metrics.folder}", flush=True)
    print('\nLog folders:')
    for folder in folders:
        print(' ', folder)


if __name__ == '__main__':
    main()
