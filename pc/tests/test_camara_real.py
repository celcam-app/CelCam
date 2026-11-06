"""Prueba la cámara virtual de verdad: el filtro compilado, abierto con FFmpeg por DirectShow.

Necesita el filtro en pc\\stage (o en CELCAM_FILTRO_PRUEBA). Usa el registro que ya exista; con
CELCAM_REGISTRAR_PRUEBA=1 (GitHub Actions) lo registra para este usuario y lo saca al terminar. Si no se puede, o si
otro CelCam tiene la cámara, se saltea. CELCAM_CAMARA_PRUEBA elige la primera cámara a usar (por defecto, 0).
"""

from __future__ import annotations

import os
import threading
import time
import winreg
from pathlib import Path

import av
import numpy as np
import pytest

from celcam.vcam import CAMERA_CLSIDS, CAMERA_COUNT, CAMERA_NAMES, CameraBusy, VirtualCamera, is_registered

STAGE = Path(os.environ.get("CELCAM_FILTRO_PRUEBA") or Path(__file__).resolve().parent.parent / "stage" / "vcam")
# La cámara que usan las pruebas; las que necesitan dos usan también la siguiente.
INDEX = int(os.environ.get("CELCAM_CAMARA_PRUEBA", "0"))
NAME = CAMERA_NAMES[INDEX]
CATEGORY = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"
CLASSES = r"Software\Classes"

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)


def _delete_tree(path: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(path + "\\" + child)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def _register_for_this_user(dll: Path, clsid: str, name: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\CLSID\{clsid}") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, name)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\CLSID\{clsid}\InprocServer32") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(dll))
        winreg.SetValueEx(key, "ThreadingModel", 0, winreg.REG_SZ, "Both")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\CLSID\{CATEGORY}\Instance\{clsid}") as key:
        winreg.SetValueEx(key, "CLSID", 0, winreg.REG_SZ, clsid)
        winreg.SetValueEx(key, "FriendlyName", 0, winreg.REG_SZ, name)


@pytest.fixture(scope="module")
def registered():
    dll = STAGE / "x64" / "celcam_vcam.dll"
    if not dll.exists():
        pytest.skip("Falta compilar la cámara virtual")
    missing = [i for i in range(CAMERA_COUNT) if not is_registered(i)]
    if missing and os.environ.get("CELCAM_REGISTRAR_PRUEBA") != "1":
        if not is_registered(INDEX):
            pytest.skip("La cámara virtual no está registrada en esta PC")
        missing = []
    for i in missing:
        _register_for_this_user(dll, CAMERA_CLSIDS[i], CAMERA_NAMES[i])
    try:
        yield dll
    finally:
        for i in missing:
            _delete_tree(rf"{CLASSES}\CLSID\{CAMERA_CLSIDS[i]}")
            _delete_tree(rf"{CLASSES}\CLSID\{CATEGORY}\Instance\{CAMERA_CLSIDS[i]}")


