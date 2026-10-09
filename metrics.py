"""
metrics.py — the simulation's thesis metrics, logged to disk while the simulation runs.

What is measured (per zone = barangay; "hotspot" = zones marked in hotspot mode, H):
  * Person-minutes in hotspots .... sum over time of the people inside hotspot zones (exposure)
  * Occupancy over time ........... people inside each zone -> peak occupancy and minutes above
                                     METRICS_OCCUPANCY_THRESHOLD (G shows it as a line graph)
  * Trips into hotspots ........... trips whose destination is a hotspot (started and arrived), and the
                                     number of different people who entered a hotspot at all (incl. passing through)
  * Displacement .................. per-zone person-minutes, peak, visitors and arrivals, per hour and for the run;
                                     compare a control run with an intervention run using tools/compare_runs.py
  * Travel time per agent, by mode  spawn -> arrival, by main mode (rail > bus > jeep > tricycle > walking,
                                     or private car)
  * Completed trips per hour ...... by clock hour of arrival

Where a person is: the zone of the node they stand at or last left (walking), or the node their vehicle is at
or last left. People at gateways (outside the map) count as "outside" and are not in any zone. People are counted
every METRICS_COUNT_S simulated seconds (person-minutes, peaks and visitors use these counts).

LOGGING
A run starts when the simulation first moves (Play) and lasts METRICS_RUN_HOURS simulated hours (default 24).
Its log folder is created at the start, sim_data/results/<case_id>/logs/<YYYYmmdd-HHMMSS>_seed<OD_SEED>/,
and these files are written WHILE the run goes (so nothing is lost if the program stops):
  log.csv ................ one row every METRICS_SAMPLE_S s: people moving by mode, waiting, trips started and
                           completed, hotspot people, person-minutes, entries, busiest zone, ...
  occupancy_timeseries.csv people in every zone, same interval
  trips.csv .............. one row per completed trip (origin, destination, main mode, modes used, times)
  hourly.csv ............. one row per simulated hour: trips, travel time by mode (mean / p90), hotspot metrics
  zones_hourly.csv ....... one row per simulated hour and zone: person-minutes, peak, minutes above threshold,
                           new visitors, arrivals (displacement by hour)
  run.log ................ the settings of the run, an hourly summary line, and every info/warning message the
                           simulation logged during the run
  congestion_timeseries.csv  road congestion every CONGESTION_LOG_INTERVAL_S s: road speed by mode, delay,
                           roads past critical density (transport/congestion.py; only when CONGESTION is on)
  congestion_links.csv ... one row per road direction used: traffic, speed, delay, worst occupancy (every
                           simulated hour and on save)
When the run ends (METRICS_RUN_HOURS reached -> the simulation pauses), or is cut short by Reset, opening a case
or closing the program, these are added:
  summary.json, zones.csv, occupancy.png
M writes summary.json, zones.csv and occupancy.png at any time without ending the run.

Config keys (optional):
  METRICS_RUN_HOURS ............. length of a run in simulated hours (default 24)
  METRICS_OCCUPANCY_THRESHOLD ... people in a zone counted as crowded (default 100); set it from the peaks of
                                  the control runs, and keep it the same across control and intervention runs
  METRICS_SAMPLE_S .............. seconds between rows of log.csv / occupancy_timeseries.csv (default 60)
  METRICS_COUNT_S ............... seconds between people counts (default 10)
"""
from __future__ import annotations
import csv
import json
import logging
from array import array
from collections import Counter
from datetime import datetime
from pathlib import Path

import configuration as config
from transport import congestion

LOGGER = logging.getLogger('Metrics')

MODE_PRIORITY = ['rail', 'bus', 'jeep', 'tricycle', 'walking']   # the "main mode" of a public-transport trip
MODES = MODE_PRIORITY + ['private car']
LIVE_MODES = ['walking', 'jeep', 'bus', 'rail', 'tricycle', 'private']
OUTSIDE = 'outside'
PLOT_COLORS = [(214, 39, 40), (31, 119, 180), (44, 160, 44), (255, 127, 14), (148, 103, 189), (140, 86, 75),
               (227, 119, 194), (23, 190, 207)]


