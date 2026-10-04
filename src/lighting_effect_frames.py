#!/usr/bin/env python3
"""Shared deterministic frame engine for EDL lighting effects.

This module is the product-facing sampling seam for effects that produce either
uniform colour or an addressable frame. It deliberately owns no Chroma/Govee
transport and no rule matching.

The configurable pending-effect semantics are established here before profile
persistence/UI promotion. This lets the same implementation be software-tested
and physically accepted first.

REACTIVE and RIPPLE remain explicit trigger-scoped effects: callers must supply
trigger timing/origin rather than treating them as continuously matched rules.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from lighting_chroma_quick_effects import (
    Frame,
    Point,
    RGB,
    _noise01,
    _smoothstep,
    reactive_frame,
    spectrum_cycle_rgb,
    starlight_frame,
    uniform_frame,
)
from lighting_effect_config import MIN_EFFECT_TIMING_SECONDS
from lighting_effects import breathe_palette_rgb, scale_rgb

CONTINUOUS_FRAME_EFFECTS = (
    "STATIC",
    "BREATH",
    "SPECTRUM",
    "WAVE",
    "STARLIGHT",
    "FIRE",
)
TRIGGERED_FRAME_EFFECTS = ("REACTIVE", "RIPPLE")
BATCH_EFFECTS = (*CONTINUOUS_FRAME_EFFECTS, *TRIGGERED_FRAME_EFFECTS)

WAVE_DIRECTIONS = (
    "LEFT_TO_RIGHT",
    "RIGHT_TO_LEFT",
    "TOP_TO_BOTTOM",
    "BOTTOM_TO_TOP",
)
RIPPLE_DIRECTIONS = (
    "CENTER_OUT",
    "EDGES_IN",
    "LEFT_TO_RIGHT",
    "RIGHT_TO_LEFT",
    "TOP_TO_BOTTOM",
    "BOTTOM_TO_TOP",
)

DEFAULT_FIRE_PALETTE: tuple[RGB, ...] = (
    (18, 0, 0),
    (255, 24, 0),
    (255, 150, 0),
    (255, 245, 120),
)


@dataclass(frozen=True)
class FrameEffectParameters:
    """Unpersisted frame-effect parameters under physical acceptance.

    These are deliberately separate from the stable profile ``EffectParameters``
    schema until their physical behavior is accepted. None means use the
    effect's explicit default below.
    """

    period_seconds: float | None = None
    direction: str | None = None
    density: float | None = None
    speed: float | None = None
    width: float | None = None
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.period_seconds is not None:
            value = self.period_seconds
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("period_seconds must be numeric or None")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("period_seconds must be finite")
            if value < MIN_EFFECT_TIMING_SECONDS:
                raise ValueError(
                    f"period_seconds must be at least {MIN_EFFECT_TIMING_SECONDS:.2f} seconds when set"
                )

        for label in ("speed", "width", "duration_seconds"):
            value = getattr(self, label)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0.0:
                raise ValueError(f"{label} must be greater than zero or None")
        if self.density is not None:
            if (
                isinstance(self.density, bool)
                or not isinstance(self.density, (int, float))
                or not 0.0 <= float(self.density) <= 1.0
            ):
                raise ValueError("density must be between 0 and 1 or None")
        if self.direction is not None and (
            not isinstance(self.direction, str) or not self.direction.strip()
        ):
            raise ValueError("direction must be a non-empty string or None")


@dataclass(frozen=True)
class TriggerFrameContext:
    """One explicit temporary-effect trigger sample."""

    elapsed_seconds: float
    indices: tuple[int, ...] = ()
    origin: Point | None = None

    def __post_init__(self) -> None:
        if isinstance(self.elapsed_seconds, bool) or not isinstance(
            self.elapsed_seconds, (int, float)
        ):
            raise ValueError("trigger elapsed_seconds must be numeric")
        if self.elapsed_seconds < 0.0:
            raise ValueError("trigger elapsed_seconds must not be negative")
        if any(isinstance(index, bool) or not isinstance(index, int) for index in self.indices):
            raise ValueError("trigger indices must contain integers")
        if self.origin is not None:
            if not isinstance(self.origin, tuple) or len(self.origin) != 2:
                raise ValueError("trigger origin must be an (x, y) tuple")
            x, y = self.origin
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in self.origin):
                raise ValueError("trigger origin coordinates must be numeric")
            if not 0.0 <= float(x) <= 1.0 or not 0.0 <= float(y) <= 1.0:
                raise ValueError("trigger origin must be normalized to 0..1")


def default_frame_parameters(effect: str) -> FrameEffectParameters:
    if effect == "SPECTRUM":
        return FrameEffectParameters(period_seconds=5.0)
    if effect == "WAVE":
        return FrameEffectParameters(period_seconds=4.0, direction="LEFT_TO_RIGHT")
    if effect == "STARLIGHT":
        return FrameEffectParameters(period_seconds=1.35, density=0.34)
    if effect == "FIRE":
        return FrameEffectParameters(period_seconds=0.10)
    if effect == "REACTIVE":
        return FrameEffectParameters(duration_seconds=0.95)
    if effect == "RIPPLE":
        return FrameEffectParameters(
            direction="CENTER_OUT",
            speed=0.72,
            width=0.11,
            duration_seconds=1.55,
        )
    return FrameEffectParameters()


def effect_palette_cardinality(effect: str) -> tuple[int, int | None]:
    """Return user-facing palette cardinality for frame effects.

    SPECTRUM owns its hue cycle and therefore exposes no user palette. Existing
    callers may still pass a legacy placeholder colour; it is ignored.
    """
    if effect in {"STATIC", "REACTIVE"}:
        return 1, 1
    if effect == "SPECTRUM":
        return 0, 0
    if effect in {"BREATH", "WAVE", "FIRE"}:
        return 2, None
    if effect in {"STARLIGHT", "RIPPLE"}:
        return 1, None
    raise ValueError(f"unknown effect {effect!r}")


def _effective_parameters(
    effect: str,
    parameters: FrameEffectParameters | None,
) -> FrameEffectParameters:
    defaults = default_frame_parameters(effect)
    if parameters is None:
        return defaults
    if not isinstance(parameters, FrameEffectParameters):
        raise ValueError("parameters must be FrameEffectParameters or None")
    return FrameEffectParameters(
        period_seconds=(parameters.period_seconds if parameters.period_seconds is not None else defaults.period_seconds),
        direction=(parameters.direction if parameters.direction is not None else defaults.direction),
        density=(parameters.density if parameters.density is not None else defaults.density),
        speed=(parameters.speed if parameters.speed is not None else defaults.speed),
        width=(parameters.width if parameters.width is not None else defaults.width),
        duration_seconds=(
            parameters.duration_seconds
            if parameters.duration_seconds is not None
            else defaults.duration_seconds
        ),
    )


def _validate_points(points: Sequence[Point]) -> tuple[Point, ...]:
    values = tuple(points)
    if not values:
        raise ValueError("effect frame requires at least one point")
    for point in values:
        if not isinstance(point, tuple) or len(point) != 2:
            raise ValueError("effect points must be (x, y) tuples")
        x, y = point
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in point):
            raise ValueError("effect point coordinates must be numeric")
        if not 0.0 <= float(x) <= 1.0 or not 0.0 <= float(y) <= 1.0:
            raise ValueError("effect point coordinates must be normalized to 0..1")
    return values


def _validate_palette(effect: str, colours: Sequence[RGB]) -> tuple[RGB, ...]:
    palette = tuple(colours)
    minimum, maximum = effect_palette_cardinality(effect)
    if effect == "SPECTRUM":
        # Compatibility: old acceptance fixtures supplied one unused RED value.
        if not palette:
            return ()
    elif len(palette) < minimum:
        raise ValueError(f"{effect} requires at least {minimum} colour(s)")
    if maximum is not None and len(palette) > maximum and effect != "SPECTRUM":
        raise ValueError(f"{effect} accepts at most {maximum} colour(s)")
    for colour in palette:
        if not isinstance(colour, tuple) or len(colour) != 3:
            raise ValueError("effect colours must be RGB tuples")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in colour):
            raise ValueError("RGB components must be integers")
        if any(value < 0 or value > 255 for value in colour):
            raise ValueError("RGB components must be 0..255")
    return palette


def _validate_brightness(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("brightness must be numeric")
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise ValueError("brightness must be between 0 and 1")
    return value


def _palette_rgb(
    palette: tuple[RGB, ...],
    phase: float,
    *,
    wrap: bool,
) -> RGB:
    if not palette:
        raise ValueError("palette cannot be empty")
    if len(palette) == 1:
        return palette[0]

    if wrap:
        position = (float(phase) % 1.0) * len(palette)
        left_index = int(position) % len(palette)
        right_index = (left_index + 1) % len(palette)
        local = position - math.floor(position)
    else:
        position = max(0.0, min(1.0, float(phase))) * (len(palette) - 1)
        left_index = min(len(palette) - 1, int(position))
        right_index = min(len(palette) - 1, left_index + 1)
        local = position - left_index

    blend = 0.5 - 0.5 * math.cos(math.pi * local)
    left = palette[left_index]
    right = palette[right_index]
    return tuple(round(a + (b - a) * blend) for a, b in zip(left, right))  # type: ignore[return-value]


def _coordinate(point: Point, direction: str) -> float:
    x, y = point
    if direction == "LEFT_TO_RIGHT":
        return x
    if direction == "RIGHT_TO_LEFT":
        return 1.0 - x
    if direction == "TOP_TO_BOTTOM":
        return y
    if direction == "BOTTOM_TO_TOP":
        return 1.0 - y
    raise ValueError(f"direction must be one of {WAVE_DIRECTIONS}")


def _palette_wave_frame(
    points: tuple[Point, ...],
    elapsed: float,
    palette: tuple[RGB, ...],
    parameters: FrameEffectParameters,
    brightness: float,
) -> Frame:
    direction = parameters.direction or "LEFT_TO_RIGHT"
    if direction not in WAVE_DIRECTIONS:
        raise ValueError(f"WAVE direction must be one of {WAVE_DIRECTIONS}")
    period = parameters.period_seconds or 4.0
    temporal = elapsed / period
    return tuple(
        scale_rgb(
            _palette_rgb(palette, _coordinate(point, direction) - temporal, wrap=True),
            brightness,
        )
        for point in points
    )


def _fire_heat_frame(
    points: tuple[Point, ...],
    elapsed: float,
    frame_seconds: float,
) -> tuple[float, ...]:
    """Reuse the accepted FIRE noise/heat mechanism before palette mapping."""
    position = elapsed / frame_seconds
    bucket = math.floor(position)
    blend = _smoothstep(position - bucket)
    heat_values: list[float] = []
    for index, (_x, y) in enumerate(points):
        noise_a = _noise01(260904, index, bucket, 3)
        noise_b = _noise01(260904, index, bucket + 1, 3)
        noise = noise_a + (noise_b - noise_a) * blend
        vertical_heat = 0.45 + 0.55 * y
        heat_values.append(vertical_heat * (0.66 + 0.34 * noise))
    return tuple(heat_values)


def _palette_fire_frame(
    points: tuple[Point, ...],
    elapsed: float,
    palette: tuple[RGB, ...],
    parameters: FrameEffectParameters,
    brightness: float,
) -> Frame:
    frame_seconds = parameters.period_seconds or 0.10
    return tuple(
        scale_rgb(_palette_rgb(palette, heat, wrap=False), brightness)
        for heat in _fire_heat_frame(points, elapsed, frame_seconds)
    )


def _edge_distance(points: tuple[Point, ...], point: Point) -> float:
    xs = {round(value[0], 9) for value in points}
    ys = {round(value[1], 9) for value in points}
    x, y = point
    if len(ys) == 1:
        return min(x, 1.0 - x)
    if len(xs) == 1:
        return min(y, 1.0 - y)
    return min(x, 1.0 - x, y, 1.0 - y)


def _ripple_distance(
    points: tuple[Point, ...],
    point: Point,
    direction: str,
    origin: Point | None,
) -> float:
    x, y = point
    if direction == "CENTER_OUT":
        if origin is None:
            raise ValueError("CENTER_OUT RIPPLE requires an explicit trigger origin")
        return math.hypot(x - origin[0], y - origin[1])
    if direction == "EDGES_IN":
        return _edge_distance(points, point)
    if direction in WAVE_DIRECTIONS:
        return _coordinate(point, direction)
    raise ValueError(f"RIPPLE direction must be one of {RIPPLE_DIRECTIONS}")


def _directional_ripple_frame(
    points: tuple[Point, ...],
    trigger: TriggerFrameContext,
    palette: tuple[RGB, ...],
    parameters: FrameEffectParameters,
    brightness: float,
) -> Frame:
    direction = parameters.direction or "CENTER_OUT"
    if direction not in RIPPLE_DIRECTIONS:
        raise ValueError(f"RIPPLE direction must be one of {RIPPLE_DIRECTIONS}")
    duration = parameters.duration_seconds or 1.55
    if trigger.elapsed_seconds >= duration:
        return uniform_frame(len(points), (0, 0, 0))
    speed = parameters.speed or 0.72
    width = parameters.width or 0.11
    front = speed * trigger.elapsed_seconds
    lifetime = 1.0 - trigger.elapsed_seconds / duration
    colour = scale_rgb(
        _palette_rgb(palette, trigger.elapsed_seconds / duration, wrap=False),
        brightness,
    )

    frame: list[RGB] = []
    for point in points:
        distance = _ripple_distance(points, point, direction, trigger.origin)
        amount = max(0.0, 1.0 - abs(distance - front) / width) * lifetime
        frame.append(scale_rgb(colour, amount))
    return tuple(frame)


def sample_effect_frame(
    effect: str,
    points: Sequence[Point],
    elapsed_seconds: float,
    colours: Sequence[RGB],
    *,
    brightness: float = 1.0,
    trigger: TriggerFrameContext | None = None,
    parameters: FrameEffectParameters | None = None,
) -> Frame:
    """Sample one complete logical frame for an EDL effect."""
    points = _validate_points(points)
    palette = _validate_palette(effect, colours)
    brightness = _validate_brightness(brightness)
    if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, (int, float)):
        raise ValueError("elapsed_seconds must be numeric")
    elapsed = float(elapsed_seconds)
    if elapsed < 0.0:
        raise ValueError("elapsed_seconds must not be negative")
    parameters = _effective_parameters(effect, parameters)

    if effect == "STATIC":
        return uniform_frame(len(points), scale_rgb(palette[0], brightness))

    if effect == "BREATH":
        sampled = breathe_palette_rgb(
            palette,
            elapsed,
            period_seconds=parameters.period_seconds or 4.0,
        )
        return uniform_frame(len(points), scale_rgb(sampled, brightness))

    if effect == "SPECTRUM":
        sampled = spectrum_cycle_rgb(
            elapsed,
            period_seconds=parameters.period_seconds or 5.0,
            brightness=brightness,
        )
        return uniform_frame(len(points), sampled)

    if effect == "WAVE":
        return _palette_wave_frame(points, elapsed, palette, parameters, brightness)

    if effect == "STARLIGHT":
        return starlight_frame(
            points,
            elapsed,
            palette,
            twinkle_seconds=parameters.period_seconds or 1.35,
            density=(parameters.density if parameters.density is not None else 0.34),
            seed=260904,
            brightness=brightness,
        )

    if effect == "FIRE":
        return _palette_fire_frame(points, elapsed, palette, parameters, brightness)

    if effect == "REACTIVE":
        if trigger is None:
            raise ValueError("REACTIVE requires an explicit trigger context")
        if not trigger.indices:
            raise ValueError("REACTIVE requires at least one trigger index")
        duration = parameters.duration_seconds or 0.95
        colour = _palette_rgb(
            palette,
            min(1.0, trigger.elapsed_seconds / duration),
            wrap=False,
        )
        return reactive_frame(
            len(points),
            trigger.indices,
            trigger.elapsed_seconds,
            scale_rgb(colour, brightness),
            duration_seconds=duration,
        )

    if effect == "RIPPLE":
        if trigger is None:
            raise ValueError("RIPPLE requires an explicit trigger context")
        return _directional_ripple_frame(
            points,
            trigger,
            palette,
            parameters,
            brightness,
        )

    raise ValueError(f"unsupported effect frame {effect!r}")
