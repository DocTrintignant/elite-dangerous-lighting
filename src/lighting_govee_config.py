#!/usr/bin/env python3
"""Persistent machine topology for direct/native Govee rendering.

Lighting profiles contain stable logical targets. This module maps those targets
to a configured physical Govee device and, for custom zones, to native address
entries on that device.

Every configured device implicitly owns one whole-device target:

    GOVEE_ENHANCED::<device>::ALL

That target is not stored as a fake user zone. It always means every trusted
native position on the physical device. User-created zones remain ordinary
``...::ZONE::<zone>`` leaves.

Geometry is trusted only when it is either:
- ``verified_model``: EDL has a physically accepted native mapping for the SKU;
- ``user_calibrated``: the operator physically calibrated this particular device.

Custom zones may also store one logical orientation per disconnected contiguous
section. This does not change physical/native addresses. It only tells effects
such as REACTIVE which end of each section is logically its Start/End.

Different logical zones may intentionally share physical positions. Zone order is
preserved as first-class configuration data; native rendering applies saved zones
in that order, so a later active zone wins on positions shared with an earlier
active zone.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from lighting_paths import config_dir, user_data_dir

CONFIG_FORMAT = "elite-dangerous-lighting-govee-devices"
CONFIG_VERSION = 4
CONFIG_FILENAME = "govee_devices.json"
TARGET_PREFIX = "GOVEE_ENHANCED"
MAX_NATIVE_SEGMENTS = 255

GEOMETRY_VERIFIED_MODEL = "verified_model"
GEOMETRY_USER_CALIBRATED = "user_calibrated"
TRUSTED_GEOMETRY_STATUSES = (
    GEOMETRY_VERIFIED_MODEL,
    GEOMETRY_USER_CALIBRATED,
)

SECTION_FORWARD = "FORWARD"
SECTION_REVERSED = "REVERSED"
SECTION_ORIENTATIONS = (SECTION_FORWARD, SECTION_REVERSED)

VERIFIED_SEGMENT_COUNTS = {"H61C3": 42}
SUPPORTED_SEGMENT_COUNTS = VERIFIED_SEGMENT_COUNTS

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must contain only letters, numbers, '_' or '-'")
    return value


def _validate_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"invalid Govee IPv4 address: {value!r}") from exc
    if address.version != 4 or address.is_multicast or address.is_unspecified:
        raise ValueError("Govee device address must be a unicast IPv4 address")
    return str(address)


def contiguous_segment_sections(segments: Sequence[int]) -> tuple[tuple[int, ...], ...]:
    """Split ascending physical segment addresses into disconnected sections."""
    values = tuple(segments)
    if not values:
        return ()
    sections: list[list[int]] = [[values[0]]]
    for segment in values[1:]:
        if segment == sections[-1][-1] + 1:
            sections[-1].append(segment)
        else:
            sections.append([segment])
    return tuple(tuple(section) for section in sections)


def device_id_for(sku: str, ip: str) -> str:
    normalized_sku = re.sub(r"[^A-Za-z0-9_-]+", "-", str(sku).strip().lower()).strip("-")
    normalized_ip = _validate_ip(ip).replace(".", "-")
    return _validate_id(f"{normalized_sku or 'govee'}-{normalized_ip}", "device id")


def all_target(device_id: str) -> str:
    return f"{TARGET_PREFIX}::{_validate_id(device_id, 'device id')}::ALL"


def parse_all_target(target: str) -> str | None:
    if not isinstance(target, str):
        return None
    parts = target.split("::")
    if len(parts) != 3 or parts[0] != TARGET_PREFIX or parts[2] != "ALL":
        return None
    try:
        return _validate_id(parts[1], "device id")
    except ValueError:
        return None


def zone_target(device_id: str, zone_id: str) -> str:
    return f"{TARGET_PREFIX}::{_validate_id(device_id, 'device id')}::ZONE::{_validate_id(zone_id, 'zone id')}"


def parse_zone_target(target: str) -> tuple[str, str] | None:
    if not isinstance(target, str):
        return None
    parts = target.split("::")
    if len(parts) != 4 or parts[0] != TARGET_PREFIX or parts[2] != "ZONE":
        return None
    try:
        return _validate_id(parts[1], "device id"), _validate_id(parts[3], "zone id")
    except ValueError:
        return None


def is_enhanced_govee_target(target: str) -> bool:
    return parse_all_target(target) is not None or parse_zone_target(target) is not None


@dataclass(frozen=True)
class GoveeZone:
    zone_id: str
    name: str
    segments: tuple[int, ...]
    section_orientations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_id(self.zone_id, "zone id")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("zone name must be non-empty")
        if not isinstance(self.segments, tuple) or not self.segments:
            raise ValueError("zone must contain at least one segment")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in self.segments):
            raise ValueError("zone segments must be integer segment numbers")
        if len(set(self.segments)) != len(self.segments):
            raise ValueError("zone segments must not contain duplicates")
        if tuple(sorted(self.segments)) != self.segments:
            raise ValueError("zone segments must be stored in ascending order")

        if not isinstance(self.section_orientations, tuple):
            raise ValueError("zone section orientations must be a tuple")
        normalized = tuple(str(value).upper() for value in self.section_orientations)
        if any(value not in SECTION_ORIENTATIONS for value in normalized):
            raise ValueError(
                "zone section orientations must contain only FORWARD or REVERSED"
            )
        section_count = len(contiguous_segment_sections(self.segments))
        if normalized and len(normalized) != section_count:
            raise ValueError(
                "zone section orientations must contain exactly one value per disconnected section"
            )
        object.__setattr__(self, "section_orientations", normalized)

    @property
    def sections(self) -> tuple[tuple[int, ...], ...]:
        return contiguous_segment_sections(self.segments)

    @property
    def effective_section_orientations(self) -> tuple[str, ...]:
        if self.section_orientations:
            return self.section_orientations
        return (SECTION_FORWARD,) * len(self.sections)


@dataclass(frozen=True)
class GoveeEnhancedDevice:
    device_id: str
    name: str
    sku: str
    ip: str
    zones: tuple[GoveeZone, ...]
    segment_count_override: int | None = None
    geometry_status: str | None = None
    geometry_note: str = ""

    def __post_init__(self) -> None:
        _validate_id(self.device_id, "device id")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("device name must be non-empty")
        sku = str(self.sku).strip().upper()
        if not sku:
            raise ValueError("Govee SKU must be non-empty")
        object.__setattr__(self, "sku", sku)
        object.__setattr__(self, "ip", _validate_ip(self.ip))
        if not isinstance(self.zones, tuple) or any(not isinstance(zone, GoveeZone) for zone in self.zones):
            raise ValueError("device zones must be a tuple of GoveeZone values")

        count = self.segment_count_override
        status = self.geometry_status
        verified_count = VERIFIED_SEGMENT_COUNTS.get(sku)
        if count is None:
            if verified_count is None:
                raise ValueError(
                    f"Govee {sku!r} has no verified model geometry; calibrate this device before using native zones"
                )
            count = verified_count
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError("native segment count must be an integer")
        if count < 1 or count > MAX_NATIVE_SEGMENTS:
            raise ValueError(f"native segment count must be 1..{MAX_NATIVE_SEGMENTS}")

        if status is None:
            if verified_count == count:
                status = GEOMETRY_VERIFIED_MODEL
            else:
                raise ValueError("unverified native geometry must be explicitly user-calibrated")
        if status not in TRUSTED_GEOMETRY_STATUSES:
            raise ValueError(f"unsupported Govee geometry status: {status!r}")
        if status == GEOMETRY_VERIFIED_MODEL and verified_count != count:
            raise ValueError(
                f"Govee {sku} verified geometry must use {verified_count!r} native segments, not {count}"
            )
        if status == GEOMETRY_USER_CALIBRATED and not str(self.geometry_note).strip():
            raise ValueError("user-calibrated geometry must include a calibration note")

        object.__setattr__(self, "segment_count_override", count)
        object.__setattr__(self, "geometry_status", status)
        object.__setattr__(self, "geometry_note", str(self.geometry_note).strip())

        ids = [zone.zone_id for zone in self.zones]
        if len(set(ids)) != len(ids):
            raise ValueError("zone ids must be unique within a device")

        # Logical zones may overlap intentionally. Keep only per-zone geometry
        # validation here; saved order is preserved and the renderer applies later
        # active zones last on any shared physical positions.
        for zone in self.zones:
            for segment in zone.segments:
                if segment < 1 or segment > count:
                    raise ValueError(
                        f"zone {zone.name!r} contains segment {segment}; this {sku} geometry supports 1..{count}"
                    )

    @property
    def segment_count(self) -> int:
        assert self.segment_count_override is not None
        return self.segment_count_override

    @property
    def geometry_is_model_verified(self) -> bool:
        return self.geometry_status == GEOMETRY_VERIFIED_MODEL

    @property
    def geometry_is_user_calibrated(self) -> bool:
        return self.geometry_status == GEOMETRY_USER_CALIBRATED

    @property
    def all_target(self) -> str:
        return all_target(self.device_id)

    def target_for_zone(self, zone: GoveeZone) -> str:
        return zone_target(self.device_id, zone.zone_id)


GoveeTargetEntry = tuple[GoveeEnhancedDevice, GoveeZone | None]


@dataclass(frozen=True)
class GoveeConfiguration:
    devices: tuple[GoveeEnhancedDevice, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.devices, tuple) or any(
            not isinstance(device, GoveeEnhancedDevice) for device in self.devices
        ):
            raise ValueError("devices must be a tuple of GoveeEnhancedDevice values")
        ids = [device.device_id for device in self.devices]
        if len(set(ids)) != len(ids):
            raise ValueError("enhanced Govee device ids must be unique")
        physical = [(device.sku, device.ip) for device in self.devices]
        if len(set(physical)) != len(physical):
            raise ValueError("the same physical Govee SKU/address cannot be configured twice")


    def target_map(self) -> dict[str, GoveeTargetEntry]:
        result: dict[str, GoveeTargetEntry] = {}
        for device in self.devices:
            result[device.all_target] = (device, None)
            for zone in device.zones:
                result[device.target_for_zone(zone)] = (device, zone)
        return result


def default_configuration_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def legacy_configuration_path() -> Path:
    return user_data_dir() / CONFIG_FILENAME


def make_h61c3_device(
    *,
    device_id: str = "h61c3-desk",
    name: str = "Desk H61C3",
    ip: str = "192.168.0.22",
    zones: tuple[GoveeZone, ...] = (),
) -> GoveeEnhancedDevice:
    return GoveeEnhancedDevice(
        device_id=device_id,
        name=name,
        sku="H61C3",
        ip=ip,
        zones=zones,
        segment_count_override=VERIFIED_SEGMENT_COUNTS["H61C3"],
        geometry_status=GEOMETRY_VERIFIED_MODEL,
    )


def make_calibrated_device(
    *,
    sku: str,
    ip: str,
    segment_count: int,
    device_id: str | None = None,
    name: str | None = None,
    zones: tuple[GoveeZone, ...] = (),
    geometry_note: str = "Operator physically accepted native sweep calibration",
) -> GoveeEnhancedDevice:
    normalized_sku = str(sku).strip().upper()
    return GoveeEnhancedDevice(
        device_id=device_id or device_id_for(normalized_sku, ip),
        name=name or f"Govee {normalized_sku}",
        sku=normalized_sku,
        ip=ip,
        zones=zones,
        segment_count_override=segment_count,
        geometry_status=GEOMETRY_USER_CALIBRATED,
        geometry_note=geometry_note,
    )




def with_discovered_verified_devices(
    configuration: GoveeConfiguration,
    discovered: Sequence[object],
) -> GoveeConfiguration:
    """Add ephemeral runtime devices for physically verified discovered models.

    Persisted configuration is an optional overlay for user naming, zones or
    calibration. Basic support for a verified model must not depend on that file
    existing. Discovery supplies only the current machine binding (SKU/IP); the
    immutable geometry comes from EDL's verified model table.
    """
    if not isinstance(configuration, GoveeConfiguration):
        raise ValueError("configuration must be GoveeConfiguration")

    devices = list(configuration.devices)
    used_ids = {device.device_id for device in devices}
    known_physical = {(device.sku.upper(), device.ip) for device in devices}

    for claim in discovered:
        sku = str(getattr(claim, "sku", "") or "").strip().upper()
        ip = str(getattr(claim, "ip", "") or "").strip()
        if sku not in VERIFIED_SEGMENT_COUNTS or not ip:
            continue
        physical = (sku, _validate_ip(ip))
        if physical in known_physical:
            continue

        preferred_id = "h61c3-desk" if sku == "H61C3" else device_id_for(sku, ip)
        if preferred_id in used_ids:
            preferred_id = device_id_for(sku, ip)

        if sku == "H61C3":
            device = make_h61c3_device(
                device_id=preferred_id,
                name="Govee H61C3",
                ip=ip,
                zones=(),
            )
        else:
            device = GoveeEnhancedDevice(
                device_id=preferred_id,
                name=f"Govee {sku}",
                sku=sku,
                ip=ip,
                zones=(),
                segment_count_override=VERIFIED_SEGMENT_COUNTS[sku],
                geometry_status=GEOMETRY_VERIFIED_MODEL,
            )

        devices.append(device)
        used_ids.add(device.device_id)
        known_physical.add(physical)

    return GoveeConfiguration(tuple(devices))

def _zone_to_dict(zone: GoveeZone) -> dict[str, Any]:
    return {
        "id": zone.zone_id,
        "name": zone.name,
        "segments": list(zone.segments),
        "section_orientations": list(zone.section_orientations),
    }


def _device_to_dict(device: GoveeEnhancedDevice) -> dict[str, Any]:
    return {
        "id": device.device_id,
        "name": device.name,
        "sku": device.sku,
        "ip": device.ip,
        "segment_count": device.segment_count,
        "geometry_status": device.geometry_status,
        "geometry_note": device.geometry_note,
        "zones": [_zone_to_dict(zone) for zone in device.zones],
    }


def configuration_to_dict(configuration: GoveeConfiguration) -> dict[str, Any]:
    if not isinstance(configuration, GoveeConfiguration):
        raise ValueError("configuration must be GoveeConfiguration")
    return {
        "format": CONFIG_FORMAT,
        "version": CONFIG_VERSION,
        "devices": [_device_to_dict(device) for device in configuration.devices],
    }


def _require_exact_keys(value: Mapping[str, Any], required: set[str], label: str) -> None:
    if set(value) != required:
        missing = required - set(value)
        unknown = set(value) - required
        detail = []
        if missing:
            detail.append(f"missing {sorted(missing)}")
        if unknown:
            detail.append(f"unknown {sorted(unknown)}")
        raise ValueError(f"{label} fields invalid: {'; '.join(detail)}")


def _zones_from_raw(
    raw_zones: Any,
    device_index: int,
    version: int,
) -> tuple[GoveeZone, ...]:
    if not isinstance(raw_zones, list):
        raise ValueError(f"device {device_index} zones must be an array")
    zones: list[GoveeZone] = []
    for zone_index, raw_zone in enumerate(raw_zones):
        if not isinstance(raw_zone, dict):
            raise ValueError(f"device {device_index} zone {zone_index} must be an object")
        label = f"device {device_index} zone {zone_index}"
        if version >= 3:
            _require_exact_keys(
                raw_zone,
                {"id", "name", "segments", "section_orientations"},
                label,
            )
            orientations = raw_zone["section_orientations"]
            if not isinstance(orientations, list):
                raise ValueError(f"{label} section_orientations must be an array")
        else:
            _require_exact_keys(raw_zone, {"id", "name", "segments"}, label)
            orientations = []
        if not isinstance(raw_zone["segments"], list):
            raise ValueError(f"{label} segments must be an array")
        zones.append(
            GoveeZone(
                zone_id=raw_zone["id"],
                name=raw_zone["name"],
                segments=tuple(raw_zone["segments"]),
                section_orientations=tuple(orientations),
            )
        )
    return tuple(zones)


def configuration_from_dict(value: Any) -> GoveeConfiguration:
    if not isinstance(value, dict):
        raise ValueError("Govee configuration must be a JSON object")
    _require_exact_keys(value, {"format", "version", "devices"}, "Govee configuration")
    if value["format"] != CONFIG_FORMAT:
        raise ValueError(f"unexpected Govee configuration format: {value['format']!r}")
    version = value["version"]
    if version not in (1, 2, 3, CONFIG_VERSION):
        raise ValueError(f"unsupported Govee configuration version: {version!r}")
    if not isinstance(value["devices"], list):
        raise ValueError("Govee configuration devices must be an array")

    devices: list[GoveeEnhancedDevice] = []
    for device_index, raw_device in enumerate(value["devices"]):
        if not isinstance(raw_device, dict):
            raise ValueError(f"device {device_index} must be an object")
        if version == 1:
            _require_exact_keys(
                raw_device,
                {"id", "name", "sku", "ip", "enabled", "zones"},
                f"device {device_index}",
            )
            sku = str(raw_device["sku"]).strip().upper()
            if sku not in VERIFIED_SEGMENT_COUNTS:
                raise ValueError(
                    f"v1 configuration contains {sku!r}, which has no verified migration geometry"
                )
            count = VERIFIED_SEGMENT_COUNTS[sku]
            status = GEOMETRY_VERIFIED_MODEL
            note = ""
        else:
            required = {
                "id", "name", "sku", "ip", "segment_count",
                "geometry_status", "geometry_note", "zones",
            }
            if version <= 3:
                required.add("enabled")
            _require_exact_keys(
                raw_device,
                required,
                f"device {device_index}",
            )
            sku = raw_device["sku"]
            count = raw_device["segment_count"]
            status = raw_device["geometry_status"]
            note = raw_device["geometry_note"]

        if version <= 3 and not isinstance(raw_device["enabled"], bool):
            raise ValueError("device enabled must be boolean")

        devices.append(
            GoveeEnhancedDevice(
                device_id=raw_device["id"],
                name=raw_device["name"],
                sku=sku,
                ip=raw_device["ip"],
                zones=_zones_from_raw(raw_device["zones"], device_index, version),
                segment_count_override=count,
                geometry_status=status,
                geometry_note=note,
            )
        )
    return GoveeConfiguration(tuple(devices))


def load_govee_configuration(path: Path | None = None) -> GoveeConfiguration:
    config_path = default_configuration_path() if path is None else Path(path)
    if path is None and not config_path.exists():
        legacy = legacy_configuration_path()
        if legacy.exists():
            config_path = legacy
    if not config_path.exists():
        return GoveeConfiguration()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Govee configuration {config_path}: {exc}") from exc
    return configuration_from_dict(raw)


def save_govee_configuration(
    configuration: GoveeConfiguration,
    path: Path | None = None,
) -> Path:
    config_path = default_configuration_path() if path is None else Path(path)
    payload = configuration_to_dict(configuration)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(temporary, config_path)
    return config_path


def friendly_target_name(configuration: GoveeConfiguration, target: str) -> str:
    entry = configuration.target_map().get(target)
    if entry is None:
        return target
    device, zone = entry
    return f"{device.name} · {'ALL' if zone is None else zone.name}"
