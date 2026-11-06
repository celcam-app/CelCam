"""La ventana de CelCam."""

from __future__ import annotations

import io
import logging
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import numpy as np
import segno
from PIL import Image, ImageTk

from . import APP_NAME, __version__, paths
from .apk_server import ApkServer
from .avatars import BACKGROUNDS, CHARACTERS, STYLES
from .core import CameraChannel, Core, PhoneControls, Status
from .identity import Identity
from .netinfo import local_hosts
from .settings import AVATAR_CORNERS, AVATAR_MODES, AVATAR_SIZES, FPS_OPTIONS, QUALITIES
from .tray import ShowSignal, Tray
from .usb import ADB_TERMS_URL, UsbError
from .vcam import CameraBusy

log = logging.getLogger(__name__)

BG = "#111318"
PANEL = "#1A1D24"
PANEL_ALT = "#242833"
PANEL_HOVER = "#2E3340"
TEXT = "#ECEFF4"
MUTED = "#969EAB"
ACCENT = "#2DD4BF"
ACCENT_HOVER = "#22B8A5"
ACCENT_TEXT = "#06241F"
# El texto de las pestañas es claro: sobre el turquesa de siempre no se lee.
SELECTED = "#0F766E"
SELECTED_HOVER = "#115E59"
OK = "#34D399"
WARN = "#FBBF24"
DANGER = "#F87171"

PREVIEW_SIZE = (640, 360)
PREVIEW_MS = 66
STATUS_MS = 500
REQUESTS_MS = 200
BRING_TO_FRONT_MS = 200
QR_PIXELS = 220
SIDE_WIDTH = 330
LOW_BATTERY = 15
# Mientras se arrastra el zoom, manda la mano; después, lo que informe el celular.
ZOOM_TOUCH_SECONDS = 1.0
# Conectado desde hace un rato y sin informar sus controles: es una app vieja.
OLD_APP_AFTER = 4.0
CHECKER = 14


