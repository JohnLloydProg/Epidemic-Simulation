"""
basemap.py — the real map under the graphics view (ui/renderer.py).

Two layers, both placed with the same UTM -> simulation transform as the network (sim_data/base/meta.json):

1. Street map tiles (BASEMAP_PROVIDER). The first time, the tiles covering the study area are downloaded in the
   background (the window keeps running and shows the progress), stitched and warped to simulation coordinates,
   then saved in sim_data/cache/basemap/. Later runs load the saved image and need no internet. If the download
   fails (offline), the barangay map below is shown instead and the download is tried again on the next start.
   Tiles are only fetched once per area/provider/zoom, which keeps within the tile servers' fair-use policies.

2. The real barangay boundaries: the study barangays (sim_data/base/zones.json) and every Manila barangay around
   them (od_bundle/zones.gpkg), drawn as a district-coloured map with white boundaries. This is the map when there
   are no tiles; over the tiles only the outlines and a light tint are drawn, so the street map stays readable.

Keys / panel: B switches the real map on and off (filter panel: "Real map").

CARTO API key: CARTO marks tiles requested without a key "API key required". Put the key in the (git-ignored) .env
file next to CONFIG_FILE_NAME, never in the code or a committed config:
    CARTO_API_KEY=<your CARTO Basemaps key>
("CARTO_API_KEY" in the config file also works; the environment variable wins.) Tiles fetched with and without a
key are cached separately, so adding the key replaces a watermarked map on the next start.

Config (optional):
    "BASEMAP_PROVIDER":  "voyager"      CARTO styles (need CARTO_API_KEY): voyager (streets + labels, default),
                                        voyager_nolabels, light, light_nolabels (pale; barangay names stand out);
                                        others: osm (OpenStreetMap standard), satellite (Esri World Imagery), none
    "BASEMAP_ZOOM":      16             tile zoom level (16 with @2x tiles = about 1.2 m per pixel)
    "BASEMAP_MARGIN_M":  500            map area beyond the network on each side
    "BASEMAP_OPACITY":   1.0            0-1, how strongly the street map shows

To prepare the map before a presentation (or on another computer): python tools/build_basemap.py
The map attribution required by the tile providers is drawn in the bottom-right corner while tiles are shown.
"""
from __future__ import annotations
import hashlib
import io
import json
import logging
import math
import sqlite3
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

LOGGER = logging.getLogger('Basemap')

_CARTO = 'https://basemaps.cartocdn.com/rastertiles/{style}/{{z}}/{{x}}/{{y}}@2x.png'
_CARTO_CREDIT = '© OpenStreetMap contributors, © CARTO'           # required by CARTO's terms on every map, free tier included
PROVIDERS = {
    'voyager':          {'url': _CARTO.format(style='voyager'), 'tile': 512, 'subdomains': '', 'carto': True,
                         'attribution': _CARTO_CREDIT},
    'voyager_nolabels': {'url': _CARTO.format(style='voyager_nolabels'), 'tile': 512, 'subdomains': '', 'carto': True,
                         'attribution': _CARTO_CREDIT},
    'light':            {'url': _CARTO.format(style='light_all'), 'tile': 512, 'subdomains': '', 'carto': True,
                         'attribution': _CARTO_CREDIT},
    'light_nolabels':   {'url': _CARTO.format(style='light_nolabels'), 'tile': 512, 'subdomains': '', 'carto': True,
                         'attribution': _CARTO_CREDIT},
    'osm':       {'url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'tile': 256, 'subdomains': '',
                  'attribution': '© OpenStreetMap contributors'},
    'satellite': {'url': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                  'tile': 256, 'subdomains': '', 'attribution': 'Imagery © Esri, Maxar, Earthstar Geographics'},
}
USER_AGENT = 'EpidemicSimulation-thesis/1.0 (pygame people-flow simulation; one-time tile cache)'
TARGET_M_PER_PX = 1.25          # resolution of the warped image (metres per pixel)
MAX_TILES = 400                 # refuse to download more than this (raise BASEMAP_ZOOM carefully)

