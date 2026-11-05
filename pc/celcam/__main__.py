"""Punto de entrada: python -m celcam (o CelCam.exe)."""

from __future__ import annotations

import argparse
import ctypes
import logging
import logging.handlers
import sys
import threading

from . import APP_NAME, __version__, paths
from .core import Core
from .identity import Identity
from .settings import Settings
from .vcam import CameraBusy, NullCamera, VirtualCamera, is_registered

log = logging.getLogger("celcam")

# Lo usa también el instalador (AppMutex) para no pisar un CelCam abierto.
MUTEX_NAME = "CelCamAppMutex"
ERROR_ALREADY_EXISTS = 183
# «Una directiva de Control de aplicaciones bloqueó este archivo».
WINERROR_APP_CONTROL = 4551


def _setup_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(
        paths.data_dir() / "celcam.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _already_running() -> bool:
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return bool(mutex) and ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS


def _open_camera(settings: Settings):
    """Devuelve la cámara y, si algo falla, el aviso para mostrar en la ventana."""
    width, height = settings.size
    dll = paths.vcam_dll()
    try:
        camera = VirtualCamera(dll)
        camera.open(width, height, settings.fps)
    except CameraBusy:
        log.warning("Cámara virtual ocupada")
        return NullCamera(width, height), "La cámara virtual está ocupada. Cierra las apps que la usan y abre CelCam otra vez."
    except OSError as exc:
        log.exception("No se pudo cargar %s", dll)
        # PyInstaller envuelve el error original.
        if WINERROR_APP_CONTROL in (getattr(exc, "winerror", None), getattr(exc.__cause__, "winerror", None)):
            return NullCamera(width, height), (
                "Windows bloqueó la cámara virtual porque CelCam no tiene firma digital "
                "(Control inteligente de aplicaciones). Las apps no van a poder verla en esta PC."
            )
        return NullCamera(width, height), "No se pudo cargar la cámara virtual. Reinstala CelCam."
    if not is_registered():
        return camera, "La cámara virtual no está instalada en Windows, así que las apps no la van a ver. Reinstala CelCam."
    return camera, None


def _run_headless(core: Core) -> int:
    """Sin ventana: para pruebas automáticas."""
    print(core.link(), flush=True)
    print(f"usb {core.usb_link()}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--sin-ventana", action="store_true", help="correr sin ventana (pruebas)")
    args = parser.parse_args()

    _setup_logging()
    log.info("%s %s arrancando", APP_NAME, __version__)
    data = paths.data_dir()
    settings_path = data / "ajustes.json"
    settings = Settings.load(settings_path)

    if _already_running():
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} ya está abierto.", APP_NAME, 0x40)
        return 0

    camera, problem = _open_camera(settings)
    identity = Identity.load_or_create(data)
    core = Core(settings, identity, camera, settings_path)
    try:
        core.start()
    except OSError:
        log.exception("No se pudo abrir el puerto")
        ctypes.windll.user32.MessageBoxW(None, "No se pudo abrir el puerto de conexión.", APP_NAME, 0x10)
        return 1

    try:
        if args.sin_ventana:
            return _run_headless(core)
        # Se importa recién acá para que las pruebas sin ventana no necesiten Tk.
        from .ui import Window

        Window(core, problem).mainloop()
        return 0
    finally:
        core.stop()


if __name__ == "__main__":
    sys.exit(main())
