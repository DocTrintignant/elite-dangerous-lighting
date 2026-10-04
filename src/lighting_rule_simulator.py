#!/usr/bin/env python3
"""Synthetic-state diagnostics over the existing EDL rule/profile evaluator.

This module deliberately contains no matching or priority semantics of its own.
Per-rule diagnostics call ``lighting_rules.rule_matches`` and final target output
calls ``lighting_profiles.calculate_profile_outputs``. It only prepares inputs
and reports what those accepted functions decided.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from numbers import Real
from typing import Any, Iterable, Mapping

from elite_status import decode_status
from lighting_profiles import (
    NativeLightingProfile,
    ProfileDefault,
    calculate_profile_outputs,
)
from lighting_rules import HidAddress, LightingRule, rule_matches


@dataclass(frozen=True)
class RuleSimulationResult:
    index: int
    rule: LightingRule
    matched: bool
    winner: bool


@dataclass(frozen=True)
class OutputSimulationResult:
    target: str
    output: LightingRule | ProfileDefault
    rule_index: int | None


@dataclass(frozen=True)
class ProfileSimulationResult:
    state: dict[str, Any]
    rules: tuple[RuleSimulationResult, ...]
    outputs: tuple[OutputSimulationResult, ...]


def empty_canonical_state() -> dict[str, Any]:
    """Return the canonical decoder's zero-flags state, not a hand-built schema."""
    return decode_status({"Flags": 0, "Flags2": 0})


def parse_synthetic_value(text: str) -> Any:
    """Parse JSON scalars/arrays/objects; unquoted text remains a string."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("synthetic override must not be empty")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def referenced_argument_sources(profile: NativeLightingProfile) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for rule in profile.rules:
        for condition in rule.conditions:
            seen.setdefault(condition.source, None)
    return tuple(seen)


def referenced_button_addresses(profile: NativeLightingProfile) -> tuple[HidAddress, ...]:
    seen: dict[HidAddress, None] = {}
    for rule in profile.rules:
        if rule.button is not None:
            seen.setdefault((rule.button.device, rule.button.button), None)
    return tuple(seen)


def referenced_axis_addresses(profile: NativeLightingProfile) -> tuple[HidAddress, ...]:
    seen: dict[HidAddress, None] = {}
    for rule in profile.rules:
        if rule.axis is not None:
            seen.setdefault((rule.axis.device, rule.axis.axis), None)
    return tuple(seen)


def build_simulated_state(
    baseline: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = empty_canonical_state()
    if baseline is not None:
        state.update(dict(baseline))
    if overrides is not None:
        state.update(dict(overrides))
    return state


def simulate_profile(
    profile: NativeLightingProfile,
    *,
    baseline_state: Mapping[str, Any] | None = None,
    state_overrides: Mapping[str, Any] | None = None,
    pressed_keys: Iterable[str] = (),
    button_states: Mapping[HidAddress, bool] | None = None,
    axis_values: Mapping[HidAddress, Real] | None = None,
) -> ProfileSimulationResult:
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")

    state = build_simulated_state(baseline_state, state_overrides)
    pressed = tuple(pressed_keys)
    buttons = button_states or {}
    axes = axis_values or {}

    matched = tuple(
        rule_matches(
            rule,
            state,
            pressed_keys=pressed,
            button_states=buttons,
            axis_values=axes,
        )
        for rule in profile.rules
    )
    calculated = calculate_profile_outputs(
        profile,
        state,
        pressed_keys=pressed,
        button_states=buttons,
        axis_values=axes,
    )

    winner_indices: set[int] = set()
    outputs: list[OutputSimulationResult] = []
    for target, output in calculated.items():
        rule_index = None
        if isinstance(output, LightingRule):
            source_rule = output.source_rule
            for index in range(len(profile.rules) - 1, -1, -1):
                if profile.rules[index] is source_rule:
                    rule_index = index
                    winner_indices.add(index)
                    break
        outputs.append(OutputSimulationResult(target, output, rule_index))

    rule_results = tuple(
        RuleSimulationResult(
            index=index,
            rule=rule,
            matched=matched[index],
            winner=index in winner_indices,
        )
        for index, rule in enumerate(profile.rules)
    )
    return ProfileSimulationResult(state, rule_results, tuple(outputs))
