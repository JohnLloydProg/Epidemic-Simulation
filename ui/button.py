import pygame as pg
from typing import Callable

FONTS = {
    'small': pg.font.Font(pg.font.get_default_font(), 14),
    'medium': pg.font.Font(pg.font.get_default_font(), 18),
    'large': pg.font.Font(pg.font.get_default_font(), 24)
}

class ButtonBehavior:
    def __init__(self, left:float, top:float, width:float, height:float, on_press:Callable|None, args:tuple=()):
        self.rect = pg.Rect(left, top, width, height)
        self.clickable:bool = True
        self.args = args
        self.on_press = on_press
        self.inside = False
    
    def clicked(self, event:pg.event.Event, consumed:list) -> bool:
        mouse = getattr(event, 'pos', None) or pg.mouse.get_pos()     # where the click happened, not where the mouse is now
        if (event.type == pg.MOUSEMOTION and self.rect.collidepoint(mouse) and self.clickable):
            pg.mouse.set_cursor(pg.SYSTEM_CURSOR_HAND)
            self.inside = True
        elif (self.inside and not self.rect.collidepoint(mouse)):
            pg.mouse.set_cursor(pg.SYSTEM_CURSOR_ARROW)
            self.inside = False
        if (event.type == pg.MOUSEBUTTONDOWN and event.button == 1 and event not in consumed):
            if (self.rect.collidepoint(mouse) and self.clickable):
                if (self.on_press):
                    if (self.args):
                        self.on_press(*self.args)
                    else:
                        self.on_press()
                consumed.append(event)
                return True
        return False

class ImageButton(ButtonBehavior):
    def __init__(self, left:float, top:float, width:float, height:float, on_press:Callable, image:pg.Surface):
        super().__init__(left, top, width, height, on_press)
        self.image = image
    
    def draw(self, window:pg.Surface) -> None:
        window.blit(self.image, self.rect)


class TextButton(ButtonBehavior):
    def __init__(self, left: float, top: float, width: float, height: float, on_press: Callable, background_color:tuple[int, int, int], content:str, size:str='large', args:tuple=()):
        super().__init__(left, top, width, height, on_press, args)
        self.surface = pg.Surface((width, height), pg.SRCALPHA)
        self.surface.fill((0, 0, 0, 100))
        self.background_color = background_color
        self.content = content
        self.size = size
    
    def get_text(self) -> tuple[pg.Surface, pg.Rect]:
        text = FONTS.get(self.size).render(self.content, True, (0, 0, 0))
        return (text, text.get_rect(center=self.rect.center))
    
    def draw(self, window:pg.Surface) -> None:
        pg.draw.rect(window, self.background_color, self.rect)
        if (not self.clickable):
            window.blit(self.surface, self.rect)
        if (self.content):
            text, rect = self.get_text()
            window.blit(text, rect)
        

class ToggleButton(ButtonBehavior):
    def __init__(self, left:float, top:float, width:float, height:float, on_toggle:Callable, background_color:tuple[int, int, int], content:tuple[str, str], size:str='small'):
        super().__init__(left, top, width, height, None)
        self.surface = pg.Surface((width, height), pg.SRCALPHA)
        self.surface.fill((0, 0, 0, 80))
        self.background_color = background_color
        self.on_toggle = on_toggle
        self.content = content
        self.size = size
        self.down = False
    
    def get_text(self) -> tuple[pg.Surface, pg.Rect]:
        font = FONTS.get(self.size)
        text = font.render(self.content[0], True, (0, 0, 0)) if not self.down else font.render(self.content[1], True, (0, 0, 0))
        return (text, text.get_rect(center=self.rect.center))
    
    def clicked(self, event:pg.event.Event, consumed:list) -> bool:
        if (super().clicked(event, consumed)):
            self.down = not self.down
            if (self.on_toggle):
                self.on_toggle(self.down)
            return True
        return False
    
    def draw(self, window:pg.Surface) -> None:
        pg.draw.rect(window, self.background_color, self.rect)
        if (self.content):
            text, rect = self.get_text()
            window.blit(text, rect)



