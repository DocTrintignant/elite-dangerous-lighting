#!/usr/bin/env python3
"""Deterministic hardware-neutral primitives for Chroma quick-effect families.

This module deliberately does *not* call deprecated fixed Chroma SDK effects and
it does not alter the frozen STATIC / FLASH / PULSE / BREATH implementation.
It supplies the first unproven boundary for the remaining current Chroma quick
families so they can be software-verified and then physically accepted before
normal profile/runtime integration.

Placement contract:

- STATIC / BREATH / SPECTRUM / WAVE / STARLIGHT / FIRE are continuous effects;
  once accepted they may exist while a calculated rule output remains active.
- REACTIVE / RIPPLE require an actuation edge/origin and therefore belong to the
  temporary-trigger/scene layer rather than to steady rule matching.
- FLASH / PULSE remain EDL/VIRPIL-native continuous effects outside the current
  Chroma quick-effect list.

Spatial effects return a logical frame over normalized (x, y) points.  A future
renderer maps those points to real device geometry.  Uniform-only hardware can
use ``average_frame_rgb`` as an explicit deterministic degradation; the intent
is not silently re-labelled as another effect.

The functions are deterministic for the same explicit inputs.  STARLIGHT/FIRE
use a stateless integer hash rather than mutable randomness so tests, previews
and later physical reproductions can use the same seed/time sample.
"""

from __future__ import annotations

import colorsys
import math
from typing import Iterable, Sequence

RGB = tuple[int, int, int]
Point = tuple[float, float]
Frame = tuple[RGB, ...]

CURRENT_CHROMA_QUICK_EFFECTS = (
    "BREATH",
    "FIRE",
    "REACTIVE",
    "RIPPLE",
    "SPECTRUM",
    "STARLIGHT",
    "STATIC",
    "WAVE",
)
CONTINUOUS_RULE_EFFECTS = (
    "STATIC",
    "BREATH",
    "SPECTRUM",
    "WAVE",
    "STARLIGHT",
    "FIRE",
)
TRIGGERED_SCENE_EFFECTS = ("REACTIVE", "RIPPLE")
EDL_EXTRA_CONTINUOUS_EFFECTS = ("FLASH", "PULSE")

UNIFORM_RGB = "UNIFORM_RGB"
ADDRESSABLE_FRAME = "ADDRESSABLE_FRAME"
TRIGGERED_ADDRESSABLE_FRAME = "TRIGGERED_ADDRESSABLE_FRAME"

_WAVE_DIRECTIONS = (
    "LEFT_TO_RIGHT",
    "RIGHT_TO_LEFT",
    "TOP_TO_BOTTOM",
    "BOTTOM_TO_TOP",
)
_MASK64 = (1 << 64) - 1


def effect_placement(effect: str) -> str:
    if effect in CONTINUOUS_RULE_EFFECTS:
        return "RULE_OUTPUT"
    if effect in TRIGGERED_SCENE_EFFECTS:
        return "TEMPORARY_TRIGGER"
    if effect in EDL_EXTRA_CONTINUOUS_EFFECTS:
        return "RULE_OUTPUT"
    raise ValueError(f"unknown effect placement for {effect!r}")


def required_capability(effect: str) -> str:
    if effect in {"STATIC", "BREATH", "SPECTRUM", "FLASH", "PULSE"}:
        return UNIFORM_RGB
    if effect in {"WAVE", "STARLIGHT", "FIRE"}:
        return ADDRESSABLE_FRAME
    if effect in TRIGGERED_SCENE_EFFECTS:
        return TRIGGERED_ADDRESSABLE_FRAME
    raise ValueError(f"unknown effect capability for {effect!r}")


