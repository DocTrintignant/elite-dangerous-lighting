#!/usr/bin/env python3
"""Native profile persistence and default/reset semantics.

Native profile evolution:
- v1: one colour + one target per rule;
- v2: ordered palettes + effect parameters;
- v3: ordered multi-target rules with one shared output configuration;
- v4: accepted spatial/continuous effect parameters;
- v5: ordered independent ``outputs`` per source rule;
- v6: optional HID-axis modulation per output.

Existing v1/v2/v3/v4/v5 files remain readable without inventing new source or
output semantics. New saves write v6. Older shared-output rules are normalized
into one explicit RuleOutput per legacy target while preserving their order and
configuration exactly.

Defaults remain one-target fallback outputs. They are intentionally not folded
into the rule-output model because fallback authority is a separate profile
concept from source-triggered rule outputs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from lighting_axis_modulation import AxisOutputModulation
from lighting_effect_config import EffectParameters, validate_effect_configuration
from lighting_rules import (
    GLOBAL_TARGET,
    STATIC_EFFECT,
    ArgumentCondition,
    AxisSource,
    ButtonSource,
    HidAddress,
    KeyboardCombination,
    LightingRule,
    RuleOutput,
    calculate_outputs,
)

PROFILE_FORMAT = "elite-dangerous-lighting-profile"
PROFILE_VERSION = 6
SUPPORTED_PROFILE_VERSIONS = (1, 2, 3, 4, 5, 6)
RGB = tuple[int, int, int]
Palette = tuple[RGB, ...]

_LEGACY_PARAMETER_FIELDS = {
    "brightness",
    "step_seconds",
    "cycle_seconds",
    "minimum_brightness",
    "maximum_brightness",
}
_V4_PARAMETER_FIELDS = _LEGACY_PARAMETER_FIELDS | {
    "direction",
    "density",
    "speed",
    "width",
    "duration_seconds",
    "response_size",
}


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
        raise ValueError("colours must contain at least one RGB colour")
    for colour in colours:
        _validate_rgb(colour)
    return colours


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    label: str,
) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ValueError(f"{label} missing required field(s): {sorted(missing)}")
    if unknown:
        raise ValueError(f"{label} has unknown field(s): {sorted(unknown)}")


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in value.items()
        )
    return False


def _require_json_value(value: Any, label: str) -> Any:
    if not _is_json_value(value):
        raise ValueError(
            f"{label} is not losslessly representable in a native JSON profile"
        )
    return value


def _rgb_to_json(rgb: RGB) -> list[int]:
    return list(_validate_rgb(rgb))


def _rgb_from_json(value: Any, label: str) -> RGB:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must be a three-item RGB array")
    rgb = tuple(value)
    return _validate_rgb(rgb)  # type: ignore[arg-type]


def _palette_to_json(colours: Palette) -> list[list[int]]:
    return [_rgb_to_json(colour) for colour in _validate_palette(colours)]


def _palette_from_json(value: Any, label: str) -> Palette:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty array of RGB arrays")
    return tuple(
        _rgb_from_json(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    )


def _targets_from_json(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty array of target strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must contain only non-empty strings")
    return tuple(value)


def _parameters_to_dict(parameters: EffectParameters) -> dict[str, Any]:
    if not isinstance(parameters, EffectParameters):
        raise ValueError("effect parameters must be EffectParameters")
    return {
        "brightness": parameters.brightness,
        "step_seconds": parameters.step_seconds,
        "cycle_seconds": parameters.cycle_seconds,
        "minimum_brightness": parameters.minimum_brightness,
        "maximum_brightness": parameters.maximum_brightness,
        "direction": parameters.direction,
        "density": parameters.density,
        "speed": parameters.speed,
        "width": parameters.width,
        "duration_seconds": parameters.duration_seconds,
        "response_size": parameters.response_size,
    }


def _parameters_from_dict(
    value: Any,
    label: str,
    *,
    version: int,
) -> EffectParameters:
    data = _require_object(value, label)
    fields = _V4_PARAMETER_FIELDS if version >= 4 else _LEGACY_PARAMETER_FIELDS
    _require_exact_keys(data, required=fields, label=label)
    return EffectParameters(
        brightness=data["brightness"],
        step_seconds=data["step_seconds"],
        cycle_seconds=data["cycle_seconds"],
        minimum_brightness=data["minimum_brightness"],
        maximum_brightness=data["maximum_brightness"],
        direction=data.get("direction"),
        density=data.get("density"),
        speed=data.get("speed"),
        width=data.get("width"),
        duration_seconds=data.get("duration_seconds"),
        response_size=data.get("response_size"),
    )


@dataclass(frozen=True)
class ProfileDefault:
    """One explicit fallback output for a logical target."""

    target: str
    colour: RGB
    effect: str = STATIC_EFFECT
    colours: Palette = ()
    effect_parameters: EffectParameters = EffectParameters()

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("default target must be a non-empty string")
        if not isinstance(self.effect, str) or not self.effect.strip():
            raise ValueError("default effect must be a non-empty string")
        if not isinstance(self.effect_parameters, EffectParameters):
            raise ValueError("default effect_parameters must be EffectParameters")
        primary = _validate_rgb(self.colour)
        if self.colours:
            palette = _validate_palette(self.colours)
            if palette[0] != primary:
                raise ValueError("default colour must equal the first item in colours")
        else:
            object.__setattr__(self, "colours", (primary,))


ProfileCalculatedOutput = LightingRule | ProfileDefault


@dataclass(frozen=True)
class NativeLightingProfile:
    """One native, ordered lighting profile."""

    name: str
    rules: tuple[LightingRule, ...]
    defaults: tuple[ProfileDefault, ...]
    reset_before_start: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("profile name must be a non-empty string")
        if not isinstance(self.rules, tuple) or any(
            not isinstance(rule, LightingRule) for rule in self.rules
        ):
            raise ValueError("profile rules must be a tuple of LightingRule values")
        if not isinstance(self.defaults, tuple) or not self.defaults:
            raise ValueError("profile must define at least one explicit default output")
        if any(not isinstance(default, ProfileDefault) for default in self.defaults):
            raise ValueError("profile defaults must contain only ProfileDefault values")
        targets = [default.target for default in self.defaults]
        if len(set(targets)) != len(targets):
            raise ValueError("profile default targets must be unique")
        if not isinstance(self.reset_before_start, bool):
            raise ValueError("reset_before_start must be boolean")


def make_default_profile(name: str = "Default") -> NativeLightingProfile:
    return NativeLightingProfile(
        name=name,
        rules=(),
        defaults=(ProfileDefault(GLOBAL_TARGET, (0, 0, 0), STATIC_EFFECT),),
        reset_before_start=True,
    )


def default_outputs(profile: NativeLightingProfile) -> dict[str, ProfileDefault]:
    return {default.target: default for default in profile.defaults}


def startup_reset_outputs(profile: NativeLightingProfile) -> dict[str, ProfileDefault]:
    if not profile.reset_before_start:
        return {}
    return default_outputs(profile)


def calculate_profile_outputs(
    profile: NativeLightingProfile,
    state: Mapping[str, Any],
    *,
    pressed_keys: Iterable[str] = (),
    button_states: Mapping[HidAddress, bool] | None = None,
    axis_values: Mapping[HidAddress, float] | None = None,
) -> dict[str, ProfileCalculatedOutput]:
    outputs: dict[str, ProfileCalculatedOutput] = dict(default_outputs(profile))
    outputs.update(
        calculate_outputs(
            state,
            profile.rules,
            pressed_keys=pressed_keys,
            button_states=button_states,
            axis_values=axis_values,
        )
    )
    return outputs


def _condition_to_dict(condition: ArgumentCondition) -> dict[str, Any]:
    return {
        "source": condition.source,
        "operator": condition.operator,
        "value": _require_json_value(condition.value, "Argument condition value"),
    }


def _condition_from_dict(value: Any) -> ArgumentCondition:
    data = _require_object(value, "Argument condition")
    _require_exact_keys(
        data,
        required={"source", "operator", "value"},
        label="Argument condition",
    )
    return ArgumentCondition(
        source=data["source"],
        operator=data["operator"],
        value=_require_json_value(data["value"], "Argument condition value"),
    )


def _source_to_dict(rule: LightingRule) -> dict[str, Any]:
    if rule.keyboard is not None:
        return {"type": "Keyboard", "keys": list(rule.keyboard.keys)}
    if rule.button is not None:
        return {
            "type": "Button",
            "device": rule.button.device,
            "button": rule.button.button,
            "state": rule.button.state,
        }
    if rule.axis is not None:
        return {
            "type": "Axis",
            "device": rule.axis.device,
            "axis": rule.axis.axis,
            "operator": rule.axis.operator,
            "value": rule.axis.value,
            "secondary_value": rule.axis.secondary_value,
        }
    return {
        "type": "Argument",
        "conditions": [_condition_to_dict(condition) for condition in rule.conditions],
    }


def _source_from_dict(value: Any) -> dict[str, Any]:
    data = _require_object(value, "rule source")
    source_type = data.get("type")

    if source_type == "Argument":
        _require_exact_keys(data, required={"type", "conditions"}, label="Argument source")
        conditions = data["conditions"]
        if not isinstance(conditions, list) or not conditions:
            raise ValueError("Argument source conditions must be a non-empty array")
        return {"conditions": tuple(_condition_from_dict(item) for item in conditions)}

    if source_type == "Keyboard":
        _require_exact_keys(data, required={"type", "keys"}, label="Keyboard source")
        keys = data["keys"]
        if not isinstance(keys, list):
            raise ValueError("Keyboard source keys must be an array")
        return {"conditions": (), "keyboard": KeyboardCombination(tuple(keys))}

    if source_type == "Button":
        _require_exact_keys(
            data,
            required={"type", "device", "button", "state"},
            label="Button source",
        )
        return {
            "conditions": (),
            "button": ButtonSource(data["device"], data["button"], data["state"]),
        }

    if source_type == "Axis":
        _require_exact_keys(
            data,
            required={"type", "device", "axis", "operator", "value", "secondary_value"},
            label="Axis source",
        )
        return {
            "conditions": (),
            "axis": AxisSource(
                device=data["device"],
                axis=data["axis"],
                operator=data["operator"],
                value=data["value"],
                secondary_value=data["secondary_value"],
            ),
        }

    raise ValueError(f"unsupported native rule source type: {source_type!r}")



def _axis_modulation_to_dict(
    modulation: AxisOutputModulation | None,
) -> dict[str, Any] | None:
    if modulation is None:
        return None
    return {
        "device": modulation.device,
        "axis": modulation.axis,
        "rest_position": modulation.rest_position,
        "rest_zone": modulation.rest_zone,
        "brightness_at_rest": modulation.brightness_at_rest,
        "brightness_minus_change": modulation.brightness_minus_change,
        "brightness_plus_change": modulation.brightness_plus_change,
        "colour_minus": (
            None if modulation.colour_minus is None else _rgb_to_json(modulation.colour_minus)
        ),
        "colour_plus": (
            None if modulation.colour_plus is None else _rgb_to_json(modulation.colour_plus)
        ),
        "colour_transition": modulation.colour_transition,
    }


def _axis_modulation_from_dict(value: Any) -> AxisOutputModulation | None:
    if value is None:
        return None
    data = _require_object(value, "rule output axis_modulation")
    _require_exact_keys(
        data,
        required={
            "device",
            "axis",
            "rest_position",
            "rest_zone",
            "brightness_at_rest",
            "brightness_minus_change",
            "brightness_plus_change",
            "colour_minus",
            "colour_plus",
            "colour_transition",
        },
        label="rule output axis_modulation",
    )
    colour_minus = (
        None
        if data["colour_minus"] is None
        else _rgb_from_json(data["colour_minus"], "axis modulation colour_minus")
    )
    colour_plus = (
        None
        if data["colour_plus"] is None
        else _rgb_from_json(data["colour_plus"], "axis modulation colour_plus")
    )
    return AxisOutputModulation(
        device=data["device"],
        axis=data["axis"],
        rest_position=data["rest_position"],
        rest_zone=data["rest_zone"],
        brightness_at_rest=data["brightness_at_rest"],
        brightness_minus_change=data["brightness_minus_change"],
        brightness_plus_change=data["brightness_plus_change"],
        colour_minus=colour_minus,
        colour_plus=colour_plus,
        colour_transition=data["colour_transition"],
    )


def _output_to_dict(output: RuleOutput) -> dict[str, Any]:
    if not isinstance(output, RuleOutput):
        raise ValueError("rule output must be RuleOutput")
    validate_effect_configuration(output.effect, output.colours, output.effect_parameters)
    return {
        "target": output.target,
        "enabled": output.enabled,
        "effect": output.effect,
        "colours": _palette_to_json(output.colours),
        "effect_parameters": _parameters_to_dict(output.effect_parameters),
        "axis_modulation": _axis_modulation_to_dict(output.axis_modulation),
    }


def _output_from_dict(value: Any, *, version: int) -> RuleOutput:
    data = _require_object(value, "rule output")
    required = {"target", "enabled", "effect", "colours", "effect_parameters"}
    if version >= 6:
        required.add("axis_modulation")
    _require_exact_keys(data, required=required, label="rule output")
    if not isinstance(data["target"], str) or not data["target"].strip():
        raise ValueError("rule output target must be a non-empty string")
    if not isinstance(data["enabled"], bool):
        raise ValueError("rule output enabled must be boolean")
    if not isinstance(data["effect"], str) or not data["effect"].strip():
        raise ValueError("rule output effect must be a non-empty string")
    palette = _palette_from_json(data["colours"], "rule output colours")
    parameters = _parameters_from_dict(
        data["effect_parameters"],
        "rule output effect_parameters",
        version=version,
    )
    validate_effect_configuration(data["effect"], palette, parameters)
    return RuleOutput(
        target=data["target"],
        enabled=data["enabled"],
        effect=data["effect"],
        colour=palette[0],
        colours=palette,
        effect_parameters=parameters,
        axis_modulation=(
            _axis_modulation_from_dict(data["axis_modulation"])
            if version >= 6
            else None
        ),
    )


def _rule_to_dict(rule: LightingRule) -> dict[str, Any]:
    if not isinstance(rule, LightingRule):
        raise ValueError("rule must be LightingRule")
    return {
        "name": rule.name,
        "enabled": rule.enabled,
        "outputs": [_output_to_dict(output) for output in rule.outputs],
        "source": _source_to_dict(rule),
    }


def _rule_from_dict(value: Any, version: int) -> LightingRule:
    data = _require_object(value, "rule")
    if data.get("name") is not None and not isinstance(data.get("name"), str):
        raise ValueError("rule name must be null or a string")
    if not isinstance(data.get("enabled"), bool):
        raise ValueError("rule enabled must be boolean")

    source_fields = _source_from_dict(data.get("source"))

    if version >= 5:
        _require_exact_keys(
            data,
            required={"name", "enabled", "outputs", "source"},
            label="rule",
        )
        values = data["outputs"]
        if not isinstance(values, list) or not values:
            raise ValueError("rule outputs must be a non-empty array")
        outputs = tuple(_output_from_dict(item, version=version) for item in values)
        first = outputs[0]
        return LightingRule(
            colour=first.colour,
            colours=first.colours,
            effect_parameters=first.effect_parameters,
            axis_modulation=first.axis_modulation,
            enabled=data["enabled"],
            effect=first.effect,
            target=first.target,
            targets=tuple(output.target for output in outputs),
            outputs=outputs,
            name=data["name"],
            **source_fields,
        )

    colour_field = "colour" if version == 1 else "colours"
    target_field = "targets" if version >= 3 else "target"
    optional = {"effect_parameters"} if version >= 2 else set()
    _require_exact_keys(
        data,
        required={"name", "enabled", target_field, "effect", colour_field, "source"},
        optional=optional,
        label="rule",
    )

    if version == 1:
        primary = _rgb_from_json(data["colour"], "rule colour")
        palette = (primary,)
        parameters = EffectParameters()
    else:
        palette = _palette_from_json(data["colours"], "rule colours")
        primary = palette[0]
        parameters = (
            _parameters_from_dict(
                data["effect_parameters"],
                "rule effect_parameters",
                version=version,
            )
            if "effect_parameters" in data
            else EffectParameters()
        )
        validate_effect_configuration(data["effect"], palette, parameters)

    target_fields = (
        {"targets": _targets_from_json(data["targets"], "rule targets")}
        if version >= 3
        else {"target": data["target"]}
    )
    return LightingRule(
        colour=primary,
        colours=palette,
        effect_parameters=parameters,
        enabled=data["enabled"],
        effect=data["effect"],
        name=data["name"],
        **target_fields,
        **source_fields,
    )


def _default_to_dict(default: ProfileDefault) -> dict[str, Any]:
    validate_effect_configuration(
        default.effect,
        default.colours,
        default.effect_parameters,
    )
    return {
        "target": default.target,
        "effect": default.effect,
        "colours": _palette_to_json(default.colours),
        "effect_parameters": _parameters_to_dict(default.effect_parameters),
    }


def _default_from_dict(value: Any, version: int) -> ProfileDefault:
    data = _require_object(value, "profile default")
    colour_field = "colour" if version == 1 else "colours"
    optional = {"effect_parameters"} if version >= 2 else set()
    _require_exact_keys(
        data,
        required={"target", "effect", colour_field},
        optional=optional,
        label="profile default",
    )
    if version == 1:
        primary = _rgb_from_json(data["colour"], "profile default colour")
        palette = (primary,)
        parameters = EffectParameters()
    else:
        palette = _palette_from_json(data["colours"], "profile default colours")
        primary = palette[0]
        parameters = (
            _parameters_from_dict(
                data["effect_parameters"],
                "profile default effect_parameters",
                version=version,
            )
            if "effect_parameters" in data
            else EffectParameters()
        )
        validate_effect_configuration(data["effect"], palette, parameters)
    return ProfileDefault(
        target=data["target"],
        effect=data["effect"],
        colour=primary,
        colours=palette,
        effect_parameters=parameters,
    )


def profile_to_dict(profile: NativeLightingProfile) -> dict[str, Any]:
    """Convert a native profile to strict current-version JSON."""
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be a NativeLightingProfile")
    return {
        "format": PROFILE_FORMAT,
        "version": PROFILE_VERSION,
        "name": profile.name,
        "reset_before_start": profile.reset_before_start,
        "defaults": [_default_to_dict(default) for default in profile.defaults],
        "rules": [_rule_to_dict(rule) for rule in profile.rules],
    }


def profile_from_dict(value: Any) -> NativeLightingProfile:
    """Parse strict native v1/v2/v3/v4/v5/v6 JSON into the current domain model."""
    data = _require_object(value, "profile")
    _require_exact_keys(
        data,
        required={"format", "version", "name", "reset_before_start", "defaults", "rules"},
        label="profile",
    )
    if data["format"] != PROFILE_FORMAT:
        raise ValueError(f"unsupported profile format: {data['format']!r}")
    version = data["version"]
    if version not in SUPPORTED_PROFILE_VERSIONS:
        raise ValueError(f"unsupported profile version: {version!r}")
    if not isinstance(data["reset_before_start"], bool):
        raise ValueError("reset_before_start must be boolean")
    if not isinstance(data["defaults"], list):
        raise ValueError("profile defaults must be an array")
    if not isinstance(data["rules"], list):
        raise ValueError("profile rules must be an array")

    return NativeLightingProfile(
        name=data["name"],
        reset_before_start=data["reset_before_start"],
        defaults=tuple(_default_from_dict(item, version) for item in data["defaults"]),
        rules=tuple(_rule_from_dict(item, version) for item in data["rules"]),
    )


def save_profile(path: str | os.PathLike[str], profile: NativeLightingProfile) -> None:
    """Atomically save a native profile as readable UTF-8 v6 JSON."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(profile_to_dict(profile), indent=2, ensure_ascii=False)

    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_profile(path: str | os.PathLike[str]) -> NativeLightingProfile:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid native profile JSON: {exc}") from exc
    return profile_from_dict(value)
