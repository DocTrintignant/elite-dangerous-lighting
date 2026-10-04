#!/usr/bin/env python3
"""Current machine/device availability for profile authoring and validation.

This module is deliberately read-only with respect to hardware. It combines the
latest discovery result with the operator's persisted device selections and the
existing logical target configurations. Saved profile targets remain untouched;
availability only decides whether a target is currently authorable/runnable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from lighting_chroma_zone_config import (
    ChromaZoneConfiguration,
    friendly_target_name as chroma_zone_friendly_name,
    parse_zone_target as parse_chroma_zone_target,
)
from lighting_chromalink_cells import CHROMALINK_CELL_TARGETS, is_chromalink_target
from lighting_discovery_catalog import (
    SOURCE_CHROMA,
    SOURCE_GOVEE,
    SOURCE_OPENRGB,
    DiscoveryCandidate,
    LightingDiscoveryResult,
)
from lighting_govee_config import (
    GoveeConfiguration,
    parse_all_target as parse_govee_all_target,
    parse_zone_target as parse_govee_zone_target,
)
from lighting_openrgb_targets import (
    bindings_for_physical as openrgb_bindings_for_physical,
    fallback_target_label as openrgb_fallback_target_label,
    parse_openrgb_target,
)
from lighting_profiles import NativeLightingProfile, ProfileDefault


AVAILABLE = "AVAILABLE"
NOT_DETECTED = "NOT_DETECTED"
NOT_SELECTED = "NOT_SELECTED"
ROUTE_UNAVAILABLE = "ROUTE_UNAVAILABLE"
ROUTE_DISABLED = "ROUTE_DISABLED"
OWNER_MISMATCH = "OWNER_MISMATCH"
UNKNOWN_TARGET = "UNKNOWN_TARGET"


@dataclass(frozen=True)
class TargetAvailability:
    target: str
    available: bool
    reason: str
    label: str
    detail: str = ""


@dataclass(frozen=True)
class ProfileTargetIssue:
    rule_index: int
    rule_name: str
    output_index: int
    target: str
    label: str
    reason: str
    detail: str
    output_enabled: bool


@dataclass(frozen=True)
class DeviceAvailabilitySnapshot:
    discovery: LightingDiscoveryResult
    selected_ids: frozenset[str]
    govee_configuration: GoveeConfiguration = GoveeConfiguration()
    chroma_zone_configuration: ChromaZoneConfiguration = ChromaZoneConfiguration()
    control_routes: dict[str, str] = field(default_factory=dict)

    def _candidate_aliases(self, candidate: DiscoveryCandidate) -> set[str]:
        aliases = set(candidate.aliases)
        physical = candidate.openrgb_device
        if physical is not None:
            aliases.update(physical.member_identities)
        return aliases

    def candidate_selected(self, candidate: DiscoveryCandidate) -> bool:
        return (
            candidate.identity in self.selected_ids
            or bool(self._candidate_aliases(candidate) & self.selected_ids)
        )

    def configured_route(self, candidate: DiscoveryCandidate) -> str | None:
        for identity in (candidate.identity, *self._candidate_aliases(candidate)):
            route = self.control_routes.get(identity)
            if route:
                return route
        return None

    def _owner_allows(self, candidate: DiscoveryCandidate, route: str) -> bool:
        owner = self.configured_route(candidate)
        if owner is None:
            return len(candidate.sources) <= 1
        return owner == route

    def _route_enabled(self, route: str) -> bool:
        if self.discovery.enabled_sources or self.discovery.available_sources:
            return route in set(self.discovery.enabled_sources)

        # Compatibility for manually constructed/legacy discovery snapshots:
        # these snapshots predate explicit enabled/disabled backend metadata.
        # Absence of metadata must therefore mean "enabled state unknown", not
        # "explicitly disabled in EDL". Availability is decided separately from
        # candidate source evidence by _route_available().
        return True

    def _route_available(self, route: str) -> bool:
        if self.discovery.enabled_sources or self.discovery.available_sources:
            return route in set(self.discovery.available_sources)
        # Compatibility fallback when health metadata predates these fields.
        return any(route in candidate.sources for candidate in self.discovery.candidates)

    def _chroma_target_status(self, surface: str) -> TargetAvailability:
        normalized = str(surface).strip().upper()
        exact = [
            candidate
            for candidate in self.discovery.candidates
            if candidate.chroma_target == normalized
        ]
        if exact:
            candidate = exact[0]
            label = candidate.name or normalized.title()
            if not self.candidate_selected(candidate):
                return TargetAvailability(
                    normalized,
                    False,
                    NOT_SELECTED,
                    label,
                    "Device is detected but not selected in EDL.",
                )

            owner = self.configured_route(candidate)
            if owner is not None and owner != SOURCE_CHROMA:
                return TargetAvailability(
                    normalized,
                    False,
                    OWNER_MISMATCH,
                    label,
                    f"Control via is set to {owner}.",
                )
            if owner is None and len(candidate.sources) > 1:
                return TargetAvailability(
                    normalized,
                    False,
                    ROUTE_UNAVAILABLE,
                    label,
                    "Chroma unavailable until Control via is confirmed in Lighting devices.",
                )
            if not self._route_enabled(SOURCE_CHROMA):
                return TargetAvailability(
                    normalized,
                    False,
                    ROUTE_DISABLED,
                    label,
                    "Chroma disabled in EDL.",
                )
            if not self._route_available(SOURCE_CHROMA) or SOURCE_CHROMA not in candidate.sources:
                return TargetAvailability(
                    normalized,
                    False,
                    ROUTE_UNAVAILABLE,
                    label,
                    "Chroma unavailable: Razer Synapse/Chroma is not currently reachable.",
                )
            return TargetAvailability(
                normalized,
                True,
                AVAILABLE,
                label,
                "Detected, selected and owned by Razer Chroma.",
            )

        if normalized in {"KEYBOARD", "MOUSE"}:
            witnesses = [
                candidate
                for candidate in self.discovery.candidates
                if candidate.vendor.casefold() == "razer"
                and candidate.device_type.casefold() == normalized.casefold()
            ]
            if witnesses:
                selected = [candidate for candidate in witnesses if self.candidate_selected(candidate)]
                label = (selected or witnesses)[0].name
                if selected:
                    candidate = selected[0]
                    owner = self.configured_route(candidate)
                    if owner is not None and owner != SOURCE_CHROMA:
                        return TargetAvailability(
                            normalized,
                            False,
                            OWNER_MISMATCH,
                            label,
                            f"Control via is set to {owner}.",
                        )
                    if owner is None and len(candidate.sources) > 1:
                        return TargetAvailability(
                            normalized,
                            False,
                            ROUTE_UNAVAILABLE,
                            label,
                            "Chroma unavailable until Control via is confirmed in Lighting devices.",
                        )
                    if not self._route_enabled(SOURCE_CHROMA):
                        return TargetAvailability(
                            normalized,
                            False,
                            ROUTE_DISABLED,
                            label,
                            "Chroma disabled in EDL.",
                        )
                    return TargetAvailability(
                        normalized,
                        False,
                        ROUTE_UNAVAILABLE,
                        label,
                        "Chroma unavailable: Razer Synapse/Chroma is not currently reachable.",
                    )
                return TargetAvailability(
                    normalized,
                    False,
                    NOT_SELECTED,
                    label,
                    "Device is detected but not selected in EDL.",
                )
            return TargetAvailability(
                normalized,
                False,
                NOT_DETECTED,
                normalized.title(),
                "No matching Razer device is currently detected.",
            )

        return TargetAvailability(
            normalized,
            False,
            ROUTE_UNAVAILABLE,
            "Chroma-compatible lighting",
            "The generic ChromaLink endpoint is not currently available.",
        )

    def _govee_device(self, device_id: str):
        return next(
            (
                device
                for device in self.govee_configuration.devices
                if device.device_id == device_id
            ),
            None,
        )

    def _govee_status(self, device_id: str, *, target: str) -> TargetAvailability:
        device = self._govee_device(device_id)
        if device is None:
            return TargetAvailability(
                target,
                False,
                UNKNOWN_TARGET,
                target,
                "The saved Enhanced Govee target no longer resolves to a configured device.",
            )

        label = f"Govee {device.sku} · {device.name}"
        candidate = next(
            (
                value
                for value in self.discovery.candidates
                if value.govee_device is not None
                and value.govee_device.sku.upper() == device.sku.upper()
                and value.govee_device.ip == device.ip
            ),
            None,
        )
        if candidate is None:
            return TargetAvailability(
                target,
                False,
                NOT_DETECTED,
                label,
                "The configured Govee device is not currently detected on the LAN.",
            )
        if not self.candidate_selected(candidate):
            return TargetAvailability(
                target,
                False,
                NOT_SELECTED,
                label,
                "Device is detected but not selected in EDL.",
            )
        owner = self.configured_route(candidate)
        if owner is not None and owner != SOURCE_GOVEE:
            return TargetAvailability(
                target,
                False,
                OWNER_MISMATCH,
                label,
                f"Control via is set to {owner}.",
            )
        if owner is None and len(candidate.sources) > 1:
            return TargetAvailability(
                target,
                False,
                ROUTE_UNAVAILABLE,
                label,
                "Enhanced Govee unavailable until Control via is confirmed in Lighting devices.",
            )
        if not self._route_enabled(SOURCE_GOVEE):
            return TargetAvailability(
                target,
                False,
                ROUTE_DISABLED,
                label,
                "Enhanced Govee is disabled in EDL integrations.",
            )
        if not self._route_available(SOURCE_GOVEE) or SOURCE_GOVEE not in candidate.sources:
            return TargetAvailability(
                target,
                False,
                ROUTE_UNAVAILABLE,
                label,
                "Enhanced Govee is not currently reachable on the LAN.",
            )
        return TargetAvailability(
            target,
            True,
            AVAILABLE,
            label,
            "Detected, selected and owned by Enhanced Govee.",
        )

    def _openrgb_status(self, target: str) -> TargetAvailability:
        spec = parse_openrgb_target(target)
        if spec is None:
            return TargetAvailability(
                target,
                False,
                UNKNOWN_TARGET,
                target,
                "The saved OpenRGB target identifier is invalid.",
            )

        physical_candidates = [
            candidate
            for candidate in self.discovery.candidates
            if (
                candidate.identity == spec.physical_identity
                or spec.physical_identity in candidate.aliases
                or (
                    candidate.openrgb_device is not None
                    and (
                        spec.physical_identity in candidate.openrgb_device.member_identities
                        or candidate.openrgb_device.identity == spec.physical_identity
                    )
                )
            )
        ]
        if not physical_candidates:
            return TargetAvailability(
                target,
                False,
                NOT_DETECTED,
                openrgb_fallback_target_label(target),
                "The saved OpenRGB device is not currently detected.",
            )

        candidate = physical_candidates[0]
        if candidate.openrgb_device is not None:
            bindings = {
                binding.target: binding
                for binding in openrgb_bindings_for_physical(candidate.openrgb_device)
            }
        else:
            bindings = {}
        binding = bindings.get(target)
        label = (
            binding.label
            if binding is not None
            else candidate.name or openrgb_fallback_target_label(target)
        )

        if not self.candidate_selected(candidate):
            return TargetAvailability(
                target,
                False,
                NOT_SELECTED,
                label,
                "Device is detected but not selected in EDL.",
            )

        owner = self.configured_route(candidate)
        if owner is not None and owner != SOURCE_OPENRGB:
            return TargetAvailability(
                target,
                False,
                OWNER_MISMATCH,
                label,
                f"Control via is set to {owner}.",
            )
        if owner is None and len(candidate.sources) > 1:
            return TargetAvailability(
                target,
                False,
                ROUTE_UNAVAILABLE,
                label,
                "OpenRGB unavailable until Control via is confirmed in Lighting devices.",
            )
        if not self._route_enabled(SOURCE_OPENRGB):
            return TargetAvailability(
                target,
                False,
                ROUTE_DISABLED,
                label,
                "OpenRGB disabled in EDL.",
            )
        if not self._route_available(SOURCE_OPENRGB) or SOURCE_OPENRGB not in candidate.sources:
            return TargetAvailability(
                target,
                False,
                ROUTE_UNAVAILABLE,
                label,
                "OpenRGB unavailable: the OpenRGB SDK server is not currently reachable.",
            )
        if binding is None:
            return TargetAvailability(
                target,
                False,
                UNKNOWN_TARGET,
                label,
                "The saved OpenRGB surface or zone is no longer exposed by the current device topology.",
            )
        return TargetAvailability(
            target,
            True,
            AVAILABLE,
            label,
            "Detected, selected and owned by OpenRGB.",
        )

    def selected_available_candidates(self) -> tuple[tuple[DiscoveryCandidate, str], ...]:
        """Return every selector row EDL has promised it can control.

        Policy is provider-agnostic: selected + available backend + resolved
        Control via means Start Lighting must acquire the device. Rule presence
        is deliberately irrelevant.
        """
        result: list[tuple[DiscoveryCandidate, str]] = []
        for candidate in self.discovery.candidates:
            if not self.candidate_selected(candidate):
                continue

            owner = self.configured_route(candidate)
            if owner is None:
                if len(candidate.sources) != 1:
                    continue
                owner = candidate.sources[0]

            if owner not in candidate.sources:
                continue
            if not self._route_enabled(owner) or not self._route_available(owner):
                continue
            result.append((candidate, owner))
        return tuple(result)

    def available_runtime_targets(self) -> frozenset[str]:
        """Expand selected+Available selector rows into renderer-owned targets.

        Provider-specific expansion is transport plumbing only. Unknown future
        providers fail explicitly rather than silently reverting to Rule-owned
        devices.
        """
        targets: set[str] = set()

        for candidate, owner in self.selected_available_candidates():
            if owner == SOURCE_CHROMA:
                surface = str(candidate.chroma_target or "").strip().upper()
                if not surface:
                    raise RuntimeError(
                        f"{candidate.name} is selected and Available through {owner}, "
                        "but EDL has no Chroma runtime target for it."
                    )
                targets.add(surface)
                if surface in {"KEYBOARD", "MOUSE"}:
                    targets.update(
                        zone.target
                        for zone in self.chroma_zone_configuration.for_surface(surface)
                    )
                elif surface == "CHROMALINK":
                    targets.update(CHROMALINK_CELL_TARGETS)
                continue

            if owner == SOURCE_GOVEE:
                discovered = candidate.govee_device
                if discovered is None:
                    raise RuntimeError(
                        f"{candidate.name} is selected and Available through {owner}, "
                        "but EDL has no Govee runtime record for it."
                    )
                device = next(
                    (
                        value
                        for value in self.govee_configuration.devices
                        if value.sku.upper() == discovered.sku.upper()
                        and value.ip == discovered.ip
                    ),
                    None,
                )
                if device is None:
                    # Discovery/selection of one incomplete provider must not
                    # invalidate unrelated valid devices. Verified Govee models
                    # are normally bootstrapped during discovery; if that did
                    # not happen, omit only this device from live ownership.
                    continue
                targets.add(device.all_target)
                targets.update(device.target_for_zone(zone) for zone in device.zones)
                continue

            if owner == SOURCE_OPENRGB:
                physical = candidate.openrgb_device
                if physical is None:
                    raise RuntimeError(
                        f"{candidate.name} is selected and Available through {owner}, "
                        "but EDL has no OpenRGB topology for it."
                    )
                bindings = openrgb_bindings_for_physical(physical)
                if not bindings:
                    raise RuntimeError(
                        f"{candidate.name} is selected and Available, but exposes no "
                        "renderable OpenRGB target."
                    )
                targets.update(binding.target for binding in bindings)
                continue

            raise RuntimeError(
                f"{candidate.name} is selected and Available through {owner}, but "
                "that integration has no live EDL ownership adapter."
            )

        return frozenset(targets)

    def status(self, target: str) -> TargetAvailability:
        value = str(target or "").strip()
        normalized = value.upper()
        if not value:
            return TargetAvailability(
                value,
                False,
                UNKNOWN_TARGET,
                value,
                "Target is empty.",
            )
        if normalized == "GLOBAL":
            return TargetAvailability(
                value,
                True,
                AVAILABLE,
                "GLOBAL",
                "Default state for lighting outputs without a target-specific state.",
            )
        if normalized in {"KEYBOARD", "MOUSE", "CHROMALINK"}:
            return self._chroma_target_status(normalized)
        if is_chromalink_target(value):
            base = self._chroma_target_status("CHROMALINK")
            return replace(base, target=value)

        chroma_zone = parse_chroma_zone_target(value)
        if chroma_zone is not None:
            surface_id, _zone_id = chroma_zone
            if value not in self.chroma_zone_configuration.target_map():
                return TargetAvailability(
                    value,
                    False,
                    UNKNOWN_TARGET,
                    value,
                    "The saved Chroma zone no longer exists in this machine's zone configuration.",
                )
            base = self._chroma_target_status(surface_id)
            zone = self.chroma_zone_configuration.zone_for_target(value)
            zone_name = zone.name if zone is not None else "missing zone"
            surface_label = str(base.label or "").strip()
            return replace(
                base,
                target=value,
                label=(
                    f"{surface_label} · {zone_name}"
                    if surface_label
                    else chroma_zone_friendly_name(self.chroma_zone_configuration, value)
                ),
            )

        govee_device_id = parse_govee_all_target(value)
        if govee_device_id is not None:
            if value not in self.govee_configuration.target_map():
                return TargetAvailability(
                    value,
                    False,
                    UNKNOWN_TARGET,
                    value,
                    "The saved Enhanced Govee whole-device target is no longer configured.",
                )
            return self._govee_status(govee_device_id, target=value)

        govee_zone = parse_govee_zone_target(value)
        if govee_zone is not None:
            device_id, _zone_id = govee_zone
            entry = self.govee_configuration.target_map().get(value)
            if entry is None:
                return TargetAvailability(
                    value,
                    False,
                    UNKNOWN_TARGET,
                    value,
                    "The saved Enhanced Govee zone no longer exists in this machine's configuration.",
                )
            device, zone = entry
            base = self._govee_status(device_id, target=value)
            zone_name = zone.name if zone is not None else "Whole device"
            return replace(base, label=f"Govee {device.sku} · {device.name} · {zone_name}")

        if value.startswith("GOVEE_DEVICE::"):
            device_id = value.split("::", 1)[1]
            return self._govee_status(device_id, target=value)

        if parse_openrgb_target(value) is not None:
            return self._openrgb_status(value)

        return TargetAvailability(
            value,
            False,
            UNKNOWN_TARGET,
            value,
            "The saved target is preserved, but EDL cannot currently resolve it to an available device.",
        )


def profile_target_issues(
    profile: NativeLightingProfile,
    snapshot: DeviceAvailabilitySnapshot,
) -> tuple[ProfileTargetIssue, ...]:
    issues: list[ProfileTargetIssue] = []
    for rule_index, rule in enumerate(profile.rules):
        rule_name = rule.name or f"Rule {rule_index + 1}"
        for output_index, output in enumerate(rule.outputs):
            status = snapshot.status(output.target)
            if status.available:
                continue
            issues.append(
                ProfileTargetIssue(
                    rule_index=rule_index,
                    rule_name=rule_name,
                    output_index=output_index,
                    target=output.target,
                    label=status.label,
                    reason=status.reason,
                    detail=status.detail,
                    output_enabled=output.enabled,
                )
            )
    return tuple(issues)


def profile_acquisition_targets(profile: NativeLightingProfile) -> tuple[str, ...]:
    """Return the ordered non-GLOBAL targets the runner may physically acquire."""
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")
    return tuple(
        dict.fromkeys(
            (
                output.target
                for rule in profile.rules
                if rule.enabled
                for output in rule.outputs
                if output.enabled and output.target != "GLOBAL"
            )
        )
    ) + tuple(
        target
        for target in dict.fromkeys(
            default.target
            for default in profile.defaults
            if default.target != "GLOBAL"
        )
        if target not in {
            output.target
            for rule in profile.rules
            if rule.enabled
            for output in rule.outputs
            if output.enabled and output.target != "GLOBAL"
        }
    )


def filter_profile_for_runtime(
    profile: NativeLightingProfile,
    snapshot: DeviceAvailabilitySnapshot,
) -> NativeLightingProfile:
    """Return an ephemeral runtime copy containing only available acquisitions.

    The persisted/editor profile is never mutated. Unavailable enabled rule
    outputs are disabled. Unavailable explicit defaults are omitted because a
    ProfileDefault has no enabled flag. If that would leave no default at all,
    an inert GLOBAL black fallback keeps the native profile invariant intact.
    """
    changed = False
    rules = []
    for rule in profile.rules:
        outputs = []
        for output in rule.outputs:
            if output.enabled and not snapshot.status(output.target).available:
                outputs.append(replace(output, enabled=False))
                changed = True
            else:
                outputs.append(output)
        output_tuple = tuple(outputs)
        derived = replace(rule, outputs=output_tuple) if output_tuple != rule.outputs else rule
        if derived.enabled and not any(output.enabled for output in output_tuple):
            derived = replace(derived, enabled=False)
            changed = True
        rules.append(derived)

    defaults = tuple(
        default
        for default in profile.defaults
        if default.target == "GLOBAL" or snapshot.status(default.target).available
    )
    if defaults != profile.defaults:
        changed = True
    if not defaults:
        defaults = (ProfileDefault("GLOBAL", (0, 0, 0)),)
        changed = True

    return (
        replace(profile, rules=tuple(rules), defaults=defaults)
        if changed
        else profile
    )
