#!/usr/bin/env python3
"""Faithful VIRPIL VPC Link Tool profile import boundary.

The importer preserves the complete foreign source record while exposing a
normalized hardware-neutral runtime rule. Virpil-specific aliases are translated
only at this boundary. Unsupported active source semantics fail loudly rather
than being approximated or silently dropped.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any, Iterable, Mapping

from elite_status import FLAGS, FLAGS2, SCALAR_KEYS
from lighting_rules import (
    AXIS_BETWEEN_OPERATOR,
    SUPPORTED_AXIS_OPERATORS,
    SUPPORTED_OPERATORS,
    ArgumentCondition,
    AxisSource,
    ButtonSource,
    HidAddress,
    KeyboardCombination,
    LightingRule,
    calculate_outputs,
)

RGB = tuple[int, int, int]

VIRPIL_STEADY = "Steady"
VIRPIL_FLASHING = "Flashing"
VIRPIL_RULE_TYPES = ("Argument", "Keyboard", "Axis", "Button")

# Link Tool axis labels seen in the approved Elite profile and documented UI.
VIRPIL_AXIS_INDEX: dict[str, int] = {
    "X AXIS": 0,
    "Y AXIS": 1,
    "Z AXIS": 2,
    # Link Tool / DirectInput compatibility names. Keep these aliases at the
    # import boundary; editor-facing HID names are a separate presentation layer.
    "RX AXIS": 3,
    "RY AXIS": 4,
    "RZ AXIS": 5,
    "S0 AXIS": 6,
    "S1 AXIS": 7,
    # Human-readable equivalents retained for native/editor interoperability.
    "X ROTATION": 3,
    "Y ROTATION": 4,
    "Z ROTATION": 5,
    "SLIDER": 6,
    "DIAL": 7,
}

VIRPIL_ARGUMENT_ALIASES: dict[str, str] = {
    "FSD_Charging": "FsdCharging",
    "FSD_Jumping": "FsdJump",
    "HasLat_Long": "HasLatLong",
    "Fuel_Main": "FuelMain",
    "Fuel_Reservoir": "FuelReservoir",
    "InDanger": "IsInDanger",
    "GearDown": "LandingGearDown",
    "ShipLightsOn": "LightsOn",
    "HUD_Night_Vision": "NightVision",
    "FuelScoopDeployed": "ScoopingFuel",
    "MassLocked": "FsdMassLocked",
    "SRV_DriveAssist": "SrvDriveAssist",
    "SRV_HandbrakeOn": "SrvHandbrake",
    "SRV_InTurret": "SrvUsingTurretView",
    "SRV_TurretRetracted": "SrvTurretRetracted",
    "SRV_HighBeam": "SrvHighBeam",
    "IsInterdicted": "BeingInterdicted",
    "OnPlanet": "Landed",
    "HUD_Analysis_Mode": "HudInAnalysisMode",
}

_CANONICAL_STATE_KEYS = tuple(FLAGS) + tuple(FLAGS2) + SCALAR_KEYS
_CANONICAL_FOLDED = {
    re.sub(r"[^a-z0-9]", "", key.lower()): key for key in _CANONICAL_STATE_KEYS
}
_BOOLEAN_STATE_KEYS = set(FLAGS) | set(FLAGS2)
_NUMERIC_SCALAR_KEYS = {
    "Latitude",
    "Longitude",
    "Heading",
    "Altitude",
    "Temperature",
    "PlanetRadius",
    "FuelMain",
    "FuelReservoir",
    "Cargo",
    "Balance",
    "Oxygen",
    "Health",
    "Gravity",
    "FireGroup",
    "GuiFocus",
}
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_BUTTON_RE = re.compile(r"^Button\s+(\d+)$", re.IGNORECASE)


class VirpilImportError(ValueError):
    pass


@dataclass(frozen=True)
class VirpilCondition:
    argument: str
    condition: str
    value: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class VirpilButtonRule:
    button: str
    mode: str
    source_device: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class VirpilAxisRule:
    axis_condition: str
    axis_name: str
    primary_value: Real
    secondary_value: Real
    source_device: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class VirpilImportedRule:
    """Complete preserved foreign rule plus normalized convenience fields."""

    source_index: int
    rule_type: str
    is_enabled: bool
    device: str
    led_number: str
    led_mode: str
    color_one: int
    color_two: int
    comment: str
    keyboard_combo: str
    conditions: tuple[VirpilCondition, ...]
    button_rule: VirpilButtonRule
    axis_rule: VirpilAxisRule
    source_fields: Mapping[str, Any]
    raw_source_json: str

    @property
    def raw_source(self) -> dict[str, Any]:
        return json.loads(self.raw_source_json)

    @property
    def primary_colour(self) -> RGB:
        return virpil_color_to_rgb(self.color_one)

    @property
    def secondary_colour(self) -> RGB:
        return virpil_color_to_rgb(self.color_two)

    @property
    def target(self) -> str:
        return f"VIRPIL::{self.device}::{self.led_number}"

    def to_runtime_rule(self) -> "VirpilRuntimeRule":
        if self.rule_type == "Argument":
            if not self.conditions:
                raise VirpilImportError(
                    f"rule {self.source_index}: Argument rule has no conditions"
                )
            kwargs: dict[str, Any] = {
                "conditions": tuple(
                    ArgumentCondition(
                        source=canonical_argument_name(condition.argument),
                        operator=condition.condition,
                        value=canonical_condition_value(
                            canonical_argument_name(condition.argument),
                            condition.condition,
                            condition.value,
                        ),
                    )
                    for condition in self.conditions
                )
            }

        elif self.rule_type == "Keyboard":
            kwargs = {
                "conditions": (),
                "keyboard": KeyboardCombination(parse_keyboard_combo(self.keyboard_combo)),
            }

        elif self.rule_type == "Button":
            kwargs = {
                "conditions": (),
                "button": ButtonSource(
                    device=self.button_rule.source_device.strip(),
                    button=parse_button_number(self.button_rule.button),
                    state=self.button_rule.mode,
                ),
            }

        elif self.rule_type == "Axis":
            axis_key = self.axis_rule.axis_name.strip().upper()
            if axis_key not in VIRPIL_AXIS_INDEX:
                raise VirpilImportError(
                    f"rule {self.source_index}: unsupported axis name {self.axis_rule.axis_name!r}"
                )
            secondary: Real | None
            if self.axis_rule.axis_condition == AXIS_BETWEEN_OPERATOR:
                secondary = self.axis_rule.secondary_value
            else:
                # Preserve the serialized secondary value even though matching
                # ignores it outside BETWEEN, mirroring Link Tool semantics.
                secondary = self.axis_rule.secondary_value
            kwargs = {
                "conditions": (),
                "axis": AxisSource(
                    device=self.axis_rule.source_device.strip(),
                    axis=VIRPIL_AXIS_INDEX[axis_key],
                    operator=self.axis_rule.axis_condition,
                    value=self.axis_rule.primary_value,
                    secondary_value=secondary,
                ),
            }

        else:
            raise VirpilImportError(
                f"rule {self.source_index}: unsupported ruleType {self.rule_type!r}"
            )

        if self.led_mode == VIRPIL_STEADY:
            effect = "STATIC"
            palette = (self.primary_colour,)
        elif self.led_mode == VIRPIL_FLASHING:
            effect = "FLASH"
            palette = (self.primary_colour, self.secondary_colour)
        else:
            raise VirpilImportError(
                f"rule {self.source_index}: unsupported ledMode {self.led_mode!r}"
            )

        runtime = LightingRule(
            colour=self.primary_colour,
            colours=palette,
            enabled=self.is_enabled,
            effect=effect,
            target=self.target,
            name=self.comment.strip() or None,
            **kwargs,
        )
        return VirpilRuntimeRule(source=self, rule=runtime)


@dataclass(frozen=True)
class VirpilRuntimeRule:
    """Accepted evaluator rule plus imported output metadata needed by M3E."""

    source: VirpilImportedRule
    rule: LightingRule

    @property
    def primary_colour(self) -> RGB:
        return self.source.primary_colour

    @property
    def secondary_colour(self) -> RGB:
        return self.source.secondary_colour

    @property
    def output_device(self) -> str:
        return self.source.device

    @property
    def output_address(self) -> str:
        return self.source.led_number

    @property
    def led_mode(self) -> str:
        return self.source.led_mode


@dataclass(frozen=True)
class VirpilImportedProfile:
    rules: tuple[VirpilImportedRule, ...]
    raw_profile_json: str

    def runtime_rules(self) -> tuple[VirpilRuntimeRule, ...]:
        return tuple(rule.to_runtime_rule() for rule in self.rules)

    @property
    def source_profile(self) -> dict[str, Any]:
        return json.loads(self.raw_profile_json)


def virpil_color_to_rgb(value: int) -> RGB:
    """Decode Link Tool's COLORREF-style integer into canonical RGB."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise VirpilImportError("Virpil colour must be an integer")
    if value < 0 or value > 0xFFFFFF:
        raise VirpilImportError("Virpil colour must fit 24 bits")
    return (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)


