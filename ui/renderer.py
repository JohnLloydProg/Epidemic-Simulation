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

Config (optional):
    "GRAPHICS_VIEW": true       start in the graphics view (false: start in the bare view)
    "GRAPHICS_SCALE": 2.0       starting Size (1.0 = real-world proportions)

The graphics view caches the road layer (rebuilt only when the camera or the network changes) and the textured
road pieces per edge (rebuilt only when the zoom changes), so panning and playing stay cheap.
"""
from __future__ import annotations
import math

import pygame as pg

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

    @property
    def filter(self):
        return self.sim.view_filter

    @property
    def camera(self):
        return self.sim.graph.camera

    # ================================================================== background and network
    def draw_background(self, window:pg.Surface):
        window.fill(GROUND if self.filter.graphics else (255, 255, 255))

    def draw_network(self, window:pg.Surface, font:pg.font.Font):
        """Roads and nodes (after the zone fills, before routes and vehicles)."""
        if self.filter.graphics:
            window.blit(self._roads(window.get_size()), (0, 0))
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

    def sprite_px(self, name:str) -> int:
        """Drawn length of a sprite: its real size at this zoom times the Size setting, but never smaller than its
        minimum (which itself shrinks on the whole-map view so traffic does not turn into solid ribbons)."""
        zoom = self.camera.zoom
        return SpriteBank.length_px(name, zoom * self.size, min(1.0, max(0.55, zoom / 0.6)) * self.size)

    def road_px(self, edge) -> int:
        kind = getattr(edge, 'highway', None)
        kind = kind if isinstance(kind, str) else ''
        return max(round(ROAD_MIN_PX.get(kind, 2) * self.size), round(ROAD_WIDTH_M.get(kind, 6) * self.camera.zoom * self.size))

    def _roads(self, size) -> pg.Surface:
        """The graphics-view road layer (transparent outside the roads), rebuilt when the view changes."""
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

        # barangay outlines
        for region in (getattr(city, 'zones', {}) or {}).values():
            polygon = getattr(region, 'polygon', None)
            if polygon and len(polygon) > 2:
                pg.draw.polygon(layer, ZONE_OUTLINE, [cam.to_screen(p) for p in polygon], 1)

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
