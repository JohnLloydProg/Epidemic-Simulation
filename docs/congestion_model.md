# Road congestion model

Implemented in `transport/congestion.py`. It changes how long a road vehicle (private car, jeepney, bus,
tricycle) takes to cross each road edge, based on how many other vehicles are on it. Rail, transfer edges and
walking are not affected. Routes do not change in response to congestion (agents keep their precomputed paths).

## Mechanism

Each direction of a city edge is a **link** made of a moving part and a queue at its downstream end. This is the
segment structure of mesoscopic dynamic traffic assignment models such as DynaMIT (Ben-Akiva et al., 2001) and of
MATSim's queue simulation (Gawron, 1998; Cetin et al., 2003; Horni et al., 2016).

**1. Moving part: speed from density.** When a vehicle enters a link, its speed is set from the link's occupancy
with the speed–density relation of Greenshields (1935), in the generalised ("modified Greenshields") form used for
segment speeds in DYNASMART and DynaMIT (Mahmassani, 2001; Ben-Akiva et al., 2001):

```
rho = background occupancy + PCU already on the link / jam storage of the link
v   = v_min + (v_free − v_min) · max(0, 1 − rho^α)^β          (α = β = 1 → Greenshields)
moving time = length / min(v, vehicle's own speed)
```

**2. Queue part: exit capacity.** Vehicles leave a link in order and no faster than its capacity (a point queue,
Vickrey, 1969; MATSim's flow capacity):

```
exit time = max(entry + moving time, previous vehicle's exit + PCU · 3600 / capacity)
capacity  = lanes · v_free · k_jam / 4                      (Greenshields maximum flow)
            capped at lanes · s · g/C at a signalised junction   (s = 1,900 pcu/h/lane, HCM)
```

**3. Spillback (optional).** A link stores at most `lanes · length / 7.5 m` PCU (MATSim). With
`CONGESTION_SPILLBACK: true`, a vehicle whose next link is full waits at the node and keeps occupying its current
link, so queues grow backwards; after `CONGESTION_STUCK_S` (MATSim's default, 10 s) one waiting vehicle is let in
per link to prevent gridlock. It is **off by default** (see *Known issue* below).

**4. Background traffic.** The OD demand only creates trips that start or end in the study area, so traffic
passing through is missing. `CONGESTION_BACKGROUND` adds a background occupancy (share of jam density) per hour of
day. It is a calibration term, fitted with `tools/calibrate_congestion.py` so simulated private cars match TomTom's
Manila rush-hour speeds. Default 0.

## Parameters and where they come from

| Parameter | Value | Source |
|---|---|---|
| Speed–density relation | Greenshields, α = β = 1 | Greenshields (1935); generalised form: Mahmassani (2001), Ben-Akiva et al. (2001) |
| Jam spacing | 7.5 m per PCU (≈133 pcu/km/lane) | MATSim convention (Horni et al., 2016) |
| Speed at jam density | 5 km/h | Assumption (keeps travel times finite) |
| Free-flow speed | trunk/primary 40, secondary/tertiary/unclassified/links 30, residential 20 km/h | JMC 2018-001 / RA 4136 Sec. 35: through streets 40, city streets 30, crowded streets 20 km/h |
| Lanes per carriageway direction | trunk 3, primary 2, secondary 2, tertiary 2, others 1 | Assumption; uses OSM `lanes` when the edge export has it |
| Saturation flow at signals | 1,900 pcu/h/lane | HCM base saturation flow (TRB, 2016) |
| Green ratio g/C per approach | 0.45 | Assumption: two-phase signal, ~10% lost time |
| Signalised junctions | OSM `highway=traffic_signals` when the node export has it; otherwise nodes where two differently named roads of tertiary class or higher meet (180 of 784 nodes) | Heuristic fallback |
| PCU: car | 1.0 | Reference unit |
| PCU: jeepney | 1.3 | MUCEP (JICA & ALMEC, 2015), as cited in Raymundo et al. (2024) |
| PCU: bus | 2.5 | MUCEP (JICA & ALMEC, 2015), as cited in Raymundo et al. (2024); DPWH HPM uses 2.0 for large buses |
| PCU: tricycle | 0.535 | Raymundo et al. (2024), urban local roads in Metro Manila |
| Stuck time (spillback) | 10 s | MATSim `qsim.stuckTime` default |
| Calibration targets | 17.2 km/h morning rush, 13.8 km/h evening rush, 18.9 km/h all-day | TomTom Traffic Index 2025, Manila |

A case file's existing `capacity_multipliers` (`{edge_id: factor}`) now take effect: they scale that edge's
capacity and storage, so a "limit traffic" intervention (e.g. 0.5 = half the lanes) can be saved per case.

## Known issue: the transit schedule overloads three corridors

With congestion on, the log warns at start-up about links where **scheduled jeepneys and buses alone** need more
than the road's capacity. In the baseline 21 links are affected, mostly Padre Burgos Avenue (up to 327 route
directions, ≈5,100 pcu/h on a 2-lane carriageway, 3.0× capacity), Quezon Boulevard (2.6×), Taft Avenue (2.1×) and
Riverside Drive (2.2×). The routes come from the 2015 Sakay GTFS, and most use the default dispatch intervals
(7.5 min, 5 min at peak), so overlapping routes stack. These links queue without limit, jeepney and bus speeds fall
toward 2 km/h within two hours, and with spillback on, the queues lock up around Lawton and the northern bridges.

Before using congestion results in the thesis, the transit supply on these corridors should be brought in line
with real vehicle counts (for example longer `interval_s`/`peak_interval_s` for overlapping routes in
`transit_routes.json` or the case `transit_overrides`). As a test, dispatching every route 3× less often brings
Padre Burgos to about its capacity.

## Calibration

```
python tools/calibrate_congestion.py                                  # 06:00–08:00, target 17.2 km/h
python tools/calibrate_congestion.py --start 17 --target 13.8         # evening
python tools/calibrate_congestion.py --transit-interval-scale 3       # test with thinner transit
```

It bisects the background occupancy for the simulated hours and prints the value to put into
`CONGESTION_BACKGROUND`. Reference fit on the baseline case with `--transit-interval-scale 3`, 2 h runs, 30 min
warm-up:

| Window | Background | Private cars | Target |
|---|---|---|---|
| 06–08 | 0.04 | 17.21 km/h | 17.2 km/h |
| 17–19 | 0.33 | 13.96 km/h | 13.8 km/h |

With the current schedule (no scaling), cars already average 8.7 km/h with no background at all, so there is
nothing to calibrate until the corridor overload is fixed; the tool says so.

## Outputs

Congestion is logged as part of each metrics run (`metrics.py`), in the run's folder
`sim_data/results/<case_id>/logs/<timestamp>_seed<N>/`, next to `log.csv`, `trips.csv` and the others:

- `congestion_timeseries.csv` — every 5 simulated minutes: background, vehicles on links, vehicles held by full
  links, links past critical density (occupancy ≥ 0.5), mean speed per mode and overall, delay (vehicle-hours).
- `congestion_links.csv` — one row per link used: road name, class, lanes, capacity, storage, vehicles entered,
  mean and free speed, delay, highest occupancy and vehicle count, sorted by delay (worst bottlenecks first).
  Rewritten every simulated hour and whenever the run is saved (M) or ends.
- `summary.json` gets a `road_congestion` block: mean road speed by mode, vehicle-km, vehicle-hours, total delay
  and the ten links with the most delay. `run.log` records the congestion settings of the run.
- `tools/compare_runs.py` adds road delay and road speed by mode to the control-vs-intervention table, and warns
  if congestion was on in only one of the two runs.

`tools/run_headless.py` runs include all of this automatically.

In the program, **K** colours each occupied road from green (free) through yellow (critical density) to red
(jammed), and a line under the metrics shows the background, roads past critical density, vehicles held and road
speeds by mode.

## Config keys

All optional (defaults in brackets): `CONGESTION` [true], `CONGESTION_ROAD_CLASSES`, `CONGESTION_PCU`,
`CONGESTION_SAT_FLOW` [1900], `CONGESTION_SIGNAL_G_C` [0.45], `CONGESTION_JAM_SPACING_M` [7.5],
`CONGESTION_MIN_KMH` [5], `CONGESTION_ALPHA` / `CONGESTION_BETA` [1, 1], `CONGESTION_SPILLBACK` [false],
`CONGESTION_STUCK_S` [10], `CONGESTION_BACKGROUND` [24 zeros], `CONGESTION_LOG_INTERVAL_S` [300].
`"CONGESTION": false` restores the old fixed-speed behaviour exactly.

## Limitations

- Vehicles keep their precomputed routes; drivers do not divert around jams.
- Junctions are not modelled movement by movement: a link's capacity applies to all vehicles leaving it, and
  turning conflicts are not represented.
- Jeepneys and buses do not stop in the lane to load passengers (no dwell time or lane blocking).
- Background traffic is uniform over all roads in a given hour.
- Lanes and signal locations are class-based estimates unless the network export is re-run with OSM `lanes` on
  edges and `highway` on nodes (OSMnx provides both); the loader picks them up automatically.

## References

Ben-Akiva, M., Bierlaire, M., Koutsopoulos, H. N., & Mishalani, R. (2001). Network state estimation and prediction
for real-time traffic management. *Networks and Spatial Economics, 1*(3–4), 293–318.

Cetin, N., Burri, A., & Nagel, K. (2003). *A large-scale agent-based traffic microsimulation based on queue model*.
Paper presented at the 3rd Swiss Transport Research Conference, Monte Verità/Ascona.

Gawron, C. (1998). An iterative algorithm to determine the dynamic user equilibrium in a traffic simulation model.
*International Journal of Modern Physics C, 9*(3), 393–407.

Greenshields, B. D. (1935). A study of traffic capacity. *Highway Research Board Proceedings, 14*, 448–477.

Horni, A., Nagel, K., & Axhausen, K. W. (Eds.). (2016). *The multi-agent transport simulation MATSim*. Ubiquity
Press.

JICA & ALMEC. (2015). *MUCEP: Metro Manila Urban Transportation Integration Study Update and Capacity Enhancement
Project*. Japan International Cooperation Agency.

Mahmassani, H. S. (2001). Dynamic network traffic assignment and simulation methodology for advanced system
management applications. *Networks and Spatial Economics, 1*(3–4), 267–292.

Raymundo, Vergel, K. N., & Gaspay. (2024). *Passenger car equivalent factor for tricycles in urban local roads
within Metro Manila* [Conference paper]. Transportation Science Society of the Philippines (TSSP) 2024.
https://ncts.upd.edu.ph/tssp/wp-content/uploads/2024/09/TSSP2024-27-Revised-Paper.pdf
(check the authors' initials on the paper)

Republic Act No. 4136. (1964). *Land Transportation and Traffic Code*, Section 35. Philippines.

TomTom. (2026). *TomTom Traffic Index 2025: Manila*. https://www.tomtom.com/traffic-index/manila-traffic/

Transportation Research Board. (2016). *Highway capacity manual* (6th ed.). National Academies of Sciences,
Engineering, and Medicine.

Vickrey, W. S. (1969). Congestion theory and transport investment. *American Economic Review, 59*(2), 251–260.

Department of Transportation, Department of Public Works and Highways, & Department of the Interior and Local
Government. (2018). *Joint Memorandum Circular No. 2018-001* [Speed limit guidelines]. (check the exact title and issuing agencies)