def main_mode(agent) -> str:
    if not agent.commuting:
        return 'private car'
    used = getattr(agent, 'modes_used', set())
    for mode in MODE_PRIORITY:
        if mode in used:
            return mode
    return 'walking'


def clock(seconds:int) -> str:
    seconds = int(seconds)
    return f"{(seconds // 3600) % 24:02d}:{(seconds // 60) % 60:02d}"


def axis_clock(seconds:int) -> str:
    """Clock label that continues past midnight as the next day ("06:00 +1d")."""
    day, rest = divmod(int(seconds), 86400)
    return clock(rest) + (f" +{day}d" if day else '')


def percentile(values, q:float) -> float:
    if not len(values):
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * q
    low = int(k)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (k - low)


def stats(values) -> dict:
    n = len(values)
    return {'trips': n, 'mean_min': round(sum(values) / n, 2) if n else 0.0,
            'median_min': round(percentile(values, 0.5), 2), 'p90_min': round(percentile(values, 0.9), 2)}


class Bitset:
    """Set of small non-negative ints (agent ids) in one bit each: 24 h of agents stays a few MB."""
    __slots__ = ('bits', 'count')

    def __init__(self):
        self.bits = bytearray(1024)
        self.count = 0

    def add(self, i:int) -> bool:
        """Adds i; True if it was not in the set before."""
        byte, bit = i >> 3, 1 << (i & 7)
        if byte >= len(self.bits):
            self.bits.extend(bytearray(max(byte + 1 - len(self.bits), len(self.bits))))
        if self.bits[byte] & bit:
            return False
        self.bits[byte] |= bit
        self.count += 1
        return True

    def __len__(self):
        return self.count


class CsvLog:
    """A CSV file written row by row while the run goes."""

    def __init__(self, path:Path, header:list):
        self.file = open(path, 'w', newline='', encoding='utf-8')
        self.writer = csv.writer(self.file)
        self.writer.writerow(header)

    def row(self, values:list):
        self.writer.writerow(values)

    def flush(self):
        self.file.flush()

    def close(self):
        self.file.close()


