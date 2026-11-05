"""El ícono junto al reloj: con la ventana cerrada, CelCam sigue funcionando."""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

SHOW_EVENT_NAME = "CelCamMostrarVentana"
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
ICON_SIZE = (64, 64)
# El color principal de la ventana, por si falta el archivo del ícono.
ICON_FALLBACK_COLOR = (45, 212, 191, 255)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenEventW.restype = wintypes.HANDLE
_kernel32.SetEvent.argtypes = [wintypes.HANDLE]
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class ShowSignal:
    """Si alguien vuelve a abrir CelCam, la copia que ya está abierta se entera por acá."""

    def __init__(self, name: str = SHOW_EVENT_NAME) -> None:
        self._handle = _kernel32.CreateEventW(None, False, False, name)

    def requested(self) -> bool:
        return bool(self._handle) and _kernel32.WaitForSingleObject(self._handle, 0) == WAIT_OBJECT_0


def ask_running_copy_to_show(name: str = SHOW_EVENT_NAME) -> bool:
    handle = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        return bool(_kernel32.SetEvent(handle))
    finally:
        _kernel32.CloseHandle(handle)


def load_icon(path: Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            return image.convert("RGBA").resize(ICON_SIZE)
    except OSError:
        log.warning("No se pudo leer el ícono %s", path)
        return Image.new("RGBA", ICON_SIZE, ICON_FALLBACK_COLOR)


class Tray:
    """Corre en su propio hilo. La ventana mira estos pedidos desde el suyo: Tk no admite otros hilos."""

    def __init__(self, icon_path: Path, title: str) -> None:
        self.show_requested = threading.Event()
        self.quit_requested = threading.Event()
        self._icon_path = icon_path
        self._title = title
        self._icon = None

    def start(self) -> bool:
        """Sin ícono, la X tiene que cerrar el programa: si no, quedaría andando sin forma de salir."""
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem(f"Abrir {self._title}", lambda: self.show_requested.set(), default=True),
                pystray.MenuItem("Salir", lambda: self.quit_requested.set()),
            )
            self._icon = pystray.Icon(self._title, load_icon(self._icon_path), self._title, menu)
            self._icon.run_detached()
            return True
        except Exception:
            log.exception("No se pudo poner el ícono junto al reloj")
            self._icon = None
            return False

    def notify(self, message: str, title: str) -> None:
        if self._icon:
            try:
                self._icon.notify(message, title)
            except Exception:
                log.warning("No se pudo mostrar el aviso", exc_info=True)

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
            self._icon = None
