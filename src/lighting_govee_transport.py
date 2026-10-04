#!/usr/bin/env python3
"""Production transport for physically accepted direct Govee realtime LAN output.

The wire behavior is intentionally the same as the isolated acceptance transport
that was physically proven on H61C3:

- UDP control port 4003
- JSON ``razer`` envelope carrying base64 binary packets
- B1 01 / B1 00 native realtime ownership activation/deactivation
- B0 segment frame with mode flag 0
- one persistent UDP socket per owned physical device

No discovery or speculative protocol variants live here. Production enhanced
mode uses an explicitly configured IPv4 address and only supported devices.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import socket
from typing import Iterable

CONTROL_PORT = 4003
MAX_SEGMENTS = 255
RGB = tuple[int, int, int]


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
        raise ValueError("Govee enhanced output requires an IPv4 address")
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


def build_segment_packet(colours: Iterable[RGB], mode_flag: int = 0) -> bytes:
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


class GoveeRealtimeSession:
    """Persistent direct-LAN owner for one configured enhanced Govee device."""

    def __init__(self, ip: str) -> None:
        self.ip = _validate_ipv4_unicast(ip)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._closed = False
        self._activated = False

    @property
    def active(self) -> bool:
        return self._activated and not self._closed

    def _send_packet(self, packet: bytes) -> None:
        if self._closed:
            raise RuntimeError("Govee realtime session is closed")
        self._socket.sendto(razer_envelope(packet), (self.ip, CONTROL_PORT))

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Govee realtime session is closed")
        if self._activated:
            return
        self._send_packet(build_mode_packet(True))
        self._activated = True

    def render_segments(self, colours: Iterable[RGB]) -> None:
        if not self.active:
            raise RuntimeError("Govee realtime session is not active")
        self._send_packet(build_segment_packet(colours, mode_flag=0))

    def release(self) -> None:
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
            self.release()
        finally:
            self._socket.close()
            self._closed = True

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
