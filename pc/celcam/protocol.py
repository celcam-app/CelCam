"""Mensajes entre el celular y la PC. Todo viaja dentro de TLS.

Saludo:
  celular -> PC: "CCAM" + largo (uint32) + JSON {token, proto, device, app}
  PC -> celular: largo (uint32) + JSON {ok, w, h, fps, kbps, pc} o {ok: false, error}

Después, en los dos sentidos: tipo (uint8) + largo (uint32) + contenido.
  TYPE_VIDEO (celular -> PC): flags (uint8) + giro en cuartos (uint8) + pts en µs (uint64) + H.264 Annex B
  TYPE_CONTROL (los dos sentidos): JSON con "cmd"
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import BinaryIO

PROTO = 1
MAGIC = b"CCAM"
DEFAULT_PORT = 47470
MAX_HELLO = 4096
MAX_MESSAGE = 16 * 1024 * 1024

TYPE_VIDEO = 1
TYPE_CONTROL = 2

FLAG_KEY = 1

_LENGTH = struct.Struct(">I")
_HEADER = struct.Struct(">BI")
_VIDEO = struct.Struct(">BBQ")


class ProtocolError(Exception):
    """El otro lado mandó algo que no respeta el protocolo."""


class ConnectionClosed(Exception):
    """El otro lado cerró la conexión."""


@dataclass(frozen=True)
class VideoFrame:
    key: bool
    quarter_turns: int
    pts_us: int
    data: bytes


def read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if data is None or len(data) < size:
        raise ConnectionClosed()
    return data


def read_hello(stream: BinaryIO) -> dict:
    if read_exact(stream, len(MAGIC)) != MAGIC:
        raise ProtocolError("no es un celular con CelCam")
    return _read_json(stream)


def _read_json(stream: BinaryIO) -> dict:
    (size,) = _LENGTH.unpack(read_exact(stream, _LENGTH.size))
    if size > MAX_HELLO:
        raise ProtocolError("saludo demasiado largo")
    return _parse_json(read_exact(stream, size))


def _parse_json(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("JSON inválido") from exc
    if not isinstance(value, dict):
        raise ProtocolError("se esperaba un objeto JSON")
    return value


def pack_reply(reply: dict) -> bytes:
    raw = json.dumps(reply).encode("utf-8")
    return _LENGTH.pack(len(raw)) + raw


def read_message(stream: BinaryIO) -> tuple[int, bytes]:
    mtype, size = _HEADER.unpack(read_exact(stream, _HEADER.size))
    if size > MAX_MESSAGE:
        raise ProtocolError("mensaje demasiado largo")
    return mtype, read_exact(stream, size)


def pack_control(command: dict) -> bytes:
    raw = json.dumps(command).encode("utf-8")
    return _HEADER.pack(TYPE_CONTROL, len(raw)) + raw


def parse_control(payload: bytes) -> dict:
    return _parse_json(payload)


def pack_video(frame: VideoFrame) -> bytes:
    flags = FLAG_KEY if frame.key else 0
    body = _VIDEO.pack(flags, frame.quarter_turns, frame.pts_us) + frame.data
    return _HEADER.pack(TYPE_VIDEO, len(body)) + body


def parse_video(payload: bytes) -> VideoFrame:
    if len(payload) < _VIDEO.size:
        raise ProtocolError("cuadro de video incompleto")
    flags, turns, pts = _VIDEO.unpack_from(payload)
    return VideoFrame(bool(flags & FLAG_KEY), turns % 4, pts, payload[_VIDEO.size :])
