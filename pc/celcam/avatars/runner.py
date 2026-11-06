"""Un avatar prendido: un hilo que lee los sensores, arma cada cuadro y se lo pasa a su cámara."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from . import make_scene
from .drawing import State
from .sensors import Motion, Sensors

log = logging.getLogger(__name__)

FPS_ACTIVE = 30
# Sin teclas, mouse ni voz, alcanza para respirar y parpadear.
FPS_IDLE = 15
IDLE_AFTER = 1.5
# Cuánto sigue mirando el teclado después de la última tecla.
TYPING_LOOK = 0.8


class AvatarRunner:
    def __init__(
        self,
        style: str,
        character: str,
        size: tuple[int, int],
        background: str,
        sensors: Sensors,
        on_frame: Callable[[np.ndarray], None],
        show_keys: bool = True,
        use_voice: bool = True,
        name: str = "",
    ) -> None:
        self._style = style
        self._character = character
        self._size = size
        self._background = background
        self._sensors = sensors
        self._on_frame = on_frame
        self._show_keys = show_keys
        self._use_voice = use_voice
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"celcam-avatar-{name}", daemon=True)
        self.ready = threading.Event()
        self.failed = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(5)

    def set_inputs(self, show_keys: bool, use_voice: bool) -> None:
        self._show_keys = show_keys
        if use_voice != self._use_voice:
            self._use_voice = use_voice
            if use_voice:
                self._sensors.start_voice()
            else:
                self._sensors.stop_voice()

    def _run(self) -> None:
        voice = self._use_voice
        if voice:
            self._sensors.start_voice()
        try:
            scene = make_scene(self._style, self._character, self._size, self._background)
            self._loop(scene)
        except Exception:
            log.exception("Falló el avatar %s/%s", self._style, self._character)
            self.failed = True
        finally:
            if self._use_voice:
                self._sensors.stop_voice()

    def _loop(self, scene) -> None:
        sensors = self._sensors
        motion = Motion()
        start = last = time.monotonic()
        last_mouse = sensors.mouse.position()
        last_activity = last_key = -10.0
        while not self._stop.is_set():
            now = time.monotonic()
            t = now - start
            dt = max(0.001, now - last)
            last = now
            keys = sensors.keyboard.read(now) if self._show_keys else ()
            mouse = sensors.mouse.position()
            click = sensors.mouse.click(now)
            voice = sensors.microphone.meter.level if self._use_voice else 0.0
            mouth = motion.update_mouth(voice, dt)
            if keys:
                last_key = now
            if keys or click or mouth > 0 or mouse != last_mouse:
                last_activity = now
            last_mouse = mouse
            state = State(
                keys=keys,
                mouse=mouse,
                click=click,
                mouth=mouth,
                blink=motion.blink(t),
                look=motion.update_look(mouse, now - last_key < TYPING_LOOK, dt),
                t=t,
            )
            self._on_frame(scene.to_camera(scene.frame(state)))
            self.ready.set()
            fps = FPS_ACTIVE if now - last_activity < IDLE_AFTER else FPS_IDLE
            self._stop.wait(max(0.001, 1 / fps - (time.monotonic() - now)))