def _validate_rgb(rgb: RGB) -> RGB:
    if not isinstance(rgb, tuple) or len(rgb) != 3:
        raise ValueError("colour must be an (R, G, B) tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in rgb):
        raise ValueError("RGB components must be integers")
    if any(value < 0 or value > 255 for value in rgb):
        raise ValueError("RGB components must be 0..255")
    return rgb


def _validate_unit(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{label} must be between 0 and 1")
    return value


def _validate_positive(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if value <= 0.0:
        raise ValueError(f"{label} must be greater than zero")
    return value


def _validate_elapsed(value: float, label: str = "elapsed_seconds") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if value < 0.0:
        raise ValueError(f"{label} must not be negative")
    return value


def _validate_points(points: Sequence[Point]) -> tuple[Point, ...]:
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)) or not points:
        raise ValueError("points must be a non-empty sequence")
    normalized: list[Point] = []
    for point in points:
        if not isinstance(point, tuple) or len(point) != 2:
            raise ValueError("each point must be an (x, y) tuple")
        x, y = point
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in point):
            raise ValueError("point coordinates must be numeric")
        x = float(x)
        y = float(y)
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError("point coordinates must be normalized to 0..1")
        normalized.append((x, y))
    return tuple(normalized)


def rectangular_grid_points(rows: int, columns: int) -> tuple[Point, ...]:
    """Return row-major normalized points for any rectangular addressable grid."""
    if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
        raise ValueError("rows must be a positive integer")
    if isinstance(columns, bool) or not isinstance(columns, int) or columns <= 0:
        raise ValueError("columns must be a positive integer")
    x_denominator = max(1, columns - 1)
    y_denominator = max(1, rows - 1)
    return tuple(
        (column / x_denominator, row / y_denominator)
        for row in range(rows)
        for column in range(columns)
    )


def uniform_frame(count: int, rgb: RGB) -> Frame:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("count must be a positive integer")
    colour = _validate_rgb(rgb)
    return tuple(colour for _ in range(count))


def average_frame_rgb(frame: Sequence[RGB]) -> RGB:
    """Explicit uniform-only degradation for an addressable logical frame."""
    if not isinstance(frame, Sequence) or isinstance(frame, (str, bytes)) or not frame:
        raise ValueError("frame must be a non-empty sequence")
    colours = tuple(_validate_rgb(colour) for colour in frame)
    return tuple(round(sum(colour[channel] for colour in colours) / len(colours)) for channel in range(3))  # type: ignore[return-value]


def _scale_rgb(rgb: RGB, intensity: float) -> RGB:
    colour = _validate_rgb(rgb)
    intensity = _validate_unit(intensity, "intensity")
    return tuple(round(component * intensity) for component in colour)  # type: ignore[return-value]


def _blend_rgb(base: RGB, overlay: RGB, amount: float) -> RGB:
    left = _validate_rgb(base)
    right = _validate_rgb(overlay)
    amount = _validate_unit(amount, "blend amount")
    return tuple(round(a + (b - a) * amount) for a, b in zip(left, right))  # type: ignore[return-value]


def _hsv_rgb(hue: float, saturation: float = 1.0, value: float = 1.0) -> RGB:
    saturation = _validate_unit(saturation, "saturation")
    value = _validate_unit(value, "value")
    r, g, b = colorsys.hsv_to_rgb(float(hue) % 1.0, saturation, value)
    return round(r * 255), round(g * 255), round(b * 255)


def spectrum_cycle_rgb(
    elapsed_seconds: float,
    *,
    period_seconds: float = 8.0,
    brightness: float = 1.0,
    phase_offset: float = 0.0,
) -> RGB:
    """Return one uniform RGB sample cycling continuously through full hue."""
    elapsed = _validate_elapsed(elapsed_seconds)
    period = _validate_positive(period_seconds, "period_seconds")
    brightness = _validate_unit(brightness, "brightness")
    if isinstance(phase_offset, bool) or not isinstance(phase_offset, (int, float)):
        raise ValueError("phase_offset must be numeric")
    hue = (elapsed / period + float(phase_offset)) % 1.0
    return _hsv_rgb(hue, 1.0, brightness)


