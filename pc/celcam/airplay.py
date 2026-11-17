"""La pantalla del iPhone por AirPlay («Duplicar pantalla»), sin instalar nada en el iPhone.

CelCam se anuncia en la red como un receptor AirPlay. El iPhone se empareja (pair-setup y pair-verify con ed25519
y x25519), pasa la llave de la sesión cifrada con FairPlay (se abre con playfair, de UxPlay, compilado aparte:
`celcam_playfair.dll`), y manda la pantalla en H.264 cifrado con AES-CTR y el sonido en AAC-ELD cifrado con AES-CBC.
Sigue lo que hace UxPlay (https://github.com/FDH2/UxPlay, GPL-3.0).

Para el resto del programa, cada iPhone conectado es un celular más (ver server.Session): va a su cámara, se ve en
la ventana y, aparte, su sonido sale por los parlantes de la PC (AirPlay deja de sonar en el teléfono).
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import logging
import plistlib
import socket
import struct
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import av
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .server import Listener, StreamServer

log = logging.getLogger(__name__)

PORT = 7000
PORT_ATTEMPTS = 10
MODEL = "AppleTV3,2"
VERSION = "220.68"
# Bit 42 (el 10 de la segunda mitad), «Screen Multi Codec»: el iPhone puede usar H.265, que hace falta para pasar
# de 1080p. Con 2560×1440 ofrecidos, un iPhone 13 Pro manda 2560×1184 a 60 cuadros/s (probado el 17/11).
FEATURES_1, FEATURES_2 = 0x5A7FFEE6, 0x400
DISPLAY_SIZE = (2560, 1440)
PI = "2e388006-13ba-4041-9a67-25dd4a43d536"
DISPLAY_UUID = "e0ff8a27-6738-3d56-8a16-cc53aacee925"
# iOS duplica la pantalla como mucho a 60 cuadros por segundo, aunque se le ofrezcan más.
MAX_FPS = 60
NAL_START = b"\x00\x00\x00\x01"
RAW = serialization.Encoding.Raw, serialization.PublicFormat.Raw
STOP_WAIT = 5.0

# Respuestas fijas de FairPlay v3, tal como están en UxPlay (lib/fairplay_playfair.c).
FP_REPLIES = [bytes.fromhex(h) for h in (
    "46504c59030102000000008202000f9f3f9e0a2521dbdf312ab2bfb29e8d232b6376a8c818701d22ae93d82737feaf9db4fdf41c2dba9d1f49caaabf6591ac1f7bc6f7e0663d21afe01565953eab81f418ceed095adb7c3d0e254909a79831d49c3982973434facb42c63a1cd911a6fe941a8a6d4a743b46c3a7649e44c78955e49d8155009549c4e2f7a3f6d5ba",
    "46504c5903010200000000820201cf32a25714b2524f8aa0ad7af164e37bcf4424e200047efc0ad67afcd95ded1c2730bb591b962ed63a9c4ded88ba8fc78de64d91ccfd5c7b56da88e31f5cceafc7431995a01665a54e1939d25b94db64b9e45d8d063e1e6af07e9656162b0efa404275ea5a44d9591c7256b9fbe6513898b80227721988571650942ad946688a",
    "46504c5903010200000000820202c169a352eeed35b18cdd9c58d64f16c1519a89eb5317bd0d4336cd68f638ff9d016a5b52b7fa9216b2b65482c78444118121a2c7fed83db7119e9182aad7d18c7063e2a457555910af9e0efc76347d164043807f581ee4fbe42ca9dedc1b5eb2a3aa3d2ecd59e7eee70b3629f22afd161d877353ddb99adc8e07006e56f850ce",
    "46504c59030102000000008202039001e1727e0f57f9f5880db104a6257a23f5cfff1abbe1e93045251afb97eb9fc0011ebe0f3a81df5b691d76acb2f7a5c708e3d328f56bb39dbde5f29c8a17f481487e3ae863c678325422e6f78e166d18aa7fd636258bce28726f661f738893ce44311e4be6c0535193e5ef72e8686233729c227d820c999445d89246c8c359",
)]
FP_HEADER = bytes.fromhex("46504c590301040000000014")

# Audio: AAC-ELD (ct=8) o ALAC (ct=2), 44100 Hz estéreo, como en UxPlay (renderers/audio_renderer.c).
ELD_CONFIG = bytes.fromhex("f8e85000")
ALAC_COOKIE = bytes.fromhex("00000024616c616300000000000001600010280a0e0200ff000000000000000000000000ac44")
RATE, CHANNELS = 44100, 2


def _hash16(*parts: bytes) -> bytes:
    return hashlib.sha512(b"".join(parts)).digest()[:16]


def _hw_address() -> bytes:
    return uuid.getnode().to_bytes(6, "big")


def _load_key(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), None)
            if isinstance(key, Ed25519PrivateKey):
                return key
            log.warning("La llave de AirPlay no es del tipo esperado: se crea otra")
        except ValueError:
            log.warning("Llave de AirPlay dañada: se crea otra")
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return key


def _txt_bytes(record: dict[str, str]) -> bytes:
    out = b""
    for key, value in record.items():
        item = f"{key}={value}".encode()
        out += bytes([len(item)]) + item
    return out


class Playfair:
    """La parte de FairPlay que abre la llave de la sesión (playfair de UxPlay, en una biblioteca aparte)."""

    def __init__(self, dll: Path) -> None:
        self._dll = ctypes.CDLL(str(dll))
        self._dll.celcam_playfair_decrypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        self._dll.celcam_playfair_decrypt.restype = None

    def decrypt(self, key_message: bytes, encrypted_key: bytes) -> bytes:
        if len(key_message) != 164 or len(encrypted_key) != 72:
            raise ValueError("FairPlay: datos de largo inesperado")
        out = ctypes.create_string_buffer(16)
        self._dll.celcam_playfair_decrypt(key_message, encrypted_key, out)
        return out.raw


# Sonido por los parlantes de la PC (waveOut de Windows, sin bibliotecas extra)

_WHDR_DONE = 0x1


class _WaveFormat(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


class _WaveHeader(ctypes.Structure):
    pass


_WaveHeader._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD), ("dwBytesRecorded", wintypes.DWORD),
                        ("dwUser", ctypes.c_size_t), ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                        ("lpNext", ctypes.POINTER(_WaveHeader)), ("reserved", ctypes.c_size_t)]


class Speaker:
    """Reproduce PCM de 16 bits con una reserva chica.

    Cada pedazo del iPhone dura ~11 ms: se juntan en bloques de 20 ms. Al empezar, o si se quedó sin sonido, espera a
    tener 80 ms (si no, cualquier demora del Wi-Fi lo corta). Con más de 300 ms esperando, descarta para no atrasarse.
    """

    BLOCK_BYTES = RATE * CHANNELS * 2 // 50  # 20 ms
    PREBUFFER_BLOCKS = 4  # 80 ms
    MAX_BLOCKS = 15  # 300 ms

    def __init__(self) -> None:
        self._winmm = ctypes.WinDLL("winmm")
        self._handle = wintypes.HANDLE()
        fmt = _WaveFormat(1, CHANNELS, RATE, RATE * CHANNELS * 2, CHANNELS * 2, 16, 0)
        result = self._winmm.waveOutOpen(ctypes.byref(self._handle), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0)
        if result:
            raise OSError(f"No se pudo abrir el sonido de la PC ({result})")
        self._pending: list[tuple[_WaveHeader, ctypes.Array]] = []
        self._partial = bytearray()
        self._waiting: list[bytes] = []
        self._lock = threading.Lock()
        self.dropped = 0
        self.underruns = 0

    def _reap(self) -> None:
        keep = []
        for header, data in self._pending:
            if header.dwFlags & _WHDR_DONE:
                self._winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(header), ctypes.sizeof(_WaveHeader))
            else:
                keep.append((header, data))
        self._pending = keep

    def _write(self, block: bytes) -> None:
        data = ctypes.create_string_buffer(block, len(block))
        header = _WaveHeader(ctypes.cast(data, ctypes.c_void_p), len(block), 0, 0, 0, 0, None, 0)
        self._winmm.waveOutPrepareHeader(self._handle, ctypes.byref(header), ctypes.sizeof(_WaveHeader))
        self._winmm.waveOutWrite(self._handle, ctypes.byref(header), ctypes.sizeof(_WaveHeader))
        self._pending.append((header, data))

    def play(self, pcm: bytes) -> None:
        with self._lock:
            self._reap()
            self._partial += pcm
            while len(self._partial) >= self.BLOCK_BYTES:
                block = bytes(self._partial[: self.BLOCK_BYTES])
                del self._partial[: self.BLOCK_BYTES]
                if not self._pending:
                    # Sin nada sonando: se junta reserva antes de arrancar.
                    if not self._waiting:
                        self.underruns += 1
                    self._waiting.append(block)
                    if len(self._waiting) >= self.PREBUFFER_BLOCKS:
                        for waiting in self._waiting:
                            self._write(waiting)
                        self._waiting = []
                elif len(self._pending) >= self.MAX_BLOCKS:
                    self.dropped += 1
                else:
                    self._write(block)

    def close(self) -> None:
        with self._lock:
            self._winmm.waveOutReset(self._handle)
            self._reap()
            self._winmm.waveOutClose(self._handle)


class AudioReceiver:
    """Paquetes RTP por UDP: cada uno se descifra desde cero con AES-CBC (lo que no completa un bloque viene sin cifrar)."""

    def __init__(self, key: bytes, iv: bytes, codec_type: int, speaker: Speaker | None) -> None:
        self._key, self._iv = key, iv
        self._decoder = av.CodecContext.create("alac" if codec_type == 2 else "aac", "r")
        self._decoder.extradata = ALAC_COOKIE if codec_type == 2 else ELD_CONFIG
        self._decoder.sample_rate = RATE
        self._decoder.layout = "stereo"
        self._decoder.open()
        self._resampler = av.AudioResampler(format="s16", layout="stereo", rate=RATE)
        self._speaker = speaker
        self._last_seq: int | None = None
        self.played = 0
        self.repeated = 0

    def fresh(self, seq: int) -> bool:
        """Con «audio redundante» el iPhone manda cada paquete tres veces: sonaba con eco. Pasa solo uno más nuevo."""
        if self._last_seq is not None and not 0 < ((seq - self._last_seq) & 0xFFFF) < 0x8000:
            self.repeated += 1
            return False
        self._last_seq = seq
        return True

    def packet(self, data: bytes) -> None:
        if len(data) < 13 or (data[1] & 0x7F) not in (0x60, 0x56) or not self.fresh(int.from_bytes(data[2:4], "big")):
            return
        payload = data[12:]
        whole = len(payload) // 16 * 16
        decryptor = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).decryptor()
        plain = decryptor.update(payload[:whole]) + payload[whole:]
        try:
            for frame in self._decoder.decode(av.Packet(plain)):
                for out in self._resampler.resample(frame):
                    if self._speaker is not None:
                        self._speaker.play(bytes(out.planes[0])[: out.samples * CHANNELS * 2])
                    self.played += 1
        except av.FFmpegError:
            log.debug("AirPlay: un paquete de audio no se pudo decodificar", exc_info=True)


class MirrorStream:
    """La pantalla: cabecera de 128 bytes y el contenido. Las imágenes vienen cifradas con un AES-CTR continuo."""

    def __init__(self, aes_key: bytes, connection_id: int) -> None:
        cid = str(connection_id & 0xFFFFFFFFFFFFFFFF).encode()
        key = _hash16(b"AirPlayStreamKey", cid, aes_key)
        iv = _hash16(b"AirPlayStreamIV", cid, aes_key)
        self._decryptor = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
        self._decoder: av.CodecContext | None = None
        self._sps_pps = b""
        self.codec = ""

    def _use(self, codec: str) -> None:
        if self._decoder is None or self.codec != codec:
            self._decoder = av.CodecContext.create(codec, "r")
            self._decoder.flags |= av.codec.context.Flags.low_delay
            self.codec = codec

    def configure(self, payload: bytes) -> None:
        """El paquete que dice cómo viene el video: SPS y PPS de H.264, o VPS, SPS y PPS de H.265 (como en UxPlay)."""
        if len(payload) >= 8 and payload[4:8] == b"hvc1":
            ptr, parts = 0x75, []
            for mark in (0xA0, 0xA1, 0xA2):
                if len(payload) < ptr + 5 or payload[ptr] != mark:
                    raise ValueError("AirPlay: configuración de H.265 inesperada")
                size = struct.unpack(">H", payload[ptr + 3:ptr + 5])[0]
                parts.append(payload[ptr + 5:ptr + 5 + size])
                ptr += 5 + size
            self._use("hevc")
            self._sps_pps = b"".join(NAL_START + part for part in parts)
        elif len(payload) >= 11:
            sps_size = struct.unpack(">H", payload[6:8])[0]
            sps = payload[8:8 + sps_size]
            pps_size = struct.unpack(">H", payload[sps_size + 9:sps_size + 11])[0]
            pps = payload[sps_size + 11:sps_size + 11 + pps_size]
            self._use("h264")
            self._sps_pps = NAL_START + sps + NAL_START + pps

    def handle(self, header: bytes, payload: bytes) -> tuple[list[av.VideoFrame], int]:
        kind = header[4]
        if kind == 0x01:
            self.configure(payload)
            return [], 0
        if kind != 0x00 or self._decoder is None:
            return [], 0
        data = bytearray(self._decryptor.update(payload))
        offset = 0
        while offset + 4 <= len(data):
            size = struct.unpack(">I", data[offset:offset + 4])[0]
            if size <= 0 or offset + 4 + size > len(data):
                raise ValueError("AirPlay: la imagen no se pudo descifrar")
            data[offset:offset + 4] = NAL_START
            offset += 4 + size
        packet = self._sps_pps + bytes(data)
        self._sps_pps = b""
        return self._decoder.decode(av.Packet(packet)), len(payload)


class Pairing:
    """Emparejamiento sin PIN, como un Apple TV: firma ed25519 sobre las llaves x25519 de los dos."""

    def __init__(self, key: Ed25519PrivateKey) -> None:
        self._key = key
        self._ours_pub = b""
        self._theirs_pub = b""
        self._their_ed: Ed25519PublicKey | None = None
        self.secret = b""
        self.verified = False

    def _ctr(self):
        return Cipher(algorithms.AES(_hash16(b"Pair-Verify-AES-Key", self.secret)), modes.CTR(_hash16(b"Pair-Verify-AES-IV", self.secret))).encryptor()

    def verify(self, data: bytes) -> bytes | None:
        if len(data) == 68 and data[0] == 1:
            self._theirs_pub = data[4:36]
            self._their_ed = Ed25519PublicKey.from_public_bytes(data[36:68])
            ours = X25519PrivateKey.generate()
            self._ours_pub = ours.public_key().public_bytes(*RAW)
            self.secret = ours.exchange(X25519PublicKey.from_public_bytes(self._theirs_pub))
            return self._ours_pub + self._ctr().update(self._key.sign(self._ours_pub + self._theirs_pub))
        if len(data) == 68 and data[0] == 0 and self._their_ed is not None:
            ctr = self._ctr()
            ctr.update(bytes(64))  # esa parte ya se usó para nuestra firma
            try:
                self._their_ed.verify(ctr.update(data[4:68]), self._theirs_pub + self._ours_pub)
            except InvalidSignature:
                return None
            self.verified = True
            return b""
        return None


class AirPlaySession:
    """Un iPhone duplicando su pantalla. Tiene lo mismo que server.Session para el resto del programa."""

    def __init__(self, server: "AirPlayServer", address: str) -> None:
        self.device = "iPhone"
        self.slot = 0
        self.closed_by_phone = False
        self.retiring = False
        self.mirroring = True
        # La última imagen del iPhone, para verla en pantalla completa (la lee la ventana).
        self.latest_frame: av.VideoFrame | None = None
        self.address = address
        self._server = server
        self._pairing = Pairing(server.key)
        self._key_message = b""
        self._aes_key = b""
        self._eiv = b""
        self._connection_id = 0
        self._attached = False
        self._closed = False
        self._writer: asyncio.StreamWriter | None = None
        self._resources: list = []
        self._audio: AudioReceiver | None = None
        self.mirror_port = 0
        self.timing_port = 0
        self.audio_ports = (0, 0)

    # Lo mismo que server.Session (desde cualquier hilo)

    def send_control(self, command: dict) -> None:
        if command.get("cmd") in ("stop", "bye"):
            self.closed_by_phone = command.get("cmd") == "stop"
            self.interrupt()

    def send_latest(self, key: str, command: dict) -> None:
        pass

    def retire(self) -> None:
        self.retiring = True
        self.interrupt()

    def interrupt(self) -> None:
        self._server.call(self._abort)

    def _abort(self) -> None:
        if self._writer is not None:
            self._writer.close()

    # Red

    async def open_ports(self) -> None:
        loop = asyncio.get_running_loop()
        mirror = await asyncio.start_server(self._mirror_client, "0.0.0.0", 0)
        self.mirror_port = mirror.sockets[0].getsockname()[1]
        self._resources.append(mirror)
        session = self

        class AudioData(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, address) -> None:
                audio = session._audio
                if audio is not None:
                    try:
                        audio.packet(data)
                    except Exception:
                        log.debug("AirPlay: paquete de audio", exc_info=True)

        ports = []
        for factory in (asyncio.DatagramProtocol, AudioData, asyncio.DatagramProtocol):
            transport, _ = await loop.create_datagram_endpoint(factory, local_addr=("0.0.0.0", 0))
            self._resources.append(transport)
            ports.append(transport.get_extra_info("sockname")[1])
        self.timing_port, self.audio_ports = ports[0], (ports[1], ports[2])

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode("utf-8", "replace").split("\r\n")
                method, url, proto = (lines[0].split(" ") + ["", "", ""])[:3]
                headers: dict[str, str] = {}
                for line in lines[1:]:
                    if ":" in line:
                        name, value = line.split(":", 1)
                        headers[name.strip().lower()] = value.strip()
                length = int(headers.get("content-length", "0") or 0)
                if length > 1_000_000:
                    raise ValueError("pedido demasiado grande")
                body = await reader.readexactly(length)
                status, reply, ctype, extra = self._answer(method, url, headers, body)
                out = [f"{proto or 'RTSP/1.0'} {status}"]
                if "cseq" in headers:
                    out.append(f"CSeq: {headers['cseq']}")
                out.append("Server: AirTunes/" + VERSION)
                out += extra
                if ctype:
                    out.append(f"Content-Type: {ctype}")
                out.append(f"Content-Length: {len(reply)}")
                writer.write(("\r\n".join(out) + "\r\n\r\n").encode() + reply)
                await writer.drain()
                if method == "TEARDOWN" and not body_has_streams(body):
                    self.closed_by_phone = True
                    break
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception:
            log.exception("AirPlay: falló la conexión con el iPhone")
        finally:
            await self.close()

    def _answer(self, method: str, url: str, headers: dict, body: bytes) -> tuple[str, bytes, str | None, list[str]]:
        server = self._server
        if method == "GET" and url.startswith("/info"):
            qualifier = None
            if body and "plist" in headers.get("content-type", ""):
                try:
                    found = plistlib.loads(body).get("qualifier")
                    qualifier = found[0] if isinstance(found, list) and found else None
                except Exception:
                    pass
            return "200 OK", server.info(qualifier, "cseq" in headers), "application/x-apple-binary-plist", []
        if method == "POST" and url == "/pair-setup":
            return "200 OK", server.public_key, "application/octet-stream", []
        if method == "POST" and url == "/pair-verify":
            reply = self._pairing.verify(body)
            if reply is None:
                log.warning("AirPlay: el iPhone de %s no pasó el emparejamiento", self.address)
                return "470 Connection Authorization Required", b"", None, []
            return "200 OK", reply, "application/octet-stream", []
        if method == "POST" and url == "/fp-setup":
            reply = self._fp_setup(body)
            if reply is None:
                return "501 Not Implemented", b"", None, []
            return "200 OK", reply, "application/octet-stream", []
        if method == "SETUP":
            return "200 OK", self._setup(body), "application/x-apple-binary-plist", []
        if method == "RECORD":
            return "200 OK", b"", None, ["Audio-Latency: 11025", "Audio-Jack-Status: connected; type=analog"]
        if method == "GET_PARAMETER" and b"volume" in body:
            return "200 OK", b"volume: 0.000000\r\n", "text/parameters", []
        if method == "OPTIONS":
            return "200 OK", b"", None, ["Public: SETUP, RECORD, FLUSH, TEARDOWN, OPTIONS, GET_PARAMETER, SET_PARAMETER"]
        if method == "TEARDOWN" and body_has_streams(body):
            # Solo termina el sonido (cuando el iPhone deja de reproducir algo): la pantalla sigue.
            self._audio = None
        return "200 OK", b"", None, []

    def _fp_setup(self, data: bytes) -> bytes | None:
        if len(data) == 16 and data[4] == 0x03 and data[14] < 4:
            return FP_REPLIES[data[14]]
        if len(data) == 164 and data[4] == 0x03:
            self._key_message = data
            return FP_HEADER + data[144:164]
        log.warning("AirPlay: FairPlay en una versión que todavía no se admite (%s)", data[4] if len(data) > 4 else "?")
        return None

    def _setup(self, body: bytes) -> bytes:
        try:
            request = plistlib.loads(body)
        except Exception:
            request = {}
        response: dict = {}
        if "ekey" in request and "eiv" in request:
            raw = self._server.playfair.decrypt(self._key_message, bytes(request["ekey"]))
            secret = self._pairing.secret if self._pairing.verified else b""
            self._aes_key = _hash16(raw, secret) if secret else raw
            self._eiv = bytes(request["eiv"])
            name = str(request.get("name") or "iPhone")[:40]
            self.device = f"{name} (pantalla)"
            self._phone_id = "airplay:" + str(request.get("deviceID") or request.get("macAddress") or self.address)
            response.update({"timingPort": self.timing_port, "eventPort": 0})
        streams = []
        for stream in request.get("streams", []):
            if stream.get("type") == 110:
                self._connection_id = int(stream.get("streamConnectionID", 0))
                streams.append({"dataPort": self.mirror_port, "type": 110})
                self._attach()
            elif stream.get("type") == 96:
                streams.append({"dataPort": self.audio_ports[0], "controlPort": self.audio_ports[1], "type": 96})
                try:
                    self._audio = AudioReceiver(self._aes_key, self._eiv, int(stream.get("ct", 8)), self._server.speaker())
                except Exception:
                    log.exception("AirPlay: no se pudo preparar el sonido")
        if streams:
            response["streams"] = streams
        return plistlib.dumps(response, fmt=plistlib.FMT_BINARY)

    def _attach(self) -> None:
        if self._attached:
            return
        listener = self._server.listener
        slot = listener.place(getattr(self, "_phone_id", "airplay:" + self.address), self.device)
        if slot is None:
            log.info("AirPlay: sin lugar para %s", self.device)
            self.interrupt()
            return
        self.slot = slot
        self._attached = True
        self._server.streams.attach(self)
        log.info("Conectado: %s desde %s (AirPlay)", self.device, self.address)
        listener.on_connected(self)
        # Lo que se maneja desde la PC no aplica a una pantalla: así la ventana no pide actualizar ninguna app.
        listener.on_capture(self, {"cmd": "capture", "zoom_min": 1.0, "zoom_max": 1.0, "zoom": 1.0, "torch_available": False,
                                   "torch": False, "can_switch": False, "dim": False, "fps": MAX_FPS, "rates": "AirPlay"})

    async def _mirror_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._resources.append(writer)
        stream = MirrorStream(self._aes_key, self._connection_id)
        listener = self._server.listener
        try:
            while True:
                header = await reader.readexactly(128)
                size = struct.unpack("<I", header[0:4])[0]
                if size > 16 * 1024 * 1024:
                    raise ValueError("cuadro demasiado grande")
                payload = await reader.readexactly(size) if size else b""
                frames, received = await asyncio.to_thread(stream.handle, header, payload)
                for frame in frames:
                    self.latest_frame = frame
                    if self._attached:
                        await asyncio.to_thread(listener.on_decoded, self, frame, received)
                        received = 0
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception:
            log.exception("AirPlay: falló la imagen")
        finally:
            writer.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._audio = None
        for resource in self._resources:
            resource.close()
        if self._writer is not None:
            self._writer.close()
        if self._attached:
            self._server.streams.detach(self)
            log.info("Sesión terminada (%s)", self.device)
            self._server.listener.on_disconnected(self)


def body_has_streams(body: bytes) -> bool:
    if not body:
        return False
    try:
        return bool(plistlib.loads(body).get("streams"))
    except Exception:
        return False


class AirPlayServer:
    """Se anuncia en la red como receptor AirPlay y atiende a los iPhone que duplican su pantalla."""

    def __init__(self, listener: Listener, streams: StreamServer, key_path: Path, playfair_dll: Path, name: str, port: int = PORT) -> None:
        self.listener = listener
        self.streams = streams
        self.name = name
        self.port = port
        self.key = _load_key(key_path)
        self.public_key = self.key.public_key().public_bytes(*RAW)
        self.playfair = Playfair(playfair_dll)
        self.loop = asyncio.new_event_loop()
        self._mac = _hw_address()
        self._device_id = ":".join(f"{b:02X}" for b in self._mac)
        self._speaker: Speaker | None = None
        self._speaker_failed = False
        self._thread: threading.Thread | None = None
        self._zeroconf = None
        self._services: list = []
        self._sessions: set[AirPlaySession] = set()
        self._listening: asyncio.Server | None = None

    # Datos que se anuncian

    def _features(self) -> str:
        return f"0x{FEATURES_1:X},0x{FEATURES_2:X}"

    def airplay_txt(self) -> dict[str, str]:
        return {"deviceid": self._device_id, "features": self._features(), "pw": "false", "flags": "0x4", "model": MODEL,
                "pk": self.public_key.hex(), "pi": PI, "srcvers": VERSION, "vv": "2"}

    def raop_txt(self) -> dict[str, str]:
        return {"ch": "2", "cn": "0,1,2,3", "da": "true", "et": "0,3,5", "vv": "2", "ft": self._features(), "am": MODEL,
                "md": "0,1,2", "rhd": "5.6.0.0", "pw": "false", "sr": "44100", "ss": "16", "sv": "false", "tp": "UDP",
                "txtvers": "1", "sf": "0x4", "vs": VERSION, "vn": "65537", "pk": self.public_key.hex()}

    def info(self, qualifier: str | None, full: bool) -> bytes:
        if qualifier == "txtAirPlay":
            return plistlib.dumps({"txtAirPlay": _txt_bytes(self.airplay_txt())}, fmt=plistlib.FMT_BINARY)
        if qualifier == "txtRAOP":
            return plistlib.dumps({"txtRAOP": _txt_bytes(self.raop_txt())}, fmt=plistlib.FMT_BINARY)
        info: dict = {
            "deviceID": self._device_id, "macAddress": self._device_id, "pk": self.public_key,
            "features": (FEATURES_2 << 32) | FEATURES_1, "name": self.name, "pi": PI, "vv": 2, "statusFlags": 68,
            "keepAliveLowPower": True, "sourceVersion": VERSION, "keepAliveSendStatsAsBody": True, "model": MODEL,
        }
        if full:
            info.update({
                "initialVolume": 0.0,
                "audioLatencies": [
                    {"type": 100, "inputLatencyMicros": 0, "audioType": "default", "outputLatencyMicros": False},
                    {"type": 101, "audioType": "default", "inputLatencyMicros": 0, "outputLatencyMicros": False},
                ],
                "audioFormats": [
                    {"audioOutputFormats": 0x3FFFFFC, "type": 100, "audioInputFormats": 0x3FFFFFC},
                    {"audioOutputFormats": 0x3FFFFFC, "type": 101, "audioInputFormats": 0x3FFFFFC},
                ],
                "displays": [{
                    "uuid": DISPLAY_UUID, "widthPhysical": 0, "heightPhysical": 0, "width": DISPLAY_SIZE[0],
                    "height": DISPLAY_SIZE[1], "widthPixels": DISPLAY_SIZE[0], "heightPixels": DISPLAY_SIZE[1],
                    "rotation": False, "refreshRate": 1 / MAX_FPS,
                    "maxFPS": MAX_FPS, "overscanned": False, "features": 14,
                }],
            })
        return plistlib.dumps(info, fmt=plistlib.FMT_BINARY)

    def speaker(self) -> Speaker | None:
        if self._speaker is None and not self._speaker_failed:
            try:
                self._speaker = Speaker()
            except OSError:
                self._speaker_failed = True
                log.warning("AirPlay: no se pudo abrir el sonido de la PC", exc_info=True)
        return self._speaker

    # Arranque y apagado

    def start(self, addresses: list[str], announce: bool = True) -> None:
        from zeroconf import IPVersion, ServiceInfo, Zeroconf

        sock = self._bind()
        self.port = sock.getsockname()[1]
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            self._listening = self.loop.run_until_complete(asyncio.start_server(self._client, sock=sock))
            ready.set()
            self.loop.run_forever()

        self._thread = threading.Thread(target=run, name="celcam-airplay", daemon=True)
        self._thread.start()
        ready.wait(5)
        if not announce:
            return
        packed = [socket.inet_aton(ip) for ip in addresses]
        host = f"CelCam-{self._mac.hex()}.local."
        self._services = [
            ServiceInfo("_airplay._tcp.local.", f"{self.name}._airplay._tcp.local.", addresses=packed, port=self.port,
                        properties=self.airplay_txt(), server=host),
            ServiceInfo("_raop._tcp.local.", f"{self._mac.hex().upper()}@{self.name}._raop._tcp.local.", addresses=packed,
                        port=self.port, properties=self.raop_txt(), server=host),
        ]
        self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        for service in self._services:
            # Si ya hay otro receptor con el mismo nombre en la red, zeroconf le agrega un número.
            self._zeroconf.register_service(service, allow_name_change=True)
        log.info("AirPlay: «%s» en el puerto %d", self.name, self.port)

    def _bind(self) -> socket.socket:
        last: OSError | None = None
        for offset in range(PORT_ATTEMPTS):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("0.0.0.0", self.port + offset if self.port else 0))
                sock.listen(8)
                sock.setblocking(False)
                return sock
            except OSError as exc:
                sock.close()
                last = exc
        raise OSError(f"No hay puerto libre para AirPlay desde {self.port}") from last

    def stop(self) -> None:
        if self._zeroconf is not None:
            try:
                for service in self._services:
                    self._zeroconf.unregister_service(service)
                self._zeroconf.close()
            except Exception:
                log.debug("AirPlay: al dejar de anunciarse", exc_info=True)
            self._zeroconf = None
        if self._thread is None:
            return

        async def close_all() -> None:
            if self._listening is not None:
                self._listening.close()
            await asyncio.gather(*(session.close() for session in list(self._sessions)), return_exceptions=True)
            pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(close_all(), self.loop).result(STOP_WAIT)
        except Exception:
            log.warning("AirPlay: no se pudieron cerrar a tiempo las conexiones", exc_info=True)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(STOP_WAIT)
        self._thread = None
        if self._speaker is not None:
            self._speaker.close()
            self._speaker = None

    def call(self, function, *args) -> None:
        try:
            self.loop.call_soon_threadsafe(function, *args)
        except RuntimeError:
            pass

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        session = AirPlaySession(self, peer[0] if peer else "?")
        self._sessions.add(session)
        try:
            await session.open_ports()
            await session.serve(reader, writer)
        finally:
            self._sessions.discard(session)
