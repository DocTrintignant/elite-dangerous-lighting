#!/usr/bin/env python3
"""Shared immutable profile shaping for lighting sessions.

This module owns no transport and no UI. It only ensures that temporary/live
sessions acquire the physical owner required by requested logical targets while
preserving the existing calculated profile underneath.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from lighting_chroma_zone_config import parse_zone_target as parse_chroma_zone_target
from lighting_chromalink_cells import physical_chroma_target
from lighting_govee_config import (
    all_target as govee_all_target,
    parse_all_target as parse_govee_all_target,
    parse_zone_target as parse_govee_zone_target,
)
from lighting_profiles import NativeLightingProfile, ProfileDefault
from lighting_rules import LightingRule, RuleOutput


def physical_owner_target(target: str) -> str:
    """Return the logical owner target whose renderer acquires one leaf."""
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string")
    value = target.strip()

    chroma_zone = parse_chroma_zone_target(value)
    if chroma_zone is not None:
        return chroma_zone[0]

    chromalink = physical_chroma_target(value)
    if chromalink == "CHROMALINK":
        return chromalink

    govee_device = parse_govee_all_target(value)
    if govee_device is not None:
        return govee_all_target(govee_device)
    govee_zone = parse_govee_zone_target(value)
    if govee_zone is not None:
        return govee_all_target(govee_zone[0])

    normalized = value.upper()
    if normalized in {"KEYBOARD", "MOUSE", "CHROMALINK"}:
        return normalized

    # Stable OpenRGB leaves and future already-routable concrete targets own
    # themselves unless a backend-specific owner mapping is explicitly defined.
    return value


def augment_profile_for_targets(
    profile: NativeLightingProfile,
    targets: Iterable[str],
) -> NativeLightingProfile:
    """Acquire missing physical owners without altering existing calculated leaves."""
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")

    requested = tuple(dict.fromkeys(str(target) for target in targets))
    if any(not target.strip() for target in requested):
        raise ValueError("targets must contain only non-empty strings")
    if not requested:
        return profile

    profile_targets = {
        output.target
        for rule in profile.rules
        if rule.enabled
        for output in rule.outputs
        if output.enabled and output.target != "GLOBAL"
    }
    profile_targets.update(
        default.target
        for default in profile.defaults
        if default.target != "GLOBAL"
    )
    existing_owners = {physical_owner_target(target) for target in profile_targets}
    requested_owners = tuple(
        dict.fromkeys(physical_owner_target(target) for target in requested)
    )
    missing_owners = tuple(
        owner for owner in requested_owners if owner not in existing_owners
    )
    if not missing_owners:
        return profile

    global_default = next(
        (default for default in profile.defaults if default.target == "GLOBAL"),
        None,
    )
    additions = []
    for owner in missing_owners:
        if global_default is not None:
            additions.append(replace(global_default, target=owner))
        else:
            additions.append(ProfileDefault(owner, (0, 0, 0)))

    return replace(profile, defaults=(*profile.defaults, *additions))



def _rule_with_outputs(
    rule: LightingRule,
    outputs: tuple[RuleOutput, ...],
) -> LightingRule:
    if not outputs:
        raise ValueError("scoped session rule requires at least one output")
    first = outputs[0]
    derived = LightingRule(
        conditions=rule.conditions,
        colour=first.colour,
        enabled=rule.enabled,
        effect=first.effect,
        target=first.target,
        name=rule.name,
        keyboard=rule.keyboard,
        button=rule.button,
        axis=rule.axis,
        colours=first.colours,
        effect_parameters=first.effect_parameters,
        targets=tuple(output.target for output in outputs),
        outputs=outputs,
    )
    object.__setattr__(derived, "_source_rule", rule.source_rule)
    return derived


def profile_for_target_owners(
    profile: NativeLightingProfile,
    targets: Iterable[str],
) -> NativeLightingProfile:
    """Keep only calculated state relevant to the requested physical owners."""
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")

    requested = tuple(dict.fromkeys(str(target) for target in targets))
    if any(not target.strip() for target in requested):
        raise ValueError("targets must contain only non-empty strings")
    if not requested:
        return profile

    owners = {physical_owner_target(target) for target in requested}
    rules = []
    for rule in profile.rules:
        outputs = tuple(
            output
            for output in rule.outputs
            if output.target == "GLOBAL"
            or physical_owner_target(output.target) in owners
        )
        if outputs:
            rules.append(_rule_with_outputs(rule, outputs))

    defaults = tuple(
        default
        for default in profile.defaults
        if default.target == "GLOBAL"
        or physical_owner_target(default.target) in owners
    )
    if not defaults:
        defaults = (ProfileDefault("GLOBAL", (0, 0, 0)),)

    if tuple(rules) == profile.rules and defaults == profile.defaults:
        return profile
    return replace(profile, rules=tuple(rules), defaults=defaults)
