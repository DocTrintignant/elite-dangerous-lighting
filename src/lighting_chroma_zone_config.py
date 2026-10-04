#!/usr/bin/env python3
"""Persistent user-created zones on Razer generic Chroma surfaces.

A zone is a reusable named subset of one Razer lighting layout. ``Whole device``
is implicit for every surface and is therefore never stored as a zone.

Unlike native Govee segment ownership, Chroma authoring zones may overlap. Rule
order remains authoritative when two active outputs eventually write the same
physical light.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lighting_chroma_surfaces import CHROMA_SURFACE_BY_ID, chroma_surface
from lighting_paths import config_dir, user_data_dir

CONFIG_FORMAT = "elite-dangerous-lighting-chroma-zones"
CONFIG_VERSION = 1
CONFIG_FILENAME = "chroma_zones.json"
TARGET_PREFIX = "CHROMA_ZONE"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must contain only letters, numbers, '_' or '-'")
    return value


def slugify_zone_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(name).strip().lower()).strip("-")
    if not value:
        raise ValueError("zone name must contain at least one letter or number")
    return _validate_id(value, "zone id")


def zone_target(surface_id: str, zone_id: str) -> str:
    surface = chroma_surface(str(surface_id).strip().upper())
    return f"{TARGET_PREFIX}::{surface.surface_id}::{_validate_id(zone_id, 'zone id')}"


def parse_zone_target(target: str) -> tuple[str, str] | None:
    if not isinstance(target, str):
        return None
    parts = target.split("::")
    if len(parts) != 3 or parts[0] != TARGET_PREFIX:
        return None
    try:
        surface = chroma_surface(parts[1])
        zone = _validate_id(parts[2], "zone id")
    except ValueError:
        return None
    return surface.surface_id, zone


def is_chroma_zone_target(target: str) -> bool:
    return parse_zone_target(target) is not None


@dataclass(frozen=True)
class ChromaZone:
    surface_id: str
    zone_id: str
    name: str
    cells: tuple[str, ...]

    def __post_init__(self) -> None:
        surface_id = str(self.surface_id).strip().upper()
        surface = chroma_surface(surface_id)
        object.__setattr__(self, "surface_id", surface_id)
        _validate_id(self.zone_id, "zone id")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("zone name must be non-empty")
        object.__setattr__(self, "name", self.name.strip())
        if not isinstance(self.cells, tuple) or not self.cells:
            raise ValueError("zone must contain at least one Chroma surface cell")
        if any(not isinstance(cell, str) or not cell for cell in self.cells):
            raise ValueError("zone cells must contain non-empty cell ids")
        if len(set(self.cells)) != len(self.cells):
            raise ValueError("zone cells must not contain duplicates")
        available = set(surface.cell_ids)
        unknown = [cell for cell in self.cells if cell not in available]
        if unknown:
            raise ValueError(
                f"zone {self.name!r} contains cells not present on {surface.display_name}: {unknown}"
            )
        ordered = tuple(cell for cell in surface.cell_ids if cell in set(self.cells))
        object.__setattr__(self, "cells", ordered)

    @property
    def target(self) -> str:
        return zone_target(self.surface_id, self.zone_id)


@dataclass(frozen=True)
class ChromaZoneConfiguration:
    zones: tuple[ChromaZone, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.zones, tuple) or any(
            not isinstance(zone, ChromaZone) for zone in self.zones
        ):
            raise ValueError("zones must be a tuple of ChromaZone values")
        identities = [(zone.surface_id, zone.zone_id) for zone in self.zones]
        if len(identities) != len(set(identities)):
            raise ValueError("zone ids must be unique within each Chroma surface")

    def for_surface(self, surface_id: str) -> tuple[ChromaZone, ...]:
        normalized = str(surface_id).strip().upper()
        return tuple(zone for zone in self.zones if zone.surface_id == normalized)

    def target_map(self) -> dict[str, ChromaZone]:
        return {zone.target: zone for zone in self.zones}

    def zone_for_target(self, target: str) -> ChromaZone | None:
        return self.target_map().get(target)

    def replace_zone(self, zone: ChromaZone) -> "ChromaZoneConfiguration":
        kept = [
            value
            for value in self.zones
            if not (value.surface_id == zone.surface_id and value.zone_id == zone.zone_id)
        ]
        kept.append(zone)
        order = {surface_id: index for index, surface_id in enumerate(CHROMA_SURFACE_BY_ID)}
        kept.sort(key=lambda value: (order[value.surface_id], value.name.lower(), value.zone_id))
        return ChromaZoneConfiguration(tuple(kept))

    def delete_zone(self, surface_id: str, zone_id: str) -> "ChromaZoneConfiguration":
        surface = str(surface_id).strip().upper()
        zone = _validate_id(zone_id, "zone id")
        return ChromaZoneConfiguration(
            tuple(
                value
                for value in self.zones
                if not (value.surface_id == surface and value.zone_id == zone)
            )
        )


def friendly_target_name(configuration: ChromaZoneConfiguration, target: str) -> str:
    parsed = parse_zone_target(target)
    if parsed is None:
        return target
    surface_id, _zone_id = parsed
    zone = configuration.zone_for_target(target)
    if zone is None:
        return f"{chroma_surface(surface_id).display_name} · missing zone"
    return f"{chroma_surface(surface_id).display_name} · {zone.name}"


def default_configuration_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def legacy_configuration_path() -> Path:
    return user_data_dir() / CONFIG_FILENAME


def configuration_to_dict(configuration: ChromaZoneConfiguration) -> dict[str, Any]:
    if not isinstance(configuration, ChromaZoneConfiguration):
        raise ValueError("configuration must be ChromaZoneConfiguration")
    return {
        "format": CONFIG_FORMAT,
        "version": CONFIG_VERSION,
        "zones": [
            {
                "surface": zone.surface_id,
                "id": zone.zone_id,
                "name": zone.name,
                "cells": list(zone.cells),
            }
            for zone in configuration.zones
        ],
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


def configuration_from_dict(value: Any) -> ChromaZoneConfiguration:
    if not isinstance(value, dict):
        raise ValueError("Chroma zone configuration must be a JSON object")
    _require_exact_keys(value, {"format", "version", "zones"}, "Chroma zone configuration")
    if value["format"] != CONFIG_FORMAT:
        raise ValueError(f"unexpected Chroma zone configuration format: {value['format']!r}")
    if value["version"] != CONFIG_VERSION:
        raise ValueError(f"unsupported Chroma zone configuration version: {value['version']!r}")
    if not isinstance(value["zones"], list):
        raise ValueError("Chroma zone configuration zones must be an array")
    zones: list[ChromaZone] = []
    for index, raw in enumerate(value["zones"]):
        if not isinstance(raw, dict):
            raise ValueError(f"zone {index} must be an object")
        _require_exact_keys(raw, {"surface", "id", "name", "cells"}, f"zone {index}")
        if not isinstance(raw["cells"], list):
            raise ValueError(f"zone {index} cells must be an array")
        zones.append(
            ChromaZone(
                surface_id=raw["surface"],
                zone_id=raw["id"],
                name=raw["name"],
                cells=tuple(raw["cells"]),
            )
        )
    return ChromaZoneConfiguration(tuple(zones))


def load_chroma_zone_configuration(path: Path | None = None) -> ChromaZoneConfiguration:
    source = default_configuration_path() if path is None else Path(path)
    if path is None and not source.exists():
        legacy = legacy_configuration_path()
        if legacy.exists():
            source = legacy
    if not source.exists():
        return ChromaZoneConfiguration()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Chroma zone configuration: {source}") from exc
    return configuration_from_dict(raw)


def save_chroma_zone_configuration(
    configuration: ChromaZoneConfiguration,
    path: Path | None = None,
) -> Path:
    target = default_configuration_path() if path is None else Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(configuration_to_dict(configuration), indent=2, sort_keys=False)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target
