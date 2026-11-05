"""Identidad de esta PC: certificado TLS propio y código secreto.

El QR lleva el código y la huella del certificado. Así el celular sabe que habla
con esta PC y no con otra de la misma red, y la PC solo acepta celulares que
escanearon su QR. Nada de esto sale de la red local.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class Identity:
    token: str
    fingerprint: str
    cert_path: Path
    key_path: Path
    pc_name: str

    @classmethod
    def load_or_create(cls, directory: Path) -> "Identity":
        cert_path = directory / "certificado.pem"
        key_path = directory / "llave.pem"
        token_path = directory / "codigo.json"
        if not (cert_path.exists() and key_path.exists() and token_path.exists()):
            return cls.regenerate(directory)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        token = json.loads(token_path.read_text(encoding="utf-8"))["token"]
        return cls(token, _fingerprint(cert), cert_path, key_path, _pc_name())

    @classmethod
    def regenerate(cls, directory: Path) -> "Identity":
        """Crea identidad nueva: los celulares ya emparejados dejan de conectarse."""
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CelCam")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365 * 20))
            .sign(key, hashes.SHA256())
        )
        cert_path = directory / "certificado.pem"
        key_path = directory / "llave.pem"
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        token = secrets.token_urlsafe(18)
        (directory / "codigo.json").write_text(json.dumps({"token": token}), encoding="utf-8")
        return cls(token, _fingerprint(cert), cert_path, key_path, _pc_name())

    def link(self, hosts: list[str], port: int) -> str:
        return (
            f"celcam://pair?h={','.join(hosts)}&p={port}&t={self.token}"
            f"&f={self.fingerprint}&n={quote(self.pc_name, safe='')}"
        )


def _fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def _pc_name() -> str:
    return socket.gethostname()[:40] or "PC"
