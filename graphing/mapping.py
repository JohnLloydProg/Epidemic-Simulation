import pandas as pd
from graphing.core import Node
from graphing.graph import Graph
import random

def load_graph() -> Graph:
    city_path = './cities/Mandaluyong'
    graph = Graph()
    nodes = pd.read_excel(f"{city_path}/nodes.xlsx", index_col=0)

    for i in range(len(nodes)):
        node_xl = nodes.iloc[i]
        node = Node(node_xl['X-Coordinate'], node_xl['Y-Coordinate'])
        graph.add_node(node)
    
    print("Nodes: ", [node.id for node in graph.nodes])
    edges = pd.read_excel(f"{city_path}/edges.xlsx", index_col=0)
    
    for i in range(len(edges)):
        edge_xl = edges.iloc[i]
        graph.add_edge(int(edge_xl['Distance (m)']), int(edge_xl['Node 1'] - 1), int(edge_xl['Node 2'] - 1))
    
    regions = pd.read_excel(f'{city_path}/regions.xlsx', index_col=0)
    regions[['Map edge nodes within the region', 'Artifical Nodes within the region', 'Street Nodes within the Region']] = regions[['Map edge nodes within the region', 'Artifical Nodes within the region', 'Street Nodes within the Region']].astype(str)

    for i in range(len(regions)):
        region_xl = regions.iloc[i]
        nodes = region_xl['Map edge nodes within the region'] if region_xl['Map edge nodes within the region'] != 'nan' else ""
        nodes += region_xl['Artifical Nodes within the region'] if region_xl['Artifical Nodes within the region'] != 'nan' else ""
        nodes +=  region_xl['Street Nodes within the Region'] if region_xl['Street Nodes within the Region'] != 'nan' else ""

        nodes = nodes.strip(",")
        node_ids = [int(node_id) for node_id in nodes.split(",")]

        graph.add_region(node_ids, int(region_xl['Alloted Residential Units']), int(region_xl['Alloted Business Units']))

    return graph


if __name__ == '__main__':
    load_graph('./cities/Mandaluyong')