class ToolButton(TextButton):
    def __init__(self, left:float, top:float, width:float, height:float, on_press:Callable, background_color:tuple[int, int, int], content:str=''):
        super().__init__(left, top, width, height, on_press, background_color, content, args=(content,))
    
    def get_text(self) -> tuple[pg.Surface, pg.Rect]:
        text = FONTS.get('small').render(self.content, True, (0, 0, 0))
        return (text, text.get_rect(midtop=(self.rect.centerx, self.rect.bottom + 5)))


class DropMenu:
    def __init__(self, left:float, top:float, width:float, height:float, options:dict, tools:dict, background:tuple[int, int, int], content:str=''):
        self.rect = pg.Rect(left, top, width, height)
        self.background = background
        self.expanded:bool = False
        self.content = content
        self.selections = []
        self.dropdown_width = max(map(lambda option: FONTS.get('small').size(option)[0], options)) + 10
        self.dropdown_height = 0
        self.tools = tools
        self.hover = None
        for i, option in enumerate(options.items()):
            text = FONTS.get('small').render(option[0], True, (0, 0, 0))
            text_rect = pg.Rect(left, self.rect.bottom + (i * (text.get_height() + 10)), width, text.get_height() + 10)
            self.dropdown_height += text_rect.height
            self.selections.append((option[1], text, text_rect))
    
    def handle(self, event:pg.event.Event, consumed:list) -> bool:
        if (event.type == pg.MOUSEMOTION):
            mouse_pos = pg.mouse.get_pos()
            if (self.rect.collidepoint(mouse_pos)):
                self.expanded = True
            elif (self.expanded or self.rect.collidepoint(mouse_pos)):
                self.hover = None
                for option, _, text_rect in self.selections:
                    if (text_rect.collidepoint(mouse_pos)):
                        self.hover = option
                        break
                self.expanded = self.hover is not None
        if (event.type == pg.MOUSEBUTTONDOWN and event.button == 1 and event not in consumed):
            if (self.expanded):
                mouse_pos = pg.mouse.get_pos()
                self.hover = None
                for option, _, text_rect in self.selections:
                    if (text_rect.collidepoint(mouse_pos)):
                        
                        command = self.tools.get(option)
                        if (command):
                            command.call()
                        consumed.append(event)
                        self.expanded = False
                        return True

                

    def draw(self, window:pg.Surface) -> None:
        pg.draw.rect(window, self.background, self.rect)
        text = FONTS.get('small').render(self.content, True, (0, 0, 0))
        text_rect = text.get_rect(center=self.rect.center)
        window.blit(text, text_rect)
        if (self.expanded):
            for option, text, text_rect in self.selections:
                if (option == self.hover):
                    pg.draw.rect(window, tuple(color - 50 for color in self.background), text_rect)
                else:
                    pg.draw.rect(window, self.background, text_rect)
                window.blit(text, text.get_rect(midleft=(text_rect.left + 5, text_rect.centery)))
        

# =====================================================================================================================
# Toolbar buttons (the row at the top of the window) and small panel buttons, drawn smooth: shapes and icons are
# drawn at SUPERSAMPLE times the size and scaled down, then cached per size and state, so they cost a blit per frame.
# =====================================================================================================================
import math as _math

SUPERSAMPLE = 3
TOOLBAR_H = 34
_ICON_PX = 16

# style: (fill top, fill bottom, border, text) for the normal state; hover/pressed/active are derived from it
BUTTON_STYLES = {
    'neutral': ((255, 255, 255), (238, 240, 243), (176, 182, 191), (34, 40, 49)),
    'primary': ((52, 168, 98),   (34, 139, 80),   (24, 110, 62),   (255, 255, 255)),   # Play
    'warning': ((247, 178, 64),  (228, 146, 30),  (184, 112, 16),  (40, 28, 6)),       # Pause
    'active':  ((226, 237, 255), (205, 222, 252), (64, 120, 220),  (22, 64, 150)),     # a panel that is open
    'accent':  ((70, 136, 240),  (40, 110, 230),  (28, 84, 190),   (255, 255, 255)),   # selected segment
}
_FONT_CACHE:dict = {}


