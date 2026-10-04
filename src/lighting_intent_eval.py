#!/usr/bin/env python3
"""Evaluate logical lighting intents into final in-memory RGB values."""

from __future__ import annotations

from lighting_compositor import composite_rgb
from lighting_effects import (
    breathe_palette_rgb,
    flash_palette_rgb,
    pulse_rgb,
    scale_rgb,
    static_rgb,
)
from lighting_intent import LightingIntent

RGB = tuple[int, int, int]
SUPPORTED_INTENT_EFFECTS = ("STATIC", "FLASH", "PULSE", "BREATH")


def _require_elapsed(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if value < 0.0:
        raise ValueError(f"{label} must not be negative")
    return value


def sample_intent(
    intent: LightingIntent,
    *,
    flash_phase_index: int | None = None,
    elapsed_seconds: float | None = None,
) -> RGB:
    """Generate one logical RGB sample from an intent.

    FLASH supports two explicit timing paths:
    - caller-supplied phase index (required for cadence-less imported Virpil data)
    - elapsed time plus a native persisted ``step_seconds`` value

    The two timing forms are mutually exclusive so cadence ownership is never
    ambiguous.
    """
    if not isinstance(intent, LightingIntent):
        raise ValueError("intent must be a LightingIntent")

    if intent.effect == "STATIC":
        if flash_phase_index is not None or elapsed_seconds is not None:
            raise ValueError("STATIC intent does not accept temporal inputs")
        return scale_rgb(static_rgb(intent.colours[0]), intent.brightness)

    if intent.effect == "FLASH":
        if flash_phase_index is not None and elapsed_seconds is not None:
            raise ValueError("FLASH accepts phase index or elapsed_seconds, not both")
        phase = flash_phase_index
        if elapsed_seconds is not None:
            elapsed = _require_elapsed(elapsed_seconds, "FLASH elapsed_seconds")
            if intent.flash is None or intent.flash.step_seconds is None:
                raise ValueError(
                    "FLASH elapsed_seconds requires configured step_seconds"
                )
            phase = int(elapsed / intent.flash.step_seconds)
        if phase is None:
            raise ValueError(
                "FLASH requires explicit flash_phase_index or elapsed_seconds with step_seconds"
            )
        sampled = flash_palette_rgb(intent.colours, phase)
        return scale_rgb(sampled, intent.brightness)

    if intent.effect == "PULSE":
        if flash_phase_index is not None:
            raise ValueError("PULSE intent does not accept flash_phase_index")
        if elapsed_seconds is None:
            raise ValueError("PULSE intent requires explicit elapsed_seconds")
        assert intent.pulse is not None
        return pulse_rgb(
            intent.colours[0],
            elapsed_seconds,
            period_seconds=intent.pulse.period_seconds,
            minimum_intensity=intent.pulse.minimum_intensity,
            maximum_intensity=intent.pulse.maximum_intensity,
        )

    if intent.effect == "BREATH":
        if flash_phase_index is not None:
            raise ValueError("BREATH intent does not accept flash_phase_index")
        if elapsed_seconds is None:
            raise ValueError("BREATH intent requires explicit elapsed_seconds")
        assert intent.breath is not None
        sampled = breathe_palette_rgb(
            intent.colours,
            elapsed_seconds,
            period_seconds=intent.breath.period_seconds,
        )
        return scale_rgb(sampled, intent.brightness)

    raise ValueError(
        f"unsupported intent effect {intent.effect!r}; "
        f"currently wired effects are {SUPPORTED_INTENT_EFFECTS}"
    )


def apply_intent(
    current_rgb: RGB,
    intent: LightingIntent,
    *,
    flash_phase_index: int | None = None,
    elapsed_seconds: float | None = None,
) -> RGB:
    sampled = sample_intent(
        intent,
        flash_phase_index=flash_phase_index,
        elapsed_seconds=elapsed_seconds,
    )
    return composite_rgb(
        current_rgb,
        sampled,
        intent.composition,
        opacity=intent.blend_opacity,
    )
