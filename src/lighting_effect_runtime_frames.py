#!/usr/bin/env python3
"""Adapt persisted rule/default effect parameters to the shared frame engine.

This module is deliberately geometry/transport neutral. It is the narrow bridge
between native profile persistence and ``lighting_effect_frames``. Existing
STATIC/FLASH/PULSE/BREATH runtime sampling remains untouched elsewhere.
"""

from __future__ import annotations

from collections.abc import Sequence

from lighting_effect_config import EffectParameters
from lighting_effect_frames import FrameEffectParameters, Point, RGB, sample_effect_frame

CONTINUOUS_FRAME_EFFECTS = frozenset({"SPECTRUM", "WAVE", "STARLIGHT", "FIRE"})
ADDRESSABLE_CONTINUOUS_EFFECTS = frozenset({"WAVE", "STARLIGHT", "FIRE"})


def is_continuous_frame_effect(effect: str) -> bool:
    return effect in CONTINUOUS_FRAME_EFFECTS


def is_addressable_continuous_effect(effect: str) -> bool:
    return effect in ADDRESSABLE_CONTINUOUS_EFFECTS


def frame_parameters_from_persisted(
    effect: str,
    parameters: EffectParameters,
) -> FrameEffectParameters:
    if not isinstance(parameters, EffectParameters):
        raise ValueError("parameters must be EffectParameters")
    if effect == "SPECTRUM":
        return FrameEffectParameters(period_seconds=parameters.cycle_seconds)
    if effect == "WAVE":
        return FrameEffectParameters(
            period_seconds=parameters.cycle_seconds,
            direction=parameters.direction,
        )
    if effect == "STARLIGHT":
        return FrameEffectParameters(
            period_seconds=parameters.cycle_seconds,
            density=parameters.density,
        )
    if effect == "FIRE":
        return FrameEffectParameters(period_seconds=parameters.step_seconds)
    raise ValueError(f"effect {effect!r} is not a persisted continuous frame effect")


def sample_persisted_effect_frame(
    effect: str,
    points: Sequence[Point],
    elapsed_seconds: float,
    colours: Sequence[RGB],
    parameters: EffectParameters,
):
    if not is_continuous_frame_effect(effect):
        raise ValueError(f"effect {effect!r} is not a continuous frame effect")
    return sample_effect_frame(
        effect,
        points,
        elapsed_seconds,
        colours,
        brightness=parameters.brightness,
        parameters=frame_parameters_from_persisted(effect, parameters),
    )
