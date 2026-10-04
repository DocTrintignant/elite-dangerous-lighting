#!/usr/bin/env python3
"""VPC-style lighting-rule primitives and deterministic source evaluation.

This module owns source/target rule semantics only. It does not watch Elite
files, capture hardware input, talk to Chroma, import Virpil profiles, or own UI.

Implemented source slices:
- Argument: one or more canonical state conditions with AND semantics
- Keyboard: one keyboard combination, matched while every configured key is down
- Button: one HID device/button in Pressed or Released state
- Axis: one HID device/axis with the VPC comparison family plus BETWEEN

Rules retain deterministic top-to-bottom source order. A matching rule may
write one or more leaf targets; a later matching rule replaces the calculated
result only for each target it also writes.

``RuleOutput`` is the canonical per-output data boundary. Each output owns one
concrete target, effect, palette, effect parameters and enabled state. Existing
``LightingRule`` output fields remain as compatibility aliases while the runtime
and editor are migrated in small verified slices. Legacy one-effect/multi-target
rules synthesize equivalent RuleOutput values automatically.

The ordered evaluator preserves its historical ``dict[str, LightingRule]``
contract. Uniform legacy rules still return the original rule object unchanged.
A heterogeneous rule resolves each enabled output to a target-local
``LightingRule`` view whose output aliases contain only that output while
``source_rule`` points back to the original ordered source rule.

HID device identifiers and numeric axis values are intentionally opaque to this
module. A hardware adapter decides how physical devices are identified and how
axis samples are represented; the rule engine only compares the supplied value.

Effect-specific meaning is deliberately validated at the current-profile and
render-intent boundaries rather than inside these generic containers. That lets
legacy profile data be loaded faithfully even when it lacks parameters that a
current dynamic effect requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Iterable, Mapping

from lighting_axis_modulation import AxisOutputModulation, validate_output_modulation
from lighting_effect_config import EffectParameters

GLOBAL_TARGET = "GLOBAL"
STATIC_EFFECT = "STATIC"

SUPPORTED_OPERATORS = (
    "Less",
    "More",
    "Equal",
    "Not Equal",
    "More or Equal",
    "Less or Equal",
)
AXIS_BETWEEN_OPERATOR = "BETWEEN"
SUPPORTED_AXIS_OPERATORS = SUPPORTED_OPERATORS + (AXIS_BETWEEN_OPERATOR,)
BUTTON_STATES = ("Pressed", "Released")

RGB = tuple[int, int, int]
HidAddress = tuple[str, int]


def rgb_to_chroma_colorref(rgb: RGB) -> int:
    """Convert canonical RGB to the Windows COLORREF integer used by Chroma."""
    r, g, b = _validate_rgb(rgb)
    return r | (g << 8) | (b << 16)


def _validate_rgb(rgb: RGB) -> RGB:
    if not isinstance(rgb, tuple) or len(rgb) != 3:
        raise ValueError("colour must be an (R, G, B) tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in rgb):
        raise ValueError("RGB components must be integers")
    if any(value < 0 or value > 255 for value in rgb):
        raise ValueError("RGB components must be 0..255")
    return rgb


def _validate_colours(colours: tuple[RGB, ...]) -> tuple[RGB, ...]:
    if not isinstance(colours, tuple) or not colours:
        raise ValueError("colours must be a non-empty tuple of RGB colours")
    for colour in colours:
        _validate_rgb(colour)
    return colours


def _normalize_rule_targets(target: str, targets: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string")
    if not isinstance(targets, tuple):
        raise ValueError("targets must be a tuple")

    if targets:
        if any(not isinstance(value, str) or not value.strip() for value in targets):
            raise ValueError("targets must contain only non-empty strings")
        if target != GLOBAL_TARGET and target != targets[0]:
            raise ValueError("target compatibility alias must equal targets[0]")
        selected = targets
    else:
        selected = (target,)

    if len(set(selected)) != len(selected):
        raise ValueError("targets must not contain duplicates")
    if GLOBAL_TARGET in selected and selected != (GLOBAL_TARGET,):
        raise ValueError("GLOBAL is exclusive and cannot be combined with other targets")
    return selected


def _validate_output_targets(outputs: tuple["RuleOutput", ...]) -> tuple[str, ...]:
    if not isinstance(outputs, tuple) or not outputs:
        raise ValueError("outputs must be a non-empty tuple of RuleOutput values")
    if any(not isinstance(output, RuleOutput) for output in outputs):
        raise ValueError("outputs must contain only RuleOutput values")
    targets = tuple(output.target for output in outputs)
    if len(set(targets)) != len(targets):
        raise ValueError("rule outputs must not contain duplicate targets")
    if GLOBAL_TARGET in targets and targets != (GLOBAL_TARGET,):
        raise ValueError("GLOBAL is exclusive and cannot be combined with other outputs")
    return targets


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _validate_hid_device(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("HID device identifier must be a non-empty string")


def _validate_hid_index(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _normalize_key_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("keyboard key names must be non-empty strings")
    return value.strip().upper()


@dataclass(frozen=True)
class ArgumentCondition:
    """One VPC-style Argument condition against canonical Elite state."""

    source: str
    operator: str
    value: Any

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty canonical state key")
        if self.operator not in SUPPORTED_OPERATORS:
            raise ValueError(
                f"unsupported operator {self.operator!r}; "
                f"expected one of {SUPPORTED_OPERATORS}"
            )


@dataclass(frozen=True)
class KeyboardCombination:
    """One VPC-style keyboard combination.

    A combination matches while every configured key is currently down.
    Unrelated additional keys do not cancel the match.
    """

    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.keys, tuple) or not self.keys:
            raise ValueError("keyboard combination must contain at least one key")
        normalized = tuple(_normalize_key_name(key) for key in self.keys)
        if len(set(normalized)) != len(normalized):
            raise ValueError("keyboard combination must not contain duplicate keys")
        object.__setattr__(self, "keys", normalized)

    def matches(self, pressed_keys: Iterable[str]) -> bool:
        pressed = {_normalize_key_name(key) for key in pressed_keys}
        return all(key in pressed for key in self.keys)


@dataclass(frozen=True)
class ButtonSource:
    """One VPC-style HID Button source."""

    device: str
    button: int
    state: str

    def __post_init__(self) -> None:
        _validate_hid_device(self.device)
        _validate_hid_index(self.button, "button")
        if self.state not in BUTTON_STATES:
            raise ValueError(
                f"unsupported button state {self.state!r}; expected one of {BUTTON_STATES}"
            )

    def matches(self, button_states: Mapping[HidAddress, bool]) -> bool:
        address = (self.device, self.button)
        if address not in button_states:
            return False
        observed = button_states[address]
        if not isinstance(observed, bool):
            return False
        expected = self.state == "Pressed"
        return observed is expected


@dataclass(frozen=True)
class AxisSource:
    """One VPC-style HID Axis source."""

    device: str
    axis: int
    operator: str
    value: Real
    secondary_value: Real | None = None

    def __post_init__(self) -> None:
        _validate_hid_device(self.device)
        _validate_hid_index(self.axis, "axis")
        if self.operator not in SUPPORTED_AXIS_OPERATORS:
            raise ValueError(
                f"unsupported axis operator {self.operator!r}; "
                f"expected one of {SUPPORTED_AXIS_OPERATORS}"
            )
        if not _is_number(self.value):
            raise ValueError("axis primary value must be numeric")
        if self.secondary_value is not None and not _is_number(self.secondary_value):
            raise ValueError("axis secondary value must be numeric or None")
        if self.operator == AXIS_BETWEEN_OPERATOR and self.secondary_value is None:
            raise ValueError("BETWEEN requires a secondary axis value")

    def matches(self, axis_values: Mapping[HidAddress, Real]) -> bool:
        address = (self.device, self.axis)
        if address not in axis_values:
            return False
        observed = axis_values[address]
        if not _is_number(observed):
            return False

        if self.operator == AXIS_BETWEEN_OPERATOR:
            assert self.secondary_value is not None
            low = min(self.value, self.secondary_value)
            high = max(self.value, self.secondary_value)
            return low <= observed <= high

        return condition_matches(observed, self.operator, self.value)


@dataclass(frozen=True)
class RuleOutput:
    """One independently configurable output owned by a source rule."""

    target: str
    colour: RGB
    enabled: bool = True
    effect: str = STATIC_EFFECT
    colours: tuple[RGB, ...] = ()
    effect_parameters: EffectParameters = EffectParameters()
    axis_modulation: AxisOutputModulation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("output target must be a non-empty string")
        if not isinstance(self.enabled, bool):
            raise ValueError("output enabled must be boolean")
        if not isinstance(self.effect, str) or not self.effect.strip():
            raise ValueError("output effect must be a non-empty string")
        if not isinstance(self.effect_parameters, EffectParameters):
            raise ValueError("output effect_parameters must be EffectParameters")
        if self.axis_modulation is not None and not isinstance(
            self.axis_modulation, AxisOutputModulation
        ):
            raise ValueError("output axis_modulation must be AxisOutputModulation or None")
        validate_output_modulation(
            self.effect,
            self.effect_parameters.brightness,
            self.axis_modulation,
        )
        primary = _validate_rgb(self.colour)
        if self.colours:
            palette = _validate_colours(self.colours)
            if palette[0] != primary:
                raise ValueError("output colour must equal the first item in colours")
        else:
            object.__setattr__(self, "colours", (primary,))

    @property
    def chroma_colorref(self) -> int:
        return rgb_to_chroma_colorref(self.colour)


@dataclass(frozen=True)
class LightingRule:
    """One source rule plus one or more output definitions.

    The legacy output fields remain compatibility aliases during the staged
    migration. For ordinary v1-v4 style construction, ``outputs`` is synthesized
    from ``target(s)`` and the shared effect/palette. For a genuine independent-
    output rule, the aliases mirror the first output and ordered output targets.
    """

    conditions: tuple[ArgumentCondition, ...]
    colour: RGB
    enabled: bool = True
    effect: str = STATIC_EFFECT
    target: str = GLOBAL_TARGET
    name: str | None = None
    keyboard: KeyboardCombination | None = None
    button: ButtonSource | None = None
    axis: AxisSource | None = None
    colours: tuple[RGB, ...] = ()
    effect_parameters: EffectParameters = EffectParameters()
    axis_modulation: AxisOutputModulation | None = None
    targets: tuple[str, ...] = ()
    outputs: tuple[RuleOutput, ...] = ()
    _independent_outputs: bool = field(default=False, init=False, repr=False, compare=False)
    _source_rule: "LightingRule | None" = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.conditions, tuple):
            raise ValueError("conditions must be a tuple")
        if any(not isinstance(condition, ArgumentCondition) for condition in self.conditions):
            raise ValueError("conditions must contain only ArgumentCondition values")

        configured_sources = sum(
            (
                bool(self.conditions),
                self.keyboard is not None,
                self.button is not None,
                self.axis is not None,
            )
        )
        if configured_sources != 1:
            raise ValueError(
                "rule must define exactly one source: Argument, Keyboard, Button, or Axis"
            )

        if self.keyboard is not None and not isinstance(self.keyboard, KeyboardCombination):
            raise ValueError("keyboard must be None or KeyboardCombination")
        if self.button is not None and not isinstance(self.button, ButtonSource):
            raise ValueError("button must be None or ButtonSource")
        if self.axis is not None and not isinstance(self.axis, AxisSource):
            raise ValueError("axis must be None or AxisSource")
        if not isinstance(self.enabled, bool):
            raise ValueError("rule enabled must be boolean")

        primary = _validate_rgb(self.colour)
        if self.colours:
            palette = _validate_colours(self.colours)
            if palette[0] != primary:
                raise ValueError("colour must equal the first item in colours")
        else:
            palette = (primary,)
            object.__setattr__(self, "colours", palette)

        if not isinstance(self.effect_parameters, EffectParameters):
            raise ValueError("effect_parameters must be EffectParameters")
        if self.axis_modulation is not None and not isinstance(
            self.axis_modulation, AxisOutputModulation
        ):
            raise ValueError("axis_modulation must be AxisOutputModulation or None")
        validate_output_modulation(
            self.effect,
            self.effect_parameters.brightness,
            self.axis_modulation,
        )
        if not isinstance(self.effect, str) or not self.effect.strip():
            raise ValueError("effect must be a non-empty string")
        selected_targets = _normalize_rule_targets(self.target, self.targets)
        object.__setattr__(self, "targets", selected_targets)
        object.__setattr__(self, "target", selected_targets[0])
        if self.name is not None and (
            not isinstance(self.name, str) or not self.name.strip()
        ):
            raise ValueError("name must be None or a non-empty string")

        if not self.outputs:
            synthesized = tuple(
                RuleOutput(
                    target=value,
                    colour=primary,
                    enabled=True,
                    effect=self.effect,
                    colours=palette,
                    effect_parameters=self.effect_parameters,
                    axis_modulation=self.axis_modulation,
                )
                for value in selected_targets
            )
            object.__setattr__(self, "outputs", synthesized)
            object.__setattr__(self, "_independent_outputs", False)
            return

        output_targets = _validate_output_targets(self.outputs)
        first = self.outputs[0]
        first_config_matches_aliases = (
            first.target == selected_targets[0]
            and first.colour == primary
            and first.colours == palette
            and first.effect == self.effect
            and first.effect_parameters == self.effect_parameters
            and first.axis_modulation == self.axis_modulation
        )
        targets_match_aliases = output_targets == selected_targets
        uniform_outputs = all(
            output.enabled
            and output.effect == first.effect
            and output.colour == first.colour
            and output.colours == first.colours
            and output.effect_parameters == first.effect_parameters
            and output.axis_modulation == first.axis_modulation
            for output in self.outputs
        )

        if uniform_outputs:
            if targets_match_aliases and first_config_matches_aliases:
                object.__setattr__(self, "_independent_outputs", False)
                return
            # Existing code frequently uses dataclasses.replace() on the legacy
            # aliases. If this is a legacy/uniform rule, those aliases remain the
            # authoritative compatibility surface and outputs are regenerated.
            synthesized = tuple(
                RuleOutput(
                    target=value,
                    colour=primary,
                    enabled=True,
                    effect=self.effect,
                    colours=palette,
                    effect_parameters=self.effect_parameters,
                    axis_modulation=self.axis_modulation,
                )
                for value in selected_targets
            )
            object.__setattr__(self, "outputs", synthesized)
            object.__setattr__(self, "_independent_outputs", False)
            return

        # Independent output rules cannot be safely edited through old shared
        # aliases. Require those aliases to mirror the first/ordered output data
        # so a stale legacy replace() cannot silently corrupt the new model.
        if not targets_match_aliases or not first_config_matches_aliases:
            raise ValueError(
                "independent outputs require compatibility aliases to mirror the first output and ordered output targets"
            )
        object.__setattr__(self, "_independent_outputs", True)

    @property
    def chroma_colorref(self) -> int:
        return rgb_to_chroma_colorref(self.colour)

    @property
    def source_type(self) -> str:
        if self.keyboard is not None:
            return "Keyboard"
        if self.button is not None:
            return "Button"
        if self.axis is not None:
            return "Axis"
        return "Argument"

    @property
    def has_independent_outputs(self) -> bool:
        """True when outputs cannot be represented by the legacy shared config."""
        return self._independent_outputs

    @property
    def source_rule(self) -> "LightingRule":
        """Original ordered source rule for a target-local resolved output view."""
        return self._source_rule if self._source_rule is not None else self

    def resolved_output_rule(self, output: RuleOutput) -> "LightingRule":
        """Return the runtime-compatible target-local view for one enabled output."""
        if not isinstance(output, RuleOutput) or output not in self.outputs:
            raise ValueError("output must belong to this rule")
        if not output.enabled:
            raise ValueError("disabled outputs do not resolve to runtime output rules")
        if not self.has_independent_outputs:
            return self

        resolved = LightingRule(
            conditions=self.conditions,
            colour=output.colour,
            enabled=self.enabled,
            effect=output.effect,
            target=output.target,
            name=self.name,
            keyboard=self.keyboard,
            button=self.button,
            axis=self.axis,
            colours=output.colours,
            effect_parameters=output.effect_parameters,
            axis_modulation=output.axis_modulation,
            targets=(output.target,),
            outputs=(output,),
        )
        object.__setattr__(resolved, "_source_rule", self.source_rule)
        return resolved


def condition_matches(observed: Any, operator: str, expected: Any) -> bool:
    """Evaluate one VPC-style condition without string/numeric coercion."""
    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"unsupported operator {operator!r}")

    if operator in {"Equal", "Not Equal"}:
        if isinstance(observed, bool) or isinstance(expected, bool):
            equal = (
                isinstance(observed, bool)
                and isinstance(expected, bool)
                and observed is expected
            )
        else:
            equal = observed == expected
        return equal if operator == "Equal" else not equal

    if not _is_number(observed) or not _is_number(expected):
        return False

    if operator == "Less":
        return observed < expected
    if operator == "More":
        return observed > expected
    if operator == "More or Equal":
        return observed >= expected
    if operator == "Less or Equal":
        return observed <= expected

    raise AssertionError("unreachable operator branch")


def condition_matches_state(
    condition: ArgumentCondition, state: Mapping[str, Any]
) -> bool:
    if condition.source not in state:
        return False
    return condition_matches(
        state[condition.source],
        condition.operator,
        condition.value,
    )


def rule_matches(
    rule: LightingRule,
    state: Mapping[str, Any],
    *,
    pressed_keys: Iterable[str] = (),
    button_states: Mapping[HidAddress, bool] | None = None,
    axis_values: Mapping[HidAddress, Real] | None = None,
) -> bool:
    if not rule.enabled:
        return False
    if rule.keyboard is not None:
        return rule.keyboard.matches(pressed_keys)
    if rule.button is not None:
        return rule.button.matches(button_states or {})
    if rule.axis is not None:
        return rule.axis.matches(axis_values or {})
    return all(
        condition_matches_state(condition, state)
        for condition in rule.conditions
    )


def matching_rules(
    state: Mapping[str, Any],
    rules: Iterable[LightingRule],
    *,
    pressed_keys: Iterable[str] = (),
    button_states: Mapping[HidAddress, bool] | None = None,
    axis_values: Mapping[HidAddress, Real] | None = None,
) -> list[LightingRule]:
    pressed = tuple(pressed_keys)
    buttons = button_states or {}
    axes = axis_values or {}
    return [
        rule
        for rule in rules
        if rule_matches(
            rule,
            state,
            pressed_keys=pressed,
            button_states=buttons,
            axis_values=axes,
        )
    ]


def calculate_outputs(
    state: Mapping[str, Any],
    rules: Iterable[LightingRule],
    *,
    pressed_keys: Iterable[str] = (),
    button_states: Mapping[HidAddress, bool] | None = None,
    axis_values: Mapping[HidAddress, Real] | None = None,
) -> dict[str, LightingRule]:
    outputs: dict[str, LightingRule] = {}
    pressed = tuple(pressed_keys)
    buttons = button_states or {}
    axes = axis_values or {}
    for rule in rules:
        if rule_matches(
            rule,
            state,
            pressed_keys=pressed,
            button_states=buttons,
            axis_values=axes,
        ):
            for output in rule.outputs:
                if not output.enabled:
                    continue
                outputs[output.target] = rule.resolved_output_rule(output)
    return outputs


def evaluate_rules(
    state: Mapping[str, Any],
    rules: Iterable[LightingRule],
    *,
    target: str = GLOBAL_TARGET,
    pressed_keys: Iterable[str] = (),
    button_states: Mapping[HidAddress, bool] | None = None,
    axis_values: Mapping[HidAddress, Real] | None = None,
) -> LightingRule | None:
    return calculate_outputs(
        state,
        rules,
        pressed_keys=pressed_keys,
        button_states=button_states,
        axis_values=axis_values,
    ).get(target)
