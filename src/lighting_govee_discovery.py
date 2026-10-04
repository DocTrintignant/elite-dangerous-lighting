#!/usr/bin/env python3
"""Optional LAN discovery for Govee setup.

Discovery is a setup convenience only. The production renderer still uses the
explicit saved IPv4 address and does not depend on discovery succeeding.

The scan grammar is the same one already exercised by the H61C3 probe tooling:
UDP multicast scan on 239.255.255.250:4001 with replies received on UDP 4002.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass

MULTICAST_GROUP = "239.255.255.250"
SCAN_PORT = 4001
RESPONSE_PORT = 4002


@dataclass(frozen=True)
class DiscoveredGoveeDevice:
    """One unauthenticated LAN inventory claim, never identity proof."""

    ip: str
    sku: str
    source_ip: str | None = None

    @property
    def source_matches_advertised(self) -> bool | None:
        if self.source_ip is None:
            return None
        return self.source_ip == self.ip


def scan_message() -> bytes:
    return b'{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}'


def _local_multicast_ipv4_interfaces() -> tuple[str, ...]:
    """Return usable local IPv4 interfaces for LAN multicast discovery.

    Windows may route an unqualified multicast send through loopback on a
    multi-adapter host. Enumerate the host's IPv4 addresses and exclude
    loopback/link-local/non-unicast addresses so discovery is transmitted on
    each plausible LAN interface instead of relying on that OS choice.
    """
    try:
        resolved = socket.getaddrinfo(
            socket.gethostname(),
            None,
            socket.AF_INET,
            socket.SOCK_DGRAM,
        )
    except OSError:
        return ()

    found: set[str] = set()
    for entry in resolved:
        try:
            value = str(entry[4][0])
            address = ipaddress.ip_address(value)
        except (IndexError, TypeError, ValueError):
            continue
        if (
            address.version != 4
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            continue
        found.add(str(address))
    return tuple(sorted(found))


def _valid_ipv4(value: str) -> str:
    address = ipaddress.ip_address(value)
    if address.version != 4 or address.is_multicast or address.is_unspecified:
        raise ValueError("Govee discovery requires a unicast IPv4 address")
    return str(address)


def parse_scan_reply(
    raw: bytes,
    *,
    source_ip: str | None = None,
) -> DiscoveredGoveeDevice | None:
    try:
        decoded = json.loads(raw.decode("utf-8"))
        data = decoded["msg"]["data"]
        ip = _valid_ipv4(str(data["ip"]))
        sku = str(data["sku"]).strip().upper()
        source = _valid_ipv4(source_ip) if source_ip is not None else None
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if not sku:
        return None
    return DiscoveredGoveeDevice(ip=ip, sku=sku, source_ip=source)


def discover_govee_devices(timeout_seconds: float = 1.5) -> tuple[DiscoveredGoveeDevice, ...]:
    """Return consistent unauthenticated LAN inventory claims from one bounded scan.

    A reply is a setup suggestion only. It does not prove device identity,
    ownership, model authenticity, or native geometry. When the UDP source IP is
    available it must match the address advertised inside the reply before EDL
    offers the claim to setup.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    found: dict[tuple[str, str], DiscoveredGoveeDevice] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", RESPONSE_PORT))
        sock.settimeout(0.15)
        interfaces = _local_multicast_ipv4_interfaces()
        if interfaces:
            for interface_ip in interfaces:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(interface_ip),
                )
                sock.sendto(scan_message(), (MULTICAST_GROUP, SCAN_PORT))
        else:
            # Preserve the previous OS-selected behavior only when no usable
            # interface can be enumerated.
            sock.sendto(scan_message(), (MULTICAST_GROUP, SCAN_PORT))
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                raw, source = sock.recvfrom(8192)
            except socket.timeout:
                continue
            source_ip = source[0] if isinstance(source, tuple) and source else None
            device = parse_scan_reply(raw, source_ip=source_ip)
            if device is not None and device.source_matches_advertised is not False:
                found[(device.ip, device.sku)] = device
    finally:
        sock.close()

    return tuple(sorted(found.values(), key=lambda device: (device.sku, device.ip)))
