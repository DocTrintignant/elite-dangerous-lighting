#!/usr/bin/env python3
"""Small UI adapter for the already accepted faithful VIRPIL importer.

This module does not parse or reinterpret Link Tool data. ``virpil_import.py``
remains the only import authority. The adapter keeps that complete imported
source object alongside a normalized native working view used by the existing
rule editor. Saving the working view creates an EDL native derivative; the
foreign source file is never rewritten and remains available losslessly through
``source_profile``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from lighting_profiles import NativeLightingProfile, make_default_profile
from virpil_import import VirpilImportedProfile, load_virpil_profile


@dataclass(frozen=True)
class VirpilImportSummary:
    rules: int
    argument_rules: int
    keyboard_rules: int
    button_rules: int
    axis_rules: int
    steady_rules: int
    flashing_rules: int
    unique_targets: int


@dataclass(frozen=True)
class VirpilUiImport:
    source_path: Path
    source_profile: VirpilImportedProfile
    working_profile: NativeLightingProfile
    summary: VirpilImportSummary


def _working_name(path: Path) -> str:
    name = path.name
    suffix = ".led.json"
    stem = name[: -len(suffix)] if name.lower().endswith(suffix) else path.stem
    return f"VIRPIL import — {stem or 'profile'}"


def load_virpil_for_ui(path: str | Path) -> VirpilUiImport:
    """Load faithfully, then expose a 1:1 normalized working view for the editor."""
    source_path = Path(path)
    source_profile = load_virpil_profile(source_path)
    runtime_rules = source_profile.runtime_rules()

    # This is deliberately a view/derivative, not another importer. Every rule
    # comes directly from the accepted VirpilImportedRule.to_runtime_rule path.
    working_profile = replace(
        make_default_profile(_working_name(source_path)),
        rules=tuple(runtime.rule for runtime in runtime_rules),
    )

    types = Counter(rule.rule_type for rule in source_profile.rules)
    modes = Counter(rule.led_mode for rule in source_profile.rules)
    targets = {rule.target for rule in source_profile.rules}
    summary = VirpilImportSummary(
        rules=len(source_profile.rules),
        argument_rules=types["Argument"],
        keyboard_rules=types["Keyboard"],
        button_rules=types["Button"],
        axis_rules=types["Axis"],
        steady_rules=modes["Steady"],
        flashing_rules=modes["Flashing"],
        unique_targets=len(targets),
    )
    return VirpilUiImport(source_path, source_profile, working_profile, summary)
