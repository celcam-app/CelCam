"""iPhone (o cualquier navegador): la cámara llega por WebRTC desde una página, sin app, servidor ni certificados.

La página se aloja gratis en GitHub Pages porque Safari solo da la cámara a páginas con HTTPS de verdad. Lo que necesita
para conectarse viene en el QR, después del «#», así que nunca llega a GitHub. Con eso arma sola la oferta de WebRTC, y
la PC responde como ICE-lite: no necesita que el navegador le mande nada antes.

Seguridad: la huella del QR le prueba al navegador que habla con esta PC. La clave de ICE sale del código de la PC, y ese
código se vuelve a pedir por el canal de datos ya cifrado: sin él no se acepta video ni se ocupa una cámara.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import socket
import threading
import time
from typing import Callable

from aioice import stun
from aiortc import RTCDataChannel, RTCDataChannelParameters, RTCRtpReceiver, RTCSctpCapabilities, RTCSctpTransport
from aiortc.mediastreams import MediaStreamError
from aiortc.rtcdtlstransport import RTCCertificate, RTCDtlsFingerprint, RTCDtlsParameters, RTCDtlsTransport
from aiortc.rtcrtpparameters import RTCRtcpFeedback, RTCRtpCodecParameters, RTCRtpReceiveParameters
from aiortc.rtcrtpreceiver import RemoteStreamTrack
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from . import __version__
from .identity import Identity
from .protocol import DEFAULT_PORT
from .server import Listener, StreamConfig, StreamServer

log = logging.getLogger(__name__)

PAGE_URL = "https://celcam-app.github.io/CelCam/iphone/"
PORT_ATTEMPTS = 10
RECEIVE_BUFFER = 4 * 1024 * 1024
# El navegador pregunta cada pocos segundos si la PC sigue ahí. Sin eso, se fue.
PEER_TIMEOUT = 20.0
HELLO_TIMEOUT = 10.0
RETIRE_WAIT = 1.0
MAX_SESSIONS = 8
MAX_MESSAGE = 16 * 1024
STOP_WAIT = 5.0


def ice_credentials(token: str) -> tuple[str, str]:
    """Salen del código, y la página hace la misma cuenta: el QR queda más corto y fácil de escanear."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    return digest[:8], digest[8:40]


def web_link(identity: Identity, hosts: list[str], port: int, page: str | None = None) -> str:
    page = page or os.environ.get("CELCAM_PAGINA") or PAGE_URL
    fingerprint = base64.urlsafe_b64encode(bytes.fromhex(identity.fingerprint)).rstrip(b"=").decode()
    # La versión evita que Safari use una copia vieja de la página.
    return f"{page}?v={__version__}#h={','.join(hosts)}&p={port}&t={identity.token}&f={fingerprint}"


