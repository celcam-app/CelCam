from __future__ import annotations

import uuid
from pathlib import Path

from celcam.settings import Settings
from celcam.tray import ICON_SIZE, ShowSignal, Tray, ask_running_copy_to_show, load_icon


def test_un_segundo_arranque_le_pide_a_la_copia_abierta_que_aparezca():
    name = f"CelCamPrueba{uuid.uuid4().hex}"
    assert ask_running_copy_to_show(name) is False
    signal = ShowSignal(name)
    assert signal.requested() is False
    assert ask_running_copy_to_show(name) is True
    assert signal.requested() is True
    # Cada pedido se atiende una sola vez.
    assert signal.requested() is False


def test_sin_archivo_de_icono_igual_hay_icono(tmp_path: Path):
    image = load_icon(tmp_path / "no-existe.ico")
    assert image.size == ICON_SIZE


def test_el_menu_pide_abrir_y_salir(tmp_path: Path, monkeypatch):
    import pystray

    created = {}

    class FakeIcon:
        def __init__(self, name, image, title, menu):
            created["menu"] = menu

        def run_detached(self):
            created["running"] = True

        def stop(self):
            created["running"] = False

    monkeypatch.setattr(pystray, "Icon", FakeIcon)
    tray = Tray(tmp_path / "no-existe.ico", "CelCam")
    assert tray.start() is True
    assert created["running"] is True
    abrir, salir = list(created["menu"].items)
    assert abrir.text == "Abrir CelCam" and abrir.default
    abrir(None)
    assert tray.show_requested.is_set()
    assert salir.text == "Salir"
    salir(None)
    assert tray.quit_requested.is_set()
    tray.stop()
    assert created["running"] is False


def test_si_falla_el_icono_avisa_para_que_la_x_cierre_el_programa(tmp_path: Path, monkeypatch):
    import pystray

    def broken(*_):
        raise OSError("sin bandeja")

    monkeypatch.setattr(pystray, "Icon", broken)
    tray = Tray(tmp_path / "no-existe.ico", "CelCam")
    assert tray.start() is False
    tray.notify("hola", "CelCam")
    tray.stop()


def test_el_aviso_de_la_x_se_recuerda(tmp_path: Path):
    path = tmp_path / "ajustes.json"
    assert Settings.load(path).hide_notice_shown is False
    settings = Settings()
    settings.hide_notice_shown = True
    settings.save(path)
    assert Settings.load(path).hide_notice_shown is True
