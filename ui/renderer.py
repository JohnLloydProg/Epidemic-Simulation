"""
renderer.py — draws the map, routes, vehicles and people in one of two views (V, or the View button):

  bare      the original look: white background, roads as black lines, nodes as circles coloured by how many
            people wait there, vehicles and walkers as dots, routes coloured by occupancy.
  graphics  sprites from assets/sprites (ui/sprites.py): roads drawn with the road tile (width by road class),
            the LRT with the rails tile, stations, barangay outlines, and every vehicle and walker as a top-down
            sprite turned to its heading. Vehicles keep to the right lane; walkers keep to the roadside.
            People waiting at a node are a crowd marker (size and colour by count) with the count when zoomed in.

Both views apply the filters of ui/view_filter.py (modes, routes, route lines; panel: L).
Drawing only: nothing here changes the simulation.

Sizes in the graphics view are multiplied by the Size setting (ui/view_filter.py; [ / ] keys): roads, rails,
stations, vehicles, people and crowd markers grow together, and lane offsets follow the road width, so traffic
stays on its side of the road at any size.

Real map (graphics view, B): the street map and the real barangay boundaries under the network (ui/basemap.py).
With the street map shown, the simulated roads are drawn slightly see-through so the map's streets, parks and
landmarks show around them. Barangay names are drawn last, above the traffic, in a bold font with a white halo
that grows with the zoom (study barangays always; the neighbouring barangays once zoomed in).

Config (optional):
    "GRAPHICS_VIEW": true       start in the graphics view (false: start in the bare view)
    "GRAPHICS_SCALE": 2.0       starting Size (1.0 = real-world proportions)
    "BASEMAP_...":              street map settings, see ui/basemap.py

The graphics view caches the road layer (rebuilt only when the camera or the network changes) and the textured
road pieces per edge (rebuilt only when the zoom changes), so panning and playing stay cheap.
"""
from __future__ import annotations
import logging
import math
from pathlib import Path

import numpy as np
import pygame as pg

import configuration as config
from ui import basemap
from ui.button import ui_font
from ui.sprites import SpriteBank, heading_angle
from ui.view_filter import ROUTE_COLORS, route_key, route_mode, vehicle_mode

GROUND = (233, 235, 227)
ZONE_OUTLINE = (196, 202, 188)
CASING = (70, 67, 66)
ASPHALT = (103, 97, 95)                 # the road tile's surface colour
FOOTPATH = (150, 150, 140)
BALLAST = (150, 140, 128)
STATION_FILL = (250, 250, 250)
STATION_EDGE = (40, 40, 40)
HIGHLIGHT = (255, 190, 0)
NO_MAP_GROUND = (226, 229, 224)         # outside every barangay in the barangay map (bay, other cities)
STUDY_OUTLINE = (88, 60, 150)
CONTEXT_OUTLINE = (255, 255, 255)
ROAD_ALPHA_ON_MAP = 215                 # simulated roads over the street map: slightly see-through
LABEL_COLOR = (34, 34, 58)
HOT_LABEL = (185, 20, 20)
LIMIT_LABEL = (110, 30, 160)
CONTEXT_LABEL = (105, 105, 115)
LOGGER = logging.getLogger('Renderer')

# road widths in metres (both directions) and the narrowest they are drawn, by OSM highway class
ROAD_WIDTH_M = {'trunk': 16, 'trunk_link': 8, 'primary': 13, 'primary_link': 7, 'secondary': 11,
                'secondary_link': 6, 'tertiary': 8, 'tertiary_link': 6, 'unclassified': 6, 'residential': 5}
ROAD_MIN_PX = {'trunk': 6, 'trunk_link': 3, 'primary': 5, 'primary_link': 3, 'secondary': 4, 'secondary_link': 3,
               'tertiary': 3, 'tertiary_link': 2, 'unclassified': 2, 'residential': 2}
TEXTURE_MIN_PX = 10                     # narrower roads are plain asphalt (the markings would be a smudge)
CHUNK_PX = 256                          # textured roads are drawn in pieces of at most this length
RAIL_WIDTH_M, RAIL_MIN_PX = 7, 7
STATION_M, STATION_MIN_PX = 16, 6


def _seg_visible(a, b, pad:float, w:int, h:int) -> bool:
    return not (max(a[0], b[0]) < -pad or min(a[0], b[0]) > w + pad or max(a[1], b[1]) < -pad or min(a[1], b[1]) > h + pad)


