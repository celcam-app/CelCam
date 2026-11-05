"""Sirve el instalador de la app al celular, solo mientras la ventana de instalación está abierta."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

APK_PORT = 47480
APK_NAME = "CelCam.apk"


class ApkServer:
    def __init__(self, apk: Path, port: int = APK_PORT, host: str = "0.0.0.0") -> None:
        self._apk = apk
        self._wanted_port = port
        self._host = host
        self._httpd: ThreadingHTTPServer | None = None
        self.port = port

    def start(self) -> None:
        apk = self._apk

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != f"/{APK_NAME}":
                    self.send_error(404)
                    return
                data = apk.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", f'attachment; filename="{APK_NAME}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:
                log.info("APK: " + fmt, *args)

        self._httpd = ThreadingHTTPServer((self._host, self._wanted_port), Handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, name="celcam-apk", daemon=True).start()

    def url(self, host: str) -> str:
        return f"http://{host}:{self.port}/{APK_NAME}"

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
