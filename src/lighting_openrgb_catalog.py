#!/usr/bin/env python3
"""Build the user-facing OpenRGB physical-device catalogue.

OpenRGB can expose more than one logical controller/surface for one physical
device. This layer groups only when OpenRGB metadata provides strong matching
evidence. It never changes hardware state or chooses a renderer.
"""

from __future__ import annotations

from dataclasses import dataclass

from lighting_device_selection import openrgb_device_identity
from lighting_openrgb_discovery import OpenRGBDevice, OpenRGBInventory


_MISSING_TOKENS = {
    "",
    "0",
    "00",
    "00000000",
    "000000000000",
    "unknown",
    "none",
    "n/a",
    "na",
    "not available",
}


def _clean(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _norm(value: str) -> str:
    return _clean(value).casefold()


def _useful(value: str) -> bool:
    return _norm(value) not in _MISSING_TOKENS


def _surface_score(device: OpenRGBDevice) -> tuple[int, int, int, int]:
    matrix_cells = 0
    matrix_zones = 0
    segment_count = 0
    for zone in device.zones:
        if zone.matrix_map is not None:
            matrix_zones += 1
            if zone.matrix_width and zone.matrix_height:
                matrix_cells += zone.matrix_width * zone.matrix_height
        segment_count += len(zone.segments)
    return (
        matrix_zones,
        matrix_cells,
        int(device.led_count),
        segment_count + len(device.zones),
    )


def _group_key(device: OpenRGBDevice) -> tuple[str, ...]:
    vendor = _norm(device.vendor)
    name = _norm(device.name)
    device_type = _norm(device.device_type)
    serial = _clean(device.serial)
    location = _clean(device.location)

    if _useful(serial):
        return ("serial", vendor, device_type, _norm(serial))

    if _useful(location):
        return ("location", vendor, name, device_type, _norm(location))

    # No strong physical evidence: keep this OpenRGB controller distinct.
    return ("controller", openrgb_device_identity(device))


def _physical_identity(key: tuple[str, ...], surfaces: tuple[OpenRGBDevice, ...]) -> str:
    if key[0] == "controller":
        return openrgb_device_identity(surfaces[0])
    return "openrgb-physical|" + "|".join(key)


@dataclass(frozen=True)
class OpenRGBPhysicalDevice:
    identity: str
    name: str
    device_type: str
    vendor: str
    surfaces: tuple[OpenRGBDevice, ...]
    primary_surface: OpenRGBDevice

    @property
    def led_count(self) -> int:
        """LED count of the richest reported control surface, not a duplicate sum."""
        return int(self.primary_surface.led_count)

    @property
    def member_identities(self) -> tuple[str, ...]:
        return tuple(openrgb_device_identity(surface) for surface in self.surfaces)


def build_openrgb_physical_devices(
    inventory: OpenRGBInventory,
) -> tuple[OpenRGBPhysicalDevice, ...]:
    groups: dict[tuple[str, ...], list[OpenRGBDevice]] = {}

    for device in inventory.devices:
        groups.setdefault(_group_key(device), []).append(device)

    physical_devices = []
    for key, members in groups.items():
        surfaces = tuple(
            sorted(
                members,
                key=lambda device: (
                    _surface_score(device),
                    _norm(device.name),
                    openrgb_device_identity(device),
                ),
                reverse=True,
            )
        )
        primary = surfaces[0]
        physical_devices.append(
            OpenRGBPhysicalDevice(
                identity=_physical_identity(key, surfaces),
                name=primary.name,
                device_type=primary.device_type,
                vendor=primary.vendor,
                surfaces=surfaces,
                primary_surface=primary,
            )
        )

    return tuple(
        sorted(
            physical_devices,
            key=lambda device: (
                _norm(device.name),
                _norm(device.device_type),
                device.identity,
            ),
        )
    )
