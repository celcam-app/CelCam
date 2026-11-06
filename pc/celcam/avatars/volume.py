"""Estilo con volumen: formas pintadas con luz, como muñecos de felpa o arcilla."""

from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import drawing as old
from .art import (
    C,
    DESK_Y,
    H,
    S,
    W,
    _robi_led,
    box_poly,
    beak_bottom,
    beak_top,
    felt,
    make_eye,
    make_grin,
    make_mouth,
    oval,
    make_paw,
    plush,
    soften,
    spline,
    taper,
    wood,
)
from .stage import GREEN, Stage



def bg_forest() -> Image.Image:
    """Bosque de noche, con luna y luciérnagas."""
    image, pen = old.scene_base((22, 34, 64), (44, 62, 70))
    old.stars(pen, (0, 0, W, 180), 50, 5)
    old.glow(image, lambda p: p.ellipse(190, 96, 60, 60), (240, 240, 200), 0.6, 30)
    pen.ellipse(190, 96, 42, 42, fill=(246, 244, 214))
    pen.ellipse(206, 86, 34, 34, fill=(40, 54, 80))
    rng = random.Random(3)
    for layer_color, base, count in (((30, 50, 56), 300, 11), ((18, 36, 40), 340, 9)):
        for i in range(count):
            x = -30 + i * (1020 / count) + rng.uniform(-14, 14)
            h = rng.uniform(130, 230)
            for level in range(3):
                width = 62 - level * 16
                top = base - h + level * 0 - level * 46
                pen.polygon([(x, top), (x + width, top + 110), (x - width, top + 110)], fill=layer_color)
            pen.rrect(x - 8, base - 90, x + 8, DESK_Y, 2, fill=(14, 22, 24))
    for _ in range(14):
        x, y = rng.uniform(30, 930), rng.uniform(170, 330)
        old.glow(image, lambda p, x=x, y=y: p.ellipse(x, y, 5, 5), (214, 240, 120), 0.9, 7)
        pen.ellipse(x, y, 2.4, 2.4, fill=(244, 252, 170))
    return old.finish_scene(image)


def prop_apple(layer) -> None:
    old.blob(layer, lambda p: p.ellipse(884, 346, 27, 25), (240, 70, 70), (170, 28, 40), shade=((110, 14, 24), 0.6, 8), light=(old.WHITE, 0.55, 3))
    old.Pen(layer).line([(884, 324), (888, 310)], fill=(80, 56, 36, 255), width=4)
    old.blob(layer, lambda p: p.ellipse(902, 314, 13, 6), (120, 196, 90), (70, 140, 66), width=3)


