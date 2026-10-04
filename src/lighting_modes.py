#!/usr/bin/env python3
"""Data-driven COVAS:NEXT lighting modes.

Modes deliberately reuse EDL's accepted ordinary output/effect vocabulary
without becoming normal source rules. A mode is named choreography: ordered
phases, each with an explicit duration and one or more simultaneous lighting
outputs.

REACTIVE and RIPPLE remain source-triggered effects and therefore are not valid
ModeOutput effects. Persistence is separate from ``lighting_profiles.py`` so
timed modes do not pollute the condition-driven ``LightingRule`` domain model.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping

from lighting_effect_config import (
    KNOWN_EFFECTS,
    EffectParameters,
    default_parameters_for_effect,
    validate_effect_configuration,
)

MODE_FORMAT = "elite-dangerous-lighting-modes"
MODE_VERSION = 1
RGB = tuple[int, int, int]
Palette = tuple[RGB, ...]
_PARAMETER_FIELDS = tuple(field.name for field in fields(EffectParameters))


def canonical_mode_name(value: str) -> str:
    """Return the stable external-command key for a human mode name."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("mode name must be a non-empty string")
    return "_".join(value.strip().upper().replace("-", " ").split())


def _validate_rgb(value: RGB) -> RGB:
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError("mode colour must be an (R, G, B) tuple")
    if any(isinstance(component, bool) or not isinstance(component, int) for component in value):
        raise ValueError("mode RGB components must be integers")
    if any(component < 0 or component > 255 for component in value):
        raise ValueError("mode RGB components must be 0..255")
    return value


def _validate_palette(value: Palette) -> Palette:
    if not isinstance(value, tuple) or not value:
        raise ValueError("mode output colours must be a non-empty tuple")
    for colour in value:
        _validate_rgb(colour)
    return value


@dataclass(frozen=True)
class ModeOutput:
    """One independently configurable output active during one mode phase."""

    target: str
    effect: str
    colours: Palette
    effect_parameters: EffectParameters
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("mode output target must be a non-empty string")
        if not isinstance(self.effect, str) or not self.effect.strip():
            raise ValueError("mode output effect must be a non-empty string")
        if not isinstance(self.enabled, bool):
            raise ValueError("mode output enabled must be boolean")
        if not isinstance(self.effect_parameters, EffectParameters):
            raise ValueError("mode output effect_parameters must be EffectParameters")

        effect = self.effect.strip().upper()
        if effect not in KNOWN_EFFECTS:
            raise ValueError(
                f"mode output effect {effect!r} is trigger-scoped or unsupported; "
                f"expected one of {KNOWN_EFFECTS}"
            )

        colours = _validate_palette(self.colours)
        validate_effect_configuration(
            effect,
            colours,
            self.effect_parameters,
        )
        object.__setattr__(self, "target", self.target.strip())
        object.__setattr__(self, "effect", effect)


@dataclass(frozen=True)
class ModePhase:
    """One top-to-bottom timed block containing simultaneous outputs."""

    name: str
    duration_seconds: float
    outputs: tuple[ModeOutput, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError("phase name must be a string")
        if isinstance(self.duration_seconds, bool) or not isinstance(
            self.duration_seconds, (int, float)
        ):
            raise ValueError("phase duration must be numeric")
        duration = float(self.duration_seconds)
        if not math.isfinite(duration):
            raise ValueError("phase duration must be finite")
        if duration <= 0.0:
            raise ValueError("phase duration must be greater than zero")
        if not isinstance(self.outputs, tuple) or not self.outputs:
            raise ValueError("mode phase must contain at least one output")
        if any(not isinstance(output, ModeOutput) for output in self.outputs):
            raise ValueError("mode phase outputs must contain only ModeOutput values")
        targets = [output.target for output in self.outputs]
        if len(set(targets)) != len(targets):
            raise ValueError("a mode phase cannot contain duplicate output targets")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "duration_seconds", duration)


