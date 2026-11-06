"""Estilo dibujado a mano alzada: tinta gruesa, mesa en diagonal, mouse a la izquierda y teclas a la derecha.

Los dibujos son propios: todo sale de este código. Cada personaje tiene sus propias manos.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import drawing as old
from .art import oval, soften, spline, taper
from .stage import GREEN, Stage

W, H, S = 960, 540, 2
INK = (20, 20, 24)
PAPER = (250, 250, 246)
ACCENT = (45, 212, 191)
PINK = (250, 150, 160)
ANGLE = math.radians(7)
COS, SIN = math.cos(ANGLE), math.sin(ANGLE)
PIVOT = (480, 270)
DESK_V = 300
FONT_HAND = "C:/Windows/Fonts/segoeprb.ttf"
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
FONT = "C:/Windows/Fonts/segoeui.ttf"


def T(p):
    """Todo se dibuja derecho y después se inclina, como la mesa."""
    u, v = p[0] - PIVOT[0], p[1] - PIVOT[1]
    return (PIVOT[0] + u * COS - v * SIN, PIVOT[1] + u * SIN + v * COS)


def px(p):
    x, y = T(p)
    return (x * S, y * S)


def wobble(i, seed):
    return 0.84 + 0.2 * math.sin(i * 0.11 + seed) + 0.1 * math.sin(i * 0.29 + seed * 2.3)


class Ink:
    """Pincel de tinta: el trazo engorda y adelgaza como hecho a mano."""

    def __init__(self, image, origin=(0, 0)):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        # Dónde cae la esquina de esta imagen dentro del cuadro completo.
        self.origin = origin

    def p(self, point):
        x, y = px(point)
        return (x - self.origin[0], y - self.origin[1])

    def stroke_path(self, path, w=6, color=INK, seed=1, ends=True):
        n = len(path)
        pts = [self.p(p) for p in path]
        for i in range(n - 1):
            k = wobble(i, seed)
            if ends:
                k *= min(1, 0.35 + i / 7, 0.35 + (n - 1 - i) / 7)
            width = max(1, w * k * S)
            a, b = pts[i], pts[i + 1]
            self.draw.line([a, b], fill=color, width=int(width))
            r = width / 2
            self.draw.ellipse([b[0] - r, b[1] - r, b[0] + r, b[1] + r], fill=color)

    def stroke(self, points, w=6, color=INK, seed=1, closed=False, ends=True, samples=12):
        path = spline(points, closed=closed, samples=samples)
        if closed:
            path = path + path[:1]
        self.stroke_path(path, w, color, seed, ends and not closed)

    def fill(self, path, color):
        self.draw.polygon([self.p(p) for p in path], fill=color)

    def shape(self, points, color, w=7, seed=1, shade=None, shade_alpha=0.42, samples=12):
        path = spline(points, samples=samples)
        self.fill(path, color)
        if shade:
            self.inner_shade(path, shade, shade_alpha)
        if w:
            self.stroke_path(path + path[:1], w, INK, seed, ends=False)
        return path

    def box(self, points, color, w=6, shade=None, shade_alpha=0.5):
        """Polígono de líneas rectas y trazo parejo: para lo que es de metal."""
        self.fill(points, color)
        if shade:
            self.inner_shade(points, shade, shade_alpha, shift=9, blur=4)
        if w:
            self.lines(points + points[:1], w)
        return points

    def lines(self, points, w=6, color=INK):
        pts = [self.p(p) for p in points]
        width = int(w * S)
        self.draw.line(pts, fill=color, width=width, joint="curve")
        for x, y in pts:
            r = width / 2
            self.draw.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def mask(self, path):
        mask = Image.new("L", self.image.size, 0)
        ImageDraw.Draw(mask).polygon([self.p(p) for p in path], fill=255)
        return mask

    def inner_shade(self, path, color, alpha=0.42, shift=16, blur=9):
        """Una sombra suave por dentro del borde de abajo y de la derecha."""
        mask = self.mask(path)
        box = mask.getbbox()
        if not box:
            return
        margin = (shift + blur * 3) * S
        box = (max(box[0] - margin, 0), max(box[1] - margin, 0), min(box[2] + margin, mask.width), min(box[3] + margin, mask.height))
        crop = np.asarray(mask.crop(box), dtype=np.float32) / 255
        moved = np.roll(np.roll(crop, -shift * S, axis=0), -int(shift * 0.6) * S, axis=1)
        edge = soften(np.clip(crop - moved, 0, 1), blur * S) * crop * alpha
        region = np.asarray(self.image.crop(box).convert("RGBA"), dtype=np.float32).copy()
        region[..., :3] = region[..., :3] * (1 - edge[..., None]) + np.array(color, dtype=np.float32) * edge[..., None]
        self.image.paste(Image.fromarray(region.astype(np.uint8), "RGBA").convert(self.image.mode), box[:2])

    def tint(self, polys, color, alpha=0.5, blur=2, clip=None):
        """Pinta dentro de un dibujo ya hecho: manchas, rubor, pelo."""
        mask = Image.new("L", self.image.size, 0)
        draw = ImageDraw.Draw(mask)
        for poly in polys:
            draw.polygon([self.p(p) for p in poly], fill=255)
        box = mask.getbbox()
        if not box:
            return
        margin = int(blur * 3 * S) + 4
        box = (max(box[0] - margin, 0), max(box[1] - margin, 0), min(box[2] + margin, mask.width), min(box[3] + margin, mask.height))
        a = soften(np.asarray(mask.crop(box), dtype=np.float32) / 255, blur * S) * alpha
        if clip is not None:
            a = a * np.asarray(clip.crop(box), dtype=np.float32) / 255
        region = np.asarray(self.image.crop(box).convert("RGBA"), dtype=np.float32).copy()
        region[..., :3] = region[..., :3] * (1 - a[..., None]) + np.array(color, dtype=np.float32) * a[..., None]
        self.image.paste(Image.fromarray(region.astype(np.uint8), "RGBA").convert(self.image.mode), box[:2])


class Tip:
    """La punta de un brazo: «f» es hacia adelante y «s» hacia el costado."""

    def __init__(self, last, ux, uy, w):
        self.last, self.ux, self.uy, self.w = last, ux, uy, w

    def at(self, f, s=0.0):
        return (self.last[0] + self.ux * f - self.uy * s, self.last[1] + self.uy * f + self.ux * s)


def arm_outline(root, mid, tip, w0, w1):
    path = spline([root, mid, tip], closed=False, samples=14)
    n = len(path)
    left, right = [], []
    for i, (x, y) in enumerate(path):
        a = path[max(i - 1, 0)]
        b = path[min(i + 1, n - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        size = math.hypot(dx, dy) or 1
        t = i / (n - 1)
        # Un poco más ancho en el medio: como un brazo, no como un tubo.
        w = (w0 + (w1 - w0) * t) * (1 + 0.1 * math.sin(t * math.pi)) / 2
        left.append((x - dy / size * w, y + dx / size * w))
        right.append((x + dy / size * w, y - dx / size * w))
    last = path[-1]
    prev = path[-2]
    dx, dy = last[0] - prev[0], last[1] - prev[1]
    size = math.hypot(dx, dy) or 1
    ux, uy = dx / size, dy / size
    r = w1 * 0.52
    cap = [(last[0] + ux * r * math.cos(a) - uy * r * math.sin(a), last[1] + uy * r * math.cos(a) + ux * r * math.sin(a))
           for a in np.linspace(math.pi / 2 * 0.98, -math.pi / 2 * 0.98, 12)]
    return left, right, cap, Tip(last, ux, uy, w1)


# Manos: la que descansa levantada y la punta del brazo que toca el mouse y las teclas


def raised_cat(ink, c):
    path = ink.shape([(626, 330), (616, 262), (628, 208), (668, 184), (712, 202), (726, 262), (716, 330)], c.fill, w=8, seed=9, shade=c.shade, shade_alpha=0.55)
    clip = ink.mask(path)
    ink.tint([oval(672, 276, 26, 24, wobble=0.04)], PINK, 1.0, 0.6, clip=clip)
    for u, v in ((636, 234), (672, 216), (708, 234)):
        ink.tint([oval(u, v, 12.5, 14)], PINK, 1.0, 0.5, clip=clip)


def tip_cat(ink, t, c):
    r = t.w * 0.52
    for s in (-0.2, 0.2):
        ink.stroke([t.at(r * 0.92, s * t.w), t.at(r * 0.92 - t.w * 0.26, s * t.w)], 4.5, INK, 3)


def raised_raccoon(ink, c):
    black = (40, 40, 48)
    ink.shape([(630, 340), (624, 290), (640, 262), (704, 262), (720, 290), (714, 340)], c.fill, w=8, seed=9, shade=c.shade, shade_alpha=0.55)
    path = ink.shape([(626, 288), (616, 244), (632, 204), (672, 188), (712, 204), (728, 244), (718, 288), (672, 298)], black, w=8, seed=8, samples=10)
    clip = ink.mask(path)
    ink.tint([oval(672, 256, 28, 24, wobble=0.04)], (112, 112, 124), 1.0, 0.8, clip=clip)
    ink.tint([oval(654, 222, 9, 8), oval(690, 222, 9, 8)], (112, 112, 124), 1.0, 0.6, clip=clip)


def tip_raccoon(ink, t, c):
    """Una manito negra y redonda, sin dedos."""
    w = t.w
    mitt = [t.at(-w * 0.62, -w * 0.5), t.at(-w * 0.62, w * 0.5), t.at(w * 0.1, w * 0.58), t.at(w * 0.46, w * 0.34), t.at(w * 0.58, 0), t.at(w * 0.46, -w * 0.34), t.at(w * 0.1, -w * 0.58)]
    ink.shape(mitt, (40, 40, 48), w=7, seed=4, samples=10)
    ink.fill(oval(*t.at(-w * 0.2, -w * 0.2), w * 0.14, w * 0.09), (96, 96, 108))


def raised_capy(ink, c):
    nails = (64, 40, 28)
    ink.shape([(620, 340), (612, 276), (626, 226), (672, 208), (718, 226), (732, 276), (724, 340)], c.fill, w=8, seed=9, shade=c.shade, shade_alpha=0.55)
    for u, v in ((630, 214), (660, 196), (690, 196), (718, 214)):
        ink.shape(oval(u, v, 19, 22, wobble=0.04), c.fill, w=6.5, seed=u, samples=6)
        ink.fill(oval(u, v - 16, 9, 7, steps=8), nails)
    ink.fill(oval(672, 276, 38, 32, wobble=0.05), (98, 64, 44))
    rng = random.Random(3)
    for _ in range(9):
        u, v = rng.uniform(646, 698), rng.uniform(256, 296)
        ink.stroke([(u, v), (u + 2, v + 6)], 2.4, (66, 42, 28), 2)


def tip_capy(ink, t, c):
    nails = (64, 40, 28)
    w = t.w
    r = w * 0.52
    for s in (-0.38, -0.13, 0.13, 0.38):
        ink.shape(oval(*t.at(r * 0.72, s * w), w * 0.17, w * 0.17, wobble=0.04, seed=int(s * 10) + 5), c.fill, w=5.5, samples=6)
        ink.fill(oval(*t.at(r * 0.72 + w * 0.11, s * w), w * 0.075, w * 0.075, steps=8), nails)


def raised_robot(ink, c):
    steel, dark = c.fill, (118, 130, 152)
    ink.box([(648, 340), (648, 262), (696, 262), (696, 340)], dark, w=6)
    ink.box([(634, 268), (710, 268), (710, 224), (634, 224)], steel, w=6, shade=(120, 132, 156))
    for x0 in (636, 662, 688):
        ink.box([(x0, 226), (x0, 188), (x0 + 20, 188), (x0 + 20, 226)], steel, w=5.5, shade=(120, 132, 156))
        ink.lines([(x0, 206), (x0 + 20, 206)], 3)
    ink.fill([(662, 238), (682, 238), (682, 256), (662, 256)], (80, 236, 218))


def raised_ghost(ink, c):
    path = ink.shape([(622, 340), (620, 280), (634, 226), (664, 192), (690, 182), (712, 200), (724, 258), (722, 340)], c.fill, w=8, seed=9, shade=c.shade, shade_alpha=0.75)
    clip = ink.mask(path)
    ink.tint([taper([(650, 300), (652, 250), (668, 214)], 11, 3)], c.shade, 0.75, 3, clip=clip)
    ink.tint([taper([(694, 300), (696, 262), (694, 226)], 9, 3)], c.shade, 0.6, 3, clip=clip)


def raised_glove(ink, c):
    white, stitch = (252, 250, 244), (60, 60, 68)
    ink.fill(taper([(672, 344), (672, 300), (672, 276)], 24, 20), INK)
    ink.fill(taper([(672, 344), (672, 300), (672, 276)], 16, 12), c.fill)
    for u, v, rx, ry in ((640, 222, 12, 25), (672, 208, 12, 28), (704, 222, 12, 25), (730, 250, 12, 18)):
        ink.shape(oval(u, v, rx, ry), white, w=6, seed=u, samples=6)
    ink.shape(oval(672, 252, 42, 34, wobble=0.02), white, w=6.5, seed=4, samples=6, shade=(200, 196, 188), shade_alpha=0.6)
    for u in (654, 672, 690):
        ink.stroke([(u, 236), (u, 258)], 3.4, stitch, 2)
    ink.box([(650, 288), (694, 288), (698, 304), (646, 304)], white, w=5.5)


def tip_glove(ink, t, c):
    white, stitch = (252, 250, 244), (60, 60, 68)
    for s in (-15, 0, 15):
        finger = [t.at(14, s - 8), t.at(40, s - 8), t.at(48, s), t.at(40, s + 8), t.at(14, s + 8)]
        ink.shape(finger, white, w=5, seed=s + 20, samples=6)
    thumb = [t.at(0, 22), t.at(16, 36), t.at(28, 32), t.at(18, 16)]
    ink.shape(thumb, white, w=5, samples=6)
    palm = [t.at(-16, -24), t.at(-16, 24), t.at(16, 27), t.at(28, 0), t.at(16, -27)]
    ink.shape(palm, white, w=6, samples=8)
    for s in (-9, 9):
        ink.stroke([t.at(-4, s), t.at(14, s)], 3, stitch, 2)
    ink.box([t.at(-30, -16), t.at(-30, 16), t.at(-16, 19), t.at(-16, -19)], white, w=5)


def raised_flipper(ink, c):
    path = ink.shape([(628, 340), (622, 292), (640, 232), (668, 186), (688, 176), (704, 200), (716, 268), (714, 340)], c.fill, w=8, seed=9, shade=c.shade, shade_alpha=0.6)
    clip = ink.mask(path)
    ink.tint([spline([(650, 330), (648, 280), (664, 226), (682, 196), (694, 226), (696, 290), (692, 330)], samples=8)], (214, 224, 240), 1.0, 1, clip=clip)


def tip_flipper(ink, t, c):
    w = t.w
    point = [t.at(-w * 0.4, -w * 0.5), t.at(w * 0.5, -w * 0.32), t.at(w * 1.1, 0), t.at(w * 0.5, w * 0.32), t.at(-w * 0.4, w * 0.5)]
    ink.shape(point, c.fill, w=7, seed=3, samples=10)
    ink.tint([spline([t.at(-w * 0.3, -w * 0.12), t.at(w * 0.46, -w * 0.08), t.at(w * 0.72, 0), t.at(w * 0.46, w * 0.08), t.at(-w * 0.3, w * 0.12)], samples=8)], (214, 224, 240), 0.9, 0.8)


WOOD = (204, 114, 44)
WOOD_LIGHT = (232, 146, 68)
WOOD_DEEP = (130, 62, 20)


def raised_none(ink, c):
    """No hay pata levantada: el brazo del bate se dibuja en cada cuadro."""


# El bate: lo que no cambia se dibuja una vez y después solo se gira.
BAT_R = 240
BAT_LENGTH = 266
BAT_GRIP = 48
BAT_TIP = BAT_LENGTH - BAT_GRIP
BAT_REST_HAND = (694, 268)
BAT_REST_ANGLE = math.radians(-70)
# La punta se queda un poco antes del centro de la tecla, para que se vea encendida.
BAT_KEY_GAP = 12


def make_bat():
    """Apunta a la derecha, con el agarre justo en el centro de la imagen."""
    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    ink = Ink(layer)

    def on_bat(t, s):
        return (PIVOT[0] - BAT_GRIP + BAT_LENGTH * t, PIVOT[1] + s)

    profile = ((0.0, 14), (0.03, 15), (0.07, 9), (0.32, 9.5), (0.56, 16), (0.8, 24), (0.95, 25), (1.0, 17))
    outline = [on_bat(t, w) for t, w in profile] + [on_bat(1.02, 0)] + [on_bat(t, -w) for t, w in reversed(profile)] + [on_bat(-0.025, 0)]
    path = ink.shape(outline, WOOD, w=7, seed=6, samples=8, shade=WOOD_DEEP, shade_alpha=0.65)
    clip = ink.mask(path)
    ink.tint([taper([on_bat(0.4, -5), on_bat(0.7, -10), on_bat(0.95, -12)], 7, 5)], (255, 196, 130), 0.75, 1.2, clip=clip)
    for t0, t1, s in ((0.42, 0.72, 5), (0.56, 0.94, 3), (0.68, 0.92, 11)):
        ink.tint([taper([on_bat(t0, s), on_bat((t0 + t1) / 2, s + 1), on_bat(t1, s)], 3, 2)], WOOD_DEEP, 0.8, 0.4, clip=clip)
    # La mano agarra el bate: la palma atrás y los dedos dando la vuelta.
    grip = BAT_GRIP / BAT_LENGTH
    ink.shape(oval(*on_bat(grip, 2), 25, 22, wobble=0.05, seed=4), WOOD_LIGHT, w=6.5, seed=7, samples=6, shade=WOOD_DEEP, shade_alpha=0.6)
    for offset, seed in ((0.07, 21), (0.0, 22), (-0.07, 23)):
        center = on_bat(grip + offset, -8)
        ink.shape(oval(center[0], center[1], 10, 15, wobble=0.03, seed=seed), WOOD_LIGHT, w=5, seed=seed, samples=6)
    cx, cy = px(PIVOT)
    r = BAT_R * S
    return layer.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r)))


def tip_wood(ink, t, c):
    for s in (-14, 0, 14):
        ink.shape(oval(*t.at(26 - abs(s) * 0.12, s), 8.5, 12, wobble=0.04, seed=s + 20), WOOD_LIGHT, w=5, seed=s + 21, samples=6)
    palm = [t.at(-14, -20), t.at(-14, 20), t.at(12, 24), t.at(24, 0), t.at(12, -24)]
    ink.shape(palm, WOOD_LIGHT, w=6, seed=4, samples=8, shade=WOOD_DEEP, shade_alpha=0.6)


def tip_none(ink, t, c):
    pass


# Teclado y alfombrilla: planos sobre la mesa, de cara al personaje


KB_CORNERS = [(530, 322), (884, 322), (950, 482), (492, 482)]
PAD_CORNERS = [(138, 336), (410, 336), (390, 480), (72, 480)]
F = 3
BW, BH = old.BOARD_W, old.BOARD_H
PW, PH = 300, 190


def key_font(size):
    try:
        return ImageFont.truetype(FONT_HAND, size)
    except OSError:
        return ImageFont.truetype(FONT_BOLD, size)


def flat_board(lit):
    image = Image.new("RGBA", (BW * F, BH * F), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for name, (x0, y0, x1, y1) in old.KEYS.items():
        box = [x0 * F + 2, y0 * F + 2, x1 * F - 2, y1 * F - 2]
        fill = ACCENT + (255,) if lit else (255, 255, 252, 255)
        draw.rounded_rectangle(box, radius=7 * F, fill=fill, outline=INK + (255,), width=int(2.2 * F))
        size = (14 if len(name) > 1 else 21) * F
        draw.text(((x0 + x1) / 2 * F, (y0 + y1) / 2 * F - 1), name, font=key_font(size), fill=INK + (255,), anchor="mm")
    return image.rotate(180)


def warp_to(flat, flat_size, corners):
    src = [(0, 0), (flat_size[0], 0), flat_size, (0, flat_size[1])]
    dst = [tuple(v for v in px(c)) for c in corners]
    coeffs = old.homography(dst, src)
    return flat.transform((W * S, H * S), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)


def map_flat(p, flat_size, corners):
    src = [(0, 0), (flat_size[0], 0), flat_size, (0, flat_size[1])]
    h = old.homography(src, corners)
    w = h[6] * p[0] + h[7] * p[1] + 1
    return ((h[0] * p[0] + h[1] * p[1] + h[2]) / w, (h[3] * p[0] + h[4] * p[1] + h[5]) / w)


def key_uv(name):
    """Dónde cae una tecla sobre la mesa, ya girada media vuelta."""
    x0, y0, x1, y1 = old.KEYS[name]
    return BW - x1, BH - y1, BW - x0, BH - y0


def build_shared():
    kb = warp_to(flat_board(False), (BW * F, BH * F), KB_CORNERS)
    lit = warp_to(flat_board(True), (BW * F, BH * F), KB_CORNERS)
    sprites = {}
    for name in old.KEYS:
        a, b, c, d = key_uv(name)
        points = [px(map_flat(q, (BW, BH), KB_CORNERS)) for q in ((a, b), (c, b), (c, d), (a, d))]
        left, top = int(min(p[0] for p in points)) - 3, int(min(p[1] for p in points)) - 3
        right, bottom = int(max(p[0] for p in points)) + 4, int(max(p[1] for p in points)) + 4
        mask = Image.new("L", (right - left, bottom - top), 0)
        ImageDraw.Draw(mask).polygon([(x - left, y - top) for x, y in points], fill=255)
        crop = lit.crop((left, top, right, bottom))
        crop.putalpha(ImageChops.multiply(mask, crop.getchannel("A")))
        sprites[name] = (crop, (left, top))
    pad_flat = Image.new("RGBA", (PW * F, PH * F), (0, 0, 0, 0))
    grad = old.gradient((PW * F, PH * F), (204, 206, 212), (152, 156, 166))
    mask = Image.new("L", (PW * F, PH * F), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, PW * F - 1, PH * F - 1], radius=20 * F, fill=255)
    pad_flat.paste(grad, (0, 0), mask)
    draw = ImageDraw.Draw(pad_flat)
    draw.rounded_rectangle([3 * F, 3 * F, PW * F - 4 * F, PH * F - 4 * F], radius=18 * F, outline=(236, 238, 242, 255), width=2 * F)
    pad = warp_to(pad_flat, (PW * F, PH * F), PAD_CORNERS)
    return kb, sprites, pad


def build_mouse():
    out = {}
    w, h = 84, 112
    for click in (None, "L", "R"):
        image = Image.new("RGBA", (w * F, h * F), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        body = [14 * F, 12 * F, 70 * F, 100 * F]
        draw.rounded_rectangle(body, radius=27 * F, fill=(255, 255, 252, 255), outline=INK + (255,), width=int(3.2 * F))
        if click:
            shape = Image.new("L", image.size, 0)
            ImageDraw.Draw(shape).rounded_rectangle([17 * F, 15 * F, 67 * F, 97 * F], radius=24 * F, fill=255)
            half = Image.new("L", image.size, 0)
            x0, x1 = (17, 42) if click == "L" else (42, 67)
            ImageDraw.Draw(half).rectangle([x0 * F, 15 * F, x1 * F, 46 * F], fill=255)
            image.paste(ACCENT + (255,), (0, 0), ImageChops.multiply(shape, half))
        draw.line([42 * F, 12 * F, 42 * F, 46 * F], fill=INK + (255,), width=int(2.4 * F))
        draw.line([15 * F, 46 * F, 69 * F, 46 * F], fill=INK + (255,), width=int(2.4 * F))
        draw.rounded_rectangle([38 * F, 22 * F, 46 * F, 38 * F], radius=4 * F, fill=ACCENT + (255,), outline=INK + (255,), width=int(1.6 * F))
        turned = image.rotate(180 - math.degrees(ANGLE), expand=False, resample=Image.BICUBIC)
        out[click] = turned.resize((int(w * S * 1.3), int(h * S * 1.08)), Image.LANCZOS)
    return out


SHARED = None


def shared():
    global SHARED
    if SHARED is None:
        SHARED = (*build_shared(), build_mouse())
    return SHARED


# Personajes: cada uno es una loma grande detrás de la mesa, con su cara.


def dome(points):
    return points + [(points[-1][0], 430), (points[0][0], 430)]


def eye_dot(ink, u, v, rx, ry, look, blink, seed=1, color=INK, shine=True):
    if blink:
        ink.stroke([(u - rx * 1.2, v), (u, v + ry * 0.55), (u + rx * 1.2, v)], max(4, rx * 0.46), color, seed)
        return
    cx, cy = u + look[0] * 5, v + look[1] * 4
    ink.fill(oval(cx, cy, rx, ry, wobble=0.04, seed=seed), color)
    if shine:
        ink.fill(oval(cx - rx * 0.32, cy - ry * 0.36, rx * 0.32, ry * 0.3), (255, 255, 255))
        ink.fill(oval(cx + rx * 0.3, cy + ry * 0.34, rx * 0.14, ry * 0.13), (255, 255, 255))


def eye_big(ink, u, v, rx, ry, look, blink, iris=INK, seed=1, line=INK):
    """Ojo con blanco, para las caras oscuras."""
    if blink:
        ink.stroke([(u - rx, v), (u, v + ry * 0.5), (u + rx, v)], 5.5, line, seed)
        return
    ink.shape(oval(u, v, rx, ry, wobble=0.03, seed=seed), (255, 255, 255), w=4.5, seed=seed, samples=6)
    eye_dot(ink, u + look[0] * 3, v + look[1] * 3, rx * 0.62, ry * 0.66, look, False, seed, iris)


def mouth_w(ink, u, v, w, seed=3):
    ink.stroke([(u - w, v - 3), (u - w * 0.5, v + w * 0.5), (u, v - 1)], 5.5, INK, seed)
    ink.stroke([(u, v - 1), (u + w * 0.5, v + w * 0.5), (u + w, v - 3)], 5.5, INK, seed + 1)


def mouth_open(ink, u, v, w, h, level, inner=(120, 26, 44), tongue=(250, 130, 150), seed=5):
    width = w * (0.6 + 0.4 * level)
    height = max(7, h * level)
    points = [(u - width, v), (u, v - 1), (u + width, v), (u + width * 0.86, v + height * 0.6), (u, v + height), (u - width * 0.86, v + height * 0.6)]
    path = spline(points, samples=8)
    ink.fill(path, inner)
    if height > 14:
        clip = ink.mask(path)
        ink.tint([oval(u, v + height * 0.82, width * 0.62, height * 0.4)], tongue, 1.0, 0.4, clip=clip)
    ink.stroke_path(path + path[:1], 5, INK, seed, ends=False)


# Michi


CAT = dome([(248, 322), (262, 248), (276, 214), (262, 200), (292, 186), (312, 158), (346, 112), (360, 72), (376, 38), (402, 56), (442, 84), (506, 76),
            (570, 84), (612, 54), (640, 36), (652, 72), (664, 112), (702, 160), (722, 188), (750, 202), (736, 216), (750, 250), (764, 322)])


def michi_static(ink):
    orange, deep = (247, 160, 74), (214, 112, 40)
    body = spline(CAT)
    ink.fill(body, (255, 250, 240))
    clip = ink.mask(body)
    ink.tint([spline([(330, 30), (392, 20), (452, 80), (470, 128), (420, 158), (352, 168), (296, 200), (250, 130)])], orange, 1.0, 1.6, clip=clip)
    ink.tint([spline([(590, 30), (660, 20), (690, 130), (640, 138), (590, 108), (572, 72)])], orange, 1.0, 1.6, clip=clip)
    for a, b, c in (((470, 74), (476, 96), (472, 120)), ((506, 70), (506, 94), (506, 116)), ((542, 74), (536, 96), (540, 120))):
        ink.stroke([a, b, c], 8, deep, 4)
    for a, b, c in (((300, 182), (328, 188), (354, 186)), ((292, 208), (324, 212), (352, 208))):
        ink.stroke([a, b, c], 7, deep, 7)
    ink.inner_shade(body, (214, 190, 170), 0.5)
    for u, flip in ((384, -1), (628, 1)):
        ink.tint([spline([(u - 18 * flip, 92), (u + 2 * flip, 58), (u + 12 * flip, 96)])], PINK, 0.95, 1.2)
    ink.stroke_path(body + body[:1], 8, INK, 2, ends=False)
    for u in (402, 610):
        ink.tint([oval(u, 214, 28, 16)], (255, 130, 140), 0.55, 6, clip=clip)
    for sign in (-1, 1):
        for dv, end in ((-4, -14), (10, 12)):
            ink.stroke([(506 + sign * 128, 206 + dv), (506 + sign * 168, 202 + dv + end * 0.4), (506 + sign * 206, 196 + dv + end)], 3.4, INK, 5 + dv)
    ink.fill(spline([(494, 192), (518, 192), (506, 204)], samples=6), (240, 110, 130))


def michi_face(ink, s):
    eye_dot(ink, 430, 170, 17, 20, s.look, s.blink, 1)
    eye_dot(ink, 582, 170, 17, 20, s.look, s.blink, 2)
    if s.mouth < 0.08:
        mouth_w(ink, 506, 212, 26)
    else:
        mouth_open(ink, 506, 212, 25, 40, s.mouth)


# Robi: todo recto, de chapa y tornillos


def robi_static(ink):
    steel, dark, line = (206, 214, 228), (138, 150, 172), (92, 104, 128)
    # Antena
    ink.lines([(506, 74), (506, 34)], 7)
    ink.box([(490, 12), (522, 12), (522, 38), (490, 38)], (120, 240, 226), w=5.5)
    # Torso con hombros en chaflán
    ink.box([(316, 262), (696, 262), (740, 306), (740, 430), (272, 430), (272, 306)], dark, w=8, shade=(92, 104, 128), shade_alpha=0.6)
    ink.box([(420, 282), (592, 282), (592, 330), (420, 330)], (24, 30, 42), w=5.5)
    for i, color in enumerate(((80, 236, 218), (246, 200, 90), (240, 96, 96))):
        ink.fill([(440 + i * 46, 296), (472 + i * 46, 296), (472 + i * 46, 316), (440 + i * 46, 316)], color)
    # Módulos a los costados de la cabeza
    for x0, x1 in ((258, 292), (720, 754)):
        ink.box([(x0, 122), (x1, 122), (x1, 218), (x0, 218)], dark, w=6.5)
        ink.fill([(x0 + 10, 136), (x1 - 10, 136), (x1 - 10, 204), (x0 + 10, 204)], (80, 236, 218))
    # Cabeza: rectángulo con esquinas cortadas
    ink.box([(322, 72), (690, 72), (726, 108), (726, 234), (690, 270), (322, 270), (286, 234), (286, 108)], steel, w=8, shade=dark, shade_alpha=0.7)
    # Pantalla
    ink.box([(346, 104), (666, 104), (684, 122), (684, 220), (666, 238), (346, 238), (328, 220), (328, 122)], (20, 26, 38), w=6.5)
    ink.fill([(346, 112), (420, 112), (372, 190), (338, 190), (338, 126)], (46, 56, 74))
    # Rejilla, costuras y tornillos
    for u in (424, 456, 488, 520, 552, 584):
        ink.lines([(u, 84), (u, 96)], 3.5, line)
    ink.lines([(300, 252), (712, 252)], 3, line)
    for u, v in ((302, 92), (710, 92), (302, 250), (710, 250), (292, 292), (720, 292)):
        ink.fill(oval(u, v, 7, 7, steps=8), line)
        ink.lines([(u - 4, v - 4), (u + 4, v + 4)], 1.6, INK)


def robi_face(ink, s):
    color = (170, 255, 244) if s.mouth > 0.08 else (80, 236, 218)
    for u in (430, 582):
        cx, cy = u + s.look[0] * 9, 152 + s.look[1] * 6
        half = 3 if s.blink else 25
        ink.fill([(cx - 28, cy - half), (cx + 28, cy - half), (cx + 28, cy + half), (cx - 28, cy + half)], color)
    for i in range(13):
        wave = abs(math.sin(s.t * 11 + i * 1.05))
        h = 2.5 + s.mouth * 15 * (0.3 + 0.7 * wave)
        u = 506 + (i - 6) * 15
        ink.fill([(u - 5, 210 - h), (u + 5, 210 - h), (u + 5, 210 + h), (u - 5, 210 + h)], color)


# Mapache


COON = dome([(248, 322), (260, 244), (296, 164), (350, 112), (424, 82), (506, 76), (588, 82), (662, 112), (716, 164), (752, 244), (764, 322)])


def mapache_static(ink):
    gray, dark, white, black = (160, 162, 172), (92, 94, 106), (248, 248, 244), (36, 36, 44)
    # La cola anillada asoma detrás.
    tail = spline([(726, 340), (760, 268), (806, 200), (850, 150), (892, 160), (886, 226), (838, 300), (786, 356)])
    ink.fill(tail, gray)
    clip = ink.mask(tail)
    for cx, cy in ((772, 276), (808, 228), (844, 186), (880, 152)):
        band = [(cx - 54, cy - 44), (cx + 54, cy + 42), (cx + 66, cy + 26), (cx - 42, cy - 60)]
        ink.tint([band], black, 1.0, 0.8, clip=clip)
    ink.stroke_path(tail + tail[:1], 8, INK, 6, ends=False)
    for u in (352, 660):
        ink.shape(oval(u, 86, 42, 40, wobble=0.03), white, w=7.5, seed=u, samples=6)
        ink.fill(oval(u, 94, 26, 26), black)
    body = ink.shape(COON, gray, w=8, seed=3, shade=dark, shade_alpha=0.55)
    clip = ink.mask(body)
    rng = random.Random(8)
    for _ in range(46):
        u, v = rng.uniform(262, 750), rng.uniform(118, 310)
        if 330 < u < 684 and 130 < v < 310:
            continue
        ink.stroke([(u, v), (u + rng.uniform(-3, 3), v + 8), (u + rng.uniform(-5, 5), v + 16)], 2.6, dark, rng.randint(1, 9))
    for sign in (-1, 1):
        ink.tint([spline([(506 + sign * 18, 134), (506 + sign * 72, 108), (506 + sign * 138, 128), (506 + sign * 112, 150), (506 + sign * 40, 156)])], white, 1.0, 1.4, clip=clip)
    ink.tint([spline([(320, 176), (372, 146), (450, 150), (506, 180), (562, 150), (640, 146), (692, 176), (668, 222), (598, 232), (506, 206), (414, 232), (344, 222)])], black, 1.0, 1.4, clip=clip)
    ink.tint([taper([(506, 92), (506, 130), (506, 186)], 22, 10)], dark, 0.95, 1.2, clip=clip)
    muzzle = spline([(506, 186), (556, 214), (600, 262), (566, 300), (506, 312), (446, 300), (412, 262), (456, 214)])
    ink.fill(muzzle, white)
    ink.inner_shade(muzzle, (196, 198, 206), 0.6)
    ink.shape([(486, 214), (526, 214), (530, 228), (506, 244), (482, 228)], black, w=4, samples=8)
    ink.fill(oval(498, 222, 6, 3.5), (150, 150, 164))
    ink.stroke([(506, 244), (506, 258)], 4, INK, 3)
    for sign in (-1, 1):
        for dv, end in ((-2, -12), (12, 10)):
            ink.stroke([(506 + sign * 80, 256 + dv), (506 + sign * 136, 252 + dv + end * 0.4), (506 + sign * 196, 246 + dv + end)], 3, INK, 5 + dv)


def mapache_face(ink, s):
    eye_big(ink, 424, 186, 21, 22, s.look, s.blink, seed=1, line=(250, 250, 250))
    eye_big(ink, 588, 186, 21, 22, s.look, s.blink, seed=2, line=(250, 250, 250))
    if s.mouth < 0.08:
        mouth_w(ink, 506, 262, 26)
    else:
        mouth_open(ink, 506, 260, 25, 34, s.mouth)


# Carpincho


CAPY = dome([(248, 322), (260, 240), (284, 170), (330, 104), (404, 76), (506, 68), (608, 76), (682, 104), (728, 170), (752, 240), (764, 322)])
SNOUT = [(506, 148), (604, 158), (652, 208), (654, 264), (596, 300), (506, 310), (416, 300), (358, 264), (360, 208), (408, 158)]


def carpincho_static(ink):
    fur, deep, light = (178, 126, 84), (130, 86, 52), (226, 188, 146)
    for u in (340, 672):
        ink.shape(oval(u, 94, 32, 28, wobble=0.05), fur, w=7, samples=6)
        ink.fill(oval(u, 98, 15, 13), (94, 58, 40))
    ink.shape(CAPY, fur, w=8, seed=3, shade=(92, 58, 36), shade_alpha=0.55)
    rng = random.Random(4)
    for _ in range(60):
        u, v = rng.uniform(270, 740), rng.uniform(90, 300)
        if 340 < u < 672 and v > 140:
            continue
        ink.stroke([(u, v), (u + rng.uniform(-3, 3), v + 7), (u + rng.uniform(-5, 5), v + 15)], 2.8, deep, rng.randint(1, 9))
    ink.shape(SNOUT, light, w=6.5, seed=7, shade=(180, 134, 96), shade_alpha=0.6)
    for u in (462, 550):
        ink.fill(spline([(u - 14, 204), (u, 196), (u + 14, 204), (u, 214)], samples=6), (70, 40, 30))
    ink.stroke([(506, 216), (506, 236)], 4, (110, 70, 50), 3)
    ink.shape(oval(506, 42, 39, 35, wobble=0.03), (250, 160, 50), w=6.5, samples=6, shade=(206, 100, 18), shade_alpha=0.7)
    ink.tint([oval(490, 28, 10, 6)], (255, 226, 170), 0.9, 1)
    ink.shape([(508, 10), (530, -6), (556, 2), (532, 16)], (120, 190, 90), w=5, samples=6)
    for u, flip in ((384, 1), (628, -1)):
        ink.stroke([(u - 28 * flip, 120), (u, 112), (u + 28 * flip, 120)], 5.5, (84, 50, 32), 6)


def carpincho_face(ink, s):
    eye_dot(ink, 384, 148, 13, 14, s.look, s.blink, 1)
    eye_dot(ink, 628, 148, 13, 14, s.look, s.blink, 2)
    if s.mouth < 0.08:
        ink.stroke([(456, 262), (506, 274), (556, 262)], 6, INK, 3)
    else:
        mouth_open(ink, 506, 262, 34, 32, s.mouth, inner=(96, 30, 34), tongue=(230, 116, 118))
        if s.mouth > 0.3:
            for u in (494, 518):
                ink.fill(spline([(u - 9, 262), (u + 9, 262), (u + 8, 273), (u - 8, 273)], samples=4), (252, 250, 244))


# Fanti


GHOST = dome([(262, 322), (276, 228), (316, 132), (402, 68), (506, 52), (610, 68), (696, 132), (736, 228), (750, 322)])


def fanti_static(ink):
    body = ink.shape(GHOST, (252, 250, 255), w=8, seed=3, shade=(176, 164, 226), shade_alpha=0.7)
    clip = ink.mask(body)
    for points in ([(322, 250), (340, 290), (330, 330)], [(690, 250), (672, 290), (682, 330)]):
        ink.tint([spline(points + [(p[0] + 12, p[1]) for p in points[::-1]], samples=6)], (176, 164, 226), 0.5, 5, clip=clip)
    for u in (376, 636):
        ink.tint([oval(u, 214, 32, 18)], (255, 160, 190), 0.75, 6, clip=clip)
    ink.shape([(420, 48), (506, 40), (596, 52), (606, 66), (506, 62), (410, 62)], (56, 38, 92), w=6.5, samples=8)
    ink.shape([(444, 50), (448, -8), (560, -10), (572, 50)], (72, 50, 116), w=6.5, seed=4, samples=8, shade=(30, 20, 60), shade_alpha=0.7)
    ink.shape([(446, 28), (506, 24), (570, 28), (572, 48), (506, 46), (446, 48)], (246, 76, 136), w=4.5, samples=6)
    for sign in (-1, 1):
        ink.shape([(506 + sign * 6, 288), (506 + sign * 52, 262), (506 + sign * 76, 268), (506 + sign * 72, 310), (506 + sign * 46, 316)], (246, 76, 136), w=6, samples=8, shade=(150, 24, 84), shade_alpha=0.6)
    ink.shape(oval(506, 290, 15, 16), (255, 120, 170), w=6, samples=6)


def fanti_face(ink, s):
    eye_dot(ink, 436, 168, 21, 28, s.look, s.blink, 1, (24, 18, 40))
    eye_dot(ink, 576, 168, 21, 28, s.look, s.blink, 2, (24, 18, 40))
    if s.mouth < 0.08:
        ink.stroke([(490, 214), (506, 224), (522, 214)], 5.5, INK, 3)
    else:
        mouth_open(ink, 506, 212, 20, 36, s.mouth, inner=(66, 34, 96), tongue=(244, 120, 164))


# Matecito


GOURD = dome([(262, 322), (270, 244), (306, 170), (360, 118), (506, 96), (652, 118), (706, 170), (742, 244), (750, 322)])


def mate_static(ink):
    wood, deep = (182, 122, 76), (128, 80, 44)
    ink.stroke([(574, 76), (620, 34), (660, -2)], 15, INK, 3, ends=False)
    ink.stroke([(574, 76), (620, 34), (660, -2)], 8, (226, 230, 238), 3, ends=False)
    ink.shape([(644, -14), (672, -16), (680, 4), (654, 12)], (246, 200, 100), w=5, samples=6)
    body = ink.shape(GOURD, wood, w=8, seed=3, shade=(92, 54, 28), shade_alpha=0.6)
    clip = ink.mask(body)
    for du in (-186, -130, -66, 66, 130, 186):
        points = [(506 + du * 0.72, 118), (506 + du * 1.05, 210), (506 + du, 320)]
        ink.stroke_path(spline(points, closed=False), 3.2, deep, 3 + du)
    ink.shape([(392, 92), (442, 60), (506, 48), (570, 60), (620, 92), (506, 106)], (146, 196, 80), w=6.5, samples=8, shade=(86, 134, 46), shade_alpha=0.7)
    rng = random.Random(6)
    for _ in range(26):
        u, v = rng.uniform(420, 592), rng.uniform(64, 96)
        ink.fill(oval(u, v, 3, 2), (84, 132, 44))
    ink.shape([(362, 96), (506, 86), (650, 96), (660, 118), (652, 130), (506, 140), (360, 130), (352, 118)], (232, 236, 242), w=6.5, samples=8, shade=(150, 156, 172), shade_alpha=0.7)
    for i in range(8):
        u = 394 + i * 32
        v = 118 + 6 * (1 - ((u - 506) / 112) ** 2)
        ink.shape([(u, v - 9), (u + 8, v), (u, v + 9), (u - 8, v)], (246, 200, 100), w=2.8, samples=3)
    for u in (394, 618):
        ink.tint([oval(u, 248, 28, 15)], (238, 110, 90), 0.6, 6, clip=clip)
    ink.shape([(322, 280), (506, 268), (690, 280), (660, 316), (506, 344), (352, 316)], (232, 62, 66), w=6.5, samples=8, shade=(150, 24, 40), shade_alpha=0.6)
    rng = random.Random(9)
    for _ in range(26):
        u, v = rng.uniform(350, 662), rng.uniform(284, 312)
        ink.fill(oval(u, v, 3.2, 3.2), (255, 240, 226))


def mate_face(ink, s):
    eye_big(ink, 436, 196, 28, 31, s.look, s.blink, seed=1)
    eye_big(ink, 576, 196, 28, 31, s.look, s.blink, seed=2)
    for u, flip in ((436, 1), (576, -1)):
        ink.stroke([(u - 26 * flip, 158), (u, 150), (u + 26 * flip, 156)], 6.5, (62, 34, 16), 4)
    if s.mouth < 0.08:
        ink.stroke([(482, 246), (506, 258), (530, 246)], 6, INK, 3)
    else:
        mouth_open(ink, 506, 244, 24, 26, s.mouth, inner=(84, 26, 26), tongue=(236, 120, 120))


# Pingüi


PENGUIN = dome([(248, 322), (262, 236), (300, 156), (362, 96), (506, 76), (650, 96), (712, 156), (750, 236), (764, 322)])


def pingui_static(ink):
    navy = (58, 68, 100)
    ink.shape(PENGUIN, navy, w=8, seed=3, shade=(20, 26, 48), shade_alpha=0.6)
    ink.shape([(506, 128), (540, 108), (612, 118), (668, 172), (640, 250), (506, 296), (372, 250), (344, 172), (400, 118), (472, 108)], (255, 255, 255), w=5, seed=5, samples=8, shade=(196, 208, 226), shade_alpha=0.7)
    for u in (398, 614):
        ink.tint([oval(u, 226, 26, 15)], (255, 130, 140), 0.65, 6)
    ink.shape([(350, 104), (404, 44), (506, 22), (608, 44), (662, 104), (650, 132), (506, 120), (362, 132)], (240, 72, 78), w=7, seed=6, samples=8, shade=(150, 26, 44), shade_alpha=0.6)
    for i in range(9):
        u = 386 + i * 30
        ink.stroke([(u, 46 + abs(u - 506) * 0.42), (u + (u - 506) * 0.05, 110)], 3, (176, 36, 54), i)
    ink.shape([(346, 100), (506, 84), (666, 100), (672, 126), (660, 140), (506, 128), (352, 140), (340, 126)], (255, 255, 255), w=6.5, seed=7, samples=8, shade=(190, 204, 224), shade_alpha=0.7)
    angles = np.linspace(0, math.tau, 18, endpoint=False)
    pom = [(506 + (31 + 6 * (i % 2)) * math.cos(a), 12 + (29 + 6 * (i % 2)) * math.sin(a)) for i, a in enumerate(angles)]
    ink.shape(pom, (255, 255, 255), w=6, seed=8, samples=4)
    ink.shape([(306, 278), (506, 296), (706, 278), (722, 312), (506, 336), (292, 312)], (240, 72, 78), w=6.5, samples=8, shade=(150, 26, 44), shade_alpha=0.6)


def pingui_face(ink, s):
    eye_dot(ink, 442, 176, 17, 21, s.look, s.blink, 1)
    eye_dot(ink, 570, 176, 17, 21, s.look, s.blink, 2)
    opened = s.mouth * 26
    if opened > 2:
        ink.fill(spline([(470, 214), (542, 214), (528, 238 + opened), (484, 238 + opened)], samples=4), (110, 28, 40))
    ink.shape([(474, 222 + opened), (538, 222 + opened), (506, 252 + opened)], (232, 136, 34), w=5, samples=6)
    ink.shape([(462, 206), (506, 200), (550, 206), (540, 222), (506, 244), (472, 222)], (255, 186, 62), w=5.5, samples=8)


# Tun Tun Sahur: alto, de madera lustrada


TALL = dome([(388, 322), (382, 250), (372, 180), (366, 110), (372, 50), (416, 20), (506, 10), (596, 20), (640, 50), (646, 110), (640, 180), (630, 250), (624, 322)])


def tuntun_static(ink):
    body = ink.shape(TALL, WOOD, w=8, seed=3, shade=WOOD_DEEP, shade_alpha=0.7)
    clip = ink.mask(body)
    # El brillo del lustre y la veta, que corre a lo largo.
    ink.tint([taper([(398, 60), (392, 170), (406, 300)], 22, 16)], (246, 170, 98), 0.55, 5, clip=clip)
    ink.tint([taper([(420, 26), (506, 18), (600, 26)], 12, 12)], (255, 206, 150), 0.6, 3, clip=clip)
    rng = random.Random(21)
    for _ in range(44):
        u, v = rng.uniform(370, 642), rng.uniform(52, 300)
        if 400 < u < 612 and 84 < v < 296:
            continue
        length = rng.uniform(26, 90)
        # Recortada: si no, la veta se sale del contorno donde el cuerpo se angosta.
        ink.tint([taper([(u, v), (u + rng.uniform(-2, 2), v + length / 2), (u + rng.uniform(-3, 3), v + length)], rng.uniform(2, 3.2), 1.4)], (150, 76, 26), 0.95, 0.4, clip=clip)
    # La tapa de arriba, apenas marcada.
    ink.stroke([(370, 56), (436, 45), (506, 43), (576, 45), (642, 56)], 3.4, WOOD_DEEP, 4)
    for sign in (-1, 1):
        # Las cuencas de los ojos, los pómulos y los pliegues de la sonrisa.
        ink.tint([oval(506 + sign * 68, 148, 58, 60)], (120, 54, 16), 0.55, 8, clip=clip)
        ink.tint([oval(506 + sign * 92, 212, 24, 18)], (255, 170, 110), 0.5, 6, clip=clip)
        ink.stroke([(506 + sign * 84, 206), (506 + sign * 92, 232), (506 + sign * 80, 254)], 4.6, WOOD_DEEP, 5)
    # La nariz larga, que baja desde el entrecejo.
    nose = spline([(490, 106), (522, 106), (524, 168), (538, 204), (526, 224), (506, 230), (486, 224), (474, 204), (488, 168)], samples=8)
    ink.tint([oval(510, 216, 44, 30)], (120, 54, 16), 0.5, 6, clip=clip)
    ink.fill(nose, WOOD_LIGHT)
    ink.inner_shade(nose, WOOD_DEEP, 0.55, shift=8, blur=5)
    ink.stroke([(490, 118), (488, 168), (474, 204), (486, 224), (506, 230), (526, 224), (538, 204), (524, 168), (522, 118)], 5.4, INK, 6)
    for u in (495, 517):
        ink.fill(oval(u, 220, 5.5, 3.6, steps=8), (48, 20, 6))


def tuntun_face(ink, s):
    rise = s.mouth * 6
    for u, flip, seed in ((438, 1, 1), (574, -1, 2)):
        if s.blink:
            ink.stroke([(u - 40, 148), (u, 164), (u + 40, 148)], 7, INK, seed)
        else:
            ink.shape(oval(u, 148, 42, 45, wobble=0.015, seed=seed), (255, 255, 250), w=6, seed=seed, samples=6, shade=(210, 200, 186), shade_alpha=0.5)
            cx, cy = u + s.look[0] * 13, 152 + s.look[1] * 12
            ink.fill(oval(cx, cy, 26, 27), (104, 54, 22))
            ink.fill(oval(cx, cy + 2, 16, 17), INK)
            ink.fill(oval(cx - 8, cy - 10, 7.5, 7), (255, 255, 255))
            ink.fill(oval(cx + 9, cy + 9, 3.2, 3.2), (255, 255, 255))
            # El párpado de madera tapa apenas la parte de arriba del ojo.
            ink.shape([(u - 40, 128), (u - 26, 108), (u, 100), (u + 26, 108), (u + 40, 128), (u, 112)], WOOD, w=5, seed=seed + 5, samples=8)
        ink.stroke([(u - 54 * flip, 126 - rise), (u - 32 * flip, 96 - rise), (u + 2 * flip, 84 - rise), (u + 44 * flip, 102 - rise)], 15, (66, 30, 10), seed + 3)
    # Siempre sonríe: al hablar, la sonrisa se abre hacia abajo.
    height = 14 + 28 * s.mouth
    ts = np.linspace(-1, 1, 25)

    def upper(t):
        return 240 + 10 * (1 - t * t) - 8 * t * t

    top = [(506 + 70 * t, upper(t)) for t in ts]
    low = [(506 + 70 * t, upper(t) + height * max(0.0, 1 - t * t) ** 0.7) for t in ts[::-1]]
    ink.fill(top + low, (58, 18, 14))
    clip = ink.mask(top + low)
    if s.mouth > 0.2:
        ink.tint([oval(506, 250 + height - 8, 30, min(13, height * 0.3))], (236, 116, 126), 1.0, 0.5, clip=clip)
    band = [(506 + 70 * t, upper(t) + 13 * max(0.0, 1 - t * t) ** 0.5) for t in ts[::-1]]
    ink.tint([top + band], (252, 250, 244), 1.0, 0.2, clip=clip)
    for t in np.linspace(-0.72, 0.72, 7):
        ink.stroke_path([(506 + 70 * t, upper(t)), (506 + 70 * t, upper(t) + 12 * max(0.0, 1 - t * t) ** 0.5)], 1.8, (150, 140, 128), 2, ends=False)
    ink.stroke_path(top + low + top[:1], 5.4, INK, 3, ends=False)


@dataclass(frozen=True)
class Drawn:
    id: str
    name: str
    note: str
    hand: str
    static: object
    face: object
    fill: tuple[int, int, int]
    raised: object
    tip: object
    back: tuple[int, int, int] = (24, 24, 30)
    shade: tuple[int, int, int] = (214, 190, 170)
    root: tuple[int, int] = (348, 222)
    root2: tuple[int, int] = (672, 236)
    mouse_arm: tuple[int, int] = (108, 76)
    key_arm: tuple[int, int] = (92, 58)
    tip_reach: int = 44
    robot: bool = False
    # La mano no gira con el brazo: siempre apunta para el mismo lado.
    fixed_tip: bool = False
    # Golpea las teclas con un bate en vez de estirar la mano.
    bat: bool = False


DRAWN = [
    Drawn("michi", "Michi", "Gata con manchas naranjas y rayitas en la frente.", "Patas con almohadillas rosas.",
          michi_static, michi_face, (255, 250, 240), raised_cat, tip_cat, back=(38, 28, 48)),
    Drawn("mapache", "Mapache", "Con su antifaz negro y la cola anillada.", "Manitos negras y redondas, sin dedos.",
          mapache_static, mapache_face, (160, 162, 172), raised_raccoon, tip_raccoon, back=(44, 54, 44), shade=(92, 94, 106),
          root=(314, 252), mouse_arm=(96, 62), key_arm=(84, 52)),
    Drawn("robi", "Robi", "Robot de chapa, todo recto: la cara muestra tu voz en barras.", "Pinzas de metal y brazos telescópicos.",
          robi_static, robi_face, (206, 214, 228), raised_robot, tip_none, back=(14, 34, 42), shade=(120, 132, 156),
          root=(310, 306), root2=(702, 306), robot=True),
    Drawn("carpincho", "Carpincho", "Tranquilo, peludo y con una naranja en la cabeza.", "Patas gruesas con cuatro dedos y uñas.",
          carpincho_static, carpincho_face, (178, 126, 84), raised_capy, tip_capy, back=(40, 58, 42), shade=(92, 58, 36),
          root=(312, 250), mouse_arm=(120, 90), key_arm=(104, 74)),
    Drawn("fanti", "Fanti", "Fantasma de galera y moño.", "Sin dedos: la tela de la sábana.",
          fanti_static, fanti_face, (252, 250, 255), raised_ghost, tip_none, back=(40, 28, 70), shade=(176, 164, 226)),
    Drawn("mate", "Matecito", "Mate de madera con bombilla y pañuelo.", "Bracitos finos y guantes blancos de dibujo animado.",
          mate_static, mate_face, (150, 96, 56), raised_glove, tip_glove, back=(58, 44, 30), shade=(92, 54, 28),
          root=(286, 296), root2=(726, 296), mouse_arm=(24, 18), key_arm=(24, 18), tip_reach=60, fixed_tip=True),
    Drawn("pingui", "Pingüi", "Pingüino de gorro y bufanda: abre el pico al hablar.", "Aletas, sin dedos.",
          pingui_static, pingui_face, (58, 68, 100), raised_flipper, tip_flipper, back=(18, 40, 66), shade=(20, 26, 48),
          root=(312, 248), mouse_arm=(92, 50), key_arm=(82, 44), tip_reach=68),
    Drawn("tuntun", "Tun Tun Sahur", "Alto, de madera lustrada, de ojos enormes y sonrisa de lado a lado: marca el ritmo con su bate cuando hablas.", "Bracitos finos: golpea cada tecla con el bate.",
          tuntun_static, tuntun_face, WOOD, raised_none, tip_wood, back=(52, 36, 78), shade=WOOD_DEEP,
          root=(392, 296), root2=(620, 296), mouse_arm=(24, 18), key_arm=(24, 18), tip_reach=56, fixed_tip=True, bat=True),
]


# Cuánto se mueve el mouse sobre la alfombrilla: poco, como una mano de verdad.
MOUSE_TRAVEL = (110, 64)
# Después de la última tecla, la mano se queda un rato sobre el teclado antes de subir.
HAND_STAYS = 0.9
BAT_STAYS = 0.45
# Pasos en que se guardan las caras ya dibujadas: alcanzan para que no se noten los saltos.
LOOK_STEPS = 9
MOUTH_STEPS = 8
# La parte del dibujo donde está la cara de cualquier personaje.
FACE_BOX = (320, 60, 700, 322)


def _level(value, steps):
    return round(max(0.0, min(1.0, value)) * steps)


def _look_step(value):
    return round((max(-1.0, min(1.0, value)) + 1) / 2 * (LOOK_STEPS - 1))


class Scene(Stage):
    """Estilo dibujado, en el tamaño de salida."""

    def __init__(self, c: Drawn, size: tuple[int, int], background: str = "color") -> None:
        super().__init__(size, background)
        kb, key_sprites, pad, mouse = shared()
        self.c = c
        body = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        c.static(Ink(body))
        self.body_alpha = body.getchannel("A")
        self.body = self.scaled(body)
        paw = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        c.raised(Ink(paw), c)
        self.paw_up, self.paw_at = self.trimmed(paw)
        desk = self._desk(kb, pad)
        self.desk_alpha = desk.getchannel("A")
        self.desk = self.scaled(desk)
        self.keys = {name: self.trimmed(sprite, origin) for name, (sprite, origin) in key_sprites.items()}
        self.mouse = {click: self.scaled(sprite) for click, sprite in mouse.items()}
        self.backdrop = None
        if background == "color":
            top = tuple(min(255, int(v * 1.25)) for v in c.back)
            low = tuple(int(v * 0.55) for v in c.back)
            self.backdrop = old.gradient(size, top, low).convert("RGBA")
        elif background == "verde":
            self.backdrop = Image.new("RGBA", size, GREEN + (255,))
        self.bat = self.scaled(make_bat()) if c.bat else None
        self.reset()

    def reset(self):
        """El movimiento depende del cuadro anterior: acá vuelve a empezar."""
        self.last_t = None
        self.mouse_uv = None
        self.hand_uv = None
        self.reach = 0.0
        self.lift = 1.0
        self.last_key_t = -10.0
        self.bat_last_t = None
        self.bat_angle = BAT_REST_ANGLE
        self.bat_hand = BAT_REST_HAND
        self.bat_strike = None
        self.bat_key_t = -10.0

    def _advance(self, s, settled):
        """Las manos recorren el camino: nada salta de un lugar a otro."""
        mx = PW / 2 + (0.5 - s.mouse[0]) * MOUSE_TRAVEL[0]
        my = PH / 2 + (0.5 - s.mouse[1]) * MOUSE_TRAVEL[1]
        mouse_goal = map_flat((mx, my), (PW, PH), PAD_CORNERS)
        key_goal = None
        if s.keys:
            a, b, c2, _ = key_uv(s.keys[-1])
            key_goal = map_flat(((a + c2) / 2, b - 16), (BW, BH), KB_CORNERS)
            self.last_key_t = s.t
        if settled or self.last_t is None or s.t < self.last_t:
            self.mouse_uv = mouse_goal
            self.hand_uv = key_goal or self.hand_uv
            if settled:
                self.reach = 1.0 if key_goal else 0.0
                self.lift = 1.0 - self.reach
            self.last_t = s.t
            return
        dt = s.t - self.last_t
        self.last_t = s.t
        follow = 1 - math.exp(-dt / 0.07)
        self.mouse_uv = tuple(p + (g - p) * follow for p, g in zip(self.mouse_uv, mouse_goal))
        typing = s.t - self.last_key_t < HAND_STAYS
        if key_goal:
            if self.hand_uv is None or self.reach < 0.02:
                self.hand_uv = key_goal
            else:
                self.hand_uv = tuple(p + (g - p) * (1 - math.exp(-dt / 0.05)) for p, g in zip(self.hand_uv, key_goal))
        step = dt / 0.14
        self.reach = min(1.0, self.reach + step) if typing and self.lift <= 0.02 else max(0.0, self.reach - step)
        self.lift = max(0.0, self.lift - step) if typing else (min(1.0, self.lift + step) if self.reach <= 0.02 else self.lift)

    def _desk(self, kb, pad):
        desk = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        ink = Ink(desk)
        ink.fill([(-300, DESK_V), (1300, DESK_V), (1300, 900), (-300, 900)], PAPER)
        # La mesa se oscurece apenas hacia abajo, para que no sea un blanco plano.
        ramp = Image.linear_gradient("L").resize((W * S, H * S)).point(lambda p: int(p * 0.09))
        shade = Image.new("RGBA", desk.size, (120, 120, 134, 255))
        keep = desk.getchannel("A")
        desk.paste(shade, (0, 0), ImageChops.multiply(keep, ramp))
        desk.putalpha(keep)
        shadow = Image.new("L", desk.size, 0)
        ImageDraw.Draw(shadow).polygon([px((u + 6, v + 8)) for u, v in PAD_CORNERS], fill=120)
        soft = ImageChops.multiply(shadow.filter(ImageFilter.GaussianBlur(8 * S)), keep)
        desk.paste((40, 40, 56, 255), (0, 0), soft)
        desk.putalpha(keep)
        desk.alpha_composite(pad)
        desk.alpha_composite(kb)
        Ink(desk).stroke_path(spline([(-300, DESK_V), (200, DESK_V + 1), (700, DESK_V - 1), (1300, DESK_V)], closed=False, samples=40), 10, INK, 3, ends=False)
        return desk

    # Lo fijo: fondo, cuerpo, pata levantada y mesa

    def _make_base(self, phase, with_paw):
        image = self.backdrop.copy() if self.backdrop is not None else self.blank()
        self.put(image, self.body, (0, phase))
        if with_paw:
            self.put(image, self.paw_up, (self.paw_at[0], self.paw_at[1] + phase))
        self.put(image, self.desk, (0, 0))
        return image

    # Lo que cambia

    def _face(self, s):
        c = self.c
        look = (_look_step(s.look[0]), _look_step(s.look[1]))
        mouth = _level(s.mouth, MOUTH_STEPS)
        # La cara de Robi muestra la voz en barras que ondulan.
        wave = round(s.t * 12) % 48 if c.id == "robi" and mouth else 0
        key = ("cara", look, s.blink, mouth, wave)

        def make():
            corners = [px(p) for p in ((FACE_BOX[0], FACE_BOX[1]), (FACE_BOX[2], FACE_BOX[1]), (FACE_BOX[2], FACE_BOX[3]), (FACE_BOX[0], FACE_BOX[3]))]
            left, top = int(min(p[0] for p in corners)), int(min(p[1] for p in corners))
            right, bottom = int(max(p[0] for p in corners)) + 1, int(max(p[1] for p in corners)) + 1
            layer = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
            exact = old.State(
                look=(look[0] / (LOOK_STEPS - 1) * 2 - 1, look[1] / (LOOK_STEPS - 1) * 2 - 1),
                blink=s.blink,
                mouth=mouth / MOUTH_STEPS,
                t=wave / 12,
            )
            c.face(Ink(layer, (left, top)), exact)
            return self.trimmed(layer, (left, top), Image.BILINEAR)

        return self.pieces.get(key, make)

    def _arm_piece(self, root, tip, widths, join=0.34, bend=1, seed=3, with_hand=True):
        c = self.c
        root = (round(root[0]), round(root[1]))
        tip = (round(tip[0]), round(tip[1]))
        key = ("brazo", root, tip, widths, join, bend, seed, with_hand)

        def make():
            w0, w1 = widths
            # Casi recto: si se curva mucho, los bordes del brazo se cruzan y forman un gancho.
            dx, dy = tip[0] - root[0], tip[1] - root[1]
            size = math.hypot(dx, dy) or 1
            # Un brazo corto no puede ser tan ancho como uno largo: quedaría un muñón.
            narrow = min(w0, max(w1, size * 0.72))
            mid = ((root[0] + tip[0]) / 2 + dy / size * 14 * bend, (root[1] + tip[1]) / 2 - dx / size * 14 * bend)
            left, right, cap, t = arm_outline(root, mid, tip, narrow, w1)
            outline = left + cap + right[::-1]
            points = [px(p) for p in outline]
            # Con lugar para la mano y la sombra.
            margin = 84 * S
            x0, y0 = int(min(p[0] for p in points)) - margin, int(min(p[1] for p in points)) - margin
            x1, y1 = int(max(p[0] for p in points)) + margin, int(max(p[1] for p in points)) + margin
            layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
            ink = Ink(layer, (x0, y0))
            # La sombra del brazo cae sobre la mesa, no sobre el cuerpo.
            mask = Image.new("L", layer.size, 0)
            ImageDraw.Draw(mask).polygon([ink.p((u + 10, v + 14)) for u, v in right + left[::-1]], fill=70)
            soft = mask.reduce(4).filter(ImageFilter.BoxBlur(3)).resize(layer.size, Image.BILINEAR)
            layer.paste((50, 50, 66, 255), (0, 0), ImageChops.multiply(soft, self.desk_alpha.crop((x0, y0, x1, y1))))
            # El comienzo del brazo se funde con el cuerpo: así no se nota dónde empieza.
            shifted = [(a - x0, b - y0) for a, b in points]
            fx0, fy0 = max(int(min(p[0] for p in shifted)) - 2, 0), max(int(min(p[1] for p in shifted)) - 2, 0)
            fx1, fy1 = min(int(max(p[0] for p in shifted)) + 3, layer.width), min(int(max(p[1] for p in shifted)) + 3, layer.height)
            if fx1 > fx0 and fy1 > fy0:
                fill = Image.new("L", (fx1 - fx0, fy1 - fy0), 0)
                ImageDraw.Draw(fill).polygon([(a - fx0, b - fy0) for a, b in shifted], fill=255)
                rx, ry = ink.p(root)
                tx, ty = ink.p(tip)
                yy, xx = np.mgrid[fy0:fy1, fx0:fx1].astype(np.float32)
                along = ((xx - rx) * (tx - rx) + (yy - ry) * (ty - ry)) / max(1.0, (tx - rx) ** 2 + (ty - ry) ** 2)
                fade = np.clip(along / 0.14, 0, 1)
                # Solo se funde sobre el cuerpo: afuera del cuerpo el brazo es opaco y no deja ver el fondo.
                body = self.body_alpha.crop((x0 + fx0, y0 + fy0, x0 + fx1, y0 + fy1))
                outside = 1 - np.asarray(body, dtype=np.float32) / 255
                alpha = np.asarray(fill, dtype=np.float32) * np.clip(fade + outside, 0, 1)
                layer.paste(c.fill + (255,), (fx0, fy0), Image.fromarray(alpha.astype(np.uint8), "L"))
            n = len(left)
            begin = int(n * 0.1)
            start = int(n * join)
            ink.stroke_path(left[begin:] + cap + right[::-1][: n - start], 8 if w1 > 30 else 6, INK, seed, ends=True)
            if with_hand:
                c.tip(ink, Tip(t.last, 0.0, 1.0, t.w) if c.fixed_tip else t, c)
            return self.trimmed(layer, (x0, y0), Image.BILINEAR)

        return self.pieces.get(key, make)

    def _robot_arm_piece(self, root, tip):
        root = (round(root[0]), round(root[1]))
        tip = (round(tip[0]), round(tip[1]))

        def make():
            steel, dark = (206, 214, 228), (138, 150, 172)
            dx, dy = tip[0] - root[0], tip[1] - root[1]
            total = math.hypot(dx, dy) or 1
            ux, uy = dx / total, dy / total
            sleeve_end = (root[0] + ux * min(82, total * 0.62), root[1] + uy * min(82, total * 0.62))

            def bar(a, b, w):
                bx, by = b[0] - a[0], b[1] - a[1]
                length = math.hypot(bx, by) or 1
                nx, ny = -by / length * w / 2, bx / length * w / 2
                return [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]

            sleeve = bar(root, sleeve_end, 48)
            rod = bar(sleeve_end, tip, 26)
            points = [px(p) for p in sleeve + rod]
            margin = 90 * S
            x0, y0 = int(min(p[0] for p in points)) - margin, int(min(p[1] for p in points)) - margin
            x1, y1 = int(max(p[0] for p in points)) + margin, int(max(p[1] for p in points)) + margin
            layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
            ink = Ink(layer, (x0, y0))
            mask = Image.new("L", layer.size, 0)
            ImageDraw.Draw(mask).polygon([ink.p((u + 10, v + 14)) for u, v in sleeve + rod], fill=70)
            soft = mask.reduce(4).filter(ImageFilter.BoxBlur(3)).resize(layer.size, Image.BILINEAR)
            layer.paste((50, 50, 66, 255), (0, 0), ImageChops.multiply(soft, self.desk_alpha.crop((x0, y0, x1, y1))))
            ink.box(rod, steel, w=6, shade=dark)
            ink.box(sleeve, dark, w=6.5)
            for f in (0.45, 0.72):
                cx, cy = root[0] + (sleeve_end[0] - root[0]) * f, root[1] + (sleeve_end[1] - root[1]) * f
                ink.lines([(cx - uy * 22, cy + ux * 22), (cx + uy * 22, cy - ux * 22)], 3, (92, 104, 128))
            ink.box(bar((sleeve_end[0] - ux * 9, sleeve_end[1] - uy * 9), (sleeve_end[0] + ux * 5, sleeve_end[1] + uy * 5), 56), steel, w=5.5)
            ink.box([(root[0] - 28, root[1] - 28), (root[0] + 28, root[1] - 28), (root[0] + 28, root[1] + 28), (root[0] - 28, root[1] + 28)], steel, w=6.5, shade=dark)
            ink.fill([(root[0] - 9, root[1] - 9), (root[0] + 9, root[1] - 9), (root[0] + 9, root[1] + 9), (root[0] - 9, root[1] + 9)], (80, 236, 218))
            t = Tip(tip, 0.0, 1.0, 40)
            palm = [t.at(-10, -26), t.at(-10, 26), t.at(22, 26), t.at(22, -26)]
            ink.box(palm, steel, w=6, shade=dark)
            for s in (-18, 0, 18):
                ink.box([t.at(22, s - 7), t.at(50, s - 7), t.at(50, s + 7), t.at(22, s + 7)], steel, w=5, shade=dark)
            ink.fill([t.at(0, -9), t.at(14, -9), t.at(14, 9), t.at(0, 9)], (80, 236, 218))
            return self.trimmed(layer, (x0, y0), Image.BILINEAR)

        return self.pieces.get(("robot", root, tip), make)

    def _swing_bat(self, image, s, rise, settled):
        """El bate sube y baja girando, siempre por afuera: nunca le pasa por encima de la cara."""
        rest_angle = BAT_REST_ANGLE + abs(math.sin(s.t * 8)) * s.mouth * math.radians(16)
        hand, angle, tau, key, name = BAT_REST_HAND, rest_angle, 0.12, None, None
        if s.keys:
            name = s.keys[-1]
            a, b, c2, d = key_uv(name)
            key = map_flat(((a + c2) / 2, (b + d) / 2), (BW, BH), KB_CORNERS)
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
        root = (self.c.root2[0], self.c.root2[1] + rise)
        sprite, position = self._arm_piece(root, self.bat_hand, self.c.key_arm, join=0.38, bend=-1, seed=6, with_hand=False)
        self.put(image, sprite, position)
        if key and abs(self.bat_angle - angle) < math.radians(8):
            # Las rayitas del golpe.
            self.put(image, *self._rays(name, key, angle))
        degrees = round(math.degrees(self.bat_angle) / 2) * 2

        def turned():
            rotated = self.bat.rotate(-degrees, resample=Image.BILINEAR)
            box = rotated.getbbox()
            return rotated.crop(box), box[:2]

        sprite, offset = self.pieces.get(("bate", degrees), turned)
        hx, hy = self.at(*px(self.bat_hand))
        self.put(image, sprite, (hx - self.bat.width // 2 + offset[0], hy - self.bat.height // 2 + offset[1]))

    def _rays(self, name, key, angle):
        def make():
            x, y = px(key)
            margin = 60 * S
            x0, y0 = int(x) - margin, int(y) - margin
            layer = Image.new("RGBA", (2 * margin, 2 * margin), (0, 0, 0, 0))
            ink = Ink(layer, (x0, y0))
            for k in range(7):
                ray = angle - math.radians(120) + k * math.radians(40)
                ink.stroke_path([(key[0] + math.cos(ray) * 26, key[1] + math.sin(ray) * 20), (key[0] + math.cos(ray) * 46, key[1] + math.sin(ray) * 34)], 4.5, INK, k + 1, ends=False)
            return self.trimmed(layer, (x0, y0), Image.BILINEAR)

        return self.pieces.get(("rayas", name), make)

    def frame(self, s, settled=False) -> Image.Image:
        """El cuadro armado. «settled»: sin arrastre del cuadro anterior."""
        c = self.c
        self._advance(s, settled)
        # Un solo número para la respiración de todo: si no, cada pieza guardada se rehace en cada cuadro.
        phase = round(math.sin(s.t * 1.8) * 1.6 * S * self.fy)
        rise = phase / (S * self.fy)
        resting = self.lift >= 0.98 and not c.bat
        image = self.base(("base", phase, resting), lambda: self._make_base(phase, resting)).copy()
        if not resting and self.lift > 0.02 and not c.bat:
            # La pata sube y baja por detrás de la mesa.
            drop = round((1 - self.lift) * 128 * S * self.fy) + phase
            self.put(image, self.paw_up, (self.paw_at[0], self.paw_at[1] + drop))
            box = (self.paw_at[0], self.paw_at[1] + drop, self.paw_at[0] + self.paw_up.width, self.paw_at[1] + drop + self.paw_up.height)
            self.put(image, self.desk.crop(box), box[:2])
        self.put(image, *self._face(s))
        for name in s.keys:
            self.put(image, *self.keys[name])
        mu, mv = self.mouse_uv
        sx, sy = self.at(*px((mu, mv)))
        sprite = self.mouse[s.click]
        self.put(image, sprite, (sx - sprite.width // 2, sy - sprite.height // 2))
        root = (c.root[0], c.root[1] + rise)
        if c.robot:
            self.put(image, *self._robot_arm_piece(root, (mu + 2, mv - 72)))
        else:
            self.put(image, *self._arm_piece(root, (mu + 2, mv - c.tip_reach), c.mouse_arm, join=0.42, bend=1, seed=3))
        if c.bat:
            self._swing_bat(image, s, rise, settled)
        elif self.reach > 0.02 and self.hand_uv:
            # El brazo sale del borde de la mesa y se estira hasta la tecla.
            start = (c.root2[0], DESK_V + 14)
            t = self.reach * self.reach * (3 - 2 * self.reach)
            ku, kv = (start[0] + (self.hand_uv[0] - start[0]) * t, start[1] + (self.hand_uv[1] - start[1]) * t)
            root2 = (c.root2[0], c.root2[1] + rise)
            if c.robot:
                self.put(image, *self._robot_arm_piece(root2, (ku, kv - 30)))
            else:
                self.put(image, *self._arm_piece(root2, (ku, kv - max(8, c.tip_reach - 36)), c.key_arm, join=0.38, bend=-1, seed=6))
        return image
