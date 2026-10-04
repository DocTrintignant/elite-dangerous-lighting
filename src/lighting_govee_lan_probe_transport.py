#!/usr/bin/env python3
"""Isolated experimental transport for H61C3 realtime-LAN acceptance probes.

This module is intentionally NOT connected to the EDL renderer. It implements
only the smallest reverse-engineered Govee Desktop/DreamView LAN boundary needed
to prove or reject direct per-segment control on real hardware.

Protocol evidence currently comes from independent community implementations
(OpenRGB and current Govee LAN/DreamView tools), not from a public Govee realtime
protocol specification. Physical acceptance is therefore required before this
can become a production renderer.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from typing import Iterable

MULTICAST_GROUP = "239.255.255.250"
SCAN_PORT = 4001
RESPONSE_PORT = 4002
CONTROL_PORT = 4003
MAX_SEGMENTS = 255
RGB = tuple[int, int, int]


@dataclass(frozen=True)
class DiscoveredGoveeDevice:
    ip: str
    sku: str


def _validate_rgb(rgb: RGB) -> RGB:
    if not isinstance(rgb, tuple) or len(rgb) != 3:
        raise ValueError("RGB value must be a three-item tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in rgb):
        raise ValueError("RGB components must be integers")
    if any(value < 0 or value > 255 for value in rgb):
        raise ValueError("RGB components must be 0..255")
    return rgb


def _validate_ipv4_unicast(value: str) -> str:
    address = ipaddress.ip_address(value)
    if address.version != 4:
        raise ValueError("Govee LAN probe requires an IPv4 address")
    if address.is_multicast or address.is_unspecified:
        raise ValueError("Govee device address must be unicast")
    return str(address)


def xor_checksum(data: bytes | bytearray) -> int:
    checksum = 0
    for value in data:
        checksum ^= value
    return checksum


def build_mode_packet(enabled: bool) -> bytes:
    packet = bytearray((0xBB, 0x00, 0x01, 0xB1, 0x01 if enabled else 0x00))
    packet.append(xor_checksum(packet))
    return bytes(packet)


def build_segment_packet(colours: Iterable[RGB], mode_flag: int) -> bytes:
    values = tuple(_validate_rgb(rgb) for rgb in colours)
    if not values:
        raise ValueError("realtime segment frame requires at least one colour")
    if len(values) > MAX_SEGMENTS:
        raise ValueError(f"realtime segment frame supports at most {MAX_SEGMENTS} colours")
    if mode_flag not in (0, 1):
        raise ValueError("mode_flag must be 0 or 1")

    data_len = 2 + (3 * len(values))
    packet = bytearray(
        (
            0xBB,
            (data_len >> 8) & 0xFF,
            data_len & 0xFF,
            0xB0,
            mode_flag,
            len(values),
        )
    )
    for red, green, blue in values:
        packet.extend((red, green, blue))
    packet.append(xor_checksum(packet))
    return bytes(packet)


def razer_envelope(packet: bytes) -> bytes:
    if not isinstance(packet, bytes) or not packet:
        raise ValueError("packet must be non-empty bytes")
    payload = base64.b64encode(packet).decode("ascii")
    return json.dumps(
        {"msg": {"cmd": "razer", "data": {"pt": payload}}},
        separators=(",", ":"),
    ).encode("utf-8")


def scan_message() -> bytes:
    return b'{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}'


def discover_devices(timeout_seconds: float = 2.5) -> tuple[DiscoveredGoveeDevice, ...]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    found: dict[tuple[str, str], DiscoveredGoveeDevice] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", RESPONSE_PORT))
        sock.settimeout(0.2)
        sock.sendto(scan_message(), (MULTICAST_GROUP, SCAN_PORT))
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                raw, _source = sock.recvfrom(8192)
            except socket.timeout:
                continue
            try:
                decoded = json.loads(raw.decode("utf-8"))
                data = decoded["msg"]["data"]
                ip = _validate_ipv4_unicast(str(data["ip"]))
                sku = str(data["sku"]).strip().upper()
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if sku:
                found[(ip, sku)] = DiscoveredGoveeDevice(ip=ip, sku=sku)
    finally:
        sock.close()
    return tuple(sorted(found.values(), key=lambda device: (device.sku, device.ip)))


class GoveeRealtimeProbeSession:
    """Persistent UDP sender for one explicitly selected Govee device."""

    def __init__(self, ip: str) -> None:
        self.ip = _validate_ipv4_unicast(ip)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._closed = False
        self._activated = False

    def _send_packet(self, packet: bytes) -> None:
        if self._closed:
            raise RuntimeError("Govee probe session is closed")
        self._socket.sendto(razer_envelope(packet), (self.ip, CONTROL_PORT))

    def activate(self) -> None:
        self._send_packet(build_mode_packet(True))
        self._activated = True

    def send_segments(self, colours: Iterable[RGB], *, mode_flag: int) -> None:
        self._send_packet(build_segment_packet(colours, mode_flag))

    def deactivate(self) -> None:
        if self._closed or not self._activated:
            return
        try:
            self._send_packet(build_mode_packet(False))
        finally:
            self._activated = False

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.deactivate()
        finally:
            self._socket.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