def wave_frame(
    points: Sequence[Point],
    elapsed_seconds: float,
    *,
    period_seconds: float = 4.0,
    direction: str = "LEFT_TO_RIGHT",
    spatial_cycles: float = 1.0,
    brightness: float = 1.0,
) -> Frame:
    """Return a travelling full-spectrum wave over normalized device geometry."""
    points = _validate_points(points)
    elapsed = _validate_elapsed(elapsed_seconds)
    period = _validate_positive(period_seconds, "period_seconds")
    brightness = _validate_unit(brightness, "brightness")
    if direction not in _WAVE_DIRECTIONS:
        raise ValueError(f"direction must be one of {_WAVE_DIRECTIONS}")
    if isinstance(spatial_cycles, bool) or not isinstance(spatial_cycles, (int, float)):
        raise ValueError("spatial_cycles must be numeric")
    spatial_cycles = float(spatial_cycles)
    if spatial_cycles <= 0.0:
        raise ValueError("spatial_cycles must be greater than zero")

    temporal_phase = elapsed / period
    frame: list[RGB] = []
    for x, y in points:
        if direction == "LEFT_TO_RIGHT":
            coordinate = x
        elif direction == "RIGHT_TO_LEFT":
            coordinate = 1.0 - x
        elif direction == "TOP_TO_BOTTOM":
            coordinate = y
        else:
            coordinate = 1.0 - y
        hue = (coordinate * spatial_cycles - temporal_phase) % 1.0
        frame.append(_hsv_rgb(hue, 1.0, brightness))
    return tuple(frame)


def _mix64(value: int) -> int:
    value &= _MASK64
    value ^= value >> 30
    value = (value * 0xBF58476D1CE4E5B9) & _MASK64
    value ^= value >> 27
    value = (value * 0x94D049BB133111EB) & _MASK64
    value ^= value >> 31
    return value & _MASK64


def _noise01(seed: int, index: int, slot: int, channel: int) -> float:
    value = (
        (int(seed) & _MASK64)
        ^ ((int(index) + 1) * 0x9E3779B97F4A7C15)
        ^ ((int(slot) + 0x100000000) * 0xD1B54A32D192ED03)
        ^ ((int(channel) + 1) * 0x94D049BB133111EB)
    ) & _MASK64
    return _mix64(value) / float(_MASK64)


def starlight_frame(
    points: Sequence[Point],
    elapsed_seconds: float,
    colours: Sequence[RGB],
    *,
    twinkle_seconds: float = 1.4,
    density: float = 0.35,
    seed: int = 0,
    brightness: float = 1.0,
) -> Frame:
    """Return deterministic independently phased twinkles across addressable LEDs."""
    points = _validate_points(points)
    elapsed = _validate_elapsed(elapsed_seconds)
    period = _validate_positive(twinkle_seconds, "twinkle_seconds")
    density = _validate_unit(density, "density")
    brightness = _validate_unit(brightness, "brightness")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not isinstance(colours, Sequence) or isinstance(colours, (str, bytes)) or not colours:
        raise ValueError("colours must be a non-empty sequence")
    palette = tuple(_validate_rgb(colour) for colour in colours)

    frame: list[RGB] = []
    for index, _point in enumerate(points):
        offset = _noise01(seed, index, 0, 0)
        local = elapsed / period + offset
        slot = math.floor(local)
        phase = local - slot
        active = _noise01(seed, index, slot, 1) < density
        if not active:
            frame.append((0, 0, 0))
            continue
        palette_index = min(
            len(palette) - 1,
            int(_noise01(seed, index, slot, 2) * len(palette)),
        )
        envelope = math.sin(math.pi * phase) ** 2
        frame.append(_scale_rgb(palette[palette_index], envelope * brightness))
    return tuple(frame)


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def _fire_rgb(heat: float) -> RGB:
    heat = max(0.0, min(1.0, float(heat)))
    if heat < 0.35:
        return _blend_rgb((18, 0, 0), (255, 24, 0), heat / 0.35)
    if heat < 0.72:
        return _blend_rgb((255, 24, 0), (255, 150, 0), (heat - 0.35) / 0.37)
    return _blend_rgb((255, 150, 0), (255, 245, 120), (heat - 0.72) / 0.28)


