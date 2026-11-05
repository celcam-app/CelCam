"""Direcciones de esta PC que el celular puede usar para encontrarla."""

from __future__ import annotations

import ipaddress
import socket

import psutil

MAX_HOSTS = 4
# Adaptadores de máquinas virtuales y VPN: se prueban últimos.
_VIRTUAL_HINTS = ("virtualbox", "vmware", "vethernet", "wsl", "hyper-v", "tailscale", "zerotier", "vpn", "loopback")


def _primary_ip() -> str | None:
    """La dirección que Windows usa para salir a la red (no manda ningún paquete)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
        except OSError:
            return None


def is_usable(ip: str) -> bool:
    address = ipaddress.IPv4Address(ip)
    if address.is_loopback or address.is_link_local:
        return False
    return address.is_private or address in ipaddress.IPv4Network("100.64.0.0/10")


def order_hosts(primary: str | None, interfaces: list[tuple[str, str]]) -> list[str]:
    """Ordena las direcciones: primero la principal, después las reales, al final las virtuales."""
    real: list[str] = []
    virtual: list[str] = []
    for name, ip in interfaces:
        if not is_usable(ip):
            continue
        bucket = virtual if any(hint in name.lower() for hint in _VIRTUAL_HINTS) else real
        bucket.append(ip)
    ordered: list[str] = []
    if primary and is_usable(primary):
        ordered.append(primary)
    for ip in real + virtual:
        if ip not in ordered:
            ordered.append(ip)
    return ordered[:MAX_HOSTS]


def local_hosts() -> list[str]:
    stats = psutil.net_if_stats()
    interfaces = [
        (name, addr.address)
        for name, addrs in psutil.net_if_addrs().items()
        if name in stats and stats[name].isup
        for addr in addrs
        if addr.family == socket.AF_INET
    ]
    return order_hosts(_primary_ip(), interfaces)
