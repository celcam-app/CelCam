"""La pantalla del iPhone por AirPlay: emparejamiento, FairPlay, imagen cifrada y sonido sin repetidos.

No anuncia nada en la red: el receptor arranca sin zeroconf y se le habla directo.
"""

from __future__ import annotations

import asyncio
import hashlib
import plistlib
import socket
import struct

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from celcam import airplay, paths
from celcam.core import Core
from celcam.server import StreamServer
from celcam.settings import Settings
from conftest import FakeCamera, encode_h264, wait_for

RAW = serialization.Encoding.Raw, serialization.PublicFormat.Raw
needs_playfair = pytest.mark.skipif(not paths.playfair_dll().exists(), reason="Falta compilar celcam_playfair.dll")


def test_el_emparejamiento_es_el_de_un_apple_tv():
    ours = Ed25519PrivateKey.generate()
    pairing = airplay.Pairing(ours)
    phone_x, phone_ed = X25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    phone_x_pub, phone_ed_pub = phone_x.public_key().public_bytes(*RAW), phone_ed.public_key().public_bytes(*RAW)

    reply = pairing.verify(b"\x01\x00\x00\x00" + phone_x_pub + phone_ed_pub)
    pc_x_pub, encrypted = reply[:32], reply[32:]
    secret = phone_x.exchange(X25519PublicKey.from_public_bytes(pc_x_pub))
    key = hashlib.sha512(b"Pair-Verify-AES-Key" + secret).digest()[:16]
    iv = hashlib.sha512(b"Pair-Verify-AES-IV" + secret).digest()[:16]
    ctr = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    ours.public_key().verify(ctr.update(encrypted), pc_x_pub + phone_x_pub)

    assert pairing.verify(b"\x00\x00\x00\x00" + ctr.update(phone_ed.sign(phone_x_pub + pc_x_pub))) == b""
    assert pairing.verified and pairing.secret == secret


def test_una_firma_falsa_no_empareja():
    pairing = airplay.Pairing(Ed25519PrivateKey.generate())
    phone_x, phone_ed = X25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    pairing.verify(b"\x01\x00\x00\x00" + phone_x.public_key().public_bytes(*RAW) + phone_ed.public_key().public_bytes(*RAW))
    assert pairing.verify(b"\x00\x00\x00\x00" + bytes(64)) is None
    assert not pairing.verified


@needs_playfair
def test_fairplay_da_lo_mismo_que_la_version_probada_con_el_iphone():
    playfair = airplay.Playfair(paths.playfair_dll())
    assert playfair.decrypt(bytes(164), bytes(72)).hex() == "3ba470ef5781b366dbb21a1fa2135dbc"
    with pytest.raises(ValueError):
        playfair.decrypt(bytes(10), bytes(72))


def test_las_respuestas_fijas_de_fairplay_estan_completas():
    assert len(airplay.FP_REPLIES) == 4 and all(len(r) == 142 and r[:4] == b"FPLY" for r in airplay.FP_REPLIES)
    assert len(airplay.FP_HEADER) == 12


def test_el_sonido_repetido_suena_una_sola_vez():
    receiver = airplay.AudioReceiver(bytes(16), bytes(16), 8, None)
    # El iPhone manda cada paquete tres veces («audio redundante»), y alguno puede llegar tarde.
    seen = [s for s in (10, 10, 10, 11, 11, 12, 11, 13, 13, 14) if receiver.fresh(s)]
    assert seen == [10, 11, 12, 13, 14]
    assert receiver.fresh(0) is False
    # Después de 65535 vuelve a cero: eso sigue siendo nuevo.
    receiver = airplay.AudioReceiver(bytes(16), bytes(16), 8, None)
    assert receiver.fresh(65535) and receiver.fresh(0) and receiver.fresh(1)


def _split_annexb(data: bytes) -> list[bytes]:
    parts, i = [], 0
    starts = [j for j in range(len(data) - 3) if data[j:j + 3] == b"\x00\x00\x01"]
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(data)
        nal = data[start + 3:end]
        parts.append(nal.rstrip(b"\x00") if n + 1 < len(starts) else nal)
    return [p for p in parts if p]


