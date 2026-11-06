"""Servidor TLS que espera a los celulares. Cada cámara tiene una sola sesión: la más nueva reemplaza a la anterior."""

from __future__ import annotations

import hmac
import logging
import queue
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import BinaryIO, Callable

from . import __version__
from .identity import Identity
from .protocol import (
    DEFAULT_PORT,
    PROTO,
    TYPE_CONTROL,
    TYPE_VIDEO,
    ConnectionClosed,
    ProtocolError,
    VideoFrame,
    pack_control,
    pack_reply,
    parse_control,
    parse_video,
    read_hello,
    read_message,
)

log = logging.getLogger(__name__)

HANDSHAKE_TIMEOUT = 8.0
# El celular manda un ping cada 2 s aunque no tenga cuadros.
READ_TIMEOUT = 15.0
RETIRE_WAIT = 2.0
MAX_PENDING = 16
PORT_ATTEMPTS = 10


@dataclass(frozen=True)
class StreamConfig:
    width: int
    height: int
    fps: int
    kbps: int

    def as_command(self) -> dict:
        return {"cmd": "config", "w": self.width, "h": self.height, "fps": self.fps, "kbps": self.kbps}


class Session:
    """Conexión autenticada.

    OpenSSL no admite leer y escribir a la vez sobre el mismo objeto desde hilos
    distintos, así que solo el hilo de la sesión toca el TLS: los comandos quedan
    en cola y salen después de cada mensaje recibido (como mínimo, cada 2 s).
    """

    def __init__(self, sock: ssl.SSLSocket, reader: BinaryIO, device: str, slot: int = 0) -> None:
        self.device = device
        self.reader = reader
        # La cámara a la que manda imagen.
        self.slot = slot
        self._sock = sock
        self._outbox: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self._latest: dict[str, dict] = {}
        self._latest_lock = threading.Lock()
        self.retiring = False
        # El celular cerró a propósito (por ejemplo, con «Detener»), no fue una falla de la red.
        self.closed_by_phone = False

    def send_control(self, command: dict) -> None:
        self._outbox.put(command)

    def send_latest(self, key: str, command: dict) -> None:
        """Para lo que cambia sin parar: si todavía no salió el anterior, este lo reemplaza."""
        with self._latest_lock:
            self._latest[key] = command

    def retire(self) -> None:
        """Otro celular tomó el lugar: se le avisa a este para que no vuelva a conectarse solo."""
        self.send_control({"cmd": "bye"})
        self.retiring = True
        threading.Timer(RETIRE_WAIT, self.interrupt).start()

    def flush(self) -> None:
        while True:
            try:
                command = self._outbox.get_nowait()
            except queue.Empty:
                break
            self._sock.sendall(pack_control(command))
        with self._latest_lock:
            latest, self._latest = self._latest, {}
        for command in latest.values():
            self._sock.sendall(pack_control(command))

    def interrupt(self) -> None:
        """Corta la conexión desde cualquier hilo; el hilo de la sesión hace la limpieza."""
        try:
            socket.socket.shutdown(self._sock, socket.SHUT_RDWR)
        except OSError:
            pass

    def close(self) -> None:
        self.interrupt()
        self.reader.close()
        self._sock.close()


class Listener:
    """Lo que el resto del programa quiere saber de las sesiones."""

    def place(self, phone_id: str, device: str) -> int | None:
        """La cámara que le toca a este celular; None si no hay lugar."""
        return 0

    def on_connected(self, session: Session) -> None: ...
    def on_video(self, session: Session, frame: VideoFrame) -> None: ...
    def on_capture(self, session: Session, report: dict) -> None: ...
    def on_battery(self, session: Session, report: dict) -> None: ...
    def on_disconnected(self, session: Session) -> None: ...


