"""
run_experiments.py — run the experiment cases one after another, headless, and pick up where it left off.

    python tools/run_experiments.py --list                 # the plan and what is already done; runs nothing
    python tools/run_experiments.py                        # every run in sim_data/experiments.json not done yet
    python tools/run_experiments.py --only scenario1       # only runs whose name contains "scenario1"
    python tools/run_experiments.py --part 1/3             # device 1 of 3 takes every 3rd run, starting with the 1st
    python tools/run_experiments.py --seeds 1 2 3          # override the seeds in experiments.json
    python tools/run_experiments.py --hours 1              # override the run length (quick tests)

Run it from the repository folder with the same CONFIG_FILE_NAME (.env) as the program.

WHAT IS RUN
sim_data/experiments.json lists the runs (case names, without .json), the seeds and the run length:
    {"seeds": [1], "hours": 24, "runs": ["00_baseline", "scenario1_HighOrigin_baseline", ...]}
Every run x seed is one simulation. Each is started as its own process (tools/run_headless.py), so a crash, or
running out of memory, only loses that one simulation; the next one starts normally.

DONE / RESUMING
A run x seed counts as done when its log folder (sim_data/results/<case>/logs/<time>_seed<N>/) has a summary.json
that covers the full run length. Done runs are skipped, so after a crash or a stop (Ctrl+C) just start the same
command again. A run that failed leaves a partial log folder; it is renamed <folder>_INCOMPLETE so it is never
mistaken for a result, and the run is done again next time. --rerun runs everything again (new log folders).

MULTIPLE DEVICES
Give each device the same code, config and sim_data/cases/, and a different --part (1/2 and 2/2, or 1/3, 2/3 and
3/3) or --only filter. Then copy every device's sim_data/results/<case>/ folders onto one machine; the folder names
never collide (each has its own timestamp). Every finished or failed simulation is also added to
sim_data/results/experiments_log.csv with the device name, times and status.
Order matters for the first steps: 00_baseline must be finished, and tools/make_hotspot_cases.py and
tools/build_cases.py run once, before the scenario cases exist — then share the same sim_data/cases/ files.
"""
from __future__ import annotations
import argparse, csv, json, os, platform, subprocess, sys, time as clock
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'sim_data'


def stamp() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def say(text: str):
    print(f"[{stamp()}] {text}", flush=True)


def case_id_of(name: str) -> str | None:
    path = DATA / 'cases' / f'{name}.json'
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f).get('case_id', name)


def seed_folders(case_id: str, seed: int) -> list[Path]:
    logs = DATA / 'results' / case_id / 'logs'
    return sorted(p for p in logs.glob(f'*_seed{seed}') if p.is_dir()) if logs.exists() else []


def finished_folder(case_id: str, seed: int, hours: float | None) -> Path | None:
    """The newest log folder of this case and seed whose summary.json covers the whole run."""
    for folder in reversed(seed_folders(case_id, seed)):
        summary = folder / 'summary.json'
        if not summary.exists():
            continue
        try:
            s = json.load(open(summary, encoding='utf-8'))
        except (OSError, ValueError):
            continue
        want = float(hours if hours is not None else s.get('run_hours', 24))
        if 'snapshot' not in str(s.get('ended_by', '')) and float(s.get('simulated_hours', 0)) >= want - 1e-6:
            return folder
    return None


