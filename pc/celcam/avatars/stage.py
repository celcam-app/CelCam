"""Lo común para armar cuadros rápido: lo fijo se prepara una vez y por cuadro solo se pega lo que cambia."""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable

import numpy as np
from PIL import Image

W, H, S = 960, 540, 2
GREEN = (0, 177, 64)
# Cuánta memoria puede usar cada avatar para las piezas ya dibujadas.
MAX_PIECES_BYTES = 64 * 1024 * 1024
MAX_BASES = 16


class SpriteCache:
    """Guarda piezas ya dibujadas; cuando se llena, tira las que hace más que no se usan."""

    def __init__(self, max_bytes: int = MAX_PIECES_BYTES) -> None:
        self._items: OrderedDict = OrderedDict()
        self._bytes = 0
        self._max = max_bytes

    def get(self, key, make: Callable[[], tuple[Image.Image, tuple[int, int]]]):
        item = self._items.get(key)
        if item is not None:
            self._items.move_to_end(key)
            return item
        item = make()
        self._items[key] = item
        self._bytes += item[0].width * item[0].height * 4
        while self._bytes > self._max and len(self._items) > 1:
            _, old = self._items.popitem(last=False)
            self._bytes -= old[0].width * old[0].height * 4
        return item


class Stage:
    """Un escenario de un tamaño dado. `background`: «color», «verde» o «transparente»."""

    def __init__(self, size: tuple[int, int], background: str) -> None:
        self.size = size
        self.background = background
        self.transparent = background == "transparente"
        self.fx = size[0] / (W * S)
        self.fy = size[1] / (H * S)
        self.pieces = SpriteCache()
        self._bases: OrderedDict = OrderedDict()

    def scaled(self, image: Image.Image, quality: int = Image.LANCZOS) -> Image.Image:
        """De la escala en que se dibuja a la de salida."""
        if self.fx == 1 and self.fy == 1:
            return image
        return image.resize((max(1, round(image.width * self.fx)), max(1, round(image.height * self.fy))), quality)

    def at(self, x: float, y: float) -> tuple[int, int]:
        """Un punto de la escala en que se dibuja, en la de salida."""
        return round(x * self.fx), round(y * self.fy)

    def trimmed(self, layer: Image.Image, origin: tuple[int, int] = (0, 0), quality: int = Image.LANCZOS):
        """La parte con dibujo de una capa, ya en la escala de salida, con su posición."""
        box = layer.getbbox()
        if not box:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0, 0)
        crop = layer.crop(box)
        return self.scaled(crop, quality), self.at(origin[0] + box[0], origin[1] + box[1])

    def put(self, frame: Image.Image, sprite: Image.Image, position: tuple[int, int]) -> None:
        """Pega una pieza. Un fondo opaco admite la forma rápida; uno transparente necesita la exacta."""
        x, y = position
        if not self.transparent:
            frame.paste(sprite, (x, y), sprite)
            return
        left, top = max(0, -x), max(0, -y)
        right, bottom = min(sprite.width, frame.width - x), min(sprite.height, frame.height - y)
        if right <= left or bottom <= top:
            return
        if (left, top, right, bottom) != (0, 0, sprite.width, sprite.height):
            sprite = sprite.crop((left, top, right, bottom))
        frame.alpha_composite(sprite, (x + left, y + top))

    def base(self, key, make: Callable[[], Image.Image]) -> Image.Image:
        """Una de las pocas composiciones de fondo, cuerpo y mesa que se van repitiendo."""
        image = self._bases.get(key)
        if image is None:
            image = make()
            self._bases[key] = image
            while len(self._bases) > MAX_BASES:
                self._bases.popitem(last=False)
        else:
            self._bases.move_to_end(key)
        return image

    def blank(self) -> Image.Image:
        return Image.new("RGBA", self.size, (0, 0, 0, 0))

    def to_camera(self, frame: Image.Image) -> np.ndarray:
        """BGR, o BGRA si el fondo es transparente: lo que recibe la cámara virtual."""
        height, width = frame.height, frame.width
        if self.transparent:
            return np.frombuffer(frame.tobytes("raw", "BGRA"), dtype=np.uint8).reshape(height, width, 4)
        return np.frombuffer(frame.convert("RGB").tobytes("raw", "BGR"), dtype=np.uint8).reshape(height, width, 3)
