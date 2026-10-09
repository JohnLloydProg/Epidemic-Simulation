"""
make_hotspot_cases.py — rank the study barangays and write the three hotspot scenario cases.

    scenario1_HighOrigin_baseline        top k by daily trips produced   (base OD matrix, row sums)
    scenario2_HighDestination_baseline   top k by daily trips attracted  (base OD matrix, column sums)
    scenario3_HighThrough_baseline       top k by through / trip_ends from the reference runs of 00_baseline
                                         (zones.csv of every run in sim_data/results/00_baseline/logs/, averaged;
                                          only zones with at least the median through count are ranked, so a
                                          tiny barangay with almost no trip ends cannot win on the ratio alone)

Each case is a copy of 00_baseline with only case_id, description, hotspots and hotspot_attraction (1.0) changed,
so the scenario baselines differ only in WHERE exposure is measured; interventions are added by build_cases.py.

    python tools/make_hotspot_cases.py              # scenarios 1 and 2 now; 3 once 00_baseline has been run
    python tools/make_hotspot_cases.py --share 0.2 --roles od connector

--roles must match OD_TRIP_END_ROLES in the config: a barangay that is not a trip end cannot be a high-origin or
high-destination hotspot. Also writes sim_data/results/hotspot_rankings.csv with every measure and the picks.
"""
from __future__ import annotations
import argparse, csv, json, math, sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from manila_od import ODModel

SCENARIOS = {
    'A': ('scenario1_HighOrigin_baseline',
          'Scenario 1 (high-origin), baseline: top {k} barangays by daily trips produced (base OD matrix)'),
    'B': ('scenario2_HighDestination_baseline',
          'Scenario 2 (high-destination), baseline: top {k} barangays by daily trips attracted (base OD matrix)'),
    'C': ('scenario3_HighThrough_baseline',
          'Scenario 3 (high through-traffic), baseline: top {k} barangays by people passing through per trip end '
          '(mean of {n} reference run(s) of {ref})'),
}


def reference_runs(data: Path, reference: str) -> list[Path]:
    """zones.csv of every finished reference run that has the through-traffic columns."""
    found = []
    for path in sorted((data / 'results' / reference / 'logs').glob('*/zones.csv')):
        with open(path, encoding='utf-8') as f:
            if 'through' in (csv.DictReader(f).fieldnames or []):
                found.append(path)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default='sim_data')
    ap.add_argument('--bundle', default='od_bundle')
    ap.add_argument('--reference', default='00_baseline', help='case the scenarios copy, and whose runs rank scenario 3')
    ap.add_argument('--roles', nargs='+', default=['od', 'connector'])
    ap.add_argument('--share', type=float, default=0.20, help='share of the barangays made hotspots')
    ap.add_argument('--k', type=int, default=None, help='exact number of hotspots (overrides --share)')
    ap.add_argument('--intrazonal', action='store_true', help='count trips within one barangay (OD_INTRAZONAL)')
    args = ap.parse_args()

    data = ROOT / args.data
    zones = [z for z in json.load(open(data / 'base' / 'zones.json', encoding='utf-8'))['zones']
             if z['role'] in args.roles]
    k = args.k or math.ceil(args.share * len(zones))
    template = json.load(open(data / 'cases' / f'{args.reference}.json', encoding='utf-8'))

    model = ODModel.load(str(ROOT / args.bundle))
    od = model.base().od.copy()
    if not args.intrazonal:
        np.fill_diagonal(od, 0)
    row = {n: i for i, n in enumerate(model.names)}
    table = {z['name']: {'zone': z['name'], 'psgc': str(z['psgc']), 'role': z['role'],
                         'produced': float(od[row[z['name']]].sum()),
                         'attracted': float(od[:, row[z['name']]].sum())} for z in zones}

    runs = reference_runs(data, args.reference)
    for path in runs:
        for r in csv.DictReader(open(path, encoding='utf-8')):
            if r['zone'] in table:
                t = table[r['zone']]
                t['through'] = t.get('through', 0) + int(r['through']) / len(runs)
                t['trip_ends'] = t.get('trip_ends', 0) + int(r['trip_ends']) / len(runs)
    for t in table.values():
        if runs:
            t.setdefault('through', 0.0); t.setdefault('trip_ends', 0.0)
            t['ratio'] = t['through'] / max(t['trip_ends'], 1)

    picks = {'A': sorted(table, key=lambda n: -table[n]['produced'])[:k],
             'B': sorted(table, key=lambda n: -table[n]['attracted'])[:k]}
    if runs:
        floor = float(np.median([t['through'] for t in table.values()]))
        eligible = [n for n in table if table[n]['through'] >= floor]
        picks['C'] = sorted(eligible, key=lambda n: -table[n]['ratio'])[:k]
    else:
        print(f"Scenario 3 skipped: no finished {args.reference} run with through-traffic in "
              f"{data / 'results' / args.reference / 'logs'}. Run it first:\n"
              f"    python tools/run_headless.py --case {args.reference}.json --seeds 1 2 3")

    for s, names in picks.items():
        case_id, text = SCENARIOS[s]
        case = {**template, 'case_id': case_id,
                'description': text.format(k=k, n=len(runs), ref=args.reference) + ': ' + ', '.join(names),
                'hotspots': sorted(table[n]['psgc'] for n in names),
                'hotspot_attraction': 1.0}
        with open(data / 'cases' / f'{case_id}.json', 'w', encoding='utf-8') as f:
            json.dump(case, f, indent=1)
        print(f"{case_id}: {', '.join(names)}")

    out = data / 'results' / 'hotspot_rankings.csv'
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ['zone', 'psgc', 'role', 'produced', 'attracted', 'through', 'trip_ends', 'ratio',
            'scenario1', 'scenario2', 'scenario3']
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for n in sorted(table, key=lambda n: -table[n]['produced']):
            t = table[n]
            w.writerow({**t, **{c: round(t[c], 2) for c in ('produced', 'attracted', 'through', 'trip_ends', 'ratio')
                                if c in t},
                        'scenario1': int(n in picks['A']), 'scenario2': int(n in picks['B']),
                        'scenario3': int(n in picks.get('C', []))})
    print(f"k = {k} of {len(zones)} barangays | rankings -> {out}")


if __name__ == '__main__':
    main()