def canonical_argument_name(name: str) -> str:
    """Translate a VPC Elite alias to this project's Frontier vocabulary."""
    if not isinstance(name, str):
        raise VirpilImportError("Virpil argument name must be a string")
    if name in VIRPIL_ARGUMENT_ALIASES:
        return VIRPIL_ARGUMENT_ALIASES[name]
    folded = re.sub(r"[^a-z0-9]", "", name.lower())
    return _CANONICAL_FOLDED.get(folded, name)


def _parse_number(text: str) -> int | float:
    if not _NUMBER_RE.fullmatch(text.strip()):
        raise VirpilImportError(f"expected numeric Virpil value, got {text!r}")
    stripped = text.strip()
    if re.fullmatch(r"[+-]?\d+", stripped):
        return int(stripped)
    return float(stripped)


def canonical_condition_value(source: str, operator: str, raw_value: str) -> Any:
    """Convert only values whose canonical state type is known."""
    if not isinstance(raw_value, str):
        raise VirpilImportError("Virpil Argument values must be strings")

    value = raw_value.strip()
    if source in _BOOLEAN_STATE_KEYS:
        if value == "1" or value.lower() == "true":
            return True
        if value == "0" or value.lower() == "false":
            return False
        raise VirpilImportError(
            f"boolean canonical source {source!r} has non-boolean value {raw_value!r}"
        )

    if source in _NUMERIC_SCALAR_KEYS or operator in {
        "Less",
        "More",
        "More or Equal",
        "Less or Equal",
    }:
        return _parse_number(value)

    return raw_value


