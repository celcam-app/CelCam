"""Dónde están los archivos del programa y los datos del usuario."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def install_dir() -> Path:
    """Carpeta con la cámara virtual, adb y el APK."""
    override = os.environ.get("CELCAM_HOME")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # En desarrollo, scripts\construir.ps1 deja todo en pc\stage.
    return Path(__file__).resolve().parent.parent / "stage"


def data_dir() -> Path:
    """Carpeta privada del usuario: identidad, ajustes y registro."""
    override = os.environ.get("CELCAM_DATA")
    base = Path(override) if override else Path(os.environ.get("APPDATA", Path.home())) / "CelCam"
    base.mkdir(parents=True, exist_ok=True)
    return base


def vcam_dll() -> Path:
    return install_dir() / "vcam" / "x64" / "celcam_vcam.dll"


def adb_exe() -> Path:
    # No viene en el instalador: es de Google y se descarga solo si alguien usa el cable.
    return data_dir() / "adb" / "adb.exe"


def apk_file() -> Path:
    return install_dir() / "CelCam.apk"
