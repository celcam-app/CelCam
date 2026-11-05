"""El programa sin la ventana: une servidor, video y cámara virtual."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import paths
from .identity import Identity
from .netinfo import local_hosts
from .pipeline import Output, Pipeline, Stats, View
from .protocol import VideoFrame
from .server import Listener, Session, StreamConfig, StreamServer
from .settings import Settings
from .usb import Adb

log = logging.getLogger(__name__)

PLACEHOLDER_INTERVAL = 0.5
# Sin cuadros durante este tiempo, la cámara vuelve a la imagen de espera.
STALE_AFTER = 2.0


def make_placeholder(width: int, height: int, title: str, subtitle: str) -> np.ndarray:
    image = Image.new("RGB", (width, height), (17, 19, 24))
    draw = ImageDraw.Draw(image)
    try:
        big = ImageFont.truetype("segoeuib.ttf", max(18, height // 14))
        small = ImageFont.truetype("segoeui.ttf", max(12, height // 28))
    except OSError:
        big = small = ImageFont.load_default()
    draw.text((width / 2, height / 2 - height // 24), title, font=big, fill=(236, 239, 244), anchor="mm")
    draw.text((width / 2, height / 2 + height // 24), subtitle, font=small, fill=(150, 158, 171), anchor="mm")
    # PIL usa RGB; la cámara, BGR.
    return np.asarray(image, dtype=np.uint8)[:, :, ::-1].copy()


@dataclass(frozen=True)
class Status:
    connected: bool
    device: str
    stats: Stats
    streaming: bool
    camera_in_use: bool
    # Lo que la cámara del celular dijo que puede hacer con lo pedido; 0 si todavía no avisó.
    phone_fps: int = 0


class Core(Listener):
    def __init__(self, settings: Settings, identity: Identity, camera: Output, settings_path: Path | None = None) -> None:
        self.settings = settings
        self.identity = identity
        self.camera = camera
        self._settings_path = settings_path
        self._pipeline: Pipeline | None = None
        self._pipeline_lock = threading.Lock()
        self._last_frame = 0.0
        self._phone_fps = 0
        self._stopping = threading.Event()
        self._placeholder = np.empty(0)
        self.adb = Adb(paths.adb_exe())
        self.server = StreamServer(identity, self, self._stream_config)

    # Arranque y apagado

    def start(self) -> None:
        self._render_placeholder()
        self.server.start()
        threading.Thread(target=self._placeholder_loop, name="celcam-espera", daemon=True).start()

    def stop(self) -> None:
        self._stopping.set()
        self.server.stop()
        self.adb.shutdown()
        close = getattr(self.camera, "close", None)
        if close:
            close()

    # Enlaces

    def link(self) -> str:
        return self.identity.link(local_hosts() or ["127.0.0.1"], self.server.port)

    def usb_link(self) -> str:
        return self.identity.link(["127.0.0.1"], self.server.port)

    def connect_usb(self) -> str:
        if not self.adb.available():
            self.adb.download()
        return self.adb.connect(self.server.port, self.usb_link(), paths.apk_file())

    # Ajustes que el usuario cambia en la ventana

    def apply_view(self) -> None:
        with self._pipeline_lock:
            if self._pipeline:
                self._pipeline.set_view(self._view())
        self._save()

    def set_quality(self, quality: str) -> None:
        self.settings.quality = quality
        self._apply_format()

    def set_fps(self, fps: int) -> None:
        self.settings.fps = fps
        self._apply_format()

    def _apply_format(self) -> None:
        """Vuelve a crear la cámara. Las apps que la tenían abierta tienen que volver a elegirla."""
        width, height = self.settings.size
        reopen = getattr(self.camera, "open", None)
        if reopen:
            reopen(width, height, self.settings.fps)
        self._render_placeholder()
        session = self.server.current()
        if session:
            session.send_control(self._stream_config().as_command())
        self._save()

    def _save(self) -> None:
        if self._settings_path:
            self.settings.save(self._settings_path)

    def _view(self) -> View:
        s = self.settings
        return View(mirror=s.mirror, extra_turns=s.extra_turns, fill=s.fill)

    def _stream_config(self) -> StreamConfig:
        width, height = self.settings.size
        return StreamConfig(width, height, self.settings.fps, self.settings.kbps)

    # Estado para la ventana

    def status(self) -> Status:
        session = self.server.current()
        with self._pipeline_lock:
            stats = self._pipeline.stats() if self._pipeline else Stats()
        streaming = session is not None and time.monotonic() - self._last_frame < STALE_AFTER
        in_use = getattr(self.camera, "in_use", lambda: False)()
        return Status(session is not None, session.device if session else "", stats, streaming, in_use, self._phone_fps)

    def latest_image(self) -> np.ndarray | None:
        with self._pipeline_lock:
            pipeline = self._pipeline
        if pipeline is None or time.monotonic() - self._last_frame >= STALE_AFTER:
            return None
        return pipeline.latest_image()

    # Sesiones

    def on_connected(self, session: Session) -> None:
        self._phone_fps = 0
        with self._pipeline_lock:
            self._pipeline = Pipeline(self.camera, self._view())
        session.send_control({"cmd": "key"})

    def on_capture(self, session: Session, report: dict) -> None:
        fps = report.get("fps")
        if self.server.current() is session and isinstance(fps, int) and fps > 0:
            self._phone_fps = fps
        log.info(
            "%s captura %sx%s a %s cuadros/s; admite: %s",
            session.device,
            report.get("w"),
            report.get("h"),
            fps,
            str(report.get("rates", ""))[:400],
        )

    def on_video(self, session: Session, frame: VideoFrame) -> None:
        with self._pipeline_lock:
            pipeline = self._pipeline
        if pipeline is None or self.server.current() is not session:
            return
        try:
            pipeline.feed(frame.data, frame.quarter_turns)
        except Exception:
            # Un cuadro dañado no corta la sesión: se pide una imagen completa y se sigue.
            log.warning("No se pudo decodificar un cuadro", exc_info=True)
            pipeline.reset_decoder()
            session.send_control({"cmd": "key"})
            return
        self._last_frame = time.monotonic()

    def on_disconnected(self, session: Session) -> None:
        if self.server.current() is None:
            self._last_frame = 0.0

    # Imagen de espera

    def _render_placeholder(self) -> None:
        width, height = self.camera.size
        self._placeholder = make_placeholder(width, height, "CelCam", "Esperando al celular…")

    def _placeholder_loop(self) -> None:
        while not self._stopping.wait(PLACEHOLDER_INTERVAL):
            if time.monotonic() - self._last_frame >= STALE_AFTER:
                self.camera.send(self._placeholder)
