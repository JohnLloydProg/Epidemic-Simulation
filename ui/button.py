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
        mouse = pg.mouse.get_pos()
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
        