def ui_font(size:int, bold:bool = False) -> pg.font.Font:
    """A clean UI font (a common system sans if there is one, else pygame's default), cached by size."""
    key = (size, bold)
    if key not in _FONT_CACHE:
        font = None
        for name in ('segoeui', 'helveticaneue', 'arial', 'dejavusans', 'liberationsans'):
            path = pg.font.match_font(name, bold=bold)
            if path:
                font = pg.font.Font(path, size)
                break
        if font is None:
            font = pg.font.Font(None, round(size * 1.3))
            font.set_bold(bold)
        _FONT_CACHE[key] = font
    return _FONT_CACHE[key]


def _shade(color, factor:float):
    """factor > 1 lightens towards white, < 1 darkens."""
    if factor >= 1:
        return tuple(round(c + (255 - c) * (factor - 1)) for c in color)
    return tuple(round(c * factor) for c in color)


def _state_colors(style:str, state:str):
    top, bottom, border, text = BUTTON_STYLES.get(style, BUTTON_STYLES['neutral'])
    if state == 'hover':
        top, bottom = _shade(top, 1.08 if style != 'neutral' else 1.0), _shade(bottom, 1.10 if style != 'neutral' else 0.97)
        border = _shade(border, 0.85)
    elif state == 'pressed':
        top, bottom = _shade(bottom, 0.92), _shade(top, 0.95)        # gradient flips: looks pushed in
        border = _shade(border, 0.8)
    return top, bottom, border, text


def _rounded_gradient(size, radius:int, top, bottom, border, border_px:int) -> pg.Surface:
    """Supersampled rounded rectangle with a vertical gradient and a border."""
    w, h = size
    shape = pg.Surface((w, h), pg.SRCALPHA)
    pg.draw.rect(shape, (255, 255, 255, 255), shape.get_rect(), border_radius=radius)
    gradient = pg.Surface((w, h), pg.SRCALPHA)
    for y in range(h):
        t = y / max(1, h - 1)
        gradient.fill(tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,), pg.Rect(0, y, w, 1))
    shape.blit(gradient, (0, 0), special_flags=pg.BLEND_RGBA_MULT)
    if border_px:
        pg.draw.rect(shape, border + (255,), shape.get_rect(), border_px, border_radius=radius)
    return shape