class SceneRenderer:
    def __init__(self, sim):
        self.sim = sim
        self.sprites = SpriteBank()
        self.counts:dict[str, int] = {}          # people / vehicles per mode in the last frame (all, shown or not)
        self.route_counts:dict[str, int] = {}    # vehicles per route key in the last frame
        self.highlight_route:str | None = None   # route key to outline (hovered in the filter panel)
        self._road_layer:pg.Surface | None = None
        self._road_key = None
        self._chunks:dict = {}                   # edge id -> (width, chunk length, rotated textured piece)
        self._chunk_zoom = None
        self._tile_cache:dict = {}
        self._overlay:pg.Surface | None = None
        self._label_font = pg.font.Font(None, 14)
        # real map (ui/basemap.py)
        self._map_city = None                   # city graph the barangay map was made for
        self.map_zones:list = []                # basemap.ContextZone, study barangays first
        self.street_map:basemap.TileBasemap | None = None
        self._street_levels:list[pg.Surface] = []   # street map image and its halves (mipmaps)
        self._map_layer:pg.Surface | None = None
        self._map_key = None
        self._labels:dict = {}                  # rendered label surfaces
        self._map_origin = (0.0, 0.0)
        self._px_stamp = None                   # (zoom, size) the memoised pixel sizes below are for
        self._road_px:dict = {}
        self._sprite_px:dict = {}

    @property
    def filter(self):
        return self.sim.view_filter

    @property
    def camera(self):
        return self.sim.graph.camera

    # ================================================================== background and network
    def draw_background(self, window:pg.Surface):
        if not self.filter.graphics:
            window.fill((255, 255, 255))
            return
        self._prepare_map()
        window.fill(GROUND if not self.filter.real_map else NO_MAP_GROUND)
        self.draw_map(window)

    # ================================================================== real map
    def _prepare_map(self):
        """Load the barangay map for the open case and start the street map (once)."""
        city = self.sim.graph
        if self._map_city is city:
            return
        self._map_city = city
        self._map_key = None
        try:
            data_dir = Path(config.get('DATA_DIR', 'sim_data'))
            meta = basemap.load_meta(data_dir)
            if meta is None:
                self.map_zones = []
                return
            projection = basemap.SimProjection(meta)
            margin = float(config.get('BASEMAP_MARGIN_M', 500))
            extent = basemap.map_extent(city, self.sim.railway_graph, margin)
            bundle = config.get('OD_BUNDLE_DIR')
            self.map_zones = basemap.load_barangay_map(city, data_dir, Path(bundle) if bundle else None, projection, extent)
            self.map_zones.sort(key=lambda z: not z.study)
            provider = str(config.get('BASEMAP_PROVIDER', 'voyager')).lower()
            if provider != 'none':
                street = basemap.TileBasemap(projection, extent, data_dir / 'cache' / 'basemap', provider,
                                             int(config.get('BASEMAP_ZOOM', 16)), basemap.carto_api_key(config))
                if self.street_map is None or street.image_path != self.street_map.image_path:
                    self.street_map, self._street_levels = street, []
                    street.start()
        except Exception:
            LOGGER.exception('Real map not available; drawing without it.')
            self.map_zones = []

    def map_status(self) -> str:
        """Short state of the real map for the filter panel."""
        street = self.street_map
        if street is None:
            return 'barangays' if self.map_zones else 'off'
        if street.status == 'ready':
            return 'streets'
        if street.status in ('downloading', 'loading', 'waiting'):
            return f"{street.progress:.0%}" if street.status == 'downloading' else 'loading'
        return 'barangays'

    def _street_ready(self) -> bool:
        street = self.street_map
        if street is None or street.status != 'ready' or street.image is None:
            return False
        if not self._street_levels:                  # first frame after loading: make the surfaces here
            image = street.image
            surface = pg.surfarray.make_surface(image.transpose(1, 0, 2)).convert()
            levels = [surface]
            while min(levels[-1].get_size()) > 400:
                w, h = levels[-1].get_size()
                levels.append(pg.transform.smoothscale(levels[-1], (max(1, w // 2), max(1, h // 2))))
            self._street_levels = levels
            self._map_key = None
        return True

    def draw_map(self, window:pg.Surface):
        """Blit the map layer. It is drawn with a margin of half a screen around the view at the current zoom, so
        panning only moves it; it is rebuilt when the zoom or the map changes, or the view leaves the margin."""
        cam, vf = self.camera, self.filter
        w, h = window.get_size()
        streets = vf.real_map and self._street_ready()
        key = ((w, h), cam.zoom, vf.real_map, streets, id(self._map_city), len(self.map_zones))
        if self._map_layer is not None and key == self._map_key:
            dx = cam.x_offset - self._map_origin[0]           # where the layer's top-left is on screen now
            dy = cam.y_offset - self._map_origin[1]
            if -w <= dx <= 0 and -h <= dy <= 0:
                window.blit(self._map_layer, (round(dx), round(dy)))
                return
        # build: the layer's (0, 0) is half a screen up-left of the window
        ox, oy = cam.x_offset + w / 2, cam.y_offset + h / 2
        size = (w * 2, h * 2)
        self._map_layer = self._build_map(size, cam.zoom, ox, oy, streets)
        self._map_key, self._map_origin = key, (ox, oy)
        window.blit(self._map_layer, (round(-w / 2), round(-h / 2)))

    def _build_map(self, size, zoom:float, ox:float, oy:float, streets:bool) -> pg.Surface:
        """Street map (if ready) and barangay boundaries; screen = world * zoom + (ox, oy) on this surface."""
        vf = self.filter
        w, h = size
        layer = pg.Surface(size, pg.SRCALPHA)
        if streets:
            self._blit_streets(layer, zoom, ox, oy)
        zones = self.map_zones if vf.real_map else [z for z in self.map_zones if z.study]
        x0, y0 = -ox / zoom, -oy / zoom
        x1, y1 = (w - ox) / zoom, (h - oy) / zoom
        offset = np.array([ox, oy])
        visible = []
        for zone in zones:
            bx0, by0, bx1, by1 = zone.bbox
            if bx1 >= x0 and bx0 <= x1 and by1 >= y0 and by0 <= y1:
                if not hasattr(zone, 'arrays'):
                    zone.arrays = [np.asarray(ring, dtype=float) for ring in zone.rings]
                visible.append((zone, [(ring * zoom + offset).tolist() for ring in zone.arrays]))
        if vf.real_map and not streets:              # barangay map: district colours, white boundaries
            for zone, rings in visible:
                color = basemap.district_color(zone.district)
                if zone.study:
                    color = tuple(max(0, c - 22) for c in color)
                for ring in rings:
                    if len(ring) > 2:
                        pg.draw.polygon(layer, color, ring)
            for zone, rings in visible:
                for ring in rings:
                    if len(ring) > 2:
                        pg.draw.polygon(layer, CONTEXT_OUTLINE, ring, 1)
        tint = pg.Surface(size, pg.SRCALPHA)
        for zone, rings in visible:                  # study area: light tint (on the streets) and a clear outline
            if not zone.study:
                if streets:
                    for ring in rings:
                        if len(ring) > 2:
                            pg.draw.polygon(tint, (90, 90, 110, 70), ring, 1)
                continue
            for ring in rings:
                if len(ring) > 2:
                    if streets:
                        pg.draw.polygon(tint, STUDY_OUTLINE + (26,), ring)
                    pg.draw.polygon(tint, STUDY_OUTLINE + (200 if vf.real_map else 90,), ring, 2 if vf.real_map else 1)
        layer.blit(tint, (0, 0))
        return layer

    def _blit_streets(self, layer:pg.Surface, zoom:float, ox:float, oy:float):
        """The part of the street map on `layer`, scaled to the zoom (from the nearest mipmap level)."""
        street = self.street_map
        ex0, ey0 = street.extent[0], street.extent[1]
        m = street.m_per_px
        w, h = layer.get_size()
        wx0, wy0 = -ox / zoom, -oy / zoom
        wx1, wy1 = (w - ox) / zoom, (h - oy) / zoom
        bx0, by0 = max(0.0, (wx0 - ex0) / m), max(0.0, (wy0 - ey0) / m)
        full_w, full_h = self._street_levels[0].get_size()
        bx1, by1 = min(float(full_w), (wx1 - ex0) / m), min(float(full_h), (wy1 - ey0) / m)
        if bx1 - bx0 < 1 or by1 - by0 < 1:
            return
        scale = zoom * m                              # layer px per street-map px
        level = 0
        while level + 1 < len(self._street_levels) and scale * (2 ** (level + 1)) <= 1.0:
            level += 1
        f = 2 ** level
        source = self._street_levels[level]
        sw, sh = source.get_size()
        rx0, ry0 = int(bx0 // f), int(by0 // f)
        rx1, ry1 = min(sw, int(math.ceil(bx1 / f))), min(sh, int(math.ceil(by1 / f)))
        if rx1 <= rx0 or ry1 <= ry0:
            return
        crop = source.subsurface(pg.Rect(rx0, ry0, rx1 - rx0, ry1 - ry0))
        dest_x = (ex0 + rx0 * f * m) * zoom + ox
        dest_y = (ey0 + ry0 * f * m) * zoom + oy
        dest_w = max(1, round((rx1 - rx0) * f * scale))
        dest_h = max(1, round((ry1 - ry0) * f * scale))
        image = pg.transform.smoothscale(crop, (dest_w, dest_h))
        opacity = float(config.get('BASEMAP_OPACITY', 1.0))
        if opacity < 1:
            image.set_alpha(round(255 * max(0.0, opacity)))
        layer.blit(image, (round(dest_x), round(dest_y)))

    def draw_attribution(self, window:pg.Surface):
        """Credit line the tile providers require, and the street map's download progress."""
        if not (self.filter.graphics and self.filter.real_map) or self.street_map is None:
            return
        street = self.street_map
        color = (60, 60, 70)
        if street.status == 'ready':
            text = street.attribution
        elif street.status in ('downloading', 'loading'):
            text = street.message or 'Loading the street map...'
        elif street.status == 'failed' and street.message:
            text, color = street.message, (170, 70, 0)
        else:
            return
        font = ui_font(11)
        surface = font.render(text, True, color)
        w, h = window.get_size()
        box = surface.get_rect(bottomright=(w - 6, h - 4)).inflate(10, 4)
        back = pg.Surface(box.size, pg.SRCALPHA)
        back.fill((255, 255, 255, 190))
        window.blit(back, box.topleft)
        window.blit(surface, surface.get_rect(center=box.center))

    # ================================================================== barangay names
    def _label(self, text:str, size:int, color, bold:bool = True, halo:int = 2) -> pg.Surface:
        key = (text, size, color, bold, halo)
        cached = self._labels.get(key)
        if cached is None:
            if len(self._labels) > 1500:
                self._labels.clear()
            font = ui_font(size, bold=bold)
            body = font.render(text, True, color)
            edge = font.render(text, True, (255, 255, 255))
            cached = pg.Surface((body.get_width() + 2 * halo, body.get_height() + 2 * halo), pg.SRCALPHA)
            for dx in range(-halo, halo + 1):
                for dy in range(-halo, halo + 1):
                    if dx * dx + dy * dy <= halo * halo + 1 and (dx or dy):
                        cached.blit(edge, (halo + dx, halo + dy))
            cached.blit(body, (halo, halo))
            self._labels[key] = cached
        return cached

    def draw_zone_labels(self, window:pg.Surface):
        """Barangay names over everything else on the map (graphics view)."""
        vf = self.filter
        if not (vf.graphics and vf.zone_labels):
            return
        cam = self.camera
        w, h = window.get_size()
        zoom = cam.zoom
        size = int(min(30, max(14, 12 + 10 * zoom)))
        regions = {str(r.name): r for r in (getattr(self.sim.graph, 'zones', {}) or {}).values()}
        editor = getattr(self.sim, 'zone_editor', None)
        placed:list[pg.Rect] = []
        for zone in self.map_zones:
            if not zone.study and (not vf.real_map or zoom < 0.55):
                continue
            pos = cam.to_screen(zone.label_pos)
            if not (-80 <= pos[0] <= w + 80 and -40 <= pos[1] <= h + 40):
                continue
            region = regions.get(zone.name)
            if zone.study:
                text = editor.label_text(region) if (editor is not None and region is not None) else zone.name
                color = HOT_LABEL if getattr(region, 'is_hotspot', False) else \
                    LIMIT_LABEL if getattr(region, 'od_scale', 1.0) != 1.0 else LABEL_COLOR
                label = self._label("Brgy " + text if text[:1].isdigit() else text, size, color)
            else:
                label = self._label(zone.name.replace('Barangay ', 'Brgy '), max(11, size - 5), CONTEXT_LABEL, bold=False)
            rect = label.get_rect(center=pos)
            if not zone.study and any(rect.colliderect(other) for other in placed):
                continue                              # neighbours give way to the study barangays
            window.blit(label, rect)
            placed.append(rect)
            if zone.study and zoom >= 0.9 and zone.district:
                sub = self._label(zone.district, max(11, size - 7), (80, 80, 100), bold=False)
                sub_rect = sub.get_rect(midtop=(rect.centerx, rect.bottom - 3))
                window.blit(sub, sub_rect)
                placed.append(sub_rect)

    def draw_network(self, window:pg.Surface, font:pg.font.Font):
        """Roads and nodes (after the zone fills, before routes and vehicles)."""
        if self.filter.graphics:
            roads = self._roads(window.get_size())
            roads.set_alpha(ROAD_ALPHA_ON_MAP if (self.filter.real_map and self._street_ready()) else 255)
            window.blit(roads, (0, 0))
            return
        graph, camera = self.sim.graph, self.camera
        for edge in graph.edges.values():
            edge.draw(window, camera)
        show_load = self.filter.mode_on('waiting')
        for node in graph.nodes.values():
            if show_load:
                node.draw(window, font, camera)
            else:                                            # waiting people hidden: plain nodes
                pos = camera.to_screen(node.pos)
                radius = camera.scale(node.radius, minimum=2)
                pg.draw.circle(window, (255, 255, 255), pos, radius)
                pg.draw.circle(window, (0, 0, 0), pos, radius, camera.scale(2))
                if camera.show_labels():
                    text = font.render(str(node.id[1]), False, (0, 0, 0))
                    window.blit(text, text.get_rect(center=pos))

    @property
    def size(self) -> float:
        return getattr(self.filter, 'size', 1.0)

    def _check_px_stamp(self):
        """Forget the memoised pixel sizes when the zoom or Size changed (called once per frame / layer build)."""
        stamp = (self.camera.zoom, self.size)
        if stamp != self._px_stamp:
            self._px_stamp = stamp
            self._road_px.clear()
            self._sprite_px.clear()

    def sprite_px(self, name:str) -> int:
        """Drawn length of a sprite: its real size at this zoom times the Size setting, but never smaller than its
        minimum (which itself shrinks on the whole-map view so traffic does not turn into solid ribbons)."""
        value = self._sprite_px.get(name) if self._px_stamp is not None else None
        if value is None:
            if self._px_stamp is None:
                self._check_px_stamp()
            zoom, size = self._px_stamp
            value = SpriteBank.length_px(name, zoom * size, min(1.0, max(0.55, zoom / 0.6)) * size)
            self._sprite_px[name] = value
        return value

    def road_px(self, edge) -> int:
        value = self._road_px.get(edge.id) if self._px_stamp is not None else None
        if value is None:
            if self._px_stamp is None:
                self._check_px_stamp()
            zoom, size = self._px_stamp
            kind = getattr(edge, 'highway', None)
            kind = kind if isinstance(kind, str) else ''
            value = max(round(ROAD_MIN_PX.get(kind, 2) * size), round(ROAD_WIDTH_M.get(kind, 6) * zoom * size))
            self._road_px[edge.id] = value
        return value

    def _roads(self, size) -> pg.Surface:
        """The graphics-view road layer (transparent outside the roads), rebuilt when the view changes."""
        self._check_px_stamp()
        cam, city, rail = self.camera, self.sim.graph, self.sim.railway_graph
        key = (size, cam.zoom, self.size, round(cam.x_offset, 2), round(cam.y_offset, 2), id(city), len(city.edges),
               sum(map(id, city.edges.values())), len(rail.edges), len(getattr(city, 'zones', {}) or {}))
        if self._road_layer is not None and key == self._road_key:
            return self._road_layer
        if self._chunk_zoom != (cam.zoom, self.size):
            self._chunks.clear()
            self._tile_cache.clear()
            self._chunk_zoom = (cam.zoom, self.size)
        w, h = size
        layer = self._road_layer if (self._road_layer is not None and self._road_layer.get_size() == size) \
            else pg.Surface(size, pg.SRCALPHA)
        layer.fill((0, 0, 0, 0))

        # roads: casing, then surface (textured when wide enough), then round joints at the nodes
        roads, paths = [], []
        for edge in city.edges.values():
            a, b = cam.to_screen(edge.nodes[0].pos), cam.to_screen(edge.nodes[1].pos)
            if edge.id[0] == 'transfer':
                if _seg_visible(a, b, 4, w, h):
                    paths.append((a, b))
                continue
            width = self.road_px(edge)
            if _seg_visible(a, b, width, w, h):
                roads.append((width, edge, a, b))
        roads.sort(key=lambda item: item[0])                  # major roads drawn over minor ones
        joints:dict = {}
        for width, edge, a, b in roads:
            casing = 1 if width < 8 else 2
            pg.draw.line(layer, CASING, a, b, width + 2 * casing)
            for node, pos in zip(edge.nodes, (a, b)):
                if joints.get(node, (0,))[0] < width:
                    joints[node] = (width, pos)
        for width, pos in joints.values():
            pg.draw.circle(layer, CASING, pos, width / 2 + (1 if width < 8 else 2))
        tile = self.sprites.tile('road')
        for width, edge, a, b in roads:
            if tile is not None and width >= TEXTURE_MIN_PX:
                self._blit_textured(layer, edge.id, a, b, width, tile, 'road')
            else:
                pg.draw.line(layer, ASPHALT, a, b, width)
        for width, pos in joints.values():
            pg.draw.circle(layer, ASPHALT, pos, width / 2)
        for a, b in paths:                                    # station access paths
            pg.draw.line(layer, FOOTPATH, a, b, max(1, round(2 * cam.zoom * self.size)))

        # railway: ballast bed, then the rails tile
        rail_px = max(round(RAIL_MIN_PX * self.size), round(RAIL_WIDTH_M * cam.zoom * self.size))
        rails = self.sprites.tile('rails')
        for edge in rail.edges.values():
            if edge.id[0] != 'railway':
                continue
            a, b = cam.to_screen(edge.nodes[0].pos), cam.to_screen(edge.nodes[1].pos)
            if not _seg_visible(a, b, rail_px, w, h):
                continue
            pg.draw.line(layer, BALLAST, a, b, rail_px + max(2, round(rail_px / 4)))
            if rails is not None:
                self._blit_textured(layer, edge.id, a, b, rail_px, rails, 'rails')
        station_px = max(round(STATION_MIN_PX * self.size), round(STATION_M * cam.zoom * self.size))
        for node in rail.nodes.values():
            pos = cam.to_screen(node.pos)
            if -station_px <= pos[0] <= w + station_px and -station_px <= pos[1] <= h + station_px:
                pg.draw.circle(layer, STATION_FILL, pos, station_px / 2 + 1)
                pg.draw.circle(layer, STATION_EDGE, pos, station_px / 2 + 1, 2)
                name = getattr(node, 'name', None)
                if cam.show_labels() and isinstance(name, str) and name:
                    label = self._label_font.render(name, True, STATION_EDGE)
                    layer.blit(label, label.get_rect(midleft=(pos[0] + station_px / 2 + 4, pos[1])))

        self._road_layer, self._road_key = layer, key
        return layer

    def _scaled_tile(self, tile:pg.Surface, kind:str, width:int) -> pg.Surface:
        """The tile scaled to `width` across the track and turned so it runs along +x."""
        key = (kind, width)
        cached = self._tile_cache.get(key)
        if cached is None:
            if kind == 'road':                    # road.png runs vertically: across = its width
                length = max(1, round(tile.get_height() * width / tile.get_width()))
                cached = pg.transform.rotate(pg.transform.smoothscale(tile, (width, length)), 90)
            else:                                 # rails.png runs horizontally: across = its height
                length = max(1, round(tile.get_width() * width / tile.get_height()))
                cached = pg.transform.smoothscale(tile, (length, width))
            self._tile_cache[key] = cached
        return cached

    def _blit_textured(self, layer:pg.Surface, edge_id, a, b, width:int, tile:pg.Surface, kind:str):
        """Lay the tile along a->b: one rotated piece (cached per edge) repeated along the edge. The tiles are
        uniform along their length, so overlapping pieces show no seam."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1:
            return
        chunk = int(min(math.ceil(length), CHUNK_PX))
        cached = self._chunks.get((kind, edge_id))
        if cached is None or cached[0] != width or cached[1] != chunk:
            step = self._scaled_tile(tile, kind, width)
            strip = pg.Surface((chunk, width), pg.SRCALPHA)
            for x in range(0, chunk, step.get_width()):
                strip.blit(step, (x, 0))
            cached = (width, chunk, pg.transform.rotate(strip, heading_angle(dx, dy)))
            self._chunks[(kind, edge_id)] = cached
        piece = cached[2]
        ux, uy = dx / length, dy / length
        count = max(1, math.ceil(length / chunk))
        for k in range(count):
            t = min(chunk / 2 + k * chunk, length - chunk / 2)
            layer.blit(piece, piece.get_rect(center=(a[0] + ux * t, a[1] + uy * t)))

    # ================================================================== routes
    def draw_routes(self, window:pg.Surface):
        vf, cam = self.filter, self.camera
        if vf.route_lines:
            routes = [r for r in self.sim.routes if vf.route_visible(r)]
            if vf.graphics:                      # translucent lines in the mode's colour
                overlay = self._clear_overlay(window.get_size())
                width = max(2, round(cam.scale(3) * self.size * 0.75))
                for route in routes:
                    points = [cam.to_screen(n.pos) for n in route.ordered_nodes]
                    if len(points) > 1:
                        pg.draw.lines(overlay, ROUTE_COLORS[route_mode(route)] + (120,), False, points, width)
                window.blit(overlay, (0, 0))
            else:                                # original look: coloured by average occupancy
                for route in sorted(routes, key=lambda r: r.get_average_occupancy(), reverse=True):
                    route.draw(window, self.sim.graph)
        if self.highlight_route is not None:
            for route in self.sim.routes:
                if route_key(route) == self.highlight_route:
                    points = [cam.to_screen(n.pos) for n in route.ordered_nodes]
                    if len(points) > 1:
                        pg.draw.lines(window, HIGHLIGHT, False, points, max(5, cam.scale(6)))
                    sx, sy = cam.to_screen(route.spawn_node.pos)
                    pg.draw.rect(window, (200, 120, 0), pg.Rect(sx - 5, sy - 5, 10, 10))

    def _clear_overlay(self, size) -> pg.Surface:
        if self._overlay is None or self._overlay.get_size() != size:
            self._overlay = pg.Surface(size, pg.SRCALPHA)
        self._overlay.fill((0, 0, 0, 0))
        return self._overlay

    # ================================================================== people and vehicles
    def draw_movers(self, window:pg.Surface, time:int):
        """Waiting crowds, vehicles and walkers; also counts every category for the filter panel."""
        self._check_px_stamp()
        counts = {key: 0 for key in ('walking', 'waiting', 'car', 'tricycle', 'jeepney', 'bus', 'train')}
        route_counts:dict[str, int] = {}
        vf, cam = self.filter, self.camera
        w, h = window.get_size()

        self._draw_waiting(window, counts)

        for vehicle in self.sim.transportations:
            mode = vehicle_mode(vehicle)
            counts[mode] = counts.get(mode, 0) + 1
            route = getattr(vehicle, 'route', None)
            if route is not None:
                key = route_key(route)
                route_counts[key] = route_counts.get(key, 0) + 1
            if not vf.vehicle_visible(vehicle, mode):
                continue
            try:
                world = vehicle.update_position(time)
            except Exception:                    # e.g. not placed yet
                continue
            pos = cam.to_screen(world)
            if not (-60 <= pos[0] <= w + 60 and -60 <= pos[1] <= h + 60):
                continue
            if vf.graphics:
                self._draw_vehicle(window, vehicle, mode, pos)
            else:
                pg.draw.circle(window, vehicle.color, pos, 5)

        for agent in self.sim.agents:
            if not self._walking(agent):
                continue
            counts['walking'] += 1
            if not vf.mode_on('walking'):
                continue
            try:
                world = agent.update_position(time)
            except Exception:
                continue
            pos = cam.to_screen(world)
            if not (-20 <= pos[0] <= w + 20 and -20 <= pos[1] <= h + 20):
                continue
            if vf.graphics:
                self._draw_walker(window, agent, pos)
            else:
                pg.draw.circle(window, (200, 200, 200), pos, 5)

        self.counts, self.route_counts = counts, route_counts

    @staticmethod
    def _walking(agent) -> bool:
        return (agent.state == 'travelling' and agent.current_edge is not None and agent.transportation is None
                and agent.current_node is not None)

    def _heading(self, vehicle):
        """(edge, dx, dy) the vehicle is driving along (or about to, while waiting at a node)."""
        node, edge = vehicle.current_node, vehicle.current_edge
        if edge is not None and not vehicle.waiting:
            nxt = edge.get_adjacent_node(node)
            return edge, nxt.pos[0] - node.pos[0], nxt.pos[1] - node.pos[1]
        upcoming = None                                   # waiting / not moving yet: face the next road
        route = getattr(vehicle, 'route', None)
        if route is not None:
            upcoming = route.next_edge(vehicle.path_index)
        elif vehicle.path:
            upcoming = vehicle.path[0]
        if upcoming is not None and node in upcoming.nodes:
            nxt = upcoming.get_adjacent_node(node)
            return upcoming, nxt.pos[0] - node.pos[0], nxt.pos[1] - node.pos[1]
        last = getattr(vehicle, '_last_heading', None)
        return (last[0], last[1], last[2]) if last else (edge, 1.0, 0.0)

    def _draw_vehicle(self, window, vehicle, mode:str, pos):
        sprite_name = 'jeepney' if mode == 'jeepney' else mode
        try:
            edge, dx, dy = self._heading(vehicle)
        except ValueError:                                 # node not on the edge (stale state for one frame)
            edge, dx, dy = getattr(vehicle, '_last_heading', None) or (None, 1.0, 0.0)
        if dx == 0 and dy == 0:
            dx, dy = 1.0, 0.0
        vehicle._last_heading = (edge, dx, dy)
        sprite = self.sprites.get(sprite_name, self.sprite_px(sprite_name), heading_angle(dx, dy))
        if sprite is None:
            pg.draw.circle(window, vehicle.color, pos, 5)
            return
        x, y = pos
        if edge is not None and edge.id[0] == 'city':      # keep right: half a lane off the centre line
            length = math.hypot(dx, dy)
            offset = self.road_px(edge) / 4
            x, y = x - dy / length * offset, y + dx / length * offset
        window.blit(sprite, sprite.get_rect(center=(x, y)))

    def _draw_walker(self, window, agent, pos):
        edge, node = agent.current_edge, agent.current_node
        try:
            nxt = edge.get_adjacent_node(node)
        except ValueError:
            return
        dx, dy = nxt.pos[0] - node.pos[0], nxt.pos[1] - node.pos[1]
        length = math.hypot(dx, dy) or 1.0
        x, y = pos
        if edge.id[0] == 'city':                           # on the roadside, not in the traffic
            offset = self.road_px(edge) / 2 + 2
            x, y = x - dy / length * offset, y + dx / length * offset
        sprite = self.sprites.get('person', self.sprite_px('person'), heading_angle(dx, dy))
        if sprite is None:
            pg.draw.circle(window, (120, 120, 120), (x, y), 3)
        else:
            window.blit(sprite, sprite.get_rect(center=(x, y)))

    def _draw_waiting(self, window, counts:dict):
        """Count people waiting at nodes; in the graphics view draw a crowd marker at each such node
        (the bare view shows them as the nodes' load colour, drawn with the network)."""
        nodes = [node for graph in (self.sim.graph, self.sim.railway_graph) for node in graph.nodes.values() if node.agents]
        counts['waiting'] = sum(len(node.agents) for node in nodes)
        if not (self.filter.graphics and self.filter.mode_on('waiting') and nodes):
            return
        cam = self.camera
        w, h = window.get_size()
        overlay = self._clear_overlay(window.get_size())
        scale = min(1.0, max(0.4, cam.zoom / 0.8)) * max(1.0, self.size * 0.75)   # smaller on the whole map
        markers = []
        for node in nodes:
            pos = cam.to_screen(node.pos)
            if not (-30 <= pos[0] <= w + 30 and -30 <= pos[1] <= h + 30):
                continue
            n = len(node.agents)
            load = min(n / node.max_agents, 1)
            radius = min(18, 4 + 1.5 * math.sqrt(n)) * scale
            color = (int(60 + 195 * load), int(170 * (1 - load) + 40), 40)
            pg.draw.circle(overlay, color + (110,), pos, radius)
            pg.draw.circle(overlay, color + (230,), pos, radius, max(2, round(self.size)))
            markers.append((pos, n))
        window.blit(overlay, (0, 0))
        if not cam.show_labels():                         # zoomed out: the markers alone
            return
        person = self.sprites.get('person', self.sprite_px('person') + 2, -90)
        for pos, n in markers:
            if person is not None:
                window.blit(person, person.get_rect(center=pos))
            if n > 1 and cam.show_labels():
                label = self._label_font.render(str(n), True, (20, 20, 20))
                window.blit(label, label.get_rect(midleft=(pos[0] + 7, pos[1] - 7)))