def fire_frame(
    points: Sequence[Point],
    elapsed_seconds: float,
    *,
    frame_seconds: float = 0.10,
    seed: int = 0,
    brightness: float = 1.0,
) -> Frame:
    """Return deterministic warm flicker with greater heat toward normalized bottom."""
    points = _validate_points(points)
    elapsed = _validate_elapsed(elapsed_seconds)
    frame_seconds = _validate_positive(frame_seconds, "frame_seconds")
    brightness = _validate_unit(brightness, "brightness")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")

    position = elapsed / frame_seconds
    bucket = math.floor(position)
    blend = _smoothstep(position - bucket)
    frame: list[RGB] = []
    for index, (_x, y) in enumerate(points):
        noise_a = _noise01(seed, index, bucket, 3)
        noise_b = _noise01(seed, index, bucket + 1, 3)
        noise = noise_a + (noise_b - noise_a) * blend
        vertical_heat = 0.45 + 0.55 * y
        heat = vertical_heat * (0.66 + 0.34 * noise)
        frame.append(_scale_rgb(_fire_rgb(heat), brightness))
    return tuple(frame)


def reactive_frame(
    count: int,
    trigger_indices: Iterable[int],
    trigger_elapsed_seconds: float,
    colour: RGB,
    *,
    duration_seconds: float = 0.8,
    base_rgb: RGB = (0, 0, 0),
) -> Frame:
    """Return one triggered LED fade; trigger timing/origin are explicit inputs."""
    base = uniform_frame(count, base_rgb)
    colour = _validate_rgb(colour)
    elapsed = _validate_elapsed(trigger_elapsed_seconds, "trigger_elapsed_seconds")
    duration = _validate_positive(duration_seconds, "duration_seconds")
    indices = tuple(trigger_indices)
    if any(isinstance(index, bool) or not isinstance(index, int) for index in indices):
        raise ValueError("trigger_indices must contain integers")
    if any(index < 0 or index >= count for index in indices):
        raise ValueError("trigger index is outside the frame")
    if elapsed >= duration or not indices:
        return base

    intensity = (1.0 - elapsed / duration) ** 2
    frame = list(base)
    for index in indices:
        frame[index] = _blend_rgb(base[index], colour, intensity)
    return tuple(frame)


def ripple_frame(
    points: Sequence[Point],
    origin: Point,
    trigger_elapsed_seconds: float,
    colour: RGB,
    *,
    speed: float = 1.15,
    width: float = 0.12,
    duration_seconds: float = 1.5,
    base_rgb: RGB = (0, 0, 0),
) -> Frame:
    """Return one outward travelling ring from an explicit normalized origin."""
    points = _validate_points(points)
    (origin_x, origin_y) = _validate_points((origin,))[0]
    colour = _validate_rgb(colour)
    base_rgb = _validate_rgb(base_rgb)
    elapsed = _validate_elapsed(trigger_elapsed_seconds, "trigger_elapsed_seconds")
    speed = _validate_positive(speed, "speed")
    width = _validate_positive(width, "width")
    duration = _validate_positive(duration_seconds, "duration_seconds")
    if elapsed >= duration:
        return uniform_frame(len(points), base_rgb)

    radius = speed * elapsed
    lifetime = 1.0 - elapsed / duration
    frame: list[RGB] = []
    for x, y in points:
        distance = math.hypot(x - origin_x, y - origin_y)
        ring = max(0.0, 1.0 - abs(distance - radius) / width)
        amount = ring * lifetime
        frame.append(_blend_rgb(base_rgb, colour, amount))
    return tuple(frame)
