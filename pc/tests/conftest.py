from __future__ import annotations

import socket
import ssl
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from celcam import protocol  # noqa: E402
from celcam.identity import Identity  # noqa: E402


class FakeCamera:
    """Guarda lo que recibiría la cámara virtual."""

    def __init__(self, width: int = 640, height: int = 360) -> None:
        self.size = (width, height)
        self.frames: list[np.ndarray] = []

    def send(self, image: np.ndarray) -> None:
        self.frames.append(image.copy())


@pytest.fixture
def identity(tmp_path: Path) -> Identity:
    return Identity.load_or_create(tmp_path)


def encode_h264(width: int, height: int, count: int, color_rgb=(0, 200, 0)) -> list[bytes]:
    """Cuadros H.264 Annex B de un color, con la parte de arriba a la izquierda blanca."""
    codec = av.CodecContext.create("libx264", "w")
    codec.width = width
    codec.height = height
    codec.pix_fmt = "yuv420p"
    codec.time_base = Fraction(1, 30)
    codec.options = {"tune": "zerolatency", "preset": "ultrafast", "bf": "0"}
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = color_rgb
    image[: height // 4, : width // 4] = (255, 255, 255)
    packets: list[bytes] = []
    for index in range(count):
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        frame.pts = index
        packets.extend(bytes(p) for p in codec.encode(frame))
    packets.extend(bytes(p) for p in codec.encode(None))
    return packets


class FakePhone:
    """Se conecta como la app de Android."""

    def __init__(self, port: int, token: str, fingerprint: str | None = None, phone_id: str | None = None, device: str = "Prueba") -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.sock = ctx.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5))
        self.reader = self.sock.makefile("rb", buffering=64 * 1024)
        self.peer_der = self.sock.getpeercert(binary_form=True)
        hello = {"token": token, "proto": protocol.PROTO, "device": device}
        if phone_id:
            hello["id"] = phone_id
        raw = protocol.json.dumps(hello).encode()
        self.sock.sendall(protocol.MAGIC + protocol._LENGTH.pack(len(raw)) + raw)
        self.reply = protocol._read_json(self.reader)

    def send_video(self, data: bytes, key: bool = False, turns: int = 0) -> None:
        self.sock.sendall(protocol.pack_video(protocol.VideoFrame(key, turns, 0, data)))

    def send_ping(self) -> None:
        self.sock.sendall(protocol.pack_control({"cmd": "ping"}))

    def send_control(self, command: dict) -> None:
        self.sock.sendall(protocol.pack_control(command))

    def read_command(self) -> dict:
        mtype, payload = protocol.read_message(self.reader)
        assert mtype == protocol.TYPE_CONTROL
        return protocol.parse_control(payload)

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def in_thread(target) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread
