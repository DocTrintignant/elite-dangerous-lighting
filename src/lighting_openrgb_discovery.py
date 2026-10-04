#!/usr/bin/env python3
"""Read-only OpenRGB SDK discovery for Elite Dangerous Lighting.

The client connects only to an already-running OpenRGB SDK server. It requests
protocol/version, controller count, and controller metadata. It never sends
lighting colors, mode changes, zone resize commands, profile writes, or hardware
ownership commands.
"""

from __future__ import annotations

from dataclasses import dataclass
import socket
import struct
import time
from typing import Optional


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6742
CLIENT_NAME = "Elite Dangerous Lighting discovery"
MAX_PROTOCOL_VERSION = 6

# Match OpenRGB's current SDK peer ceiling. This is a receive-side resource
# boundary only; it does not change any packet IDs or controller semantics.
MAX_PACKET_PAYLOAD_BYTES = 8 * 1024 * 1024

# Protocol 6 may interleave acknowledgements/metadata/callbacks while EDL waits
# for one synchronous reply. EDL permits a generous bounded burst rather than
# accepting an unbounded stream from a broken or hostile local SDK peer.
MAX_INTERSTITIAL_PACKETS = 64

# EDL discovery-work ceiling, not an OpenRGB protocol semantic. This remains
# deliberately far above normal desktop controller counts while preventing a
# malformed local SDK peer from turning a four-byte count into unbounded work.
MAX_DISCOVERED_CONTROLLERS = 1024

PACKET_REQUEST_CONTROLLER_COUNT = 0
PACKET_REQUEST_CONTROLLER_DATA = 1
PACKET_REQUEST_PROTOCOL_VERSION = 40
PACKET_SET_CLIENT_NAME = 50

READ_ONLY_PACKET_TYPES = frozenset(
    {
        PACKET_REQUEST_CONTROLLER_COUNT,
        PACKET_REQUEST_CONTROLLER_DATA,
        PACKET_REQUEST_PROTOCOL_VERSION,
        PACKET_SET_CLIENT_NAME,
    }
)

DEVICE_TYPES = {
    0: "Motherboard",
    1: "DRAM",
    2: "GPU",
    3: "Cooler",
    4: "LED strip",
    5: "Keyboard",
    6: "Mouse",
    7: "Mouse mat",
    8: "Headset",
    9: "Headset stand",
    10: "Gamepad",
    11: "Light",
    12: "Speaker",
    13: "Virtual",
    14: "Storage",
    15: "Case",
    16: "Microphone",
    17: "Accessory",
    18: "Keypad",
    19: "Laptop",
    20: "Monitor",
    21: "Unknown",
}

ZONE_TYPES = {
    0: "Single",
    1: "Linear",
    2: "Matrix",
    3: "Linear loop",
    4: "Matrix loop X",
    5: "Matrix loop Y",
    6: "Segmented",
}


class OpenRGBDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenRGBSegment:
    name: str
    segment_type: str
    start_index: int
    led_count: int
    matrix_width: Optional[int] = None
    matrix_height: Optional[int] = None
    matrix_map: Optional[tuple[tuple[Optional[int], ...], ...]] = None
    flags: int = 0


@dataclass(frozen=True)
class OpenRGBMode:
    name: str
    value: Optional[int]
    flags: int
    speed_min: int
    speed_max: int
    brightness_min: int
    brightness_max: int
    colors_min: int
    colors_max: int
    speed: int
    brightness: int
    direction: int
    color_mode: int
    colors: tuple[tuple[int, int, int], ...] = ()


@dataclass(frozen=True)
class OpenRGBZone:
    name: str
    zone_type: str
    led_count: int
    led_min: int
    led_max: int
    led_names: tuple[str, ...]
    led_display_names: tuple[str, ...] = ()
    matrix_width: Optional[int] = None
    matrix_height: Optional[int] = None
    matrix_map: Optional[tuple[tuple[Optional[int], ...], ...]] = None
    segments: tuple[OpenRGBSegment, ...] = ()
    flags: int = 0
    display_name: str = ""


@dataclass(frozen=True)
class OpenRGBDevice:
    index: int
    name: str
    device_type: str
    vendor: str
    description: str
    version: str
    serial: str
    location: str
    led_count: int
    zones: tuple[OpenRGBZone, ...]
    display_name: str = ""
    flags: int = 0
    colors: tuple[tuple[int, int, int], ...] = ()
    active_mode: int = -1
    modes: tuple[OpenRGBMode, ...] = ()


@dataclass(frozen=True)
class OpenRGBInventory:
    protocol_version: int
    devices: tuple[OpenRGBDevice, ...]


class _Reader:
    def __init__(self, data: bytes, context: str = "controller data") -> None:
        self._data = data
        self._offset = 0
        self.context = context

    @property
    def remaining(self) -> int:
        return len(self._data) - self._offset

    def _take(self, size: int) -> bytes:
        end = self._offset + size
        if end > len(self._data):
            raise OpenRGBDiscoveryError(
                f"OpenRGB SDK data ended while parsing {self.context}: "
                f"needed {size} bytes at offset {self._offset}, "
                f"but only {self.remaining} bytes remained."
            )
        chunk = self._data[self._offset:end]
        self._offset = end
        return chunk

    def u16(self) -> int:
        return struct.unpack("<H", self._take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self._take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self._take(4))[0]

    def text(self) -> str:
        length = self.u16()
        raw = self._take(length)
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")

    def text_u32(self) -> str:
        length = self.u32()
        raw = self._take(length)
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")

    def block(self, size: int, context: str):
        return _Reader(self._take(size), context)

    def skip(self, size: int) -> None:
        self._take(size)



def _parse_mode(reader: _Reader, protocol_version: int) -> OpenRGBMode:
    name = reader.text()
    value = reader.i32() if protocol_version < 6 else None
    flags = reader.u32()
    speed_min = reader.u32()
    speed_max = reader.u32()
    brightness_min = reader.u32() if protocol_version >= 3 else 0
    brightness_max = reader.u32() if protocol_version >= 3 else 0
    colors_min = reader.u32()
    colors_max = reader.u32()
    speed = reader.u32()
    brightness = reader.u32() if protocol_version >= 3 else 0
    direction = reader.u32()
    color_mode = reader.u32()
    color_count = reader.u16()
    colors = []
    for _ in range(color_count):
        packed = reader.u32()
        colors.append(
            (
                packed & 0xFF,
                (packed >> 8) & 0xFF,
                (packed >> 16) & 0xFF,
            )
        )
    return OpenRGBMode(
        name=name,
        value=value,
        flags=flags,
        speed_min=speed_min,
        speed_max=speed_max,
        brightness_min=brightness_min,
        brightness_max=brightness_max,
        colors_min=colors_min,
        colors_max=colors_max,
        speed=speed,
        brightness=brightness,
        direction=direction,
        color_mode=color_mode,
        colors=tuple(colors),
    )


def _parse_matrix_block(reader: _Reader, byte_length: int, context: str):
    if not byte_length:
        return None, None, None
    if byte_length < 8:
        raise OpenRGBDiscoveryError(
            f"OpenRGB SDK returned an invalid {context} length of {byte_length} bytes."
        )
    matrix = reader.block(byte_length, context)
    height = matrix.u32()
    width = matrix.u32()
    if height == 0 or width == 0:
        raise OpenRGBDiscoveryError(
            f"OpenRGB SDK returned invalid {context} dimensions {width}×{height}; "
            "a present matrix map must have non-zero width and height."
        )
    expected = height * width
    available_entries = matrix.remaining // 4
    if expected != available_entries:
        raise OpenRGBDiscoveryError(
            f"OpenRGB SDK returned an inconsistent {context}: "
            f"{width}×{height} requires {expected} entries, "
            f"but the block contains {available_entries}."
        )
    flat = []
    for _ in range(expected):
        value = matrix.u32()
        flat.append(None if value == 0xFFFFFFFF else value)
    rows = []
    offset = 0
    for _ in range(height):
        rows.append(tuple(flat[offset:offset + width]))
        offset += width
    return width, height, tuple(rows)


def _parse_zone(reader: _Reader, protocol_version: int, zone_index: int):
    reader.context = f"zone {zone_index} header"
    name = reader.text()
    zone_type_value = reader.i32()
    led_min = reader.u32()
    led_max = reader.u32()
    led_count = reader.u32()

    matrix_size = reader.u16()
    matrix_width, matrix_height, matrix_map = _parse_matrix_block(
        reader,
        matrix_size,
        f"zone {zone_index} ({name!r}) matrix map",
    )

    segments = []
    if protocol_version >= 4:
        reader.context = f"zone {zone_index} ({name!r}) segments"
        segment_count = reader.u16()
        for segment_index in range(segment_count):
            segment_name = reader.text()
            segment_type_value = reader.i32()
            start_index = reader.u32()
            segment_led_count = reader.u32()

            segment_matrix_width = None
            segment_matrix_height = None
            segment_matrix_map = None
            segment_flags = 0
            if protocol_version >= 6:
                segment_matrix_size = reader.u16()
                (
                    segment_matrix_width,
                    segment_matrix_height,
                    segment_matrix_map,
                ) = _parse_matrix_block(
                    reader,
                    segment_matrix_size,
                    f"zone {zone_index} segment {segment_index} ({segment_name!r}) matrix map",
                )
                segment_flags = reader.u32()

            segments.append(
                OpenRGBSegment(
                    name=segment_name,
                    segment_type=ZONE_TYPES.get(
                        segment_type_value,
                        f"Unknown ({segment_type_value})",
                    ),
                    start_index=start_index,
                    led_count=segment_led_count,
                    matrix_width=segment_matrix_width,
                    matrix_height=segment_matrix_height,
                    matrix_map=segment_matrix_map,
                    flags=segment_flags,
                )
            )

    zone_flags = 0
    if protocol_version >= 5:
        reader.context = f"zone {zone_index} ({name!r}) flags"
        zone_flags = reader.u32()

    display_name = ""
    if protocol_version >= 6:
        reader.context = f"zone {zone_index} ({name!r}) protocol-6 metadata"
        reader.i32()  # active_mode
        zone_mode_count = reader.u16()
        for _ in range(zone_mode_count):
            _parse_mode(reader, protocol_version)
        display_name = reader.text()

    return {
        "name": name,
        "display_name": display_name,
        "zone_type": ZONE_TYPES.get(zone_type_value, f"Unknown ({zone_type_value})"),
        "led_min": led_min,
        "led_max": led_max,
        "led_count": led_count,
        "matrix_width": matrix_width,
        "matrix_height": matrix_height,
        "matrix_map": matrix_map,
        "segments": tuple(segments),
        "flags": zone_flags,
    }


def parse_controller_data(
    raw: bytes,
    protocol_version: int,
    device_index: int,
) -> OpenRGBDevice:
    reader = _Reader(raw, f"device ID {device_index} header")
    name = ""

    try:
        declared_packet_size = reader.u32()
        if declared_packet_size > len(raw):
            raise OpenRGBDiscoveryError(
                f"device ID {device_index} declared {declared_packet_size} bytes "
                f"but the SDK packet contained only {len(raw)}."
            )

        device_type_value = reader.i32()
        name = reader.text()

        reader.context = f"device ID {device_index} ({name!r}) metadata"
        vendor = reader.text() if protocol_version >= 1 else ""
        description = reader.text()
        version = reader.text()
        serial = reader.text()
        location = reader.text()

        reader.context = f"device ID {device_index} ({name!r}) modes"
        mode_count = reader.u16()
        active_mode = reader.i32()
        modes = tuple(
            _parse_mode(reader, protocol_version)
            for _ in range(mode_count)
        )

        reader.context = f"device ID {device_index} ({name!r}) zones"
        zone_count = reader.u16()
        zone_records = [
            _parse_zone(reader, protocol_version, zone_index)
            for zone_index in range(zone_count)
        ]

        reader.context = f"device ID {device_index} ({name!r}) LED names"
        led_count = reader.u16()
        led_names = []
        for _ in range(led_count):
            led_names.append(reader.text())
            if protocol_version < 6:
                reader.u32()  # led_value was removed in protocol 6.

        reader.context = f"device ID {device_index} ({name!r}) colors"
        color_count = reader.u16()
        colors = []
        for _ in range(color_count):
            packed = reader.u32()
            colors.append(
                (
                    packed & 0xFF,
                    (packed >> 8) & 0xFF,
                    (packed >> 16) & 0xFF,
                )
            )

        led_display_names = []
        controller_flags = 0
        display_name = ""
        if protocol_version >= 5:
            reader.context = f"device ID {device_index} ({name!r}) display names"
            display_name_count = reader.u16()
            led_display_names = [reader.text() for _ in range(display_name_count)]
            controller_flags = reader.u32()

        if protocol_version >= 6:
            reader.context = f"device ID {device_index} ({name!r}) protocol-6 metadata"
            display_name = reader.text()
            reader.text_u32()  # controller configuration JSON/string; preserve later if needed.

        zones = []
        led_offset = 0
        for record in zone_records:
            count = record["led_count"]
            zone_led_names = tuple(led_names[led_offset:led_offset + count])
            zone_display_names = tuple(
                led_display_names[led_offset:led_offset + count]
            )
            led_offset += count
            zones.append(
                OpenRGBZone(
                    name=record["name"],
                    display_name=record["display_name"],
                    zone_type=record["zone_type"],
                    led_count=count,
                    led_min=record["led_min"],
                    led_max=record["led_max"],
                    led_names=zone_led_names,
                    led_display_names=zone_display_names,
                    matrix_width=record["matrix_width"],
                    matrix_height=record["matrix_height"],
                    matrix_map=record["matrix_map"],
                    segments=record["segments"],
                    flags=record["flags"],
                )
            )

        return OpenRGBDevice(
            index=device_index,
            name=name,
            display_name=display_name,
            device_type=DEVICE_TYPES.get(
                device_type_value,
                f"Unknown ({device_type_value})",
            ),
            vendor=vendor,
            description=description,
            version=version,
            serial=serial,
            location=location,
            led_count=led_count,
            zones=tuple(zones),
            flags=controller_flags,
            colors=tuple(colors),
            active_mode=active_mode,
            modes=modes,
        )
    except OpenRGBDiscoveryError as exc:
        label = f"device ID {device_index}"
        if name:
            label += f" ({name})"
        raise OpenRGBDiscoveryError(
            f"OpenRGB SDK protocol {protocol_version} parsing failed for {label}. "
            f"{exc}"
        ) from exc




class OpenRGBReadOnlyClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = 3.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._socket: socket.socket | None = None
        self.protocol_version = MAX_PROTOCOL_VERSION

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def connect(self) -> None:
        if self._socket is not None:
            return
        try:
            self._socket = socket.create_connection((self.host, self.port), self.timeout)
            self._socket.settimeout(self.timeout)
            self._send(
                0,
                PACKET_REQUEST_PROTOCOL_VERSION,
                struct.pack("<I", MAX_PROTOCOL_VERSION),
            )
            _device, packet_type, payload = self._receive_expected(
                PACKET_REQUEST_PROTOCOL_VERSION
            )
            if packet_type != PACKET_REQUEST_PROTOCOL_VERSION or len(payload) < 4:
                raise OpenRGBDiscoveryError("OpenRGB did not return a valid protocol version")
            server_version = struct.unpack("<I", payload[:4])[0]
            self.protocol_version = min(server_version, MAX_PROTOCOL_VERSION)

            client_name = CLIENT_NAME.encode("utf-8") + b"\x00"
            self._send(0, PACKET_SET_CLIENT_NAME, client_name)
        except OSError as exc:
            self.close()
            raise OpenRGBDiscoveryError(
                f"OpenRGB SDK unavailable at {self.host}:{self.port}. "
                "Start or enable the OpenRGB SDK server, then scan again."
            ) from exc

    def close(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _send(self, device_id: int, packet_type: int, payload: bytes = b"") -> None:
        if packet_type not in READ_ONLY_PACKET_TYPES:
            raise OpenRGBDiscoveryError(
                f"Read-only discovery blocked OpenRGB packet type {packet_type}"
            )
        if self._socket is None:
            raise OpenRGBDiscoveryError("OpenRGB SDK client is not connected")
        header = struct.pack("<4sIII", b"ORGB", device_id, packet_type, len(payload))
        self._socket.sendall(header + payload)

    def _recv_exact(self, size: int, *, deadline: float | None = None) -> bytes:
        if self._socket is None:
            raise OpenRGBDiscoveryError("OpenRGB SDK client is not connected")
        chunks = []
        remaining = size
        while remaining:
            if deadline is not None:
                seconds_left = deadline - time.monotonic()
                if seconds_left <= 0:
                    raise OpenRGBDiscoveryError(
                        "OpenRGB SDK transaction exceeded the configured timeout"
                    )
                self._socket.settimeout(min(self.timeout, seconds_left))
            try:
                chunk = self._socket.recv(remaining)
            except socket.timeout as exc:
                raise OpenRGBDiscoveryError(
                    "OpenRGB SDK transaction exceeded the configured timeout"
                ) from exc
            finally:
                if deadline is not None and self._socket is not None:
                    self._socket.settimeout(self.timeout)
            if not chunk:
                raise OpenRGBDiscoveryError("OpenRGB SDK connection closed unexpectedly")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _receive(self, *, deadline: float | None = None):
        header = self._recv_exact(16, deadline=deadline)
        signature, device_id, packet_type, payload_size = struct.unpack("<4sIII", header)
        if signature != b"ORGB":
            raise OpenRGBDiscoveryError("Invalid OpenRGB SDK packet signature")
        if payload_size > MAX_PACKET_PAYLOAD_BYTES:
            raise OpenRGBDiscoveryError(
                f"OpenRGB SDK packet payload {payload_size} bytes exceeds the "
                f"{MAX_PACKET_PAYLOAD_BYTES}-byte peer limit"
            )
        payload = (
            self._recv_exact(payload_size, deadline=deadline)
            if payload_size
            else b""
        )
        return device_id, packet_type, payload

    def _receive_expected(self, expected_packet_type: int):
        # Protocol 6 can emit acknowledgements/server metadata/callbacks between
        # the synchronous discovery replies. Ignore those without performing any
        # write or state-changing request.
        ignorable = {10, 51, 53, 100, 101, 102, 103}
        deadline = time.monotonic() + self.timeout
        skipped = 0
        while True:
            if time.monotonic() >= deadline:
                raise OpenRGBDiscoveryError(
                    f"OpenRGB SDK transaction timed out while waiting for packet "
                    f"{expected_packet_type}"
                )
            device_id, packet_type, payload = self._receive(deadline=deadline)
            if packet_type == expected_packet_type:
                return device_id, packet_type, payload
            if packet_type in ignorable:
                skipped += 1
                if skipped > MAX_INTERSTITIAL_PACKETS:
                    raise OpenRGBDiscoveryError(
                        f"OpenRGB SDK sent more than {MAX_INTERSTITIAL_PACKETS} "
                        f"interstitial packets while EDL waited for packet "
                        f"{expected_packet_type}"
                    )
                continue
            raise OpenRGBDiscoveryError(
                f"Unexpected OpenRGB SDK packet {packet_type} while waiting for "
                f"packet {expected_packet_type} (protocol {self.protocol_version})."
            )

    def discover(self) -> OpenRGBInventory:
        if self._socket is None:
            self.connect()

        self._send(0, PACKET_REQUEST_CONTROLLER_COUNT)
        _device, packet_type, payload = self._receive_expected(
            PACKET_REQUEST_CONTROLLER_COUNT
        )
        if packet_type != PACKET_REQUEST_CONTROLLER_COUNT or len(payload) < 4:
            raise OpenRGBDiscoveryError(
                f"OpenRGB SDK protocol {self.protocol_version} did not return "
                "a valid controller-count response."
            )
        count = struct.unpack("<I", payload[:4])[0]
        if count > MAX_DISCOVERED_CONTROLLERS:
            raise OpenRGBDiscoveryError(
                f"OpenRGB SDK reported {count} controllers; EDL discovery accepts at most "
                f"{MAX_DISCOVERED_CONTROLLERS} controllers per server."
            )

        if self.protocol_version >= 6:
            required = 4 + (count * 4)
            if len(payload) < required:
                raise OpenRGBDiscoveryError(
                    f"OpenRGB SDK protocol {self.protocol_version} reported {count} "
                    f"controllers but returned only {len(payload)} bytes of controller IDs."
                )
            device_ids = list(struct.unpack(f"<{count}I", payload[4:required]))
        else:
            device_ids = list(range(count))

        devices = []
        for device_id in device_ids:
            request_payload = (
                struct.pack("<I", self.protocol_version)
                if self.protocol_version > 0
                else b""
            )
            self._send(
                device_id,
                PACKET_REQUEST_CONTROLLER_DATA,
                request_payload,
            )
            response_device, response_type, controller_payload = self._receive_expected(
                PACKET_REQUEST_CONTROLLER_DATA
            )
            if response_type != PACKET_REQUEST_CONTROLLER_DATA:
                raise OpenRGBDiscoveryError(
                    f"Unexpected OpenRGB response while reading device ID {device_id}"
                )
            devices.append(
                parse_controller_data(
                    controller_payload,
                    self.protocol_version,
                    response_device,
                )
            )

        return OpenRGBInventory(
            protocol_version=self.protocol_version,
            devices=tuple(devices),
        )


def discover_openrgb(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = 3.0,
) -> OpenRGBInventory:
    with OpenRGBReadOnlyClient(host=host, port=port, timeout=timeout) as client:
        return client.discover()
