"""Lo que mueve al avatar: teclas, mouse y volumen de la voz.

Nada de esto se guarda ni sale de la PC. De las teclas solo se mira cuáles están apretadas en este momento, y del
micrófono solo cuánto volumen hay: el sonido se descarta enseguida, y se lee únicamente con un avatar prendido.
"""

from __future__ import annotations

import ctypes
import logging
import math
import random
import threading
from ctypes import wintypes

import numpy as np

log = logging.getLogger(__name__)

VK_LBUTTON, VK_RBUTTON = 0x01, 0x02
# Una tecla tocada muy rápido igual tiene que verse encendida un momento.
MIN_LIT = 0.12

SPECIAL_KEYS = {"Shift": 0x10, "Enter": 0x0D, "Ctrl": 0x11, "Alt": 0x12, "Espacio": 0x20, "Tab": 0x09, "Ñ": 0xC0}


def key_codes() -> dict[str, int]:
    codes = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
    codes.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
    codes.update(SPECIAL_KEYS)
    return codes


class Keyboard:
    """Sin ganchos en el sistema: solo pregunta qué está apretado, y solo mientras se muestran las teclas."""

    def __init__(self) -> None:
        self._codes = key_codes()
        self._state = ctypes.windll.user32.GetAsyncKeyState
        self._state.restype = ctypes.c_short
        self._state.argtypes = [ctypes.c_int]
        self._pressed: list[str] = []
        self._until: dict[str, float] = {}

    def read(self, now: float) -> tuple[str, ...]:
        for name, code in self._codes.items():
            state = self._state(code)
            if state & 0x8000 or state & 1:
                self._until[name] = now + MIN_LIT
                if name not in self._pressed:
                    self._pressed.append(name)
        for name in list(self._pressed):
            if now >= self._until.get(name, 0) and not self._state(self._codes[name]) & 0x8000:
                self._pressed.remove(name)
        # La última de la lista es la más reciente: esa es la que busca la mano.
        return tuple(self._pressed)


class Mouse:
    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32
        self._user32.GetAsyncKeyState.restype = ctypes.c_short
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._click: str | None = None
        self._click_until = 0.0

    def position(self) -> tuple[float, float]:
        """De 0 a 1 en el conjunto de las pantallas."""
        point = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        metrics = self._user32.GetSystemMetrics
        left, top, width, height = metrics(76), metrics(77), metrics(78), metrics(79)
        return (
            max(0.0, min(1.0, (point.x - left) / max(1, width))),
            max(0.0, min(1.0, (point.y - top) / max(1, height))),
        )

    def click(self, now: float) -> str | None:
        state = self._user32.GetAsyncKeyState
        for code, name in ((VK_LBUTTON, "L"), (VK_RBUTTON, "R")):
            value = state(code)
            if value & 0x8000 or value & 1:
                self._click, self._click_until = name, now + MIN_LIT
                return name
        return self._click if now < self._click_until else None


