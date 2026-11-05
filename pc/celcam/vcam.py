"""Salida a la cámara virtual «CelCam» (filtro DirectShow basado en Softcam)."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
import winreg
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Tiene que coincidir con CLSID_DShowSoftcam de vcam\softcam\src\softcam\softcam.cpp.
CAMERA_CLSID = "{7E2A6C14-9B3D-4F58-A1C6-5D0E8B7F3A21}"
SETTINGS_KEY = r"Software\CelCam"


def is_registered() -> bool:
    """¿Quedó instalada la cámara virtual en Windows?"""
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{CAMERA_CLSID}"))
        return True
    except OSError:
        return False


def remember_format(width: int, height: int, fps: int) -> None:
    """El filtro lee este formato si una app abre la cámara antes que CelCam."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as key:
        winreg.SetValueEx(key, "Ancho", 0, winreg.REG_DWORD, width)
        winreg.SetValueEx(key, "Alto", 0, winreg.REG_DWORD, height)
        winreg.SetValueEx(key, "Cuadros", 0, winreg.REG_DWORD, fps)


class CameraBusy(RuntimeError):
    """Otra copia de CelCam tiene la cámara, o una app todavía no la soltó."""


class NullCamera:
    """Reemplazo cuando la cámara virtual no está disponible: la vista previa sigue andando."""

    def __init__(self, width: int, height: int) -> None:
        self.size = (width, height)

    def open(self, width: int, height: int, fps: int) -> None:
        self.size = (width, height)

    def send(self, image: np.ndarray) -> None:
        pass


class VirtualCamera:
    """Recibe imágenes BGR de arriba hacia abajo y las publica en la cámara."""

    def __init__(self, dll_path: Path) -> None:
        self._dll = ctypes.CDLL(str(dll_path))
        self._dll.scCreateCamera.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_float]
        self._dll.scCreateCamera.restype = ctypes.c_void_p
        self._dll.scDeleteCamera.argtypes = [ctypes.c_void_p]
        self._dll.scDeleteCamera.restype = None
        self._dll.scSendFrame.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._dll.scSendFrame.restype = None
        self._dll.scIsConnected.argtypes = [ctypes.c_void_p]
        self._dll.scIsConnected.restype = ctypes.c_bool
        self._lock = threading.Lock()
        self._camera: int | None = None
        self._size = (0, 0)

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def open(self, width: int, height: int, fps: int, wait_seconds: float = 8.0) -> None:
        remember_format(width, height, fps)
        with self._lock:
            self._close_locked()
            # Una app que estaba usando la cámara tarda un momento en soltar la memoria compartida.
            deadline = time.monotonic() + wait_seconds
            while True:
                # Es lo que se les anuncia a las apps; la cadencia real la marca el celular.
                camera = self._dll.scCreateCamera(width, height, float(fps))
                if camera:
                    break
                if time.monotonic() >= deadline:
                    raise CameraBusy("La cámara virtual está ocupada")
                time.sleep(0.25)
            self._camera = camera
            self._size = (width, height)
            log.info("Cámara virtual abierta en %dx%d a %d cuadros/s", width, height, fps)

    def send(self, image: np.ndarray) -> None:
        with self._lock:
            if self._camera is None or image.shape != (self._size[1], self._size[0], 3):
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
