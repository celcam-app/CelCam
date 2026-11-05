from __future__ import annotations

import functools
import http.server
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

from celcam.usb import ADB_FILES, Adb, UsbError, install_adb, parse_devices


def _result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, "")


class ScriptedAdb(Adb):
    """adb de mentira que contesta según el comando."""

    def __init__(self, tmp_path: Path, devices: str, installed: bool = True) -> None:
        exe = tmp_path / "adb.exe"
        exe.write_bytes(b"")
        super().__init__(exe)
        self.devices = devices
        self.installed = installed
        self.calls: list[tuple[str, ...]] = []

    def _run(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
        self.calls.append(args)
        if args[0] == "devices":
            return _result(self.devices)
        if "pm" in args:
            return _result("package:/data/app/base.apk" if self.installed else "")
        if "install" in args:
            return _result("Success")
        return _result("Starting: Intent")


def test_lee_la_lista_de_dispositivos():
    output = "List of devices attached\nABC123\tdevice\nDEF456\tunauthorized\n\n"
    devices = parse_devices(output)
    assert [(d.serial, d.state) for d in devices] == [("ABC123", "device"), ("DEF456", "unauthorized")]


def test_sin_celular_explica_que_hacer(tmp_path):
    adb = ScriptedAdb(tmp_path, "List of devices attached\n\n")
    with pytest.raises(UsbError, match="Depuración por USB"):
        adb.find_device()


def test_sin_autorizar_pide_aceptar(tmp_path):
    adb = ScriptedAdb(tmp_path, "List of devices attached\nABC\tunauthorized\n")
    with pytest.raises(UsbError, match="Permitir"):
        adb.find_device()


def test_conecta_redirige_y_abre_la_app(tmp_path):
    adb = ScriptedAdb(tmp_path, "List of devices attached\nABC\tdevice\n")
    link = "celcam://pair?h=127.0.0.1&p=47470&t=abc&f=def"
    assert adb.connect(47470, link, tmp_path / "CelCam.apk") == "ABC"
    assert ("-s", "ABC", "reverse", "tcp:47470", "tcp:47470") in adb.calls
    start = next(call for call in adb.calls if "am" in call)
    # Entre comillas: la consola del celular cortaría el enlace en cada «&».
    assert f"'{link}'" in start
    assert not any("install" in call for call in adb.calls)


def test_instala_la_app_si_falta(tmp_path):
    apk = tmp_path / "CelCam.apk"
    apk.write_bytes(b"apk")
    adb = ScriptedAdb(tmp_path, "List of devices attached\nABC\tdevice\n", installed=False)
    adb.connect(47470, "celcam://pair", apk)
    assert any("install" in call for call in adb.calls)


def test_sin_app_ni_instalador_avisa(tmp_path):
    adb = ScriptedAdb(tmp_path, "List of devices attached\nABC\tdevice\n", installed=False)
    with pytest.raises(UsbError, match="instalador"):
        adb.connect(47470, "celcam://pair", tmp_path / "no-existe.apk")


def test_adb_faltante_da_un_mensaje_claro(tmp_path):
    adb = Adb(tmp_path / "no-existe.exe")
    assert not adb.available()
    with pytest.raises(UsbError, match="descargarlo"):
        adb.find_device()


@pytest.fixture
def web(tmp_path):
    """Servidor local con los archivos que se dejen en su carpeta."""
    root = tmp_path / "web"
    root.mkdir()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _platform_tools(path: Path, names=ADB_FILES, extra=()) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(f"platform-tools/{name}", f"contenido de {name}")
        for name in extra:
            archive.writestr(name, "no debería extraerse")


def test_descarga_solo_los_archivos_necesarios(tmp_path, web):
    root, url = web
    _platform_tools(root / "pt.zip", extra=("platform-tools/fastboot.exe", "../fuera.exe"))
    target = tmp_path / "adb"
    install_adb(target, f"{url}/pt.zip")
    assert sorted(p.name for p in target.iterdir()) == sorted(ADB_FILES)
    assert (target / "adb.exe").read_text() == "contenido de adb.exe"
    assert not (tmp_path / "fuera.exe").exists()


def test_descarga_que_no_existe_explica_que_hacer(tmp_path, web):
    _, url = web
    with pytest.raises(UsbError, match="conexión a Internet"):
        install_adb(tmp_path / "adb", f"{url}/no-existe.zip")


def test_descarga_danada_no_deja_nada_a_medias(tmp_path, web):
    root, url = web
    (root / "pt.zip").write_bytes(b"esto no es un zip")
    target = tmp_path / "adb"
    with pytest.raises(UsbError, match="dañada"):
        install_adb(target, f"{url}/pt.zip")
    assert not (target / "adb.exe").exists()


def test_paquete_incompleto_se_rechaza(tmp_path, web):
    root, url = web
    _platform_tools(root / "pt.zip", names=("adb.exe",))
    with pytest.raises(UsbError, match="dañada"):
        install_adb(tmp_path / "adb", f"{url}/pt.zip")
