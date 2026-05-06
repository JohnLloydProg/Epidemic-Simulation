import multiprocessing
import itertools
from graphing.graph import Graph, RegionGraph
from transport.transportation import Route
from transport.checkpoint import generate_checkpoints, Checkpoint
from graphing.mapping import shortest_path, load_graph
from agents.core import Establishment
import logging
import pickle
import os

CACHE_FILE_NAME = 'routing_table.pkl'
LOGGER = logging.getLogger('RoutingTable')


worker_city:RegionGraph = None
worker_routes:list[Route] = None

def save_dehydrated_cache(dehydrated_cache: dict):
    """Saves the primitive dictionary to a file."""
    LOGGER.info(f"Saving routing cache to {CACHE_FILE_NAME}...")
    with open(CACHE_FILE_NAME, 'wb') as f:
        pickle.dump(dehydrated_cache, f)
    LOGGER.info("Save complete!")

def rehydrate_cache(dehydrated_cache:dict, city:RegionGraph, railway:Graph, routes:list[Route]):
    route_lookup = {route.id:route for route in routes}
    routing_cache = {}

    for (start_id, dest_id), pickled_checkpoints in dehydrated_cache.items():
        checkpoints = []
        for pickled_checkpoint in pickled_checkpoints:
            start_node_id = pickled_checkpoint['start_node']
            start_node = city.get_node(start_node_id) if start_node_id[0] == 'city' else railway.get_node(start_node_id)
            end_node_id = pickled_checkpoint['end_node']
            end_node =  city.get_node(end_node_id) if end_node_id[0] == 'city' else railway.get_node(end_node_id)
            route = route_lookup.get(pickled_checkpoint['route'])
            checkpoint = Checkpoint(
                mode=pickled_checkpoint['mode'], start_node=start_node, end_node=end_node, route=route
                )
            checkpoints.append(checkpoint)
        routing_cache[(start_id, dest_id)] = checkpoints
    
    return routing_cache

def init_worker():
    global worker_city, worker_routes
    
    city_data, _, routes_data = load_graph()
    worker_city = city_data
    worker_routes = routes_data


def compute_single_path(pair:tuple[tuple[str, int], tuple[str, int]]):
    start_id, dest_id = pair
    
    start_node = worker_city.get_node(start_id)
    dest_node = worker_city.get_node(dest_id)
    
    raw_path = shortest_path(start_node, dest_node, worker_routes)
    
    if raw_path:
        checkpoints = generate_checkpoints(raw_path)
        pickable_checkpoints = []
        for cp in checkpoints:
            pickable = {
                'mode':cp.mode,
                'start_node': cp.start_node.id if cp.start_node else None,
                'end_node': cp.end_node.id if cp.end_node else None,
                'route': cp.route.id if cp.route else None
            }
            pickable_checkpoints.append(pickable)
        
        return (start_id, dest_id, pickable_checkpoints)
    return (start_id, dest_id, [])


def build_routing_cache(establishments:list[Establishment], city:RegionGraph, railway:Graph, routes:list[Route]) -> dict[tuple, list[Checkpoint]]:
    if os.path.exists(CACHE_FILE_NAME):
        LOGGER.info(f"Found existing {CACHE_FILE_NAME}! Loading from disk...")
        with open(CACHE_FILE_NAME, 'rb') as f:
            pickled_cache = pickle.load(f)
        LOGGER.info(f'Done reading file.')
        return rehydrate_cache(pickled_cache, city, railway, routes)

    LOGGER.info("Gathering origin-destination pairs...")
    est_node_ids = list(set([est.node.id for est in establishments]))
    
    pairs_to_compute = list(itertools.permutations(est_node_ids, 2))
    LOGGER.info(f"Total paths to compute: {len(pairs_to_compute)}")

    pickled_cache = {}
    

    LOGGER.info("Igniting multiprocessing pool...")
    with multiprocessing.Pool(initializer=init_worker) as pool:
        
        results = pool.imap_unordered(compute_single_path, pairs_to_compute, chunksize=100)
        
        for start_id, dest_id, pickled_checkpoints in results:
            pickled_cache[(start_id, dest_id)] = pickled_checkpoints
    
    save_dehydrated_cache(pickled_cache)

    LOGGER.info("Routing cache built successfully!")
    return rehydrate_cache(pickled_cache, city, railway, routes)