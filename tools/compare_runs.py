"""
compare_runs.py — compare a control run with an intervention run (displacement and the other metrics).

    python tools/compare_runs.py <control_metrics_folder> <intervention_metrics_folder> [--out compare.csv]

Both folders are run log folders written by metrics.py (sim_data/results/<case_id>/logs/<timestamp>_seed<N>/).
Use runs with the same OD_SEED, start hour and length so the difference comes from the intervention.

Prints:
  * the hotspot metrics side by side (person-minutes, peak, minutes above threshold, trips ending there,
    people who entered) and the travel time by mode / completed trips
  * displacement: the zones that gained the most person-minutes and visitors, and any zone that was below the
    threshold in the control run but crosses it in the intervention run (a possible new hotspot)
Writes a per-zone CSV with the control value, the intervention value and the change for every zone metric.
"""
import argparse
import csv
import json
from pathlib import Path

ZONE_FIELDS = ['person_minutes', 'peak', 'minutes_above_threshold', 'visitors', 'arrivals']


def load(folder:Path):
    with open(folder / 'summary.json', encoding='utf-8') as f:
        summary = json.load(f)
    with open(folder / 'zones.csv', encoding='utf-8') as f:
        zones = {row['zone']: row for row in csv.DictReader(f)}
    return summary, zones


def pct(a:float, b:float) -> str:
    return f"{100 * (b - a) / a:+.1f}%" if a else ('n/a' if not b else '+new')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('control', type=Path)
    ap.add_argument('intervention', type=Path)
    ap.add_argument('--out', type=Path, default=None, help='per-zone CSV (default: <intervention>/compare_vs_control.csv)')
    ap.add_argument('--top', type=int, default=5, help='how many gaining zones to list')
    args = ap.parse_args()

    s0, z0 = load(args.control)
    s1, z1 = load(args.intervention)
    for key in ('od_seed', 'sim_start', 'simulated_hours'):
        if s0.get(key) != s1.get(key):
            print(f"WARNING: {key} differs ({s0.get(key)} vs {s1.get(key)}) - the runs are not directly comparable.")
    hot = s0['hotspots'] or s1['hotspots']
    if s0['hotspots'] != s1['hotspots']:
        print(f"WARNING: different hotspots ({s0['hotspots']} vs {s1['hotspots']}); using {hot}.")
    threshold = s1.get('occupancy_threshold')

    def row(name, a, b, unit=''):
        print(f"  {name:<44}{a:>12,.1f}{unit} {b:>12,.1f}{unit}   {pct(a, b):>8}")

    print(f"\n{s0['case_id']} (control)  vs  {s1['case_id']} (intervention)")
    print(f"  {'':<44}{'control':>12} {'intervention':>13}   {'change':>8}")
    print('Hotspots: ' + (', '.join(hot) if hot else '(none)'))
    row('person-minutes in hotspots', s0['hotspot_person_minutes'], s1['hotspot_person_minutes'])
    for z in hot:
        a, b = z0.get(z, {}), z1.get(z, {})
        row(f"  {z} peak", float(a.get('peak', 0)), float(b.get('peak', 0)))
        row(f"  {z} min above threshold", float(a.get('minutes_above_threshold', 0)),
            float(b.get('minutes_above_threshold', 0)))
    row('trips ending in hotspots', s0['trips_to_hotspot_completed'], s1['trips_to_hotspot_completed'])
    row('people who entered a hotspot', s0['people_who_entered_a_hotspot'], s1['people_who_entered_a_hotspot'])
    print('Efficiency:')
    row('trips completed', s0['trips_completed'], s1['trips_completed'])
    row('completed trips per hour (avg)', s0['completed_trips_per_sim_hour_avg'], s1['completed_trips_per_sim_hour_avg'])
    row('mean travel time (min)', s0['travel_time_all']['mean_min'], s1['travel_time_all']['mean_min'])
    row('90th pct travel time (min)', s0['travel_time_all']['p90_min'], s1['travel_time_all']['p90_min'])
    for mode in sorted(set(s0['travel_time_by_mode']) | set(s1['travel_time_by_mode'])):
        a = s0['travel_time_by_mode'].get(mode, {})
        b = s1['travel_time_by_mode'].get(mode, {})
        row(f"  {mode} mean min (trips {a.get('trips', 0)}/{b.get('trips', 0)})",
            a.get('mean_min', 0.0), b.get('mean_min', 0.0))

    # road congestion (transport/congestion.py), when both runs had it on
    c0, c1 = s0.get('road_congestion', {}), s1.get('road_congestion', {})
    if c0.get('enabled') and c1.get('enabled'):
        row('road delay (vehicle-hours)', c0['delay_vehicle_hours'], c1['delay_vehicle_hours'])
        for mode in ('private', 'jeep', 'bus', 'tricycle', 'all'):
            a, b = c0['mean_speed_kmh'].get(mode, ''), c1['mean_speed_kmh'].get(mode, '')
            if a != '' and b != '':
                row(f"  {mode} road speed (km/h)", a, b)
    elif c0.get('enabled') != c1.get('enabled'):
        print("WARNING: road congestion was on in only one of the runs - travel times are not directly comparable.")

    # displacement
    table = []
    for z in sorted(set(z0) | set(z1)):
        a, b = z0.get(z, {}), z1.get(z, {})
        entry = {'zone': z, 'hotspot': z in hot}
        for f in ZONE_FIELDS:
            va, vb = float(a.get(f, 0) or 0), float(b.get(f, 0) or 0)
            entry[f'{f}_control'], entry[f'{f}_intervention'], entry[f'{f}_change'] = va, vb, round(vb - va, 1)
        entry['new_above_threshold'] = (threshold is not None and z not in hot
                                        and entry['peak_control'] <= threshold < entry['peak_intervention'])
        table.append(entry)
    gainers = sorted((e for e in table if not e['hotspot']), key=lambda e: -e['person_minutes_change'])[:args.top]
    print('Displacement - zones that gained the most person-minutes:')
    for e in gainers:
        print(f"  {e['zone']:<22} {e['person_minutes_change']:>+10,.0f} person-min  "
              f"({pct(e['person_minutes_control'], e['person_minutes_intervention'])}), "
              f"visitors {e['visitors_change']:+.0f}, peak {e['peak_control']:.0f} -> {e['peak_intervention']:.0f}")
    new = [e['zone'] for e in table if e['new_above_threshold']]
    print(f"Zones newly above the threshold ({threshold}): {', '.join(new) if new else 'none'}")

    out = args.out or args.intervention / 'compare_vs_control.csv'
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(sorted(table, key=lambda e: -e['person_minutes_change']))
    print(f"\nPer-zone comparison written to {out}")


if __name__ == '__main__':
    main()
