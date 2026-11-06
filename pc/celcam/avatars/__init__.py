"""Avatares de CelCam: personajes propios en dos estilos, que siguen tus teclas, tu mouse y tu voz."""

from __future__ import annotations

from dataclasses import dataclass

STYLES = {"dibujado": "Dibujado", "volumen": "Con volumen"}
BACKGROUNDS = {"color": "Con fondo", "transparente": "Transparente", "verde": "Verde para croma"}


@dataclass(frozen=True)
class Character:
    id: str
    name: str


# Son los mismos en los dos estilos.
CHARACTERS = (
    Character("michi", "Michi"),
    Character("mapache", "Mapache"),
    Character("robi", "Robi"),
    Character("carpincho", "Carpincho"),
    Character("fanti", "Fanti"),
    Character("mate", "Matecito"),
    Character("pingui", "Pingüi"),
    Character("tuntun", "Tun Tun Sahur"),
)


def make_scene(style: str, character: str, size: tuple[int, int], background: str):
    """Dibuja el avatar elegido. Tarda hasta un par de segundos: no llamarlo desde la ventana."""
    if style == "volumen":
        from . import art, volume

        return volume.Scene(next(c for c in art.CHARACTERS if c.id == character), size, background)
    from . import drawn

    return drawn.Scene(next(c for c in drawn.DRAWN if c.id == character), size, background)
