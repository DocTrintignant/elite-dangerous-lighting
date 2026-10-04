#!/usr/bin/env python3
"""Backend-neutral temporary Preview execution over the normal EDL live runner.

Rule Preview is represented as one immutable ProfileDefault. Mode Preview reuses
the current applied base profile, augments only missing physical owners, and
installs the Mode through the existing Scene authority after all renderers have
been acquired. No preview-specific renderer or transport exists here.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Mapping, Sequence

from lighting_effect_config import EffectParameters
from lighting_govee_config import GoveeConfiguration
from lighting_mode_runtime import start_lighting_mode
from lighting_modes import LightingMode
from lighting_profile_runner import run_profile
from lighting_profiles import NativeLightingProfile, ProfileCalculatedOutput, ProfileDefault
from lighting_rules import LightingRule
from lighting_scene_authority import SceneAuthorityController, scene_authority
from lighting_session_profile import (
    augment_profile_for_targets,
    profile_for_target_owners,
)

RGB = tuple[int, int, int]
ProfileRunner = Callable[..., None]
ProfileFilter = Callable[[NativeLightingProfile], NativeLightingProfile]


def _output_preview_targets(target: str | Sequence[str]) -> tuple[str, ...]:
    values = (target,) if isinstance(target, str) else tuple(target)
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Preview requires one or more non-empty lighting targets")
    normalized = tuple(value.strip() for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError("Preview lighting targets must not contain duplicates")
    return normalized


def preview_profile_for_output(
    target: str | Sequence[str],
    effect: str,
    colours: Sequence[RGB],
    parameters: EffectParameters,
) -> NativeLightingProfile:
    palette = tuple(colours)
    if not palette:
        raise ValueError("Preview requires at least one colour")
    defaults = tuple(
        ProfileDefault(
            target=value,
            colour=palette[0],
            effect=effect,
            colours=palette,
            effect_parameters=parameters,
        )
        for value in _output_preview_targets(target)
    )
    return NativeLightingProfile(
        name="EDL Preview",
        rules=(),
        defaults=defaults,
        reset_before_start=False,
    )


def run_output_preview(
    target: str | Sequence[str],
    effect: str,
    colours: Sequence[RGB],
    parameters: EffectParameters,
    stop_event: threading.Event,
    *,
    runner: ProfileRunner = run_profile,
) -> None:
    """Preview one output through the exact normal renderer/device-router stack."""
    profile = preview_profile_for_output(target, effect, colours, parameters)
    runner(profile, stop_event)



def preview_profile_for_calculated_outputs(
    outputs: Mapping[str, ProfileCalculatedOutput],
) -> NativeLightingProfile:
    """Freeze already-calculated profile outputs into one temporary preview profile.

    The rule engine decides the hypothetical result before this boundary. This
    adapter only preserves each resolved target's effect/palette/parameters as a
    ProfileDefault so the normal runner can render it continuously.
    """
    if not isinstance(outputs, Mapping):
        raise ValueError("calculated preview outputs must be a mapping")

    defaults = []
    for target, output in outputs.items():
        if not isinstance(target, str) or not target.strip():
            raise ValueError("calculated preview target must be a non-empty string")
        if not isinstance(output, (LightingRule, ProfileDefault)):
            raise ValueError(
                f"unsupported calculated preview output for {target!r}: "
                f"{type(output).__name__}"
            )
        defaults.append(
            ProfileDefault(
                target=target,
                colour=output.colour,
                effect=output.effect,
                colours=output.colours,
                effect_parameters=output.effect_parameters,
            )
        )

    if not defaults:
        defaults.append(ProfileDefault("GLOBAL", (0, 0, 0)))

    return NativeLightingProfile(
        name="EDL Situation Test",
        rules=(),
        defaults=tuple(defaults),
        reset_before_start=False,
    )


def run_calculated_preview(
    outputs: Mapping[str, ProfileCalculatedOutput],
    stop_event: threading.Event,
    *,
    ownership_targets: Sequence[str] = (),
    runner: ProfileRunner = run_profile,
    profile_filter: ProfileFilter | None = None,
    govee_configuration: GoveeConfiguration | None = None,
) -> None:
    """Render a frozen hypothetical result through the normal live runner."""
    if not isinstance(stop_event, threading.Event):
        raise ValueError("stop_event must be threading.Event")

    profile = preview_profile_for_calculated_outputs(outputs)
    if profile_filter is not None:
        if not callable(profile_filter):
            raise ValueError("profile_filter must be callable or None")
        profile = profile_filter(profile)

    kwargs = {"ownership_targets": tuple(ownership_targets)}
    if govee_configuration is not None:
        kwargs["govee_configuration"] = govee_configuration
    runner(profile, stop_event, **kwargs)


def preview_profile_for_rule(
    rule: LightingRule,
    *,
    ownership_targets: Sequence[str] | None = None,
) -> NativeLightingProfile:
    """Build one temporary real-Rule profile with black target-local fallbacks.

    The Rule itself is not simplified: its source conditions, HID bindings,
    controller response, enabled state, outputs and effect data are preserved.
    Only the rest of the user's profile is omitted.
    """
    if not isinstance(rule, LightingRule):
        raise ValueError("Rule test requires one LightingRule")

    enabled_targets = tuple(
        dict.fromkeys(
            output.target
            for output in rule.outputs
            if output.enabled
        )
    )
    if enabled_targets == ("GLOBAL",):
        targets = tuple(
            dict.fromkeys(
                str(target)
                for target in (ownership_targets or ())
                if str(target).strip() and str(target).upper() != "GLOBAL"
            )
        )
        if not targets:
            raise ValueError(
                "All selected lights needs at least one selected and available lighting device."
            )
        defaults = (ProfileDefault("GLOBAL", (0, 0, 0)),)
    else:
        targets = tuple(target for target in enabled_targets if target != "GLOBAL")
        if not targets:
            raise ValueError("Rule test requires at least one enabled lighting output")
        defaults = tuple(ProfileDefault(target, (0, 0, 0)) for target in targets)

    return NativeLightingProfile(
        name="EDL Rule Test",
        rules=(rule,),
        defaults=defaults,
        reset_before_start=False,
    )


def run_rule_test(
    rule: LightingRule,
    stop_event: threading.Event,
    *,
    status_path: Path | str | None = None,
    ownership_targets: Sequence[str] | None = None,
    runner: ProfileRunner = run_profile,
    govee_configuration: GoveeConfiguration | None = None,
) -> None:
    """Run exactly one Rule through the normal live source/HID/renderer path."""
    profile = preview_profile_for_rule(rule, ownership_targets=ownership_targets)
    kwargs = {"status_path": status_path} if status_path is not None else {}
    if ownership_targets is not None:
        kwargs["ownership_targets"] = tuple(ownership_targets)
    if govee_configuration is not None:
        kwargs["govee_configuration"] = govee_configuration
    runner(profile, stop_event, **kwargs)


def run_mode_preview(
    base_profile: NativeLightingProfile,
    mode: LightingMode,
    stop_event: threading.Event,
    *,
    status_path: Path | str | None = None,
    runner: ProfileRunner = run_profile,
    controller: SceneAuthorityController = scene_authority,
    profile_filter: ProfileFilter | None = None,
    govee_configuration: GoveeConfiguration | None = None,
) -> None:
    """Preview one Mode on a temporary normal session and restore on completion."""
    if not isinstance(base_profile, NativeLightingProfile):
        raise ValueError("base_profile must be NativeLightingProfile")
    if not isinstance(mode, LightingMode):
        raise ValueError("mode must be LightingMode")
    if not isinstance(stop_event, threading.Event):
        raise ValueError("stop_event must be threading.Event")

    scoped_profile = profile_for_target_owners(base_profile, mode.targets)
    runtime_profile = augment_profile_for_targets(scoped_profile, mode.targets)
    if profile_filter is not None:
        if not callable(profile_filter):
            raise ValueError("profile_filter must be callable or None")
        runtime_profile = profile_filter(runtime_profile)

    installed = None
    timer: threading.Timer | None = None

    def session_ready() -> None:
        nonlocal installed, timer
        installed = start_lighting_mode(mode, controller=controller)
        timer = threading.Timer(mode.duration_seconds, stop_event.set)
        timer.daemon = True
        timer.start()

    try:
        kwargs = {"status_path": status_path} if status_path is not None else {}
        if govee_configuration is not None:
            kwargs["govee_configuration"] = govee_configuration
        runner(
            runtime_profile,
            stop_event,
            on_session_ready=session_ready,
            **kwargs,
        )
    finally:
        if timer is not None:
            timer.cancel()
        active = controller.active_scene()
        if installed is not None and active is installed:
            controller.stop()