def qr_image(text: str, pixels: int) -> Image.Image:
    # Corrección de errores baja: en una pantalla limpia alcanza, y el código sale menos denso.
    code = segno.make(text, error="l")
    buffer = io.BytesIO()
    scale = max(1, pixels // code.symbol_size(border=2)[0])
    code.save(buffer, kind="png", scale=scale, border=2, dark="#000000", light="#FFFFFF")
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def checkerboard(size: tuple[int, int]) -> Image.Image:
    """Los cuadros grises de siempre, para mostrar lo que es transparente."""
    rows = (np.arange(size[1]) // CHECKER)[:, None]
    columns = (np.arange(size[0]) // CHECKER)[None, :]
    value = np.where((rows + columns) % 2 == 0, 138, 201).astype(np.uint8)
    return Image.fromarray(np.dstack([value, value, value, np.full_like(value, 255)]), "RGBA")


def battery_text(status: Status) -> str:
    battery = status.battery
    if battery is None:
        return ""
    return f"Batería {battery.level} %" + (" · cargando" if battery.charging else "")


def key_for(names: dict, label: str):
    return next(key for key, value in names.items() if value == label)


class Window(ctk.CTk):
    def __init__(self, core: Core, camera_problem: str | None = None, show_signal: ShowSignal | None = None) -> None:
        super().__init__(fg_color=BG)
        self.core = core
        self._apk_server: ApkServer | None = None
        self._last_link = ""
        self._show_signal = show_signal
        self._index = 0
        self._panels_for: tuple | None = None
        self._was_connected = [False] * len(core.channels)
        self._connected_since = [0.0] * len(core.channels)
        self._low_warned = [False] * len(core.channels)
        self._zoom_touched = 0.0
        self._controls: PhoneControls | None = None
        self._filling = False
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1040x700")
        self.minsize(1040, 700)
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
        self._fill_from_settings()
        self._tick_preview()
        self._tick_status()
        self._tick_requests()

    @property
    def channel(self) -> CameraChannel:
        return self.core.channels[self._index]

    # Armado de la ventana

    def _build_preview(self) -> None:
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(20, 10), pady=20)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(header, text=APP_NAME, font=ctk.CTkFont(size=24, weight="bold"), text_color=TEXT).pack(side="left")
        self._state_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=14), text_color=MUTED)
        self._state_label.pack(side="right")
        self._battery_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=14, weight="bold"), text_color=MUTED)
        self._battery_label.pack(side="right", padx=(0, 16))

        self._tab_labels = [channel.name for channel in self.core.channels]
        self._tabs = ctk.CTkSegmentedButton(
            left,
            values=self._tab_labels,
            height=32,
            fg_color=PANEL,
            unselected_color=PANEL,
            unselected_hover_color=PANEL_HOVER,
            selected_color=SELECTED,
            selected_hover_color=SELECTED_HOVER,
            text_color=TEXT,
            command=self._on_tab,
        )
        self._tabs.set(self._tab_labels[0])
        self._tabs.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        holder = tk.Frame(left, bg="#000000")
        holder.grid(row=2, column=0, sticky="nsew")
        self._blank = Image.new("RGB", PREVIEW_SIZE, (0, 0, 0))
        self._checker = checkerboard(PREVIEW_SIZE)
        self._photo = ImageTk.PhotoImage(self._blank)
        self._preview = tk.Label(holder, image=self._photo, bg="#000000", bd=0)
        self._preview.pack(expand=True)

        self._build_phone_bar(left)

        self._notice = ctk.CTkLabel(
            left, text="", font=ctk.CTkFont(size=13), text_color=MUTED, justify="left", wraplength=640, anchor="w"
        )
        self._notice.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self._stats_label = ctk.CTkLabel(left, text="", font=ctk.CTkFont(size=13), text_color=MUTED, anchor="w")
        self._stats_label.grid(row=5, column=0, sticky="ew")

    def _small_button(self, parent, text: str, command, width: int = 92) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, width=width, height=30, fg_color=PANEL_ALT, hover_color=PANEL_HOVER, text_color=TEXT, text_color_disabled="#5B6270", command=command
        )

    def _build_phone_bar(self, parent) -> None:
        """Lo mismo que se maneja desde la pantalla del celular."""
        bar = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10)
        bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        bar.grid_columnconfigure(3, weight=1)
        self._switch_button = self._small_button(bar, "Cambiar cámara", self._on_switch, 112)
        self._switch_button.grid(row=0, column=0, padx=(8, 4), pady=8)
        self._torch_button = self._small_button(bar, "Linterna", self._on_torch)
        self._torch_button.grid(row=0, column=1, padx=4, pady=8)
        self._zoom_label = ctk.CTkLabel(bar, text="Zoom", font=ctk.CTkFont(size=13), text_color=MUTED, width=64)
        self._zoom_label.grid(row=0, column=2, padx=(6, 0))
        self._zoom_slider = ctk.CTkSlider(bar, from_=0, to=1, progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER, command=self._on_zoom)
        self._zoom_slider.grid(row=0, column=3, sticky="ew", padx=4)
        self._dim_button = self._small_button(bar, "Oscurecer", self._on_dim)
        self._dim_button.grid(row=0, column=4, padx=4, pady=8)
        self._stop_button = self._small_button(bar, "Detener", self._on_stop, 76)
        self._stop_button.grid(row=0, column=5, padx=(4, 8), pady=8)

    def _build_side(self) -> None:
        side = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=14, width=SIDE_WIDTH)
        side.grid(row=0, column=1, sticky="ns", padx=(10, 20), pady=20)
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(1, weight=1)

        self._side_tabs = ctk.CTkSegmentedButton(
            side,
            values=["Imagen", "Avatar"],
            height=30,
            fg_color=PANEL_ALT,
            unselected_color=PANEL_ALT,
            unselected_hover_color=PANEL_HOVER,
            selected_color=SELECTED,
            selected_hover_color=SELECTED_HOVER,
            text_color=TEXT,
            command=self._on_side_tab,
        )
        self._side_tabs.set("Imagen")
        self._side_tabs.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 10))

        self._image_page = ctk.CTkFrame(side, fg_color="transparent")
        self._image_page.grid(row=1, column=0, sticky="nsew")
        self._image_page.grid_columnconfigure(0, weight=1)
        self._image_page.grid_rowconfigure(1, weight=1)
        self._avatar_page = ctk.CTkFrame(side, fg_color="transparent")
        self._avatar_page.grid_columnconfigure(0, weight=1)

        self._build_connect_panels(self._image_page)
        self._build_image_options(self._image_page)
        self._build_avatar_page(self._avatar_page)

    def _build_connect_panels(self, parent) -> None:
        self._connect_panel = ctk.CTkFrame(parent, fg_color="transparent")
        self._connect_panel.grid(row=0, column=0, sticky="ew", padx=20)
        self._connect_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self._connect_panel, text="Conecta un celular", font=ctk.CTkFont(size=17, weight="bold"), text_color=TEXT
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            self._connect_panel,
            text="Abre CelCam en el celular y toca «Escanear código». Cada celular va a su propia cámara.",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            wraplength=285,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 6))
        self._qr_label = ctk.CTkLabel(self._connect_panel, text="")
        self._qr_label.grid(row=2, column=0, pady=(0, 6))
        ctk.CTkButton(
            self._connect_panel,
            text="Conectar por cable USB",
            height=32,
            fg_color=PANEL_ALT,
            hover_color=PANEL_HOVER,
            text_color=TEXT,
            command=self._connect_usb,
        ).grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkButton(
            self._connect_panel,
            text="¿No tienes la app? Instálala",
            height=32,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT,
            command=self._show_install,
        ).grid(row=4, column=0, sticky="ew")

        self._connected_panel = ctk.CTkFrame(parent, fg_color="transparent")
        self._connected_panel.grid_columnconfigure(0, weight=1)
        self._connected_title = ctk.CTkLabel(
            self._connected_panel, text="", font=ctk.CTkFont(size=17, weight="bold"), text_color=OK, anchor="w", wraplength=285, justify="left"
        )
        self._connected_title.grid(row=0, column=0, sticky="ew")
        self._connected_help = ctk.CTkLabel(
            self._connected_panel, text="", font=ctk.CTkFont(size=13), text_color=MUTED, wraplength=285, justify="left"
        )
        self._connected_help.grid(row=1, column=0, sticky="w", pady=(4, 10))
        self._another_button = ctk.CTkButton(
            self._connected_panel,
            text="Conectar otro celular",
            height=32,
            fg_color=PANEL_ALT,
            hover_color=PANEL_HOVER,
            text_color=TEXT,
            command=self._go_to_free_camera,
        )
        self._another_button.grid(row=2, column=0, sticky="ew")

    def _menu(self, parent, values, command, width: int = 170) -> ctk.CTkOptionMenu:
        return ctk.CTkOptionMenu(
            parent, values=values, width=width, height=28, fg_color=PANEL_ALT, button_color=PANEL_ALT, button_hover_color=PANEL_HOVER, text_color=TEXT, command=command
        )

    def _build_image_options(self, parent) -> None:
        options = ctk.CTkFrame(parent, fg_color="transparent")
        options.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 14))
        options.grid_columnconfigure(1, weight=1)
        small = ctk.CTkFont(size=13)

        ctk.CTkLabel(options, text="Calidad", font=small, text_color=TEXT).grid(row=0, column=0, sticky="w", pady=4)
        self._quality = self._menu(options, list(QUALITIES), self._on_quality, 110)
        self._quality.grid(row=0, column=1, sticky="e", pady=4)
        ctk.CTkLabel(options, text="Cuadros por segundo", font=small, text_color=TEXT).grid(row=1, column=0, sticky="w", pady=4)
        self._fps = self._menu(options, [str(fps) for fps in FPS_OPTIONS], self._on_fps, 110)
        self._fps.grid(row=1, column=1, sticky="e", pady=4)
        ctk.CTkLabel(options, text="Girar", font=small, text_color=TEXT).grid(row=2, column=0, sticky="w", pady=4)
        self._turn_button = ctk.CTkButton(
            options, text="", width=110, height=28, fg_color=PANEL_ALT, hover_color=PANEL_HOVER, text_color=TEXT, command=self._on_turn
        )
        self._turn_button.grid(row=2, column=1, sticky="e", pady=4)
        self._mirror = ctk.BooleanVar(value=False)
        self._fill = ctk.BooleanVar(value=False)
        for row, (label, variable) in enumerate((("Espejo", self._mirror), ("Llenar sin bordes", self._fill)), start=3):
            ctk.CTkLabel(options, text=label, font=small, text_color=TEXT).grid(row=row, column=0, sticky="w", pady=4)
            ctk.CTkSwitch(
                options, text="", variable=variable, width=44, progress_color=ACCENT, command=self._on_view_change
            ).grid(row=row, column=1, sticky="e", pady=4)

    def _build_avatar_page(self, parent) -> None:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid(row=0, column=0, sticky="ew", padx=20)
        page.grid_columnconfigure(1, weight=1)
        small = ctk.CTkFont(size=13)

        rows = []

        def add(label: str, widget) -> None:
            row = len(rows)
            caption = ctk.CTkLabel(page, text=label, font=small, text_color=TEXT)
            caption.grid(row=row, column=0, sticky="w", pady=4)
            widget.grid(row=row, column=1, sticky="e", pady=4)
            rows.append((caption, widget))

        self._avatar_mode = self._menu(page, list(AVATAR_MODES.values()), self._on_avatar_change)
        add("Mostrar", self._avatar_mode)
        self._avatar_style = self._menu(page, list(STYLES.values()), self._on_avatar_change)
        add("Estilo", self._avatar_style)
        self._avatar_id = self._menu(page, [c.name for c in CHARACTERS], self._on_avatar_change)
        add("Personaje", self._avatar_id)
        self._avatar_background = self._menu(page, list(BACKGROUNDS.values()), self._on_avatar_change)
        add("Fondo", self._avatar_background)
        self._background_row = rows[-1]
        self._avatar_corner = self._menu(page, list(AVATAR_CORNERS.values()), self._on_avatar_place)
        add("Esquina", self._avatar_corner)
        self._corner_row = rows[-1]
        self._avatar_size = self._menu(page, list(AVATAR_SIZES.values()), self._on_avatar_change)
        add("Tamaño", self._avatar_size)
        self._size_row = rows[-1]

        self._avatar_keys = ctk.BooleanVar(value=True)
        self._avatar_voice = ctk.BooleanVar(value=True)
        for label, variable in (("Mostrar mis teclas", self._avatar_keys), ("Mover la boca con mi voz", self._avatar_voice)):
            add(label, ctk.CTkSwitch(page, text="", variable=variable, width=44, progress_color=ACCENT, command=self._on_avatar_inputs))

        self._avatar_note = ctk.CTkLabel(
            parent, text="", font=small, text_color=MUTED, wraplength=285, justify="left", anchor="w"
        )
        self._avatar_note.grid(row=1, column=0, sticky="ew", padx=20, pady=(10, 0))

    def _show_problem(self, problem: str | None) -> None:
        self._notice.configure(text=problem or "", text_color=WARN if problem else MUTED)

    def _fill_from_settings(self) -> None:
        """Lo elegido para la cámara que se está mirando."""
        self._filling = True
        s = self.channel.settings
        self._quality.set(s.quality)
        self._fps.set(str(s.fps))
        self._turn_button.configure(text=f"{s.extra_turns * 90}°")
        self._mirror.set(s.mirror)
        self._fill.set(s.fill)
        self._avatar_mode.set(AVATAR_MODES[s.avatar_mode])
        self._avatar_style.set(STYLES[s.avatar_style])
        self._avatar_id.set(next(c.name for c in CHARACTERS if c.id == s.avatar_id))
        self._avatar_background.set(BACKGROUNDS[s.avatar_background])
        self._avatar_corner.set(AVATAR_CORNERS[s.avatar_corner])
        self._avatar_size.set(AVATAR_SIZES[s.avatar_size])
        self._avatar_keys.set(s.avatar_keys)
        self._avatar_voice.set(s.avatar_voice)
        self._filling = False
        self._update_avatar_rows()

    def _update_avatar_rows(self) -> None:
        s = self.channel.settings
        over_video = s.avatar_mode == "sobre"
        for row, visible in ((self._corner_row, over_video), (self._size_row, over_video), (self._background_row, s.avatar_mode == "solo")):
            for widget in row:
                widget.grid() if visible else widget.grid_remove()
        notes = []
        if s.avatar_mode != "apagado":
            if s.avatar_keys:
                notes.append("Las teclas que tocas se ven en la cámara: apaga «Mostrar mis teclas» antes de escribir una contraseña.")
            if s.avatar_voice:
                notes.append("Tu voz solo mueve la boca: no se graba ni se envía.")
            if s.avatar_mode == "solo" and s.avatar_background == "transparente":
                notes.append("Transparente funciona en OBS. Discord, Zoom y Meet no lo admiten y muestran un fondo oscuro.")
            if s.avatar_mode == "solo" and s.avatar_background == "verde":
                notes.append("En OBS, agrega a la cámara el filtro «Clave de croma» para quitar el verde.")
        else:
            notes.append("Elige «Solo el avatar» para usarlo sin celular, o «Sobre el video» para verlo en una esquina de tu imagen.")
        self._avatar_note.configure(text="\n\n".join(notes))

    # Enlace y QR

    def _refresh_link(self) -> None:
        link = self.core.link()
        if link != self._last_link:
            self._last_link = link
            image = qr_image(link, QR_PIXELS)
            # A su tamaño exacto: escalado se ven borrosos los módulos y cuesta leerlo.
            self._qr_image = ctk.CTkImage(image, size=image.size)
            self._qr_label.configure(image=self._qr_image)

    # Pestañas

    def _select_camera(self, index: int) -> None:
        self._index = index
        self._tabs.set(self._tab_labels[index])
        self._panels_for = None
        self._zoom_touched = 0.0
        self._photo.paste(self._blank)
        self._show_problem(None)
        self._fill_from_settings()
        self._tick_status(once=True)

    def _on_tab(self, label: str) -> None:
        self._select_camera(self._tab_labels.index(label))

    def _on_side_tab(self, label: str) -> None:
        if label == "Avatar":
            self._image_page.grid_remove()
            self._avatar_page.grid(row=1, column=0, sticky="nsew")
        else:
            self._avatar_page.grid_remove()
            self._image_page.grid()

    def _go_to_free_camera(self) -> None:
        for channel in self.core.channels:
            if not channel.status().connected:
                self._select_camera(channel.index)
                return

    # Acciones de la imagen

    def _on_quality(self, quality: str) -> None:
        try:
            self.channel.set_quality(quality)
        except CameraBusy:
            self._show_problem("La cámara está ocupada. Cierra la app que la usa y vuelve a elegir la calidad.")
            return
        self._show_problem(f"Calidad {quality}.")

    def _on_fps(self, value: str) -> None:
        try:
            self.channel.set_fps(int(value))
        except CameraBusy:
            self._show_problem("La cámara está ocupada. Cierra la app que la usa y vuelve a elegir los cuadros.")
            return
        self._show_problem(f"{value} cuadros por segundo, si tu celular los permite.")

    def _on_turn(self) -> None:
        s = self.channel.settings
        s.extra_turns = (s.extra_turns + 1) % 4
        self._turn_button.configure(text=f"{s.extra_turns * 90}°")
        self.channel.apply_view()

    def _on_view_change(self) -> None:
        s = self.channel.settings
        s.mirror = bool(self._mirror.get())
        s.fill = bool(self._fill.get())
        self.channel.apply_view()

    # Acciones del celular

    def _on_switch(self) -> None:
        self.channel.switch_camera()

    def _on_torch(self) -> None:
        if self._controls:
            self.channel.set_torch(not self._controls.torch)

    def _on_dim(self) -> None:
        if self._controls:
            self.channel.set_dim(not self._controls.dim)

    def _on_zoom(self, position: float) -> None:
        controls = self._controls
        if not controls:
            return
        self._zoom_touched = time.monotonic()
        value = controls.zoom_min + (controls.zoom_max - controls.zoom_min) * position
        self._zoom_label.configure(text=f"Zoom {value:.1f}×")
        self.channel.set_zoom(value)

    def _on_stop(self) -> None:
        self.channel.stop_phone()
        self._show_problem("Se detuvo el celular. Para volver a transmitir, toca Conectar en el celular.")

    def _render_phone_bar(self, status: Status) -> None:
        controls = status.controls
        self._controls = controls
        ready = status.connected and controls is not None
        state = "normal" if ready else "disabled"
        self._stop_button.configure(state="normal" if status.connected and (ready or self._looks_old()) else "disabled")
        self._switch_button.configure(state="normal" if ready and controls.can_switch else "disabled")
        self._torch_button.configure(state="normal" if ready and controls.torch_available else "disabled", text="Apagar luz" if ready and controls.torch else "Linterna")
        self._dim_button.configure(state=state, text="Encender" if ready and controls.dim else "Oscurecer")
        can_zoom = ready and controls.zoom_max > controls.zoom_min
        self._zoom_slider.configure(state="normal" if can_zoom else "disabled")
        if can_zoom and time.monotonic() - self._zoom_touched > ZOOM_TOUCH_SECONDS:
            self._zoom_slider.set((controls.zoom - controls.zoom_min) / (controls.zoom_max - controls.zoom_min))
            self._zoom_label.configure(text=f"Zoom {controls.zoom:.1f}×")
        elif not can_zoom:
            self._zoom_label.configure(text="Zoom")
        low = status.battery is not None and status.battery.level <= LOW_BATTERY and not status.battery.charging
        self._battery_label.configure(text=battery_text(status), text_color=DANGER if low else MUTED)

    def _looks_old(self) -> bool:
        return time.monotonic() - self._connected_since[self._index] > OLD_APP_AFTER

    # Acciones del avatar

    def _read_avatar_options(self) -> None:
        s = self.channel.settings
        s.avatar_mode = key_for(AVATAR_MODES, self._avatar_mode.get())
        s.avatar_style = key_for(STYLES, self._avatar_style.get())
        s.avatar_id = next(c.id for c in CHARACTERS if c.name == self._avatar_id.get())
        s.avatar_background = key_for(BACKGROUNDS, self._avatar_background.get())
        s.avatar_corner = key_for(AVATAR_CORNERS, self._avatar_corner.get())
        s.avatar_size = key_for(AVATAR_SIZES, self._avatar_size.get())

    def _on_avatar_change(self, _value: str) -> None:
        if self._filling:
            return
        self._read_avatar_options()
        self._update_avatar_rows()
        # Dibujarlo lleva un momento: la ventana no se traba.
        threading.Thread(target=self.channel.apply_avatar, name="celcam-avatar-cambio", daemon=True).start()

    def _on_avatar_place(self, _value: str) -> None:
        if not self._filling:
            self._read_avatar_options()
            self.core.save_settings()

    def _on_avatar_inputs(self) -> None:
        s = self.channel.settings
        s.avatar_keys = bool(self._avatar_keys.get())
        s.avatar_voice = bool(self._avatar_voice.get())
        self._update_avatar_rows()
        self.channel.update_avatar_inputs()

    def _connect_usb(self) -> None:
        if not self.core.adb.available() and not messagebox.askyesno(
            APP_NAME,
            "Para conectar por cable, CelCam necesita adb, una herramienta de Google (unos 8 MB).\n\n"
            f"¿Descargarla ahora? Al hacerlo aceptas los términos de Google:\n{ADB_TERMS_URL}",
        ):
            return
        self._show_problem("Buscando celulares por el cable…")

        def work() -> None:
            try:
                count = len(self.core.connect_usb())
                message = "Revisa el celular: toca «Conectar» si te lo pregunta." if count == 1 else f"Revisa los {count} celulares: toca «Conectar» si te lo preguntan."
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
            image = self.channel.latest_image()
            if image is not None:
                self._photo.paste(self._to_preview(image))
            else:
                self._photo.paste(self._blank)
        self.after(PREVIEW_MS, self._tick_preview)

    def _to_preview(self, image: np.ndarray) -> Image.Image:
        # Saltear píxeles alcanza para una vista previa y es mucho más liviano que escalar.
        step = max(1, image.shape[1] // PREVIEW_SIZE[0])
        small = image[::step, ::step]
        size = (small.shape[1], small.shape[0])
        raw = np.ascontiguousarray(small).tobytes()
        if small.shape[2] == 4:
            picture = Image.frombuffer("RGBA", size, raw, "raw", "BGRA", 0, 1).resize(PREVIEW_SIZE, Image.NEAREST)
            shown = self._checker.copy()
            shown.alpha_composite(picture)
            return shown.convert("RGB")
        return Image.frombuffer("RGB", size, raw, "raw", "BGR", 0, 1).resize(PREVIEW_SIZE, Image.NEAREST)

    def _tick_tabs(self, statuses: list[Status]) -> None:
        now = time.monotonic()
        for channel, status in zip(self.core.channels, statuses):
            index = channel.index
            if status.connected and not self._was_connected[index]:
                self._connected_since[index] = now
                self._low_warned[index] = False
                # Un celular recién conectado se muestra, si no se está mirando otro conectado.
                if not statuses[self._index].connected and index != self._index:
                    self._select_camera(index)
            self._was_connected[index] = status.connected
            battery = status.battery
            if battery and battery.level <= LOW_BATTERY and not battery.charging and not self._low_warned[index]:
                self._low_warned[index] = True
                self._tray.notify(f"{status.device}: queda {battery.level} % de batería.", f"{APP_NAME}: poca batería")
        labels = [("● " if status.connected else "") + channel.name for channel, status in zip(self.core.channels, statuses)]
        if labels != self._tab_labels:
            self._tab_labels = labels
            self._tabs.configure(values=labels)
            self._tabs.set(labels[self._index])

    def _render_panels(self, status: Status, statuses: list[Status]) -> None:
        key = (self._index, status.connected, status.device)
        if key == self._panels_for:
            return
        self._panels_for = key
        if status.connected:
            self._connect_panel.grid_remove()
            self._connected_panel.grid(row=0, column=0, sticky="ew", padx=20)
            name = self.channel.name
            self._connected_title.configure(text=f"Conectado: {status.device}")
            self._connected_help.configure(
                text=(
                    f"Ahora elige la cámara «{name}» en Discord, Zoom, Meet o Teams.\n\n"
                    f"En OBS: Fuentes > + > Dispositivo de captura de video > {name}."
                )
            )
        else:
            self._connected_panel.grid_remove()
            self._connect_panel.grid()
        self._photo.paste(self._blank)

    def _tick_status(self, once: bool = False) -> None:
        if self._is_hidden() and not once:
            self.after(STATUS_MS, self._tick_status)
            return
        statuses = [channel.status() for channel in self.core.channels]
        self._tick_tabs(statuses)
        status = statuses[self._index]
        self._render_panels(status, statuses)
        self._another_button.configure(state="normal" if not all(s.connected for s in statuses) else "disabled")
        self._render_phone_bar(status)
        self._render_state(status)
        if not once:
            self.after(STATUS_MS, self._tick_status)

    def _render_state(self, status: Status) -> None:
        settings = self.channel.settings
        usage = " · en uso por una app" if status.camera_in_use else ""
        if not status.connected:
            self._refresh_link()
        if settings.avatar_mode == "solo":
            if status.avatar_failed:
                self._state_label.configure(text="No se pudo mostrar el avatar", text_color=WARN)
            elif status.avatar_ready:
                self._state_label.configure(text="Avatar" + usage, text_color=OK)
            else:
                self._state_label.configure(text="Dibujando el avatar…", text_color=MUTED)
            self._stats_label.configure(text="")
            return
        if not status.connected:
            self._state_label.configure(text="Esperando al celular", text_color=MUTED)
            self._stats_label.configure(text="")
            return
        self._state_label.configure(text="Transmitiendo" + usage if status.streaming else "Conectado", text_color=OK)
        s = status.stats
        text = ""
        if s.source[0]:
            text = f"{s.source[0]}×{s.source[1]} · {s.fps:.0f} cuadros/s · {s.mbps:.1f} Mbps"
            if 0 < status.phone_fps < settings.fps:
                text += f"\nCon esta cámara y calidad, tu celular llega a {status.phone_fps} cuadros/s."
        if status.controls is None and self._looks_old():
            text = (text + "\n" if text else "") + "Actualiza la app del celular para manejarlo desde aquí."
        self._stats_label.configure(text=text, justify="left")

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
