#!/usr/bin/env python3
"""Build one normalized discovery catalogue from the integrations enabled in EDL.

Discovery sources are evidence and control routes, not device identities. The
catalogue therefore resolves physical identity first where strong evidence exists
and then attaches OpenRGB/Chroma/native routes without making checkbox order part
of the result.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import csv
import io
import os
import socket
import subprocess

from lighting_govee_config import VERIFIED_SEGMENT_COUNTS
from lighting_govee_discovery import DiscoveredGoveeDevice, discover_govee_devices
from lighting_integration_preferences import (
    SOURCE_CHROMA as PREF_SOURCE_CHROMA,
    SOURCE_GOVEE as PREF_SOURCE_GOVEE,
    SOURCE_OPENRGB as PREF_SOURCE_OPENRGB,
    IntegrationPreferences,
)
from lighting_openrgb_catalog import OpenRGBPhysicalDevice, build_openrgb_physical_devices
from lighting_openrgb_discovery import OpenRGBInventory, discover_openrgb
from lighting_razer_pnp import (
    RazerPhysicalDevice,
    discover_razer_physical_devices,
    normalize_razer_product_name,
)


SOURCE_OPENRGB = PREF_SOURCE_OPENRGB
SOURCE_CHROMA = PREF_SOURCE_CHROMA
SOURCE_GOVEE = PREF_SOURCE_GOVEE


@dataclass(frozen=True)
class DiscoveryCandidate:
    identity: str
    name: str
    vendor: str
    device_type: str
    led_count: int | None
    layout: str
    sources: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    openrgb_device: OpenRGBPhysicalDevice | None = None
    chroma_target: str | None = None
    govee_device: DiscoveredGoveeDevice | None = None

    @property
    def source_label(self) -> str:
        return " + ".join(self.sources)


@dataclass(frozen=True)
class LightingDiscoveryResult:
    candidates: tuple[DiscoveryCandidate, ...]
    errors: tuple[str, ...] = ()
    openrgb_inventory: OpenRGBInventory | None = None
    available_sources: tuple[str, ...] = ()
    enabled_sources: tuple[str, ...] = ()


def normalize_display_name(name: str, vendor: str = "") -> str:
    """Normalize only user-facing vendor casing; preserve raw identity elsewhere."""
    value = str(name or "").strip()
    if str(vendor or "").strip().casefold() == "asus":
        if value[:4].casefold() == "asus":
            return "ASUS" + value[4:]
    return value


def _openrgb_layout(device: OpenRGBPhysicalDevice) -> str:
    surface = device.primary_surface
    matrix_zone = next(
        (
            zone
            for zone in surface.zones
            if zone.matrix_map is not None and int(zone.led_count) > 0
        ),
        None,
    )
    if matrix_zone is not None:
        if matrix_zone.matrix_width and matrix_zone.matrix_height:
            return f"Matrix {matrix_zone.matrix_width}×{matrix_zone.matrix_height}"
        return "Matrix"

    nonempty = sum(1 for zone in surface.zones if int(zone.led_count) > 0)
    zero_led = len(surface.zones) - nonempty
    if len(surface.zones) > 1:
        if zero_led:
            return f"{nonempty} non-empty zones · {zero_led} zero-LED"
        return f"{nonempty} zones"
    if len(surface.zones) == 1:
        zone = surface.zones[0]
        if int(zone.led_count) <= 0:
            return f"{zone.zone_type} · zero-LED"
        return zone.zone_type
    return "—"


def _openrgb_candidates(inventory: OpenRGBInventory) -> list[DiscoveryCandidate]:
    result = []
    for physical in build_openrgb_physical_devices(inventory):
        result.append(
            DiscoveryCandidate(
                identity=physical.identity,
                name=normalize_display_name(physical.name, physical.vendor),
                vendor=physical.vendor,
                device_type=physical.device_type,
                led_count=physical.led_count,
                layout=_openrgb_layout(physical),
                sources=(SOURCE_OPENRGB,),
                aliases=physical.member_identities,
                openrgb_device=physical,
            )
        )
    return result


def _richest_surface(surfaces):
    def score(surface):
        matrix_cells = 0
        matrix_zones = 0
        for zone in surface.zones:
            if zone.matrix_map is not None:
                matrix_zones += 1
                if zone.matrix_width and zone.matrix_height:
                    matrix_cells += zone.matrix_width * zone.matrix_height
        return (
            matrix_zones,
            matrix_cells,
            int(surface.led_count),
            len(surface.zones),
        )
    return max(surfaces, key=score)


def _collapse_openrgb_onto_razer_identity(
    candidates: list[DiscoveryCandidate],
    razer_devices: tuple[RazerPhysicalDevice, ...],
) -> list[DiscoveryCandidate]:
    """Use Windows ContainerId only when one physical Razer match is unambiguous."""
    result = list(candidates)

    for pnp in razer_devices:
        matching_indexes = [
            index
            for index, candidate in enumerate(result)
            if candidate.openrgb_device is not None
            and candidate.vendor.casefold() == "razer"
            and candidate.device_type.casefold() == pnp.device_type.casefold()
            and normalize_razer_product_name(candidate.name)
            == normalize_razer_product_name(pnp.name)
        ]
        if not matching_indexes:
            continue

        same_model_pnp = [
            device
            for device in razer_devices
            if device.device_type.casefold() == pnp.device_type.casefold()
            and normalize_razer_product_name(device.name)
            == normalize_razer_product_name(pnp.name)
        ]
        if len(same_model_pnp) != 1:
            continue

        matched = [result[index] for index in matching_indexes]
        surfaces = tuple(
            surface
            for candidate in matched
            for surface in candidate.openrgb_device.surfaces
        )
        primary = _richest_surface(surfaces)
        merged_openrgb = OpenRGBPhysicalDevice(
            identity=pnp.identity,
            name=pnp.name,
            device_type=pnp.device_type,
            vendor="Razer",
            surfaces=surfaces,
            primary_surface=primary,
        )
        aliases = {
            alias
            for candidate in matched
            for alias in (candidate.identity, *candidate.aliases)
        }
        merged = DiscoveryCandidate(
            identity=pnp.identity,
            name=pnp.name,
            vendor="Razer",
            device_type=pnp.device_type,
            led_count=merged_openrgb.led_count,
            layout=_openrgb_layout(merged_openrgb),
            sources=(SOURCE_OPENRGB,),
            aliases=tuple(sorted(aliases)),
            openrgb_device=merged_openrgb,
        )

        first_index = min(matching_indexes)
        result = [
            candidate
            for index, candidate in enumerate(result)
            if index not in matching_indexes
        ]
        result.insert(first_index, merged)

    return result


def _add_source(
    candidate: DiscoveryCandidate,
    source: str,
    *,
    chroma_target: str | None = None,
    aliases: tuple[str, ...] = (),
) -> DiscoveryCandidate:
    canonical = (SOURCE_OPENRGB, SOURCE_CHROMA, SOURCE_GOVEE)
    sources = tuple(
        value
        for value in canonical
        if value in set((*candidate.sources, source))
    )
    merged_aliases = tuple(sorted(set((*candidate.aliases, *aliases))))
    return replace(
        candidate,
        sources=sources,
        aliases=merged_aliases,
        chroma_target=chroma_target or candidate.chroma_target,
    )


def _windows_process_names() -> tuple[str, ...] | None:
    """Return current Windows process image names, or None if enumeration fails."""
    if os.name != "nt":
        return None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=flags,
        )
        reader = csv.reader(io.StringIO(completed.stdout))
        return tuple(
            row[0].strip()
            for row in reader
            if row and row[0].strip()
        )
    except Exception:
        return None


def razer_control_app_running(
    process_names: tuple[str, ...] | None = None,
) -> bool:
    """Check for the user-facing Razer/Synapse control process on Windows.

    Chroma SDK background services can keep the REST port open after the operator
    exits Synapse. EDL therefore treats the Razer control application as available
    only when a user-facing Synapse/AppEngine process is also present. If process
    enumeration itself fails, callers may conservatively fall back to REST health.
    """
    if os.name != "nt" and process_names is None:
        return True

    names = process_names if process_names is not None else _windows_process_names()
    if names is None:
        return True

    for name in names:
        value = str(name).strip().casefold()
        if "service" in value:
            continue
        if (
            "razerappengine" in value
            or "razer synapse" in value
            or "razersynapse" in value
        ):
            return True
    return False


def chroma_backend_available(
    host: str = "127.0.0.1",
    port: int = 54235,
    timeout: float = 0.35,
) -> bool:
    """Require both the Chroma REST endpoint and the Razer control app on Windows."""
    if not chroma_rest_available(host=host, port=port, timeout=timeout):
        return False
    return razer_control_app_running()


def chroma_rest_available(
    host: str = "127.0.0.1",
    port: int = 54235,
    timeout: float = 0.35,
) -> bool:
    """Check whether the local Chroma REST service is reachable without creating a session."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _candidate_index_by_identity(
    candidates: list[DiscoveryCandidate],
    identity: str,
) -> int | None:
    return next(
        (index for index, candidate in enumerate(candidates) if candidate.identity == identity),
        None,
    )


