#!/usr/bin/env python3
"""Stable OpenRGB rule targets derived from live physical-device metadata.

OpenRGB SDK controller indexes are process-local and must never enter profiles.
Targets therefore persist the catalogue's physical identity plus, where needed,
the exact OpenRGB surface identity and zone name.

Current product policy:
- renderer ownership is decided by the persisted per-physical-device route choice;
  Razer is therefore authorable through OpenRGB only when OpenRGB owns it.
- monitors are not exposed through OpenRGB; PG32UCDP stays excluded/off.
- one-surface devices with one non-empty zone use a whole-device target.
- multi-zone devices expose each non-empty zone as an independent leaf.
- zero-length zones are setup/topology state and are never authorable/renderable.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

from lighting_device_selection import openrgb_device_identity
from lighting_discovery_catalog import (
    SOURCE_OPENRGB,
    DiscoveryCandidate,
    LightingDiscoveryResult,
)
from lighting_openrgb_catalog import OpenRGBPhysicalDevice
from lighting_openrgb_discovery import OpenRGBDevice, OpenRGBInventory, OpenRGBZone


DEVICE_PREFIX = "OPENRGB_DEVICE::"
ZONE_PREFIX = "OPENRGB_ZONE::"


def _encode(value: str) -> str:
    return quote(str(value), safe="")


def _decode(value: str) -> str:
    return unquote(str(value))


@dataclass(frozen=True)
class OpenRGBTargetSpec:
    target: str
    physical_identity: str
    surface_identity: str
    zone_name: str | None = None

    @property
    def whole_device(self) -> bool:
        return self.zone_name is None


@dataclass(frozen=True)
class OpenRGBTargetBinding:
    spec: OpenRGBTargetSpec
    label: str
    physical: OpenRGBPhysicalDevice
    surface: OpenRGBDevice
    zone_index: int | None
    zone: OpenRGBZone | None

    @property
    def target(self) -> str:
        return self.spec.target

    @property
    def led_count(self) -> int:
        return int(self.surface.led_count if self.zone is None else self.zone.led_count)


def make_device_target(physical_identity: str, surface_identity: str) -> str:
    return f"{DEVICE_PREFIX}{_encode(physical_identity)}::{_encode(surface_identity)}"


def make_zone_target(
    physical_identity: str,
    surface_identity: str,
    zone_name: str,
) -> str:
    return (
        f"{ZONE_PREFIX}{_encode(physical_identity)}::"
        f"{_encode(surface_identity)}::{_encode(zone_name)}"
    )


def parse_openrgb_target(target: str) -> OpenRGBTargetSpec | None:
    value = str(target or "")
    if value.startswith(DEVICE_PREFIX):
        parts = value[len(DEVICE_PREFIX):].split("::")
        if len(parts) != 2 or not all(parts):
            return None
        physical_identity, surface_identity = map(_decode, parts)
        return OpenRGBTargetSpec(
            target=value,
            physical_identity=physical_identity,
            surface_identity=surface_identity,
        )
    if value.startswith(ZONE_PREFIX):
        parts = value[len(ZONE_PREFIX):].split("::")
        if len(parts) != 3 or not all(parts):
            return None
        physical_identity, surface_identity, zone_name = map(_decode, parts)
        return OpenRGBTargetSpec(
            target=value,
            physical_identity=physical_identity,
            surface_identity=surface_identity,
            zone_name=zone_name,
        )
    return None


def is_openrgb_target(target: str) -> bool:
    return parse_openrgb_target(target) is not None


def _eligible_physical(physical: OpenRGBPhysicalDevice) -> bool:
    """The selector, not device type, decides whether EDL may own a device."""
    return True


def _surface_label(physical: OpenRGBPhysicalDevice, surface: OpenRGBDevice) -> str:
    if len(physical.surfaces) == 1:
        return physical.name
    return f"{physical.name} · {surface.name}"


def bindings_for_physical(
    physical: OpenRGBPhysicalDevice,
) -> tuple[OpenRGBTargetBinding, ...]:
    if not _eligible_physical(physical):
        return ()

    bindings: list[OpenRGBTargetBinding] = []
    for surface in physical.surfaces:
        surface_identity = openrgb_device_identity(surface)
        nonempty = tuple(
            (index, zone)
            for index, zone in enumerate(surface.zones)
            if int(zone.led_count) > 0
        )
        if int(surface.led_count) <= 0:
            continue

        # A simple one-zone controller is truthfully represented as one physical
        # device target. Multi-zone controllers expose real zone leaves instead.
        if len(surface.zones) <= 1 and len(nonempty) == 1:
            target = make_device_target(physical.identity, surface_identity)
            spec = OpenRGBTargetSpec(
                target,
                physical.identity,
                surface_identity,
            )
            bindings.append(
                OpenRGBTargetBinding(
                    spec=spec,
                    label=_surface_label(physical, surface),
                    physical=physical,
                    surface=surface,
                    zone_index=None,
                    zone=None,
                )
            )
            continue

        for zone_index, zone in nonempty:
            target = make_zone_target(
                physical.identity,
                surface_identity,
                zone.name,
            )
            spec = OpenRGBTargetSpec(
                target,
                physical.identity,
                surface_identity,
                zone.name,
            )
            bindings.append(
                OpenRGBTargetBinding(
                    spec=spec,
                    label=f"{_surface_label(physical, surface)} · {zone.name}",
                    physical=physical,
                    surface=surface,
                    zone_index=zone_index,
                    zone=zone,
                )
            )

    return tuple(bindings)


def bindings_from_inventory(
    inventory: OpenRGBInventory,
) -> dict[str, OpenRGBTargetBinding]:
    from lighting_openrgb_catalog import build_openrgb_physical_devices

    result: dict[str, OpenRGBTargetBinding] = {}
    for physical in build_openrgb_physical_devices(inventory):
        for binding in bindings_for_physical(physical):
            result[binding.target] = binding
    return result


def _candidate_aliases(candidate: DiscoveryCandidate) -> set[str]:
    aliases = set(candidate.aliases)
    if candidate.openrgb_device is not None:
        aliases.update(candidate.openrgb_device.member_identities)
    return aliases


def _configured_route_for_candidate(
    candidate: DiscoveryCandidate,
    control_routes: dict[str, str] | None,
) -> str | None:
    routes = control_routes or {}
    for identity in (candidate.identity, *_candidate_aliases(candidate)):
        route = routes.get(identity)
        if route:
            return route
    return None


def candidate_selected(
    candidate: DiscoveryCandidate,
    selected_ids: set[str] | frozenset[str],
) -> bool:
    aliases = _candidate_aliases(candidate)
    return candidate.identity in selected_ids or bool(aliases & set(selected_ids))


def discovery_target_bindings(
    discovery: LightingDiscoveryResult,
    selected_ids: set[str] | frozenset[str],
    *,
    selected_only: bool,
    control_routes: dict[str, str] | None = None,
) -> dict[str, OpenRGBTargetBinding]:
    result: dict[str, OpenRGBTargetBinding] = {}
    for candidate in discovery.candidates:
        physical = candidate.openrgb_device
        if (
            physical is None
            or SOURCE_OPENRGB not in candidate.sources
            or not _eligible_physical(physical)
        ):
            continue

        configured = _configured_route_for_candidate(candidate, control_routes)
        if configured is not None and configured != SOURCE_OPENRGB:
            continue

        # A currently overlapping physical device is not authorable through
        # either backend until the operator confirms Control via. Single-route
        # devices need no persisted owner.
        if configured is None and len(candidate.sources) > 1:
            continue

        if selected_only and not candidate_selected(candidate, selected_ids):
            continue
        for binding in bindings_for_physical(physical):
            result[binding.target] = binding
    return result


def openrgb_target_device_label(target: str) -> str:
    """Return a stable human device label encoded in one OpenRGB target."""
    spec = parse_openrgb_target(target)
    if spec is None:
        return "OpenRGB device"

    parts = spec.surface_identity.split("|", 4)
    if len(parts) >= 4 and parts[0] == "openrgb":
        name = parts[2].strip()
        if name:
            return name

    physical = spec.physical_identity.split("|")
    if len(physical) >= 4 and physical[0] == "openrgb-physical":
        device_type = physical[3].strip()
        if device_type:
            return f"OpenRGB {device_type.title()}"
    return "OpenRGB device"


def fallback_target_label(target: str) -> str:
    spec = parse_openrgb_target(target)
    if spec is None:
        return str(target)
    device_hint = openrgb_target_device_label(target)

    if spec.zone_name is not None:
        return f"{device_hint} · {spec.zone_name}"
    return f"{device_hint} · Whole device"
