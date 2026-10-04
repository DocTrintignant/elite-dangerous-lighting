#!/usr/bin/env python3
"""Logical render intent between calculated rules and effect generation.

The intent owns renderer-facing effect parameters while source matching remains
separate. Ordered palettes are preserved, and current effect semantics are
validated before rendering.
"""

from __future__ import annotations

from dataclasses import dataclass

from lighting_compositor import BLEND, REPLACE, SUPPORTED_COMPOSITIONS
from lighting_effect_config import EffectParameters, validate_effect_configuration
from lighting_rules import LightingRule

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


def _validate_palette(colours: Palette) -> Palette:
    if not isinstance(colours, tuple) or not colours:
        raise ValueError("intent colours must be a non-empty tuple")
    for colour in colours:
        _validate_rgb(colour)
    return colours


def _validate_unit(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{label} must be between 0 and 1")
    return value


@dataclass(frozen=True)
class FlashParameters:
    """Optional native FLASH step duration.

    ``None`` deliberately means cadence is unspecified. This is required for
    faithful Virpil import, where Flashing carries no persisted real-time rate.
    """

    step_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.step_seconds is None:
            return
        if isinstance(self.step_seconds, bool) or not isinstance(
            self.step_seconds, (int, float)
        ):
            raise ValueError("FLASH step_seconds must be numeric or None")
        if self.step_seconds <= 0.0:
            raise ValueError("FLASH step_seconds must be greater than zero")


@dataclass(frozen=True)
class PulseParameters:
    period_seconds: float
    minimum_intensity: float
    maximum_intensity: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.period_seconds, bool) or not isinstance(
            self.period_seconds, (int, float)
        ):
            raise ValueError("PULSE period_seconds must be numeric")
        if self.period_seconds <= 0.0:
            raise ValueError("PULSE period_seconds must be greater than zero")
        minimum = _validate_unit(self.minimum_intensity, "PULSE minimum_intensity")
        maximum = _validate_unit(self.maximum_intensity, "PULSE maximum_intensity")
        if minimum > maximum:
            raise ValueError("PULSE minimum_intensity must not exceed maximum_intensity")


@dataclass(frozen=True)
class BreathParameters:
    period_seconds: float

    def __post_init__(self) -> None:
        if isinstance(self.period_seconds, bool) or not isinstance(
            self.period_seconds, (int, float)
        ):
            raise ValueError("BREATH period_seconds must be numeric")
        if self.period_seconds <= 0.0:
            raise ValueError("BREATH period_seconds must be greater than zero")


@dataclass(frozen=True)
class LightingIntent:
    target: str
    effect: str
    color_one: RGB
    color_two: RGB | None = None
    composition: str = REPLACE
    blend_opacity: float | None = None
    pulse: PulseParameters | None = None
    breath: BreathParameters | None = None
    colours: Palette = ()
    flash: FlashParameters | None = None
    brightness: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("intent target must be a non-empty string")
        if not isinstance(self.effect, str) or not self.effect.strip():
            raise ValueError("intent effect must be a non-empty string")

        primary = _validate_rgb(self.color_one)
        if self.color_two is not None:
            _validate_rgb(self.color_two)

        if self.colours:
            palette = _validate_palette(self.colours)
            if palette[0] != primary:
                raise ValueError("color_one must equal the first item in colours")
            if self.color_two is not None:
                if len(palette) < 2 or palette[1] != self.color_two:
                    raise ValueError("color_two must equal the second item in colours")
        else:
            palette = (primary,) if self.color_two is None else (primary, self.color_two)
            object.__setattr__(self, "colours", palette)

        brightness = _validate_unit(self.brightness, "intent brightness")
        object.__setattr__(self, "brightness", brightness)

        if self.composition not in SUPPORTED_COMPOSITIONS:
            raise ValueError(
                f"unsupported composition {self.composition!r}; "
                f"expected one of {SUPPORTED_COMPOSITIONS}"
            )
        if self.composition == BLEND:
            if isinstance(self.blend_opacity, bool) or not isinstance(
                self.blend_opacity, (int, float)
            ):
                raise ValueError("BLEND intent requires numeric blend_opacity")
            if self.blend_opacity < 0.0 or self.blend_opacity > 1.0:
                raise ValueError("blend_opacity must be between 0 and 1")
        elif self.blend_opacity is not None:
            raise ValueError("blend_opacity is only valid for BLEND composition")

        if self.effect == "STATIC":
            validate_effect_configuration(
                self.effect,
                palette,
                EffectParameters(brightness=brightness),
            )

        if self.effect == "FLASH":
            if self.color_two is None and len(palette) >= 2:
                object.__setattr__(self, "color_two", palette[1])
            if self.flash is not None and not isinstance(self.flash, FlashParameters):
                raise ValueError("flash must be None or FlashParameters")
            validate_effect_configuration(
                self.effect,
                palette,
                EffectParameters(
                    brightness=brightness,
                    step_seconds=(self.flash.step_seconds if self.flash else None),
                ),
            )
        elif self.flash is not None:
            raise ValueError("FlashParameters are only valid for FLASH intent")

        if self.effect == "PULSE":
            if self.color_two is not None:
                raise ValueError("PULSE uses one colour only")
            if not isinstance(self.pulse, PulseParameters):
                raise ValueError("PULSE intent requires explicit PulseParameters")
            if brightness != 1.0:
                raise ValueError(
                    "PULSE uses minimum/maximum intensity; generic brightness must remain 1"
                )
            validate_effect_configuration(
                self.effect,
                palette,
                EffectParameters(
                    cycle_seconds=self.pulse.period_seconds,
                    minimum_brightness=self.pulse.minimum_intensity,
                    maximum_brightness=self.pulse.maximum_intensity,
                ),
            )
        elif self.pulse is not None:
            raise ValueError("PulseParameters are only valid for PULSE intent")

        if self.effect == "BREATH":
            if self.color_two is None and len(palette) >= 2:
                object.__setattr__(self, "color_two", palette[1])
            if not isinstance(self.breath, BreathParameters):
                raise ValueError("BREATH intent requires explicit BreathParameters")
            validate_effect_configuration(
                self.effect,
                palette,
                EffectParameters(
                    brightness=brightness,
                    cycle_seconds=self.breath.period_seconds,
                ),
            )
        elif self.breath is not None:
            raise ValueError("BreathParameters are only valid for BREATH intent")