def parse_button_number(text: str) -> int:
    if not isinstance(text, str):
        raise VirpilImportError("Virpil button address must be a string")
    match = _BUTTON_RE.fullmatch(text)
    if match is None:
        raise VirpilImportError(f"unsupported Virpil button address {text!r}")
    return int(match.group(1))


def parse_keyboard_combo(text: str) -> tuple[str, ...]:
    """Parse the Link Tool combination text without emulating key presses."""
    if not isinstance(text, str) or not text.strip():
        raise VirpilImportError("Virpil Keyboard rule has an empty keyboardCombo")
    parts = tuple(part.strip() for part in text.split("+") if part.strip())
    if not parts:
        raise VirpilImportError(f"invalid keyboardCombo {text!r}")
    return parts


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VirpilImportError(f"{label} must be a JSON object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise VirpilImportError(f"{label} must be a string")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise VirpilImportError(f"{label} must be boolean")
    return value


def _require_number(value: Any, label: str) -> Real:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise VirpilImportError(f"{label} must be numeric")
    return value


def _parse_condition(value: Any, rule_index: int) -> VirpilCondition:
    data = _require_mapping(value, f"rule {rule_index} condition")
    argument = _require_string(data.get("argument"), f"rule {rule_index} condition argument")
    condition = _require_string(data.get("condition"), f"rule {rule_index} condition operator")
    raw_value = _require_string(data.get("value"), f"rule {rule_index} condition value")
    # Conditions can be stale/inactive fields on Keyboard/Button/Axis rules.
    # Preserve them structurally here; active Argument semantics are validated
    # only when ruleType selects Argument in to_runtime_rule().
    return VirpilCondition(argument, condition, raw_value, copy.deepcopy(dict(data)))


def _parse_button_rule(value: Any, rule_index: int) -> VirpilButtonRule:
    data = _require_mapping(value, f"rule {rule_index} buttonRule")
    return VirpilButtonRule(
        button=_require_string(data.get("button"), f"rule {rule_index} button address"),
        mode=_require_string(data.get("mode"), f"rule {rule_index} button mode"),
        source_device=_require_string(
            data.get("sourceDevice"), f"rule {rule_index} button sourceDevice"
        ),
        raw=copy.deepcopy(dict(data)),
    )


def _parse_axis_rule(value: Any, rule_index: int) -> VirpilAxisRule:
    data = _require_mapping(value, f"rule {rule_index} axisRule")
    return VirpilAxisRule(
        axis_condition=_require_string(
            data.get("axisCondition"), f"rule {rule_index} axis condition"
        ),
        axis_name=_require_string(data.get("axisName"), f"rule {rule_index} axis name"),
        primary_value=_require_number(
            data.get("primaryValue"), f"rule {rule_index} axis primaryValue"
        ),
        secondary_value=_require_number(
            data.get("secondaryValue"), f"rule {rule_index} axis secondaryValue"
        ),
        source_device=_require_string(
            data.get("sourceDevice"), f"rule {rule_index} axis sourceDevice"
        ),
        raw=copy.deepcopy(dict(data)),
    )


def _parse_imported_rule(value: Any, source_index: int) -> VirpilImportedRule:
    data = _require_mapping(value, f"rule {source_index}")
    conditions_data = data.get("conditions")
    if not isinstance(conditions_data, list):
        raise VirpilImportError(f"rule {source_index}: conditions must be an array")
    rule_type = _require_string(data.get("ruleType"), f"rule {source_index} ruleType")
    led_mode = _require_string(data.get("ledMode"), f"rule {source_index} ledMode")
    color_one = data.get("colorOne")
    color_two = data.get("colorTwo")
    if isinstance(color_one, bool) or not isinstance(color_one, int):
        raise VirpilImportError(f"rule {source_index}: colorOne must be an integer")
    if isinstance(color_two, bool) or not isinstance(color_two, int):
        raise VirpilImportError(f"rule {source_index}: colorTwo must be an integer")

    return VirpilImportedRule(
        source_index=source_index,
        rule_type=rule_type,
        is_enabled=_require_bool(data.get("isEnabled"), f"rule {source_index} isEnabled"),
        device=_require_string(data.get("device"), f"rule {source_index} device"),
        led_number=_require_string(data.get("ledNumber"), f"rule {source_index} ledNumber"),
        led_mode=led_mode,
        color_one=color_one,
        color_two=color_two,
        comment=_require_string(data.get("comment"), f"rule {source_index} comment"),
        keyboard_combo=_require_string(
            data.get("keyboardCombo"), f"rule {source_index} keyboardCombo"
        ),
        conditions=tuple(
            _parse_condition(item, source_index) for item in conditions_data
        ),
        button_rule=_parse_button_rule(data.get("buttonRule"), source_index),
        axis_rule=_parse_axis_rule(data.get("axisRule"), source_index),
        source_fields=copy.deepcopy(dict(data)),
        raw_source_json=json.dumps(data, separators=(",", ":"), ensure_ascii=False),
    )


def import_virpil_profile_data(value: Any) -> VirpilImportedProfile:
    data = _require_mapping(value, "Virpil profile")
    rules_data = data.get("rules")
    if not isinstance(rules_data, list):
        raise VirpilImportError("Virpil profile rules must be an array")
    rules = tuple(
        _parse_imported_rule(item, source_index)
        for source_index, item in enumerate(rules_data)
    )
    return VirpilImportedProfile(
        rules=rules,
        raw_profile_json=json.dumps(data, separators=(",", ":"), ensure_ascii=False),
    )


def load_virpil_profile(path: str | Path) -> VirpilImportedProfile:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VirpilImportError(f"invalid Virpil profile JSON: {exc}") from exc
    return import_virpil_profile_data(value)


def calculate_virpil_outputs(
    profile: VirpilImportedProfile,
    state: Mapping[str, Any],
    *,
    pressed_keys: Iterable[str] = (),
    button_states: Mapping[HidAddress, bool] | None = None,
    axis_values: Mapping[HidAddress, Real] | None = None,
) -> dict[str, VirpilRuntimeRule]:
    runtime_rules = profile.runtime_rules()
    calculated = calculate_outputs(
        state,
        (runtime.rule for runtime in runtime_rules),
        pressed_keys=pressed_keys,
        button_states=button_states,
        axis_values=axis_values,
    )
    by_rule_id = {id(runtime.rule): runtime for runtime in runtime_rules}
    return {
        target: by_rule_id[id(rule)]
        for target, rule in calculated.items()
    }
