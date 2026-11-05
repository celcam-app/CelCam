"""Prueba CelCam ya instalada: el programa de verdad, con un celular simulado, hasta una app que abre la cámara.

Solo corre con CELCAM_INSTALADO=<carpeta de instalación>: lo hace GitHub Actions después de ejecutar el instalador.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
import winreg
from pathlib import Path

import av
import numpy as np
import psutil
import pytest

from celcam import __version__, paths
from celcam.identity import Identity
from celcam.protocol import DEFAULT_PORT
from celcam.vcam import CAMERA_CLSID, is_registered
from conftest import FakePhone, encode_h264, wait_for

INSTALLED = os.environ.get("CELCAM_INSTALADO")
pytestmark = pytest.mark.skipif(not INSTALLED, reason="Solo con CelCam instalada")

CATEGORY = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"
WINDOW_TITLE = f"CelCam {__version__}"
WM_CLOSE = 0x0010
WHITE = (255, 255, 255)
GREEN = (0, 200, 0)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = ctypes.c_void_p
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]


def _exe() -> Path:
    return Path(INSTALLED) / "CelCam.exe"


def _window_visible() -> bool:
    handle = user32.FindWindowW(None, WINDOW_TITLE)
    return bool(handle) and bool(user32.IsWindowVisible(handle))


def _listening(port: int) -> bool:
    return any(c.laddr.port == port and c.status == psutil.CONN_LISTEN for c in psutil.net_connections(kind="tcp") if c.laddr)


def _log() -> str:
    return (paths.data_dir() / "celcam.log").read_text(encoding="utf-8")


def _capture(seconds: float) -> np.ndarray:
    """Abre la cámara como lo haría una app y devuelve la última imagen."""
    image = None
    with av.open("video=CelCam", format="dshow", options={"rtbufsize": "100M"}) as box:
        start = time.monotonic()
        for frame in box.decode(box.streams.video[0]):
            image = frame.to_ndarray(format="rgb24")
            if time.monotonic() - start > seconds:
                break
    return image


def _looks_like(image: np.ndarray, x: float, y: float, color: tuple[int, int, int]) -> bool:
    height, width, _ = image.shape
    got = image[int(y * height), int(x * width)]
    return all(abs(int(a) - b) < 50 for a, b in zip(got, color))


@pytest.fixture(scope="module")
def program():
    process = subprocess.Popen([str(_exe())])
    try:
        assert wait_for(lambda: _listening(DEFAULT_PORT), timeout=90), "El programa no abrió su puerto"
        yield process
    finally:
        process.kill()
        process.wait(15)


@pytest.fixture(scope="module")
def phone_stream(program):
    identity = Identity.load_or_create(paths.data_dir())
    phone = FakePhone(DEFAULT_PORT, identity.token)
    assert phone.reply["ok"] is True
    packets = encode_h264(1280, 720, 30)
    stop = threading.Event()

    def feed() -> None:
        while not stop.is_set():
            for index, packet in enumerate(packets):
                if stop.is_set():
                    return
                phone.send_video(packet, key=index == 0)
                time.sleep(1 / 30)

    thread = threading.Thread(target=feed, daemon=True)
    thread.start()
    yield phone
    stop.set()
    thread.join(3)
    phone.close()


def test_la_camara_quedo_registrada_para_apps_de_64_y_de_32_bits():
    assert is_registered()
    for view, folder in ((winreg.KEY_WOW64_64KEY, "x64"), (winreg.KEY_WOW64_32KEY, "x86")):
        access = winreg.KEY_READ | view
        path = rf"SOFTWARE\Classes\CLSID\{CAMERA_CLSID}\InprocServer32"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, access) as key:
            dll = winreg.QueryValueEx(key, None)[0]
        assert os.path.samefile(dll, Path(INSTALLED) / "vcam" / folder / "celcam_vcam.dll")
        # El filtro se registra con su nombre (softcam.cpp).
        path = rf"SOFTWARE\Classes\CLSID\{CATEGORY}\Instance\CelCam"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, access) as key:
            assert winreg.QueryValueEx(key, "FriendlyName")[0] == "CelCam"


def test_el_firewall_deja_entrar_al_celular():
    result = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=CelCam"], capture_output=True)
    assert result.returncode == 0


def test_vienen_todos_los_archivos():
    for name in ("CelCam.exe", "CelCam.apk", "celcam.ico", "LICENSE.txt", "TERCEROS.txt"):
        assert (Path(INSTALLED) / name).exists(), name


def test_el_programa_arranca_con_ventana_e_icono(program):
    assert wait_for(_window_visible, timeout=20), "No apareció la ventana"
    log = _log()
    assert f"CelCam {__version__} arrancando" in log
    assert "Cámara virtual abierta" in log
    assert "No se pudo" not in log


def test_sin_celular_la_app_ve_la_imagen_de_espera(program):
    image = _capture(1.5)
    assert image.shape == (720, 1280, 3)
    # Fondo oscuro con el texto claro.
    assert _looks_like(image, 0.05, 0.05, (17, 19, 24))
    assert image.max() > 200


def test_del_celular_a_la_app_que_abre_la_camara(phone_stream):
    assert wait_for(lambda: "Conectado: Prueba" in _log())
    image = _capture(3.0)
    assert image.shape == (720, 1280, 3)
    assert _looks_like(image, 0.1, 0.1, WHITE)
    assert _looks_like(image, 0.75, 0.75, GREEN)
    assert "Primera imagen" in _log()


def test_la_x_oculta_la_ventana_y_todo_sigue_funcionando(program, phone_stream):
    user32.PostMessageW(user32.FindWindowW(None, WINDOW_TITLE), WM_CLOSE, 0, 0)
    assert wait_for(lambda: not _window_visible(), timeout=10)
    time.sleep(1)
    assert program.poll() is None
    image = _capture(1.5)
    assert _looks_like(image, 0.1, 0.1, WHITE)
    assert _looks_like(image, 0.75, 0.75, GREEN)


def test_abrirla_de_nuevo_vuelve_a_mostrar_la_ventana(program):
    second = subprocess.run([str(_exe())], timeout=60)
    assert second.returncode == 0
    assert program.poll() is None
    assert wait_for(_window_visible, timeout=10)