def add_chroma_candidates(
    candidates: list[DiscoveryCandidate],
    razer_devices: tuple[RazerPhysicalDevice, ...] = (),
) -> list[DiscoveryCandidate]:
    """Attach Chroma endpoint classes to physical Razer devices when unambiguous.

    Chroma exposes KEYBOARD/MOUSE endpoint classes, not independently addressable
    physical instances. Therefore a physical model name is used only when Windows
    proves exactly one present Razer device for that endpoint class. Otherwise the
    truthful generic Chroma surface remains.
    """
    result = list(candidates)

    for device_type, target, generic_name, layout in (
        ("Keyboard", "KEYBOARD", "Chroma keyboard", "Chroma keyboard matrix"),
        ("Mouse", "MOUSE", "Chroma mouse", "Chroma mouse surface"),
    ):
        pnp_matches = [
            device
            for device in razer_devices
            if device.device_type.casefold() == device_type.casefold()
        ]

        if len(pnp_matches) == 1:
            pnp = pnp_matches[0]
            index = _candidate_index_by_identity(result, pnp.identity)
            legacy_alias = f"chroma|{target}"
            if index is not None:
                result[index] = _add_source(
                    result[index],
                    SOURCE_CHROMA,
                    chroma_target=target,
                    aliases=(legacy_alias,),
                )
            else:
                result.append(
                    DiscoveryCandidate(
                        identity=pnp.identity,
                        name=pnp.name,
                        vendor="Razer",
                        device_type=device_type,
                        led_count=None,
                        layout=layout,
                        sources=(SOURCE_CHROMA,),
                        aliases=(legacy_alias,),
                        chroma_target=target,
                    )
                )
        else:
            result.append(
                DiscoveryCandidate(
                    identity=f"chroma|{target}",
                    name=generic_name,
                    vendor="Razer",
                    device_type=device_type,
                    led_count=None,
                    layout=layout,
                    sources=(SOURCE_CHROMA,),
                    chroma_target=target,
                )
            )

    result.append(
        DiscoveryCandidate(
            identity="chroma|CHROMALINK",
            name="Chroma-compatible lighting",
            vendor="",
            device_type="Lighting",
            led_count=None,
            layout="5 ChromaLink cells",
            sources=(SOURCE_CHROMA,),
            chroma_target="CHROMALINK",
        )
    )
    return result


