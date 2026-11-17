"""Lo que se agregó para que TikTok no se trabe ni se desfase la voz."""

from __future__ import annotations

import numpy as np

from celcam.core import DelayMeter
from celcam.pipeline import compose


def _feed(meter: DelayMeter, start: float, seconds: float, extra_ms: float = 0.0, offset_s: float = -5000.0) -> float:
    """Cuadros a 30 por segundo: el reloj del celular corre `-offset_s` adelante del de la PC."""
    now = start
    while now < start + seconds:
        pts_us = int((now - offset_s) * 1_000_000 - extra_ms * 1000)
        meter.add(pts_us, now, "Prueba")
        now += 1 / 30
    return now


def test_sin_atraso_no_avisa_aunque_los_relojes_sean_distintos():
    meter = DelayMeter()
    _feed(meter, 100.0, 5.0)
    assert meter.excess_ms < 5


def test_cuando_la_imagen_se_atrasa_lo_mide():
    meter = DelayMeter()
    now = _feed(meter, 100.0, 5.0)
    # Cada cuadro llega 300 ms más tarde de lo normal.
    _feed(meter, now, 2.0, extra_ms=300)
    assert 250 < meter.excess_ms <= 300


def test_cuando_se_pone_al_dia_el_aviso_baja():
    meter = DelayMeter()
    now = _feed(meter, 100.0, 5.0)
    now = _feed(meter, now, 2.0, extra_ms=300)
    _feed(meter, now, 2.0)
    assert meter.excess_ms < 20


def test_un_cuadro_suelto_tarde_no_es_un_atraso():
    meter = DelayMeter()
    now = _feed(meter, 100.0, 5.0)
    meter.add(int((now + 5000.0) * 1_000_000 - 400_000), now, "Prueba")
    assert meter.excess_ms < 60


def test_sin_hora_del_celular_no_mide_nada():
    meter = DelayMeter()
    meter.add(0, 100.0, "Prueba")
    assert meter.excess_ms == 0


def test_una_imagen_del_tamano_justo_no_se_copia():
    image = np.full((360, 640, 3), 7, dtype=np.uint8)
    assert compose(image, 640, 360) is image


def test_una_imagen_girada_o_en_espejo_si_se_copia_entera():
    image = np.full((360, 640, 3), 7, dtype=np.uint8)
    mirrored = image[:, ::-1]
    out = compose(mirrored, 640, 360)
    assert out is not mirrored and out.flags.c_contiguous
    assert (out == 7).all()
