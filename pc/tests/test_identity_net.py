from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from celcam.identity import Identity
from celcam.netinfo import is_usable, order_hosts
from celcam.settings import QUALITIES, CameraSettings, Settings


def test_identidad_persiste(tmp_path: Path):
    first = Identity.load_or_create(tmp_path)
    second = Identity.load_or_create(tmp_path)
    assert first.token == second.token
    assert first.fingerprint == second.fingerprint


def test_regenerar_cambia_codigo_y_certificado(tmp_path: Path):
    first = Identity.load_or_create(tmp_path)
    second = Identity.regenerate(tmp_path)
    assert first.token != second.token
    assert first.fingerprint != second.fingerprint


def test_huella_es_sha256_del_certificado(tmp_path: Path):
    identity = Identity.load_or_create(tmp_path)
    cert = x509.load_pem_x509_certificate(identity.cert_path.read_bytes())
    der = cert.public_bytes(serialization.Encoding.DER)
    assert identity.fingerprint == hashlib.sha256(der).hexdigest()


def test_enlace_lleva_todo_lo_necesario(tmp_path: Path):
    identity = Identity.load_or_create(tmp_path)
    parsed = urlparse(identity.link(["192.168.1.5", "10.0.0.2"], 47470))
    query = parse_qs(parsed.query)
    assert parsed.scheme == "celcam" and parsed.netloc == "pair"
    assert query["h"] == ["192.168.1.5,10.0.0.2"]
    assert query["p"] == ["47470"]
    assert query["t"] == [identity.token]
    assert query["f"] == [identity.fingerprint]


def test_direcciones_utiles():
    assert is_usable("192.168.1.5")
    assert is_usable("10.1.2.3")
    assert is_usable("100.64.1.2")
    assert not is_usable("127.0.0.1")
    assert not is_usable("169.254.1.1")
    assert not is_usable("8.8.8.8")


def test_orden_de_direcciones():
    interfaces = [
        ("VirtualBox Host-Only", "192.168.56.1"),
        ("Tailscale", "100.64.1.2"),
        ("Ethernet", "192.168.1.20"),
        ("Wi-Fi", "192.168.1.50"),
        ("Loopback", "127.0.0.1"),
    ]
    assert order_hosts("192.168.1.50", interfaces) == [
        "192.168.1.50",
        "192.168.1.20",
        "192.168.56.1",
        "100.64.1.2",
    ]


def test_tamanos_multiplos_de_cuatro():
    for width, height, _ in QUALITIES.values():
        assert width % 4 == 0 and height % 4 == 0


def test_ajustes_ida_y_vuelta(tmp_path: Path):
    path = tmp_path / "ajustes.json"
    settings = Settings()
    settings.cameras[2] = CameraSettings(quality="1080p", fps=60, mirror=True, extra_turns=2, fill=True)
    settings.phones["celular"] = 2
    settings.save(path)
    loaded = Settings.load(path)
    third = loaded.cameras[2]
    assert (third.quality, third.fps, third.mirror, third.extra_turns, third.fill) == ("1080p", 60, True, 2, True)
    assert loaded.cameras[0].quality == "720p"
    assert loaded.phones == {"celular": 2}


def test_los_ajustes_de_antes_son_los_de_la_primera_camara(tmp_path: Path):
    path = tmp_path / "ajustes.json"
    path.write_text('{"quality": "1080p", "fps": 60, "mirror": true, "extra_turns": 1, "fill": true, "hide_notice_shown": true}', encoding="utf-8")
    loaded = Settings.load(path)
    assert (loaded.cameras[0].quality, loaded.cameras[0].fps, loaded.cameras[0].mirror) == ("1080p", 60, True)
    assert loaded.cameras[1].quality == "720p"
    assert len(loaded.cameras) == 4
    assert loaded.hide_notice_shown is True


def test_un_lugar_danado_no_se_usa(tmp_path: Path):
    path = tmp_path / "ajustes.json"
    path.write_text('{"phones": {"a": 1, "b": 1, "c": 9, "d": "x", "e": 3}}', encoding="utf-8")
    assert Settings.load(path).phones == {"a": 1, "e": 3}


def test_mas_cuadros_piden_mas_calidad_de_video():
    assert CameraSettings(quality="720p", fps=30).kbps == 5000
    assert CameraSettings(quality="720p", fps=60).kbps == 7500
    assert CameraSettings(quality="1080p", fps=120).kbps == 18000


def test_ajustes_dañados_usan_valores_de_fabrica(tmp_path: Path):
    path = tmp_path / "ajustes.json"
    path.write_text("no es json", encoding="utf-8")
    assert Settings.load(path).cameras[0].quality == "720p"
    path.write_text('{"quality": "4K", "extra_turns": "x", "fps": 999}', encoding="utf-8")
    loaded = Settings.load(path)
    first = loaded.cameras[0]
    assert first.quality == "720p" and first.extra_turns == 0 and first.fps == 30