def record(row: dict):
    path = DATA / 'results' / 'experiments_log.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['device', 'case', 'seed', 'started', 'finished', 'minutes', 'status',
                                          'log_folder'])
        if new:
            w.writeheader()
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan', default=str(DATA / 'experiments.json'), help='experiment list (default sim_data/experiments.json)')
    ap.add_argument('--only', nargs='+', help='only runs whose name contains one of these texts')
    ap.add_argument('--part', help='K/N: this device runs every N-th simulation, starting with the K-th')
    ap.add_argument('--seeds', type=int, nargs='+', help='seeds (overrides the plan)')
    ap.add_argument('--hours', type=float, help='simulated hours per run (overrides the plan)')
    ap.add_argument('--rerun', action='store_true', help='run again even if already done')
    ap.add_argument('--list', action='store_true', help='show the plan and its status, run nothing')
    args = ap.parse_args()

    plan = json.load(open(args.plan, encoding='utf-8'))
    seeds = args.seeds or plan.get('seeds') or [1]
    hours = args.hours if args.hours is not None else plan.get('hours')
    names = plan['runs']
    if args.only:
        names = [n for n in names if any(t in n for t in args.only)]
    jobs = [(n, s) for n in names for s in seeds]
    if args.part:
        k, n = (int(x) for x in args.part.split('/'))
        if not 1 <= k <= n:
            sys.exit('--part must be K/N with 1 <= K <= N')
        jobs = jobs[k - 1::n]
    device = platform.node() or 'device'

    # ---- status of every job
    rows = []
    for name, seed in jobs:
        cid = case_id_of(name)
        done = finished_folder(cid, seed, hours) if cid else None
        rows.append((name, seed, cid, done))
    todo = [r for r in rows if r[2] and (args.rerun or not r[3])]
    missing = [r for r in rows if not r[2]]

    say(f"Device {device}: {len(jobs)} simulation(s) in this selection — {sum(1 for r in rows if r[3])} done, "
        f"{len(todo)} to run, {len(missing)} without a case file | seeds {seeds}, "
        f"{hours if hours is not None else 'config'} simulated hours each")
    for name, seed, cid, done in rows:
        state = ('MISSING CASE FILE' if not cid else 'done' if done and not args.rerun else 'to run')
        print(f"    {state:<18} {name}  seed {seed}" + (f"  -> {done.relative_to(ROOT)}" if done and not args.rerun else ''))
    if missing:
        print("    (missing cases: run tools/make_hotspot_cases.py and tools/build_cases.py first, "
              "or copy sim_data/cases/ from the device that made them)")
    if args.list or not todo:
        return

    # ---- run
    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
    total_start = clock.time()
    results = []
    for i, (name, seed, cid, _) in enumerate(todo, 1):
        before = set(seed_folders(cid, seed))
        started = stamp()
        t0 = clock.time()
        say(f"START  [{i}/{len(todo)}] {name}  seed {seed}")
        cmd = [sys.executable, str(ROOT / 'tools' / 'run_headless.py'), '--case', f'{name}.json', '--seeds', str(seed)]
        if hours is not None:
            cmd += ['--hours', str(hours)]
        try:
            code = subprocess.call(cmd, cwd=ROOT, env=env)
        except KeyboardInterrupt:
            code = 'stopped'
        minutes = (clock.time() - t0) / 60
        folder = finished_folder(cid, seed, hours)
        ok = code == 0 and folder is not None and folder not in before
        if not ok:                                      # mark the partial folder(s) of this attempt
            for f in set(seed_folders(cid, seed)) - before:
                if f != folder:
                    f.rename(f.with_name(f.name + '_INCOMPLETE'))
            folder = None
        status = 'done' if ok else ('stopped' if code == 'stopped' else f'FAILED (exit {code})')
        say(f"FINISH [{i}/{len(todo)}] {name}  seed {seed}: {status} in {minutes:.1f} min"
            + (f" -> {folder.relative_to(ROOT)}" if folder else ''))
        record({'device': device, 'case': name, 'seed': seed, 'started': started, 'finished': stamp(),
                'minutes': round(minutes, 1), 'status': status,
                'log_folder': str(folder.relative_to(ROOT)) if folder else ''})
        results.append((name, seed, status))
        if code == 'stopped':
            say("Stopped by Ctrl+C. Start the same command again to continue with the remaining runs.")
            break

    failed = [r for r in results if r[2] != 'done']
    say(f"All done in {(clock.time() - total_start) / 60:.1f} min: {len(results) - len(failed)} finished, "
        f"{len(failed)} failed or stopped" + (" — run the same command again to retry them" if failed else ""))
    for name, seed, status in failed:
        print(f"    {status}: {name} seed {seed}")


if __name__ == '__main__':
    main()
