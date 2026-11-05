"""El número de versión está en varios archivos: tienen que coincidir (lo exige la firma de código)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from celcam import __version__

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("relative", "pattern"),
    [
        ("android/app/build.gradle.kts", r'versionName = "([\d.]+)"'),
        ("installer/CelCam.iss", r'#define AppVersion "([\d.]+)"'),
        ("pc/version_info.txt", r"StringStruct\('ProductVersion', '([\d.]+)'\)"),
        ("vcam/softcam/src/softcam/celcam_vcam.rc", r'VALUE "ProductVersion", "([\d.]+)"'),
    ],
)
def test_misma_version_en_todos_lados(relative: str, pattern: str):
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert re.search(pattern, text).group(1) == __version__


def test_el_codigo_de_version_numerico_acompana():
    major, minor, patch = (int(part) for part in __version__.split("."))
    text = (ROOT / "pc/version_info.txt").read_text(encoding="utf-8")
    assert f"prodvers=({major}, {minor}, {patch}, 0)" in text
    rc = (ROOT / "vcam/softcam/src/softcam/celcam_vcam.rc").read_text(encoding="utf-8")
    assert f"PRODUCTVERSION {major},{minor},{patch},0" in rc