class MetricsTracker:
    def __init__(self, sim):
        self.sim = sim
        self.sample_s = int(config.get('METRICS_SAMPLE_S', 60))
        self.count_s = int(config.get('METRICS_COUNT_S', 10))
        self.run_hours = float(config.get('METRICS_RUN_HOURS', 24))
        self.threshold = config.get('METRICS_OCCUPANCY_THRESHOLD', 100)
        self.logs:dict[str, CsvLog] = {}
        self.run_log_handler = None
        self.folder = None
        self.reset()

    # ------------------------------------------------------------------ setup
    def reset(self):
        """Forget everything recorded (new run). Call end_run() first to keep the logs of the current run."""
        self._close_files()
        self.ready = False
        self.finished = False
        self.saved = False
        self.folder = None
        self.start_time = self.last_time = self.end_time = None
        self.samples_t:list[int] = []                 # sample times
        self.samples:dict[str, array] = {}            # zone -> people inside at each sample
        self.now:Counter = Counter()                  # people per zone at the last count
        self.live_modes:Counter = Counter()           # people moving by mode at the last count
        self.waiting = 0
        self.person_s:Counter = Counter()             # zone -> person-seconds
        self.peak:dict[str, int] = {}
        self.peak_time:dict[str, int] = {}
        self.above_samples:Counter = Counter()        # zone -> samples above the threshold
        self.visitors:dict[str, Bitset] = {}          # zone -> agents that were inside at some point
        self.hot_visitors = Bitset()                  # agents that were inside any hotspot
        self.arrivals:Counter = Counter()             # zone -> completed trips ending there
        self.through:Counter = Counter()              # zone -> visitors whose trip neither starts nor ends there
        self.trip_ends:Counter = Counter()            # zone -> visitors whose trip starts or ends there
        self.travel:dict[str, array] = {}             # main mode -> travel minutes of completed trips
        self.travel_sum:Counter = Counter()
        self.n_completed = 0
        self.n_started = 0
        self.trips_to_hotspot_scheduled = 0
        self.trips_to_hotspot_arrived = 0
        self.lost_mid_trip = 0
        self.per_hour:Counter = Counter()             # clock hour -> completed trips
        self.zones, self.hotspots, self.hot = [], [], set()
        self._new_hour()

    def _new_hour(self):
        """Accumulators of the simulated hour being logged (hourly.csv, zones_hourly.csv)."""
        self.h_person_s = Counter()
        self.h_peak = Counter()
        self.h_above = Counter()
        self.h_new_visitors = Counter()
        self.h_arrivals = Counter()
        self.h_travel:dict[str, list] = {}
        self.h_started = 0
        self.h_hot_started = 0
        self.h_hot_arrived = 0
        self.h_hot_entered = 0
        self.h_lost = 0

    def _setup(self, time:int):
        zones = getattr(self.sim.graph, 'zones', {}) or {}
        self.zone_of_node = {}
        self.zone_info = {}
        for region in zones.values():
            self.zone_info[region.name] = {'psgc': region.psgc, 'role': region.role,
                                           'hotspot': bool(getattr(region, 'is_hotspot', False))}
            for node in region.nodes:
                if node is not None:
                    self.zone_of_node[node.id] = region.name
        self.zones = list(self.zone_info)
        self.hotspots = [name for name, info in self.zone_info.items() if info['hotspot']]
        self.hot = set(self.hotspots)
        self.samples = {name: array('i') for name in self.zones}
        self.visitors = {name: Bitset() for name in self.zones}
        self.peak = {name: 0 for name in self.zones}
        self.peak_time = {name: time for name in self.zones}
        self.start_time = self.last_time = self.last_count = time
        self.next_count = self.next_sample = time
        self.hour_start = time
        self.hour_end = (time // 3600 + 1) * 3600
        self.end_time = time + int(round(self.run_hours * 3600))
        self.ready = True
        self._open_files(time)

    def _open_files(self, time:int):
        from graphing.data_loader import results_dir
        seed = config.get('OD_SEED', 42)
        self.folder = results_dir() / 'logs' / f"{datetime.now():%Y%m%d-%H%M%S}_seed{seed}"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.logs = {
            'log': CsvLog(self.folder / 'log.csv',
                          ['time_s', 'clock', 'elapsed_h', 'agents_active', 'waiting_for_ride']
                          + [f'moving_{m}' for m in LIVE_MODES]
                          + ['trips_started', 'trips_completed', 'trips_completed_last_interval',
                             'mean_travel_min_last_interval', 'hotspot_people', 'hotspot_person_minutes',
                             'people_entered_hotspot', 'trips_to_hotspot_started', 'trips_to_hotspot_arrived',
                             'zones_above_threshold', 'busiest_zone', 'busiest_zone_people']),
            'occupancy': CsvLog(self.folder / 'occupancy_timeseries.csv', ['time_s', 'clock'] + self.zones),
            'trips': CsvLog(self.folder / 'trips.csv',
                            ['agent', 'origin_zone', 'dest_zone', 'kind', 'mode', 'modes_used', 'depart_s',
                             'arrive_s', 'travel_min', 'to_hotspot']),
            'hourly': CsvLog(self.folder / 'hourly.csv',
                             ['hour', 'minutes_covered', 'trips_started', 'trips_completed', 'trips_lost_mid_trip',
                              'mean_travel_min', 'p90_travel_min']
                             + [f'{m.replace(" ", "_")}_{k}' for m in MODES for k in ('trips', 'mean_min', 'p90_min')]
                             + ['hotspot_person_minutes', 'hotspot_peak', 'hotspot_minutes_above_threshold',
                                'people_entered_hotspot', 'trips_to_hotspot_started', 'trips_to_hotspot_arrived']),
            'zones_hourly': CsvLog(self.folder / 'zones_hourly.csv',
                                   ['hour', 'zone', 'hotspot', 'person_minutes', 'peak', 'minutes_above_threshold',
                                    'new_visitors', 'arrivals']),
        }
        self._interval_completed = 0
        self._interval_travel = 0.0

        # run.log: the settings, then every info/warning line logged while the run goes
        self.run_log_handler = logging.FileHandler(self.folder / 'run.log', 'w', encoding='utf-8')
        self.run_log_handler.setLevel(logging.INFO)
        self.run_log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        logging.getLogger().addHandler(self.run_log_handler)
        od_hours = float(config.get('OD_DURATION_HOURS', 24))
        settings = {'OD_SEED': seed, 'SIM_START_HOUR': config.get('SIM_START_HOUR', 0),
                    'TIME_STEP': config.get('TIME_STEP', 2), 'OD_DURATION_HOURS': od_hours,
                    'OD_HOUR_PROFILE': config.get('OD_HOUR_PROFILE') or 'uniform (not set)',
                    'OD_SAMPLING': config.get('OD_SAMPLING', 'sample'),
                    'HOTSPOT_ATTRACTION': config.get('HOTSPOT_ATTRACTION', 0.5),
                    'METRICS_RUN_HOURS': self.run_hours, 'METRICS_OCCUPANCY_THRESHOLD': self.threshold,
                    'METRICS_SAMPLE_S': self.sample_s, 'METRICS_COUNT_S': self.count_s,
                    **congestion.run_settings()}
        LOGGER.info(f"Run started: case {self._case_id()}, sim time {clock(time)}, "
                    f"{self.run_hours:g} simulated hours, hotspots {self.hotspots or 'none'}")
        LOGGER.info(f"Settings: {json.dumps(settings)}")
        LOGGER.info(f"Logging to {self.folder}")
        congestion.open_run(self.folder, time)
        if od_hours < self.run_hours:
            LOGGER.warning(f"OD_DURATION_HOURS is {od_hours:g}: no new trips start after the first {od_hours:g} "
                           f"of the {self.run_hours:g} logged hours.")
        if not config.get('OD_HOUR_PROFILE'):
            LOGGER.warning("OD_HOUR_PROFILE is not set: trips start evenly over the day (as many at 3 am as at 8 am).")

    def _close_files(self):
        congestion.close_run()
        for log in self.logs.values():
            log.close()
        self.logs = {}
        if self.run_log_handler is not None:
            logging.getLogger().removeHandler(self.run_log_handler)
            self.run_log_handler.close()
            self.run_log_handler = None

    # ------------------------------------------------------------------ recording
    def zone_of(self, agent) -> str:
        node = agent.current_node
        if node is None and agent.transportation is not None:
            node = agent.transportation.current_node
        if node is None:
            return OUTSIDE
        return self.zone_of_node.get(node.id, OUTSIDE)

    def step(self, time:int) -> bool:
        """Call once per simulation step, after the step's events were handled.
        Returns True when the run has just reached METRICS_RUN_HOURS (the logs are then complete)."""
        if self.finished:
            return False
        if not self.ready:
            self._setup(time)
        self.last_time = time
        if time >= self.next_count:
            self._count(time)
            while self.next_count <= time:
                self.next_count += self.count_s
        while time >= self.next_sample:
            self._sample(self.next_sample)
            self.next_sample += self.sample_s
        while time >= self.hour_end:
            self._write_hour(self.hour_end)
        if time >= self.end_time:
            self.end_run(time, f'{self.run_hours:g}h complete')
            return True
        return False

    def _hold(self, time:int):
        """The people counted last stayed where they were until `time` (person-seconds)."""
        dt = time - self.last_count
        if dt <= 0:
            return
        for zone, count in self.now.items():
            self.person_s[zone] += count * dt
            self.h_person_s[zone] += count * dt
        self.last_count = time

    def _count(self, time:int):
        self._hold(time)
        counts = Counter()
        modes = Counter()
        waiting = 0
        hot = self.hot
        for agent in self.sim.agents:
            if agent.state == 'waiting':
                waiting += 1
            elif agent.state == 'travelling':
                modes[agent.transportation.method if agent.transportation else 'walking'] += 1
            zone = self.zone_of(agent)
            if zone == OUTSIDE:
                continue
            counts[zone] += 1
            if self.visitors[zone].add(agent.id):
                self.h_new_visitors[zone] += 1
                if zone in (agent.origin_zone, agent.destination_zone):   # scenario C: through-traffic
                    self.trip_ends[zone] += 1
                else:
                    self.through[zone] += 1
            if zone in hot and self.hot_visitors.add(agent.id):
                self.h_hot_entered += 1
        self.now, self.live_modes, self.waiting = counts, modes, waiting
        for zone, count in counts.items():
            if count > self.peak[zone]:
                self.peak[zone] = count
                self.peak_time[zone] = time
            if count > self.h_peak[zone]:
                self.h_peak[zone] = count

    def _sample(self, t:int):
        counts = self.now
        self.samples_t.append(t)
        for zone in self.zones:
            value = counts.get(zone, 0)
            self.samples[zone].append(value)
            if self.threshold is not None and value > self.threshold:
                self.above_samples[zone] += 1
                self.h_above[zone] += 1
        self.logs['occupancy'].row([t, clock(t)] + [counts.get(z, 0) for z in self.zones])
        busiest = max(self.zones, key=lambda z: counts.get(z, 0)) if self.zones else ''
        mean_interval = (round(self._interval_travel / self._interval_completed, 2)
                         if self._interval_completed else '')
        self.logs['log'].row(
            [t, clock(t), round((t - self.start_time) / 3600, 3), len(self.sim.agents), self.waiting]
            + [self.live_modes.get(m, 0) for m in LIVE_MODES]
            + [self.n_started, self.n_completed, self._interval_completed, mean_interval,
               sum(counts.get(z, 0) for z in self.hotspots), round(self.person_minutes(self.hotspots), 1),
               len(self.hot_visitors), self.trips_to_hotspot_scheduled, self.trips_to_hotspot_arrived,
               sum(1 for z in self.zones if self.threshold is not None and counts.get(z, 0) > self.threshold),
               busiest, counts.get(busiest, 0)])
        self._interval_completed = 0
        self._interval_travel = 0.0
        for log in self.logs.values():
            log.flush()

    def _write_hour(self, end:int):
        """Write the rows of the hour that ends at `end` (or earlier, when the run is cut short)."""
        self._hold(end)
        start = self.hour_start
        label = clock(start)
        minutes = round((end - start) / 60, 1)
        all_minutes = [v for values in self.h_travel.values() for v in values]
        hot_person_min = sum(self.h_person_s.get(z, 0) for z in self.hotspots) / 60
        hot_peak = max([self.h_peak.get(z, 0) for z in self.hotspots], default=0)
        hot_above = sum(self.h_above.get(z, 0) for z in self.hotspots) * self.sample_s / 60
        by_mode = []
        for mode in MODES:
            s = stats(self.h_travel.get(mode, []))
            by_mode += [s['trips'], s['mean_min'], s['p90_min']]
        total = stats(all_minutes)
        self.logs['hourly'].row(
            [label, minutes, self.h_started, total['trips'], self.h_lost, total['mean_min'], total['p90_min']]
            + by_mode
            + [round(hot_person_min, 1), hot_peak, hot_above, self.h_hot_entered, self.h_hot_started,
               self.h_hot_arrived])
        for zone in self.zones:
            self.logs['zones_hourly'].row(
                [label, zone, zone in self.hot, round(self.h_person_s.get(zone, 0) / 60, 1),
                 self.h_peak.get(zone, 0), self.h_above.get(zone, 0) * self.sample_s / 60,
                 self.h_new_visitors.get(zone, 0), self.h_arrivals.get(zone, 0)])
        self.logs['hourly'].flush()
        self.logs['zones_hourly'].flush()
        hot_text = (f", hotspots {round(hot_person_min):,} person-min (peak {hot_peak}, "
                    f"{hot_above:g} min above, {self.h_hot_entered} entered)" if self.hotspots else '')
        LOGGER.info(f"Hour {label}: {self.h_started:,} trips started, {total['trips']:,} completed "
                    f"(mean {total['mean_min']} min, p90 {total['p90_min']} min), "
                    f"{len(self.sim.agents):,} active{hot_text}")
        self.hour_start = end
        self.hour_end = (end // 3600 + 1) * 3600
        self._new_hour()

    def trip_started(self, agent, time:int):
        if self.finished:
            return
        if not self.ready:                            # trips spawn before the first step() of the run
            self._setup(time)
        agent.spawn_time = time
        self.n_started += 1
        self.h_started += 1
        if agent.destination_zone in self.hot:
            self.trips_to_hotspot_scheduled += 1
            self.h_hot_started += 1

    def spawn_failed(self, agent):
        """The trip could not start (no route): it no longer counts as started."""
        if getattr(agent, 'spawn_time', None) is None or self.finished:
            return
        self.n_started -= 1
        self.h_started -= 1
        if agent.destination_zone in self.hot:
            self.trips_to_hotspot_scheduled -= 1
            self.h_hot_started -= 1

    def trip_failed(self, agent):
        """The agent left the simulation mid-trip (no walking path)."""
        if getattr(agent, 'spawn_time', None) is not None and not self.finished:
            self.lost_mid_trip += 1
            self.h_lost += 1

    def trip_completed(self, agent, time:int):
        start = getattr(agent, 'spawn_time', None)
        if start is None or not self.ready or self.finished:   # right-click test agents are not part of the results
            return
        zone = agent.destination_zone or self.zone_of_node.get(agent.destination_node.id, OUTSIDE)
        mode = main_mode(agent)
        minutes = (time - start) / 60
        self.n_completed += 1
        self.arrivals[zone] += 1
        self.h_arrivals[zone] += 1
        self.per_hour[(time // 3600) % 24] += 1
        self.travel.setdefault(mode, array('f')).append(minutes)
        self.travel_sum[mode] += minutes
        self.h_travel.setdefault(mode, []).append(minutes)
        self._interval_completed += 1
        self._interval_travel += minutes
        to_hot = zone in self.hot
        if to_hot:
            self.trips_to_hotspot_arrived += 1
            self.h_hot_arrived += 1
        self.logs['trips'].row([agent.id, agent.origin_zone, zone, agent.trip_kind, mode,
                                '+'.join(sorted(getattr(agent, 'modes_used', ()))) or 'walking',
                                start, time, round(minutes, 2), to_hot])

    # ------------------------------------------------------------------ results
    def minutes_above(self, zone:str) -> float:
        return self.above_samples.get(zone, 0) * self.sample_s / 60

    def person_minutes(self, zones=None) -> float:
        zones = self.zones if zones is None else zones
        return sum(self.person_s.get(z, 0) for z in zones) / 60

    def mean_travel_by_mode(self) -> dict:
        """Mean travel minutes per main mode (cheap enough for the HUD)."""
        return {mode: self.travel_sum[mode] / len(v)
                for mode, v in sorted(self.travel.items(), key=lambda item: -len(item[1])) if len(v)}

    def travel_by_mode(self) -> dict:
        return {mode: stats(v) for mode, v in sorted(self.travel.items(), key=lambda item: -len(item[1]))}

    def summary(self, time:int, ended_by:str = None) -> dict:
        if not self.ready:
            return {}
        all_min = [v for values in self.travel.values() for v in values]
        hours = max((time - self.start_time) / 3600, 1e-9)
        return {
            'case_id': self._case_id(), 'saved_at': datetime.now().isoformat(timespec='seconds'),
            'ended_by': ended_by or 'snapshot (run still going)', 'log_folder': str(self.folder),
            'sim_start': clock(self.start_time), 'sim_end': clock(time),
            'simulated_hours': round((time - self.start_time) / 3600, 2), 'run_hours': self.run_hours,
            'od_seed': config.get('OD_SEED', 42),
            'occupancy_threshold': self.threshold, 'sample_s': self.sample_s, 'count_s': self.count_s,
            'hotspots': self.hotspots,
            'hotspot_person_minutes': round(self.person_minutes(self.hotspots), 1),
            'hotspot_detail': {z: {'person_minutes': round(self.person_minutes([z]), 1), 'peak': self.peak[z],
                                   'peak_at': clock(self.peak_time[z]),
                                   'minutes_above_threshold': self.minutes_above(z)} for z in self.hotspots},
            'trips_to_hotspot_scheduled': self.trips_to_hotspot_scheduled,
            'trips_to_hotspot_completed': self.trips_to_hotspot_arrived,
            'people_who_entered_a_hotspot': len(self.hot_visitors),
            'trips_spawned': self.sim.od_spawned, 'trips_not_spawned': self.sim.od_failed,
            'trips_lost_mid_trip': self.lost_mid_trip,
            'trips_completed': self.n_completed, 'trips_still_travelling': len(self.sim.agents),
            'travel_time_all': {k: v for k, v in stats(all_min).items() if k != 'trips'},
            'travel_time_by_mode': self.travel_by_mode(),
            'completed_trips_per_hour': {f"{h:02d}:00": n for h, n in sorted(self.per_hour.items())},
            'completed_trips_per_sim_hour_avg': round(self.n_completed / hours, 1),
            'road_congestion': congestion.run_summary(),
        }

    def _case_id(self) -> str:
        try:
            from graphing.data_loader import load_case
            return load_case()['case_id']
        except Exception:
            return 'unknown'

    def has_data(self) -> bool:
        return self.ready and self.last_time is not None and self.last_time > self.start_time

    def save(self, time:int, label:str = None) -> Path | None:
        """Write summary.json, zones.csv and occupancy.png into the run's log folder (the run keeps going)."""
        if not self.has_data() or self.finished:
            return None
        self._hold(time)
        with open(self.folder / 'summary.json', 'w', encoding='utf-8') as f:
            json.dump(self.summary(time, label), f, indent=2)
        with open(self.folder / 'zones.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['zone', 'psgc', 'role', 'hotspot', 'person_minutes', 'peak', 'peak_at',
                        'minutes_above_threshold', 'visitors', 'arrivals', 'through', 'trip_ends'])
            for z in sorted(self.zones, key=lambda z: -self.person_s.get(z, 0)):
                info = self.zone_info[z]
                w.writerow([z, info['psgc'], info['role'], info['hotspot'], round(self.person_minutes([z]), 1),
                            self.peak[z], clock(self.peak_time[z]), self.minutes_above(z),
                            len(self.visitors[z]), self.arrivals.get(z, 0),
                            self.through.get(z, 0), self.trip_ends.get(z, 0)])
        for log in self.logs.values():
            log.flush()
        congestion.write_link_table(self.folder / 'congestion_links.csv')
        try:
            self._plot(self.folder / 'occupancy.png')
        except Exception:                                  # the CSVs are what matters; the figure is a bonus
            LOGGER.exception('Could not draw occupancy.png')
        self.saved = True
        return self.folder

    def end_run(self, time:int, reason:str) -> Path | None:
        """End the run: write the last (partial) hour and the summary files, then close the logs."""
        if not self.ready or self.finished:
            return None
        if not self.has_data():                            # no simulated time passed: nothing worth keeping
            LOGGER.info('Run ended before any simulated time passed; no results written.')
            self._close_files()
            self.finished = True
            return None
        if time > self.hour_start:
            self._write_hour(time)
        folder = self.save(time, reason)
        LOGGER.info(f"Run ended ({reason}) at {clock(time)} after {(time - self.start_time) / 3600:.2f} simulated "
                    f"hours: {self.n_completed:,} trips completed, hotspot person-minutes "
                    f"{self.person_minutes(self.hotspots):,.0f}. Logs in {folder}")
        self._close_files()
        self.finished = True
        return folder

    def plot_zones(self, limit:int = 6) -> list[str]:
        """Zones drawn in the graphs: the hotspots, or (with none) the busiest zones by peak occupancy."""
        if self.hotspots:
            return self.hotspots
        return sorted(self.zones, key=lambda z: -self.peak.get(z, 0))[:limit]

    def _plot(self, path:Path):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
        zones = self.plot_zones()
        hours = [t / 3600 for t in self.samples_t]
        fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
        for k, z in enumerate(zones):
            color = [c / 255 for c in PLOT_COLORS[k % len(PLOT_COLORS)]]       # same colors as the in-sim graph
            label = f"{z} (peak {self.peak[z]} at {clock(self.peak_time[z])}, {self.minutes_above(z):g} min above)"
            ax.plot(hours, self.samples[z], linewidth=1.2, color=color, label=label)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda h, _pos: axis_clock(round(h * 3600))))
        if self.threshold is not None:
            ax.axhline(self.threshold, color='black', linestyle='--', linewidth=1, label=f"threshold ({self.threshold})")
        title = 'Hotspot occupancy' if self.hotspots else 'Occupancy of the busiest zones (no hotspots set)'
        ax.set_title(f"{title} — {self._case_id()}, seed {config.get('OD_SEED', 42)}")
        ax.set_xlabel('Time of day')
        ax.set_ylabel('People in zone')
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, loc='upper left')
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
