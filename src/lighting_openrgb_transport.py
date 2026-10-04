#!/usr/bin/env python3
"""Persistent OpenRGB SDK transport for realtime EDL rendering.

This is the first production write boundary below the existing EDL renderer/router
seam. It connects only to an already-running OpenRGB SDK server, resolves devices
from live inventory, switches a target controller into direct/custom mode once,
and sends colour frames.

Hard invariant: realtime EDL OpenRGB transport NEVER resizes or reconfigures zone
topology. Packet 1000 (ResizeZone) is intentionally absent from the allowed write
set and no resize/configure method is exposed here.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Iterable

from lighting_openrgb_catalog import (
    OpenRGBPhysicalDevice,
    build_openrgb_physical_devices,
)
from lighting_openrgb_discovery import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    READ_ONLY_PACKET_TYPES,
    OpenRGBDevice,
    OpenRGBDiscoveryError,
    OpenRGBInventory,
    OpenRGBMode,
    OpenRGBReadOnlyClient,
    OpenRGBZone,
)


PACKET_UPDATE_LEDS = 1050
PACKET_UPDATE_ZONE_LEDS = 1051
PACKET_SET_CUSTOM_MODE = 1100
PACKET_UPDATE_MODE = 1101

# Deliberately excludes ResizeZone (1000), segment/configuration packets and
# profile/settings mutation. Realtime EDL owns colours/frames only.
REALTIME_WRITE_PACKET_TYPES = frozenset(
    {
        PACKET_UPDATE_LEDS,
        PACKET_UPDATE_ZONE_LEDS,
        PACKET_SET_CUSTOM_MODE,
        PACKET_UPDATE_MODE,
    }
)
ALLOWED_PACKET_TYPES = READ_ONLY_PACKET_TYPES | REALTIME_WRITE_PACKET_TYPES

CLIENT_NAME = "Elite Dangerous Lighting realtime"

RGB = tuple[int, int, int]


class OpenRGBTransportError(RuntimeError):
    pass


def openrgb_color(rgb: RGB) -> int:
    if (
        not isinstance(rgb, tuple)
        or len(rgb) != 3
        or any(isinstance(value, bool) or not isinstance(value, int) for value in rgb)
        or any(value < 0 or value > 255 for value in rgb)
    ):
        raise ValueError("OpenRGB colour must be an (R, G, B) tuple with components 0..255")
    r, g, b = rgb
    return r | (g << 8) | (b << 16)


def _frame_payload(colours: Iterable[RGB]) -> tuple[tuple[RGB, ...], bytes]:
    values = tuple(colours)
    if len(values) > 0xFFFF:
        raise ValueError("OpenRGB frame cannot contain more than 65535 colours")
    packed = tuple(openrgb_color(value) for value in values)
    payload_size = 4 + 2 + (4 * len(packed))
    payload = struct.pack("<IH", payload_size, len(packed))
    if packed:
        payload += struct.pack(f"<{len(packed)}I", *packed)
    return values, payload


def _mode_description_payload(mode: OpenRGBMode, protocol_version: int) -> bytes:
    name = mode.name.encode("utf-8") + b"\x00"
    if len(name) > 0xFFFF:
        raise ValueError("OpenRGB mode name is too long")
    if len(mode.colors) > 0xFFFF:
        raise ValueError("OpenRGB mode cannot contain more than 65535 colours")

    payload = struct.pack("<H", len(name)) + name
    if protocol_version < 6:
        if mode.value is None:
            raise OpenRGBTransportError(
                "Cannot restore a pre-v6 OpenRGB mode without its mode value"
            )
        payload += struct.pack("<i", int(mode.value))

    payload += struct.pack(
        "<III",
        int(mode.flags),
        int(mode.speed_min),
        int(mode.speed_max),
    )
    if protocol_version >= 3:
        payload += struct.pack(
            "<II",
            int(mode.brightness_min),
            int(mode.brightness_max),
        )
    payload += struct.pack(
        "<III",
        int(mode.colors_min),
        int(mode.colors_max),
        int(mode.speed),
    )
    if protocol_version >= 3:
        payload += struct.pack("<I", int(mode.brightness))
    payload += struct.pack(
        "<IIH",
        int(mode.direction),
        int(mode.color_mode),
        len(mode.colors),
    )
    if mode.colors:
        payload += struct.pack(
            f"<{len(mode.colors)}I",
            *(openrgb_color(colour) for colour in mode.colors),
        )
    return payload


def _update_mode_payload(
    mode_index: int,
    mode: OpenRGBMode,
    protocol_version: int,
) -> bytes:
    if isinstance(mode_index, bool) or not isinstance(mode_index, int) or mode_index < 0:
        raise ValueError("OpenRGB mode index must be a non-negative integer")
    mode_payload = _mode_description_payload(mode, protocol_version)
    data_size = 8 + len(mode_payload)
    return struct.pack("<Ii", data_size, mode_index) + mode_payload


def _zone_frame_payload(zone_index: int, colours: Iterable[RGB]) -> tuple[tuple[RGB, ...], bytes]:
    if isinstance(zone_index, bool) or not isinstance(zone_index, int) or zone_index < 0:
        raise ValueError("OpenRGB zone index must be a non-negative integer")
    values = tuple(colours)
    if len(values) > 0xFFFF:
        raise ValueError("OpenRGB zone frame cannot contain more than 65535 colours")
    packed = tuple(openrgb_color(value) for value in values)
    payload_size = 4 + 4 + 2 + (4 * len(packed))
    payload = struct.pack("<IIH", payload_size, zone_index, len(packed))
    if packed:
        payload += struct.pack(f"<{len(packed)}I", *packed)
    return values, payload


class OpenRGBRealtimeClient(OpenRGBReadOnlyClient):
    """One persistent SDK connection used for bounded realtime colour writes."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = 3.0,
    ) -> None:
        super().__init__(host=host, port=port, timeout=timeout)
        self.inventory: OpenRGBInventory | None = None
        self._send_lock = threading.Lock()
        self._drain_stop = threading.Event()
        self._drain_thread: threading.Thread | None = None
        self._custom_mode_ids: set[int] = set()

    def _send(self, device_id: int, packet_type: int, payload: bytes = b"") -> None:
        if packet_type not in ALLOWED_PACKET_TYPES:
            raise OpenRGBTransportError(
                f"Realtime OpenRGB transport blocked packet type {packet_type}; "
                "topology/configuration writes are not permitted."
            )
        if self._socket is None:
            raise OpenRGBTransportError("OpenRGB realtime client is not connected")
        header = struct.pack("<4sIII", b"ORGB", int(device_id), int(packet_type), len(payload))
        try:
            with self._send_lock:
                self._socket.sendall(header + payload)
        except OSError as exc:
            raise OpenRGBTransportError(
                "OpenRGB SDK connection failed while sending a realtime frame"
            ) from exc

    def connect(self) -> None:
        super().connect()
        # Replace the discovery-only label after protocol negotiation. This uses
        # the already-accepted harmless client-name packet.
        client_name = CLIENT_NAME.encode("utf-8") + b"\x00"
        self._send(0, 50, client_name)

    def start(
        self,
        *,
        readiness_timeout: float = 8.0,
        retry_interval: float = 0.20,
    ) -> OpenRGBInventory:
        """Connect and wait until the server exposes a non-empty usable inventory."""
        if readiness_timeout <= 0:
            raise ValueError("readiness_timeout must be positive")
        if retry_interval <= 0:
            raise ValueError("retry_interval must be positive")
        if self.inventory is not None and self._socket is not None:
            return self.inventory

        self._drain_stop.clear()
        deadline = time.monotonic() + readiness_timeout
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                super().close()
                self.protocol_version = 6
                self.connect()
                inventory = self.discover()
                if inventory.devices:
                    self.inventory = inventory
                    self._custom_mode_ids.clear()
                    self._start_drain_thread()
                    return inventory
                last_error = OpenRGBTransportError(
                    "OpenRGB SDK server is reachable but its controller inventory is still empty"
                )
            except (OpenRGBDiscoveryError, OpenRGBTransportError, OSError) as exc:
                last_error = exc
            super().close()
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(retry_interval, remaining))

        self.inventory = None
        detail = f": {last_error}" if last_error is not None else ""
        raise OpenRGBTransportError(
            f"OpenRGB SDK did not expose a usable device inventory within "
            f"{readiness_timeout:.1f}s{detail}"
        )

    def _start_drain_thread(self) -> None:
        if self._drain_thread is not None and self._drain_thread.is_alive():
            return
        self._drain_thread = threading.Thread(
            target=self._drain_loop,
            name="edl-openrgb-sdk-drain",
            daemon=True,
        )
        self._drain_thread.start()

    def _drain_loop(self) -> None:
        # Protocol 6 sends asynchronous controller/update notifications back to
        # clients. Once startup discovery is complete EDL has no synchronous SDK
        # requests, so consume those notifications to prevent receive-buffer
        # backpressure during long 30/60 FPS sessions.
        while not self._drain_stop.is_set():
            try:
                self._receive()
            except socket.timeout:
                continue
            except (OSError, OpenRGBDiscoveryError):
                return

    def close(self) -> None:
        self._drain_stop.set()
        super().close()
        thread = self._drain_thread
        self._drain_thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.25)
        self.inventory = None
        self._custom_mode_ids.clear()

    def __enter__(self):
        self.start()
        return self

    def physical_devices(self) -> tuple[OpenRGBPhysicalDevice, ...]:
        if self.inventory is None:
            raise OpenRGBTransportError("OpenRGB realtime inventory is not loaded")
        return build_openrgb_physical_devices(self.inventory)

    def resolve_physical_device(
        self,
        *,
        identity: str | None = None,
        name: str | None = None,
        vendor: str | None = None,
        device_type: str | None = None,
    ) -> OpenRGBPhysicalDevice:
        """Resolve one physical device from live metadata, never numeric index."""
        if not identity and not name:
            raise ValueError("resolve_physical_device requires identity or name")

        devices = list(self.physical_devices())
        if identity:
            devices = [device for device in devices if device.identity == identity]
        if name:
            wanted = name.strip().casefold()
            devices = [device for device in devices if device.name.strip().casefold() == wanted]
        if vendor:
            wanted = vendor.strip().casefold()
            devices = [device for device in devices if device.vendor.strip().casefold() == wanted]
        if device_type:
            wanted = device_type.strip().casefold()
            devices = [
                device
                for device in devices
                if device.device_type.strip().casefold() == wanted
            ]

        if not devices:
            detail = identity or name or "<unspecified>"
            raise OpenRGBTransportError(
                f"OpenRGB device {detail!r} is not present in the current SDK inventory"
            )
        if len(devices) != 1:
            labels = ", ".join(
                f"{device.name} [{device.identity}]" for device in devices
            )
            raise OpenRGBTransportError(
                f"OpenRGB device resolution is ambiguous: {labels}"
            )
        return devices[0]

    @staticmethod
    def resolve_zone(
        device: OpenRGBDevice,
        zone: int | str,
    ) -> tuple[int, OpenRGBZone]:
        if isinstance(zone, bool):
            raise ValueError("OpenRGB zone must be an index or name")
        if isinstance(zone, int):
            if not 0 <= zone < len(device.zones):
                raise OpenRGBTransportError(
                    f"OpenRGB device {device.name!r} has no zone index {zone}"
                )
            return zone, device.zones[zone]

        wanted = str(zone).strip().casefold()
        matches = [
            (index, candidate)
            for index, candidate in enumerate(device.zones)
            if candidate.name.strip().casefold() == wanted
            or candidate.display_name.strip().casefold() == wanted
        ]
        if not matches:
            raise OpenRGBTransportError(
                f"OpenRGB device {device.name!r} has no zone named {zone!r}"
            )
        if len(matches) != 1:
            raise OpenRGBTransportError(
                f"OpenRGB zone name {zone!r} is ambiguous on {device.name!r}"
            )
        return matches[0]

    def set_custom_mode(self, device: OpenRGBDevice) -> None:
        device_id = int(device.index)
        if device_id in self._custom_mode_ids:
            return
        self._send(device_id, PACKET_SET_CUSTOM_MODE)
        self._custom_mode_ids.add(device_id)

    def restore_device_mode(self, device: OpenRGBDevice) -> bool:
        mode_index = int(device.active_mode)
        if mode_index < 0:
            return False
        if mode_index >= len(device.modes):
            raise OpenRGBTransportError(
                f"Cannot restore OpenRGB device {device.name!r}: active mode "
                f"{mode_index} is outside its captured mode list."
            )
        payload = _update_mode_payload(
            mode_index,
            device.modes[mode_index],
            self.protocol_version,
        )
        self._send(int(device.index), PACKET_UPDATE_MODE, payload)
        self._custom_mode_ids.discard(int(device.index))
        return True

    def write_device_frame(
        self,
        device: OpenRGBDevice,
        colours: Iterable[RGB],
    ) -> None:
        values, payload = _frame_payload(colours)
        if len(values) != int(device.led_count):
            raise OpenRGBTransportError(
                f"OpenRGB device {device.name!r} expects {device.led_count} colours, "
                f"got {len(values)}"
            )
        if not values:
            raise OpenRGBTransportError(
                f"OpenRGB device {device.name!r} currently exposes zero LEDs"
            )
        self.set_custom_mode(device)
        self._send(int(device.index), PACKET_UPDATE_LEDS, payload)

    def write_device_rgb(self, device: OpenRGBDevice, rgb: RGB) -> None:
        self.write_device_frame(device, (rgb,) * int(device.led_count))

    def write_zone_frame(
        self,
        device: OpenRGBDevice,
        zone: int | str,
        colours: Iterable[RGB],
    ) -> None:
        zone_index, zone_value = self.resolve_zone(device, zone)
        values, payload = _zone_frame_payload(zone_index, colours)
        if len(values) != int(zone_value.led_count):
            raise OpenRGBTransportError(
                f"OpenRGB zone {zone_value.name!r} on {device.name!r} expects "
                f"{zone_value.led_count} colours, got {len(values)}"
            )
        if not values:
            raise OpenRGBTransportError(
                f"OpenRGB zone {zone_value.name!r} on {device.name!r} has zero configured LEDs"
            )
        self.set_custom_mode(device)
        self._send(int(device.index), PACKET_UPDATE_ZONE_LEDS, payload)

    def write_zone_rgb(
        self,
        device: OpenRGBDevice,
        zone: int | str,
        rgb: RGB,
    ) -> None:
        _index, zone_value = self.resolve_zone(device, zone)
        self.write_zone_frame(device, zone, (rgb,) * int(zone_value.led_count))