class AnyPeerDtls(RTCDtlsTransport):
    """No hay huella del navegador para comparar: lo autoriza el código que manda por este canal."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.received_bytes = 0

    def _validate_peer_identity(self, remoteParameters) -> None:
        pass

    async def _handle_rtp_data(self, data: bytes, arrival_time_ms: int) -> None:
        self.received_bytes += len(data)
        await super()._handle_rtp_data(data, arrival_time_ms)

    def take_received_bytes(self) -> int:
        size, self.received_bytes = self.received_bytes, 0
        return size


class LiteIce:
    """Lo que RTCDtlsTransport necesita de ICE, una vez que el navegador ya eligió el camino."""

    role = "controlled"
    state = "completed"

    def __init__(self, send: Callable[[bytes], None]) -> None:
        self._send_now = send
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.closed = False

    def feed(self, data: bytes | None) -> None:
        self._queue.put_nowait(data)

    async def _recv(self) -> bytes:
        data = await self._queue.get()
        if data is None:
            raise ConnectionError
        return data

    async def _send(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionError
        self._send_now(data)


class WebSession:
    """Un navegador conectado. Para el resto del programa es un celular más (ver server.Session)."""

    def __init__(self, server: "WebServer", remote_ufrag: str, address: tuple) -> None:
        self.device = "iPhone"
        self.slot = 0
        self.closed_by_phone = False
        self.retiring = False
        self.remote_ufrag = remote_ufrag
        self.address = address
        self.last_seen = time.monotonic()
        self._server = server
        self._created = time.monotonic()
        self._authorized = False
        self._closing = False
        self._ice = LiteIce(lambda data: server.send_to(data, self.address))
        self._dtls = AnyPeerDtls(self._ice, [server.certificate])
        self._sctp: RTCSctpTransport | None = None
        self._channel: RTCDataChannel | None = None
        self._receiver: RTCRtpReceiver | None = None
        self._video: asyncio.Task | None = None
        self._latest: dict[str, dict] = {}
        self._latest_lock = threading.Lock()

    # Lo mismo que server.Session: se puede llamar desde cualquier hilo

    def send_control(self, command: dict) -> None:
        self._server.call(self._deliver, command)

    def send_latest(self, key: str, command: dict) -> None:
        with self._latest_lock:
            waiting = bool(self._latest)
            self._latest[key] = command
        if not waiting:
            self._server.call(self._deliver_latest)

    def retire(self) -> None:
        self.retiring = True
        self.send_control({"cmd": "bye"})
        self._server.call(lambda: self._server.loop.call_later(RETIRE_WAIT, self.begin_close))

    def interrupt(self) -> None:
        self._server.call(self.begin_close)

    # Del lado de la red: todo en el hilo del servidor

    def feed(self, data: bytes) -> None:
        self._ice.feed(data)

    def hello_overdue(self, now: float) -> bool:
        return not self._authorized and now - self._created > HELLO_TIMEOUT

    def begin_close(self) -> None:
        asyncio.ensure_future(self.close())

    def _deliver(self, command: dict) -> None:
        channel = self._channel
        if channel is not None and channel.readyState == "open":
            channel.send(json.dumps(command))

    def _deliver_latest(self) -> None:
        with self._latest_lock:
            latest, self._latest = self._latest, {}
        for command in latest.values():
            self._deliver(command)

    async def run(self) -> None:
        self._dtls._set_role("server")
        await self._dtls.start(RTCDtlsParameters(fingerprints=[RTCDtlsFingerprint("sha-256", "00")]))
        if self._dtls.state != "connected":
            log.info("El navegador no completó el cifrado")
            await self.close()
            return
        self._sctp = RTCSctpTransport(self._dtls)
        self._channel = RTCDataChannel(self._sctp, RTCDataChannelParameters(label="c", negotiated=True, id=0))
        self._channel.on("message", self._on_message)
        await self._sctp.start(RTCSctpCapabilities(maxMessageSize=65536), 5000)

    def _on_message(self, raw) -> None:
        if not isinstance(raw, str) or len(raw) > MAX_MESSAGE:
            return
        try:
            message = json.loads(raw)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        try:
            if not self._authorized:
                self._on_hello(message)
            elif message.get("cmd") == "capture":
                self._server.listener.on_capture(self, message)
            elif message.get("cmd") == "bye":
                self.closed_by_phone = True
                self.begin_close()
        except Exception:
            log.exception("Mensaje del navegador")

    def _on_hello(self, hello: dict) -> None:
        token = hello.get("token")
        expected = self._server.identity.token
        if not isinstance(token, str) or not hmac.compare_digest(token.encode(), expected.encode()):
            log.warning("Código incorrecto desde %s", self.address[0])
            self._deliver({"ok": False, "error": "codigo"})
            self._server.loop.call_later(RETIRE_WAIT, self.begin_close)
            return
        self.device = str(hello.get("device") or "iPhone")[:60]
        phone_id = str(hello.get("id") or self.device)[:80]
        slot = self._server.listener.place(phone_id, self.device)
        if slot is None:
            log.info("Sin lugar para %s", self.device)
            self._deliver({"ok": False, "error": "lleno"})
            self._server.loop.call_later(RETIRE_WAIT, self.begin_close)
            return
        self.slot = slot
        self._authorized = True
        config = self._server.config(slot)
        self._deliver(
            {
                "ok": True,
                "w": config.width,
                "h": config.height,
                "fps": config.fps,
                "kbps": config.kbps,
                "pc": self._server.identity.pc_name,
                "camera": slot + 1,
            }
        )
        self._server.streams.attach(self)
        log.info("Conectado: %s desde %s", self.device, self.address[0])
        self._server.listener.on_connected(self)
        self._video = asyncio.ensure_future(self._receive_video())

    async def _receive_video(self) -> None:
        self._receiver = RTCRtpReceiver("video", self._dtls)
        self._receiver._track = RemoteStreamTrack(kind="video")
        # Es lo que hace RTCPeerConnection: sin esto no salen los informes que regulan la calidad.
        self._receiver._set_rtcp_ssrc(int.from_bytes(os.urandom(4), "big"))
        codec = RTCRtpCodecParameters(
            mimeType="video/H264",
            clockRate=90000,
            payloadType=96,
            rtcpFeedback=[RTCRtcpFeedback("nack"), RTCRtcpFeedback("nack", "pli")],
            parameters={"level-asymmetry-allowed": "1", "packetization-mode": "1", "profile-level-id": "42e01f"},
        )
        await self._receiver.receive(RTCRtpReceiveParameters(codecs=[codec], muxId="0"))
        track = self._receiver.track
        try:
            while True:
                frame = await track.recv()
                # Si la PC se atrasó, mejor saltar cuadros que acumular demora.
                if track._queue.qsize() > 1:
                    continue
                await asyncio.to_thread(self._server.listener.on_decoded, self, frame, self._dtls.take_received_bytes())
        except MediaStreamError:
            pass

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._server.forget(self)
        self._ice.closed = True
        if self._video is not None:
            self._video.cancel()
        self._ice.feed(None)
        for part in (self._receiver, self._sctp, self._dtls):
            if part is not None:
                try:
                    await asyncio.wait_for(part.stop(), 3)
                except Exception:
                    log.debug("Al cerrar la sesión del navegador", exc_info=True)
        if self._authorized:
            self._server.streams.detach(self)
            log.info("Sesión terminada (%s)", self.device)
            self._server.listener.on_disconnected(self)


class WebServer(asyncio.DatagramProtocol):
    def __init__(
        self,
        identity: Identity,
        listener: Listener,
        config: Callable[[int], StreamConfig],
        streams: StreamServer,
        port: int = DEFAULT_PORT,
        host: str = "0.0.0.0",
    ) -> None:
        self.identity = identity
        self.listener = listener
        self.config = config
        self.streams = streams
        self.port = port
        self.loop = asyncio.new_event_loop()
        key = serialization.load_pem_private_key(identity.key_path.read_bytes(), password=None)
        self.certificate = RTCCertificate(key, x509.load_pem_x509_certificate(identity.cert_path.read_bytes()))
        self._ufrag, self._password = ice_credentials(identity.token)
        self._host = host
        self._transport: asyncio.DatagramTransport | None = None
        self._sessions: dict[str, WebSession] = {}
        self._by_address: dict[tuple, WebSession] = {}
        self._thread: threading.Thread | None = None
        self._watching: asyncio.Task | None = None

    # Arranque y apagado

    def start(self) -> None:
        sock = self._bind()
        self.port = sock.getsockname()[1]
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.loop.create_datagram_endpoint(lambda: self, sock=sock))
            self._watching = self.loop.create_task(self._watch())
            ready.set()
            self.loop.run_forever()

        self._thread = threading.Thread(target=run, name="celcam-iphone", daemon=True)
        self._thread.start()
        ready.wait(5)
        log.info("Esperando al iPhone en el puerto UDP %d", self.port)

    def _bind(self) -> socket.socket:
        last_error: OSError | None = None
        for offset in range(PORT_ATTEMPTS):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECEIVE_BUFFER)
                sock.bind((self._host, self.port + offset if self.port else 0))
                sock.setblocking(False)
                return sock
            except OSError as exc:
                sock.close()
                last_error = exc
        raise OSError(f"No hay puerto UDP libre desde {self.port}") from last_error

    def stop(self) -> None:
        if self._thread is None:
            return

        async def close_all() -> None:
            await asyncio.gather(*(session.close() for session in list(self._sessions.values())), return_exceptions=True)
            if self._watching is not None:
                self._watching.cancel()
                await asyncio.gather(self._watching, return_exceptions=True)

        # Cierra los decodificadores: si no, sus hilos no dejan terminar el programa.
        try:
            asyncio.run_coroutine_threadsafe(close_all(), self.loop).result(STOP_WAIT)
        except Exception:
            log.warning("No se pudieron cerrar a tiempo las sesiones de iPhone", exc_info=True)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(STOP_WAIT)
        self._thread = None

    def call(self, function, *args) -> None:
        try:
            self.loop.call_soon_threadsafe(function, *args)
        except RuntimeError:
            pass

    def link(self, hosts: list[str]) -> str:
        return web_link(self.identity, hosts, self.port)

    # Red

    def connection_made(self, transport) -> None:
        self._transport = transport

    def error_received(self, exc: Exception) -> None:
        # Windows avisa así cuando el otro lado ya cerró: lo resuelve el control de vida.
        log.debug("UDP: %s", exc)

    def send_to(self, data: bytes, address: tuple) -> None:
        if self._transport is not None:
            self._transport.sendto(data, address)

    def datagram_received(self, data: bytes, address: tuple) -> None:
        if not data:
            return
        # STUN empieza con 0 o 1; lo demás (cifrado y video), solo de quien ya mostró la clave.
        if data[0] < 2:
            self._on_stun(data, address)
            return
        session = self._by_address.get(address)
        if session is not None:
            session.feed(data)

    def _on_stun(self, data: bytes, address: tuple) -> None:
        try:
            message = stun.parse_message(data)
        except ValueError:
            return
        username = str(message.attributes.get("USERNAME", ""))
        if (
            message.message_method != stun.Method.BINDING
            or message.message_class != stun.Class.REQUEST
            or not username.startswith(self._ufrag + ":")
            or not message.check_message_integrity(self._password.encode())
        ):
            return
        response = stun.Message(message_method=stun.Method.BINDING, message_class=stun.Class.RESPONSE, transaction_id=message.transaction_id)
        response.attributes["XOR-MAPPED-ADDRESS"] = address
        response.add_message_integrity(self._password.encode())
        self.send_to(bytes(response), address)

        remote = username.split(":", 1)[1]
        session = self._sessions.get(remote)
        if session is None:
            if len(self._sessions) >= MAX_SESSIONS:
                return
            # Acá mismo, no en otra tarea: el navegador suele mandar dos pedidos seguidos.
            session = WebSession(self, remote, address)
            self._sessions[remote] = session
            self._by_address[address] = session
            asyncio.ensure_future(self._run_session(session))
            return
        session.last_seen = time.monotonic()
        self._by_address[address] = session
        if "USE-CANDIDATE" in message.attributes:
            session.address = address

    async def _run_session(self, session: WebSession) -> None:
        try:
            await session.run()
        except Exception:
            log.exception("Falló la sesión del navegador")
            await session.close()

    def forget(self, session: WebSession) -> None:
        self._sessions.pop(session.remote_ufrag, None)
        for address in [a for a, s in self._by_address.items() if s is session]:
            del self._by_address[address]

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            for session in list(self._sessions.values()):
                if now - session.last_seen > PEER_TIMEOUT:
                    log.info("El navegador de %s dejó de responder", session.device)
                    session.begin_close()
                elif session.hello_overdue(now):
                    session.begin_close()
