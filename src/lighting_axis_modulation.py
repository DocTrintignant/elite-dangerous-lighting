#!/usr/bin/env python3
"""Constrained HID-axis modulation for one calculated lighting output.

This is not a lighting effect. An existing effect remains authoritative; this
module only derives output-level brightness and, for STATIC, an optional primary
colour from one live HID axis. Axis samples use EDL's accepted 0..100 scale.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from numbers import Real

RGB = tuple[int, int, int]

COLOUR_BLEND = "BLEND"
COLOUR_HUE_SPECTRUM = "HUE_SPECTRUM"
COLOUR_TRANSITIONS = (COLOUR_BLEND, COLOUR_HUE_SPECTRUM)


def _finite(value: Real, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _percent(value: Real, label: str) -> float:
    result = _finite(value, label)
    if not 0.0 <= result <= 100.0:
        raise ValueError(f"{label} must be between 0 and 100")
    return result


def _unit(value: Real, label: str) -> float:
    result = _finite(value, label)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{label} must be between 0 and 1")
    return result


def _signed_unit(value: Real, label: str) -> float:
    result = _finite(value, label)
    if not -1.0 <= result <= 1.0:
        raise ValueError(f"{label} must be between -1 and 1")
    return result


def _rgb(value: RGB, label: str) -> RGB:
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError(f"{label} must be an (R, G, B) tuple")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise ValueError(f"{label} components must be integers")
    if any(v < 0 or v > 255 for v in value):
        raise ValueError(f"{label} components must be 0..255")
    return value


@dataclass(frozen=True)
class AxisOutputModulation:
    """One understandable live-axis binding for one rule output."""

    device: str
    axis: int
    rest_position: float = 50.0
    rest_zone: float = 3.0

    brightness_at_rest: float | None = None
    brightness_minus_change: float = 0.0
    brightness_plus_change: float = 0.0

    colour_minus: RGB | None = None
    colour_plus: RGB | None = None
    colour_transition: str = COLOUR_BLEND

    def __post_init__(self) -> None:
        if not isinstance(self.device, str) or not self.device.strip():
            raise ValueError("axis modulation device must be a non-empty string")
        if isinstance(self.axis, bool) or not isinstance(self.axis, int) or self.axis < 0:
            raise ValueError("axis modulation axis must be a non-negative integer")
        object.__setattr__(self, "rest_position", _percent(self.rest_position, "rest_position"))
        zone = _percent(self.rest_zone, "rest_zone")
        if zone > 25.0:
            raise ValueError("rest_zone must not exceed 25%")
        object.__setattr__(self, "rest_zone", zone)

        if self.brightness_at_rest is None:
            if self.brightness_minus_change != 0.0 or self.brightness_plus_change != 0.0:
                raise ValueError("brightness changes require brightness_at_rest")
        else:
            rest = _unit(self.brightness_at_rest, "brightness_at_rest")
            minus = _signed_unit(self.brightness_minus_change, "brightness_minus_change")
            plus = _signed_unit(self.brightness_plus_change, "brightness_plus_change")
            for label, result in (
                ("brightness toward axis -", rest + minus),
                ("brightness toward axis +", rest + plus),
            ):
                if not 0.0 <= result <= 1.0:
                    raise ValueError(f"{label} must resolve between 0 and 1")
            object.__setattr__(self, "brightness_at_rest", rest)
            object.__setattr__(self, "brightness_minus_change", minus)
            object.__setattr__(self, "brightness_plus_change", plus)

        if (self.colour_minus is None) != (self.colour_plus is None):
            raise ValueError("axis-driven colour requires both end colours")
        if self.colour_minus is not None:
            object.__setattr__(self, "colour_minus", _rgb(self.colour_minus, "colour_minus"))
            object.__setattr__(self, "colour_plus", _rgb(self.colour_plus, "colour_plus"))

        transition = str(self.colour_transition).strip().upper()
        if transition not in COLOUR_TRANSITIONS:
            raise ValueError(f"colour_transition must be one of {COLOUR_TRANSITIONS}")
        object.__setattr__(self, "colour_transition", transition)

        if not self.brightness_enabled and not self.colour_enabled:
            raise ValueError("axis modulation must follow brightness, colour, or both")

    @property
    def brightness_enabled(self) -> bool:
        return self.brightness_at_rest is not None

    @property
    def colour_enabled(self) -> bool:
        return self.colour_minus is not None


def axis_side_progress(
    modulation: AxisOutputModulation,
    observed_percent: Real | None,
) -> tuple[int, float]:
    """Return (-1|0|+1, 0..1 progress) outside the configured rest zone."""

    if not isinstance(modulation, AxisOutputModulation):
        raise ValueError("modulation must be AxisOutputModulation")
    if observed_percent is None:
        return 0, 0.0

    observed = max(0.0, min(100.0, _finite(observed_percent, "observed axis position")))
    rest = modulation.rest_position
    low_edge = max(0.0, rest - modulation.rest_zone)
    high_edge = min(100.0, rest + modulation.rest_zone)

    if observed < low_edge:
        return (-1, 0.0 if low_edge <= 0.0 else (low_edge - observed) / low_edge)
    if observed > high_edge:
        span = 100.0 - high_edge
        return (+1, 0.0 if span <= 0.0 else (observed - high_edge) / span)
    return 0, 0.0


def brightness_for_axis(
    modulation: AxisOutputModulation,
    observed_percent: Real | None,
) -> float | None:
    if not modulation.brightness_enabled:
        return None
    assert modulation.brightness_at_rest is not None
    side, progress = axis_side_progress(modulation, observed_percent)
    change = (
        modulation.brightness_minus_change if side < 0
        else modulation.brightness_plus_change if side > 0
        else 0.0
    )
    return modulation.brightness_at_rest + change * progress


def _blend_rgb(left: RGB, right: RGB, progress: float) -> RGB:
    t = max(0.0, min(1.0, float(progress)))
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(left, right))


def _hue_rgb(left: RGB, right: RGB, progress: float) -> RGB:
    t = max(0.0, min(1.0, float(progress)))
    lh, ls, lv = colorsys.rgb_to_hsv(*(v / 255.0 for v in left))
    rh, rs, rv = colorsys.rgb_to_hsv(*(v / 255.0 for v in right))
    delta = ((rh - lh + 0.5) % 1.0) - 0.5
    r, g, b = colorsys.hsv_to_rgb(
        (lh + delta * t) % 1.0,
        ls + (rs - ls) * t,
        lv + (rv - lv) * t,
    )
    return (int(round(r * 255.0)), int(round(g * 255.0)), int(round(b * 255.0)))


def colour_for_axis(
    rest_colour: RGB,
    modulation: AxisOutputModulation,
    observed_percent: Real | None,
) -> RGB:
    rest = _rgb(rest_colour, "rest_colour")
    if not modulation.colour_enabled:
        return rest
    side, progress = axis_side_progress(modulation, observed_percent)
    if side == 0:
        return rest
    end = modulation.colour_minus if side < 0 else modulation.colour_plus
    assert end is not None
    if modulation.colour_transition == COLOUR_HUE_SPECTRUM:
        return _hue_rgb(rest, end, progress)
    return _blend_rgb(rest, end, progress)


def scale_rgb(rgb: RGB, brightness: Real) -> RGB:
    colour = _rgb(rgb, "rgb")
    level = _unit(brightness, "brightness")
    return tuple(int(round(component * level)) for component in colour)


def validate_output_modulation(
    effect: str,
    effect_brightness: Real,
    modulation: AxisOutputModulation | None,
) -> None:
    """Validate the deliberately narrow first product contract."""

    if modulation is None:
        return
    if not isinstance(modulation, AxisOutputModulation):
        raise ValueError("axis_modulation must be AxisOutputModulation or None")

    if effect in {"REACTIVE", "RIPPLE"}:
        raise ValueError(
            f"{effect} is a triggered overlay; Follow axis is not supported on that path yet"
        )

    if modulation.brightness_enabled:
        if effect == "PULSE":
            raise ValueError(
                "PULSE keeps its own minimum/maximum brightness; Follow axis is unavailable"
            )
        if _unit(effect_brightness, "effect brightness") != 1.0:
            raise ValueError(
                "Follow axis brightness requires the fixed Brightness control to remain 100%"
            )

    if modulation.colour_enabled and effect != "STATIC":
        raise ValueError("Follow axis colour is currently available only for STATIC outputs")
