#!/usr/bin/env python3
"""Effect-specific configuration rules shared by persistence, runtime and UI.

This module deliberately separates the generic ordered colour-palette mechanism
from the semantics of individual effects. A palette may contain N colours, but
an effect accepts only the cardinality and parameters that have defined meaning.

STATIC / FLASH / PULSE / BREATH keep their accepted semantics unchanged.
SPECTRUM / WAVE / STARLIGHT / FIRE are the physically accepted continuous
addressable effects now eligible for normal rule output. REACTIVE / RIPPLE keep
their trigger-scoped classification; their parameter schema is defined here for
future scene/trigger persistence but they are not normal rule effects.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

RGB = tuple[int, int, int]
Palette = tuple[RGB, ...]
MIN_EFFECT_TIMING_SECONDS = 0.01

STATIC = "STATIC"
FLASH = "FLASH"
PULSE = "PULSE"
BREATH = "BREATH"
SPECTRUM = "SPECTRUM"
WAVE = "WAVE"
STARLIGHT = "STARLIGHT"
FIRE = "FIRE"
REACTIVE = "REACTIVE"
RIPPLE = "RIPPLE"

# Effects that are valid while an ordinary ordered rule remains matched.
KNOWN_EFFECTS = (
    STATIC,
    FLASH,
    PULSE,
    BREATH,
    SPECTRUM,
    WAVE,
    STARLIGHT,
    FIRE,
)
TRIGGERED_EFFECTS = (REACTIVE, RIPPLE)

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
# REACTIVE reuses the already-persisted spatial selector field. The operator UI
# calls it Origin; no profile-format change is required.
REACTIVE_ORIGINS = (
    "AUTO",
    "EACH_SECTION_START",
    "EACH_SECTION_CENTER",
    "EACH_SECTION_END",
    "ZONE_START",
    "ZONE_END",
)


def _validate_unit(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{label} must be between 0 and 1")
    return value


def _validate_positive_optional(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric or None")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if value <= 0.0:
        raise ValueError(f"{label} must be greater than zero")
    return value


def _validate_timing_optional(value: float | None, label: str) -> float | None:
    value = _validate_positive_optional(value, label)
    if value is not None and value < MIN_EFFECT_TIMING_SECONDS:
        raise ValueError(
            f"{label} must be at least {MIN_EFFECT_TIMING_SECONDS:.2f} seconds when set"
        )
    return value


def _validate_positive_int_optional(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer or None")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return value


def _validate_direction_optional(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("direction must be a non-empty string or None")
    return value.strip().upper()


@dataclass(frozen=True)
class EffectParameters:
    """Persisted effect-local parameters.

    Legacy fields remain unchanged:
    - ``brightness``: generic output multiplier where meaningful.
    - ``step_seconds``: FLASH colour step; FIRE noise/flicker step.
    - ``cycle_seconds``: complete PULSE/BREATH/SPECTRUM/WAVE cycle and
      STARLIGHT twinkle period.
    - ``minimum_brightness`` / ``maximum_brightness``: PULSE endpoints.

    v4 adds spatial/trigger parameters without changing old profile meaning:
    - ``direction``: WAVE/RIPPLE direction, or REACTIVE Origin policy.
    - ``density``: STARLIGHT active-point density.
    - ``speed`` / ``width``: RIPPLE wavefront geometry.
    - ``duration_seconds``: REACTIVE/RIPPLE temporary-effect lifetime.
    - ``response_size``: REACTIVE local response size.
    """

    brightness: float = 1.0
    step_seconds: float | None = None
    cycle_seconds: float | None = None
    minimum_brightness: float | None = None
    maximum_brightness: float | None = None
    direction: str | None = None
    density: float | None = None
    speed: float | None = None
    width: float | None = None
    duration_seconds: float | None = None
    response_size: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "brightness", _validate_unit(self.brightness, "brightness"))
        object.__setattr__(
            self,
            "step_seconds",
            _validate_timing_optional(self.step_seconds, "step_seconds"),
        )
        object.__setattr__(
            self,
            "cycle_seconds",
            _validate_timing_optional(self.cycle_seconds, "cycle_seconds"),
        )
        if self.minimum_brightness is not None:
            object.__setattr__(
                self,
                "minimum_brightness",
                _validate_unit(self.minimum_brightness, "minimum_brightness"),
            )
        if self.maximum_brightness is not None:
            object.__setattr__(
                self,
                "maximum_brightness",
                _validate_unit(self.maximum_brightness, "maximum_brightness"),
            )
        if (self.minimum_brightness is None) != (self.maximum_brightness is None):
            raise ValueError(
                "minimum_brightness and maximum_brightness must both be set or both be None"
            )
        if (
            self.minimum_brightness is not None
            and self.maximum_brightness is not None
            and self.minimum_brightness > self.maximum_brightness
        ):
            raise ValueError("minimum_brightness must not exceed maximum_brightness")

        object.__setattr__(self, "direction", _validate_direction_optional(self.direction))
        if self.density is not None:
            object.__setattr__(self, "density", _validate_unit(self.density, "density"))
        object.__setattr__(self, "speed", _validate_positive_optional(self.speed, "speed"))
        object.__setattr__(self, "width", _validate_positive_optional(self.width, "width"))
        object.__setattr__(
            self,
            "duration_seconds",
            _validate_positive_optional(self.duration_seconds, "duration_seconds"),
        )
        object.__setattr__(
            self,
            "response_size",
            _validate_positive_int_optional(self.response_size, "response_size"),
        )


def colour_cardinality(effect: str) -> tuple[int, int | None]:
    """Return (minimum, maximum) meaningful persisted palette entries."""
    if effect in {STATIC, PULSE, SPECTRUM, REACTIVE}:
        # SPECTRUM keeps one compatibility colour because LightingRule still
        # carries a primary colour; the renderer ignores it and owns full hue.
        return 1, 1
    if effect in {BREATH, FLASH, WAVE, FIRE}:
        return 2, None
    if effect in {STARLIGHT, RIPPLE}:
        return 1, None
    return 1, None


def _present(parameters: EffectParameters, *names: str) -> tuple[str, ...]:
    return tuple(name for name in names if getattr(parameters, name) is not None)


def _reject_fields(
    effect: str,
    parameters: EffectParameters,
    *,
    allowed: set[str],
) -> None:
    optional_fields = {
        "step_seconds",
        "cycle_seconds",
        "minimum_brightness",
        "maximum_brightness",
        "direction",
        "density",
        "speed",
        "width",
        "duration_seconds",
        "response_size",
    }
    invalid = sorted(_present(parameters, *(optional_fields - allowed)))
    if invalid:
        raise ValueError(f"{effect} does not accept parameter(s): {', '.join(invalid)}")


def validate_effect_configuration(
    effect: str,
    colours: Palette,
    parameters: EffectParameters,
) -> None:
    """Reject colour/parameter combinations with no defined effect semantics."""
    if not isinstance(effect, str) or not effect.strip():
        raise ValueError("effect must be a non-empty string")
    if not isinstance(colours, tuple) or not colours:
        raise ValueError("effect palette must contain at least one colour")
    if not isinstance(parameters, EffectParameters):
        raise ValueError("effect_parameters must be EffectParameters")

    minimum, maximum = colour_cardinality(effect)
    count = len(colours)
    if count < minimum:
        raise ValueError(f"{effect} requires at least {minimum} colour(s); got {count}")
    if maximum is not None and count > maximum:
        if minimum == maximum:
            raise ValueError(f"{effect} requires exactly {minimum} colour(s); got {count}")
        raise ValueError(f"{effect} accepts at most {maximum} colour(s); got {count}")

    if effect == STATIC:
        _reject_fields(effect, parameters, allowed=set())
        return

    if effect == FLASH:
        _reject_fields(effect, parameters, allowed={"step_seconds"})
        return

    if effect == PULSE:
        if parameters.brightness != 1.0:
            raise ValueError(
                "PULSE uses minimum_brightness/maximum_brightness; generic brightness must remain 1"
            )
        _reject_fields(
            effect,
            parameters,
            allowed={"cycle_seconds", "minimum_brightness", "maximum_brightness"},
        )
        return

    if effect == BREATH:
        _reject_fields(effect, parameters, allowed={"cycle_seconds"})
        return

    if effect == SPECTRUM:
        _reject_fields(effect, parameters, allowed={"cycle_seconds"})
        return

    if effect == WAVE:
        _reject_fields(effect, parameters, allowed={"cycle_seconds", "direction"})
        if parameters.direction is not None and parameters.direction not in WAVE_DIRECTIONS:
            raise ValueError(f"WAVE direction must be one of {WAVE_DIRECTIONS}")
        return

    if effect == STARLIGHT:
        _reject_fields(effect, parameters, allowed={"cycle_seconds", "density"})
        return

    if effect == FIRE:
        _reject_fields(effect, parameters, allowed={"step_seconds"})
        return

    if effect == REACTIVE:
        _reject_fields(
            effect,
            parameters,
            allowed={"direction", "duration_seconds", "response_size"},
        )
        if parameters.direction is not None and parameters.direction not in REACTIVE_ORIGINS:
            raise ValueError(f"REACTIVE origin must be one of {REACTIVE_ORIGINS}")
        return

    if effect == RIPPLE:
        _reject_fields(
            effect,
            parameters,
            allowed={"direction", "speed", "width", "duration_seconds"},
        )
        if parameters.direction is not None and parameters.direction not in RIPPLE_DIRECTIONS:
            raise ValueError(f"RIPPLE direction must be one of {RIPPLE_DIRECTIONS}")
        return

    # Unknown/future effects keep the generic palette mechanism available, but
    # do not silently inherit timing/spatial semantics belonging to known effects.
    _reject_fields(effect, parameters, allowed=set())


def default_parameters_for_effect(effect: str) -> EffectParameters:
    """Return UI creation defaults; loading old data never calls this implicitly."""
    if effect == FLASH:
        return EffectParameters(brightness=1.0, step_seconds=0.5)
    if effect == PULSE:
        return EffectParameters(
            cycle_seconds=2.0,
            minimum_brightness=0.10,
            maximum_brightness=1.0,
        )
    if effect == BREATH:
        return EffectParameters(brightness=1.0, cycle_seconds=2.0)
    if effect == SPECTRUM:
        return EffectParameters(brightness=1.0, cycle_seconds=5.0)
    if effect == WAVE:
        return EffectParameters(
            brightness=1.0,
            cycle_seconds=4.0,
            direction="LEFT_TO_RIGHT",
        )
    if effect == STARLIGHT:
        return EffectParameters(
            brightness=1.0,
            cycle_seconds=1.35,
            density=0.34,
        )
    if effect == FIRE:
        return EffectParameters(brightness=1.0, step_seconds=0.10)
    if effect == REACTIVE:
        return EffectParameters(
            brightness=1.0,
            direction="AUTO",
            duration_seconds=0.95,
            response_size=1,
        )
    if effect == RIPPLE:
        return EffectParameters(
            brightness=1.0,
            direction="CENTER_OUT",
            speed=0.72,
            width=0.11,
            duration_seconds=1.55,
        )
    return EffectParameters(brightness=1.0)
