from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from celcam.apk_server import ApkServer


@pytest.fixture
def server(tmp_path):
    apk = tmp_path / "CelCam.apk"
    apk.write_bytes(b"contenido del apk")
    srv = ApkServer(apk, port=0, host="127.0.0.1")
    srv.start()
    yield srv
    srv.stop()


def test_entrega_el_apk_con_el_tipo_correcto(server):
    with urllib.request.urlopen(server.url("127.0.0.1"), timeout=5) as response:
        assert response.read() == b"contenido del apk"
        assert response.headers["Content-Type"] == "application/vnd.android.package-archive"


@pytest.mark.parametrize("path", ["/", "/../secreto.txt", "/CelCam.apk/otro", "/otro.apk"])
def test_no_entrega_ninguna_otra_ruta(server, path):
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=5)
    assert error.value.code == 404


def test_al_detenerlo_deja_de_responder(tmp_path):
    apk = tmp_path / "CelCam.apk"
    apk.write_bytes(b"x")
    srv = ApkServer(apk, port=0, host="127.0.0.1")
    srv.start()
    url = srv.url("127.0.0.1")
    srv.stop()
    with pytest.raises(OSError):
        urllib.request.urlopen(url, timeout=2)