class WaveFormat(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WaveHeader(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_EVENT = 0x00050000
WHDR_DONE = 1
RATE = 16000
CHUNK_SAMPLES = 480
BUFFERS = 4
# Por debajo de esto no es voz, aunque el micrófono esté muy bajo.
FLOOR_LIMITS = (-72.0, -32.0)
ABOVE_FLOOR = 7.0
SPEECH_RANGE = 24.0


def decibels(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -90.0
    rms = math.sqrt(float(np.mean(np.square(samples.astype(np.float32) / 32768.0))))
    return 20 * math.log10(max(rms, 1e-5))


class VoiceMeter:
    """Convierte volumen en cuánto se abre la boca, adaptándose al ruido de la habitación."""

    def __init__(self) -> None:
        self._floor = -55.0
        self.level = 0.0

    def feed(self, db: float) -> None:
        # El ruido de fondo se sigue despacio hacia arriba y enseguida hacia abajo.
        self._floor = min(db, self._floor + 0.02)
        self._floor = max(FLOOR_LIMITS[0], min(FLOOR_LIMITS[1], self._floor))
        self.level = max(0.0, min(1.0, (db - self._floor - ABOVE_FLOOR) / SPEECH_RANGE))


class Microphone:
    """Mide el volumen del micrófono predeterminado, sin guardar ni mandar el sonido a ningún lado."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.meter = VoiceMeter()
        self.available = True

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self.available = True
            self._thread = threading.Thread(target=self._run, name="celcam-voz", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread:
            thread.join(2)
        self.meter.level = 0.0

    def _run(self) -> None:
        winmm = ctypes.WinDLL("winmm")
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, ctypes.c_wchar_p]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        winmm.waveInOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(WaveFormat), ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
        for name in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
            getattr(winmm, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(WaveHeader), wintypes.UINT]
        for name in ("waveInStart", "waveInReset", "waveInClose"):
            getattr(winmm, name).argtypes = [wintypes.HANDLE]
        event = kernel32.CreateEventW(None, False, False, None)
        handle = wintypes.HANDLE()
        wave = WaveFormat(1, 1, RATE, RATE * 2, 2, 16, 0)
        result = winmm.waveInOpen(ctypes.byref(handle), WAVE_MAPPER, ctypes.byref(wave), event, 0, CALLBACK_EVENT)
        if result != 0:
            log.info("Sin micrófono para el avatar (código %s)", result)
            self.available = False
            kernel32.CloseHandle(event)
            return
        size = ctypes.sizeof(WaveHeader)
        chunks = [ctypes.create_string_buffer(CHUNK_SAMPLES * 2) for _ in range(BUFFERS)]
        headers = [WaveHeader(ctypes.cast(chunk, ctypes.c_void_p), CHUNK_SAMPLES * 2, 0, 0, 0, 0, None, 0) for chunk in chunks]
        try:
            for header in headers:
                winmm.waveInPrepareHeader(handle, ctypes.byref(header), size)
                winmm.waveInAddBuffer(handle, ctypes.byref(header), size)
            winmm.waveInStart(handle)
            while not self._stop.is_set():
                kernel32.WaitForSingleObject(event, 200)
                for chunk, header in zip(chunks, headers):
                    if header.dwFlags & WHDR_DONE:
                        count = header.dwBytesRecorded // 2
                        self.meter.feed(decibels(np.frombuffer(chunk, dtype=np.int16, count=count)))
                        header.dwFlags &= ~WHDR_DONE
                        winmm.waveInAddBuffer(handle, ctypes.byref(header), size)
        finally:
            winmm.waveInReset(handle)
            for header in headers:
                winmm.waveInUnprepareHeader(handle, ctypes.byref(header), size)
            winmm.waveInClose(handle)
            kernel32.CloseHandle(event)


class Sensors:
    """Lo comparten todos los avatares prendidos. El micrófono se abre solo si alguno lo usa."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voice_users = 0
        self.keyboard = Keyboard()
        self.mouse = Mouse()
        self.microphone = Microphone()

    def start_voice(self) -> None:
        with self._lock:
            self._voice_users += 1
            if self._voice_users == 1:
                self.microphone.start()

    def stop_voice(self) -> None:
        with self._lock:
            self._voice_users = max(0, self._voice_users - 1)
            if self._voice_users == 0:
                self.microphone.stop()


class Motion:
    """Lo que no sale directo de un sensor: la boca suavizada, el parpadeo y hacia dónde mira. Uno por avatar."""

    def __init__(self, seed: int | None = None) -> None:
        self._random = random.Random(seed)
        self.mouth = 0.0
        self.look = (0.0, 0.1)
        self._next_blink = 2.0
        self._blink_until = 0.0

    def update_mouth(self, target: float, dt: float) -> float:
        # Se abre rápido y se cierra un poco más despacio, como una boca.
        speed = 0.045 if target > self.mouth else 0.11
        self.mouth += (target - self.mouth) * (1 - math.exp(-dt / speed))
        return self.mouth if self.mouth > 0.02 else 0.0

    def blink(self, t: float) -> bool:
        if t >= self._next_blink:
            self._blink_until = t + 0.11
            self._next_blink = t + self._random.uniform(2.5, 6.0)
        return t < self._blink_until

    def update_look(self, mouse: tuple[float, float], typing: bool, dt: float) -> tuple[float, float]:
        # Escribiendo mira el teclado; si no, la mano del mouse, que se ve a la izquierda.
        target = (0.35, 0.55) if typing else ((0.5 - mouse[0]) * 0.9 - 0.2, 0.15 + mouse[1] * 0.45)
        follow = 1 - math.exp(-dt / 0.12)
        self.look = (self.look[0] + (target[0] - self.look[0]) * follow, self.look[1] + (target[1] - self.look[1]) * follow)
        return self.look
