from __future__ import annotations

import io
import struct

import pytest

from celcam import protocol
from celcam.protocol import ConnectionClosed, ProtocolError, VideoFrame


def test_video_ida_y_vuelta():
    frame = VideoFrame(True, 3, 123456789, b"\x00\x00\x00\x01datos")
    mtype, payload = protocol.read_message(io.BytesIO(protocol.pack_video(frame)))
    assert mtype == protocol.TYPE_VIDEO
    assert protocol.parse_video(payload) == frame


def test_control_ida_y_vuelta():
    mtype, payload = protocol.read_message(io.BytesIO(protocol.pack_control({"cmd": "key"})))
    assert mtype == protocol.TYPE_CONTROL
    assert protocol.parse_control(payload) == {"cmd": "key"}


def test_saludo_con_marca_equivocada():
    with pytest.raises(ProtocolError):
        protocol.read_hello(io.BytesIO(b"HTTP" + struct.pack(">I", 2) + b"{}"))


def test_saludo_demasiado_largo():
    with pytest.raises(ProtocolError):
        protocol.read_hello(io.BytesIO(protocol.MAGIC + struct.pack(">I", protocol.MAX_HELLO + 1)))


def test_saludo_que_no_es_objeto():
    with pytest.raises(ProtocolError):
        protocol.read_hello(io.BytesIO(protocol.MAGIC + struct.pack(">I", 2) + b"[]"))


def test_mensaje_demasiado_largo_no_reserva_memoria():
    header = struct.pack(">BI", protocol.TYPE_VIDEO, protocol.MAX_MESSAGE + 1)
    with pytest.raises(ProtocolError):
        protocol.read_message(io.BytesIO(header))


def test_conexion_cortada_a_la_mitad():
    with pytest.raises(ConnectionClosed):
        protocol.read_message(io.BytesIO(b"\x01\x00"))


def test_cuadro_incompleto():
    with pytest.raises(ProtocolError):
        protocol.parse_video(b"\x00\x00")


def test_giro_se_normaliza():
    payload = struct.pack(">BBQ", 0, 7, 0) + b"x"
    assert protocol.parse_video(payload).quarter_turns == 3
