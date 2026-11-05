from __future__ import annotations

import hashlib
import socket
import ssl
import time

import numpy as np
import pytest

from celcam import core as core_module
from celcam.core import Core, resize_image
from celcam.protocol import ConnectionClosed
from celcam.server import Listener, StreamConfig, StreamServer
from celcam.settings import Settings
from conftest import FakeCamera, FakePhone, encode_h264, wait_for


class Recorder(Listener):
    def __init__(self) -> None:
        self.connected = []
        self.disconnected = []
        self.frames = []

    def on_connected(self, session) -> None:
        self.connected.append(session)

    def on_video(self, session, frame) -> None:
        self.frames.append(frame)

    def on_disconnected(self, session) -> None:
        self.disconnected.append(session)


@pytest.fixture
def server(identity):
    recorder = Recorder()
    srv = StreamServer(identity, recorder, lambda: StreamConfig(1280, 720, 30, 5000), port=0, host="127.0.0.1")
    srv.start()
    srv.recorder = recorder
    yield srv
    srv.stop()


def test_codigo_correcto_recibe_configuracion(server, identity):
    phone = FakePhone(server.port, identity.token)
    assert phone.reply["ok"] is True
    assert (phone.reply["w"], phone.reply["h"], phone.reply["fps"], phone.reply["kbps"]) == (1280, 720, 30, 5000)
    assert wait_for(lambda: server.recorder.connected)
    assert server.recorder.connected[0].device == "Prueba"
    phone.close()


def test_codigo_incorrecto_es_rechazado(server):
    phone = FakePhone(server.port, "codigo-que-no-es-el-correcto")
    assert phone.reply == {"ok": False, "error": "codigo"}
    assert not server.recorder.connected
    assert server.current() is None
    phone.close()


def test_el_certificado_es_el_del_qr(server, identity):
    phone = FakePhone(server.port, identity.token)
    assert hashlib.sha256(phone.peer_der).hexdigest() == identity.fingerprint
    phone.close()


def test_conexion_sin_tls_no_cuelga_el_servidor(server, identity):
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as plain:
        plain.sendall(b"GET / HTTP/1.1\r\n\r\n")
        plain.settimeout(5)
        try:
            plain.recv(100)
        except OSError:
            pass
    phone = FakePhone(server.port, identity.token)
    assert phone.reply["ok"] is True
    phone.close()


def test_tls_sin_saludo_valido_es_rechazado(server, identity):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with ctx.wrap_socket(socket.create_connection(("127.0.0.1", server.port), timeout=5)) as tls:
        tls.sendall(b"NOPE" + b"\x00" * 16)
        tls.settimeout(5)
        assert tls.recv(10) in (b"",)
    assert server.current() is None


def test_recibe_video_y_contesta_ping(server, identity):
    phone = FakePhone(server.port, identity.token)
    phone.send_video(b"cuadro", key=True, turns=1)
    assert wait_for(lambda: server.recorder.frames)
    frame = server.recorder.frames[0]
    assert (frame.data, frame.key, frame.quarter_turns) == (b"cuadro", True, 1)
    phone.send_ping()
    assert phone.read_command() == {"cmd": "pong"}
    phone.close()


def test_comando_encolado_sale_con_el_proximo_mensaje(server, identity):
    phone = FakePhone(server.port, identity.token)
    assert wait_for(lambda: server.current() is not None)
    server.current().send_control({"cmd": "key"})
    phone.send_video(b"x")
    assert phone.read_command() == {"cmd": "key"}
    phone.close()


