#!/usr/bin/env python3
"""Pure M3E runtime tick calculation.

This module is the final hardware-neutral calculation seam before device
geometry/render adapters. It joins already-proven pieces without owning a clock,
rule matching, Chroma transport, scenes, or authority arbitration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from lighting_compositor import REPLACE
from lighting_intent import (
    BreathParameters,
    FlashParameters,
    LightingIntent,
    PulseParameters,
    intent_from_rule,
    intent_from_virpil_runtime,
)
from lighting_intent_eval import apply_intent
from lighting_profiles import ProfileCalculatedOutput, ProfileDefault
from lighting_rules import LightingRule

RGB = tuple[int, int, int]


def _validate_rgb(rgb: RGB) -> RGB:
    if not isinstance(rgb, tuple) or len(rgb) != 3:
        raise ValueError("RGB value must be an (R, G, B) tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in rgb):
        raise ValueError("RGB components must be integers")
    if any(value < 0 or value > 255 for value in rgb):
        raise ValueError("RGB components must be 0..255")
    return rgb


@dataclass(frozen=True)
class RuntimeIntentSample:
    """One intent plus the temporal sample supplied for this render tick."""

    intent: LightingIntent
    flash_phase_index: int | None = None
    elapsed_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, LightingIntent):
            raise ValueError("runtime sample intent must be a LightingIntent")


def _intent_from_default(output: ProfileDefault) -> LightingIntent:
    parameters = output.effect_parameters
    secondary = output.colours[1] if len(output.colours) >= 2 else None
    flash = None
    pulse = None
    breath = None

    if output.effect == "FLASH" and parameters.step_seconds is not None:
        flash = FlashParameters(parameters.step_seconds)
    elif output.effect == "PULSE":
        if (
            parameters.cycle_seconds is not None
            and parameters.minimum_brightness is not None
            and parameters.maximum_brightness is not None
        ):
            pulse = PulseParameters(
                parameters.cycle_seconds,
                parameters.minimum_brightness,
                parameters.maximum_brightness,
            )
    elif output.effect == "BREATH" and parameters.cycle_seconds is not None:
        breath = BreathParameters(parameters.cycle_seconds)

    brightness = 1.0 if output.effect == "PULSE" else parameters.brightness
    return LightingIntent(
        target=output.target,
        effect=output.effect,
        color_one=output.colour,
        color_two=secondary if output.effect in {"FLASH", "BREATH"} else None,
        colours=output.colours,
        flash=flash,
        pulse=pulse,
        breath=breath,
        brightness=brightness,
    )


def intents_from_native_outputs(
    outputs: Mapping[str, ProfileCalculatedOutput],
) -> tuple[LightingIntent, ...]:
    """Adapt calculated native profile outputs into validated render intents."""
    intents: list[LightingIntent] = []
    for target, output in outputs.items():
        if isinstance(output, LightingRule):
            intent = intent_from_rule(output)
        elif isinstance(output, ProfileDefault):
            intent = _intent_from_default(output)
        else:
            raise ValueError(
                f"unsupported native calculated output for target {target!r}: "
                f"{type(output).__name__}"
            )
        if intent.target != target:
            raise ValueError(
                f"native calculated output key {target!r} does not match "
                f"intent target {intent.target!r}"
            )
        intents.append(intent)
    return tuple(intents)


def intents_from_virpil_outputs(
    outputs: Mapping[str, object],
) -> tuple[LightingIntent, ...]:
    """Adapt calculated Virpil runtime outputs without inventing cadence."""
    intents: list[LightingIntent] = []
    for target, runtime in outputs.items():
        intent = intent_from_virpil_runtime(runtime)
        if intent.target != target:
            raise ValueError(
                f"Virpil calculated output key {target!r} does not match "
                f"intent target {intent.target!r}"
            )
        intents.append(intent)
    return tuple(intents)


def render_runtime_tick(
    samples: Iterable[RuntimeIntentSample],
    *,
    initial_rgb_by_target: Mapping[str, RGB] | None = None,
) -> dict[str, RGB]:
    """Return one final logical RGB value per target for this render tick."""
    final: dict[str, RGB] = {}

    for target, rgb in (initial_rgb_by_target or {}).items():
        if not isinstance(target, str) or not target.strip():
            raise ValueError("initial target must be a non-empty string")
        final[target] = _validate_rgb(rgb)

    for sample in samples:
        if not isinstance(sample, RuntimeIntentSample):
            raise ValueError("samples must contain only RuntimeIntentSample values")

        intent = sample.intent
        target = intent.target
        if target not in final and intent.composition != REPLACE:
            raise ValueError(
                f"first intent for target {target!r} uses {intent.composition}; "
                "provide initial_rgb_by_target or begin with REPLACE"
            )

        current = final.get(target, (0, 0, 0))
        final[target] = apply_intent(
            current,
            intent,
            flash_phase_index=sample.flash_phase_index,
            elapsed_seconds=sample.elapsed_seconds,
        )

    return final
