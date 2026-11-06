"""Conexión por cable: adb redirige el puerto y abre la app en el celular."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PACKAGE = "app.celcam"
ADB_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
ADB_TERMS_URL = "https://developer.android.com/studio/terms"
ADB_FILES = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
_ADB_MAX_DOWNLOAD = 200 * 1024 * 1024
_ADB_TIMEOUT = 30
_NO_WINDOW = subprocess.CREATE_NO_WINDOW


class UsbError(Exception):
    """Mensaje para mostrarle al usuario."""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str


def install_adb(target_dir: Path, url: str = ADB_URL) -> None:
    """Descarga las herramientas de Google y guarda solo los tres archivos que hacen falta."""
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as download:
        try:
            with urllib.request.urlopen(url, timeout=_ADB_TIMEOUT) as response:
                received = 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > _ADB_MAX_DOWNLOAD:
                        raise UsbError("La descarga de adb es más grande de lo esperado. Prueba con el código QR.")
                    download.write(chunk)
        except OSError as exc:
            log.warning("No se pudo descargar adb: %s", exc)
            raise UsbError("No se pudo descargar adb. Revisa tu conexión a Internet y vuelve a intentar.") from exc
        download.seek(0)
        try:
            with zipfile.ZipFile(download) as archive:
                for name in ADB_FILES:
                    # Nombres fijos: nunca se usan rutas que vengan dentro del archivo.
                    with archive.open(f"platform-tools/{name}") as source, tempfile.NamedTemporaryFile(
                        dir=target_dir, delete=False
                    ) as partial:
                        shutil.copyfileobj(source, partial)
                    os.replace(partial.name, target_dir / name)
        except (zipfile.BadZipFile, KeyError) as exc:
            log.warning("Paquete de adb inválido: %s", exc)
            raise UsbError("La descarga de adb llegó dañada. Vuelve a intentar.") from exc


def parse_devices(output: str) -> list[Device]:
    devices = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append(Device(parts[0], parts[1]))
    return devices


class Adb:
    def __init__(self, exe: Path) -> None:
        self._exe = exe
        self.used = False

    def available(self) -> bool:
        return self._exe.exists()

    def download(self) -> None:
        install_adb(self._exe.parent)

    def _run(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
        if not self._exe.exists():
            raise UsbError("Falta adb. Vuelve a tocar «Conectar por cable USB» para descargarlo.")
        self.used = True
        try:
            return subprocess.run(
                [str(self._exe), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise UsbError("El celular no respondió. Desconecta el cable y vuelve a conectarlo.") from exc

    def find_devices(self) -> list[Device]:
        devices = parse_devices(self._run("devices").stdout)
        ready = [d for d in devices if d.state == "device"]
        if ready:
            return ready
        if any(d.state == "unauthorized" for d in devices):
            raise UsbError("Acepta el cartel «¿Permitir depuración por USB?» en el celular y vuelve a tocar el botón.")
        raise UsbError(
            "No veo ningún celular. Conecta el cable y activa «Depuración por USB» en las "
            "Opciones de desarrollador del celular."
        )

    def connect(self, port: int, link_for_usb: str, apk: Path) -> list[str]:
        """Deja listos todos los celulares enchufados y abre la app. Devuelve sus números de serie.

        A uno que ya está transmitiendo no le pasa nada: la app ignora el enlace.
        """
        connected: list[str] = []
        problem: UsbError | None = None
        for device in self.find_devices():
            try:
                self._connect_one(device.serial, port, link_for_usb, apk)
                connected.append(device.serial)
            except UsbError as exc:
                log.warning("%s: %s", device.serial, exc)
                problem = exc
        if not connected and problem:
            raise problem
        return connected

    def _connect_one(self, serial: str, port: int, link_for_usb: str, apk: Path) -> None:
        installed = self._run("-s", serial, "shell", "pm", "path", PACKAGE)
        if "package:" not in installed.stdout:
            if not apk.exists():
                raise UsbError("Falta la app en el celular y no se encontró el instalador.")
            log.info("Instalando la app por USB en %s", serial)
            result = self._run("-s", serial, "install", "-r", str(apk), timeout=120.0)
            if result.returncode != 0 or "Success" not in result.stdout:
                raise UsbError("No se pudo instalar la app en el celular. Instálala con el código QR.")
        reverse = self._run("-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}")
        if reverse.returncode != 0:
            raise UsbError("No se pudo preparar la conexión por cable. Desconecta el cable y vuelve a intentar.")
        # El enlace va entre comillas simples porque lo interpreta la consola del celular.
        start = self._run(
            "-s", serial, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"'{link_for_usb}'", PACKAGE
        )
        if start.returncode != 0 or "Error" in start.stdout:
            log.warning("am start: %s %s", start.stdout, start.stderr)
            raise UsbError("No se pudo abrir la app en el celular. Ábrela a mano.")

    def shutdown(self) -> None:
        if self.used:
            try:
                self._run("kill-server", timeout=5.0)
            except UsbError:
                pass