def test_el_celular_nuevo_reemplaza_al_anterior_y_le_avisa(server, identity):
    first = FakePhone(server.port, identity.token)
    assert wait_for(lambda: server.current() is not None)
    old_session = server.current()
    second = FakePhone(server.port, identity.token)
    assert wait_for(lambda: server.current() is not old_session)
    first.send_video(b"x")
    assert first.read_command() == {"cmd": "bye"}
    with pytest.raises((ConnectionClosed, OSError)):
        for _ in range(3):
            first.read_command()
    # Se desconectó el viejo, pero el nuevo sigue siendo el actual.
    assert wait_for(lambda: old_session in server.recorder.disconnected)
    assert server.current() is not None and server.current() is not old_session
    second.send_ping()
    assert second.read_command() == {"cmd": "pong"}
    first.close()
    second.close()


def test_desconexion_avisa_y_libera(server, identity):
    phone = FakePhone(server.port, identity.token)
    assert wait_for(lambda: server.current() is not None)
    phone.close()
    assert wait_for(lambda: server.current() is None)
    assert wait_for(lambda: server.recorder.disconnected)


def _core(identity, camera):
    core = Core(Settings(quality="480p"), identity, camera)
    core.server = StreamServer(identity, core, core._stream_config, port=0, host="127.0.0.1")
    return core


def test_de_punta_a_punta_con_imagen_de_espera(identity):
    camera = FakeCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        # Sin celular, la cámara muestra la imagen de espera.
        assert wait_for(lambda: camera.frames)
        waiting = camera.frames[-1].copy()
        assert not core.status().connected

        phone = FakePhone(core.server.port, identity.token)
        assert wait_for(lambda: core.status().connected)
        for index, packet in enumerate(encode_h264(320, 180, 10)):
            phone.send_video(packet, key=index == 0)
        assert wait_for(lambda: core.latest_image() is not None)
        frame = core.latest_image()
        assert frame.shape == (480, 856, 3)
        assert not (frame == waiting).all()
        status = core.status()
        assert status.streaming and status.device == "Prueba"

        phone.close()
        assert wait_for(lambda: not core.status().connected)
        # Sin celular, vuelve a la imagen de espera.
        camera.frames.clear()
        assert wait_for(lambda: len(camera.frames) > 0, timeout=4)
        assert (camera.frames[-1] == waiting).all()
    finally:
        core.stop()


def _stream_a_little(core, identity):
    phone = FakePhone(core.server.port, identity.token)
    assert wait_for(lambda: core.status().connected)
    for index, packet in enumerate(encode_h264(320, 180, 10)):
        phone.send_video(packet, key=index == 0)
    assert wait_for(lambda: core.latest_image() is not None)
    return phone


def test_un_corte_de_la_red_no_muestra_la_imagen_de_espera(identity, monkeypatch):
    monkeypatch.setattr(core_module, "HOLD_LAST_IMAGE", 1.5)
    camera = FakeCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        phone = _stream_a_little(core, identity)
        # Lo que hace el Wi-Fi cuando daña un dato: al descifrarlo, la conexión se cae.
        socket.socket.send(phone.sock, b"\x17\x03\x03\x00\x40" + b"\xaa" * 64)
        assert wait_for(lambda: not core.status().connected)
        camera.frames.clear()
        time.sleep(0.7)
        assert not camera.frames
        # Si no vuelve, recién ahí aparece la imagen de espera.
        assert wait_for(lambda: camera.frames, timeout=3)
        phone.close()
    finally:
        core.stop()


def test_detener_en_el_celular_saca_su_imagen_enseguida(identity):
    camera = FakeCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        phone = _stream_a_little(core, identity)
        phone.close()
        assert wait_for(lambda: not core.status().connected)
        camera.frames.clear()
        assert wait_for(lambda: camera.frames, timeout=1)
    finally:
        core.stop()


class ChangingCamera(FakeCamera):
    def open(self, width, height, fps, image=None):
        self.size = (width, height)
        self.opened = (width, height, fps, image)


def test_cambiar_de_calidad_conserva_la_imagen_actual(identity):
    camera = ChangingCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        phone = _stream_a_little(core, identity)
        current = core.latest_image()
        core.set_quality("720p")
        width, height, fps, image = camera.opened
        assert (width, height, fps) == (1280, 720, 30)
        assert image.shape == (720, 1280, 3)
        assert (image == resize_image(current, 1280, 720)).all()
        phone.close()
    finally:
        core.stop()


