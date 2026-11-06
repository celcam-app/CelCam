"""El programa sin la ventana: une servidor, video y cámaras virtuales."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import paths
from .avatars.runner import AvatarRunner
from .avatars.sensors import Sensors
from .identity import Identity
from .netinfo import local_hosts
from .pipeline import Output, Pipeline, Stats, View
from .protocol import VideoFrame
from .server import Listener, Session, StreamConfig, StreamServer
from .settings import CameraSettings, Settings
from .usb import Adb
from .vcam import CAMERA_NAMES

log = logging.getLogger(__name__)

PLACEHOLDER_INTERVAL = 0.5
# Sin cuadros durante este tiempo, la ventana deja de decir «Transmitiendo».
STALE_AFTER = 2.0
# Un corte corto no tiene que verse: la cámara sigue con la última imagen. Después, la de espera.
HOLD_LAST_IMAGE = 10.0


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


def resize_image(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Alcanza para el momento que tarda el celular en mandar el formato nuevo."""
    rows = np.arange(height) * image.shape[0] // height
    columns = np.arange(width) * image.shape[1] // width
    return image[rows][:, columns]


@dataclass(frozen=True)
class PhoneControls:
    """Lo que se puede manejar desde la PC, tal como está ahora en el celular."""

    zoom_min: float
    zoom_max: float
    zoom: float
    torch_available: bool
    torch: bool
    can_switch: bool
    dim: bool

    @classmethod
    def from_report(cls, report: dict) -> "PhoneControls | None":
        """Una app vieja no informa nada de esto: la ventana le pide actualizarse."""
        try:
            return cls(
                float(report["zoom_min"]),
                float(report["zoom_max"]),
                float(report["zoom"]),
                bool(report["torch_available"]),
                bool(report["torch"]),
                bool(report["can_switch"]),
                bool(report["dim"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Battery:
    level: int
    charging: bool

    @classmethod
    def from_report(cls, report: dict) -> "Battery | None":
        level = report.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
            return None
        return cls(level, bool(report.get("charging", False)))


@dataclass(frozen=True)
class Status:
    connected: bool
    device: str
    stats: Stats
    streaming: bool
    camera_in_use: bool
    # Lo que la cámara del celular dijo que puede hacer con lo pedido; 0 si todavía no avisó.
    phone_fps: int = 0
    controls: PhoneControls | None = None
    battery: Battery | None = None
    # Con un avatar prendido: ya se ve, o todavía se está dibujando.
    avatar_ready: bool = False
    avatar_failed: bool = False


class CameraChannel:
    """Una cámara virtual, sus ajustes y el celular que le manda imagen."""

    def __init__(self, index: int, core: "Core", settings: CameraSettings, camera: Output) -> None:
        self.index = index
        self.name = CAMERA_NAMES[index]
        self.settings = settings
        self.camera = camera
        self._core = core
        self._pipeline: Pipeline | None = None
        self._pipeline_lock = threading.Lock()
        self._last_frame = 0.0
        self._connected_at: float | None = None
        self._phone_fps = 0
        self._controls: PhoneControls | None = None
        self._battery: Battery | None = None
        self._last_report: tuple | None = None
        self._placeholder = np.empty(0)
        self._placeholder_sent = False
        self._render_placeholder(camera.size)
        self._runner: AvatarRunner | None = None
        self._avatar_lock = threading.Lock()
        # Con avatar: lo último que se mandó a la cámara, y el avatar chico que va sobre el video.
        self._shown: np.ndarray | None = None
        self._overlay: np.ndarray | None = None

    @property
    def session(self) -> Session | None:
        return self._core.server.current(self.index)

    # Lo que recibe el video del celular

    @property
    def size(self) -> tuple[int, int]:
        return self.camera.size

    def send(self, image: np.ndarray) -> None:
        mode = self.settings.avatar_mode
        if mode == "solo":
            # El celular puede seguir conectado, pero la cámara muestra solo el avatar.
            return
        if mode == "sobre" and self._overlay is not None:
            image = self._with_overlay(image)
            self._shown = image
        self.camera.send(image)

    def _with_overlay(self, image: np.ndarray) -> np.ndarray:
        overlay = self._overlay
        height, width = overlay.shape[:2]
        out_h, out_w = image.shape[:2]
        if height > out_h or width > out_w:
            return image
        margin = out_h // 40
        corner = self.settings.avatar_corner
        x = margin if corner.endswith("izq") else out_w - width - margin
        y = margin if corner.startswith("arriba") else out_h - height - margin
        result = image.copy()
        region = result[y : y + height, x : x + width]
        alpha = overlay[..., 3:4].astype(np.uint16)
        region[:] = ((overlay[..., :3].astype(np.uint16) * alpha + region.astype(np.uint16) * (255 - alpha) + 127) // 255).astype(np.uint8)
        return result

    # El avatar

    def apply_avatar(self) -> None:
        """Prende, apaga o cambia el avatar según los ajustes. Dibujarlo sigue en otro hilo."""
        with self._avatar_lock:
            self._apply_avatar()

    def _apply_avatar(self) -> None:
        self._stop_avatar()
        s = self.settings
        width, height = s.size
        channels = 4 if s.avatar_mode == "solo" and s.avatar_background == "transparente" else 3
        if channels != getattr(self.camera, "channels", 3):
            reopen = getattr(self.camera, "open", None)
            if reopen:
                # Lo transparente arranca vacío; lo demás, con la imagen de siempre hasta que el avatar esté listo.
                image = None if channels == 4 else self._image_for_new_format(width, height)
                reopen(width, height, s.fps, image=image, channels=channels)
        if s.avatar_mode == "solo":
            self._start_runner(s.size, s.avatar_background)
        elif s.avatar_mode == "sobre":
            scale = s.avatar_size / 100
            small = (int(width * scale) & ~1, int(height * scale) & ~1)
            self._start_runner(small, "transparente")
        self._core.save_settings()

    def update_avatar_inputs(self) -> None:
        if self._runner:
            self._runner.set_inputs(self.settings.avatar_keys, self.settings.avatar_voice)
        self._core.save_settings()

    def _start_runner(self, size: tuple[int, int], background: str) -> None:
        s = self.settings
        # La ventana cambia el ajuste un momento antes de que este avatar se detenga:
        # lo que dibuja sigue siendo para el modo con el que se creó.
        over_video = s.avatar_mode == "sobre"
        self._runner = AvatarRunner(
            s.avatar_style,
            s.avatar_id,
            size,
            background,
            self._core.sensors(),
            lambda frame: self._on_avatar_frame(frame, over_video),
            s.avatar_keys,
            s.avatar_voice,
            self.name,
        )
        self._runner.start()

    def _stop_avatar(self) -> None:
        runner, self._runner = self._runner, None
        if runner:
            runner.stop()
        self._overlay = None
        self._shown = None

    def _on_avatar_frame(self, frame: np.ndarray, over_video: bool) -> None:
        if not over_video:
            self._shown = frame
            self.camera.send(frame)
            return
        self._overlay = frame
        # Sin imagen del celular, el avatar va sobre la imagen de espera.
        if time.monotonic() - self._last_frame >= STALE_AFTER:
            image = self._with_overlay(self._placeholder)
            self._shown = image
            self.camera.send(image)

    # Ajustes que el usuario cambia en la ventana

    def apply_view(self) -> None:
        with self._pipeline_lock:
            if self._pipeline:
                self._pipeline.set_view(self._view())
        self._core.save_settings()

    def set_quality(self, quality: str) -> None:
        self.settings.quality = quality
        self._apply_format()

    def set_fps(self, fps: int) -> None:
        self.settings.fps = fps
        self._apply_format()

    def _apply_format(self) -> None:
        """Las apps que tienen la cámara abierta siguen viéndola, sin que nadie tenga que volver a elegirla."""
        width, height = self.settings.size
        self._render_placeholder((width, height))
        reopen = getattr(self.camera, "open", None)
        if reopen:
            channels = getattr(self.camera, "channels", 3)
            image = None if channels == 4 else self._image_for_new_format(width, height)
            if channels == 3:
                reopen(width, height, self.settings.fps, image=image)
            else:
                reopen(width, height, self.settings.fps, image=image, channels=channels)
        if self.settings.avatar_mode != "apagado":
            # Se vuelve a dibujar en el tamaño nuevo, sin trabar la ventana.
            threading.Thread(target=self.apply_avatar, name="celcam-avatar-cambio", daemon=True).start()
        session = self.session
        if session:
            session.send_control(self.stream_config().as_command())
        self._core.save_settings()

    def _image_for_new_format(self, width: int, height: int) -> np.ndarray:
        with self._pipeline_lock:
            pipeline = self._pipeline
        image = pipeline.latest_image() if pipeline else None
        if image is None or time.monotonic() - self._last_frame >= HOLD_LAST_IMAGE:
            return self._placeholder
        return resize_image(image, width, height)

    def _view(self) -> View:
        s = self.settings
        return View(mirror=s.mirror, extra_turns=s.extra_turns, fill=s.fill)

    def stream_config(self) -> StreamConfig:
        width, height = self.settings.size
        return StreamConfig(width, height, self.settings.fps, self.settings.kbps)

    # Lo mismo que se maneja desde el celular

    def _send_to_phone(self, command: dict) -> None:
        session = self.session
        if session:
            session.send_control(command)

    def switch_camera(self) -> None:
        self._send_to_phone({"cmd": "camera"})

    def set_torch(self, on: bool) -> None:
        self._send_to_phone({"cmd": "torch", "on": on})

    def set_zoom(self, value: float) -> None:
        # Al arrastrar salen muchos: solo importa el último.
        session = self.session
        if session:
            session.send_latest("zoom", {"cmd": "zoom", "value": round(value, 2)})

    def set_dim(self, on: bool) -> None:
        self._send_to_phone({"cmd": "dim", "on": on})

    def stop_phone(self) -> None:
        self._send_to_phone({"cmd": "stop"})

    # Estado para la ventana

    def status(self) -> Status:
        session = self.session
        with self._pipeline_lock:
            stats = self._pipeline.stats() if self._pipeline else Stats()
        streaming = session is not None and time.monotonic() - self._last_frame < STALE_AFTER
        in_use = getattr(self.camera, "in_use", lambda: False)()
        runner = self._runner
        return Status(
            session is not None,
            session.device if session else "",
            stats,
            streaming,
            in_use,
            self._phone_fps,
            self._controls if session else None,
            self._battery if session else None,
            bool(runner and runner.ready.is_set()),
            bool(runner and runner.failed),
        )

    def latest_image(self) -> np.ndarray | None:
        if self.settings.avatar_mode != "apagado" and self._shown is not None:
            return self._shown
        with self._pipeline_lock:
            pipeline = self._pipeline
        if pipeline is None or time.monotonic() - self._last_frame >= STALE_AFTER:
            return None
        return pipeline.latest_image()

    # Sesión

    def on_connected(self, session: Session) -> None:
        self._phone_fps = 0
        self._controls = None
        self._battery = None
        self._last_report = None
        self._connected_at = time.monotonic()
        with self._pipeline_lock:
            self._pipeline = Pipeline(self, self._view())
        session.send_control({"cmd": "key"})

    def on_capture(self, session: Session, report: dict) -> None:
        if self.session is not session:
            return
        fps = report.get("fps")
        if isinstance(fps, int) and fps > 0:
            self._phone_fps = fps
        self._controls = PhoneControls.from_report(report)
        # Mover el zoom también manda este informe: al registro va solo si cambió lo que importa ahí.
        summary = (report.get("w"), report.get("h"), fps)
        if summary != self._last_report:
            self._last_report = summary
            log.info(
                "%s captura %sx%s a %s cuadros/s; admite: %s",
                session.device,
                report.get("w"),
                report.get("h"),
                fps,
                str(report.get("rates", ""))[:400],
            )

    def on_battery(self, session: Session, report: dict) -> None:
        battery = Battery.from_report(report)
        if battery and self.session is session:
            self._battery = battery

    def on_video(self, session: Session, frame: VideoFrame) -> None:
        with self._pipeline_lock:
            pipeline = self._pipeline
        if pipeline is None or self.session is not session:
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
        if self._connected_at is not None:
            log.info("Primera imagen a los %d ms de conectar", (self._last_frame - self._connected_at) * 1000)
            self._connected_at = None

    def on_disconnected(self, session: Session) -> None:
        # Si el celular cortó a propósito, su última imagen no queda a la vista.
        # Si falló la red, se está por reconectar: mejor que no se note.
        if self.session is None and session.closed_by_phone:
            self._last_frame = 0.0

    # Imagen de espera

    def _render_placeholder(self, size: tuple[int, int]) -> None:
        self._placeholder = make_placeholder(size[0], size[1], self.name, "Esperando al celular…")

    def tick_placeholder(self) -> None:
        if self.settings.avatar_mode != "apagado" or time.monotonic() - self._last_frame < HOLD_LAST_IMAGE:
            return
        # Si nadie está mirando, con una vez alcanza: la imagen queda guardada en la cámara.
        in_use = getattr(self.camera, "in_use", None)
        if in_use is None or in_use() or not self._placeholder_sent:
            self.camera.send(self._placeholder)
            self._placeholder_sent = True


class Core(Listener):
    def __init__(self, settings: Settings, identity: Identity, cameras: Sequence[Output], settings_path: Path | None = None) -> None:
        self.settings = settings
        self.identity = identity
        self._settings_path = settings_path
        self._stopping = threading.Event()
        self._place_lock = threading.Lock()
        self.channels = [CameraChannel(i, self, settings.cameras[i], camera) for i, camera in enumerate(cameras)]
        self._sensors: Sensors | None = None
        self.adb = Adb(paths.adb_exe())
        self.server = StreamServer(identity, self, self._stream_config)

    # Arranque y apagado

    def sensors(self) -> Sensors:
        # Recién cuando hace falta: sin avatares, CelCam ni mira el teclado.
        if self._sensors is None:
            self._sensors = Sensors()
        return self._sensors

    def start(self) -> None:
        self.server.start()
        threading.Thread(target=self._placeholder_loop, name="celcam-espera", daemon=True).start()
        for channel in self.channels:
            if channel.settings.avatar_mode != "apagado":
                channel.apply_avatar()

    def stop(self) -> None:
        self._stopping.set()
        self.server.stop()
        self.adb.shutdown()
        for channel in self.channels:
            channel._stop_avatar()
            close = getattr(channel.camera, "close", None)
            if close:
                close()

    # Enlaces

    def link(self) -> str:
        return self.identity.link(local_hosts() or ["127.0.0.1"], self.server.port)

    def usb_link(self) -> str:
        return self.identity.link(["127.0.0.1"], self.server.port)

    def connect_usb(self) -> list[str]:
        if not self.adb.available():
            self.adb.download()
        return self.adb.connect(self.server.port, self.usb_link(), paths.apk_file())

    def save_settings(self) -> None:
        if self._settings_path:
            self.settings.save(self._settings_path)

    def _stream_config(self, slot: int) -> StreamConfig:
        return self.channels[slot].stream_config()

    def _placeholder_loop(self) -> None:
        while not self._stopping.wait(PLACEHOLDER_INTERVAL):
            for channel in self.channels:
                channel.tick_placeholder()

    # Qué cámara le toca a cada celular

    def place(self, phone_id: str, device: str) -> int | None:
        with self._place_lock:
            phones = self.settings.phones
            slot = phones.pop(phone_id, None)
            if slot is None:
                slot = self._free_slot()
                if slot is None:
                    return None
                log.info("%s va a %s", device, CAMERA_NAMES[slot])
            # Queda último: es el que más tarda en ceder su lugar.
            phones[phone_id] = slot
            self.save_settings()
            return slot

    def _free_slot(self) -> int | None:
        phones = self.settings.phones
        taken = set(phones.values())
        for channel in self.channels:
            if channel.index not in taken:
                return channel.index
        # Todas tienen dueño: la del que hace más tiempo que no aparece, si no está conectado.
        for phone, slot in list(phones.items()):
            if slot < len(self.channels) and self.server.current(slot) is None:
                del phones[phone]
                return slot
        return None

    # Sesiones: cada una va al canal de su cámara

    def on_connected(self, session: Session) -> None:
        self.channels[session.slot].on_connected(session)

    def on_capture(self, session: Session, report: dict) -> None:
        self.channels[session.slot].on_capture(session, report)

    def on_battery(self, session: Session, report: dict) -> None:
        self.channels[session.slot].on_battery(session, report)

    def on_video(self, session: Session, frame: VideoFrame) -> None:
        self.channels[session.slot].on_video(session, frame)

    def on_disconnected(self, session: Session) -> None:
        self.channels[session.slot].on_disconnected(session)
