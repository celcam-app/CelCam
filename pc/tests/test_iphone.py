"""El camino del iPhone de punta a punta: la página real en Edge sin ventana, con una imagen animada como cámara."""

from __future__ import annotations

import asyncio
import functools
import http.server
import os
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import av

from celcam.core import Core
from celcam.netinfo import local_hosts
from celcam.server import StreamServer
from celcam.settings import Settings
from celcam.webphone import ice_credentials, web_link
from conftest import FakeCamera, FakePhone, wait_for

PAGE = Path(__file__).resolve().parents[2] / "web" / "iphone"
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
needs_edge = pytest.mark.skipif(not EDGE.exists() or not local_hosts(), reason="Hace falta Edge y estar en una red")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


@pytest.fixture(scope="module")
def page_url():
    handler = functools.partial(Quiet, directory=str(PAGE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://localhost:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


class Browser:
    """Edge con un perfil propio: no toca el navegador de quien corre la prueba."""

    def __init__(self, link: str, profile: Path) -> None:
        self.process = subprocess.Popen(
            [str(EDGE), "--headless=new", "--no-first-run", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--user-data-dir={profile}", f"{link}&auto=1&lienzo=1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def close(self) -> None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)], capture_output=True)
        self.process.wait(10)


class ReopenCamera(FakeCamera):
    channels = 3

    def open(self, width, height, fps, image=None, channels=3):
        self.size = (width, height)
        self.channels = channels


@pytest.fixture
def web_core(identity):
    settings = Settings()
    for camera in settings.cameras:
        camera.quality = "480p"
    cameras = [ReopenCamera(*settings.cameras[i].size) for i in range(4)]
    core = Core(settings, identity, cameras)
    core.server = StreamServer(identity, core, core._stream_config, port=0, host="127.0.0.1")
    core.start()
    assert core.web is not None, "El módulo del iPhone no arrancó"
    yield core, cameras
    core.stop()


def _link(core, page_url, **changes) -> str:
    link = web_link(core.identity, local_hosts(), core.web.port, page_url)
    for field, value in changes.items():
        head, _, tail = link.partition(f"&{field}=")
        rest = tail.split("&", 1)
        link = f"{head}&{field}={value}" + (f"&{rest[1]}" if len(rest) > 1 else "")
    return link


def test_las_claves_salen_del_codigo_y_tienen_el_formato_de_ice():
    ufrag, password = ice_credentials("un-código")
    assert len(ufrag) == 8 and len(password) == 32
    assert ufrag.isalnum() and password.isalnum()
    assert ice_credentials("otro") != (ufrag, password)


def test_el_enlace_no_manda_nada_al_servidor_de_la_pagina(identity):
    link = web_link(identity, ["192.168.0.5", "10.0.0.2"], 47470, "https://ejemplo.test/iphone/")
    page, _, private = link.partition("#")
    assert identity.token not in page and "192.168" not in page
    assert identity.token in private and "h=192.168.0.5,10.0.0.2" in private
    # Que entre sin problemas en un QR que se pueda leer de una pantalla.
    assert len(link) < 220


@needs_edge
def test_la_imagen_del_navegador_llega_a_la_camara(web_core, page_url, tmp_path):
    core, cameras = web_core
    browser = Browser(_link(core, page_url), tmp_path / "edge")
    try:
        channel = core.channels[0]
        assert wait_for(lambda: channel.status().connected, timeout=30), "No se conectó"
        assert channel.status().device == "Navegador"
        assert wait_for(lambda: len(cameras[0].frames) > 30, timeout=20), "No llegó video"
        assert cameras[0].frames[-1].shape == (480, 856, 3)
        # Es la imagen animada, no la de espera: mucho color y distinta de un cuadro a otro.
        assert wait_for(lambda: channel.status().stats.fps > 10, timeout=10)
        assert (cameras[0].frames[-1] != cameras[0].frames[-20]).any()
        assert channel.status().stats.mbps > 0
        # Los controles: un navegador informa lo mismo que la app.
        assert wait_for(lambda: channel.status().controls is not None, timeout=5)
        assert channel.status().battery is None
    finally:
        browser.close()
    # Cerrar el navegador deja libre la cámara.
    assert wait_for(lambda: not core.channels[0].status().connected, timeout=30)


@needs_edge
def test_un_navegador_vuelve_siempre_a_su_camara_y_otro_va_a_la_siguiente(web_core, page_url, tmp_path):
    core, cameras = web_core
    phone = FakePhone(core.server.port, core.identity.token, phone_id="android", device="Android")
    assert wait_for(lambda: core.channels[0].status().connected)
    browser = Browser(_link(core, page_url), tmp_path / "edge")
    try:
        assert wait_for(lambda: core.channels[1].status().connected, timeout=30), "Tenía que ir a CelCam 2"
        assert wait_for(lambda: len(cameras[1].frames) > 5, timeout=20)
        # El celular de Android no se enteró.
        assert core.channels[0].status().device == "Android"
    finally:
        browser.close()
        phone.close()


@needs_edge
def test_con_un_codigo_falso_no_entra(web_core, page_url, tmp_path):
    core, cameras = web_core
    browser = Browser(_link(core, page_url, t="codigo-falso"), tmp_path / "edge")
    try:
        time.sleep(8)
        assert not any(channel.status().connected for channel in core.channels)
        assert not core.settings.phones
    finally:
        browser.close()


@needs_edge
def test_si_la_huella_no_es_la_de_esta_pc_el_navegador_no_entra(web_core, page_url, tmp_path):
    core, _ = web_core
    # El navegador no le entrega el código a una PC que no es la del QR.
    browser = Browser(_link(core, page_url, f="A" * 43), tmp_path / "edge")
    try:
        time.sleep(8)
        assert not any(channel.status().connected for channel in core.channels)
    finally:
        browser.close()


def test_quien_tiene_la_clave_pero_no_el_codigo_no_ocupa_una_camara(web_core):
    from celcam.webphone import WebSession

    core, _ = web_core

    async def hello() -> None:
        session = WebSession(core.web, "ajeno", ("192.0.2.1", 5000))
        session._on_hello({"token": "falso", "id": "ajeno", "device": "Ajeno"})

    asyncio.run_coroutine_threadsafe(hello(), core.web.loop).result(5)
    assert not core.settings.phones
    assert not any(channel.status().connected for channel in core.channels)


def test_sin_el_modulo_del_iphone_el_programa_sigue(identity, monkeypatch):
    import celcam.webphone as module

    def broken(*args, **kwargs):
        raise OSError("Windows bloqueó una biblioteca")

    monkeypatch.setattr(module, "WebServer", broken)
    settings = Settings()
    core = Core(settings, identity, [ReopenCamera(*settings.cameras[i].size) for i in range(4)])
    core.server = StreamServer(identity, core, core._stream_config, port=0, host="127.0.0.1")
    core.start()
    try:
        assert core.web is None and core.web_link() is None
        assert core.link().startswith("celcam://")
    finally:
        core.stop()


def test_la_vista_acepta_cuadros_ya_decodificados():
    from celcam.pipeline import Pipeline

    camera = FakeCamera(640, 360)
    pipeline = Pipeline(camera)
    image = np.zeros((1280, 720, 3), dtype=np.uint8)
    image[:, :] = (0, 0, 200)
    pipeline.show(av.VideoFrame.from_ndarray(image, format="bgr24"), 1000)
    assert camera.frames[-1].shape == (360, 640, 3)
    # Vertical en una cámara apaisada: entero, con bandas negras a los costados.
    assert tuple(camera.frames[-1][180, 320]) == (0, 0, 200)
    assert tuple(camera.frames[-1][180, 5]) == (0, 0, 0)
