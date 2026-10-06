City of Manila OD model bundle — use with manila_od.py
zones.csv: one row per zone (row = matrix index), P/A, district, point (EPSG:32651)
travel_time.npy, transit_hops.npy, pasig_crossings_per_trip.npy: zone x zone matrices
facilities.csv: Manila facilities (category, footprint, floors, zone row)
routes.csv: zones served by each route (route_id = line, relation = OSM relation); neighbors.csv: tricycle links
base_config.json: base settings and calibrated beta
od_matrix_base_reference.npy: the notebook's final matrix (for testing)
zones.gpkg: zone shapes (optional, for maps)
