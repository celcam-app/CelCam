"""Salida a las cámaras virtuales «CelCam», «CelCam 2», «CelCam 3» y «CelCam 4» (filtro DirectShow basado en Softcam)."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
import winreg
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Tienen que coincidir con los CLSID de vcam\softcam\src\softcam\softcam.cpp.
CAMERA_CLSIDS = (
    "{7E2A6C14-9B3D-4F58-A1C6-5D0E8B7F3A21}",
    "{7E2A6C15-9B3D-4F58-A1C6-5D0E8B7F3A21}",
    "{7E2A6C16-9B3D-4F58-A1C6-5D0E8B7F3A21}",
    "{7E2A6C17-9B3D-4F58-A1C6-5D0E8B7F3A21}",
)
CAMERA_NAMES = ("CelCam", "CelCam 2", "CelCam 3", "CelCam 4")
CAMERA_COUNT = len(CAMERA_CLSIDS)
SETTINGS_KEY = r"Software\CelCam"


def is_registered(index: int = 0) -> bool:
    """¿Quedó instalada esa cámara virtual en Windows?"""
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{CAMERA_CLSIDS[index]}"))
        return True
    except OSError:
        return False


def format_key(index: int) -> str:
    return SETTINGS_KEY if index == 0 else rf"{SETTINGS_KEY}\Camara{index + 1}"


def remember_format(width: int, height: int, fps: int, index: int = 0) -> None:
    """El filtro lee este formato si una app abre la cámara antes que CelCam."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, format_key(index)) as key:
        winreg.SetValueEx(key, "Ancho", 0, winreg.REG_DWORD, width)
        winreg.SetValueEx(key, "Alto", 0, winreg.REG_DWORD, height)
        winreg.SetValueEx(key, "Cuadros", 0, winreg.REG_DWORD, fps)


class CameraBusy(RuntimeError):
    """Otra copia de CelCam tiene la cámara, o una app todavía no la soltó."""


class NullCamera:
    """Reemplazo cuando la cámara virtual no está disponible: la vista previa sigue andando."""

    def __init__(self, width: int, height: int) -> None:
        self.size = (width, height)
        self.channels = 3

    def open(self, width: int, height: int, fps: int, image: np.ndarray | None = None, channels: int = 3) -> None:
        self.size = (width, height)
        self.channels = channels

    def send(self, image: np.ndarray) -> None:
        pass


class VirtualCamera:
    """Recibe imágenes BGR o BGRA de arriba hacia abajo y las publica en una de las cámaras."""

    def __init__(self, dll_path: Path, index: int = 0) -> None:
        self.index = index
        self._dll = ctypes.CDLL(str(dll_path))
        self._dll.scCreateCamera.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_int]
        self._dll.scCreateCamera.restype = ctypes.c_void_p
        self._dll.scDeleteCamera.argtypes = [ctypes.c_void_p]
        self._dll.scDeleteCamera.restype = None
        self._dll.scSendFrame.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._dll.scSendFrame.restype = None
        self._dll.scSetFormat.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_int, ctypes.c_void_p]
        self._dll.scSetFormat.restype = ctypes.c_bool
        self._dll.scIsConnected.argtypes = [ctypes.c_void_p]
        self._dll.scIsConnected.restype = ctypes.c_bool
        self._lock = threading.Lock()
        self._camera: int | None = None
        self._size = (0, 0)
        self._channels = 3

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def channels(self) -> int:
        return self._channels

    def open(self, width: int, height: int, fps: int, wait_seconds: float = 8.0, image: np.ndarray | None = None, channels: int = 3) -> None:
        """Abre la cámara o cambia su formato. `image` es la primera imagen en el formato nuevo."""
        remember_format(width, height, fps, self.index)
        with self._lock:
            if self._camera is not None:
                # Las apps que la tienen abierta siguen viéndola: el filtro adapta la imagen a cada una.
                bits = image.ctypes.data if image is not None and image.shape == (height, width, channels) else None
                if self._dll.scSetFormat(self._camera, width, height, float(fps), channels, bits):
                    self._size = (width, height)
                    self._channels = channels
                    log.info("%s en %dx%d a %d cuadros/s", CAMERA_NAMES[self.index], width, height, fps)
                    return
                self._close_locked()
            # Una app que estaba usando la cámara tarda un momento en soltar la memoria compartida.
            deadline = time.monotonic() + wait_seconds
            while True:
                # Es lo que se les anuncia a las apps; la cadencia real la marca el celular.
                camera = self._dll.scCreateCamera(self.index, width, height, float(fps), channels)
                if camera:
                    break
                if time.monotonic() >= deadline:
                    raise CameraBusy("La cámara virtual está ocupada")
                time.sleep(0.25)
            self._camera = camera
            self._size = (width, height)
            self._channels = channels
            log.info("%s abierta en %dx%d a %d cuadros/s", CAMERA_NAMES[self.index], width, height, fps)

    def send(self, image: np.ndarray) -> None:
        with self._lock:
            if self._camera is None or image.shape != (self._size[1], self._size[0], self._channels):
                return
            self._dll.scSendFrame(self._camera, image.ctypes.data)

    def in_use(self) -> bool:
        with self._lock:
            return bool(self._camera and self._dll.scIsConnected(self._camera))

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._camera is not None:
            self._dll.scDeleteCamera(self._camera)
            self._camera = None
