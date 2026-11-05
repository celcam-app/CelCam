"""Dibuja el ícono de CelCam para Windows: la misma cámara que el ícono de la app."""

import sys

from PIL import Image, ImageDraw

BACKGROUND = (17, 19, 24, 255)
ACCENT = (45, 212, 191, 255)
SIZE = 512


def main(path: str) -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=96, fill=BACKGROUND)
    draw.rounded_rectangle((88, 150, 340, 362), radius=28, fill=ACCENT)
    draw.polygon([(356, 226), (436, 158), (436, 354), (356, 286)], fill=ACCENT)
    image.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main(sys.argv[1])