# district colours for the barangay map (soft, readable under traffic)
DISTRICT_COLORS = [(238, 228, 205), (214, 232, 214), (215, 226, 242), (240, 220, 222), (228, 222, 240),
                   (244, 236, 210), (213, 236, 234), (236, 226, 214), (222, 234, 208), (232, 216, 236)]


# ===================================================================================== coordinates
def utm_to_lonlat(easting, northing, zone:int = 51, north:bool = True):
    """WGS84 UTM -> (lon, lat) in degrees (numpy arrays or floats). Snyder's series, error well under 1 cm."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x = np.asarray(easting, dtype=float) - 500000.0
    y = np.asarray(northing, dtype=float) - (0.0 if north else 10000000.0)
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * np.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * np.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * np.sin(6 * mu) + (1097 * e1 ** 4 / 512) * np.sin(8 * mu))
    sin1, cos1, tan1 = np.sin(phi1), np.cos(phi1), np.tan(phi1)
    n1 = a / np.sqrt(1 - e2 * sin1 ** 2)
    t1 = tan1 ** 2
    c1 = ep2 * cos1 ** 2
    r1 = a * (1 - e2) / (1 - e2 * sin1 ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * tan1 / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                     + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / cos1
    return np.degrees(lon) + (zone - 1) * 6 - 180 + 3, np.degrees(lat)


def lonlat_to_world_px(lon, lat, zoom:int, tile:int = 256):
    """Web Mercator: degrees -> global pixel coordinates at `zoom` (tiles `tile` px wide)."""
    n = tile * (2 ** zoom)
    lat_r = np.radians(np.clip(lat, -85.05, 85.05))
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


class SimProjection:
    """simulation (x right, y down, metres) <-> UTM, from sim_data/base/meta.json."""

    def __init__(self, meta:dict):
        t = meta['transform']
        self.origin_x, self.origin_y = float(t['origin_x']), float(t['origin_y'])
        crs = str(meta.get('crs', 'EPSG:32651'))
        code = int(crs.split(':')[-1])
        self.zone, self.north = code % 100, code // 100 == 326

    def to_lonlat(self, sx, sy):
        return utm_to_lonlat(np.asarray(sx) + self.origin_x, self.origin_y - np.asarray(sy), self.zone, self.north)

    def utm_to_sim(self, ex, ny):
        return ex - self.origin_x, self.origin_y - ny


# ===================================================================================== barangay polygons
def _gpkg_polygons(blob:bytes) -> list[list[tuple[float, float]]]:
    """Outer rings of a GeoPackage Polygon / MultiPolygon geometry (2D, Z or M dropped)."""
    if blob[:2] != b'GP':
        return []
    flags = blob[3]
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get((flags >> 1) & 7, 0)
    data, pos = blob, 8 + envelope
    rings_out = []

    def read_geometry(pos):
        order = '<' if data[pos] == 1 else '>'
        gtype = struct.unpack_from(order + 'I', data, pos + 1)[0]
        pos += 5
        base, dims = gtype % 1000, 2 + (1 if gtype // 1000 in (1, 2) else 2 if gtype // 1000 == 3 else 0)
        if gtype & 0x80000000:                   # EWKB-style Z flag
            base, dims = gtype & 0xFFFF, 3
        if base == 3:
            nrings = struct.unpack_from(order + 'I', data, pos)[0]
            pos += 4
            for r in range(nrings):
                npts = struct.unpack_from(order + 'I', data, pos)[0]
                pos += 4
                coords = struct.unpack_from(order + 'd' * (npts * dims), data, pos)
                pos += 8 * npts * dims
                if r == 0:
                    rings_out.append([(coords[i], coords[i + 1]) for i in range(0, len(coords), dims)])
        elif base == 6:
            count = struct.unpack_from(order + 'I', data, pos)[0]
            pos += 4
            for _ in range(count):
                pos = read_geometry(pos)
        return pos

    try:
        read_geometry(pos)
    except struct.error:
        return []
    return rings_out


class ContextZone:
    def __init__(self, name:str, district:str, rings:list, study:bool):
        self.name, self.district, self.rings, self.study = name, district, rings, study
        xs = [p[0] for ring in rings for p in ring]
        ys = [p[1] for ring in rings for p in ring]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        ring = max(rings, key=len)
        self.label_pos = polygon_label_point(ring)


def polygon_label_point(ring) -> tuple[float, float]:
    """Area centroid of a ring, moved inside it if the shape is concave enough to put the centroid outside."""
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area) < 1e-9:
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    cx, cy = cx / (3 * area), cy / (3 * area)
    if _inside(cx, cy, ring):
        return (cx, cy)
    best, best_len = None, -1.0                     # widest horizontal span through the middle rows
    ys = sorted(p[1] for p in ring)
    for k in range(1, 8):
        y = ys[0] + (ys[-1] - ys[0]) * k / 8
        xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]) if (y0 <= y < y1) or (y1 <= y < y0))
        for a, b in zip(xs[::2], xs[1::2]):
            if b - a > best_len:
                best, best_len = ((a + b) / 2, y), b - a
    return best or (cx, cy)


def _inside(x, y, ring) -> bool:
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def load_barangay_map(city, data_dir:Path, bundle_dir:Path | None, projection:SimProjection, extent) -> list[ContextZone]:
    """The real barangay boundaries in simulation coordinates, limited to `extent` (x0, y0, x1, y1): the study
    barangays (flagged study=True) and the Manila barangays around them. Full-detail polygons come from the OD
    bundle's zones.gpkg (zones.json keeps simplified outlines); a study barangay missing there uses its outline."""
    study = {}
    for region in (getattr(city, 'zones', {}) or {}).values():
        study[str(region.name)] = region
    detailed = {}
    path = Path(bundle_dir) / 'zones.gpkg' if bundle_dir else None
    if path is not None and path.exists():
        try:
            connection = sqlite3.connect(str(path))
            rows = connection.execute("select zone, kind, district, geom from zones").fetchall()
            connection.close()
        except sqlite3.Error as error:
            LOGGER.warning(f"Could not read barangays from {path}: {error}")
            rows = []
        for name, kind, district, geom in rows:
            if kind != 'barangay' or geom is None:
                continue
            rings = [[projection.utm_to_sim(e, n) for e, n in ring] for ring in _gpkg_polygons(bytes(geom))]
            if rings:
                detailed[str(name)] = (str(district or ''), rings)
    zones = []
    for name, region in study.items():
        district = str(getattr(region, 'district', '') or '')
        if name in detailed:
            zones.append(ContextZone(name, detailed[name][0] or district, detailed[name][1], True))
        elif len(getattr(region, 'polygon', None) or []) > 2:
            zones.append(ContextZone(name, district, [[tuple(p) for p in region.polygon]], True))
    x0, y0, x1, y1 = extent
    for name, (district, rings) in detailed.items():
        if name in study:
            continue
        zone = ContextZone(name, district, rings, False)
        bx0, by0, bx1, by1 = zone.bbox
        if bx1 >= x0 and bx0 <= x1 and by1 >= y0 and by0 <= y1:
            zones.append(zone)
    return zones