def _ensure_razer_pnp_candidates(
    candidates: list[DiscoveryCandidate],
    razer_devices: tuple[RazerPhysicalDevice, ...],
) -> list[DiscoveryCandidate]:
    """Keep physically present Razer devices visible even when no backend is healthy."""
    result = list(candidates)
    represented = {candidate.identity for candidate in result}

    for device in razer_devices:
        if device.identity in represented:
            continue
        normalized_type = device.device_type.strip().casefold()
        chroma_target = (
            "KEYBOARD" if normalized_type == "keyboard"
            else "MOUSE" if normalized_type == "mouse"
            else None
        )
        layout = (
            "Chroma keyboard matrix" if chroma_target == "KEYBOARD"
            else "Chroma mouse surface" if chroma_target == "MOUSE"
            else "Razer device"
        )
        result.append(
            DiscoveryCandidate(
                identity=device.identity,
                name=device.name,
                vendor="Razer",
                device_type=device.device_type,
                led_count=None,
                layout=layout,
                sources=(),
                chroma_target=chroma_target,
            )
        )
    return result


def _add_govee_candidates(
    candidates: list[DiscoveryCandidate],
    discovered: tuple[DiscoveredGoveeDevice, ...],
) -> list[DiscoveryCandidate]:
    result = list(candidates)
    for device in discovered:
        if device.sku != "H61C3":
            continue
        result.append(
            DiscoveryCandidate(
                identity=f"govee|{device.sku}|{device.ip}",
                name=f"Govee {device.sku}",
                vendor="Govee",
                device_type="Light",
                led_count=None,
                layout=f"{VERIFIED_SEGMENT_COUNTS['H61C3']} segments",
                sources=(SOURCE_GOVEE,),
                govee_device=device,
            )
        )
    return result


