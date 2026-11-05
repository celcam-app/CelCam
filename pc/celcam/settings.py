"""Ajustes que el usuario elige en la ventana."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Nombre visible -> (ancho, alto, kbps). Softcam exige múltiplos de 4.
QUALITIES: dict[str, tuple[int, int, int]] = {
    "480p": (856, 480, 2500),
    "720p": (1280, 720, 5000),
    "1080p": (1920, 1080, 9000),
}
# Cuadros por segundo que se pueden pedir -> cuánto se multiplica la calidad de video para sostenerlos.
FPS_OPTIONS: dict[int, float] = {30: 1.0, 60: 1.5, 120: 2.0}


@dataclass
class Settings:
    quality: str = "720p"
    fps: int = 30
    mirror: bool = False
    extra_turns: int = 0
    fill: bool = False

    @property
    def size(self) -> tuple[int, int]:
        return QUALITIES[self.quality][:2]

    @property
    def kbps(self) -> int:
        return int(QUALITIES[self.quality][2] * FPS_OPTIONS[self.fps])

    @classmethod
    def load(cls, path: Path) -> "Settings":
        settings = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return settings
        except (OSError, ValueError):
            log.warning("No se pudieron leer los ajustes; se usan los de fábrica", exc_info=True)
            return settings
        if raw.get("quality") in QUALITIES:
            settings.quality = raw["quality"]
        if raw.get("fps") in FPS_OPTIONS:
            settings.fps = raw["fps"]
        settings.mirror = bool(raw.get("mirror", False))
        settings.fill = bool(raw.get("fill", False))
        turns = raw.get("extra_turns", 0)
        settings.extra_turns = turns % 4 if isinstance(turns, int) else 0
        return settings

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
