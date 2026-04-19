from graphing.core import Node
from transport.transportation import Route
from typing import Literal


class Checkpoint:
    def __init__(self, mode:Literal['walking', 'ride'], start_node:Node, end_node:Node, route:Route | None):
        self.mode = mode
        self.start_node = start_node
        self.end_node = end_node
        self.route = route
        self.path_nodes = []


def generate_checkpoints(raw_path: list[tuple]) -> list[Checkpoint]:
    if not raw_path: return []

    checkpoints = []
    current_mode = 'walk' if raw_path[0][1] is None else 'ride'
    current_route = raw_path[0][1]
    
    current_leg = Checkpoint(mode=current_mode, start_node=raw_path[0][0], end_node=None, route=current_route)
    current_leg.path_nodes.append(raw_path[0][0])

    for i in range(1, len(raw_path)):
        node, route = raw_path[i]
        mode = 'walk' if route is None else 'ride'

        if mode != current_mode or route != current_route:
            current_leg.end_node = raw_path[i-1][0]
            checkpoints.append(current_leg)
            
            current_leg = Checkpoint(mode=mode, start_node=raw_path[i-1][0], end_node=None, route=route)
            current_leg.path_nodes.append(raw_path[i-1][0])
            
            current_mode = mode
            current_route = route
            
        current_leg.path_nodes.append(node)

    current_leg.end_node = raw_path[-1][0]
    checkpoints.append(current_leg)
    
    return checkpoints