class StreamServer:
    def __init__(
        self,
        identity: Identity,
        listener: Listener,
        config: Callable[[int], StreamConfig],
        port: int = DEFAULT_PORT,
        host: str = "0.0.0.0",
    ) -> None:
        self._identity = identity
        self._listener = listener
        self._config = config
        self._wanted_port = port
        self._host = host
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self._ctx.load_cert_chain(identity.cert_path, identity.key_path)
        self._sock: socket.socket | None = None
        self._pending = threading.BoundedSemaphore(MAX_PENDING)
        self._lock = threading.Lock()
        self._sessions: dict[int, Session] = {}
        self._stopping = threading.Event()
        self.port = port

    def start(self) -> None:
        self._sock = self._bind()
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._accept_loop, name="celcam-accept", daemon=True).start()
        log.info("Esperando al celular en el puerto %d", self.port)

    def _bind(self) -> socket.socket:
        last_error: OSError | None = None
        for offset in range(PORT_ATTEMPTS):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((self._host, self._wanted_port + offset if self._wanted_port else 0))
                sock.listen(8)
                return sock
            except OSError as exc:
                sock.close()
                last_error = exc
        raise OSError(f"No hay puerto libre desde {self._wanted_port}") from last_error

    def stop(self) -> None:
        self._stopping.set()
        if self._sock:
            self._sock.close()
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.interrupt()

    def current(self, slot: int = 0) -> Session | None:
        with self._lock:
            return self._sessions.get(slot)

    def _accept_loop(self) -> None:
        assert self._sock
        while not self._stopping.is_set():
            try:
                conn, peer = self._sock.accept()
            except OSError:
                return
            if not self._pending.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn, peer), name="celcam-session", daemon=True).start()

    def _serve(self, conn: socket.socket, peer: tuple) -> None:
        session = None
        try:
            session = self._authenticate(conn, peer)
        except (OSError, ssl.SSLError, ProtocolError, ConnectionClosed) as exc:
            log.info("Conexión rechazada de %s: %s", peer[0], exc)
            conn.close()
            return
        finally:
            self._pending.release()
        if session is None:
            conn.close()
            return
        self._run(session, peer)

    def _authenticate(self, conn: socket.socket, peer: tuple) -> Session | None:
        conn.settimeout(HANDSHAKE_TIMEOUT)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        tls = self._ctx.wrap_socket(conn, server_side=True)
        # Un solo lector para toda la sesión: guarda lo que ya leyó de más.
        reader = tls.makefile("rb", buffering=256 * 1024)
        hello = read_hello(reader)
        token = hello.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self._identity.token.encode()):
            tls.sendall(pack_reply({"ok": False, "error": "codigo"}))
            log.warning("Código incorrecto desde %s", peer[0])
            tls.close()
            return None
        if hello.get("proto") != PROTO:
            tls.sendall(pack_reply({"ok": False, "error": "version"}))
            tls.close()
            return None
        device = str(hello.get("device", "Celular"))[:60]
        # Una app vieja no manda su identificador: se la reconoce por el modelo.
        phone_id = str(hello.get("id") or device)[:80]
        slot = self._listener.place(phone_id, device)
        if slot is None:
            tls.sendall(pack_reply({"ok": False, "error": "lleno"}))
            log.info("Sin lugar para %s", device)
            tls.close()
            return None
        config = self._config(slot)
        tls.sendall(
            pack_reply(
                {
                    "ok": True,
                    "w": config.width,
                    "h": config.height,
                    "fps": config.fps,
                    "kbps": config.kbps,
                    "pc": self._identity.pc_name,
                    "version": __version__,
                    "camera": slot + 1,
                }
            )
        )
        tls.settimeout(READ_TIMEOUT)
        return Session(tls, reader, device, slot)

    def _run(self, session: Session, peer: tuple) -> None:
        with self._lock:
            previous = self._sessions.get(session.slot)
            self._sessions[session.slot] = session
        if previous:
            previous.retire()
        log.info("Conectado: %s desde %s", session.device, peer[0])
        self._listener.on_connected(session)
        try:
            while not self._stopping.is_set():
                session.flush()
                if session.retiring:
                    return
                mtype, payload = read_message(session.reader)
                if mtype == TYPE_VIDEO:
                    self._listener.on_video(session, parse_video(payload))
                elif mtype == TYPE_CONTROL:
                    command = parse_control(payload)
                    if command.get("cmd") == "ping":
                        session.send_control({"cmd": "pong"})
                    elif command.get("cmd") == "capture":
                        self._listener.on_capture(session, command)
                    elif command.get("cmd") == "battery":
                        self._listener.on_battery(session, command)
        except (OSError, ValueError, ssl.SSLError, ProtocolError, ConnectionClosed) as exc:
            log.info("Sesión terminada (%s): %r", session.device, exc)
            session.closed_by_phone = isinstance(exc, ConnectionClosed)
        finally:
            with self._lock:
                if self._sessions.get(session.slot) is session:
                    del self._sessions[session.slot]
            session.close()
            self._listener.on_disconnected(session)
