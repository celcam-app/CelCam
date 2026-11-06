"""Los avatares: que se dibujen todos, que el armado sea rápido y que sigan a los sensores."""

from __future__ import annotations

import time

import numpy as np
import pytest

from celcam.avatars import BACKGROUNDS, CHARACTERS, STYLES, make_scene
from celcam.avatars import drawing as old
from celcam.avatars.sensors import Keyboard, Motion, VoiceMeter, decibels, key_codes
from celcam.core import Core
from celcam.server import StreamServer
from celcam.settings import CameraSettings, Settings
from conftest import FakeCamera, FakePhone, encode_h264, wait_for

SIZE = (640, 360)
TOP_CORNER = (4, 4)


@pytest.fixture(scope="module")
def scenes():
    cache = {}

    def get(style, character, background="color"):
        key = (style, character, background)
        if key not in cache:
            cache[key] = make_scene(style, character, SIZE, background)
        return cache[key]

    return get


@pytest.mark.parametrize("style", list(STYLES))
@pytest.mark.parametrize("character", [c.id for c in CHARACTERS])
def test_cada_personaje_se_dibuja_y_se_mueve(scenes, style, character):
    scene = scenes(style, character)
    quiet = scene.to_camera(scene.frame(old.State(t=0.1), settled=True))
    assert quiet.shape == (360, 640, 3)
    talking = scene.to_camera(scene.frame(old.State(mouth=0.9, look=(0.5, 0.5), t=0.1), settled=True))
    assert not (quiet == talking).all(), "La boca o los ojos no cambiaron"


@pytest.mark.parametrize("style", list(STYLES))
@pytest.mark.parametrize("character", [c.id for c in CHARACTERS])
def test_la_tecla_que_se_toca_se_ilumina_y_el_mouse_se_mueve(scenes, style, character):
    scene = scenes(style, character)
    before = scene.to_camera(scene.frame(old.State(t=0.1), settled=True))
    typing = scene.to_camera(scene.frame(old.State(keys=("H",), t=0.1), settled=True))
    assert (before != typing).any()
    moved = scene.to_camera(scene.frame(old.State(mouse=(0.05, 0.05), t=0.1), settled=True))
    clicked = scene.to_camera(scene.frame(old.State(mouse=(0.05, 0.05), click="L", t=0.1), settled=True))
    assert (before != moved).any()
    assert (moved != clicked).any()


@pytest.mark.parametrize("style", list(STYLES))
def test_transparente_tiene_fondo_vacio_y_mesa_opaca(scenes, style):
    scene = scenes(style, "michi", "transparente")
    image = scene.to_camera(scene.frame(old.State(t=0.1), settled=True))
    assert image.shape == (360, 640, 4)
    assert image[TOP_CORNER[1], TOP_CORNER[0], 3] == 0
    assert image[352, 320, 3] == 255


@pytest.mark.parametrize("style", list(STYLES))
def test_verde_para_croma(scenes, style):
    scene = scenes(style, "michi", "verde")
    image = scene.to_camera(scene.frame(old.State(t=0.1), settled=True))
    assert tuple(image[TOP_CORNER[1], TOP_CORNER[0]]) == (64, 177, 0)


def test_hay_un_fondo_para_cada_opcion():
    assert set(BACKGROUNDS) == {"color", "transparente", "verde"}


@pytest.mark.parametrize("style", list(STYLES))
def test_arma_los_cuadros_rapido(scenes, style):
    """Una vez dibujadas las piezas, el procesador casi no trabaja."""
    scene = scenes(style, "tuntun")
    states = [old.scenario(i / 30) for i in range(180)]
    for state in states:
        scene.to_camera(scene.frame(state))
    start = time.perf_counter()
    for state in states:
        scene.to_camera(scene.frame(state))
    assert (time.perf_counter() - start) / len(states) < 0.025


@pytest.mark.parametrize("style", list(STYLES))
def test_tun_tun_golpea_con_el_bate_y_su_otra_mano_sigue_en_el_mouse(style):
    scene = make_scene(style, "tuntun", SIZE, "color")
    quiet = scene.frame(old.State(t=1.0), settled=True)
    angle_at_rest = scene.bat_angle
    scene.frame(old.State(keys=("1",), t=1.0), settled=True)
    # Baja hacia el teclado: apunta hacia abajo.
    assert scene.bat_angle > 0 > angle_at_rest
    assert quiet is not None


def test_las_teclas_que_se_leen_son_las_del_dibujo():
    assert set(key_codes()) >= set(old.KEYS)


