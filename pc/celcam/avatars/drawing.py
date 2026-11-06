"""Lo que comparten los dos estilos: lápiz, mesa, teclado, mouse, escenarios y objetos."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


W, H = 960, 540


S = 2


INK = (24, 24, 34)


WHITE = (255, 255, 255)


ACCENT = (45, 212, 191)


ACCENT_DARK = (6, 40, 36)


FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"


FONT = "C:/Windows/Fonts/segoeui.ttf"


DESK_Y = 356


@dataclass
class State:
    keys: tuple[str, ...] = ()
    mouse: tuple[float, float] = (0.5, 0.5)
    click: str | None = None
    mouth: float = 0.0
    blink: bool = False
    look: tuple[float, float] = (0.0, 0.0)
    t: float = 0.0


class Pen:
    """Dibuja en coordenadas de 960x540; el factor hace que los bordes salgan suaves."""

    def __init__(self, image: Image.Image, scale: int = S) -> None:
        self.draw = ImageDraw.Draw(image)
        self.s = scale
        self.dx = 0.0
        self.dy = 0.0

    def _p(self, x: float, y: float) -> tuple[float, float]:
        return ((x + self.dx) * self.s, (y + self.dy) * self.s)

    def ellipse(self, cx, cy, rx, ry, fill=255, outline=None, width=4) -> None:
        box = [self._p(cx - rx, cy - ry), self._p(cx + rx, cy + ry)]
        self.draw.ellipse(box, fill=fill, outline=outline, width=int(width * self.s) if outline else 0)

    def rrect(self, x0, y0, x1, y1, radius, fill=255, outline=None, width=4) -> None:
        self.draw.rounded_rectangle(
            [self._p(x0, y0), self._p(x1, y1)],
            radius=radius * self.s,
            fill=fill,
            outline=outline,
            width=int(width * self.s) if outline else 0,
        )

    def polygon(self, points, fill=255, outline=None, width=4) -> None:
        scaled = [self._p(x, y) for x, y in points]
        self.draw.polygon(scaled, fill=fill)
        if outline:
            self.draw.line(scaled + scaled[:2], fill=outline, width=int(width * self.s), joint="curve")

    def line(self, points, fill=255, width=4) -> None:
        scaled = [self._p(x, y) for x, y in points]
        w = int(width * self.s)
        self.draw.line(scaled, fill=fill, width=w, joint="curve")
        for x, y in (scaled[0], scaled[-1]):
            self.draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=fill)

    def arc(self, cx, cy, rx, ry, start, end, fill=255, width=4) -> None:
        box = [self._p(cx - rx, cy - ry), self._p(cx + rx, cy + ry)]
        self.draw.arc(box, start, end, fill=fill, width=int(width * self.s))

    def text(self, x, y, value, size, fill, bold=True) -> None:
        font = ImageFont.truetype(FONT_BOLD if bold else FONT, int(size * self.s))
        self.draw.text(self._p(x, y), value, font=font, fill=fill, anchor="mm")


def gradient(size, top, bottom) -> Image.Image:
    mask = Image.linear_gradient("L").resize(size)
    return Image.composite(Image.new("RGB", size, bottom), Image.new("RGB", size, top), mask)


def blob(layer, paint, top, bottom=None, line=INK, width=5, shade=None, light=None, scale=S) -> None:
    """Una forma con degradé, sombra y brillo en los bordes, y contorno."""
    bottom = bottom or top
    mask = Image.new("L", layer.size, 0)
    paint(Pen(mask, scale))
    box = mask.getbbox()
    if not box:
        return
    margin = 34 * scale
    box = (
        max(box[0] - margin, 0),
        max(box[1] - margin, 0),
        min(box[2] + margin, layer.size[0]),
        min(box[3] + margin, layer.size[1]),
    )
    crop = mask.crop(box)
    tight = crop.getbbox()
    size = (tight[2] - tight[0], tight[3] - tight[1])
    fill = Image.new("RGB", crop.size, top)
    fill.paste(gradient(size, top, bottom), (tight[0], tight[1]))
    for spec, direction in ((shade, 1), (light, -1)):
        if spec:
            # Se puede pasar solo el color: el resto toma valores suaves.
            color, alpha, shift = (spec, 0.5, 5) if isinstance(spec[0], int) else spec
            offset = int(shift * scale) * direction
            edge = ImageChops.subtract(crop, ImageChops.offset(crop, -offset, -offset))
            edge = edge.filter(ImageFilter.GaussianBlur(shift * scale * 0.9))
            fill.paste(color, mask=ImageChops.multiply(edge, crop).point(lambda p: int(p * alpha)))
    if line:
        edge = crop.filter(ImageFilter.GaussianBlur(width * scale * 0.5)).point(lambda p: 255 if p > 28 else 0)
        layer.paste(line + (255,), box, edge.filter(ImageFilter.GaussianBlur(scale * 0.4)))
    layer.paste(fill, box, crop)


def glow(layer, paint, color, alpha=0.6, radius=20, scale=S) -> None:
    mask = Image.new("L", layer.size, 0)
    paint(Pen(mask, scale))
    soft = mask.filter(ImageFilter.GaussianBlur(radius * scale)).point(lambda p: int(p * alpha))
    layer.paste(tuple(color) + (255,) if layer.mode == "RGBA" else tuple(color), (0, 0), soft)


FW, FH = 960, 300


F = 3


BOARD_X, BOARD_Y, BOARD_W, BOARD_H = 352, 62, 520, 178


PAD = (62, 44, 300, 262)


ROWS = [
    list("1234567890"),
    list("QWERTYUIOP"),
    list("ASDFGHJKLÑ"),
    ["Shift", *"ZXCVBNM", "Enter"],
    ["Ctrl", "Alt", "Espacio", "Tab"],
]


def homography(src, dst) -> np.ndarray:
    rows, values = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        values.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.append(v)
    return np.linalg.solve(np.array(rows, float), np.array(values, float))


FLAT_CORNERS = [(0, 0), (FW, 0), (FW, FH), (0, FH)]


SCREEN_CORNERS = [(118, DESK_Y + 6), (842, DESK_Y + 6), (1016, 552), (-56, 552)]


TO_SCREEN = homography(FLAT_CORNERS, SCREEN_CORNERS)


def on_desk(x: float, y: float) -> tuple[float, float]:
    h = TO_SCREEN
    w = h[6] * x + h[7] * y + 1
    return (h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w


def warp(flat: Image.Image) -> Image.Image:
    coeffs = homography([(x * S, y * S) for x, y in SCREEN_CORNERS], [(x * F, y * F) for x, y in FLAT_CORNERS])
    return flat.transform((W * S, H * S), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)


def key_boxes() -> dict[str, tuple[float, float, float, float]]:
    """Dónde está cada tecla, mirando el teclado desde el lado del personaje."""
    boxes = {}
    margin, gap = 10, 4
    key_h = (BOARD_H - 2 * margin - 4 * gap) / 5
    inner = BOARD_W - 2 * margin
    for index, row in enumerate(ROWS):
        y = margin + index * (key_h + gap)
        if index == 3:
            sizes = [1.5] + [0.95] * 7 + [1.6]
        elif index == 4:
            sizes = [1.3, 1.1, 5.6, 1.3]
        else:
            sizes = [1.0] * 10
        unit = (inner - gap * (len(sizes) - 1)) / sum(sizes)
        x = margin
        for name, size in zip(row, sizes):
            boxes[name] = (x, y, x + size * unit, y + key_h)
            x += size * unit + gap
    return boxes


KEYS = key_boxes()


def key_on_desk(name: str) -> tuple[float, float, float, float]:
    """La misma tecla en la mesa: girada media vuelta, porque el teclado mira al personaje."""
    x0, y0, x1, y1 = KEYS[name]
    return BOARD_X + BOARD_W - x1, BOARD_Y + BOARD_H - y1, BOARD_X + BOARD_W - x0, BOARD_Y + BOARD_H - y0


def draw_board(lit: bool) -> Image.Image:
    board = Image.new("RGBA", (BOARD_W * F, BOARD_H * F), (0, 0, 0, 0))
    pen = Pen(board, F)
    if not lit:
        blob(board, lambda p: p.rrect(2, 2, BOARD_W - 2, BOARD_H - 2, 13), (46, 50, 64), (26, 28, 38), width=2,
             light=(WHITE, 0.25, 3), scale=F)
    for name, (x0, y0, x1, y1) in KEYS.items():
        size = 12 if len(name) > 1 else 16
        if lit:
            pen.rrect(x0, y0, x1, y1, 5, fill=(16, 110, 100, 255))
            pen.rrect(x0 + 1.5, y0 + 1, x1 - 1.5, y1 - 3, 5, fill=ACCENT + (255,))
            pen.rrect(x0 + 3, y0 + 2, x1 - 3, y0 + 7, 3, fill=(150, 245, 230, 255))
            pen.text((x0 + x1) / 2, (y0 + y1) / 2 - 1, name, size, ACCENT_DARK + (255,))
        else:
            pen.rrect(x0, y0, x1, y1, 5, fill=(18, 20, 28, 255))
            pen.rrect(x0 + 1.5, y0 + 1, x1 - 1.5, y1 - 3.5, 5, fill=(82, 88, 108, 255))
            pen.rrect(x0 + 3, y0 + 2, x1 - 3, y0 + 7, 3, fill=(104, 110, 132, 255))
            pen.text((x0 + x1) / 2, (y0 + y1) / 2 - 1, name, size, (240, 243, 250, 255))
    return board.rotate(180)


def build_keyboard():
    flat = Image.new("RGBA", (FW * F, FH * F), (0, 0, 0, 0))
    pen = Pen(flat, F)
    # Sombra del teclado y de la alfombrilla, y la luz de colores que sale de abajo del teclado.
    shadow = Image.new("L", flat.size, 0)
    sp = Pen(shadow, F)
    sp.rrect(BOARD_X - 2, BOARD_Y + 6, BOARD_X + BOARD_W + 4, BOARD_Y + BOARD_H + 12, 16)
    sp.rrect(PAD[0], PAD[1] + 6, PAD[2] + 3, PAD[3] + 10, 16)
    flat.paste((6, 6, 14, 255), (0, 0), shadow.filter(ImageFilter.GaussianBlur(9 * F)).point(lambda p: int(p * 0.65)))
    glow(flat, lambda p: p.rrect(BOARD_X - 4, BOARD_Y - 4, BOARD_X + BOARD_W + 4, BOARD_Y + BOARD_H + 4, 16), ACCENT, 0.55, 12, F)
    blob(flat, lambda p: p.rrect(*PAD, 14), (42, 46, 60), (26, 28, 38), width=2, light=(WHITE, 0.12, 2), scale=F)
    pen.rrect(PAD[0] + 8, PAD[1] + 8, PAD[2] - 8, PAD[3] - 8, 9, outline=(70, 76, 94, 255), width=1)
    flat.alpha_composite(draw_board(False), (BOARD_X * F, BOARD_Y * F))
    lit = Image.new("RGBA", flat.size, (0, 0, 0, 0))
    lit.alpha_composite(draw_board(True), (BOARD_X * F, BOARD_Y * F))
    return warp(flat), warp(lit)


def build_key_sprites(lit: Image.Image) -> dict[str, tuple[Image.Image, tuple[int, int]]]:
    sprites = {}
    for name in KEYS:
        x0, y0, x1, y1 = key_on_desk(name)
        points = [tuple(v * S for v in on_desk(x, y)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        # Con margen, para el brillo alrededor de la tecla.
        margin = 16 * S
        left = int(min(p[0] for p in points)) - margin
        top = int(min(p[1] for p in points)) - margin
        right = int(max(p[0] for p in points)) + margin
        bottom = int(max(p[1] for p in points)) + margin
        mask = Image.new("L", (right - left, bottom - top), 0)
        ImageDraw.Draw(mask).polygon([(x - left, y - top) for x, y in points], fill=255)
        halo = mask.filter(ImageFilter.GaussianBlur(6 * S)).point(lambda p: min(255, int(p * 1.9)))
        sprite = Image.new("RGBA", mask.size, ACCENT + (0,))
        sprite.putalpha(halo)
        key = lit.crop((left, top, right, bottom))
        key.putalpha(ImageChops.multiply(mask, key.getchannel("A")))
        sprite.alpha_composite(key)
        sprites[name] = (sprite, (left, top))
    return sprites


def build_mouse() -> dict[str | None, Image.Image]:
    sprites: dict[str | None, Image.Image] = {}
    w, h = 84, 110
    for click in (None, "L", "R"):
        image = Image.new("RGBA", (w * F, h * F), (0, 0, 0, 0))
        shadow = Image.new("L", image.size, 0)
        Pen(shadow, F).rrect(21, 23, 65, 95, 21)
        image.paste((4, 4, 10, 255), (0, 0), shadow.filter(ImageFilter.GaussianBlur(4 * F)).point(lambda p: int(p * 0.7)))
        blob(image, lambda p: p.rrect(20, 18, 64, 90, 21), (248, 250, 254), (182, 188, 204), width=2.5,
             shade=((120, 126, 146), 0.7, 4), light=(WHITE, 0.9, 2), scale=F)
        pen = Pen(image, F)
        if click:
            # El botón entero: la mitad de adelante del mouse, recortada con su forma.
            shape = Image.new("L", image.size, 0)
            Pen(shape, F).rrect(21, 19, 63, 89, 20)
            half = Image.new("L", image.size, 0)
            Pen(half, F).rrect(21 if click == "L" else 42, 19, 42 if click == "L" else 63, 46, 0)
            image.paste(ACCENT + (255,), (0, 0), ImageChops.multiply(shape, half))
        pen.line([(42, 19), (42, 46)], fill=(60, 64, 80, 255), width=1.5)
        pen.line([(21, 46), (63, 46)], fill=(120, 126, 146, 255), width=1.2)
        pen.rrect(39, 26, 45, 40, 3, fill=ACCENT + (255,))
        rotated = image.rotate(180)
        # Aplastado, por la perspectiva de la mesa.
        sprites[click] = rotated.resize((int(w * S * 1.2), int(h * S * 0.95)), Image.LANCZOS)
    return sprites


KEYBOARD_LAYER: Image.Image | None = None


KEY_SPRITES: dict = {}


MOUSE_SPRITES: dict = {}


def shared_layers() -> None:
    global KEYBOARD_LAYER, KEY_SPRITES, MOUSE_SPRITES
    if KEYBOARD_LAYER is None:
        KEYBOARD_LAYER, lit = build_keyboard()
        KEY_SPRITES = build_key_sprites(lit)
        MOUSE_SPRITES = build_mouse()


def mouse_on_desk(state: State) -> tuple[float, float]:
    # Si el cursor va a la derecha, el personaje lleva el mouse hacia su derecha: de este lado se ve hacia la izquierda.
    margin_x, margin_y = 36, 46
    x0, y0, x1, y1 = PAD
    return (
        x1 - margin_x - state.mouse[0] * (x1 - x0 - 2 * margin_x),
        y0 + margin_y + (1 - state.mouse[1]) * (y1 - y0 - 2 * margin_y),
    )


def scene_base(top, bottom) -> tuple[Image.Image, Pen]:
    image = gradient((W * S, H * S), top, bottom)
    return image, Pen(image)


def finish_scene(image: Image.Image, blur: float = 1.3) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(blur * S))


def stars(pen, box, count, seed, color=(240, 240, 255)):
    rng = random.Random(seed)
    for _ in range(count):
        r = rng.uniform(0.8, 2.2)
        pen.ellipse(rng.uniform(box[0], box[2]), rng.uniform(box[1], box[3]), r, r, fill=color)


def bg_room() -> Image.Image:
    image, pen = scene_base((70, 54, 112), (34, 28, 62))
    pen.rrect(84, 56, 316, 262, 8, fill=(30, 26, 52))
    sky = gradient((212 * S, 186 * S), (22, 26, 70), (70, 56, 130))
    image.paste(sky, (94 * S, 66 * S))
    stars(pen, (100, 72, 300, 200), 26, 3)
    glow(image, lambda p: p.ellipse(250, 120, 34, 34), (255, 240, 200), 0.6, 20)
    pen.ellipse(250, 120, 25, 25, fill=(252, 242, 214))
    pen.line([(200, 66), (200, 252)], fill=(30, 26, 52), width=7)
    pen.line([(94, 150), (306, 150)], fill=(30, 26, 52), width=7)
    for x0, x1 in ((66, 118), (282, 334)):
        pen.rrect(x0, 46, x1, 300, 12, fill=(176, 72, 118))
        for x in range(x0 + 12, x1, 15):
            pen.line([(x, 54), (x, 296)], fill=(148, 54, 98), width=3)
    pen.rrect(640, 150, 890, 164, 3, fill=(60, 40, 40))
    x = 660
    for width, height, color in ((18, 68, (224, 92, 92)), (14, 58, (90, 180, 170)), (22, 76, (238, 190, 80)),
                                 (15, 62, (120, 130, 220)), (19, 70, (232, 130, 90)), (16, 56, (150, 200, 110))):
        pen.rrect(x, 150 - height, x + width, 150, 2, fill=color)
        x += width + 3
    pen.rrect(810, 118, 856, 150, 5, fill=(196, 104, 70))
    for dx, dy in ((-20, -26), (0, -40), (20, -28), (-10, -32), (12, -36)):
        pen.line([(833, 120), (833 + dx, 120 + dy)], fill=(80, 170, 104), width=7)
    pen.rrect(640, 196, 890, 210, 3, fill=(60, 40, 40))
    pen.rrect(668, 216, 780, 300, 4, fill=(44, 34, 70))
    pen.rrect(676, 224, 772, 292, 2, fill=(236, 150, 90))
    pen.ellipse(724, 262, 22, 22, fill=(255, 214, 120))
    glow(image, lambda p: p.rrect(0, 0, W, 8, 2), (255, 96, 170), 0.9, 24)
    return finish_scene(image)


def bg_lab() -> Image.Image:
    image, pen = scene_base((22, 64, 76), (10, 28, 40))
    for x in range(0, W, 60):
        pen.line([(x, 0), (x, DESK_Y)], fill=(30, 84, 96), width=1.5)
    for y in range(0, DESK_Y, 60):
        pen.line([(0, y), (W, y)], fill=(30, 84, 96), width=1.5)
    for box in ((56, 64, 300, 226), (664, 54, 908, 212)):
        glow(image, lambda p, b=box: p.rrect(*b, 10), ACCENT, 0.5, 20)
        pen.rrect(*box, 10, fill=(8, 18, 26), outline=(70, 130, 140), width=4)
    rng = random.Random(9)
    for i in range(14):
        h = rng.randint(14, 100)
        pen.rrect(76 + i * 15, 206 - h, 86 + i * 15, 206, 2, fill=(70, 230, 210))
    points = [(684 + i * 9, 130 + 44 * math.sin(i * 0.55) * math.cos(i * 0.13)) for i in range(25)]
    pen.line(points, fill=(255, 150, 90), width=4)
    pen.line([(684 + i * 9, 170 + 14 * math.sin(i * 1.1)) for i in range(25)], fill=(120, 240, 225), width=3)
    pen.line([(20, 260), (120, 300), (200, 270), (360, 330)], fill=(8, 18, 26), width=9)
    pen.line([(940, 250), (820, 290), (740, 262), (600, 330)], fill=(8, 18, 26), width=9)
    for x, y, color in ((80, 260, (250, 90, 90)), (110, 260, (250, 200, 80)), (140, 260, (90, 240, 150))):
        pen.ellipse(x, y, 6, 6, fill=color)
    return finish_scene(image)


def bg_lagoon() -> Image.Image:
    image, pen = scene_base((76, 96, 170), (252, 170, 110))
    glow(image, lambda p: p.ellipse(720, 216, 90, 90), (255, 210, 120), 0.8, 46)
    pen.ellipse(720, 216, 56, 56, fill=(255, 226, 150))
    for x, y, w in ((120, 74, 120), (300, 120, 90), (560, 60, 140), (820, 104, 90)):
        for dx, r in ((-w * 0.5, 16), (0, 24), (w * 0.5, 15)):
            pen.ellipse(x + dx, y, r * 1.6, r, fill=(255, 214, 196))
    pen.polygon([(0, 262), (150, 214), (330, 246), (520, 222), (700, 250), (960, 208), (960, 300), (0, 300)], fill=(66, 96, 84))
    water = gradient((W * S, (DESK_Y - 254) * S), (252, 176, 118), (116, 122, 150))
    image.paste(water, (0, 254 * S))
    for i in range(9):
        y = 262 + i * 11
        width = 70 + i * 13
        pen.rrect(720 - width / 2, y, 720 + width / 2, y + 3, 1.5, fill=(255, 226, 170))
    rng = random.Random(4)
    for x in list(range(10, 160, 14)) + list(range(806, 960, 14)):
        top = rng.randint(226, 280)
        pen.line([(x, DESK_Y), (x + rng.randint(-8, 8), top)], fill=(44, 70, 54), width=4)
        pen.ellipse(x, top, 3.5, 10, fill=(90, 62, 42))
    return finish_scene(image, 1.1)


def bg_attic() -> Image.Image:
    image, pen = scene_base((62, 42, 104), (24, 18, 48))
    for y in range(20, DESK_Y, 44):
        pen.line([(0, y), (W, y)], fill=(50, 34, 86), width=3)
    glow(image, lambda p: p.ellipse(720, 150, 120, 120), (186, 170, 255), 0.6, 40)
    pen.ellipse(720, 150, 112, 112, fill=(40, 28, 72))
    pen.ellipse(720, 150, 100, 100, fill=(142, 130, 214))
    pen.ellipse(742, 128, 42, 42, fill=(246, 240, 214))
    pen.line([(620, 150), (820, 150)], fill=(40, 28, 72), width=8)
    pen.line([(720, 50), (720, 250)], fill=(40, 28, 72), width=8)
    for bx, by, size in ((610, 90, 14), (120, 110, 18), (240, 70, 12)):
        pen.polygon([(bx - size * 2, by - size * 0.6), (bx - size, by + size * 0.2), (bx, by - size * 0.2),
                     (bx + size, by + size * 0.2), (bx + size * 2, by - size * 0.6), (bx + size, by + size * 0.9),
                     (bx, by + size * 0.5), (bx - size, by + size * 0.9)], fill=(18, 12, 34))
    for i in range(5):
        pen.line([(0, 0), (150 - i * 28, i * 30 + 12)], fill=(120, 110, 170), width=1.2)
    for r in (46, 78, 112):
        pen.arc(0, 0, r, r, 0, 90, fill=(120, 110, 170), width=1.2)
    pen.rrect(36, 300, 140, 316, 3, fill=(44, 30, 60))
    for x, h in ((60, 44), (88, 62), (116, 36)):
        pen.rrect(x - 8, 300 - h, x + 8, 300, 3, fill=(232, 224, 200))
        glow(image, lambda p, x=x, h=h: p.ellipse(x, 300 - h - 10, 14, 18), (255, 190, 90), 0.8, 12)
        pen.ellipse(x, 300 - h - 8, 4.5, 8, fill=(255, 230, 150))
    return finish_scene(image)


def bg_pampa() -> Image.Image:
    image, pen = scene_base((98, 140, 196), (252, 196, 132))
    glow(image, lambda p: p.ellipse(650, 262, 90, 70), (255, 214, 130), 0.75, 42)
    pen.ellipse(650, 262, 42, 42, fill=(255, 232, 170))
    for x, y, w in ((110, 92, 130), (420, 58, 100), (790, 112, 140)):
        for dx, r in ((-w * 0.5, 13), (0, 20), (w * 0.5, 12)):
            pen.ellipse(x + dx, y, r * 1.8, r, fill=(255, 236, 214))
    field = gradient((W * S, (DESK_Y - 272) * S), (172, 164, 82), (112, 122, 56))
    image.paste(field, (0, 272 * S))
    for cx, r in ((820, 26), (856, 32), (892, 24)):
        pen.ellipse(cx, 258, r, r, fill=(66, 92, 62))
    pen.rrect(852, 258, 860, 278, 2, fill=(60, 44, 34))
    ink = (50, 40, 44)
    pen.line([(132, 286), (160, 130)], fill=ink, width=4)
    pen.line([(188, 286), (160, 130)], fill=ink, width=4)
    for y, half in ((180, 12), (225, 19), (262, 25)):
        pen.line([(160 - half, y), (160 + half, y)], fill=ink, width=3)
    pen.ellipse(160, 122, 6, 6, fill=ink)
    for i in range(10):
        angle = i * math.pi / 5 + 0.2
        pen.polygon([(160, 122), (160 + 44 * math.cos(angle), 122 + 44 * math.sin(angle)),
                     (160 + 44 * math.cos(angle + 0.28), 122 + 44 * math.sin(angle + 0.28))], fill=(226, 226, 220))
    pen.polygon([(160, 122), (112, 134), (112, 110)], fill=(190, 70, 60))
    for x in range(240, 620, 54):
        pen.rrect(x - 3, 266, x + 3, 298, 1.5, fill=(82, 60, 44))
    pen.line([(236, 276), (620, 276)], fill=(82, 60, 44), width=2)
    pen.line([(236, 288), (620, 288)], fill=(82, 60, 44), width=2)
    return finish_scene(image, 1.1)


def bg_ice() -> Image.Image:
    image, pen = scene_base((12, 22, 62), (40, 78, 124))
    stars(pen, (0, 0, W, 180), 60, 7)
    aurora = Image.new("L", image.size, 0)
    ap = Pen(aurora)
    ap.polygon([(0, 150), (180, 70), (360, 120), (560, 44), (760, 110), (960, 40), (960, 96), (760, 168), (560, 100), (360, 176), (180, 126), (0, 206)], fill=210)
    image.paste((96, 240, 170), (0, 0), aurora.filter(ImageFilter.GaussianBlur(20 * S)))
    second = Image.new("L", image.size, 0)
    Pen(second).polygon([(0, 110), (240, 34), (520, 96), (960, 20), (960, 50), (520, 130), (240, 68), (0, 144)], fill=170)
    image.paste((170, 110, 240), (0, 0), second.filter(ImageFilter.GaussianBlur(18 * S)))
    for points, color in (
        ([(0, 330), (0, 230), (90, 172), (180, 244), (260, 200), (380, 300), (380, 340)], (150, 190, 226)),
        ([(560, 340), (660, 220), (760, 150), (860, 240), (960, 186), (960, 340)], (168, 206, 236)),
    ):
        pen.polygon(points, fill=color)
    for points in ([(90, 172), (58, 192), (90, 208), (122, 198)], [(760, 150), (722, 178), (758, 196), (800, 184)]):
        pen.polygon(points, fill=(240, 248, 255))
    snow = gradient((W * S, (DESK_Y - 300) * S), (216, 234, 248), (178, 208, 234))
    image.paste(snow, (0, 300 * S))
    stars(pen, (0, 0, W, DESK_Y), 44, 11, color=(255, 255, 255))
    return finish_scene(image)


def prop_yarn(layer):
    blob(layer, lambda p: p.ellipse(888, 348, 28, 26), (240, 110, 150), (176, 62, 110), shade=((120, 30, 80), 0.6, 8), light=(WHITE, 0.3, 3))
    pen = Pen(layer)
    for r in (10, 17, 23):
        pen.arc(888, 348, r, r * 0.8, 200, 340, fill=(255, 190, 210, 255), width=2)
    pen.line([(862, 368), (830, 376), (800, 372)], fill=(240, 110, 150, 255), width=3)


def prop_battery(layer):
    blob(layer, lambda p: p.rrect(866, 316, 908, 376, 7), (72, 84, 100), (38, 44, 56), light=(WHITE, 0.3, 3))
    blob(layer, lambda p: p.rrect(878, 306, 896, 318, 3), (150, 160, 176), line=INK, width=3)
    pen = Pen(layer)
    for i in range(3):
        pen.rrect(873, 362 - i * 14, 901, 372 - i * 14, 2, fill=ACCENT + (255,))


def prop_orange(layer):
    blob(layer, lambda p: p.ellipse(886, 350, 25, 24), (255, 180, 66), (232, 118, 26), shade=((170, 70, 10), 0.6, 8), light=(WHITE, 0.45, 3))
    blob(layer, lambda p: p.ellipse(846, 358, 18, 17), (255, 180, 66), (232, 118, 26), shade=((170, 70, 10), 0.6, 6), light=(WHITE, 0.45, 3))


def prop_candle(layer):
    blob(layer, lambda p: p.rrect(868, 312, 896, 372, 5), (250, 244, 224), (204, 192, 170))
    blob(layer, lambda p: p.ellipse(882, 298, 7, 13), (255, 226, 130), (255, 150, 60), width=2.5)
    blob(layer, lambda p: p.rrect(852, 366, 912, 378, 5), (120, 100, 150), (70, 54, 100))


def prop_termo(layer):
    blob(layer, lambda p: p.rrect(858, 244, 914, 372, 13), (214, 56, 60), (150, 30, 40), shade=((90, 14, 24), 0.5, 10), light=(WHITE, 0.5, 4))
    blob(layer, lambda p: p.rrect(866, 226, 906, 250, 8), (226, 230, 238), (160, 166, 180))
    blob(layer, lambda p: p.rrect(860, 356, 912, 374, 5), (226, 230, 238), (160, 166, 180), width=3)
    pen = Pen(layer)
    pen.rrect(866, 268, 906, 276, 2, fill=(255, 220, 130, 255))


def prop_fish(layer):
    blob(layer, lambda p: p.polygon([(824, 368), (872, 340), (904, 362), (940, 340), (940, 380), (904, 368), (872, 388)]), (176, 216, 240), (98, 150, 196), light=(WHITE, 0.5, 3))
    Pen(layer).ellipse(848, 362, 3.5, 3.5, fill=INK + (255,))


def scenario(t: float) -> State:
    word = "HOLA MUNDO"
    keys: tuple[str, ...] = ()
    look = (0.0, 0.1)
    if t < 2.6:
        index = int(t / 0.26)
        if t % 0.26 < 0.17 and index < len(word):
            letter = word[index]
            keys = ("Espacio",) if letter == " " else (letter,)
        look = (0.4, 0.75)
    elif 5.4 < t < 5.9:
        keys = ("Ctrl", "C")
        look = (0.5, 0.75)
    mouse = (0.5, 0.5)
    click = None
    if 2.6 <= t < 5.2:
        phase = (t - 2.6) * 2.4
        mouse = (0.5 + 0.42 * math.cos(phase), 0.5 + 0.42 * math.sin(phase))
        look = (-0.6 - 0.25 * math.cos(phase), 0.65)
        if 3.4 < t < 3.6:
            click = "L"
        if 4.5 < t < 4.7:
            click = "R"
    mouth = 0.0
    if 0.7 < t < 2.2 or 3.2 < t < 5.0:
        mouth = min(1.0, abs(math.sin(t * 8.5)) * (0.45 + 0.55 * abs(math.sin(t * 2.3))) * 1.15)
        mouth = 0.0 if mouth < 0.1 else mouth
    blink = 1.5 < t < 1.62 or 4.7 < t < 4.82
    return State(keys, mouse, click, mouth, blink, look, t)