def bg_dawn() -> Image.Image:
    """Un pueblo antes de que amanezca, con faroles encendidos."""
    image, pen = old.scene_base((22, 24, 72), (214, 116, 104))
    old.stars(pen, (0, 0, W, 170), 70, 11)
    old.glow(image, lambda p: p.ellipse(176, 92, 42, 42), (250, 240, 200), 0.55, 26)
    # La luna en cuarto, recortada: un círculo tapado dejaría una mancha sobre el resplandor.
    moon = Image.new("L", image.size, 0)
    cut = old.Pen(moon)
    cut.ellipse(176, 92, 30, 30, fill=255)
    cut.ellipse(190, 82, 27, 27, fill=0)
    image.paste((250, 242, 206), (0, 0), moon)
    rng = random.Random(7)
    for color, base, count, window in (((58, 42, 84), 300, 9, (250, 196, 110)), ((30, 24, 56), 330, 7, (255, 214, 130))):
        for i in range(count):
            x0 = -30 + i * (1040 / count) + rng.uniform(-12, 12)
            width = rng.uniform(96, 130)
            wall = base - rng.uniform(40, 110)
            pen.rrect(x0, wall, x0 + width, DESK_Y, 2, fill=color)
            pen.polygon([(x0 - 12, wall + 2), (x0 + width / 2, wall - rng.uniform(34, 56)), (x0 + width + 12, wall + 2)], fill=tuple(int(v * 0.8) for v in color))
            for k in range(2):
                wx = x0 + 18 + k * (width - 54)
                if rng.random() < 0.7:
                    old.glow(image, lambda p, a=wx, b=wall: p.rrect(a, b + 18, a + 18, b + 42, 2), window, 0.5, 8)
                    pen.rrect(wx, wall + 18, wx + 18, wall + 42, 2, fill=window)
    # Una guirnalda de faroles de lado a lado.
    points = [(x, 118 + 44 * ((x - 480) / 520) ** 2 * -1 + 44) for x in range(-20, 981, 40)]
    pen.line(points, fill=(26, 20, 44), width=3)
    for index, (x, y) in enumerate(points):
        if index % 3 == 1:
            color = ((255, 176, 84), (255, 120, 96), (250, 210, 110))[(index // 3) % 3]
            old.glow(image, lambda p, a=x, b=y: p.ellipse(a, b + 20, 15, 18), color, 0.75, 16)
            pen.line([(x, y), (x, y + 8)], fill=(26, 20, 44), width=2)
            pen.ellipse(x, y + 20, 11, 14, fill=color)
            pen.rrect(x - 7, y + 6, x + 7, y + 10, 1, fill=(60, 40, 40))
    return old.finish_scene(image)


def prop_none(layer) -> None:
    """Sin nada sobre la mesa: el bate lo tiene en la mano."""


# El bate: lo que no cambia se pinta una vez y después solo se gira.

BAT_R = 240
BAT_TIP = 200
BAT_REST_HAND = (C + 172, 316)
BAT_REST_ANGLE = math.radians(-70)
# La punta se queda un poco antes del centro de la tecla, para que se vea encendida.
BAT_KEY_GAP = 8
BAT_STAYS = 0.45


def make_bat() -> Image.Image:
    """Apunta a la derecha y tiene el agarre en el centro."""
    R = BAT_R
    layer = Image.new("RGBA", (2 * R * S, 2 * R * S), (0, 0, 0, 0))

    def at(along, across):
        return (R + across, R - along)

    profile = ((-48, 14), (-42, 15), (-32, 9), (30, 9.5), (84, 15), (150, 23), (190, 24), (200, 15))
    outline = [at(f, w) for f, w in profile] + [at(206, 0)] + [at(f, -w) for f, w in reversed(profile)] + [at(-54, 0)]
    plush(layer, [spline(outline, samples=8)], (228, 136, 58), (150, 76, 26), bevel=7, dome=0.7, amb=0.56, kd=0.7, spec=0.45, shine=28, tex=wood, tex_power=0.2)
    plush(layer, [oval(*at(0, 2), 21, 22, wobble=0.04, seed=4)], (214, 124, 50), (150, 76, 26), bevel=8, dome=0.6, amb=0.56, kd=0.68, spec=0.15, tex=wood, tex_power=0.2)
    for along in (-15, 0, 15):
        plush(layer, [oval(*at(along, -5), 18, 8.5, wobble=0.03, seed=along + 30)], (224, 134, 58), (156, 80, 28), bevel=4, dome=0.7, amb=0.58, kd=0.66, spec=0.2, shine=20)
    # Hecho parado, para que la veta corra a lo largo; se acuesta al final.
    return layer.transpose(Image.ROTATE_270)


SCENES = {
    "tuntun": (bg_dawn, prop_none),
    "michi": (old.bg_room, old.prop_yarn),
    "mapache": (bg_forest, prop_apple),
    "robi": (old.bg_lab, old.prop_battery),
    "carpincho": (old.bg_lagoon, old.prop_orange),
    "fanti": (old.bg_attic, old.prop_candle),
    "mate": (old.bg_pampa, old.prop_termo),
    "pingui": (old.bg_ice, old.prop_fish),
}


LOOK_STEPS = 5
MOUTH_STEPS = 7
FLOAT_STEP = 2


def quant(value, steps, low=-1.0, high=1.0):
    t = (min(high, max(low, value)) - low) / (high - low)
    return round(t * (steps - 1))


class Scene(Stage):
    """Estilo con volumen, en el tamaño de salida."""

    def __init__(self, character, size: tuple[int, int], background: str = "color") -> None:
        super().__init__(size, background)
        old.shared_layers()
        self.c = character
        scene, prop = SCENES[character.id]
        self.backdrop = None
        if background == "color":
            painted = scene().filter(ImageFilter.GaussianBlur(1.5 * S))
            vignette = Image.new("L", painted.size, 0)
            old.Pen(vignette).ellipse(480, 240, 640, 400, fill=255)
            vignette = vignette.filter(ImageFilter.GaussianBlur(70 * S))
            full = Image.composite(painted, Image.eval(painted, lambda v: int(v * 0.5)), vignette)
            self.backdrop = full.resize(size, Image.LANCZOS).convert("RGBA")
        elif background == "verde":
            self.backdrop = Image.new("RGBA", size, GREEN + (255,))
        body = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        character.body(body)
        head = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        character.head(head)
        self.head, self.head_at = self.trimmed(head)
        desk = self._build_desk(prop)
        # Lo que puede recibir la sombra de un brazo.
        self.solid = ImageChops.lighter(body.getchannel("A"), desk.getchannel("A"))
        self.body = self.scaled(body)
        self.desk = self.scaled(desk)
        self.keys = {name: self.trimmed(sprite, origin) for name, (sprite, origin) in old.KEY_SPRITES.items()}
        self.mouse = {click: self.scaled(sprite) for click, sprite in old.MOUSE_SPRITES.items()}
        kind, color, bottom = character.paw
        tex = wood if kind == "madera" else felt if kind in ("gato", "dedos", "aleta") else None
        self.paw = make_paw(kind, color, bottom, tex=tex, spec=0.7 if kind == "pinza" else 0.08)
        self.paw_shadow = self._shadow_of(self.paw)
        self.closed = None
        if character.closed:
            layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
            character.closed(layer)
            self.closed = self.trimmed(layer)
        if character.id == "pingui":
            self.beak_top = beak_top()
            self.beak_bottom = beak_bottom()
        if character.segmented:
            layer = Image.new("RGBA", (70 * S, 70 * S), (0, 0, 0, 0))
            plush(layer, [box_poly(9, 9, 61, 61, 7)], (176, 188, 208), (96, 110, 134), bevel=4, dome=0.2, spec=0.9, shine=60)
            ImageDraw.Draw(layer).rectangle([26 * S, 26 * S, 44 * S, 44 * S], fill=(70, 232, 214, 255))
            self.joint = layer
        self.bat = self.scaled(make_bat()) if character.bat else None
        self.reset()

    def reset(self):
        self.bat_last_t = None
        self.bat_angle = BAT_REST_ANGLE
        self.bat_hand = BAT_REST_HAND
        self.bat_key_t = -10.0
        self.bat_strike = None

    @staticmethod
    def _shadow_of(sprite):
        alpha = np.asarray(sprite.getchannel("A"), dtype=np.float32) / 255
        soft = soften(alpha, 3 * S) * 0.5
        out = np.zeros((*alpha.shape, 4), dtype=np.uint8)
        out[..., :3] = (8, 8, 16)
        out[..., 3] = (soft * 255).astype(np.uint8)
        return Image.fromarray(out, "RGBA")

    def _build_desk(self, prop):
        desk = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        top, bottom = self.c.desk
        desk.paste(old.gradient((W * S, (H - DESK_Y) * S), top, bottom), (0, DESK_Y * S))
        pen = old.Pen(desk)
        if self.c.planks:
            for x in range(-240, 1200, 120):
                pen.line([old.on_desk(x, 0), old.on_desk(x, old.FH)], fill=tuple(int(v * 0.74) for v in top) + (255,), width=2)
        edge = tuple(min(255, int(v * 1.3) + 16) for v in top)
        pen.rrect(-20, DESK_Y - 1, W + 20, DESK_Y + 6, 0, fill=edge + (255,))
        desk.alpha_composite(old.KEYBOARD_LAYER)
        prop(desk)
        return desk

    def _make_base(self, offset):
        image = self.backdrop.copy() if self.backdrop is not None else self.blank()
        self.put(image, self.body, (0, offset))
        self.put(image, self.desk, (0, 0))
        return image

    # La cara

    def _eye(self, index, look, blink):
        key = ("ojo", index, quant(look[0], LOOK_STEPS), quant(look[1], LOOK_STEPS), blink)

        def make():
            lx = (key[2] / (LOOK_STEPS - 1)) * 2 - 1
            ly = (key[3] / (LOOK_STEPS - 1)) * 2 - 1
            sprite, position = make_eye(self.c.eyes[index], (lx, ly), blink)
            return self.scaled(sprite), self.at(*position)

        return self.pieces.get(key, make)

    def _mouth(self, level):
        key = quant(level, MOUTH_STEPS, 0, 1)
        if key == 0:
            return None

        def make():
            cx, cy, w, h, options = self.c.mouth
            t = key / (MOUTH_STEPS - 1)
            if options.get("grin"):
                sprite, position = make_grin(cx, cy, w, 14 + h * t)
            else:
                sprite, position = make_mouth(cx, cy, w * (0.62 + 0.38 * t), max(5, h * t), **options)
            return self.scaled(sprite), self.at(*position)

        return self.pieces.get(("boca", key), make)

    def _small_layer(self, box):
        """Una capa para dibujar solo ese pedazo, con un lápiz que usa las coordenadas de siempre."""
        layer = Image.new("RGBA", ((box[2] - box[0]) * S, (box[3] - box[1]) * S), (0, 0, 0, 0))
        pen = old.Pen(layer)
        pen.dx, pen.dy = -box[0], -box[1]
        return layer, pen

    def _beak(self, level):
        def make():
            opened = level / 8 * 24
            box = (C - 70, 170, C + 70, 300)
            layer, pen = self._small_layer(box)
            pen.polygon([(C - 30, 208), (C + 30, 208), (C + 18, 230 + opened), (C - 18, 230 + opened)], fill=(104, 26, 38))
            layer.alpha_composite(self.beak_bottom, (int((C - 50 - box[0]) * S), int((200 + opened - box[1]) * S)))
            layer.alpha_composite(self.beak_top, (int((C - 60 - box[0]) * S), int((180 - box[1]) * S)))
            return self.trimmed(layer, (box[0] * S, box[1] * S), Image.BILINEAR)

        return self.pieces.get(("pico", level), make)

    def _robi_face(self, s):
        look = (quant(s.look[0], LOOK_STEPS), quant(s.look[1], LOOK_STEPS))
        mouth = quant(s.mouth, MOUTH_STEPS, 0, 1)
        wave = round(s.t * 12) % 48 if mouth else 0

        def make():
            x, y = (look[0] / (LOOK_STEPS - 1)) * 2 - 1, (look[1] / (LOOK_STEPS - 1)) * 2 - 1
            layer = _robi_led((x, y), s.blink, mouth / (MOUTH_STEPS - 1), wave / 12)
            return self.scaled(layer, Image.BILINEAR), self.at((C - 110) * S, 110 * S)

        return self.pieces.get(("robi", look, s.blink, mouth, wave), make)

    def _robi_extra(self, s):
        """La antena y la línea del pecho, que también siguen a la voz."""
        mouth = quant(s.mouth, MOUTH_STEPS, 0, 1)
        wave = round(s.t * 12) % 48 if mouth else 0

        def antenna():
            size = 13 + mouth / (MOUTH_STEPS - 1) * 4
            box = (C - 30, -14, C + 30, 44)
            layer, pen = self._small_layer(box)
            pen.rrect(C - size - 3, 14 - size - 3, C + size + 3, 14 + size + 3, 3, fill=(26, 96, 100))
            pen.rrect(C - size, 14 - size, C + size, 14 + size, 2, fill=(170, 255, 244))
            return self.trimmed(layer, (box[0] * S, box[1] * S), Image.BILINEAR)

        def chest():
            box = (C - 70, 304, C + 70, 352)
            layer, pen = self._small_layer(box)
            level = mouth / (MOUTH_STEPS - 1)
            points = [(C - 60 + i * 3.9, 328 + (1.5 + level * 13) * math.sin(i * 0.8 + wave / 12 * 12)) for i in range(32)]
            pen.line(points, fill=(70, 232, 214), width=3)
            return self.trimmed(layer, (box[0] * S, box[1] * S), Image.BILINEAR)

        return self.pieces.get(("antena", mouth), antenna), self.pieces.get(("pecho", mouth, wave), chest)

    def _face(self, image, s, hp):
        c = self.c

        def moved(piece):
            return piece[0], (piece[1][0] + hp[0], piece[1][1] + hp[1])

        if c.id == "robi":
            self.put(image, *moved(self._robi_face(s)))
            antenna, chest = self._robi_extra(s)
            self.put(image, *moved(antenna))
            self.put(image, *chest)
            return
        for index in range(len(c.eyes)):
            self.put(image, *moved(self._eye(index, s.look, s.blink)))
        if c.id == "pingui":
            self.put(image, *moved(self._beak(round(max(0.0, min(1.0, s.mouth)) * 8))))
            return
        mouth = self._mouth(s.mouth)
        self.put(image, *moved(mouth if mouth else self.closed))

    # Las manos

    def _paws(self, s):
        """El personaje nos mira: su mano derecha, la del mouse, se ve a nuestra izquierda."""
        mx, my = old.mouse_on_desk(s)
        right_hand = old.on_desk(mx, my - 58)
        left_hand = old.on_desk(old.BOARD_X + old.BOARD_W * 0.66, old.BOARD_Y - 12)
        middle = old.BOARD_X + old.BOARD_W / 2
        for name in s.keys:
            x0, y0, x1, _ = old.key_on_desk(name)
            center = (x0 + x1) / 2 if name != "Espacio" else middle + 40
            spot = old.on_desk(center, y0 - 34)
            # El bate se encarga de todas las teclas: la otra mano no suelta el mouse.
            if center >= middle or self.c.bat:
                left_hand = spot
            else:
                right_hand = spot
        return right_hand, left_hand

    def _shadow_on_things(self, layer, box, polygon):
        """La sombra cae sobre lo que ya está dibujado, nunca sobre lo transparente."""
        mask = Image.new("L", layer.size, 0)
        ImageDraw.Draw(mask).polygon([((x + 5 - box[0]) * S, (y + 11 - box[1]) * S) for x, y in polygon], fill=120)
        soft = mask.reduce(4).filter(ImageFilter.BoxBlur(2)).resize(layer.size, Image.BILINEAR)
        keep = self.solid.crop((box[0] * S, box[1] * S, box[2] * S, box[3] * S))
        layer.paste((8, 8, 16, 255), (0, 0), ImageChops.multiply(soft, keep))

    def _hand_on(self, layer, box, target):
        x, y = int((target[0] - 60 - box[0]) * S), int((target[1] - 44 - box[1]) * S)
        layer.alpha_composite(self.paw_shadow, (x + 4 * S, y + 9 * S))
        layer.alpha_composite(self.paw, (x, y))

    def _arm_piece(self, sign, target, with_hand=True):
        target = (round(target[0]), round(target[1]))

        def make():
            c = self.c
            sx, sy = c.shoulder
            shoulder = (C + sign * (C - sx), sy)
            elbow = ((shoulder[0] + target[0]) / 2 + sign * abs(c.elbow[0]), (shoulder[1] + target[1]) / 2 + c.elbow[1])
            points = [shoulder, elbow, target]
            w0, w1 = c.width
            outline = taper(points, w0, w1, samples=10)
            margin = 76
            box = (
                int(min(p[0] for p in outline)) - margin,
                int(min(p[1] for p in outline)) - margin,
                int(max(p[0] for p in outline)) + margin,
                int(max(p[1] for p in outline)) + margin,
            )
            layer = Image.new("RGBA", ((box[2] - box[0]) * S, (box[3] - box[1]) * S), (0, 0, 0, 0))
            self._shadow_on_things(layer, box, outline)
            draw = ImageDraw.Draw(layer)

            def scaled(poly):
                return [((x - box[0]) * S, (y - box[1]) * S) for x, y in poly]

            base = np.array(c.arm, dtype=np.float32)
            steps = 6
            for i in range(steps):
                t = i / (steps - 1)
                width = 1 - t * 0.78
                shift = t * w0 * 0.17
                color = base * (0.56 + 0.62 * t ** 1.1) + 22 * t ** 3
                poly = taper([(x - shift * 0.8, y - shift) for x, y in points], w0 * width, w1 * width, samples=10)
                draw.polygon(scaled(poly), fill=tuple(int(min(255, v)) for v in color) + (255,))
            if c.cuff:
                dx, dy = target[0] - elbow[0], target[1] - elbow[1]
                size = math.hypot(dx, dy) or 1
                ux, uy = dx / size, dy / size
                cuff_points = [(target[0] - ux * 18, target[1] - uy * 18), (target[0] - ux * 4, target[1] - uy * 4)]
                for scale, tone in ((1.0, 0.72), (0.76, 1.0)):
                    poly = taper(cuff_points, (w1 + 7) * scale, (w1 + 7) * scale)
                    draw.polygon(scaled(poly), fill=tuple(int(v * tone) for v in c.cuff) + (255,))
            if with_hand:
                self._hand_on(layer, box, target)
            return self.trimmed(layer, (box[0] * S, box[1] * S), Image.BILINEAR)

        return self.pieces.get(("brazo", sign, target, with_hand), make)

    def _robot_arm_piece(self, sign, target):
        target = (round(target[0]), round(target[1]))

        def make():
            """Dos tramos rectos de metal y una articulación."""
            c = self.c
            sx, _ = c.shoulder
            shoulder = (C + sign * (C - sx + 22), 330)
            elbow = ((shoulder[0] + target[0]) / 2 + sign * 34, (shoulder[1] + target[1]) / 2 + 26)

            def bar(a, b, w):
                dx, dy = b[0] - a[0], b[1] - a[1]
                size = math.hypot(dx, dy) or 1
                nx, ny = -dy / size * w / 2, dx / size * w / 2
                return [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]

            segments = bar(shoulder, elbow, 34) + bar(elbow, target, 26)
            margin = 80
            box = (
                int(min(p[0] for p in segments)) - margin,
                int(min(p[1] for p in segments)) - margin,
                int(max(p[0] for p in segments)) + margin,
                int(max(p[1] for p in segments)) + margin,
            )
            layer = Image.new("RGBA", ((box[2] - box[0]) * S, (box[3] - box[1]) * S), (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            for a, b, w, tone in ((shoulder, elbow, 34, 0.74), (elbow, target, 26, 1.0)):
                self._shadow_on_things(layer, box, bar(a, b, w))
                for scale, shift, light in ((1.0, 0, 0.5), (0.8, 0, 0.82), (0.42, -w * 0.14, 1.12)):
                    poly = bar((a[0], a[1] + shift), (b[0], b[1] + shift), w * scale)
                    color = tuple(int(min(255, v * tone * light)) for v in c.arm) + (255,)
                    draw.polygon([((x - box[0]) * S, (y - box[1]) * S) for x, y in poly], fill=color)
            for point in (shoulder, elbow):
                layer.alpha_composite(self.joint, (int((point[0] - 35 - box[0]) * S), int((point[1] - 35 - box[1]) * S)))
            self._hand_on(layer, box, target)
            return self.trimmed(layer, (box[0] * S, box[1] * S), Image.BILINEAR)

        return self.pieces.get(("robot", sign, target), make)

    def _swing_bat(self, image, s, settled=False):
        """El bate sube y baja girando, siempre por afuera: nunca le pasa por encima de la cara."""
        rest_angle = BAT_REST_ANGLE + abs(math.sin(s.t * 8)) * s.mouth * math.radians(16)
        hand, angle, tau, key, name = BAT_REST_HAND, rest_angle, 0.12, None, None
        if s.keys:
            name = s.keys[-1]
            x0, y0, x1, y1 = old.key_on_desk(name)
            key = old.on_desk((x0 + x1) / 2, (y0 + y1) / 2)
            dx, dy = key[0] - BAT_REST_HAND[0], key[1] - BAT_REST_HAND[1]
            size = math.hypot(dx, dy) or 1
            ux, uy = dx / size, dy / size
            reach = BAT_TIP + BAT_KEY_GAP
            hand, angle, tau = (key[0] - ux * reach, key[1] - uy * reach), math.atan2(uy, ux), 0.03
            self.bat_strike = (hand, angle)
            self.bat_key_t = s.t
        elif self.bat_strike and s.t - self.bat_key_t < BAT_STAYS and not settled:
            # Entre tecla y tecla queda levantado, listo para el próximo golpe.
            hand, angle, tau = self.bat_strike[0], self.bat_strike[1] - math.radians(50), 0.05
        if settled or self.bat_last_t is None or s.t < self.bat_last_t:
            follow = 1.0
        else:
            follow = 1 - math.exp(-(s.t - self.bat_last_t) / tau)
        self.bat_last_t = s.t
        self.bat_angle += (angle - self.bat_angle) * follow
        self.bat_hand = tuple(p + (g - p) * follow for p, g in zip(self.bat_hand, hand))
        self.put(image, *self._arm_piece(1, self.bat_hand, with_hand=False))
        if key and abs(self.bat_angle - angle) < math.radians(8):
            self.put(image, *self._rays(name, key, angle))
        degrees = round(math.degrees(self.bat_angle) / 2) * 2

        def turned():
            rotated = self.bat.rotate(-degrees, resample=Image.BILINEAR)
            box = rotated.getbbox()
            return rotated.crop(box), box[:2]

        sprite, offset = self.pieces.get(("bate", degrees), turned)
        hx, hy = self.at(self.bat_hand[0] * S, self.bat_hand[1] * S)
        self.put(image, sprite, (hx - self.bat.width // 2 + offset[0], hy - self.bat.height // 2 + offset[1]))

    def _rays(self, name, key, angle):
        def make():
            box = (int(key[0]) - 60, int(key[1]) - 60, int(key[0]) + 60, int(key[1]) + 60)
            layer, pen = self._small_layer(box)
            for k in range(7):
                a = angle - math.radians(120) + k * math.radians(40)
                pen.line(
                    [(key[0] + math.cos(a) * 26, key[1] + math.sin(a) * 18), (key[0] + math.cos(a) * 44, key[1] + math.sin(a) * 30)],
                    fill=(255, 226, 150, 255),
                    width=3.5,
                )
            return self.trimmed(layer, (box[0] * S, box[1] * S), Image.BILINEAR)

        return self.pieces.get(("rayas", name), make)

    def frame(self, s, settled=False) -> Image.Image:
        c = self.c
        floating = math.sin(s.t * 2.4) * 8 if c.floats else 0
        breathe = math.sin(s.t * 1.8) * 1.5
        step = FLOAT_STEP if c.floats else 1
        offset = round((floating + breathe) * S * self.fy / step) * step
        image = self.base(("base", offset), lambda: self._make_base(offset)).copy()
        hx = s.look[0] * 7
        hy = floating + breathe * 1.4 + s.look[1] * 3 - s.mouth * 2
        hp = self.at(hx * S, hy * S)
        self.put(image, self.head, (self.head_at[0] + hp[0], self.head_at[1] + hp[1]))
        self._face(image, s, hp)
        for name in s.keys:
            self.put(image, *self.keys[name])
        mx, my = old.mouse_on_desk(s)
        sx, sy = old.on_desk(mx, my)
        sprite = self.mouse[s.click]
        cx, cy = self.at(sx * S, sy * S)
        self.put(image, sprite, (cx - sprite.width // 2, cy - sprite.height // 2))
        for sign, target in zip((-1, 1), self._paws(s)):
            if c.segmented:
                self.put(image, *self._robot_arm_piece(sign, target))
            elif c.bat and sign == 1:
                self._swing_bat(image, s, settled)
            else:
                self.put(image, *self._arm_piece(sign, target))
        return image