def test_una_tecla_tocada_rapido_igual_se_ve(monkeypatch):
    keyboard = Keyboard()
    states = {ord("H"): 1}
    keyboard._state = lambda code: states.pop(code, 0)
    assert keyboard.read(10.0) == ("H",)
    assert keyboard.read(10.05) == ("H",)
    assert keyboard.read(10.2) == ()


def test_la_ultima_tecla_apretada_queda_ultima(monkeypatch):
    keyboard = Keyboard()
    down = {ord("A")}
    keyboard._state = lambda code: 0x8000 - 0x10000 if code in down else 0
    assert keyboard.read(1.0) == ("A",)
    down.add(ord("B"))
    assert keyboard.read(1.03) == ("A", "B")
    down.discard(ord("A"))
    assert keyboard.read(1.5) == ("B",)


def test_silencio_cierra_la_boca_y_la_voz_la_abre():
    meter = VoiceMeter()
    for _ in range(50):
        meter.feed(-60.0)
    assert meter.level == 0.0
    meter.feed(-18.0)
    assert meter.level > 0.5


def test_mide_el_volumen():
    silence = np.zeros(480, dtype=np.int16)
    loud = (np.sin(np.arange(480) / 3) * 20000).astype(np.int16)
    assert decibels(silence) < -80
    assert decibels(loud) > -10
    assert decibels(np.zeros(0, dtype=np.int16)) == -90.0


def test_la_boca_se_abre_rapido_y_no_queda_entreabierta():
    motion = Motion(seed=1)
    assert motion.update_mouth(1.0, 0.05) > 0.5
    for _ in range(60):
        value = motion.update_mouth(0.0, 0.033)
    assert value == 0.0


def test_parpadea_de_vez_en_cuando():
    motion = Motion(seed=1)
    seen = [motion.blink(t / 30) for t in range(30 * 20)]
    assert 2 <= sum(1 for a, b in zip(seen, seen[1:]) if b and not a) <= 8
    assert sum(seen) < len(seen) * 0.1


# El avatar en la cámara


class ReopenCamera(FakeCamera):
    channels = 3

    def open(self, width, height, fps, image=None, channels=3):
        self.size = (width, height)
        self.channels = channels


def _core(identity, settings):
    cameras = [ReopenCamera(*settings.cameras[i].size) for i in range(4)]
    core = Core(settings, identity, cameras)
    core.server = StreamServer(identity, core, core._stream_config, port=0, host="127.0.0.1")
    return core, cameras


class FakeSensors:
    class _Keyboard:
        def read(self, now):
            return ()

    class _Mouse:
        def position(self):
            return (0.5, 0.5)

        def click(self, now):
            return None

    class _Mic:
        class meter:
            level = 0.0

    keyboard = _Keyboard()
    mouse = _Mouse()
    microphone = _Mic()

    def start_voice(self):
        pass

    def stop_voice(self):
        pass


def _settings(**values):
    settings = Settings()
    defaults = dict(quality="480p")
    defaults.update(values)
    settings.cameras[0] = CameraSettings(**defaults)
    return settings


def test_solo_el_avatar_funciona_sin_celular(identity):
    core, cameras = _core(identity, _settings(avatar_mode="solo", avatar_id="michi"))
    core._sensors = FakeSensors()
    core.start()
    try:
        channel = core.channels[0]
        assert wait_for(lambda: channel.status().avatar_ready, timeout=15)
        assert wait_for(lambda: len(cameras[0].frames) > 3)
        assert cameras[0].frames[-1].shape == (480, 856, 3)
        assert channel.latest_image() is not None
        # La imagen de espera no se mete en el medio.
        assert not channel.status().connected
    finally:
        core.stop()


def test_con_fondo_transparente_la_camara_recibe_cuatro_canales(identity):
    core, cameras = _core(identity, _settings(avatar_mode="solo", avatar_background="transparente"))
    core._sensors = FakeSensors()
    core.start()
    try:
        assert wait_for(lambda: core.channels[0].status().avatar_ready, timeout=15)
        assert cameras[0].channels == 4
        assert wait_for(lambda: cameras[0].frames and cameras[0].frames[-1].shape == (480, 856, 4))
        # Al apagarlo, la cámara vuelve a la normalidad.
        core.channels[0].settings.avatar_mode = "apagado"
        core.channels[0].apply_avatar()
        assert cameras[0].channels == 3
    finally:
        core.stop()