def draw_icon(surface:pg.Surface, name:str, center, size:float, color):
    """Simple vector icons (drawn on the supersampled surface)."""
    cx, cy = center
    s = size / 2
    lw = max(2, round(size / 7))
    if name == 'play':
        pg.draw.polygon(surface, color, [(cx - s * 0.55, cy - s * 0.85), (cx - s * 0.55, cy + s * 0.85), (cx + s * 0.85, cy)])
    elif name == 'pause':
        bar = s * 0.42
        for dx in (-s * 0.45, s * 0.45):
            pg.draw.rect(surface, color, pg.Rect(cx + dx - bar / 2, cy - s * 0.8, bar, s * 1.6), border_radius=round(bar / 4))
    elif name == 'reset':                                   # circular arrow
        r = s * 0.72
        rect = pg.Rect(cx - r, cy - r, 2 * r, 2 * r)
        pg.draw.arc(surface, color, rect, _math.radians(75), _math.radians(360), lw)   # open at the top right
        ex, ey = cx + r, cy                                 # arrowhead on the right end, pointing up (clockwise ↻)
        head = s * 0.5
        pg.draw.polygon(surface, color, [(ex, ey - head * 1.05), (ex - head * 0.8, ey + head * 0.15),
                                         (ex + head * 0.8, ey + head * 0.15)])
    elif name == 'folder':
        body = pg.Rect(cx - s * 0.95, cy - s * 0.45, s * 1.9, s * 1.3)
        tab = pg.Rect(cx - s * 0.95, cy - s * 0.75, s * 0.85, s * 0.5)
        pg.draw.rect(surface, color, tab, border_radius=round(s * 0.15))
        pg.draw.rect(surface, color, body, border_radius=round(s * 0.18))
    elif name == 'eye':
        w, h = s * 1.9, s * 1.15
        pg.draw.ellipse(surface, color, pg.Rect(cx - w / 2, cy - h / 2, w, h), lw)
        pg.draw.circle(surface, color, (cx, cy), s * 0.38)
    elif name == 'filter':                                  # funnel
        pg.draw.polygon(surface, color, [(cx - s * 0.95, cy - s * 0.8), (cx + s * 0.95, cy - s * 0.8),
                                         (cx + s * 0.22, cy + s * 0.05), (cx + s * 0.22, cy + s * 0.85),
                                         (cx - s * 0.22, cy + s * 0.6), (cx - s * 0.22, cy + s * 0.05)])
    elif name == 'minus':
        pg.draw.rect(surface, color, pg.Rect(cx - s * 0.7, cy - lw / 2, s * 1.4, lw), border_radius=lw // 2)
    elif name == 'plus':
        pg.draw.rect(surface, color, pg.Rect(cx - s * 0.7, cy - lw / 2, s * 1.4, lw), border_radius=lw // 2)
        pg.draw.rect(surface, color, pg.Rect(cx - lw / 2, cy - s * 0.7, lw, s * 1.4), border_radius=lw // 2)
    elif name == 'close':
        for a, b in (((-1, -1), (1, 1)), ((-1, 1), (1, -1))):
            pg.draw.line(surface, color, (cx + a[0] * s * 0.55, cy + a[1] * s * 0.55), (cx + b[0] * s * 0.55, cy + b[1] * s * 0.55), lw)


_SURFACE_CACHE:dict = {}


def button_surface(size, style:str, state:str, radius:int = 8, icon:str | None = None, icon_x:float | None = None,
                   shadow:bool = True) -> pg.Surface:
    """The button background (and icon) at `size`, smooth-edged, cached. The label is drawn on top by the caller."""
    key = (tuple(size), style, state, radius, icon, icon_x, shadow)
    cached = _SURFACE_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_SURFACE_CACHE) > 400:
        _SURFACE_CACHE.clear()
    w, h = size
    S = SUPERSAMPLE
    pad = 3 if shadow else 0                              # room for the shadow under the button
    big = pg.Surface(((w + pad) * S, (h + pad) * S), pg.SRCALPHA)
    top, bottom, border, text = _state_colors(style, state)
    pressed = state == 'pressed'
    if shadow and not pressed:
        shade = pg.Surface((w * S, h * S), pg.SRCALPHA)
        pg.draw.rect(shade, (20, 30, 45, 45), shade.get_rect(), border_radius=radius * S)
        big.blit(shade, (0, 2 * S))
    dy = S if pressed else 0
    big.blit(_rounded_gradient((w * S, h * S), radius * S, top, bottom, border, S), (0, dy))
    if not pressed:                                       # thin highlight along the top edge
        pg.draw.line(big, _shade(top, 1.25) + (150,), (radius * S, S * 1.5), ((w - radius) * S, S * 1.5), S)
    if icon:
        ix = (icon_x if icon_x is not None else w / 2) * S
        draw_icon(big, icon, (ix, h * S / 2 + dy), _ICON_PX * S * (0.8 if h < 26 else 1.0), text)
    surface = pg.transform.smoothscale(big, (w + pad, h + pad))
    _SURFACE_CACHE[key] = surface
    return surface


def draw_small_button(window:pg.Surface, rect:pg.Rect, label:str = '', style:str = 'neutral', icon:str | None = None,
                      enabled:bool = True, font:pg.font.Font | None = None) -> None:
    """A small panel button (hover and pressed states from the mouse), e.g. in the filter panel."""
    mouse = pg.mouse.get_pos()
    hover = enabled and rect.collidepoint(mouse)
    state = 'pressed' if hover and pg.mouse.get_pressed()[0] else 'hover' if hover else 'normal'
    surface = button_surface(rect.size, style, state, radius=min(6, rect.height // 3), shadow=False,
                             icon=icon if not label else None)
    if not enabled:
        surface = surface.copy()
        surface.set_alpha(110)
    window.blit(surface, rect.topleft)
    if label:
        text_color = _state_colors(style, state)[3]
        text = (font or ui_font(12)).render(label, True, text_color)
        if not enabled:
            text.set_alpha(110)
        window.blit(text, text.get_rect(center=(rect.centerx, rect.centery + (1 if state == 'pressed' else 0))))


class ToolbarButton(ButtonBehavior):
    """A toolbar button with an icon and a label. `label`, `icon` and `style` may be callables, so a button can
    follow the program's state (Play / Pause, the current view, an open panel). `widest` lists every label the
    button can show, so its width does not jump when the label changes. `badge()` True puts a dot on the corner
    (e.g. the map is filtered); `tooltip` is shown after hovering a moment."""
    PAD_X = 12
    GAP = 7
    TOOLTIP_DELAY_MS = 450

    def __init__(self, left:float, top:float, on_press:Callable, label, icon=None, style='neutral',
                 widest:tuple = (), tooltip:str = '', badge:Callable | None = None, height:int = TOOLBAR_H):
        self.label, self.icon, self.style = label, icon, style
        self.tooltip = tooltip
        self.badge = badge
        self.font = ui_font(14, bold=True)
        labels = tuple(widest) or (self._value(label),)
        text_w = max(self.font.size(text)[0] for text in labels)
        icon_w = (_ICON_PX + self.GAP) if icon else 0
        super().__init__(left, top, self.PAD_X * 2 + icon_w + text_w, height, on_press)
        self._hover_since = None

    @staticmethod
    def _value(item):
        return item() if callable(item) else item

    def hovered(self) -> bool:
        return self.clickable and self.rect.collidepoint(pg.mouse.get_pos())

    def draw(self, window:pg.Surface) -> None:
        hover = self.hovered()
        now = pg.time.get_ticks()
        if hover and self._hover_since is None:
            self._hover_since = now
        elif not hover:
            self._hover_since = None
        state = 'pressed' if hover and pg.mouse.get_pressed()[0] else 'hover' if hover else 'normal'
        style, icon, label = self._value(self.style), self._value(self.icon), self._value(self.label)
        text = self.font.render(label, True, _state_colors(style, state)[3])
        content_w = (_ICON_PX + self.GAP if icon else 0) + text.get_width()
        left = (self.rect.width - content_w) / 2
        surface = button_surface(self.rect.size, style, state, icon=icon, icon_x=left + _ICON_PX / 2 if icon else None)
        window.blit(surface, self.rect.topleft)
        dy = 1 if state == 'pressed' else 0
        tx = self.rect.left + left + (_ICON_PX + self.GAP if icon else 0)
        window.blit(text, text.get_rect(midleft=(tx, self.rect.centery + dy)))
        if self.badge is not None and self.badge():
            spot = (self.rect.right - 5, self.rect.top + 5)
            pg.draw.circle(window, (255, 255, 255), spot, 6)
            pg.draw.circle(window, (230, 110, 20), spot, 4)

    def draw_tooltip(self, window:pg.Surface) -> None:
        if not self.tooltip or self._hover_since is None or not self.hovered():
            return
        if pg.time.get_ticks() - self._hover_since < self.TOOLTIP_DELAY_MS:
            return
        font = ui_font(12)
        text = font.render(self.tooltip, True, (245, 247, 250))
        box = pg.Rect(0, 0, text.get_width() + 16, text.get_height() + 10)
        box.midtop = (self.rect.centerx, self.rect.bottom + 8)
        box.clamp_ip(window.get_rect().inflate(-8, -8))
        tip = pg.Surface(box.size, pg.SRCALPHA)
        pg.draw.rect(tip, (32, 38, 48, 235), tip.get_rect(), border_radius=6)
        window.blit(tip, box.topleft)
        pg.draw.polygon(window, (32, 38, 48), [(self.rect.centerx - 5, box.top), (self.rect.centerx + 5, box.top),
                                               (self.rect.centerx, box.top - 5)])
        window.blit(text, text.get_rect(center=box.center))


def layout_row(buttons:list, left:int, top:int, gap:int = 8) -> None:
    """Place buttons left to right (their widths are already set)."""
    x = left
    for button in buttons:
        button.rect.topleft = (x, top)
        x = button.rect.right + gap


def draw_toolbar(window:pg.Surface, buttons:list) -> None:
    """A soft translucent strip behind a row of toolbar buttons, the buttons, then any tooltip on top."""
    if not buttons:
        return
    area = buttons[0].rect.unionall([b.rect for b in buttons[1:]]).inflate(14, 12)
    strip = pg.Surface(area.size, pg.SRCALPHA)
    pg.draw.rect(strip, (255, 255, 255, 170), strip.get_rect(), border_radius=12)
    pg.draw.rect(strip, (150, 160, 175, 120), strip.get_rect(), 1, border_radius=12)
    window.blit(strip, area.topleft)
    for button in buttons:
        button.draw(window)
    for button in buttons:
        button.draw_tooltip(window)
