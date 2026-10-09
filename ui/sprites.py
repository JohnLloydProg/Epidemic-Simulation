"""
sprites.py — sprites and tiles for the graphics view (ui/renderer.py), loaded from assets/sprites/.

The vehicle and person sprites are small top-down pixel images drawn on an orange background (jeepney.png too, so
there is no transparency to rely on). When a sprite is loaded the background is keyed out: a shallow flood fill
from the image border removes the bright orange, and the orange-ish blend left on the outer ring (JPEG fringe) is
half faded. Images that already have transparency (train.png) are kept as they are. So a sprite can be replaced
by any image in the same style, or by a PNG with a transparent background.

Each vehicle sprite is turned once so its front points right (+x); drawing then scales it to a length in pixels
and rotates it to the heading, from a cache (rotations are rounded to ANGLE_STEP degrees).

    file            used for                       front of the vehicle in the image
    car.jpg         private cars                   up
    tricycle.jpg    tricycles                      down
    jeepney.png     jeepneys                       down
    bus.jpg         buses                          down
    train.png       LRT trains                     right
    person.jpg      people walking / waiting       down
    road.png        road surface tile, runs vertically (two lanes, edge lines, double yellow centre line)
    rails.png       track tile, runs horizontally

To swap a sprite, replace the file (or change SPRITES below: file, front direction, real length in metres and
the smallest length it is drawn at in pixels, so vehicles stay visible when zoomed out).
"""
from __future__ import annotations
import logging
import math
from collections import deque
from pathlib import Path

import numpy as np
import pygame as pg

LOGGER = logging.getLogger('Sprites')

ASSET_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'sprites'

# name: (file, where the front points in the image, real length in metres, smallest drawn length in px)
SPRITES = {
    'car':      ('car.jpg',      'up',     4.5, 10),
    'tricycle': ('tricycle.jpg', 'down',   2.8,  9),
    'jeepney':  ('jeepney.png',  'down',   7.5, 13),
    'bus':      ('bus.jpg',      'down',  12.0, 18),
    'train':    ('train.png',    'right', 60.0, 36),
    'person':   ('person.jpg',   'down',   0.9,  8),
}
TILES = {'road': 'road.png', 'rails': 'rails.png'}

_TURN_TO_RIGHT = {'right': 0, 'up': -90, 'down': 90, 'left': 180}   # pygame rotates counter-clockwise
ANGLE_STEP = 6            # degrees between cached rotations
KEY_DEPTH = 2             # the background flood fill goes at most this many pixels in from the border
CACHE_LIMIT = 4000        # cached scaled/rotated sprites before the cache is emptied


def _to_alpha_surface(image:pg.Surface) -> pg.Surface:
    surface = pg.Surface(image.get_size(), pg.SRCALPHA)
    surface.blit(image, (0, 0))
    return surface


