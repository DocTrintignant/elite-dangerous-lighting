#!/usr/bin/env python3
"""Hardware-neutral lighting effect primitives.

Effects generate logical RGB output only. They do not know about Chroma,
Status.json, rules, scenes, or device geometry.

FLASH accepts an explicit palette phase index and therefore invents no cadence.
PULSE and BREATH accept explicit elapsed time and explicit cycle parameters.
"""

from __future__ import annotations

import math

RGB = tuple[int, int, int]
Palette = tuple[RGB, ...]


def _validate_rgb(rgb: RGB) -> RGB:
    if not isinstance(rgb, tuple) or len(rgb) != 3:
        raise ValueError("colour must be an (R, G, B) tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in rgb):
        raise ValueError("RGB components must be integers")
    if any(value < 0 or value > 255 for value in rgb):
        raise ValueError("RGB components must be 0..255")
    return rgb


def _validate_palette(colours: Palette, *, minimum: int = 1) -> Palette:
    if not isinstance(colours, tuple) or len(colours) < minimum:
        raise ValueError(f"palette must contain at least {minimum} colour(s)")
    for colour in colours:
        _validate_rgb(colour)
    return colours


def _validate_intensity(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{label} must be between 0 and 1")
    return value


def _validate_elapsed_period(elapsed_seconds: float, period_seconds: float, label: str) -> tuple[float, float]:
    if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, (int, float)):
        raise ValueError(f"{label} elapsed_seconds must be numeric")
    if elapsed_seconds < 0.0:
        raise ValueError(f"{label} elapsed_seconds must not be negative")
    if isinstance(period_seconds, bool) or not isinstance(period_seconds, (int, float)):
        raise ValueError(f"{label} period_seconds must be numeric")
    if period_seconds <= 0.0:
        raise ValueError(f"{label} period_seconds must be greater than zero")
    return float(elapsed_seconds), float(period_seconds)


def static_rgb(rgb: RGB) -> RGB:
    return _validate_rgb(rgb)


def flash_palette_rgb(colours: Palette, phase_index: int) -> RGB:
    """Return one deterministic N-colour FLASH sample."""
    palette = _validate_palette(colours, minimum=2)
    if isinstance(phase_index, bool) or not isinstance(phase_index, int):
        raise ValueError("phase_index must be an integer")
    if phase_index < 0:
        raise ValueError("phase_index must not be negative")
    return palette[phase_index % len(palette)]


def flash_rgb(color_one: RGB, color_two: RGB, phase_index: int) -> RGB:
    return flash_palette_rgb((color_one, color_two), phase_index)


def scale_rgb(rgb: RGB, intensity: float) -> RGB:
    r, g, b = _validate_rgb(rgb)
    intensity = _validate_intensity(intensity, "intensity")
    return tuple(round(component * intensity) for component in (r, g, b))


def pulse_rgb(
    rgb: RGB,
    elapsed_seconds: float,
    *,
    period_seconds: float = 1.0,
    minimum_intensity: float = 0.05,
    maximum_intensity: float = 1.0,
) -> RGB:
    """Return one deterministic smooth brightness cycle for one colour."""
    _validate_rgb(rgb)
    elapsed_seconds, period_seconds = _validate_elapsed_period(
        elapsed_seconds, period_seconds, "PULSE"
    )
    minimum = _validate_intensity(minimum_intensity, "minimum_intensity")
    maximum = _validate_intensity(maximum_intensity, "maximum_intensity")
    if minimum > maximum:
        raise ValueError("minimum_intensity must not exceed maximum_intensity")

    phase = (elapsed_seconds % period_seconds) / period_seconds
    normalized = 0.5 - 0.5 * math.cos(2.0 * math.pi * phase)
    intensity = minimum + (maximum - minimum) * normalized
    return scale_rgb(rgb, intensity)


def breathe_palette_rgb(
    colours: Palette,
    elapsed_seconds: float,
    *,
    period_seconds: float,
) -> RGB:
    """Smoothly traverse an ordered 2+ colour palette and wrap to colour one.

    One complete cycle visits each configured colour in order. Every adjacent
    pair receives an equal segment of the cycle, including the final colour back
    to the first, so there is no discontinuity at the wrap point.
    """
    palette = _validate_palette(colours, minimum=2)
    elapsed_seconds, period_seconds = _validate_elapsed_period(
        elapsed_seconds, period_seconds, "BREATH"
    )

    phase = (elapsed_seconds % period_seconds) / period_seconds
    position = phase * len(palette)
    left_index = int(position) % len(palette)
    right_index = (left_index + 1) % len(palette)
    local_phase = position - int(position)
    blend = 0.5 - 0.5 * math.cos(math.pi * local_phase)
    left = palette[left_index]
    right = palette[right_index]
    return tuple(
        round(a + (b - a) * blend)
        for a, b in zip(left, right)
    )


def breathe_rgb(
    color_one: RGB,
    color_two: RGB,
    elapsed_seconds: float,
    *,
    period_seconds: float,
) -> RGB:
    """Backward-compatible two-colour BREATH helper."""
    return breathe_palette_rgb(
        (color_one, color_two), elapsed_seconds, period_seconds=period_seconds
    )