def district_color(district:str) -> tuple:
    digest = int(hashlib.md5(district.encode('utf-8')).hexdigest(), 16)
    return DISTRICT_COLORS[digest % len(DISTRICT_COLORS)]


# ===================================================================================== street map tiles
class WatermarkError(RuntimeError):
    """The tile server would only give placeholder ('API key required') tiles."""


def mask_key(key:str) -> str:
    return f"{key[:4]}...{key[-4:]}" if len(key) > 10 else ('set' if key else 'not set')


class TileBasemap:
    """Downloads (once), warps and caches the street map for an extent. Runs in a background thread; `status`
    tells the panel what is going on, `image` (H x W x 3 uint8, simulation-aligned) is set when it is ready."""

    def __init__(self, projection:SimProjection, extent, cache_dir:Path, provider:str = 'voyager', zoom:int = 16,
                 api_key:str | None = None):
        self.projection = projection
        self.provider = provider if provider in PROVIDERS else 'voyager'
        self.spec = dict(PROVIDERS[self.provider])        # own copy: the CARTO check may switch the tile form
        key = (api_key or '').strip().strip('\'"').strip()   # tolerate CARTO_API_KEY = 'key' style lines
        self.api_key = key if self.spec.get('carto') else ''
        self._variant = ''                                 # CARTO tile form that showed a real map ('@2x' or '')
        if self.spec.get('carto'):
            LOGGER.info(f"CARTO key: {mask_key(self.api_key)}")
        self.zoom = int(zoom)
        x0, y0, x1, y1 = extent
        self.extent = (math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))
        self.cache_dir = Path(cache_dir)
        self.m_per_px = TARGET_M_PER_PX
        self.status = 'waiting'                 # waiting, downloading, ready, failed
        self.progress = 0.0
        self.message = ''
        self.image:np.ndarray | None = None
        self._thread = None
        self.fetch = self._fetch_tile           # replaceable (tests)

    @property
    def attribution(self) -> str:
        return self.spec['attribution']

    def _key(self) -> str:
        raw = json.dumps([self.provider, self.zoom, self.extent, self.m_per_px, self.projection.origin_x,
                          self.projection.origin_y, bool(self.api_key), 'checked-v2'], sort_keys=True)
        # 'checked-v2': only maps whose tiles passed the watermark check are saved under this name
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    @property
    def image_path(self) -> Path:
        return self.cache_dir / f"basemap_{self.provider}_z{self.zoom}_{self._key()}.png"

    def start(self):
        """Load the saved map, or download it in the background."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name='basemap', daemon=True)
        self._thread.start()

    def build_now(self):
        """Same as start() but in this thread (tools/build_basemap.py)."""
        self._run()

    def _run(self):
        try:
            if self.image_path.exists():
                self.status, self.message = 'loading', 'Loading the saved map...'
                self.image = self._load_png(self.image_path)
            else:
                self.status = 'downloading'
                if self.spec.get('carto'):
                    self._check_carto()
                self.image = self._build()
                self.image_path.parent.mkdir(parents=True, exist_ok=True)
                self._save_png(self.image, self.image_path)
                LOGGER.info(f"Street map saved to {self.image_path}")
            self.status, self.message, self.progress = 'ready', '', 1.0
        except WatermarkError as error:             # never show a watermarked map: the barangay map is used
            self.status = 'failed'
            self.message = str(error)
            LOGGER.warning(str(error))
        except Exception as error:                  # offline, blocked, ...: the barangay map is used
            self.status = 'failed'
            self.message = f"Street map not available ({type(error).__name__}); showing the barangay map."
            LOGGER.warning(f"Street map not available: {error}")

    # ---------------------------------------------------------------- building
    def _tile_range(self):
        x0, y0, x1, y1 = self.extent
        xs = np.array([x0, x1, x0, x1, (x0 + x1) / 2, (x0 + x1) / 2, x0, x1])
        ys = np.array([y0, y0, y1, y1, y0, y1, (y0 + y1) / 2, (y0 + y1) / 2])
        lon, lat = self.projection.to_lonlat(xs, ys)
        px, py = lonlat_to_world_px(lon, lat, self.zoom, self.spec['tile'])
        size = self.spec['tile']
        return (int(px.min() // size), int(py.min() // size), int(px.max() // size), int(py.max() // size))

    @property
    def _tile_folder(self) -> Path:
        # checked CARTO tiles in their own folder: tiles saved by older versions (possibly watermarked) are not used
        name = self.provider + (f"_key{self._variant or '1x'}_v2" if self.api_key else '')
        return self.cache_dir / 'tiles' / name / str(self.zoom)

    def _url(self, x:int, y:int) -> str:
        subdomains = self.spec['subdomains']
        url = self.spec['url'].format(s=subdomains[(x + y) % len(subdomains)] if subdomains else '', z=self.zoom, x=x, y=y)
        if self.api_key:
            url += '?key=' + urllib.parse.quote(self.api_key, safe='')
        return url

    @staticmethod
    def _download(url:str, tries:int = 3) -> bytes:
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        last = None
        for attempt in range(tries):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code in (401, 403):            # refused: retrying will not help
                    raise
                last = error
            except Exception as error:
                last = error
            time.sleep(1.5 * (attempt + 1))
        raise last

    def _fetch_tile(self, x:int, y:int) -> bytes:
        cache = self._tile_folder / str(x) / f"{y}.png"
        if cache.exists() and cache.stat().st_size > 0:
            return cache.read_bytes()
        data = self._download(self._url(x, y))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return data

    def _check_carto(self):
        """Make sure CARTO sends a real map, not its 'API key required' tile, before downloading the area.
        Two neighbouring land tiles of a street map are never identical; CARTO's watermark tile always is.
        The high-resolution (@2x) form is tried first, then the standard one."""
        if not self.api_key:
            raise WatermarkError("CARTO needs an API key (it marks tiles 'API key required'). Add "
                                 "CARTO_API_KEY=<your key> to .env; showing the barangay map.")
        x0, y0, x1, y1 = self.extent
        lon, lat = self.projection.to_lonlat((x0 + x1) / 2, (y0 + y1) / 2)    # middle of the study area (land)
        base = PROVIDERS[self.provider]['url']
        refused = None
        for variant, size in (('@2x', 512), ('', 256)):
            self.spec['url'], self.spec['tile'] = base.replace('@2x.png', variant + '.png'), size
            px, py = lonlat_to_world_px(lon, lat, self.zoom, size)
            tx, ty = int(px // size), int(py // size)
            try:
                first = self._download(self._url(tx, ty), tries=2)
                second = self._download(self._url(tx + 1, ty), tries=2)
            except urllib.error.HTTPError as error:
                refused = error.code
                continue
            if first != second:
                self._variant = variant
                LOGGER.info(f"CARTO accepted the key ({'high-resolution' if variant else 'standard'} tiles).")
                return
        detail = f"HTTP {refused}" if refused else "it sent the 'API key required' tile"
        raise WatermarkError(f"CARTO did not accept the key {mask_key(self.api_key)} ({detail}). Check it in your "
                             "CARTO dashboard (an active Basemaps key, no domain/app restriction); showing the "
                             "barangay map.")

    def _build(self) -> np.ndarray:
        from PIL import Image
        tx0, ty0, tx1, ty1 = self._tile_range()
        size = self.spec['tile']
        tiles = [(x, y) for y in range(ty0, ty1 + 1) for x in range(tx0, tx1 + 1)]
        if len(tiles) > MAX_TILES:
            raise ValueError(f"{len(tiles)} tiles needed (more than {MAX_TILES}); lower BASEMAP_ZOOM")
        LOGGER.info(f"Downloading {len(tiles)} {self.provider} map tiles (zoom {self.zoom}) once...")
        mosaic = np.full(((ty1 - ty0 + 1) * size, (tx1 - tx0 + 1) * size, 3), 235, dtype=np.uint8)
        done = 0

        def get(tile):
            return tile, self.fetch(*tile)

        digests = []
        with ThreadPoolExecutor(max_workers=2) as pool:            # gentle on the tile server
            for (x, y), data in pool.map(get, tiles):
                digests.append(hashlib.sha1(data).hexdigest())
                image = Image.open(io.BytesIO(data)).convert('RGB')
                if image.size != (size, size):
                    image = image.resize((size, size))
                oy, ox = (y - ty0) * size, (x - tx0) * size
                mosaic[oy:oy + size, ox:ox + size] = np.asarray(image)
                done += 1
                self.progress = done / len(tiles) * 0.9
                self.message = f"Downloading the street map: {done}/{len(tiles)} tiles"
        if len(tiles) >= 6:                     # (nearly) every tile the same picture: a placeholder, not a map
            most = max(digests.count(d) for d in set(digests))
            if most > 0.9 * len(tiles):
                import shutil
                shutil.rmtree(self._tile_folder, ignore_errors=True)
                raise WatermarkError("The map server sent the same placeholder tile everywhere (watermark / "
                                     "'API key required'); showing the barangay map.")
        self.message = 'Placing the street map...'
        return self._warp(mosaic, tx0 * size, ty0 * size)

    def _warp(self, mosaic:np.ndarray, origin_px:float, origin_py:float) -> np.ndarray:
        """Resample the Web Mercator mosaic onto the simulation grid (bilinear), row block by row block."""
        x0, y0, x1, y1 = self.extent
        width = int(math.ceil((x1 - x0) / self.m_per_px))
        height = int(math.ceil((y1 - y0) / self.m_per_px))
        out = np.empty((height, width, 3), dtype=np.uint8)
        sx = x0 + (np.arange(width) + 0.5) * self.m_per_px
        mh, mw = mosaic.shape[:2]
        block = 128
        for r0 in range(0, height, block):
            rows = np.arange(r0, min(height, r0 + block))
            sy = y0 + (rows + 0.5) * self.m_per_px
            gx, gy = np.meshgrid(sx, sy)
            lon, lat = self.projection.to_lonlat(gx, gy)
            px, py = lonlat_to_world_px(lon, lat, self.zoom, self.spec['tile'])
            px, py = px - origin_px - 0.5, py - origin_py - 0.5
            ix = np.clip(np.floor(px).astype(int), 0, mw - 2)
            iy = np.clip(np.floor(py).astype(int), 0, mh - 2)
            fx = np.clip(px - ix, 0, 1)[..., None]
            fy = np.clip(py - iy, 0, 1)[..., None]
            top = mosaic[iy, ix] * (1 - fx) + mosaic[iy, ix + 1] * fx
            bottom = mosaic[iy + 1, ix] * (1 - fx) + mosaic[iy + 1, ix + 1] * fx
            out[rows[0]:rows[-1] + 1] = (top * (1 - fy) + bottom * fy).astype(np.uint8)
            self.progress = 0.9 + 0.1 * (rows[-1] + 1) / height
        return out

    @staticmethod
    def _save_png(image:np.ndarray, path:Path):
        from PIL import Image
        Image.fromarray(image).save(str(path), optimize=True)

    @staticmethod
    def _load_png(path:Path) -> np.ndarray:
        from PIL import Image
        return np.asarray(Image.open(str(path)).convert('RGB'))


def map_extent(city, railway, margin:float) -> tuple[float, float, float, float]:
    """Area the street map covers: every node and study barangay outline, plus `margin` metres."""
    xs, ys = [], []
    for graph in (city, railway):
        for node in graph.nodes.values():
            xs.append(node.pos[0])
            ys.append(node.pos[1])
    for region in (getattr(city, 'zones', {}) or {}).values():
        for x, y in (getattr(region, 'polygon', None) or []):
            xs.append(x)
            ys.append(y)
    return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def carto_api_key(config_module=None) -> str:
    """CARTO Basemaps key: the CARTO_API_KEY environment variable (.env), else "CARTO_API_KEY" in the config."""
    import os
    key = os.environ.get('CARTO_API_KEY', '')
    if not key and config_module is not None:
        try:
            key = config_module.get('CARTO_API_KEY', '') or ''
        except ValueError:                        # config not loaded
            key = ''
    return str(key).strip()


def load_meta(data_dir:Path) -> dict | None:
    path = Path(data_dir) / 'base' / 'meta.json'
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        meta = json.load(f)
    return meta if 'transform' in meta else None