def discover_enabled_candidates(
    preferences: IntegrationPreferences,
    source_order: tuple[str, ...] | None = None,
) -> LightingDiscoveryResult:
    """Discover enabled integrations and normalize them independently of UI order."""
    del source_order  # retained for API compatibility; order is no longer identity data.

    candidates: list[DiscoveryCandidate] = []
    errors: list[str] = []
    available_sources: list[str] = []
    openrgb_inventory = None

    razer_devices: tuple[RazerPhysicalDevice, ...] = ()
    if preferences.openrgb_enabled or preferences.chroma_enabled:
        try:
            razer_devices = discover_razer_physical_devices()
        except Exception as exc:
            # PnP identity enriches naming/grouping only. Discovery routes remain
            # usable if Windows identity lookup fails.
            errors.append(f"Windows Razer identity: {exc}")

    if preferences.openrgb_enabled:
        try:
            openrgb_inventory = discover_openrgb()
            available_sources.append(SOURCE_OPENRGB)
            candidates = _openrgb_candidates(openrgb_inventory)
            candidates = _collapse_openrgb_onto_razer_identity(
                candidates,
                razer_devices,
            )
        except Exception as exc:
            errors.append(f"OpenRGB: {exc}")

    if preferences.chroma_enabled:
        if chroma_backend_available():
            available_sources.append(SOURCE_CHROMA)
            candidates = add_chroma_candidates(candidates, razer_devices)
        else:
            errors.append(
                "Razer Chroma: Synapse/Chroma is not currently available. "
                "Start Razer Synapse with Chroma Apps enabled, then scan again."
            )

    if preferences.govee_h61c3_enabled:
        try:
            discovered_govee = discover_govee_devices()
            if discovered_govee:
                available_sources.append(SOURCE_GOVEE)
            candidates = _add_govee_candidates(
                candidates,
                discovered_govee,
            )
        except Exception as exc:
            errors.append(f"Enhanced Govee H61C3: {exc}")

    candidates = _ensure_razer_pnp_candidates(candidates, razer_devices)

    candidates.sort(
        key=lambda candidate: (
            candidate.name.casefold(),
            candidate.device_type.casefold(),
            candidate.identity,
        )
    )
    return LightingDiscoveryResult(
        candidates=tuple(candidates),
        errors=tuple(errors),
        openrgb_inventory=openrgb_inventory,
        available_sources=tuple(
            source
            for source in (SOURCE_OPENRGB, SOURCE_CHROMA, SOURCE_GOVEE)
            if source in available_sources
        ),
        enabled_sources=tuple(
            source
            for source, enabled in (
                (SOURCE_OPENRGB, preferences.openrgb_enabled),
                (SOURCE_CHROMA, preferences.chroma_enabled),
                (SOURCE_GOVEE, preferences.govee_h61c3_enabled),
            )
            if enabled
        ),
    )