def _pattern(width: int, height: int) -> np.ndarray:
    """Rojo y verde arriba, azul y blanco abajo. La cámara recibe BGR."""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[: height // 2, : width // 2] = RED[::-1]
    image[: height // 2, width // 2 :] = GREEN[::-1]
    image[height // 2 :, : width // 2] = BLUE[::-1]
    image[height // 2 :, width // 2 :] = WHITE[::-1]
    return image


class Sender:
    """Manda la imagen de prueba a 30 cuadros por segundo, como un celular."""

    def __init__(self, dll: Path, width: int, height: int, fps: int = 30, index: int = INDEX, image: np.ndarray | None = None) -> None:
        self.camera = VirtualCamera(dll, index)
        self.image = _pattern(width, height) if image is None else image
        try:
            self.camera.open(width, height, fps, wait_seconds=1.0, channels=self.image.shape[2])
        except CameraBusy:
            pytest.skip("Otro CelCam tiene la cámara abierta")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(1 / 30):
            self.camera.send(self.image)

    def change(self, width: int, height: int, fps: int) -> None:
        image = _pattern(width, height)
        self.camera.open(width, height, fps, image=image)
        self.image = image

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)
        self.camera.close()


def _spots(image: np.ndarray, bars: tuple[int, int] = (0, 0)) -> list[tuple[int, ...]]:
    """Los colores en el centro de cada cuarto de la parte con imagen."""
    height, width, _ = image.shape
    bar_w, bar_h = bars
    x = [bar_w + (width - 2 * bar_w) // 4, width - bar_w - (width - 2 * bar_w) // 4]
    y = [bar_h + (height - 2 * bar_h) // 4, height - bar_h - (height - 2 * bar_h) // 4]
    return [tuple(int(v) for v in image[y[row], x[col]]) for row in (0, 1) for col in (0, 1)]


def _assert_pattern(image: np.ndarray, bars: tuple[int, int] = (0, 0)) -> None:
    for got, wanted in zip(_spots(image, bars), (RED, GREEN, BLUE, WHITE)):
        assert all(abs(a - b) < 40 for a, b in zip(got, wanted)), f"{got} en lugar de {wanted}"


def _capture(seconds: float, options: dict[str, str] | None = None, on_frame=None, name: str = NAME) -> list[np.ndarray]:
    frames = []
    with av.open(f"video={name}", format="dshow", options={"rtbufsize": "100M", **(options or {})}) as box:
        start = time.monotonic()
        for frame in box.decode(box.streams.video[0]):
            frames.append(frame.to_ndarray(format="rgb24"))
            if on_frame:
                on_frame(time.monotonic() - start)
            if time.monotonic() - start > seconds:
                return frames
    return frames


def test_por_defecto_entrega_el_formato_actual(registered):
    sender = Sender(registered, 1280, 720)
    try:
        frames = _capture(1.5)
        assert frames[-1].shape == (720, 1280, 3)
        _assert_pattern(frames[-1])
    finally:
        sender.stop()


@pytest.mark.parametrize(
    ("size", "bars"),
    [("640x360", (0, 0)), ("1920x1080", (0, 0)), ("640x480", (0, 60))],
)
def test_cada_app_recibe_el_tamano_que_pide(registered, size, bars):
    sender = Sender(registered, 1280, 720)
    try:
        width, height = (int(v) for v in size.split("x"))
        frames = _capture(1.5, {"video_size": size})
        assert frames[-1].shape == (height, width, 3)
        _assert_pattern(frames[-1], bars)
    finally:
        sender.stop()


def test_cambiar_el_formato_no_corta_a_quien_tiene_la_camara_abierta(registered):
    sender = Sender(registered, 1280, 720)
    changes = [(1.0, 1920, 1080, 30), (2.0, 856, 480, 60), (3.0, 1280, 720, 120)]

    def on_frame(elapsed: float) -> None:
        while changes and elapsed >= changes[0][0]:
            _, width, height, fps = changes.pop(0)
            sender.change(width, height, fps)

    try:
        frames = _capture(4.0, on_frame=on_frame)
        assert not changes
        # La app sigue recibiendo el tamaño del principio, siempre con la imagen bien.
        for frame in frames[5:]:
            assert frame.shape == (720, 1280, 3)
            _assert_pattern(frame)
        assert len(frames) > 60
    finally:
        sender.stop()


def test_abierta_sin_programa_queda_en_negro_y_toma_la_imagen_al_llegar(registered):
    sender = None
    frames = []

    def on_frame(elapsed: float) -> None:
        nonlocal sender
        if sender is None and elapsed >= 0.5:
            sender = Sender(registered, 856, 480)

    try:
        frames = _capture(2.5, {"video_size": "1280x720"}, on_frame=on_frame)
        assert frames[0].max() == 0
        assert frames[-1].shape == (720, 1280, 3)
        _assert_pattern(frames[-1])
    finally:
        if sender:
            sender.stop()


def test_cada_camara_muestra_su_propia_imagen(registered):
    if INDEX + 1 >= CAMERA_COUNT or not is_registered(INDEX + 1):
        pytest.skip("Falta la segunda cámara")
    # La segunda, con los cuartos al revés.
    first = Sender(registered, 1280, 720, index=INDEX)
    second = Sender(registered, 640, 360, index=INDEX + 1, image=_pattern(640, 360)[::-1, ::-1].copy())
    try:
        _assert_pattern(_capture(1.0, name=CAMERA_NAMES[INDEX])[-1])
        frame = _capture(1.0, name=CAMERA_NAMES[INDEX + 1])[-1]
        assert frame.shape == (360, 640, 3)
        _assert_pattern(frame[::-1, ::-1])
    finally:
        first.stop()
        second.stop()


def _transparent(width: int, height: int) -> np.ndarray:
    """BGRA: la mitad de arriba es roja y opaca; la de abajo, azul pero del todo transparente."""
    image = np.zeros((height, width, 4), dtype=np.uint8)
    image[: height // 2] = (0, 0, 255, 255)
    image[height // 2 :] = (255, 0, 0, 0)
    return image


def test_con_transparencia_una_app_comun_ve_el_color_de_fondo(registered):
    sender = Sender(registered, 1280, 720, image=_transparent(1280, 720))
    try:
        # Sin pedir nada, FFmpeg toma el primer formato: con transparencia.
        frame = _capture(1.2, {"pixel_format": "bgr24"})[-1]
        assert frame.shape == (720, 1280, 3)
        assert all(abs(a - b) < 12 for a, b in zip(frame[180, 640], RED))
        # Lo transparente se ve del color de fondo, no del azul que hay debajo.
        assert all(abs(a - b) < 12 for a, b in zip(frame[540, 640], (17, 19, 24)))
    finally:
        sender.stop()


def test_con_transparencia_la_app_que_la_pide_la_recibe(registered):
    sender = Sender(registered, 1280, 720, image=_transparent(1280, 720))
    try:
        raw = None
        with av.open(f"video={NAME}", format="dshow", options={"rtbufsize": "100M", "pixel_format": "bgr0"}) as box:
            # Los datos tal cual los entrega el filtro: BGRA, de abajo hacia arriba.
            for index, packet in enumerate(box.demux(box.streams.video[0])):
                if index >= 10 and packet.size:
                    raw = np.frombuffer(bytes(packet), dtype=np.uint8).reshape(720, 1280, 4)
                    break
        assert list(raw[719 - 180, 640]) == [0, 0, 255, 255]
        assert raw[719 - 540, 640][3] == 0
    finally:
        sender.stop()


def test_sin_transparencia_no_se_ofrece_ese_formato(registered):
    sender = Sender(registered, 1280, 720)
    try:
        with pytest.raises(av.error.FFmpegError):
            _capture(0.5, {"pixel_format": "bgr0"})
    finally:
        sender.stop()