def test_cambiar_de_calidad_sin_celular_muestra_la_imagen_de_espera(identity):
    camera = ChangingCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        core.set_fps(60)
        width, height, fps, image = camera.opened
        assert (width, height, fps) == (856, 480, 60)
        assert (image == core_module.make_placeholder(856, 480, "CelCam", "Esperando al celular…")).all()
    finally:
        core.stop()


def test_resize_image_mantiene_cada_esquina():
    image = np.zeros((4, 8, 3), dtype=np.uint8)
    image[:2, :4] = (1, 1, 1)
    image[2:, 4:] = (2, 2, 2)
    small = resize_image(image, 4, 2)
    assert small.shape == (2, 4, 3)
    assert (small[0, 0] == 1).all() and (small[-1, -1] == 2).all()


def test_datos_danados_no_cortan_la_sesion(identity):
    camera = FakeCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        phone = FakePhone(core.server.port, identity.token)
        assert wait_for(lambda: core.status().connected)
        phone.send_video(b"\x00\x00\x00\x01" + b"\xff" * 500)
        time.sleep(0.1)
        phone.send_ping()
        assert wait_for(lambda: core.status().connected)
        for index, packet in enumerate(encode_h264(320, 180, 5)):
            phone.send_video(packet, key=index == 0)
        assert wait_for(lambda: core.latest_image() is not None)
        phone.close()
    finally:
        core.stop()


def test_cambio_de_cuadros_avisa_al_celular_con_mas_calidad_de_video(identity):
    camera = FakeCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        phone = FakePhone(core.server.port, identity.token)
        assert wait_for(lambda: core.status().connected)
        core.set_fps(60)
        seen = []
        for _ in range(3):
            phone.send_ping()
            seen.append(phone.read_command())
            if any(cmd.get("cmd") == "config" for cmd in seen):
                break
        config = next(cmd for cmd in seen if cmd.get("cmd") == "config")
        assert (config["fps"], config["kbps"]) == (60, 3750)
        phone.close()
    finally:
        core.stop()


def test_el_celular_informa_lo_que_su_camara_puede(identity):
    camera = FakeCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        phone = FakePhone(core.server.port, identity.token)
        assert wait_for(lambda: core.status().connected)
        assert core.status().phone_fps == 0
        phone.send_control({"cmd": "capture", "w": 1280, "h": 720, "fps": 30, "rates": "normal 15-30 30-30"})
        assert wait_for(lambda: core.status().phone_fps == 30)
        phone.send_control({"cmd": "capture", "fps": "muchos"})
        phone.send_ping()
        assert phone.read_command().get("cmd") in {"key", "pong"}
        assert core.status().phone_fps == 30
        phone.close()
        assert wait_for(lambda: not core.status().connected)
        # Un celular nuevo empieza sin dato: no se le atribuye lo que informó el anterior.
        other = FakePhone(core.server.port, identity.token)
        assert wait_for(lambda: core.status().connected)
        assert core.status().phone_fps == 0
        other.close()
    finally:
        core.stop()


def test_cambio_de_calidad_avisa_al_celular(identity):
    camera = FakeCamera(856, 480)
    core = _core(identity, camera)
    core.start()
    try:
        phone = FakePhone(core.server.port, identity.token)
        assert wait_for(lambda: core.status().connected)
        core.set_quality("720p")
        seen = []
        for _ in range(3):
            phone.send_ping()
            seen.append(phone.read_command())
            if any(cmd.get("cmd") == "config" for cmd in seen):
                break
        config = next(cmd for cmd in seen if cmd.get("cmd") == "config")
        assert (config["w"], config["h"], config["kbps"]) == (1280, 720, 5000)
        phone.close()
    finally:
        core.stop()
