"""Personajes de CelCam con volumen: formas orgánicas pintadas con luz, como muñecos de felpa o arcilla.

Cada personaje tiene cuerpo y cabeza fijos, y ojos y boca que cambian. Todo es dibujo original, hecho con código.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

W, H = 960, 540
S = 2
C = 480
DESK_Y = 356
WHITE = (255, 255, 255)
ACCENT = (45, 212, 191)

LIGHT = np.array([-0.40, -0.62, 0.67])
LIGHT = LIGHT / np.linalg.norm(LIGHT)
HALF = LIGHT + np.array([0, 0, 1.0])
HALF = HALF / np.linalg.norm(HALF)


# Formas


def spline(points, closed=True, samples=14):
    pts = list(points)
    n = len(pts)
    out = []
    count = n if closed else n - 1
    for i in range(count):
        if closed:
            p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        else:
            p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, n - 1)]
        for j in range(samples):
            t = j / samples
            t2, t3 = t * t, t * t * t
            out.append(
                tuple(
                    0.5 * (2 * p1[k] + (p2[k] - p0[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                           + (3 * p1[k] - p0[k] - 3 * p2[k] + p3[k]) * t3)
                    for k in (0, 1)
                )
            )
    if not closed:
        out.append(tuple(pts[-1]))
    return out


def sym(half):
    """Medio contorno, de arriba al centro hasta abajo al centro: lo espeja y lo cierra."""
    right = [(C + dx, y) for dx, y in half]
    left = [(C - dx, y) for dx, y in half[-2:0:-1]]
    return spline(right + left)


def side(points, sign):
    return [(C + sign * dx, y) for dx, y in points]


def blob_of(points):
    return spline(points)


def oval(cx, cy, rx, ry, wobble=0.0, seed=1, steps=10):
    rng = np.random.default_rng(seed)
    return spline(
        [
            (cx + rx * (1 + rng.uniform(-wobble, wobble)) * math.cos(a), cy + ry * (1 + rng.uniform(-wobble, wobble)) * math.sin(a))
            for a in np.linspace(0, math.tau, steps, endpoint=False)
        ]
    )


def taper(points, w0, w1, closed_end=True, samples=10):
    """Un trazo que cambia de ancho, como una pincelada."""
    path = spline(points, closed=False, samples=samples)
    n = len(path)
    left, right = [], []
    for i, (x, y) in enumerate(path):
        a = path[max(i - 1, 0)]
        b = path[min(i + 1, n - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        size = math.hypot(dx, dy) or 1
        t = i / (n - 1)
        w = (w0 + (w1 - w0) * t) / 2
        left.append((x - dy / size * w, y + dx / size * w))
        right.append((x + dy / size * w, y - dx / size * w))
    return left + right[::-1]


def polygon_mask(size, polys, scale=S, origin=(0, 0)) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for poly in polys:
        draw.polygon([((x - origin[0]) * scale, (y - origin[1]) * scale) for x, y in poly], fill=255)
    return mask


# Desenfoque en números decimales: el de 8 bits deja escalones que se verían como bandas en la luz.


def _box(a, r, axis):
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
    if axis == 0:
        return (c[2 * r + 1 :, :] - c[: -(2 * r + 1), :]) / (2 * r + 1)
    return (c[:, 2 * r + 1 :] - c[:, : -(2 * r + 1)]) / (2 * r + 1)


def soften(a, sigma):
    r = max(1, int(round(sigma)))
    for _ in range(3):
        a = _box(a, r, 0)
        a = _box(a, r, 1)
    return a


# Texturas: cambian cómo le da la luz a la superficie.


def _noise(shape, cell_y, cell_x, seed):
    rng = np.random.default_rng(seed)
    small = rng.standard_normal((max(2, shape[0] // cell_y), max(2, shape[1] // cell_x))).astype(np.float32)
    return np.asarray(Image.fromarray(small, "F").resize((shape[1], shape[0]), Image.BICUBIC))


def felt(shape, seed=1):
    return _noise(shape, 3, 3, seed) * 0.7 + _noise(shape, 8, 8, seed + 1) * 0.5


def fur(shape, seed=1):
    return _noise(shape, 14, 3, seed) * 1.0 + _noise(shape, 3, 3, seed + 1) * 0.35 + _noise(shape, 10, 10, seed + 2) * 0.4


def coarse_fur(shape, seed=1):
    return _noise(shape, 11, 4, seed) * 1.5 + _noise(shape, 26, 7, seed + 1) * 0.9


def knit(shape, seed=1):
    y, x = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float32)
    return np.sin(x / 2.6) * 1.3 + np.sin(y / 3.0) * 0.7


def wood(shape, seed=1):
    y, x = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float32)
    warp = _noise(shape, 60, 24, seed) * 16
    return np.sin((x + warp) / 5.0) * 0.9 + _noise(shape, 18, 3, seed + 1) * 0.5


def cloth(shape, seed=1):
    return _noise(shape, 4, 4, seed) * 0.35


# Pintar con luz


def plush(layer, polys, color, bottom=None, bevel=14, dome=0.55, amb=0.5, kd=0.72, spec=0.1, shine=26, rim=None,
          tex=None, tex_power=0.5, cast=None, alpha=1.0, scale=S, seed=3):
    """Infla la forma como un almohadón y la ilumina. Devuelve la máscara, para pintar encima."""
    size = layer.size
    mask = polygon_mask(size, polys, scale)
    box = mask.getbbox()
    if not box:
        return mask
    margin = int((bevel * 3 + 8) * scale)
    box = (max(box[0] - margin, 0), max(box[1] - margin, 0), min(box[2] + margin, size[0]), min(box[3] + margin, size[1]))
    m = np.asarray(mask.crop(box), dtype=np.float32) / 255
    near = soften(m, bevel * scale * 0.9)
    far = soften(m, bevel * scale * 2.6)
    height = near * (1 - dome) + far * dome
    gy, gx = np.gradient(height)
    k = bevel * scale * 2.3
    nx, ny = -gx * k, -gy * k
    if tex is not None:
        ty, tx = np.gradient(tex(m.shape, seed))
        nx = nx - tx * tex_power
        ny = ny - ty * tex_power
    length = np.sqrt(nx * nx + ny * ny + 1)
    nx, ny, nz = nx / length, ny / length, 1 / length
    diffuse = np.clip(nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2], 0, 1)
    sky = np.clip(0.5 - 0.5 * ny, 0, 1)
    shade = amb + kd * diffuse * (0.55 + 0.45 * sky)
    ys = np.nonzero(m.sum(axis=1))[0]
    y0, y1 = ys[0], ys[-1]
    t = np.clip((np.arange(m.shape[0]) - y0) / max(1, y1 - y0), 0, 1)[:, None, None]
    top = np.array(color, dtype=np.float32)
    low = np.array(bottom or color, dtype=np.float32)
    rgb = (top * (1 - t) + low * t) * shade[..., None]
    if spec:
        gloss = np.clip(nx * HALF[0] + ny * HALF[1] + nz * HALF[2], 0, 1) ** shine * spec * 255
        rgb = rgb + gloss[..., None]
    if rim:
        rim_color, rim_power = rim
        rgb = rgb + ((1 - nz) ** 2.2 * rim_power)[..., None] * np.array(rim_color, dtype=np.float32)
    if cast:
        dx, dy, blur, strength = cast
        moved = np.roll(np.roll(m, int(dy * scale), axis=0), int(dx * scale), axis=1)
        shadow = soften(moved, blur * scale) * strength
        below = np.asarray(layer.crop(box), dtype=np.float32).copy()
        below[..., :3] = below[..., :3] * (1 - shadow[..., None] * (1 - m[..., None]))
        layer.paste(Image.fromarray(below.astype(np.uint8), "RGBA"), box[:2])
    rgba = np.dstack([np.clip(rgb, 0, 255), m * 255 * alpha]).astype(np.uint8)
    layer.alpha_composite(Image.fromarray(rgba, "RGBA"), box[:2])
    return mask


def airbrush(layer, polys, color, alpha=0.5, blur=6, clip_to=None, scale=S) -> None:
    """Pinta suave sobre lo que ya hay, sin cambiar la transparencia."""
    mask = polygon_mask(layer.size, polys, scale)
    box = mask.getbbox()
    if not box:
        return
    margin = int((blur * 3 + 4) * scale)
    box = (max(box[0] - margin, 0), max(box[1] - margin, 0), min(box[2] + margin, layer.size[0]), min(box[3] + margin, layer.size[1]))
    a = soften(np.asarray(mask.crop(box), dtype=np.float32) / 255, blur * scale) * alpha
    if clip_to is not None:
        a = a * np.asarray(clip_to.crop(box), dtype=np.float32) / 255
    region = np.asarray(layer.crop(box), dtype=np.float32).copy()
    a = a[..., None] * (region[..., 3:4] / 255)
    region[..., :3] = region[..., :3] * (1 - a) + np.array(color, dtype=np.float32) * a
    layer.paste(Image.fromarray(region.astype(np.uint8), "RGBA"), box[:2])


def under_glow(layer, polys, color, alpha=0.6, blur=20, scale=S) -> None:
    """Un resplandor detrás de lo que se dibuje después."""
    mask = polygon_mask(layer.size, polys, scale)
    a = soften(np.asarray(mask, dtype=np.float32) / 255, blur * scale) * alpha
    glow = np.zeros((*a.shape, 4), dtype=np.uint8)
    glow[..., :3] = color
    glow[..., 3] = (a * 255).astype(np.uint8)
    layer.alpha_composite(Image.fromarray(glow, "RGBA"))


def desk_shadow(layer, strength=0.55, height=46) -> None:
    """El cuerpo queda en sombra donde se acerca a la mesa."""
    h = int(height * S)
    y1 = int((DESK_Y + 6) * S)
    region = np.asarray(layer.crop((0, y1 - h, layer.size[0], y1)), dtype=np.float32).copy()
    ramp = np.linspace(0, strength, h, dtype=np.float32)[:, None, None]
    region[..., :3] = region[..., :3] * (1 - ramp)
    layer.paste(Image.fromarray(region.astype(np.uint8), "RGBA"), (0, y1 - h))


# Ojos y bocas


@dataclass(frozen=True)
class Eye:
    cx: float
    cy: float
    rx: float
    ry: float
    iris: tuple[int, int, int]
    lid: tuple[int, int, int]
    line: tuple[int, int, int] = (40, 26, 26)
    pupil: float = 0.5
    slit: bool = False
    tilt: float = 0.0
    drop: float = 0.0
    black: bool = False
    sclera: tuple[int, int, int] = (252, 250, 248)
    iris_size: float = 1.0


def _smooth(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0, 1)
    return t * t * (3 - 2 * t)


def make_eye(e: Eye, look, blink: bool):
    """Un ojo terminado: esclerótica, iris con profundidad, brillos y párpado."""
    margin = 10
    x0, y0 = e.cx - e.rx - margin, e.cy - e.ry - margin
    w, h = int((2 * e.rx + 2 * margin) * S), int((2 * e.ry + 2 * margin) * S)
    outline = spline(
        [
            (e.cx + e.rx * math.cos(a), e.cy + e.ry * math.sin(a) + e.tilt * e.rx * math.cos(a) * 0.22)
            for a in np.linspace(0, math.tau, 12, endpoint=False)
        ]
    )
    mask = polygon_mask((w, h), [outline], S, (x0, y0))
    m = np.asarray(mask, dtype=np.float32) / 255
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    x = x / S + x0
    y = y / S + y0
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    if blink:
        rgb[...] = np.array(e.lid, dtype=np.float32) * (0.82 + 0.28 * (1 - (y - y0) / (h / S))[..., None])
    else:
        vertical = (y - (e.cy - e.ry)) / (2 * e.ry)
        sclera = np.array(e.sclera, dtype=np.float32)
        rgb[...] = sclera * (0.62 + 0.38 * _smooth(0.0, 0.42, vertical))[..., None]
        px = e.cx + look[0] * e.rx * 0.30
        py = e.cy + look[1] * e.ry * 0.26
        ir_x, ir_y = e.rx * 0.74 * e.iris_size, e.ry * 0.80 * e.iris_size
        d = np.sqrt(((x - px) / ir_x) ** 2 + ((y - py) / ir_y) ** 2)
        iris = np.array(e.iris, dtype=np.float32)
        rays = 0.92 + 0.08 * np.sin(np.arctan2(y - py, x - px) * 34)
        lower = _smooth(-0.1, 0.8, (y - py) / ir_y)
        body = iris * (0.42 + 0.95 * lower * (1 - d * 0.45))[..., None] * rays[..., None]
        ring = _smooth(0.74, 1.0, d)[..., None]
        body = body * (1 - ring * 0.82)
        inside = (1 - _smooth(0.97, 1.03, d))[..., None]
        if e.black:
            body = np.array((26, 18, 44), dtype=np.float32) * (0.7 + 0.8 * lower)[..., None]
        rgb = rgb * (1 - inside) + body * inside
        if e.slit:
            p = np.sqrt(((x - px) / (ir_x * 0.26)) ** 2 + ((y - py) / (ir_y * 0.82)) ** 2)
        else:
            p = d / e.pupil
        rgb = rgb * _smooth(0.86, 1.04, p)[..., None] + np.array((10, 10, 16), dtype=np.float32) * (1 - _smooth(0.86, 1.04, p))[..., None]
        top_shadow = 0.55 + 0.45 * _smooth(0.0, 0.34, vertical)
        rgb = rgb * top_shadow[..., None]
        for hx, hy, hr, power in ((-0.34, -0.40, 0.25, 1.0), (0.34, 0.34, 0.11, 0.8)):
            cx, cy = px + hx * ir_x, py + hy * ir_y
            spot = 1 - _smooth(0.72, 1.12, np.sqrt(((x - cx) / (hr * ir_x)) ** 2 + ((y - cy) / (hr * ir_y)) ** 2))
            rgb = rgb * (1 - spot[..., None] * power) + 255 * spot[..., None] * power
    image = Image.fromarray(np.dstack([np.clip(rgb, 0, 255), m * 255]).astype(np.uint8), "RGBA")
    extra = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if e.drop and not blink:
        cover = [p for p in outline]
        lid_mask = polygon_mask((w, h), [cover], S, (x0, y0))
        band = Image.new("L", (w, h), 0)
        ImageDraw.Draw(band).rectangle([0, 0, w, int((e.ry * 2 * e.drop + margin) * S)], fill=255)
        lid_alpha = np.asarray(lid_mask, dtype=np.float32) * np.asarray(band, dtype=np.float32) / 255
        lid_rgb = np.array(e.lid, dtype=np.float32) * np.ones((h, w, 3), dtype=np.float32) * 0.95
        extra.alpha_composite(Image.fromarray(np.dstack([lid_rgb, lid_alpha]).astype(np.uint8), "RGBA"))
        ImageDraw.Draw(extra).polygon(
            [((px_ - x0) * S, (py_ - y0) * S) for px_, py_ in taper(
                [(e.cx - e.rx, e.cy - e.ry + e.ry * 2 * e.drop + 1), (e.cx, e.cy - e.ry + e.ry * 2 * e.drop + 3), (e.cx + e.rx, e.cy - e.ry + e.ry * 2 * e.drop + 1)], 4, 4)],
            fill=e.line + (255,),
        )
    draw = ImageDraw.Draw(extra)
    arc = [
        (e.cx + e.rx * 1.02 * math.cos(a), (e.cy + e.ry * 1.02 * math.sin(a)) + e.tilt * e.rx * math.cos(a) * 0.22)
        for a in np.linspace(math.pi * 1.03, math.pi * 1.97, 9)
    ]
    if blink:
        arc = [(e.cx - e.rx * 1.02, e.cy + 2), (e.cx - e.rx * 0.45, e.cy + e.ry * 0.34), (e.cx + e.rx * 0.45, e.cy + e.ry * 0.34), (e.cx + e.rx * 1.02, e.cy + 2)]
    stroke = taper(arc, 3.5, 6 if e.tilt >= 0 else 4)
    draw.polygon([((px_ - x0) * S, (py_ - y0) * S) for px_, py_ in stroke], fill=e.line + (255,))
    image.alpha_composite(extra)
    return image, (int(x0 * S), int(y0 * S))


def mouth_shape(cx, cy, w, h, smile=0.25):
    return sym_at(cx, [(0, cy + smile * 3), (w * 0.5, cy - h * 0.04), (w, cy + h * 0.05), (w * 0.82, cy + h * 0.52), (w * 0.42, cy + h * 0.92), (0, cy + h)])


def sym_at(cx, half):
    right = [(cx + dx, y) for dx, y in half]
    left = [(cx - dx, y) for dx, y in half[-2:0:-1]]
    return spline(right + left)


def make_mouth(cx, cy, w, h, inner=(92, 22, 40), tongue=(238, 112, 132), lip=(70, 24, 34), teeth=False):
    """Boca abierta con profundidad: oscura atrás, lengua abajo y borde suave."""
    margin = 8
    x0, y0 = cx - w - margin, cy - margin
    size = (int((2 * w + 2 * margin) * S), int((h + 2 * margin) * S))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    shape = [(x - x0, y - y0) for x, y in mouth_shape(cx, cy, w, h)]
    mask = polygon_mask(size, [shape], S)
    yy = np.linspace(0, 1, size[1], dtype=np.float32)[:, None, None]
    base = np.array(inner, dtype=np.float32) * (0.45 + 0.75 * yy) * np.ones((size[1], size[0], 3), dtype=np.float32)
    m = np.asarray(mask, dtype=np.float32) / 255
    layer = Image.fromarray(np.dstack([base, m * 255]).astype(np.uint8), "RGBA")
    if h > 9:
        t_w, t_h = w * 0.62, h * 0.42
        tcx, tcy = w + margin, margin + h - t_h * 0.7
        tongue_layer = Image.new("RGBA", size, (0, 0, 0, 0))
        plush(tongue_layer, [oval(tcx, tcy, t_w, t_h)], tongue, bevel=max(2, t_h * 0.35), amb=0.62, kd=0.55, spec=0.2, shine=20)
        tm = np.asarray(tongue_layer.getchannel("A"), dtype=np.float32) * m
        tongue_layer.putalpha(Image.fromarray(tm.astype(np.uint8), "L"))
        layer.alpha_composite(tongue_layer)
    if teeth and h > 7:
        draw = ImageDraw.Draw(layer)
        for sign in (-1, 1):
            tx = w + margin + sign * w * 0.14
            draw.rounded_rectangle(
                [((tx - w * 0.13) * S, (margin + 1) * S), ((tx + w * 0.13) * S, (margin + 1 + min(h * 0.32, 9)) * S)], radius=2 * S, fill=(252, 250, 244, 255)
            )
    edge = polygon_mask(size, [shape], S)
    rim = soften(np.asarray(edge, dtype=np.float32) / 255, 1.6 * S)
    ring = np.clip((1 - np.abs(rim - 0.5) * 3.2), 0, 1) * 0.95
    border = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    border[..., :3] = lip
    border[..., 3] = (ring * 255).astype(np.uint8)
    layer.alpha_composite(Image.fromarray(border, "RGBA"))
    return layer, (int(x0 * S), int(y0 * S))


def make_grin(cx, cy, w, h, inner=(58, 18, 14), tongue=(232, 112, 122), lip=(56, 24, 8), teeth=(252, 250, 244)):
    """Una sonrisa de lado a lado con los dientes a la vista; al hablar se abre hacia abajo."""
    margin = 8
    x0, y0 = cx - w - margin, cy - 8 - margin
    size = (int((2 * w + 2 * margin) * S), int((h + 18 + 2 * margin) * S))
    ts = np.linspace(-1, 1, 31)

    def upper(t):
        return cy + 10 * (1 - t * t) - 8 * t * t

    top = [(cx + w * t - x0, upper(t) - y0) for t in ts]
    low = [(cx + w * t - x0, upper(t) + h * max(0.0, 1 - t * t) ** 0.7 - y0) for t in ts[::-1]]
    mask = polygon_mask(size, [top + low], S)
    m = np.asarray(mask, dtype=np.float32) / 255
    yy = np.linspace(0, 1, size[1], dtype=np.float32)[:, None, None]
    base = np.array(inner, dtype=np.float32) * (0.6 + 0.7 * yy) * np.ones((size[1], size[0], 3), dtype=np.float32)
    layer = Image.fromarray(np.dstack([base, m * 255]).astype(np.uint8), "RGBA")
    if h > 20:
        extra = Image.new("RGBA", size, (0, 0, 0, 0))
        plush(extra, [oval(w + margin, h + 18 + margin - 8, w * 0.42, min(13, h * 0.3))], tongue, bevel=4, amb=0.62, kd=0.55, spec=0.2, shine=20)
        extra.putalpha(Image.fromarray((np.asarray(extra.getchannel("A"), dtype=np.float32) * m).astype(np.uint8), "L"))
        layer.alpha_composite(extra)
    # Los dientes: una tira blanca con sus divisiones, recortada por la boca.
    band = [(cx + w * t - x0, upper(t) + min(15, h) * max(0.0, 1 - t * t) ** 0.5 - y0) for t in ts[::-1]]
    extra = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(extra)
    draw.polygon([(x * S, y * S) for x, y in top + band], fill=teeth + (255,))
    for t in np.linspace(-0.72, 0.72, 7):
        x = cx + w * t - x0
        draw.line([(x * S, (upper(t) - y0) * S), (x * S, (upper(t) + 15 - y0) * S)], fill=(196, 186, 172, 255), width=S)
    extra.putalpha(Image.fromarray((np.asarray(extra.getchannel("A"), dtype=np.float32) * m).astype(np.uint8), "L"))
    layer.alpha_composite(extra)
    rim = soften(m, 1.4 * S)
    ring = np.clip(1 - np.abs(rim - 0.5) * 3.2, 0, 1) * 0.95
    border = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    border[..., :3] = lip
    border[..., 3] = (ring * 255).astype(np.uint8)
    layer.alpha_composite(Image.fromarray(border, "RGBA"))
    return layer, (int(x0 * S), int(y0 * S))


# Michi


def michi_body(body):
    plush(body, [sym([(0, 250), (104, 248), (142, 286), (108, 330), (0, 340)])], (36, 136, 130), bevel=16, tex=felt)
    torso = sym([(0, 282), (66, 280), (128, 296), (168, 340), (190, 420), (0, 446)])
    m = plush(body, [torso], (72, 196, 180), bottom=(30, 122, 122), bevel=22, dome=0.6, tex=felt, tex_power=0.55, rim=((180, 255, 240), 0.32))
    for points in (
        [(C - 122, 318), (C - 92, 336), (C - 70, 372)],
        [(C + 122, 318), (C + 92, 336), (C + 70, 372)],
        [(C - 52, 322), (C - 40, 346), (C - 44, 376)],
        [(C + 52, 322), (C + 40, 346), (C + 44, 376)],
    ):
        airbrush(body, [taper(points, 12, 3)], (12, 72, 76), 0.5, 5, clip_to=m)
    plush(body, [sym([(0, 268), (74, 262), (98, 288), (42, 316), (0, 322)])], (46, 158, 150), bevel=10, tex=felt, cast=(0, 6, 7, 0.4))
    plush(body, [taper([(C, 318), (C, 356)], 5, 5)], (216, 224, 232), bevel=2, spec=0.5, shine=16, amb=0.7)
    for sign in (-1, 1):
        cord = [(C + sign * 24, 300), (C + sign * 30, 324), (C + sign * 22, 352)]
        plush(body, [taper(cord, 4.2, 3.4)], (246, 248, 250), bevel=2, amb=0.75, kd=0.5)
        plush(body, [oval(C + sign * 22, 354, 4.2, 7)], (220, 90, 110), bevel=2, spec=0.4)
    desk_shadow(body)


def michi_head(head):
    orange, deep, cream = (255, 172, 84), (222, 116, 42), (255, 244, 228)
    for sign in (-1, 1):
        ear = side([(34, 96), (62, 46), (102, 12), (118, 12), (128, 62), (136, 130), (86, 118)], sign)
        plush(head, [spline(ear)], orange, deep, bevel=9, tex=fur, tex_power=0.45, rim=((255, 220, 170), 0.3))
        inner = side([(62, 92), (80, 56), (104, 32), (112, 36), (116, 74), (120, 112), (88, 106)], sign)
        im = plush(head, [spline(inner)], (248, 168, 170), (206, 104, 124), bevel=9, dome=0.2, amb=0.5, kd=0.42)
        for k, (a, b) in enumerate((((76, 108), (98, 62)), ((92, 112), (108, 68)), ((104, 116), (114, 80)))):
            airbrush(head, [taper(side([(a[0], a[1]), ((a[0] + b[0]) / 2 + 2, (a[1] + b[1]) / 2), (b[0], b[1])], sign), 7, 1.5)], cream, 0.85, 1.2, clip_to=im)
    face = sym(
        [(0, 58), (56, 60), (100, 84), (128, 124), (138, 170), (140, 196), (156, 214), (128, 220), (146, 244), (108, 244), (112, 266), (66, 266), (0, 284)]
    )
    m = plush(head, [face], (255, 186, 96), (232, 130, 54), bevel=20, dome=0.62, amb=0.52, kd=0.68, spec=0.05, tex=fur, tex_power=0.6,
              rim=((255, 226, 170), 0.35), cast=(0, 12, 12, 0.45), seed=4)
    for points, w0 in (
        ([(C, 62), (C - 1, 84), (C, 112)], 11),
        ([(C - 28, 66), (C - 30, 84), (C - 22, 104)], 9),
        ([(C + 28, 66), (C + 30, 84), (C + 22, 104)], 9),
        ([(C - 58, 76), (C - 56, 90), (C - 44, 102)], 7),
        ([(C + 58, 76), (C + 56, 90), (C + 44, 102)], 7),
    ):
        airbrush(head, [taper(points, w0, 2)], deep, 0.82, 2.2, clip_to=m)
    for sign in (-1, 1):
        for y, length in ((170, 40), (192, 44), (214, 34)):
            points = [(C + sign * 136, y + 2), (C + sign * (136 - length * 0.5), y), (C + sign * (136 - length), y + 5)]
            airbrush(head, [taper(points, 9, 2)], deep, 0.78, 2.2, clip_to=m)
        airbrush(head, [oval(C + sign * 56, 192, 44, 36)], (255, 228, 190), 0.5, 12, clip_to=m)
    muzzle = sym([(0, 190), (34, 188), (62, 206), (66, 234), (42, 258), (0, 270)])
    mm = plush(head, [muzzle], cream, (250, 226, 202), bevel=13, dome=0.7, amb=0.6, kd=0.56, spec=0.04, tex=felt, tex_power=0.3, cast=(0, 5, 8, 0.28))
    for sign in (-1, 1):
        for dx, dy in ((26, 214), (40, 222), (30, 230)):
            airbrush(head, [oval(C + sign * dx, dy, 1.6, 1.6)], (150, 120, 110), 0.8, 0.5)
    airbrush(head, [taper([(C, 208), (C, 224)], 2.8, 2.2)], (112, 70, 60), 0.9, 0.6, clip_to=mm)
    plush(head, [sym_at(C, [(0, 192), (14, 192), (15, 198), (5, 209), (0, 211)])], (246, 140, 156), (214, 92, 122), bevel=3.2, amb=0.62, kd=0.55, spec=0.5, shine=22)
    for sign in (-1, 1):
        for y0, y1 in ((212, 196), (222, 222), (232, 246)):
            line = [(C + sign * 54, y0), (C + sign * 100, (y0 + y1) / 2 - 2), (C + sign * 152, y1)]
            poly = taper(line, 2.4, 0.8)
            plush(head, [poly], (250, 246, 240), bevel=0.8, amb=0.86, kd=0.2)
        airbrush(head, [oval(C + sign * 92, 212, 22, 13)], (255, 120, 120), 0.32, 8, clip_to=m)
        airbrush(head, [taper([(C + sign * 78, 108), (C + sign * 50, 98), (C + sign * 24, 108)], 3, 6)], (146, 72, 24), 0.85, 1.2, clip_to=m)
    _headset(head)


def _headset(head):
    band = taper([(C - 142, 150), (C - 122, 88), (C - 60, 44), (C, 32), (C + 60, 44), (C + 122, 88), (C + 142, 150)], 13, 13, samples=12)
    plush(head, [band], (92, 98, 118), (52, 56, 72), bevel=4, dome=0.3, amb=0.5, kd=0.7, spec=0.6, shine=40, cast=(0, 5, 6, 0.4))
    pad = taper([(C - 66, 44), (C, 32), (C + 66, 44)], 16, 16)
    plush(head, [pad], (56, 60, 74), (40, 44, 58), bevel=5, tex=felt, tex_power=0.4)
    for sign in (-1, 1):
        cup = oval(C + sign * 142, 178, 27, 48)
        plush(head, [cup], (86, 92, 112), (40, 44, 56), bevel=11, dome=0.45, spec=0.55, shine=34, cast=(sign * -3, 4, 6, 0.4))
        plush(head, [oval(C + sign * 141, 178, 15, 30)], (28, 32, 42), bevel=4, amb=0.6, spec=0.3)
        plush(head, [oval(C + sign * 141, 178, 8, 20)], (90, 240, 220), (40, 200, 180), bevel=3, amb=0.95, kd=0.25, spec=0.6, shine=18)
    boom = taper([(C - 136, 218), (C - 118, 248), (C - 84, 262), (C - 58, 262)], 6, 4.5)
    plush(head, [boom], (84, 90, 110), (46, 50, 64), bevel=2.2, spec=0.5, shine=28, cast=(0, 3, 3, 0.4))
    plush(head, [oval(C - 52, 262, 13, 10)], (58, 62, 78), (28, 30, 40), bevel=5, spec=0.3, tex=felt, tex_power=0.4)


MICHI_EYES = (
    Eye(C - 56, 154, 31, 35, (82, 188, 116), (246, 162, 76), line=(64, 36, 20), pupil=0.45, slit=False, tilt=-0.6),
    Eye(C + 56, 154, 31, 35, (82, 188, 116), (246, 162, 76), line=(64, 36, 20), pupil=0.45, slit=False, tilt=0.6),
)


# Robi


def box_poly(x0, y0, x1, y1, cut=0):
    """Rectángulo con las esquinas cortadas: todo el robot sale de esto."""
    if not cut:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [(x0 + cut, y0), (x1 - cut, y0), (x1, y0 + cut), (x1, y1 - cut), (x1 - cut, y1), (x0 + cut, y1), (x0, y1 - cut), (x0, y0 + cut)]


def _bolt(layer, x, y):
    plush(layer, [box_poly(x - 5, y - 5, x + 5, y + 5, 2)], (150, 162, 182), (84, 96, 118), bevel=2, spec=0.8, shine=30)


def robi_body(body):
    plush(body, [box_poly(C - 40, 244, C + 40, 292)], (92, 104, 124), (58, 68, 88), bevel=4, dome=0.2, spec=0.6, shine=50)
    for y in (254, 268, 282):
        airbrush(body, [box_poly(C - 40, y, C + 40, y + 2.5)], (28, 34, 46), 0.9, 0.4)
    plush(body, [box_poly(C - 158, 286, C + 158, 450, 34)], (214, 222, 236), (116, 132, 158), bevel=7, dome=0.2, amb=0.5, kd=0.72, spec=1.0, shine=90, rim=((150, 255, 240), 0.2))
    for sign in (-1, 1):
        plush(body, [box_poly(C + sign * 150 - 34, 296, C + sign * 150 + 34, 356, 8)], (176, 188, 208), (96, 110, 134), bevel=5, dome=0.2, spec=0.9, shine=70, cast=(0, 4, 5, 0.4))
        _bolt(body, C + sign * 150 - 22, 308)
        _bolt(body, C + sign * 150 + 22, 308)
    plush(body, [box_poly(C - 68, 304, C + 68, 352, 6)], (10, 16, 24), bevel=3, dome=0.1, amb=0.9, kd=0.2, spec=0.5, shine=60)
    airbrush(body, [box_poly(C - 158, 366, C + 158, 368.5)], (70, 84, 108), 0.8, 0.4)
    desk_shadow(body, 0.42)


def robi_head(head):
    plush(head, [box_poly(C - 4, 28, C + 4, 66)], (168, 180, 200), (96, 110, 134), bevel=2, spec=0.9, shine=50)
    for sign in (-1, 1):
        x0, x1 = sorted((C + sign * 118, C + sign * 148))
        plush(head, [box_poly(x0, 112, x1, 204, 5)], (166, 178, 198), (92, 106, 130), bevel=4, dome=0.2, spec=0.9, shine=60)
        plush(head, [box_poly(x0 + 8, 126, x1 - 8, 190)], (90, 240, 220), (40, 200, 180), bevel=2, amb=0.95, kd=0.25)
    plush(head, [box_poly(C - 122, 62, C + 122, 258, 28)], (238, 242, 250), (146, 162, 188), bevel=8, dome=0.2, amb=0.5, kd=0.72, spec=1.0, shine=100, rim=((150, 255, 240), 0.22), cast=(0, 9, 10, 0.42))
    for x in range(C - 60, C + 61, 24):
        airbrush(head, [box_poly(x - 3, 70, x + 3, 84)], (60, 72, 94), 0.9, 0.4)
    for x, y in ((C - 104, 80), (C + 104, 80), (C - 104, 240), (C + 104, 240)):
        _bolt(head, x, y)
    plush(head, [box_poly(C - 102, 90, C + 102, 232, 16)], (12, 18, 28), (6, 10, 16), bevel=4, dome=0.15, amb=0.92, kd=0.2, spec=0.4, shine=40, cast=(0, -3, 4, 0.4))
    airbrush(head, [[(C - 92, 100), (C - 36, 100), (C - 70, 176), (C - 92, 176)]], (150, 200, 220), 0.26, 1.2)


def _robi_led(look, blink, mouth, t):
    size = (int(220 * S), int(150 * S))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    color = (160, 255, 244) if mouth > 0.08 else (70, 232, 214)
    polys = []
    for sign in (-1, 1):
        h = 3 if blink else 22
        cx, cy = 110 + sign * 50 + look[0] * 8, 36 + look[1] * 7
        polys.append(box_poly(cx - 25, cy - h, cx + 25, cy + h))
    bars = 13
    for i in range(bars):
        wave = abs(math.sin(t * 11 + i * 1.05))
        h = 2.5 + mouth * 15 * (0.3 + 0.7 * wave)
        x = 110 + (i - (bars - 1) / 2) * 13
        polys.append(box_poly(x - 4.5, 92 - h, x + 4.5, 92 + h))
    under_glow(layer, polys, color, 0.8, 6)
    draw = ImageDraw.Draw(layer)
    for poly in polys:
        draw.polygon([(x * S, y * S) for x, y in poly], fill=color + (255,))
    return layer


# Mapache


def mapache_body(body):
    gray, deep = (168, 170, 180), (92, 94, 106)
    tail = spline([(C + 150, 370), (C + 196, 296), (C + 256, 226), (C + 318, 176), (C + 356, 196), (C + 344, 262), (C + 280, 340), (C + 214, 392)])
    m = plush(body, [tail], gray, deep, bevel=20, dome=0.7, amb=0.54, kd=0.68, tex=coarse_fur, tex_power=0.75, rim=((220, 224, 236), 0.3))
    for cx, cy in ((C + 206, 322), (C + 256, 262), (C + 306, 212), (C + 346, 182)):
        airbrush(body, [[(cx - 76, cy - 54), (cx + 64, cy + 64), (cx + 82, cy + 40), (cx - 54, cy - 76)]], (34, 34, 42), 0.95, 2.5, clip_to=m)
    torso = sym([(0, 240), (92, 248), (132, 296), (166, 356), (188, 430), (0, 452)])
    plush(body, [torso], gray, deep, bevel=22, dome=0.6, amb=0.54, kd=0.7, tex=coarse_fur, tex_power=0.7, rim=((220, 224, 236), 0.32), seed=5)
    belly = sym([(0, 284), (58, 288), (92, 330), (98, 400), (0, 436)])
    plush(body, [belly], (232, 232, 236), (186, 188, 198), bevel=18, dome=0.65, amb=0.6, kd=0.5, tex=coarse_fur, tex_power=0.5, seed=7)
    desk_shadow(body)


def mapache_head(head):
    gray, deep, white, black = (172, 174, 184), (106, 108, 120), (252, 252, 250), (32, 32, 40)
    for sign in (-1, 1):
        plush(head, [oval(C + sign * 102, 84, 38, 36, wobble=0.04)], white, (214, 214, 220), bevel=12, dome=0.6, amb=0.6, kd=0.5, tex=felt, tex_power=0.7)
        plush(head, [oval(C + sign * 102, 92, 23, 23)], black, bevel=7, amb=0.8, kd=0.34, tex=felt, tex_power=0.5)
    skull = sym([(0, 62), (58, 66), (106, 96), (132, 148), (150, 204), (118, 232), (126, 254), (80, 268), (0, 282)])
    m = plush(head, [skull], gray, deep, bevel=22, dome=0.62, amb=0.54, kd=0.68, tex=coarse_fur, tex_power=0.8, rim=((226, 230, 240), 0.32), cast=(0, 11, 12, 0.48), seed=6)
    airbrush(head, [taper([(C, 66), (C, 110), (C, 170)], 26, 12)], (66, 66, 76), 0.92, 3, clip_to=m)
    for sign in (-1, 1):
        brow = spline([(C + sign * 14, 124), (C + sign * 62, 96), (C + sign * 124, 122), (C + sign * 100, 142), (C + sign * 36, 148)])
        airbrush(head, [brow], white, 1.0, 3, clip_to=m)
    band = spline([(C - 146, 172), (C - 96, 138), (C - 30, 146), (C, 172), (C + 30, 146), (C + 96, 138), (C + 146, 172), (C + 118, 212), (C + 56, 222), (C, 196), (C - 56, 222), (C - 118, 212)])
    plush(head, [band], (46, 46, 56), (24, 24, 32), bevel=8, dome=0.35, amb=0.78, kd=0.5, spec=0.08, tex=felt, tex_power=0.8, cast=(0, 3, 4, 0.3))
    muzzle = sym([(0, 178), (42, 196), (84, 244), (66, 274), (0, 290)])
    plush(head, [muzzle], white, (214, 214, 220), bevel=14, dome=0.68, amb=0.6, kd=0.54, tex=felt, tex_power=0.5, cast=(0, 5, 7, 0.34))
    plush(head, [sym_at(C, [(0, 204), (22, 206), (24, 218), (8, 232), (0, 234)])], (44, 44, 52), (20, 20, 26), bevel=5, amb=0.76, kd=0.5, spec=0.9, shine=26)
    airbrush(head, [taper([(C, 232), (C, 250)], 3, 2.4)], (64, 60, 66), 0.9, 0.5)
    for sign in (-1, 1):
        for y0, y1 in ((246, 234), (256, 262)):
            poly = taper([(C + sign * 60, y0), (C + sign * 110, (y0 + y1) / 2), (C + sign * 164, y1)], 2.2, 0.8)
            plush(head, [poly], (248, 248, 246), bevel=0.8, amb=0.88, kd=0.2)


MAPACHE_EYES = (
    Eye(C - 62, 176, 23, 24, (206, 138, 58), (46, 46, 56), line=(8, 8, 12), pupil=0.5),
    Eye(C + 62, 176, 23, 24, (206, 138, 58), (46, 46, 56), line=(8, 8, 12), pupil=0.5),
)


# Carpincho


def carpincho_body(body):
    torso = sym([(0, 276), (84, 278), (146, 304), (182, 354), (196, 430), (0, 452)])
    m = plush(body, [torso], (56, 168, 182), (24, 100, 126), bevel=22, dome=0.6, tex=cloth, rim=((190, 255, 250), 0.3))
    rng = np.random.default_rng(21)
    for _ in range(30):
        x, y = rng.uniform(C - 180, C + 180), rng.uniform(282, 400)
        size = rng.uniform(0.75, 1.2)
        polys = []
        for a in range(5):
            angle = a * math.tau / 5 + rng.uniform(-0.2, 0.2)
            polys.append(oval(x + 9 * size * math.cos(angle), y + 9 * size * math.sin(angle), 6.5 * size, 5 * size, wobble=0.2, seed=a))
        airbrush(body, polys, (255, 200, 96), 0.98, 0.6, clip_to=m)
        airbrush(body, [oval(x, y, 4 * size, 4 * size)], (240, 88, 92), 1.0, 0.5, clip_to=m)
        leaf = rng.uniform(0, math.tau)
        airbrush(body, [oval(x + 17 * size * math.cos(leaf), y + 17 * size * math.sin(leaf), 7 * size, 3.6 * size)], (20, 112, 96), 0.9, 0.6, clip_to=m)
    for points in ([(C - 128, 316), (C - 96, 338), (C - 80, 376)], [(C + 128, 316), (C + 96, 338), (C + 80, 376)]):
        airbrush(body, [taper(points, 14, 3)], (8, 56, 84), 0.45, 6, clip_to=m)
    for sign in (-1, 1):
        collar = spline(side([(6, 300), (72, 270), (96, 298), (40, 346)], sign))
        plush(body, [collar], (250, 250, 248), (212, 216, 224), bevel=8, dome=0.4, amb=0.6, kd=0.5, tex=cloth, cast=(0, 5, 7, 0.4))
    desk_shadow(body)


def carpincho_head(head):
    brown, deep, snout = (190, 138, 90), (136, 92, 56), (224, 184, 138)
    for sign in (-1, 1):
        ear = oval(C + sign * 96, 94, 27, 24, wobble=0.08, seed=2)
        plush(head, [ear], brown, deep, bevel=9, tex=coarse_fur, tex_power=0.5)
        plush(head, [oval(C + sign * 96, 97, 13, 11)], (98, 60, 40), (70, 42, 30), bevel=5, amb=0.7, kd=0.3)
    skull = sym([(0, 72), (54, 74), (98, 96), (122, 138), (130, 192), (120, 240), (92, 276), (42, 292), (0, 294)])
    m = plush(head, [skull], (202, 150, 102), (150, 102, 66), bevel=22, dome=0.6, amb=0.52, kd=0.66, tex=coarse_fur, tex_power=0.75, rim=((255, 214, 170), 0.28), cast=(0, 10, 12, 0.5), seed=6)
    for sign in (-1, 1):
        airbrush(head, [oval(C + sign * 88, 128, 40, 32)], (120, 80, 50), 0.28, 12, clip_to=m)
    muzzle = sym([(0, 152), (50, 154), (88, 176), (98, 222), (80, 262), (40, 284), (0, 288)])
    mm = plush(head, [muzzle], (236, 198, 152), (200, 156, 112), bevel=20, dome=0.62, amb=0.56, kd=0.6, tex=coarse_fur, tex_power=0.45, cast=(0, 6, 9, 0.32), seed=8)
    for sign in (-1, 1):
        plush(head, [spline(side([(16, 190), (32, 182), (42, 192), (30, 204)], sign))], (78, 46, 34), (50, 30, 24), bevel=3, dome=0.2, amb=0.9, kd=0.3)
        for dx, dy in ((58, 226), (70, 214), (64, 240), (46, 238)):
            airbrush(head, [oval(C + sign * dx, dy, 1.5, 1.5)], (110, 76, 56), 0.9, 0.4)
    airbrush(head, [taper([(C, 204), (C, 222)], 3, 2)], (110, 70, 50), 0.8, 0.8, clip_to=mm)
    fruit = oval(C, 56, 37, 34, wobble=0.03, seed=3)
    plush(head, [fruit], (255, 186, 64), (240, 120, 26), bevel=14, dome=0.75, amb=0.56, kd=0.66, spec=0.55, shine=22, tex=felt, tex_power=0.5, cast=(0, 6, 6, 0.4))
    plush(head, [taper([(C + 2, 24), (C + 4, 14)], 5, 4)], (110, 80, 50), bevel=1.6)
    plush(head, [spline([(C + 4, 18), (C + 24, 4), (C + 44, 12), (C + 28, 24)])], (130, 208, 96), (70, 150, 70), bevel=4, spec=0.3, cast=(0, 2, 2, 0.3))


CARPINCHO_EYES = (
    Eye(C - 70, 130, 22, 22, (104, 66, 42), (176, 124, 82), line=(60, 36, 26), pupil=0.62, drop=0.34),
    Eye(C + 70, 130, 22, 22, (104, 66, 42), (176, 124, 82), line=(60, 36, 26), pupil=0.62, drop=0.34),
)


# Fanti


def fanti_body(body):
    sheet = sym([(0, 56), (60, 60), (104, 92), (122, 160), (126, 240), (146, 310), (176, 380), (150, 430), (60, 446), (0, 440)])
    under_glow(body, [sheet], (178, 140, 255), 0.75, 26)
    m = plush(body, [sheet], (255, 254, 255), (196, 186, 244), bevel=26, dome=0.7, amb=0.62, kd=0.5, spec=0.12, shine=16, rim=((190, 170, 255), 0.5), alpha=0.97)
    for points in (
        [(C - 86, 256), (C - 74, 312), (C - 96, 372)],
        [(C + 86, 256), (C + 74, 312), (C + 96, 372)],
        [(C - 30, 330), (C - 24, 366)],
        [(C + 30, 330), (C + 24, 366)],
    ):
        airbrush(body, [taper(points, 16, 5)], (146, 126, 222), 0.42, 8, clip_to=m)
    for sign in (-1, 1):
        wing = spline(side([(6, 300), (32, 278), (70, 272), (74, 300), (68, 334), (32, 324)], sign))
        plush(body, [wing], (250, 76, 136), (186, 32, 94), bevel=9, dome=0.45, spec=0.35, shine=24, tex=cloth, cast=(0, 4, 6, 0.3))
        airbrush(body, [taper(side([(14, 300), (40, 296), (64, 288)], sign), 3, 6)], (130, 18, 70), 0.5, 2)
        airbrush(body, [taper(side([(14, 304), (40, 312), (62, 320)], sign), 3, 6)], (130, 18, 70), 0.5, 2)
    plush(body, [oval(C, 302, 13, 15)], (255, 120, 168), (206, 44, 108), bevel=6, spec=0.4, cast=(0, 2, 4, 0.3))
    desk_shadow(body, 0.3)


def fanti_head(head):
    tilt = lambda x, y: (C + 10 + (x * 0.97 + y * 0.2), 56 + (-x * 0.2 + y * 0.97))
    crown = spline([tilt(*p) for p in [(-40, -50), (0, -56), (40, -50), (46, 0), (42, 12), (-42, 12), (-46, 0)]])
    plush(head, [crown], (84, 58, 124), (32, 22, 58), bevel=10, dome=0.5, spec=0.35, shine=28, rim=((200, 170, 255), 0.3), cast=(0, 4, 5, 0.3))
    band = spline([tilt(*p) for p in [(-44, -8), (0, -6), (44, -8), (44, 10), (0, 12), (-44, 10)]])
    plush(head, [band], (250, 76, 136), (186, 32, 94), bevel=3, spec=0.4)
    brim = spline([tilt(*p) for p in [(-76, 12), (0, 6), (76, 12), (70, 22), (0, 26), (-70, 22)]])
    plush(head, [brim], (70, 48, 106), (28, 20, 52), bevel=5, spec=0.4, shine=30, cast=(0, 5, 6, 0.32))


FANTI_EYES = (
    Eye(C - 44, 166, 21, 29, (60, 40, 96), (240, 234, 255), line=(40, 24, 66), black=True, pupil=0.2),
    Eye(C + 44, 166, 21, 29, (60, 40, 96), (240, 234, 255), line=(40, 24, 66), black=True, pupil=0.2),
)


# Matecito


def mate_body(body):
    gourd = sym([(0, 118), (70, 122), (108, 158), (132, 226), (142, 300), (126, 380), (64, 436), (0, 444)])
    m = plush(body, [gourd], (196, 132, 82), (112, 68, 38), bevel=26, dome=0.66, amb=0.5, kd=0.72, spec=0.2, shine=18, tex=wood, tex_power=0.5, rim=((255, 214, 170), 0.3))
    for sign in (-1, 1):
        airbrush(body, [oval(C + sign * 98, 256, 22, 13)], (240, 110, 80), 0.42, 7, clip_to=m)
    cloth_shape = sym([(0, 296), (84, 288), (136, 296), (120, 322), (58, 356), (0, 386)])
    bm = plush(body, [cloth_shape], (238, 66, 70), (168, 30, 46), bevel=9, dome=0.4, spec=0.18, shine=14, tex=cloth, tex_power=0.4, cast=(0, 5, 7, 0.42), rim=((255, 170, 150), 0.25))
    rng = np.random.default_rng(14)
    for _ in range(36):
        x, y = rng.uniform(C - 120, C + 120), rng.uniform(296, 372)
        airbrush(body, [oval(x, y, 3, 3)], (255, 240, 226), 0.95, 0.5, clip_to=bm)
    for points in ([(C - 70, 300), (C - 34, 326), (C - 8, 368)], [(C + 70, 300), (C + 34, 326), (C + 8, 368)]):
        airbrush(body, [taper(points, 8, 2)], (110, 16, 30), 0.5, 3, clip_to=bm)
    desk_shadow(body, 0.5)


def mate_head(head):
    straw = taper([(C + 44, 118), (C + 76, 72), (C + 116, 20)], 9, 7)
    plush(head, [straw], (246, 248, 252), (150, 156, 172), bevel=3, spec=0.9, shine=40, amb=0.55)
    plush(head, [spline([(C + 104, 12), (C + 124, 4), (C + 134, 16), (C + 118, 30)])], (252, 212, 120), (196, 148, 60), bevel=5, spec=0.8, shine=30)
    mound = sym([(0, 78), (50, 84), (80, 108), (86, 122), (0, 132)])
    plush(head, [mound], (168, 210, 96), (96, 146, 54), bevel=10, dome=0.6, amb=0.55, kd=0.66, tex=felt, tex_power=1.1, cast=(0, 3, 4, 0.35))
    rim = sym([(0, 112), (84, 114), (100, 130), (92, 156), (0, 164)])
    m = plush(head, [rim], (250, 252, 255), (150, 156, 172), bevel=9, dome=0.45, amb=0.48, kd=0.62, spec=1.0, shine=60, cast=(0, 5, 7, 0.45))
    for i in range(7):
        x = C - 72 + i * 24
        y = 138 + 4 * (1 - ((x - C) / 90) ** 2)
        plush(head, [spline([(x, y - 8), (x + 8, y), (x, y + 8), (x - 8, y)])], (252, 212, 120), (196, 148, 60), bevel=2.6, spec=0.7, shine=26, amb=0.6)


MATE_EYES = (
    Eye(C - 54, 212, 32, 36, (120, 82, 50), (172, 112, 66), line=(50, 28, 16), pupil=0.48),
    Eye(C + 54, 212, 32, 36, (120, 82, 50), (172, 112, 66), line=(50, 28, 16), pupil=0.48),
)


# Pingüi


def pingui_body(body):
    navy, deep = (66, 80, 118), (28, 34, 58)
    torso = sym([(0, 240), (92, 248), (128, 296), (164, 356), (186, 430), (0, 452)])
    m = plush(body, [torso], navy, deep, bevel=22, dome=0.6, amb=0.54, kd=0.72, tex=felt, tex_power=0.55, rim=((140, 170, 230), 0.4))
    belly = sym([(0, 276), (58, 278), (98, 318), (110, 380), (70, 430), (0, 444)])
    plush(body, [belly], (255, 255, 255), (214, 226, 242), bevel=18, dome=0.65, amb=0.62, kd=0.46, tex=felt, tex_power=0.45)
    for x, y in ((-40, 340), (0, 332), (40, 340), (-20, 362), (20, 362)):
        airbrush(body, [taper([(C + x - 8, y), (C + x, y + 7), (C + x + 8, y)], 2, 2)], (176, 196, 222), 0.6, 1)
    scarf = sym([(0, 262), (86, 256), (116, 272), (108, 300), (40, 314), (0, 316)])
    plush(body, [scarf], (246, 80, 84), (176, 36, 52), bevel=10, dome=0.5, tex=knit, tex_power=0.34, cast=(0, 6, 8, 0.4), rim=((255, 190, 170), 0.25))
    tail = spline([(C + 56, 296), (C + 92, 294), (C + 104, 346), (C + 62, 352)])
    plush(body, [tail], (246, 80, 84), (176, 36, 52), bevel=8, tex=knit, tex_power=0.34, cast=(0, 4, 6, 0.4))
    for x in (66, 76, 86, 96):
        plush(body, [taper([(C + x, 350), (C + x + 1, 366)], 4.5, 3)], (246, 96, 96), bevel=1.6)
    desk_shadow(body, 0.4)


def pingui_head(head):
    navy, deep = (70, 84, 124), (32, 38, 64)
    skull = sym([(0, 58), (58, 62), (102, 90), (124, 142), (122, 200), (98, 252), (48, 276), (0, 280)])
    m = plush(head, [skull], navy, deep, bevel=24, dome=0.62, amb=0.54, kd=0.72, tex=felt, tex_power=0.5, rim=((140, 170, 230), 0.4), cast=(0, 10, 12, 0.42))
    mask = sym([(0, 122), (26, 106), (68, 112), (96, 152), (92, 208), (54, 248), (0, 262)])
    mm = plush(head, [mask], (255, 255, 255), (222, 232, 246), bevel=14, dome=0.5, amb=0.62, kd=0.46, tex=felt, tex_power=0.35, cast=(0, 4, 6, 0.25))
    for sign in (-1, 1):
        airbrush(head, [oval(C + sign * 74, 212, 19, 12)], (255, 130, 140), 0.55, 6, clip_to=mm)
    hat = sym([(0, 12), (60, 16), (104, 52), (120, 100), (124, 120), (0, 124)])
    plush(head, [hat], (250, 84, 88), (178, 38, 54), bevel=18, dome=0.6, amb=0.52, kd=0.7, tex=knit, tex_power=0.36, rim=((255, 190, 170), 0.3), cast=(0, 7, 8, 0.42))
    cuff = sym([(0, 88), (112, 90), (132, 106), (124, 130), (0, 134)])
    plush(head, [cuff], (255, 255, 255), (214, 224, 238), bevel=9, dome=0.55, amb=0.6, kd=0.52, tex=knit, tex_power=0.3, cast=(0, 6, 7, 0.4))
    angles = np.linspace(0, math.tau, 26, endpoint=False)
    pom = [(C + (28 + 5 * (i % 2)) * math.cos(a), 16 + (26 + 5 * (i % 2)) * math.sin(a)) for i, a in enumerate(angles)]
    plush(head, [spline(pom, samples=5)], (255, 255, 255), (214, 226, 240), bevel=9, dome=0.8, amb=0.62, kd=0.5, tex=felt, tex_power=1.0, cast=(0, 4, 5, 0.32))


PINGUI_EYES = (
    Eye(C - 44, 168, 27, 31, (88, 156, 226), (66, 80, 118), line=(26, 30, 50), pupil=0.46, tilt=-0.4),
    Eye(C + 44, 168, 27, 31, (88, 156, 226), (66, 80, 118), line=(26, 30, 50), pupil=0.46, tilt=0.4),
)


def beak_top() -> Image.Image:
    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    shape = sym_at(C, [(0, 198), (38, 200), (34, 214), (12, 234), (0, 238)])
    plush(layer, [shape], (255, 190, 70), (240, 130, 30), bevel=8, dome=0.5, amb=0.6, kd=0.6, spec=0.55, shine=28, cast=(0, 3, 4, 0.3))
    return layer.crop((int((C - 60) * S), int(180 * S), int((C + 60) * S), int(260 * S)))


def beak_bottom() -> Image.Image:
    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    shape = sym_at(C, [(0, 214), (30, 216), (24, 232), (8, 246), (0, 248)])
    plush(layer, [shape], (240, 146, 40), (206, 104, 24), bevel=6, dome=0.5, amb=0.56, kd=0.56, spec=0.3, shine=22)
    return layer.crop((int((C - 50) * S), int(200 * S), int((C + 50) * S), int(260 * S)))


# Tun Tun Sahur: alto, de madera lustrada. Todo el cuerpo es una sola pieza, así que va entero en la capa de abajo;
# la capa de la cabeza solo lleva las facciones, y son ellas las que se mueven un poco al mirar.


def tuntun_body(body):
    shape = sym([(0, 12), (88, 14), (122, 30), (130, 74), (128, 190), (120, 300), (126, 390), (134, 452), (0, 464)])
    m = plush(body, [shape], (228, 136, 58), (130, 60, 20), bevel=24, dome=0.62, amb=0.5, kd=0.72, spec=0.45, shine=34, tex=wood, tex_power=0.22, rim=((255, 196, 130), 0.28), seed=9)
    rng = np.random.default_rng(31)
    for _ in range(30):
        x, y = rng.uniform(C - 122, C + 122), rng.uniform(44, 380)
        length = rng.uniform(40, 150)
        airbrush(body, [taper([(x, y), (x + rng.uniform(-2, 2), y + length / 2), (x + rng.uniform(-3, 3), y + length)], 3, 1.2)], (112, 50, 14), 0.34, 1.0, clip_to=m)
    # La tapa de arriba, apenas marcada.
    airbrush(body, [taper([(C - 126, 48), (C - 60, 40), (C, 38), (C + 60, 40), (C + 126, 48)], 3.2, 3.2)], (92, 42, 12), 0.7, 0.6, clip_to=m)
    airbrush(body, [taper([(C - 100, 28), (C, 22), (C + 100, 28)], 12, 12)], (255, 206, 150), 0.32, 4, clip_to=m)
    for sign in (-1, 1):
        # Las cuencas de los ojos y los pliegues de la sonrisa.
        airbrush(body, [oval(C + sign * 58, 150, 57, 58)], (84, 38, 10), 0.58, 9, clip_to=m)
        airbrush(body, [taper([(C + sign * 86, 196), (C + sign * 96, 228), (C + sign * 82, 262)], 8, 3)], (84, 38, 10), 0.62, 2.4, clip_to=m)
        airbrush(body, [oval(C + sign * 92, 212, 22, 16)], (255, 176, 110), 0.24, 7, clip_to=m)
    # La sombra de la nariz.
    airbrush(body, [oval(C + 4, 214, 34, 28)], (84, 38, 10), 0.42, 7, clip_to=m)
    airbrush(body, [taper([(C - 70, 322), (C, 330), (C + 70, 322)], 5, 5)], (92, 42, 12), 0.34, 2.5, clip_to=m)
    desk_shadow(body)


def tuntun_head(head):
    for sign in (-1, 1):
        # Un anillo oscuro alrededor del ojo y una ceja gruesa, como tallada.
        plush(head, [oval(C + sign * 58, 150, 46, 50)], (70, 32, 10), (44, 20, 6), bevel=4, dome=0.3, amb=0.7, kd=0.4)
        brow = taper([(C + sign * 118, 134), (C + sign * 98, 100), (C + sign * 58, 86), (C + sign * 12, 106)], 14, 20)
        plush(head, [brow], (112, 54, 18), (64, 30, 8), bevel=6, dome=0.5, amb=0.56, kd=0.72, spec=0.3, shine=22)
    nose = spline([(C - 13, 110), (C + 13, 110), (C + 14, 170), (C + 28, 206), (C + 17, 226), (C, 232), (C - 17, 226), (C - 28, 206), (C - 14, 170)])
    plush(head, [nose], (232, 142, 62), (166, 86, 32), bevel=9, dome=0.7, amb=0.54, kd=0.72, spec=0.5, shine=30, tex=wood, tex_power=0.15)
    for sign in (-1, 1):
        plush(head, [oval(C + sign * 10, 224, 5.5, 3.5)], (48, 20, 6), bevel=1.5, amb=0.9, kd=0.2)


TUNTUN_EYES = (
    Eye(C - 58, 150, 41, 45, (118, 66, 30), (204, 116, 44), line=(52, 24, 8), pupil=0.56, iris_size=0.74),
    Eye(C + 58, 150, 41, 45, (118, 66, 30), (204, 116, 44), line=(52, 24, 8), pupil=0.56, iris_size=0.74),
)


# Las patas y manos, dibujadas hacia abajo


def paw_polys(kind):
    if kind == "madera":
        return [oval(60, 42, 23, 19, wobble=0.06, seed=4)] + [oval(60 + dx, 60 + dy, 8.5, 11.5, wobble=0.05, seed=7) for dx, dy in ((-15, -3), (0, 2), (15, -3))]
    if kind == "gato":
        return [oval(60, 44, 25, 21, wobble=0.04)] + [oval(60 + dx, 60 + dy, 10.5, 10.5) for dx, dy in ((-15, -1), (0, 3), (15, -1))]
    if kind == "dedos":
        return [oval(60, 42, 29, 23, wobble=0.04)] + [oval(60 + dx, 60 + dy, 9.5, 11) for dx, dy in ((-21, -3), (-7, 2), (7, 2), (21, -3))]
    if kind == "cinco":
        return [oval(60, 38, 24, 20, wobble=0.03)] + [
            spline([(60 + dx - 4.5, 44), (60 + dx + 4.5, 44), (60 + dx + 3.5 + dx * 0.25, 62 + dy), (60 + dx + dx * 0.3, 70 + dy), (60 + dx - 3.5 + dx * 0.25, 62 + dy)], samples=6)
            for dx, dy in ((-20, -8), (-10, 0), (0, 3), (10, 0), (20, -8))
        ]
    if kind == "pinza":
        return [box_poly(38, 28, 82, 54, 4)] + [box_poly(60 + dx - 7, 52, 60 + dx + 7, 78, 2) for dx in (-15, 0, 15)]
    if kind == "muñon":
        return [oval(60, 50, 21, 21, wobble=0.05)]
    if kind == "guante":
        return [oval(60, 46, 24, 21, wobble=0.03), oval(81, 42, 8, 11)] + [oval(60 + dx, 62, 8.5, 10) for dx in (-13, 0, 13)]
    if kind == "aleta":
        return [spline([(38, 30), (82, 30), (78, 62), (60, 74), (42, 62)])]
    raise ValueError(kind)


def make_paw(kind, color, bottom, tex=None, spec=0.08):
    size = (120 * S, 100 * S)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    bevel = 3 if kind == "pinza" else 5 if kind == "cinco" else 8
    plush(layer, paw_polys(kind), color, bottom, bevel=bevel, dome=0.3 if kind == "pinza" else 0.55, amb=0.58, kd=0.62, spec=spec, shine=24, tex=tex, tex_power=0.4)
    draw = ImageDraw.Draw(layer)
    if kind == "dedos":
        # Las uñas del carpincho.
        for dx, dy in ((-21, -3), (-7, 2), (7, 2), (21, -3)):
            x, y = (60 + dx) * S, (68 + dy) * S
            draw.ellipse([x - 5 * S, y - 3.5 * S, x + 5 * S, y + 3.5 * S], fill=(62, 40, 28, 255))
    elif kind == "gato":
        for dx in (-8, 8):
            x, y = (60 + dx) * S, 58 * S
            draw.line([(x, y), (x, y + 13 * S)], fill=(150, 120, 104, 255), width=2 * S)
    elif kind == "pinza":
        draw.rectangle([52 * S, 34 * S, 68 * S, 46 * S], fill=ACCENT + (255,))
    elif kind == "guante":
        for dx in (-9, 0, 9):
            x = (60 + dx) * S
            draw.line([(x, 34 * S), (x, 50 * S)], fill=(90, 90, 96, 255), width=2 * S)
    elif kind == "madera":
        # Las vetas de la palma.
        for box in ((46, 32, 74, 50), (52, 37, 68, 46)):
            draw.arc([v * S for v in box], 190, 350, fill=(112, 72, 42, 255), width=2 * S)
    return layer


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    note: str
    body: object
    head: object
    eyes: tuple[Eye, ...]
    mouth: tuple
    closed: object
    arm: tuple[int, int, int]
    paw: tuple
    shoulder: tuple[float, float] = (C - 128, 322)
    elbow: tuple[float, float] = (-44, 26)
    width: tuple[float, float] = (30, 21)
    cuff: tuple[int, int, int] | None = None
    floats: bool = False
    desk: tuple = ((96, 62, 52), (54, 34, 34))
    planks: bool = False
    hand: str = ""
    segmented: bool = False
    bat: bool = False


def closed_cat(layer):
    for sign in (-1, 1):
        stroke = taper([(C, 224), (C + sign * 11, 232), (C + sign * 24, 226)], 3.4, 2)
        ImageDraw.Draw(layer).polygon([(x * S, y * S) for x, y in stroke], fill=(96, 56, 50, 255))


def closed_raccoon(layer):
    for sign in (-1, 1):
        stroke = taper([(C, 252), (C + sign * 13, 262), (C + sign * 28, 254)], 3.4, 2)
        ImageDraw.Draw(layer).polygon([(x * S, y * S) for x, y in stroke], fill=(60, 56, 62, 255))


def closed_capy(layer):
    stroke = taper([(C - 44, 240), (C - 20, 250), (C + 20, 250), (C + 44, 240)], 3, 3, samples=8)
    ImageDraw.Draw(layer).polygon([(x * S, y * S) for x, y in stroke], fill=(96, 56, 40, 255))


def closed_ghost(layer):
    stroke = taper([(C - 15, 210), (C, 219), (C + 15, 210)], 3, 3)
    ImageDraw.Draw(layer).polygon([(x * S, y * S) for x, y in stroke], fill=(62, 40, 96, 255))


def closed_mate(layer):
    stroke = taper([(C - 24, 262), (C, 274), (C + 24, 262)], 4, 4)
    ImageDraw.Draw(layer).polygon([(x * S, y * S) for x, y in stroke], fill=(64, 30, 18, 255))


def closed_grin(layer):
    # Siempre está sonriendo: con la boca cerrada igual se le ven los dientes.
    sprite, position = make_grin(C, 240, 66, 14)
    layer.alpha_composite(sprite, position)


CHARACTERS = [
    Character("michi", "Michi", "Gato atigrado de pelo suave, con buzo y auriculares, en su cuarto de noche.", michi_body, michi_head, MICHI_EYES,
              (C, 230, 22, 40, {}), closed_cat, (66, 186, 172), ("gato", (255, 244, 228), (246, 214, 186)), cuff=(38, 146, 138), planks=True,
              hand="Patas de gata, con mangas de buzo."),
    Character("mapache", "Mapache", "Peludo, con antifaz y cola anillada, en el bosque de noche.", mapache_body, mapache_head, MAPACHE_EYES,
              (C, 258, 24, 34, {"inner": (96, 24, 40), "tongue": (240, 120, 136), "lip": (40, 36, 44)}), closed_raccoon, (150, 152, 162), ("cinco", (52, 52, 62), (24, 24, 32)),
              width=(26, 18), desk=((104, 76, 52), (58, 40, 32)), planks=True, hand="Manos negras de cinco dedos largos."),
    Character("robi", "Robi", "Robot de chapa pulida, todo recto, en su laboratorio: la cara y el pecho muestran tu voz.", robi_body, robi_head, (),
              None, None, (176, 188, 208), ("pinza", (226, 234, 244), (150, 164, 188)), width=(30, 22), desk=((52, 64, 82), (22, 28, 40)),
              hand="Pinzas de metal con articulaciones.", segmented=True),
    Character("carpincho", "Carpincho", "Peludo y tranquilo, con camisa floreada y naranja, en la laguna al atardecer.", carpincho_body, carpincho_head, CARPINCHO_EYES,
              (C, 244, 32, 34, {"teeth": True, "inner": (96, 30, 34), "tongue": (226, 116, 118), "lip": (92, 50, 34)}), closed_capy, (56, 160, 176), ("dedos", (224, 184, 138), (186, 142, 100)),
              cuff=(240, 240, 238), planks=True, desk=((158, 112, 72), (96, 62, 40)), hand="Patas gruesas con cuatro dedos y uñas."),
    Character("fanti", "Fanti", "Fantasma etéreo de galera y moño, en un altillo con luna.", fanti_body, fanti_head, FANTI_EYES,
              (C, 206, 19, 44, {"inner": (66, 34, 96), "tongue": (244, 120, 164), "lip": (62, 40, 96)}), closed_ghost, (232, 226, 252), ("muñon", (250, 248, 255), (214, 206, 246)),
              floats=True, desk=((64, 46, 84), (30, 22, 46)), hand="Sin dedos: la tela de la sábana."),
    Character("mate", "Matecito", "Mate de madera con virola de plata y pañuelo, en el campo con molino.", mate_body, mate_head, MATE_EYES,
              (C, 258, 24, 36, {"inner": (84, 26, 26), "tongue": (236, 120, 120), "lip": (64, 30, 18)}), closed_mate, (176, 118, 72), ("guante", (252, 250, 244), (222, 218, 208)),
              shoulder=(C - 132, 336), elbow=(-10, 4), width=(19, 15), planks=True, desk=((140, 92, 52), (84, 52, 30)), hand="Bracitos finos y guantes blancos."),
    Character("pingui", "Pingüi", "Pingüino de gorro y bufanda tejidos, bajo una aurora: abre el pico al hablar.", pingui_body, pingui_head, PINGUI_EYES,
              None, None, (62, 76, 112), ("aleta", (72, 88, 128), (40, 50, 80)), width=(30, 25), desk=((206, 226, 244), (136, 170, 206)), hand="Aletas, sin dedos."),
    Character("tuntun", "Tun Tun Sahur", "Alto, de madera lustrada, de ojos enormes y sonrisa de lado a lado, con su bate.", tuntun_body, tuntun_head, TUNTUN_EYES,
              (C, 240, 66, 34, {"grin": True}), closed_grin, (196, 108, 40), ("madera", (214, 124, 50), (150, 76, 26)),
              shoulder=(C - 116, 330), elbow=(-10, 4), width=(18, 14), planks=True, desk=((122, 86, 60), (70, 46, 36)), hand="Bracitos finos: golpea cada tecla con el bate.", bat=True),
]
