from __future__ import annotations

import numpy as np

from celcam.core import make_placeholder
from celcam.pipeline import Pipeline, View, compose, placement
from conftest import FakeCamera, encode_h264


def _dominant_bgr(image: np.ndarray) -> tuple[int, int, int]:
    return tuple(int(v) for v in image.reshape(-1, 3).mean(axis=0))


def _decode_all(pipeline: Pipeline, packets: list[bytes], turns: int = 0) -> None:
    for packet in packets:
        pipeline.feed(packet, turns)


def test_encaje_con_bordes_y_recorte():
    assert placement(1280, 720, 1280, 720, fill=False) == (1280, 720)
    assert placement(720, 1280, 1280, 720, fill=False) == (404, 720)
    assert placement(720, 1280, 1280, 720, fill=True) == (1280, 2276)


def test_compose_centra_y_recorta():
    tall = np.full((100, 20, 3), 255, dtype=np.uint8)
    canvas = compose(tall, 40, 40)
    assert canvas.shape == (40, 40, 3)
    assert canvas[20, 20, 0] == 255 and canvas[20, 0, 0] == 0


def test_decodifica_y_entrega_el_tamano_de_la_camara():
    camera = FakeCamera(640, 360)
    pipeline = Pipeline(camera)
    _decode_all(pipeline, encode_h264(320, 180, 5))
    assert len(camera.frames) >= 4
    assert all(frame.shape == (360, 640, 3) for frame in camera.frames)
    # Verde dominante: en BGR el canal del medio es el más alto.
    blue, green, red = _dominant_bgr(camera.frames[-1])
    assert green > blue and green > red


def test_giro_a_favor_del_reloj_y_bordes():
    camera = FakeCamera(640, 360)
    pipeline = Pipeline(camera)
    _decode_all(pipeline, encode_h264(320, 180, 5), turns=1)
    frame = camera.frames[-1]
    # De costado queda angosta: bordes negros a los lados.
    assert frame[180, 5].sum() < 30
    assert frame[180, 635].sum() < 30
    # El rincón blanco (arriba a la izquierda) pasa arriba a la derecha de la imagen girada.
    usable_right = 320 + 101 - 10
    assert frame[10, usable_right].mean() > 200
    assert frame[350, 320 - 101 + 10].mean() < 200


def test_espejo_invierte_izquierda_y_derecha():
    camera = FakeCamera(640, 360)
    pipeline = Pipeline(camera, View(mirror=True))
    _decode_all(pipeline, encode_h264(320, 180, 5))
    frame = camera.frames[-1]
    assert frame[10, 630].mean() > 200
    assert frame[10, 10].mean() < 200


def test_giro_extra_se_suma_al_del_celular():
    camera = FakeCamera(640, 360)
    pipeline = Pipeline(camera, View(extra_turns=1))
    _decode_all(pipeline, encode_h264(320, 180, 5), turns=1)
    frame = camera.frames[-1]
    # Dos cuartos: el rincón blanco queda abajo a la derecha.
    assert frame[350, 630].mean() > 200
    assert frame[10, 10].mean() < 200


def test_llenar_no_deja_bordes():
    camera = FakeCamera(640, 360)
    pipeline = Pipeline(camera, View(fill=True))
    _decode_all(pipeline, encode_h264(320, 180, 5), turns=1)
    frame = camera.frames[-1]
    assert frame[180, 5].sum() > 30
    assert frame[180, 635].sum() > 30


def test_estadisticas_y_ultima_imagen():
    camera = FakeCamera(640, 360)
    pipeline = Pipeline(camera)
    _decode_all(pipeline, encode_h264(320, 180, 3))
    assert pipeline.latest_image() is not None


def test_imagen_de_espera_tiene_el_tamano_justo():
    image = make_placeholder(1280, 720, "CelCam", "Esperando al celular…")
    assert image.shape == (720, 1280, 3)
    assert image.flags["C_CONTIGUOUS"]