def _orange(rgb:np.ndarray, min_saturation:float, min_value:float) -> np.ndarray:
    """True where a colour is the bright, saturated orange of the sprite backgrounds (hue 22-52 degrees)."""
    rgb = rgb / 255.0
    high, low = rgb.max(axis=2), rgb.min(axis=2)
    chroma = high - low
    saturation = np.where(high > 0, chroma / np.maximum(high, 1e-9), 0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    with np.errstate(invalid='ignore', divide='ignore'):
        hue = np.where(high == r, ((g - b) / chroma) % 6, np.where(high == g, (b - r) / chroma + 2, (r - g) / chroma + 4)) * 60
    return (chroma > 0) & (hue >= 22) & (hue <= 52) & (saturation >= min_saturation) & (high >= min_value)


def key_out_background(surface:pg.Surface) -> pg.Surface:
    """Copy of `surface` with its orange background made transparent, cropped to what is left.

    The sprites fill nearly their whole frame, and some vehicles are orange-brown themselves, so the flood fill
    starts at the border and goes at most KEY_DEPTH pixels in; what is left of the outer ring that is still
    orange-ish (the JPEG blend of body and background) is half faded."""
    surface = _to_alpha_surface(surface)
    w, h = surface.get_size()
    rgb = pg.surfarray.array3d(surface).astype(np.float64)         # indexed [x, y]
    alpha = pg.surfarray.array_alpha(surface).astype(np.int32)
    if (alpha < 128).any():                                         # already has transparency (train.png)
        return surface
    background = _orange(rgb, 0.55, 0.60)
    depth = np.full((w, h), -1)
    queue = deque()
    for x in range(w):
        for y in range(h):
            if (x in (0, w - 1) or y in (0, h - 1)) and background[x, y]:
                depth[x, y] = 0
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        if depth[x, y] >= KEY_DEPTH:
            continue
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and depth[nx, ny] < 0 and background[nx, ny]:
                depth[nx, ny] = depth[x, y] + 1
                queue.append((nx, ny))
    keyed = depth >= 0

    new_alpha = alpha.copy()
    new_alpha[keyed] = 0
    ring = np.zeros((w, h), bool)                                   # outer pixels: border or next to keyed ones
    ring[[0, -1], :] = True
    ring[:, [0, -1]] = True
    ring[1:, :] |= keyed[:-1, :]
    ring[:-1, :] |= keyed[1:, :]
    ring[:, 1:] |= keyed[:, :-1]
    ring[:, :-1] |= keyed[:, 1:]
    fringe = ring & ~keyed & _orange(rgb, 0.40, 0.35)
    new_alpha[fringe] = alpha[fringe] // 2

    view = pg.surfarray.pixels_alpha(surface)
    view[:, :] = new_alpha.astype(np.uint8)
    del view                                                        # unlock the surface
    box = surface.get_bounding_rect(min_alpha=1)
    return surface.subsurface(box).copy() if box.width and box.height else surface


class SpriteBank:
    """Loads the sprites on first use and hands out scaled, rotated copies."""

    def __init__(self, asset_dir:Path = ASSET_DIR):
        self.asset_dir = Path(asset_dir)
        self._base:dict[str, pg.Surface | None] = {}     # name -> sprite facing right (None: file missing)
        self._tiles:dict[str, pg.Surface | None] = {}
        self._cache:dict[tuple, pg.Surface] = {}
        self._loaded = False

    def _load(self):
        self._loaded = True
        for name, (file, front, _, _) in SPRITES.items():
            path = self.asset_dir / file
            try:
                image = key_out_background(pg.image.load(str(path)))
                self._base[name] = pg.transform.rotate(image, _TURN_TO_RIGHT[front])
            except (FileNotFoundError, pg.error) as error:
                LOGGER.warning(f"Sprite '{path}' not loaded ({error}); drawing a dot instead.")
                self._base[name] = None
        for name, file in TILES.items():
            path = self.asset_dir / file
            try:
                tile = _to_alpha_surface(pg.image.load(str(path)))
                if name == 'rails':                       # last column is a half-transparent seam
                    tile = tile.subsurface(pg.Rect(0, 0, tile.get_width() - 1, tile.get_height())).copy()
                self._tiles[name] = tile
            except (FileNotFoundError, pg.error) as error:
                LOGGER.warning(f"Tile '{path}' not loaded ({error}); drawing plain lines instead.")
                self._tiles[name] = None

    def tile(self, name:str) -> pg.Surface | None:
        if not self._loaded:
            self._load()
        return self._tiles.get(name)

    def has(self, name:str) -> bool:
        if not self._loaded:
            self._load()
        return self._base.get(name) is not None

    @staticmethod
    def length_px(name:str, zoom:float, min_scale:float = 1.0) -> int:
        _, _, metres, minimum = SPRITES[name]
        return max(round(minimum * min_scale), round(metres * zoom))

    def get(self, name:str, length_px:int, angle_deg:float = 0.0) -> pg.Surface | None:
        """The sprite `length_px` long (front to back), its front turned to `angle_deg` (0 = right,
        counter-clockwise on screen). None if the sprite file is missing."""
        if not self._loaded:
            self._load()
        base = self._base.get(name)
        if base is None:
            return None
        step = int(round(angle_deg / ANGLE_STEP)) * ANGLE_STEP % 360
        key = (name, length_px, step)
        sprite = self._cache.get(key)
        if sprite is None:
            if len(self._cache) > CACHE_LIMIT:
                self._cache.clear()
            width = max(2, round(length_px * base.get_height() / base.get_width()))
            sprite = pg.transform.rotate(pg.transform.smoothscale(base, (length_px, width)), step)
            self._cache[key] = sprite
        return sprite


def heading_angle(dx:float, dy:float) -> float:
    """Screen-space direction (y down) -> pygame rotation angle in degrees (counter-clockwise, 0 = right)."""
    return math.degrees(math.atan2(-dy, dx))