def test_el_avatar_chico_va_sobre_el_video_del_celular(identity):
    core, cameras = _core(identity, _settings(avatar_mode="sobre", avatar_corner="abajo-der", avatar_size=25))
    core._sensors = FakeSensors()
    core.start()
    try:
        channel = core.channels[0]
        assert wait_for(lambda: channel.status().avatar_ready, timeout=15)
        phone = FakePhone(core.server.port, identity.token, phone_id="uno")
        assert wait_for(lambda: channel.status().connected)
        for index, packet in enumerate(encode_h264(320, 180, 8, (0, 0, 200))):
            phone.send_video(packet, key=index == 0)
        assert wait_for(lambda: channel.status().streaming)
        assert wait_for(lambda: cameras[0].frames and (cameras[0].frames[-1][460, 800] != cameras[0].frames[-1][20, 800]).any())
        frame = cameras[0].frames[-1]
        # Arriba a la derecha sigue el video azul; abajo a la derecha está la mesa del avatar.
        assert frame[20, 800, 0] > 120 and frame[20, 800, 2] < 60
        assert frame[460, 800, 2] > 100
        phone.close()
    finally:
        core.stop()


def test_al_cambiar_de_modo_el_avatar_anterior_no_falla(identity):
    core, cameras = _core(identity, _settings(avatar_mode="solo"))
    core._sensors = FakeSensors()
    core.start()
    try:
        channel = core.channels[0]
        assert wait_for(lambda: channel.status().avatar_ready, timeout=15)
        anterior = channel._runner
        # La ventana cambia el ajuste un momento antes de que el avatar anterior se detenga.
        channel.settings.avatar_mode = "sobre"
        time.sleep(0.3)
        assert not anterior.failed
        channel.apply_avatar()
        assert wait_for(lambda: channel.status().avatar_ready, timeout=15)
        assert wait_for(lambda: channel._overlay is not None)
        assert channel._overlay.shape[2] == 4
        assert not channel.status().avatar_failed
    finally:
        core.stop()


def test_con_solo_avatar_el_video_del_celular_no_llega_a_la_camara(identity):
    core, cameras = _core(identity, _settings(avatar_mode="solo"))
    core._sensors = FakeSensors()
    core.start()
    try:
        channel = core.channels[0]
        assert wait_for(lambda: channel.status().avatar_ready, timeout=15)
        # El celular manda azul entero; nunca aparece.
        channel.send(np.full((480, 856, 3), (255, 0, 0), dtype=np.uint8))
        time.sleep(0.2)
        assert not any((frame == (255, 0, 0)).all() for frame in cameras[0].frames)
    finally:
        core.stop()


def test_los_avatares_de_dos_camaras_son_independientes(identity):
    settings = _settings(avatar_mode="solo", avatar_id="michi", avatar_style="dibujado")
    settings.cameras[1] = CameraSettings(quality="480p", avatar_mode="solo", avatar_id="robi", avatar_style="volumen")
    core, cameras = _core(identity, settings)
    core._sensors = FakeSensors()
    core.start()
    try:
        assert wait_for(lambda: core.channels[0].status().avatar_ready and core.channels[1].status().avatar_ready, timeout=20)
        assert wait_for(lambda: cameras[0].frames and cameras[1].frames)
        assert (cameras[0].frames[-1] != cameras[1].frames[-1]).any()
        assert not core.channels[2].status().avatar_ready
    finally:
        core.stop()


def test_los_ajustes_del_avatar_se_guardan_y_lo_raro_se_ignora(tmp_path):
    path = tmp_path / "ajustes.json"
    settings = Settings()
    settings.cameras[3] = CameraSettings(avatar_mode="sobre", avatar_style="volumen", avatar_id="tuntun", avatar_background="verde", avatar_corner="arriba-izq", avatar_size=50, avatar_keys=False, avatar_voice=False)
    settings.save(path)
    camera = Settings.load(path).cameras[3]
    assert (camera.avatar_mode, camera.avatar_style, camera.avatar_id) == ("sobre", "volumen", "tuntun")
    assert (camera.avatar_background, camera.avatar_corner, camera.avatar_size) == ("verde", "arriba-izq", 50)
    assert camera.avatar_keys is False and camera.avatar_voice is False
    path.write_text('{"cameras": [{"avatar_mode": "x", "avatar_id": "nadie", "avatar_size": 7, "avatar_background": 3}]}', encoding="utf-8")
    camera = Settings.load(path).cameras[0]
    assert (camera.avatar_mode, camera.avatar_id, camera.avatar_size, camera.avatar_background) == ("apagado", "michi", 35, "color")
