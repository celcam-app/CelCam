"""La ventana de CelCam."""

from __future__ import annotations

import io
import logging
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import numpy as np
import segno
from PIL import Image, ImageTk

from . import APP_NAME, __version__, paths
from .apk_server import ApkServer
from .core import Core
from .identity import Identity
from .netinfo import local_hosts
from .settings import FPS_OPTIONS, QUALITIES
from .tray import ShowSignal, Tray
from .usb import ADB_TERMS_URL, UsbError
from .vcam import CameraBusy

log = logging.getLogger(__name__)

BG = "#111318"
PANEL = "#1A1D24"
PANEL_ALT = "#242833"
TEXT = "#ECEFF4"
MUTED = "#969EAB"
ACCENT = "#2DD4BF"
ACCENT_HOVER = "#22B8A5"
ACCENT_TEXT = "#06241F"
OK = "#34D399"
WARN = "#FBBF24"

PREVIEW_SIZE = (640, 360)
PREVIEW_MS = 66
STATUS_MS = 500
REQUESTS_MS = 200
BRING_TO_FRONT_MS = 200
QR_PIXELS = 240


def qr_image(text: str, pixels: int) -> Image.Image:
    # Corrección de errores baja: en una pantalla limpia alcanza, y el código sale menos denso.
    code = segno.make(text, error="l")
    buffer = io.BytesIO()
    scale = max(1, pixels // code.symbol_size(border=2)[0])
    code.save(buffer, kind="png", scale=scale, border=2, dark="#000000", light="#FFFFFF")
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


class Window(ctk.CTk):
    def __init__(self, core: Core, camera_problem: str | None = None, show_signal: ShowSignal | None = None) -> None:
        super().__init__(fg_color=BG)
        self.core = core
        self._apk_server: ApkServer | None = None
        self._last_link = ""
        self._was_connected = False
        self._show_signal = show_signal
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1020x640")
        self.minsize(1020, 640)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        icon = paths.install_dir() / "celcam.ico"
        if icon.exists():
            self.iconbitmap(str(icon))
        self._tray = Tray(icon, APP_NAME)
        self._has_tray = self._tray.start()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_preview()
        self._build_side()
        self._show_problem(camera_problem)
        self._refresh_link()
        self._tick_preview()
        self._tick_status()
        self._tick_requests()

    # Armado de la ventana

    def _build_preview(self) -> None:
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(20, 10), pady=20)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(header, text=APP_NAME, font=ctk.CTkFont(size=24, weight="bold"), text_color=TEXT).pack(side="left")
        self._state_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=14), text_color=MUTED)
        self._state_label.pack(side="right")

        holder = tk.Frame(left, bg="#000000")
        holder.grid(row=1, column=0, sticky="nsew")
        self._blank = Image.new("RGB", PREVIEW_SIZE, (0, 0, 0))
        self._photo = ImageTk.PhotoImage(self._blank)
        self._preview = tk.Label(holder, image=self._photo, bg="#000000", bd=0)
        self._preview.pack(expand=True)

        self._notice = ctk.CTkLabel(
            left, text="", font=ctk.CTkFont(size=13), text_color=MUTED, justify="left", wraplength=620, anchor="w"
        )
        self._notice.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self._stats_label = ctk.CTkLabel(left, text="", font=ctk.CTkFont(size=13), text_color=MUTED, anchor="w")
        self._stats_label.grid(row=3, column=0, sticky="ew")

    def _build_side(self) -> None:
        side = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=14, width=320)
        side.grid(row=0, column=1, sticky="ns", padx=(10, 20), pady=20)
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(1, weight=1)

        self._connect_panel = ctk.CTkFrame(side, fg_color="transparent")
        self._connect_panel.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 0))
        self._connect_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._connect_panel, text="Conecta tu celular", font=ctk.CTkFont(size=17, weight="bold"), text_color=TEXT
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            self._connect_panel,
            text="Abre CelCam en el celular y toca «Escanear código».",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            wraplength=270,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 8))
        self._qr_label = ctk.CTkLabel(self._connect_panel, text="")
        self._qr_label.grid(row=2, column=0, pady=(0, 8))
        ctk.CTkButton(
            self._connect_panel,
            text="Conectar por cable USB",
            height=34,
            fg_color=PANEL_ALT,
            hover_color="#2E3340",
            text_color=TEXT,
            command=self._connect_usb,
        ).grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkButton(
            self._connect_panel,
            text="¿No tienes la app? Instálala",
            height=34,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT,
            command=self._show_install,
        ).grid(row=4, column=0, sticky="ew")

        self._connected_panel = ctk.CTkFrame(side, fg_color="transparent")
        self._connected_panel.grid_columnconfigure(0, weight=1)
        self._connected_title = ctk.CTkLabel(
            self._connected_panel, text="", font=ctk.CTkFont(size=17, weight="bold"), text_color=OK, anchor="w"
        )
        self._connected_title.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            self._connected_panel,
            text=(
                "Ahora elige la cámara «CelCam» en Discord, Zoom, Meet o Teams.\n\n"
                "En OBS: Fuentes > + > Dispositivo de captura de video > CelCam."
            ),
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            wraplength=270,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        options = ctk.CTkFrame(side, fg_color="transparent")
        options.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))
        options.grid_columnconfigure(1, weight=1)
        small = ctk.CTkFont(size=13)

        ctk.CTkLabel(options, text="Calidad", font=small, text_color=TEXT).grid(row=0, column=0, sticky="w", pady=4)
        self._quality = ctk.CTkOptionMenu(
            options,
            values=list(QUALITIES),
            width=110,
            height=28,
            fg_color=PANEL_ALT,
            button_color=PANEL_ALT,
            button_hover_color="#2E3340",
            text_color=TEXT,
            command=self._on_quality,
        )
        self._quality.set(self.core.settings.quality)
        self._quality.grid(row=0, column=1, sticky="e", pady=4)

        ctk.CTkLabel(options, text="Cuadros por segundo", font=small, text_color=TEXT).grid(
            row=1, column=0, sticky="w", pady=4
        )
        self._fps = ctk.CTkOptionMenu(
            options,
            values=[str(fps) for fps in FPS_OPTIONS],
            width=110,
            height=28,
            fg_color=PANEL_ALT,
            button_color=PANEL_ALT,
            button_hover_color="#2E3340",
            text_color=TEXT,
            command=self._on_fps,
        )
        self._fps.set(str(self.core.settings.fps))
        self._fps.grid(row=1, column=1, sticky="e", pady=4)

        ctk.CTkLabel(options, text="Girar", font=small, text_color=TEXT).grid(row=2, column=0, sticky="w", pady=4)
        self._turn_button = ctk.CTkButton(
            options,
            text=self._turn_text(),
            width=110,
            height=28,
            fg_color=PANEL_ALT,
            hover_color="#2E3340",
            text_color=TEXT,
            command=self._on_turn,
        )
        self._turn_button.grid(row=2, column=1, sticky="e", pady=4)

        self._mirror = ctk.BooleanVar(value=self.core.settings.mirror)
        self._fill = ctk.BooleanVar(value=self.core.settings.fill)
        for row, (label, variable) in enumerate((("Espejo", self._mirror), ("Llenar sin bordes", self._fill)), start=3):
            ctk.CTkLabel(options, text=label, font=small, text_color=TEXT).grid(row=row, column=0, sticky="w", pady=4)
            ctk.CTkSwitch(
                options, text="", variable=variable, width=44, progress_color=ACCENT, command=self._on_view_change
            ).grid(row=row, column=1, sticky="e", pady=4)

    def _show_problem(self, problem: str | None) -> None:
        self._notice.configure(text=problem or "", text_color=WARN if problem else MUTED)

    # Enlace y QR

    def _refresh_link(self) -> None:
        link = self.core.link()
        if link != self._last_link:
            self._last_link = link
            image = qr_image(link, QR_PIXELS)
            # A su tamaño exacto: escalado se ven borrosos los módulos y cuesta leerlo.
            self._qr_image = ctk.CTkImage(image, size=image.size)
            self._qr_label.configure(image=self._qr_image)

    # Acciones

    def _on_quality(self, quality: str) -> None:
        try:
            self.core.set_quality(quality)
        except CameraBusy:
            self._show_problem("La cámara está ocupada. Cierra la app que la usa y vuelve a elegir la calidad.")
            return
        self._show_problem(f"Calidad {quality}.")

    def _on_fps(self, value: str) -> None:
        try:
            self.core.set_fps(int(value))
        except CameraBusy:
            self._show_problem("La cámara está ocupada. Cierra la app que la usa y vuelve a elegir los cuadros.")
            return
        self._show_problem(f"{value} cuadros por segundo, si tu celular los permite.")

    def _turn_text(self) -> str:
        return f"{self.core.settings.extra_turns * 90}°"

    def _on_turn(self) -> None:
        self.core.settings.extra_turns = (self.core.settings.extra_turns + 1) % 4
        self._turn_button.configure(text=self._turn_text())
        self.core.apply_view()

    def _on_view_change(self) -> None:
        self.core.settings.mirror = bool(self._mirror.get())
        self.core.settings.fill = bool(self._fill.get())
        self.core.apply_view()

    def _connect_usb(self) -> None:
        if not self.core.adb.available() and not messagebox.askyesno(
            APP_NAME,
            "Para conectar por cable, CelCam necesita adb, una herramienta de Google (unos 8 MB).\n\n"
            f"¿Descargarla ahora? Al hacerlo aceptas los términos de Google:\n{ADB_TERMS_URL}",
        ):
            return
        self._show_problem("Buscando el celular por el cable…")

        def work() -> None:
            try:
                self.core.connect_usb()
                message = "Revisa el celular: toca «Conectar» si te lo pregunta."
                problem = False
            except UsbError as exc:
                message = str(exc)
                problem = True
            except Exception:
                log.exception("Falló la conexión por USB")
                message = "No se pudo conectar por cable. Prueba con el código QR."
                problem = True
            self.after(0, lambda: self._notice.configure(text=message, text_color=WARN if problem else MUTED))

        threading.Thread(target=work, name="celcam-usb", daemon=True).start()

    def _show_install(self) -> None:
        apk = paths.apk_file()
        hosts = local_hosts()
        if not apk.exists() or not hosts:
            messagebox.showerror(
                APP_NAME, "No se puede ofrecer la app: falta el instalador o la PC no está conectada a una red."
            )
            return
        server = ApkServer(apk)
        try:
            server.start()
        except OSError:
            log.exception("No se pudo abrir el puerto del APK")
            messagebox.showerror(APP_NAME, "No se pudo abrir el puerto para pasar la app al celular.")
            return
        self._apk_server = server

        dialog = ctk.CTkToplevel(self, fg_color=PANEL)
        dialog.title("Instalar CelCam en el celular")
        dialog.geometry("420x560")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        def close() -> None:
            server.stop()
            self._apk_server = None
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close)
        ctk.CTkLabel(
            dialog, text="Instala la app en el celular", font=ctk.CTkFont(size=18, weight="bold"), text_color=TEXT
        ).pack(pady=(20, 8))
        image = qr_image(server.url(hosts[0]), 240)
        self._apk_qr = ctk.CTkImage(image, size=image.size)
        ctk.CTkLabel(dialog, image=self._apk_qr, text="").pack(pady=6)
        steps = (
            "1. Conecta el celular al mismo WiFi que esta PC.\n"
            "2. Escanea este código con la cámara del celular.\n"
            "3. Descarga y abre «CelCam.apk».\n"
            "4. Si te lo pide, permite instalar apps de esta fuente.\n"
            "5. Si aparece Play Protect, toca «Más detalles» e «Instalar de todos modos»."
        )
        ctk.CTkLabel(dialog, text=steps, font=ctk.CTkFont(size=13), text_color=MUTED, justify="left", wraplength=370).pack(
            padx=20, pady=8
        )
        ctk.CTkButton(
            dialog,
            text="Listo",
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT,
            command=close,
        ).pack(pady=(4, 16))

    # Actualizaciones periódicas

    def _is_hidden(self) -> bool:
        return self.state() == "withdrawn"

    def _tick_preview(self) -> None:
        # Oculta, no gasta procesador en una vista previa que nadie ve.
        if not self._is_hidden():
            image = self.core.latest_image()
            if image is not None:
                self._photo.paste(self._to_preview(image))
            elif self._was_connected:
                self._photo.paste(self._blank)
        self.after(PREVIEW_MS, self._tick_preview)

    @staticmethod
    def _to_preview(image: np.ndarray) -> Image.Image:
        # Saltear píxeles alcanza para una vista previa y es mucho más liviano que escalar.
        step = max(1, image.shape[1] // PREVIEW_SIZE[0])
        small = image[::step, ::step, ::-1]
        return Image.fromarray(np.ascontiguousarray(small), "RGB").resize(PREVIEW_SIZE, Image.NEAREST)

    def _tick_status(self) -> None:
        if self._is_hidden():
            self.after(STATUS_MS, self._tick_status)
            return
        status = self.core.status()
        if status.connected != self._was_connected:
            self._was_connected = status.connected
            if status.connected:
                self._connect_panel.grid_remove()
                self._connected_panel.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 0))
                self._connected_title.configure(text=f"Conectado: {status.device}")
                self._photo.paste(self._blank)
            else:
                self._connected_panel.grid_remove()
                self._connect_panel.grid()
                self._photo.paste(self._blank)
        if not status.connected:
            self._refresh_link()
            self._state_label.configure(text="Esperando al celular", text_color=MUTED)
            self._stats_label.configure(text="")
        else:
            usage = " · en uso por una app" if status.camera_in_use else ""
            self._state_label.configure(text="Transmitiendo" + usage if status.streaming else "Conectado", text_color=OK)
            s = status.stats
            if s.source[0]:
                text = f"{s.source[0]}×{s.source[1]} · {s.fps:.0f} cuadros/s · {s.mbps:.1f} Mbps"
                if 0 < status.phone_fps < self.core.settings.fps:
                    text += f"\nCon esta cámara y calidad, tu celular llega a {status.phone_fps} cuadros/s."
                self._stats_label.configure(text=text, justify="left")
        self.after(STATUS_MS, self._tick_status)

    def _tick_requests(self) -> None:
        if self._tray.quit_requested.is_set():
            self._quit()
            return
        if self._tray.show_requested.is_set() or (self._show_signal and self._show_signal.requested()):
            self._tray.show_requested.clear()
            self._show_window()
        self.after(REQUESTS_MS, self._tick_requests)

    def _show_window(self) -> None:
        self.deiconify()
        self.lift()
        # Windows no deja que una ventana se ponga adelante sola: siempre visible, por un momento.
        self.attributes("-topmost", True)
        self.after(BRING_TO_FRONT_MS, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _on_close(self) -> None:
        if not self._has_tray:
            self._quit()
            return
        # La cámara y el celular siguen funcionando; para terminar está «Salir» en el ícono.
        self.withdraw()
        settings = self.core.settings
        if not settings.hide_notice_shown:
            settings.hide_notice_shown = True
            self.core.save_settings()
            self._tray.notify(
                "Para cerrarlo del todo, busca su ícono junto al reloj y elige «Salir».",
                f"{APP_NAME} sigue funcionando",
            )

    def _quit(self) -> None:
        self._tray.stop()
        if self._apk_server:
            self._apk_server.stop()
        self.destroy()