def _mirror_packets(key: bytes, connection_id: int) -> list[tuple[bytes, bytes]]:
    """Lo que manda un iPhone: configuración (sin cifrar) y cuadros con largo delante de cada NAL, cifrados."""
    cid = str(connection_id & 0xFFFFFFFFFFFFFFFF).encode()
    ctr = Cipher(algorithms.AES(hashlib.sha512(b"AirPlayStreamKey" + cid + key).digest()[:16]),
                 modes.CTR(hashlib.sha512(b"AirPlayStreamIV" + cid + key).digest()[:16])).encryptor()
    packets = []
    for raw in encode_h264(640, 360, 10):
        nals = _split_annexb(raw)
        sps = next((n for n in nals if n[0] & 0x1F == 7), None)
        pps = next((n for n in nals if n[0] & 0x1F == 8), None)
        if sps and pps:
            config = bytes([1, sps[1], sps[2], sps[3], 0xFF, 0xE1]) + struct.pack(">H", len(sps)) + sps + b"\x01" + struct.pack(">H", len(pps)) + pps
            packets.append((bytes([0, 0, 0, 0, 0x01]) + bytes(123), config))
        body = b"".join(struct.pack(">I", len(n)) + n for n in nals if n[0] & 0x1F not in (7, 8, 9, 6))
        if body:
            packets.append((bytes([0, 0, 0, 0, 0x00]) + bytes(123), ctr.update(body)))
    return [(struct.pack("<I", len(p)) + h[4:], p) for h, p in packets]


def test_la_imagen_cifrada_se_descifra_y_decodifica():
    key, cid = bytes(range(16)), -8420518876395912581
    stream = airplay.MirrorStream(key, cid)
    frames = []
    for header, payload in _mirror_packets(key, cid):
        decoded, _ = stream.handle(header, payload)
        frames += decoded
    assert stream.codec == "h264"
    assert len(frames) >= 8 and (frames[0].width, frames[0].height) == (640, 360)


def test_con_la_llave_equivocada_no_muestra_basura():
    stream = airplay.MirrorStream(bytes(16), 1)
    with pytest.raises(ValueError):
        for header, payload in _mirror_packets(bytes(range(16)), 1):
            stream.handle(header, payload)


def test_la_configuracion_de_h265_se_lee_como_en_uxplay():
    vps, sps, pps = b"\x40\x01" + bytes(20), b"\x42\x01" + bytes(40), b"\x44\x01\xc1\x72"
    payload = bytearray(0x75)
    payload[4:8] = b"hvc1"
    for mark, part in ((0xA0, vps), (0xA1, sps), (0xA2, pps)):
        payload += bytes([mark, 0, 1]) + struct.pack(">H", len(part)) + part
    stream = airplay.MirrorStream(bytes(16), 1)
    stream.configure(bytes(payload))
    assert stream.codec == "hevc"
    assert stream._sps_pps == b"".join(airplay.NAL_START + p for p in (vps, sps, pps))


@needs_playfair
def test_el_iphone_va_a_su_camara_y_la_imagen_llega(identity, tmp_path):
    settings = Settings()
    for camera in settings.cameras:
        camera.quality = "480p"
    cameras = [FakeCamera(*settings.cameras[i].size) for i in range(4)]
    core = Core(settings, identity, cameras)
    core.server = StreamServer(identity, core, core._stream_config, port=0, host="127.0.0.1")
    server = airplay.AirPlayServer(core, core.server, tmp_path / "airplay_llave.pem", paths.playfair_dll(), "CelCam prueba", port=0)
    server.start(["127.0.0.1"], announce=False)
    try:
        info = plistlib.loads(server.info(None, True))
        assert info["displays"][0]["maxFPS"] == 60 and info["features"] >> 32 == 0x400

        key, cid = bytes(range(16)), 1234

        async def make() -> airplay.AirPlaySession:
            session = airplay.AirPlaySession(server, "127.0.0.1")
            await session.open_ports()
            session._aes_key = key
            session._phone_id = "airplay:prueba"
            session.device = "iPhone de prueba (pantalla)"
            reply = plistlib.loads(session._setup(plistlib.dumps({"streams": [{"type": 110, "streamConnectionID": cid}]}, fmt=plistlib.FMT_BINARY)))
            assert reply["streams"][0]["dataPort"] == session.mirror_port
            return session

        session = asyncio.run_coroutine_threadsafe(make(), server.loop).result(5)
        assert core.channels[0].status().connected
        assert core.channels[0].status().device == "iPhone de prueba (pantalla)"
        with socket.create_connection(("127.0.0.1", session.mirror_port)) as sock:
            for header, payload in _mirror_packets(key, cid):
                sock.sendall(header + payload)
            assert wait_for(lambda: len(cameras[0].frames) >= 5, timeout=10)
            assert cameras[0].frames[-1].shape == (480, 856, 3)
            assert session.latest_frame is not None
        # «Detener» desde la PC corta la duplicación.
        session.send_control({"cmd": "stop"})
        asyncio.run_coroutine_threadsafe(session.close(), server.loop).result(5)
        assert wait_for(lambda: not core.channels[0].status().connected, timeout=5)
    finally:
        server.stop()
