"""
build_basemap.py — download and prepare the street map for the graphics view now (ui/basemap.py), so the program
needs no internet later, e.g. before a presentation.

    python tools/build_basemap.py                       # the config's provider (BASEMAP_PROVIDER, default voyager)
    python tools/build_basemap.py --provider light      # voyager, voyager_nolabels, light, light_nolabels, osm, satellite
    python tools/build_basemap.py --provider satellite --zoom 17

Run it from the repository folder with the same CONFIG_FILE_NAME (.env) as the program. The map is saved in
sim_data/cache/basemap/ (copy that folder to another computer to use it there without downloading).
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--provider', default=None, help='voyager, voyager_nolabels, light, light_nolabels, osm or satellite')
    ap.add_argument('--zoom', type=int, default=None, help='tile zoom (default: BASEMAP_ZOOM or 16)')
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv()
    import configuration as config
    config.init()
    from graphing.data_loader import load_graph_from_data, data_dir
    from ui import basemap

    meta = basemap.load_meta(data_dir())
    if meta is None:
        sys.exit("sim_data/base/meta.json has no coordinate transform; cannot place a street map.")
    city, railway, _ = load_graph_from_data()
    extent = basemap.map_extent(city, railway, float(config.get('BASEMAP_MARGIN_M', 500)))
    street = basemap.TileBasemap(basemap.SimProjection(meta), extent, data_dir() / 'cache' / 'basemap',
                                 (args.provider or config.get('BASEMAP_PROVIDER', 'voyager')).lower(),
                                 args.zoom or int(config.get('BASEMAP_ZOOM', 16)), basemap.carto_api_key(config))
    street.start()
    while street.status not in ('ready', 'failed'):
        print(f"\r{street.message or street.status:70s}", end='', flush=True)
        time.sleep(0.5)
    print()
    if street.status == 'failed':
        sys.exit(street.message)
    print(f"Street map ready: {street.image_path}  ({street.image.shape[1]} x {street.image.shape[0]} px)")


if __name__ == '__main__':
    main()
