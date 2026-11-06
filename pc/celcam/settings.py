"""Ajustes que el usuario elige en la ventana."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .avatars import BACKGROUNDS, CHARACTERS, STYLES

log = logging.getLogger(__name__)

# Nombre visible -> (ancho, alto, kbps). Softcam exige múltiplos de 4.
QUALITIES: dict[str, tuple[int, int, int]] = {
    "480p": (856, 480, 2500),
    "720p": (1280, 720, 5000),
    "1080p": (1920, 1080, 9000),
}
# Cuadros por segundo que se pueden pedir -> cuánto se multiplica la calidad de video para sostenerlos.
FPS_OPTIONS: dict[int, float] = {30: 1.0, 60: 1.5, 120: 2.0}
CAMERA_COUNT = 4
# Qué muestra la cámara con un avatar.
AVATAR_MODES = {"apagado": "Apagado", "solo": "Solo el avatar", "sobre": "Sobre el video"}
AVATAR_CORNERS = {"abajo-der": "Abajo a la derecha", "abajo-izq": "Abajo a la izquierda", "arriba-der": "Arriba a la derecha", "arriba-izq": "Arriba a la izquierda"}
# Qué parte del ancho de la imagen ocupa cuando va sobre el video.
AVATAR_SIZES = {25: "Chico", 35: "Mediano", 50: "Grande"}


@dataclass
class CameraSettings:
    """Lo que se elige para cada cámara."""

    quality: str = "720p"
    fps: int = 30
    mirror: bool = False
    extra_turns: int = 0
    fill: bool = False
    avatar_mode: str = "apagado"
    avatar_style: str = "dibujado"
    avatar_id: str = "michi"
    avatar_background: str = "color"
    avatar_corner: str = "abajo-der"
    avatar_size: int = 35
    avatar_keys: bool = True
    avatar_voice: bool = True

    @property
    def size(self) -> tuple[int, int]:
        return QUALITIES[self.quality][:2]

    @property
    def kbps(self) -> int:
        return int(QUALITIES[self.quality][2] * FPS_OPTIONS[self.fps])

    @classmethod
    def from_dict(cls, raw: dict) -> "CameraSettings":
        """Lo que no se entienda queda con el valor de fábrica."""
        camera = cls()
        if raw.get("quality") in QUALITIES:
            camera.quality = raw["quality"]
        if raw.get("fps") in FPS_OPTIONS:
            camera.fps = raw["fps"]
        camera.mirror = bool(raw.get("mirror", False))
        camera.fill = bool(raw.get("fill", False))
        turns = raw.get("extra_turns", 0)
        camera.extra_turns = turns % 4 if isinstance(turns, int) else 0
        if raw.get("avatar_mode") in AVATAR_MODES:
            camera.avatar_mode = raw["avatar_mode"]
        if raw.get("avatar_style") in STYLES:
            camera.avatar_style = raw["avatar_style"]
        if raw.get("avatar_id") in {c.id for c in CHARACTERS}:
            camera.avatar_id = raw["avatar_id"]
        if raw.get("avatar_background") in BACKGROUNDS:
            camera.avatar_background = raw["avatar_background"]
        if raw.get("avatar_corner") in AVATAR_CORNERS:
            camera.avatar_corner = raw["avatar_corner"]
        if raw.get("avatar_size") in AVATAR_SIZES:
            camera.avatar_size = raw["avatar_size"]
        camera.avatar_keys = bool(raw.get("avatar_keys", True))
        camera.avatar_voice = bool(raw.get("avatar_voice", True))
        return camera


@dataclass
class Settings:
    cameras: list[CameraSettings] = field(default_factory=lambda: [CameraSettings() for _ in range(CAMERA_COUNT)])
    # Cada celular vuelve siempre a su cámara. El más viejo va primero: es el primero en ceder su lugar.
    phones: dict[str, int] = field(default_factory=dict)
    # El aviso de que CelCam sigue abierta se muestra una sola vez.
    hide_notice_shown: bool = False

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
        if not isinstance(raw, dict):
            return settings
        settings.hide_notice_shown = bool(raw.get("hide_notice_shown", False))
        cameras = raw.get("cameras")
        if isinstance(cameras, list):
            for index, camera in enumerate(cameras[:CAMERA_COUNT]):
                if isinstance(camera, dict):
                    settings.cameras[index] = CameraSettings.from_dict(camera)
        else:
            # Los ajustes de antes de que hubiera varias cámaras son los de la primera.
            settings.cameras[0] = CameraSettings.from_dict(raw)
        phones = raw.get("phones")
        if isinstance(phones, dict):
            taken = set()
            for phone, slot in phones.items():
                if isinstance(slot, int) and 0 <= slot < CAMERA_COUNT and slot not in taken:
                    settings.phones[str(phone)] = slot
                    taken.add(slot)
        return settings

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