def intent_from_rule(
    rule: LightingRule,
    *,
    color_two: RGB | None = None,
    colours: Palette | None = None,
    composition: str = REPLACE,
    blend_opacity: float | None = None,
    pulse: PulseParameters | None = None,
    breath: BreathParameters | None = None,
    flash: FlashParameters | None = None,
) -> LightingIntent:
    """Adapt one calculated LightingRule into renderer intent.

    Explicit keyword parameters remain supported for diagnostic/legacy call
    sites. Validation applies to the effective palette/parameters after those
    overrides are resolved.
    """
    if not isinstance(rule, LightingRule):
        raise ValueError("rule must be a LightingRule")

    palette = tuple(rule.colours if colours is None else colours)
    if not palette:
        raise ValueError("intent palette override must contain at least one colour")

    secondary = color_two
    if secondary is not None:
        if len(palette) <= 1:
            palette = (palette[0], secondary)
        else:
            palette = (palette[0], secondary, *palette[2:])
    elif len(palette) >= 2 and rule.effect in {"FLASH", "BREATH"}:
        secondary = palette[1]

    parameters = rule.effect_parameters

    if flash is None and rule.effect == "FLASH" and parameters.step_seconds is not None:
        flash = FlashParameters(parameters.step_seconds)

    if pulse is None and rule.effect == "PULSE":
        if (
            parameters.cycle_seconds is not None
            and parameters.minimum_brightness is not None
            and parameters.maximum_brightness is not None
        ):
            pulse = PulseParameters(
                period_seconds=parameters.cycle_seconds,
                minimum_intensity=parameters.minimum_brightness,
                maximum_intensity=parameters.maximum_brightness,
            )

    if breath is None and rule.effect == "BREATH" and parameters.cycle_seconds is not None:
        breath = BreathParameters(parameters.cycle_seconds)

    if rule.effect == "STATIC":
        effective_parameters = EffectParameters(brightness=parameters.brightness)
    elif rule.effect == "FLASH":
        effective_parameters = EffectParameters(
            brightness=parameters.brightness,
            step_seconds=(flash.step_seconds if flash else None),
        )
    elif rule.effect == "PULSE" and pulse is not None:
        effective_parameters = EffectParameters(
            cycle_seconds=pulse.period_seconds,
            minimum_brightness=pulse.minimum_intensity,
            maximum_brightness=pulse.maximum_intensity,
        )
    elif rule.effect == "BREATH" and breath is not None:
        effective_parameters = EffectParameters(
            brightness=parameters.brightness,
            cycle_seconds=breath.period_seconds,
        )
    else:
        effective_parameters = parameters

    validate_effect_configuration(rule.effect, palette, effective_parameters)
    brightness = 1.0 if rule.effect == "PULSE" else parameters.brightness

    return LightingIntent(
        target=rule.target,
        effect=rule.effect,
        color_one=palette[0],
        color_two=secondary,
        colours=palette,
        composition=composition,
        blend_opacity=blend_opacity,
        pulse=pulse,
        breath=breath,
        flash=flash,
        brightness=brightness,
    )


def intent_from_virpil_runtime(
    runtime: object,
    *,
    composition: str = REPLACE,
    blend_opacity: float | None = None,
) -> LightingIntent:
    """Adapt Virpil output without inventing Flashing cadence."""
    try:
        rule = runtime.rule
        primary = runtime.primary_colour
        secondary = runtime.secondary_colour
    except AttributeError as exc:
        raise ValueError("runtime is not a Virpil-compatible runtime rule") from exc

    if not isinstance(rule, LightingRule):
        raise ValueError("Virpil runtime rule must contain a LightingRule")

    if rule.effect == "FLASH":
        palette = (primary, secondary)
        color_two = secondary
    else:
        palette = (primary,)
        color_two = None

    return LightingIntent(
        target=rule.target,
        effect=rule.effect,
        color_one=primary,
        color_two=color_two,
        colours=palette,
        composition=composition,
        blend_opacity=blend_opacity,
        flash=None,
        brightness=1.0,
    )
