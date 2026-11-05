"""De H.264 a la imagen final: decodifica, gira, escala y encaja en el tamaño de la cámara."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import av
import numpy as np

log = logging.getLogger(__name__)


class Output(Protocol):
    @property
    def size(self) -> tuple[int, int]: ...
    def send(self, image: np.ndarray) -> None: ...


@dataclass(frozen=True)
class View:
    mirror: bool = False
    extra_turns: int = 0
    fill: bool = False


@dataclass(frozen=True)
class Stats:
    fps: float = 0.0
    mbps: float = 0.0
    source: tuple[int, int] = (0, 0)


def placement(src_w: int, src_h: int, out_w: int, out_h: int, fill: bool) -> tuple[int, int]:
    """Tamaño de la imagen ya girada: entera con bordes, o recortada para llenar."""
    scale = max(out_w / src_w, out_h / src_h) if fill else min(out_w / src_w, out_h / src_h)
    # swscale y el H.264 trabajan mejor con tamaños pares.
    width = max(2, int(round(src_w * scale)) & ~1)
    height = max(2, int(round(src_h * scale)) & ~1)
    return width, height


def compose(image: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    """Centra la imagen en un lienzo negro del tamaño de salida, recortando lo que sobre."""
    canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    img_h, img_w = image.shape[:2]
    copy_w, copy_h = min(img_w, out_w), min(img_h, out_h)
    src_x, src_y = (img_w - copy_w) // 2, (img_h - copy_h) // 2
    dst_x, dst_y = (out_w - copy_w) // 2, (out_h - copy_h) // 2
    canvas[dst_y : dst_y + copy_h, dst_x : dst_x + copy_w] = image[src_y : src_y + copy_h, src_x : src_x + copy_w]
    return canvas


class Decoder:
    def __init__(self) -> None:
        self._ctx = av.CodecContext.create("h264", "r")
        # Sin hilos por cuadro: agregarían varios cuadros de atraso.
        self._ctx.thread_type = "SLICE"
        self._ctx.flags |= av.codec.context.Flags.low_delay

    def decode(self, data: bytes) -> list[av.VideoFrame]:
        return self._ctx.decode(av.Packet(data))


class Pipeline:
    """Procesa los cuadros de un celular. Lo usa un solo hilo por sesión."""

    def __init__(self, output: Output, view: View | None = None) -> None:
        self._output = output
        self._view = view or View()
        self._decoder = Decoder()
        self._latest: np.ndarray | None = None
        self._latest_lock = threading.Lock()
        self._frames = 0
        self._bytes = 0
        self._window_start = time.monotonic()
        self._stats = Stats()
        self._source = (0, 0)

    def set_view(self, view: View) -> None:
        self._view = view

    def reset_decoder(self) -> None:
        self._decoder = Decoder()

    def feed(self, data: bytes, quarter_turns: int) -> None:
        self._bytes += len(data)
        for frame in self._decoder.decode(data):
            self._source = (frame.width, frame.height)
            image = self.render(frame, quarter_turns)
            self._output.send(image)
            with self._latest_lock:
                self._latest = image
            self._frames += 1
        self._update_stats()

    def render(self, frame: av.VideoFrame, quarter_turns: int) -> np.ndarray:
        view = self._view
        out_w, out_h = self._output.size
        turns = (quarter_turns + view.extra_turns) % 4
        sideways = turns % 2 == 1
        upright = (frame.height, frame.width) if sideways else (frame.width, frame.height)
        target_w, target_h = placement(upright[0], upright[1], out_w, out_h, view.fill)
        scale_w, scale_h = (target_h, target_w) if sideways else (target_w, target_h)
        image = frame.reformat(width=scale_w, height=scale_h, format="bgr24").to_ndarray()
        if turns:
            # rot90 gira contra el reloj; el giro pedido es a favor del reloj.
            image = np.rot90(image, k=-turns)
        if view.mirror:
            image = image[:, ::-1]
        return compose(image, out_w, out_h)

    def latest_image(self) -> np.ndarray | None:
        with self._latest_lock:
            return self._latest

    def stats(self) -> Stats:
        return self._stats

    def _update_stats(self) -> None:
        now = time.monotonic()
        elapsed = now - self._window_start
        if elapsed >= 1.0:
            self._stats = Stats(self._frames / elapsed, self._bytes * 8 / elapsed / 1_000_000, self._source)
            self._frames = 0
            self._bytes = 0
            self._window_start = now