@dataclass(frozen=True)
class LightingMode:
    """One named deterministic lighting choreography."""

    name: str
    phases: tuple[ModePhase, ...]
    media_cue: str | None = None

    def __post_init__(self) -> None:
        canonical_mode_name(self.name)
        if not isinstance(self.phases, tuple) or not self.phases:
            raise ValueError("mode must contain at least one phase")
        if any(not isinstance(phase, ModePhase) for phase in self.phases):
            raise ValueError("mode phases must contain only ModePhase values")
        if self.media_cue is not None:
            if not isinstance(self.media_cue, str) or not self.media_cue.strip():
                raise ValueError("mode media_cue must be a non-empty string when provided")
            object.__setattr__(self, "media_cue", self.media_cue.strip())
        object.__setattr__(self, "name", self.name.strip())

    @property
    def key(self) -> str:
        return canonical_mode_name(self.name)

    @property
    def duration_seconds(self) -> float:
        return sum(phase.duration_seconds for phase in self.phases)

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                output.target
                for phase in self.phases
                for output in phase.outputs
                if output.enabled
            )
        )


@dataclass(frozen=True)
class ModeLibrary:
    """One saved collection of uniquely named modes."""

    modes: tuple[LightingMode, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.modes, tuple):
            raise ValueError("mode library modes must be a tuple")
        if any(not isinstance(mode, LightingMode) for mode in self.modes):
            raise ValueError("mode library must contain only LightingMode values")
        keys = [mode.key for mode in self.modes]
        if len(set(keys)) != len(keys):
            raise ValueError("mode names must be unique")

    def mode(self, name: str) -> LightingMode | None:
        key = canonical_mode_name(name)
        return next((mode for mode in self.modes if mode.key == key), None)


def default_mode_output(target: str = "KEYBOARD") -> ModeOutput:
    effect = "STATIC"
    return ModeOutput(
        target=target,
        effect=effect,
        colours=((0, 128, 255),),
        effect_parameters=default_parameters_for_effect(effect),
    )


def make_default_mode(name: str = "NEW MODE") -> LightingMode:
    return LightingMode(
        name=name,
        phases=(
            ModePhase(
                name="Phase 1",
                duration_seconds=10.0,
                outputs=(default_mode_output(),),
            ),
        ),
    )


def make_default_mode_library() -> ModeLibrary:
    return ModeLibrary((make_default_mode(),))


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_exact_keys(value: Mapping[str, Any], required: set[str], label: str) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required
    if missing:
        raise ValueError(f"{label} missing required field(s): {sorted(missing)}")
    if unknown:
        raise ValueError(f"{label} has unknown field(s): {sorted(unknown)}")


def _rgb_to_json(value: RGB) -> list[int]:
    return list(_validate_rgb(value))


def _rgb_from_json(value: Any, label: str) -> RGB:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must be a three-item RGB array")
    return _validate_rgb(tuple(value))  # type: ignore[arg-type]


def _parameters_to_dict(parameters: EffectParameters) -> dict[str, Any]:
    if not isinstance(parameters, EffectParameters):
        raise ValueError("mode effect parameters must be EffectParameters")
    return asdict(parameters)


def _parameters_from_dict(value: Any, label: str) -> EffectParameters:
    data = _require_object(value, label)
    _require_exact_keys(data, set(_PARAMETER_FIELDS), label)
    return EffectParameters(**data)


def mode_output_to_dict(output: ModeOutput) -> dict[str, Any]:
    if not isinstance(output, ModeOutput):
        raise ValueError("mode output must be ModeOutput")
    return {
        "target": output.target,
        "enabled": output.enabled,
        "effect": output.effect,
        "colours": [_rgb_to_json(colour) for colour in output.colours],
        "effect_parameters": _parameters_to_dict(output.effect_parameters),
    }


