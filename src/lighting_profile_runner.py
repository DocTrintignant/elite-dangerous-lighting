#!/usr/bin/env python3
"""Run one applied native profile against live Elite/keyboard/HID state.

Existing ordered base-rule calculation, persistent Chroma lifecycle and native
Govee transport remain unchanged. Continuous effects are sampled at the renderer
boundary. REACTIVE/RIPPLE are evaluated as source transitions and composited as
temporary per-event overlays over the continuously recalculated underlying state.

Independent rule outputs keep their target-local effect data through the same
render paths. Physical ownership is derived only from enabled outputs, and
order-sensitive composition resolves target-local views back to their original
source rule.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable

from elite_status_watch import EliteStatusWatcher, default_status_path
from keyboard_input import KeyboardInputAdapter
from lighting_axis_modulation import brightness_for_axis, colour_for_axis
from lighting_chroma_live import LiveChromaSession
from lighting_chroma_surface_effects import (
    compose_surface_matrices,
    frame_to_surface_matrix,
    surface_cell_points,
    uniform_surface_matrix,
)
from lighting_chroma_zone_config import (
    is_chroma_zone_target,
    load_chroma_zone_configuration,
)
from lighting_chromalink_cells import (
    CHROMALINK_CELL_TARGETS,
    is_chromalink_cell_target,
    physical_chroma_target,
)
from lighting_chromalink_render import BLACK, render_chromalink_cells
from lighting_effect_runtime_frames import (
    is_addressable_continuous_effect,
    is_continuous_frame_effect,
    sample_persisted_effect_frame,
)
from lighting_govee_config import GoveeConfiguration, load_govee_configuration
from lighting_govee_render import EnhancedGoveeRenderer, compose_device_target_frames
from lighting_hid_state import HidStateAdapter
from lighting_intent import intent_from_rule
from lighting_openrgb_render import OpenRGBRenderer
from lighting_openrgb_targets import is_openrgb_target
from lighting_profiles import NativeLightingProfile, ProfileDefault, calculate_profile_outputs
from lighting_render_rate import frame_period_seconds
from lighting_rules import ArgumentCondition, LightingRule, RuleOutput
from lighting_runtime import RuntimeIntentSample, render_runtime_tick
from lighting_trigger_production import (
    any_active_for_targets,
    chromalink_target_masks,
    govee_target_masks,
    linear_points,
    overlay_chroma_surface_matrix,
    overlay_targeted_frame,
    split_trigger_profile,
)
from lighting_trigger_runtime import TriggerEffectRuntime

SUPPORTED_TARGETS = ("KEYBOARD", "MOUSE", "CHROMALINK")
SUPPORTED_LOGICAL_TARGETS = (*SUPPORTED_TARGETS, *CHROMALINK_CELL_TARGETS)
CHROMALINK_POINTS = tuple((index / 4.0, 0.5) for index in range(5))

def _attach_cleanup_failures(
    error: BaseException,
    cleanup_errors: tuple[Exception, ...] | list[Exception],
    *,
    prefix: str = "Cleanup failure",
) -> None:
    """Preserve the initiating error while retaining every cleanup diagnostic."""
    for index, cleanup_error in enumerate(cleanup_errors, start=1):
        error.add_note(
            f"{prefix} {index}: "
            f"{type(cleanup_error).__name__}: {cleanup_error}"
        )


def format_run_error(error: BaseException) -> str:
    """Return the operator-visible runtime error plus attached cleanup diagnostics."""
    lines = [str(error)]
    lines.extend(str(note) for note in (getattr(error, "__notes__", None) or ()))
    return "\n".join(line for line in lines if line)



def profile_uses_elite_status(profile: NativeLightingProfile) -> bool:
    """Return whether any enabled Rule reads canonical Elite state."""
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")
    return any(rule.enabled and bool(rule.conditions) for rule in profile.rules)


def _watched_keys(profile: NativeLightingProfile) -> tuple[str, ...]:
    keys: list[str] = []
    for rule in profile.rules:
        if rule.keyboard is not None:
            keys.extend(rule.keyboard.keys)
    return tuple(dict.fromkeys(key.upper() for key in keys))


def _watched_buttons(profile: NativeLightingProfile) -> tuple[tuple[str, int], ...]:
    values = []
    for rule in profile.rules:
        if rule.button is not None:
            values.append((rule.button.device, rule.button.button))
    return tuple(dict.fromkeys(values))


def _watched_axes(profile: NativeLightingProfile) -> tuple[tuple[str, int], ...]:
    values = []
    for rule in profile.rules:
        if rule.axis is not None:
            values.append((rule.axis.device, rule.axis.axis))
        for output in rule.outputs:
            if output.axis_modulation is not None:
                values.append(
                    (output.axis_modulation.device, output.axis_modulation.axis)
                )
    return tuple(dict.fromkeys(values))


def _as_rule(output, target: str) -> LightingRule:
    if isinstance(output, LightingRule):
        if output.targets == (target,):
            return output
        return LightingRule(
            name=output.name,
            enabled=output.enabled,
            conditions=output.conditions,
            keyboard=output.keyboard,
            button=output.button,
            axis=output.axis,
            colour=output.colour,
            colours=output.colours,
            effect=output.effect,
            effect_parameters=output.effect_parameters,
            axis_modulation=output.axis_modulation,
            target=target,
        )
    if isinstance(output, ProfileDefault):
        return LightingRule(
            name="Profile fallback",
            conditions=(ArgumentCondition("__ProfileFallback__", "Equal", True),),
            colour=output.colour,
            colours=output.colours,
            effect=output.effect,
            effect_parameters=output.effect_parameters,
            target=target,
        )
    raise ValueError(f"unsupported calculated output {type(output).__name__}")




def _modulated_output(
    output,
    target: str,
    axis_values,
):
    """Resolve one live axis binding without changing rule matching or source order."""
    if not isinstance(output, LightingRule):
        return output
    rule = _as_rule(output, target)
    modulation = rule.axis_modulation
    if modulation is None:
        return output

    observed = axis_values.get((modulation.device, modulation.axis))
    colour = colour_for_axis(rule.colour, modulation, observed)
    palette = (colour,) if modulation.colour_enabled else rule.colours
    parameters = rule.effect_parameters
    brightness = brightness_for_axis(modulation, observed)
    if brightness is not None:
        parameters = replace(parameters, brightness=brightness)

    effective = LightingRule(
        name=rule.name,
        enabled=rule.enabled,
        conditions=rule.conditions,
        keyboard=rule.keyboard,
        button=rule.button,
        axis=rule.axis,
        colour=colour,
        colours=palette,
        effect=rule.effect,
        effect_parameters=parameters,
        axis_modulation=None,
        target=target,
    )
    object.__setattr__(effective, "_source_rule", rule.source_rule)
    return effective

def _runtime_sample(output, target: str, elapsed_seconds: float) -> RuntimeIntentSample:
    intent = intent_from_rule(_as_rule(output, target))
    if intent.effect == "STATIC":
        return RuntimeIntentSample(intent)
    return RuntimeIntentSample(intent, elapsed_seconds=elapsed_seconds)


def _output_effect(output, target: str) -> str:
    return _as_rule(output, target).effect


def _uniform_rgb(output, target: str, elapsed_seconds: float) -> tuple[int, int, int]:
    rule = _as_rule(output, target)
    if is_continuous_frame_effect(rule.effect):
        frame = sample_persisted_effect_frame(
            rule.effect,
            ((0.0, 0.0),),
            elapsed_seconds,
            rule.colours,
            rule.effect_parameters,
        )
        return frame[0]
    return render_runtime_tick((_runtime_sample(output, target, elapsed_seconds),))[target]


def _surface_matrix(output, target: str, surface_id: str, elapsed_seconds: float):
    rule = _as_rule(output, target)
    if is_addressable_continuous_effect(rule.effect):
        frame = sample_persisted_effect_frame(
            rule.effect,
            surface_cell_points(surface_id),
            elapsed_seconds,
            rule.colours,
            rule.effect_parameters,
        )
        return frame_to_surface_matrix(surface_id, frame)
    return uniform_surface_matrix(
        surface_id,
        _uniform_rgb(output, target, elapsed_seconds),
    )


def _linear_frame(
    output,
    target: str,
    count: int,
    elapsed_seconds: float,
    *,
    y: float = 0.0,
):
    rule = _as_rule(output, target)
    if count <= 0:
        raise ValueError("linear effect surface requires at least one position")
    points = linear_points(count, y=y)
    if is_continuous_frame_effect(rule.effect):
        return sample_persisted_effect_frame(
            rule.effect,
            points,
            elapsed_seconds,
            rule.colours,
            rule.effect_parameters,
        )
    rgb = _uniform_rgb(output, target, elapsed_seconds)
    return tuple(rgb for _index in range(count))


def _point_rgb(output, target: str, point, elapsed_seconds: float):
    rule = _as_rule(output, target)
    if is_continuous_frame_effect(rule.effect):
        return sample_persisted_effect_frame(
            rule.effect,
            (point,),
            elapsed_seconds,
            rule.colours,
            rule.effect_parameters,
        )[0]
    return _uniform_rgb(output, target, elapsed_seconds)


def _uses_chromalink_cell_addressing(profile: NativeLightingProfile) -> bool:
    return any(
        is_chromalink_cell_target(output.target)
        for rule in profile.rules
        if rule.enabled
        for output in rule.outputs
        if output.enabled
    ) or any(
        is_chromalink_cell_target(default.target)
        for default in profile.defaults
    )


def _derived_rule_with_outputs(
    rule: LightingRule,
    outputs: tuple[RuleOutput, ...],
) -> LightingRule:
    if not outputs:
        raise ValueError("derived runner rule requires at least one output")
    first = outputs[0]
    derived = LightingRule(
        name=rule.name,
        enabled=rule.enabled,
        conditions=rule.conditions,
        keyboard=rule.keyboard,
        button=rule.button,
        axis=rule.axis,
        colour=first.colour,
        colours=first.colours,
        effect=first.effect,
        effect_parameters=first.effect_parameters,
        axis_modulation=first.axis_modulation,
        target=first.target,
        targets=tuple(output.target for output in outputs),
        outputs=outputs,
    )
    object.__setattr__(derived, "_source_rule", rule.source_rule)
    return derived


def _expand_whole_chromalink_rules(profile: NativeLightingProfile) -> NativeLightingProfile:
    """Treat enabled CHROMALINK outputs as ordered writes to all five cells."""
    changed = False
    expanded_rules: list[LightingRule] = []
    for rule in profile.rules:
        if not any(
            output.enabled and output.target == "CHROMALINK"
            for output in rule.outputs
        ):
            expanded_rules.append(rule)
            continue

        expanded_by_target: dict[str, RuleOutput] = {}
        target_order: list[str] = []
        for output in rule.outputs:
            targets = (
                CHROMALINK_CELL_TARGETS
                if output.enabled and output.target == "CHROMALINK"
                else (output.target,)
            )
            for target in targets:
                concrete = replace(output, target=target)
                if target in expanded_by_target:
                    target_order.remove(target)
                expanded_by_target[target] = concrete
                target_order.append(target)

        expanded_outputs = tuple(expanded_by_target[target] for target in target_order)
        expanded_rules.append(_derived_rule_with_outputs(rule, expanded_outputs))
        changed = True

    if not changed:
        return profile
    return replace(profile, rules=tuple(expanded_rules))


def _profile_targets(profile: NativeLightingProfile) -> set[str]:
    values = {
        output.target
        for rule in profile.rules
        if rule.enabled
        for output in rule.outputs
        if output.enabled and output.target != "GLOBAL"
    }
    values.update(default.target for default in profile.defaults if default.target != "GLOBAL")
    return values


def _output_priority(output, rule_order: dict[int, int]) -> int:
    if isinstance(output, LightingRule):
        return rule_order.get(id(output.source_rule), -1)
    return -1


def run_profile(
    profile: NativeLightingProfile,
    stop_event: threading.Event,
    *,
    on_session_ready: Callable[[], None] | None = None,
    ownership_targets: Iterable[str] | None = None,
    status_path: Path | str | None = None,
    govee_configuration: GoveeConfiguration | None = None,
) -> None:
    """Continuously run one immutable applied profile until stopped."""
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")
    if not isinstance(stop_event, threading.Event):
        raise ValueError("stop_event must be threading.Event")
    if on_session_ready is not None and not callable(on_session_ready):
        raise ValueError("on_session_ready must be callable or None")

    base_profile, trigger_rules = split_trigger_profile(profile)
    trigger_runtime = TriggerEffectRuntime(trigger_rules) if trigger_rules else None

    if govee_configuration is None:
        govee_configuration = load_govee_configuration()
    elif not isinstance(govee_configuration, GoveeConfiguration):
        raise ValueError("govee_configuration must be GoveeConfiguration or None")
    chroma_zone_configuration = load_chroma_zone_configuration()
    configured_govee_targets = set(govee_configuration.target_map())

    configured_govee_map = govee_configuration.target_map()
    configured_chroma_zone_targets = set(chroma_zone_configuration.target_map())
    supported_targets = (
        set(SUPPORTED_LOGICAL_TARGETS)
        | configured_govee_targets
        | configured_chroma_zone_targets
    )

    # Rules calculate base state; the Lighting devices selector decides which
    # available hardware EDL owns for this live run.
    profile_targets = _profile_targets(profile)
    live_targets = set(profile_targets)
    if ownership_targets is not None:
        live_targets.update(str(target) for target in ownership_targets)

    requested_openrgb_targets = {
        target for target in live_targets if is_openrgb_target(target)
    }
    supported_targets.update(requested_openrgb_targets)
    missing_chroma_zones = sorted(
        target
        for target in live_targets
        if is_chroma_zone_target(target) and target not in configured_chroma_zone_targets
    )
    if missing_chroma_zones:
        raise ValueError(
            "These saved Chroma zones no longer exist on this computer: "
            + ", ".join(missing_chroma_zones)
        )

    unsupported = sorted(target for target in live_targets if target not in supported_targets)
    if unsupported:
        raise ValueError(
            "Start lighting cannot render these target(s) on this computer: "
            + ", ".join(unsupported)
        )

    requested_govee_targets = live_targets & configured_govee_targets
    requested_chroma_zone_targets = live_targets & configured_chroma_zone_targets
    requested_chroma_logical_targets = live_targets & set(SUPPORTED_LOGICAL_TARGETS)
    requested_chroma_zone_surfaces = {
        chroma_zone_configuration.target_map()[target].surface_id
        for target in requested_chroma_zone_targets
    }
    if (
        not requested_chroma_logical_targets
        and not requested_chroma_zone_targets
        and not requested_govee_targets
        and not requested_openrgb_targets
    ):
        raise ValueError("This profile has no applied physical lighting targets to run yet.")

    resolved_status_path = (
        Path(status_path) if status_path is not None else default_status_path()
    )
    watcher = (
        EliteStatusWatcher(resolved_status_path)
        if resolved_status_path is not None
        else None
    )
    state: dict[str, object] = {}
    if watcher is not None:
        first = watcher.poll_once()
        if first is not None:
            state = dict(first.state)

    keys = _watched_keys(profile)
    keyboard = KeyboardInputAdapter(keys) if keys else None
    watched_buttons = _watched_buttons(profile)
    watched_axes = _watched_axes(profile)
    hid = HidStateAdapter(
        watched_buttons=watched_buttons,
        watched_axes=watched_axes,
    ) if (watched_buttons or watched_axes) else None

    uses_cell_addressing = (
        _uses_chromalink_cell_addressing(profile)
        or bool(set(CHROMALINK_CELL_TARGETS) & live_targets)
    )
    calculation_profile = (
        _expand_whole_chromalink_rules(base_profile)
        if uses_cell_addressing
        else base_profile
    )

    physical_chroma_targets = {
        physical_chroma_target(target)
        for target in requested_chroma_logical_targets
    }
    physical_chroma_targets.update(requested_chroma_zone_surfaces)
    chroma_targets = tuple(sorted(physical_chroma_targets))
    chroma_session = LiveChromaSession(chroma_targets) if chroma_targets else None
    govee_renderer = (
        EnhancedGoveeRenderer(govee_configuration, requested_govee_targets)
        if requested_govee_targets
        else None
    )
    openrgb_renderer = (
        OpenRGBRenderer(requested_openrgb_targets)
        if requested_openrgb_targets
        else None
    )
    rule_order = {id(rule): index for index, rule in enumerate(profile.rules)}

    try:
        if chroma_session is not None:
            chroma_session.start()
        if govee_renderer is not None:
            govee_renderer.start()
        if openrgb_renderer is not None:
            openrgb_renderer.start()

        if on_session_ready is not None:
            on_session_ready()

        started = time.perf_counter()
        next_frame = started
        while not stop_event.is_set():
            if watcher is not None:
                update = watcher.poll_once()
                if update is not None:
                    state = dict(update.state)

            pressed_keys = keyboard.snapshot() if keyboard is not None else frozenset()
            if hid is not None:
                button_states, axis_values = hid.snapshot()
            else:
                button_states, axis_values = {}, {}

            now = time.perf_counter()
            active_triggers = (
                trigger_runtime.update(
                    now,
                    state,
                    pressed_keys=pressed_keys,
                    button_states=button_states,
                    axis_values=axis_values,
                )
                if trigger_runtime is not None
                else {}
            )
            calculated = calculate_profile_outputs(
                calculation_profile,
                state,
                pressed_keys=pressed_keys,
                button_states=button_states,
                axis_values=axis_values,
            )
            calculated = {
                target: _modulated_output(output, target, axis_values)
                for target, output in calculated.items()
            }
            global_fallback = calculated.get("GLOBAL")
            elapsed = now - started

            if chroma_session is not None:
                regular_targets = tuple(
                    target
                    for target in chroma_targets
                    if target not in requested_chroma_zone_surfaces
                    and not (uses_cell_addressing and target == "CHROMALINK")
                )
                regular_old_samples = []
                for target in regular_targets:
                    output = calculated.get(target, global_fallback)
                    effect = _output_effect(output, target) if output is not None else None

                    if target in {"KEYBOARD", "MOUSE"} and active_triggers.get(target):
                        base_matrix = (
                            _surface_matrix(output, target, target, elapsed)
                            if output is not None
                            else uniform_surface_matrix(target, BLACK)
                        )
                        matrix = overlay_chroma_surface_matrix(
                            target,
                            base_matrix,
                            chroma_zone_configuration,
                            active_triggers,
                            now,
                        )
                        chroma_session.render_custom_matrix(target, matrix)
                    elif target == "CHROMALINK" and active_triggers.get("CHROMALINK"):
                        base_frame = (
                            _linear_frame(output, target, 5, elapsed, y=0.5)
                            if output is not None
                            else (BLACK,) * 5
                        )
                        final_frame = overlay_targeted_frame(
                            base_frame,
                            CHROMALINK_POINTS,
                            active_triggers,
                            {"CHROMALINK": None},
                            now,
                        )
                        render_chromalink_cells(chroma_session, final_frame)
                    elif output is None:
                        # Selected+Available Chroma devices stay armed even with
                        # no Rule output; higher authority can still override.
                        chroma_session.render_rgb(target, BLACK)
                    elif target in {"KEYBOARD", "MOUSE"} and is_addressable_continuous_effect(effect):
                        matrix = _surface_matrix(output, target, target, elapsed)
                        chroma_session.render_custom_matrix(target, matrix)
                    elif target == "CHROMALINK" and is_addressable_continuous_effect(effect):
                        render_chromalink_cells(
                            chroma_session,
                            _linear_frame(output, target, 5, elapsed, y=0.5),
                        )
                    elif is_continuous_frame_effect(effect):
                        chroma_session.render_rgb(target, _uniform_rgb(output, target, elapsed))
                    else:
                        regular_old_samples.append(_runtime_sample(output, target, elapsed))

                if regular_old_samples:
                    regular_frame = render_runtime_tick(tuple(regular_old_samples))
                    for target, rgb in regular_frame.items():
                        chroma_session.render_rgb(target, rgb)

                for surface_id in sorted(requested_chroma_zone_surfaces):
                    matrices = {}
                    priorities: dict[str, int] = {}
                    base_output = calculated.get(surface_id, global_fallback)
                    if base_output is not None:
                        matrices[surface_id] = _surface_matrix(
                            base_output, surface_id, surface_id, elapsed
                        )
                        priorities[surface_id] = _output_priority(base_output, rule_order)

                    for zone in chroma_zone_configuration.for_surface(surface_id):
                        if zone.target not in requested_chroma_zone_targets:
                            continue
                        output = calculated.get(zone.target)
                        if output is None:
                            continue
                        matrices[zone.target] = _surface_matrix(
                            output, zone.target, surface_id, elapsed
                        )
                        priorities[zone.target] = _output_priority(output, rule_order)

                    matrix = compose_surface_matrices(
                        surface_id,
                        chroma_zone_configuration,
                        matrices,
                        priorities,
                    )
                    matrix = overlay_chroma_surface_matrix(
                        surface_id,
                        matrix,
                        chroma_zone_configuration,
                        active_triggers,
                        now,
                    )
                    chroma_session.render_custom_matrix(surface_id, matrix)

                if uses_cell_addressing and "CHROMALINK" in chroma_targets:
                    whole_chromalink = calculated.get("CHROMALINK", global_fallback)
                    colours = []
                    for index, cell_target in enumerate(CHROMALINK_CELL_TARGETS):
                        output = calculated.get(cell_target, whole_chromalink)
                        colours.append(
                            BLACK
                            if output is None
                            else _point_rgb(
                                output,
                                cell_target,
                                CHROMALINK_POINTS[index],
                                elapsed,
                            )
                        )
                    colours = overlay_targeted_frame(
                        tuple(colours),
                        CHROMALINK_POINTS,
                        active_triggers,
                        chromalink_target_masks(),
                        now,
                    )
                    render_chromalink_cells(chroma_session, colours)

            if govee_renderer is not None:
                requested_outputs = {
                    target: calculated.get(target, global_fallback)
                    for target in requested_govee_targets
                }
                if any_active_for_targets(active_triggers, requested_govee_targets):
                    base_target_frames = {}
                    for target, output in requested_outputs.items():
                        if output is None:
                            continue
                        device, _zone = configured_govee_map[target]
                        base_target_frames[target] = _linear_frame(
                            output,
                            target,
                            device.segment_count,
                            elapsed,
                        )
                    final_device_frames = {}
                    for device in govee_renderer.devices:
                        base_frame = compose_device_target_frames(
                            device,
                            base_target_frames,
                        )
                        final_device_frames[device.device_id] = overlay_targeted_frame(
                            base_frame,
                            linear_points(device.segment_count),
                            active_triggers,
                            govee_target_masks(device),
                            now,
                        )
                    govee_renderer.render_device_frames(
                        final_device_frames,
                        now=now,
                    )
                else:
                    has_addressable = any(
                        output is not None
                        and is_addressable_continuous_effect(_output_effect(output, target))
                        for target, output in requested_outputs.items()
                    )
                    if has_addressable:
                        target_frames = {}
                        for target, output in requested_outputs.items():
                            if output is None:
                                continue
                            device, _zone = configured_govee_map[target]
                            target_frames[target] = _linear_frame(
                                output,
                                target,
                                device.segment_count,
                                elapsed,
                            )
                        govee_renderer.render_target_frames(
                            target_frames,
                            now=now,
                        )
                    else:
                        govee_samples = []
                        for target, output in requested_outputs.items():
                            if output is None:
                                continue
                            if is_continuous_frame_effect(_output_effect(output, target)):
                                continue
                            govee_samples.append(_runtime_sample(output, target, elapsed))
                        govee_frame = (
                            render_runtime_tick(tuple(govee_samples)) if govee_samples else {}
                        )
                        for target, output in requested_outputs.items():
                            if output is not None and is_continuous_frame_effect(_output_effect(output, target)):
                                govee_frame[target] = _uniform_rgb(output, target, elapsed)
                        govee_renderer.render(govee_frame, now=now)

            if openrgb_renderer is not None:
                openrgb_frames = {}
                for target, binding in openrgb_renderer.bindings.items():
                    output = calculated.get(target, global_fallback)
                    if output is None:
                        continue
                    frame = _linear_frame(
                        output,
                        target,
                        binding.led_count,
                        elapsed,
                    )
                    if active_triggers.get(target):
                        frame = overlay_targeted_frame(
                            frame,
                            linear_points(binding.led_count),
                            active_triggers,
                            {target: None},
                            now,
                        )
                    openrgb_frames[target] = frame
                # Keep selected OpenRGB devices in the continuous renderer path
                # even when no Rule supplies a frame.
                openrgb_renderer.render_target_frames(openrgb_frames)

            next_frame += frame_period_seconds()
            remaining = next_frame - time.perf_counter()
            if remaining > 0:
                stop_event.wait(remaining)
            else:
                next_frame = time.perf_counter()
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_errors: list[Exception] = []
        if hid is not None:
            try:
                hid.close()
            except Exception as exc:
                cleanup_errors.append(exc)
        if openrgb_renderer is not None and openrgb_renderer.active:
            try:
                openrgb_renderer.release()
            except Exception as exc:
                cleanup_errors.append(exc)
        if govee_renderer is not None and govee_renderer.active:
            try:
                govee_renderer.release()
            except Exception as exc:
                cleanup_errors.append(exc)
        if chroma_session is not None and chroma_session.active:
            try:
                chroma_session.release()
            except Exception as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            if primary_error is not None:
                _attach_cleanup_failures(primary_error, cleanup_errors)
            else:
                cleanup_error = cleanup_errors[0]
                _attach_cleanup_failures(
                    cleanup_error,
                    cleanup_errors[1:],
                    prefix="Additional cleanup failure",
                )
                raise cleanup_error