def mode_output_from_dict(value: Any) -> ModeOutput:
    data = _require_object(value, "mode output")
    _require_exact_keys(
        data,
        {"target", "enabled", "effect", "colours", "effect_parameters"},
        "mode output",
    )
    if not isinstance(data["colours"], list) or not data["colours"]:
        raise ValueError("mode output colours must be a non-empty array")
    return ModeOutput(
        target=data["target"],
        enabled=data["enabled"],
        effect=data["effect"],
        colours=tuple(
            _rgb_from_json(colour, f"mode output colours[{index}]")
            for index, colour in enumerate(data["colours"])
        ),
        effect_parameters=_parameters_from_dict(
            data["effect_parameters"],
            "mode output effect_parameters",
        ),
    )


def mode_phase_to_dict(phase: ModePhase) -> dict[str, Any]:
    return {
        "name": phase.name,
        "duration_seconds": phase.duration_seconds,
        "outputs": [mode_output_to_dict(output) for output in phase.outputs],
    }


def mode_phase_from_dict(value: Any) -> ModePhase:
    data = _require_object(value, "mode phase")
    _require_exact_keys(data, {"name", "duration_seconds", "outputs"}, "mode phase")
    if not isinstance(data["outputs"], list) or not data["outputs"]:
        raise ValueError("mode phase outputs must be a non-empty array")
    return ModePhase(
        name=data["name"],
        duration_seconds=data["duration_seconds"],
        outputs=tuple(mode_output_from_dict(output) for output in data["outputs"]),
    )


def lighting_mode_to_dict(mode: LightingMode) -> dict[str, Any]:
    payload = {
        "name": mode.name,
        "phases": [mode_phase_to_dict(phase) for phase in mode.phases],
    }
    if mode.media_cue is not None:
        payload["media_cue"] = mode.media_cue
    return payload


def lighting_mode_from_dict(value: Any) -> LightingMode:
    data = _require_object(value, "mode")
    required = {"name", "phases"}
    allowed = required | {"media_cue"}
    keys = set(data)
    missing = required - keys
    unknown = keys - allowed
    if missing:
        raise ValueError(f"mode missing required field(s): {sorted(missing)}")
    if unknown:
        raise ValueError(f"mode has unknown field(s): {sorted(unknown)}")
    if not isinstance(data["phases"], list) or not data["phases"]:
        raise ValueError("mode phases must be a non-empty array")
    if "media_cue" in data and (
        not isinstance(data["media_cue"], str) or not data["media_cue"].strip()
    ):
        raise ValueError("mode media_cue must be a non-empty string when provided")
    return LightingMode(
        name=data["name"],
        phases=tuple(mode_phase_from_dict(phase) for phase in data["phases"]),
        media_cue=data.get("media_cue"),
    )


def mode_library_to_dict(library: ModeLibrary) -> dict[str, Any]:
    if not isinstance(library, ModeLibrary):
        raise ValueError("mode library must be ModeLibrary")
    return {
        "format": MODE_FORMAT,
        "version": MODE_VERSION,
        "modes": [lighting_mode_to_dict(mode) for mode in library.modes],
    }


def mode_library_from_dict(value: Any) -> ModeLibrary:
    data = _require_object(value, "mode library")
    _require_exact_keys(data, {"format", "version", "modes"}, "mode library")
    if data["format"] != MODE_FORMAT:
        raise ValueError(f"unsupported mode library format: {data['format']!r}")
    if data["version"] != MODE_VERSION:
        raise ValueError(f"unsupported mode library version: {data['version']!r}")
    if not isinstance(data["modes"], list):
        raise ValueError("mode library modes must be an array")
    return ModeLibrary(tuple(lighting_mode_from_dict(mode) for mode in data["modes"]))


def load_mode_library(path: str | os.PathLike[str]) -> ModeLibrary:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read mode library {source}: {exc}") from exc
    return mode_library_from_dict(raw)


def save_mode_library(path: str | os.PathLike[str], library: ModeLibrary) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(mode_library_to_dict(library), indent=2, sort_keys=False) + "\n"
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
