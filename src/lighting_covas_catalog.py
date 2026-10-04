#!/usr/bin/env python3
"""Read-only native effect/capability catalog for Chromas Next.

Live device topology is projected from the renderer ownership registry by
lighting_covas_bridge. This module intentionally contains no configured-device
shadow model.
"""

from __future__ import annotations

from typing import Any

from lighting_effect_config import (
    BREATH,
    FIRE,
    FLASH,
    MIN_EFFECT_TIMING_SECONDS,
    PULSE,
    REACTIVE,
    REACTIVE_ORIGINS,
    RIPPLE,
    RIPPLE_DIRECTIONS,
    SPECTRUM,
    STARLIGHT,
    STATIC,
    WAVE,
    WAVE_DIRECTIONS,
    colour_cardinality,
    default_parameters_for_effect,
)


CATALOG_VERSION = 4

DIRECT_EFFECTS = (
    STATIC,
    FLASH,
    PULSE,
    BREATH,
    SPECTRUM,
    WAVE,
    STARLIGHT,
    FIRE,
    REACTIVE,
    RIPPLE,
)

_EFFECT_PARAMETER_NAMES: dict[str, tuple[str, ...]] = {
    STATIC: ("brightness",),
    FLASH: ("brightness", "step_seconds"),
    PULSE: ("cycle_seconds", "minimum_brightness", "maximum_brightness"),
    BREATH: ("brightness", "cycle_seconds"),
    SPECTRUM: ("brightness", "cycle_seconds"),
    WAVE: ("brightness", "cycle_seconds", "direction"),
    STARLIGHT: ("brightness", "cycle_seconds", "density"),
    FIRE: ("brightness", "step_seconds"),
    REACTIVE: ("brightness", "direction", "duration_seconds", "response_size"),
    RIPPLE: ("brightness", "direction", "speed", "width", "duration_seconds"),
}

_PARAMETER_SCHEMA_BASE: dict[str, dict[str, object]] = {
    "brightness": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "step_seconds": {"type": "number", "minimum": MIN_EFFECT_TIMING_SECONDS},
    "cycle_seconds": {"type": "number", "minimum": MIN_EFFECT_TIMING_SECONDS},
    "minimum_brightness": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "maximum_brightness": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "direction": {"type": "string"},
    "density": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "speed": {"type": "number", "exclusive_minimum": 0.0},
    "width": {"type": "number", "exclusive_minimum": 0.0},
    "duration_seconds": {"type": "number", "exclusive_minimum": 0.0},
    "response_size": {"type": "integer", "exclusive_minimum": 0},
}


def _parameter_defaults(effect: str) -> dict[str, object]:
    defaults = default_parameters_for_effect(effect)
    result: dict[str, object] = {}
    for name in _EFFECT_PARAMETER_NAMES[effect]:
        value = getattr(defaults, name)
        if value is not None:
            result[name] = value
    return result


def _parameter_schema(effect: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in _EFFECT_PARAMETER_NAMES[effect]:
        schema = dict(_PARAMETER_SCHEMA_BASE[name])
        if name == "direction":
            if effect == WAVE:
                schema["enum"] = list(WAVE_DIRECTIONS)
            elif effect == REACTIVE:
                schema["enum"] = list(REACTIVE_ORIGINS)
            elif effect == RIPPLE:
                schema["enum"] = list(RIPPLE_DIRECTIONS)
        result[name] = schema
    return result


def build_effect_catalog() -> dict[str, Any]:
    """Describe the direct-control surface using native EDL terminology."""
    effects: list[dict[str, Any]] = []
    for effect in DIRECT_EFFECTS:
        minimum, maximum = colour_cardinality(effect)
        # SPECTRUM carries one ignored compatibility colour in persisted rules,
        # but direct COVAS callers do not need to invent one.
        if effect == SPECTRUM:
            minimum, maximum = 0, 0
        effects.append(
            {
                "id": effect,
                "kind": "triggered_once" if effect in {REACTIVE, RIPPLE} else "continuous",
                "colors": {"minimum": minimum, "maximum": maximum},
                "parameters": list(_EFFECT_PARAMETER_NAMES[effect]),
                "parameter_schema": _parameter_schema(effect),
                "defaults": _parameter_defaults(effect),
                "supported_target_scope": "any_active_catalog_target",
                "supported_target_types": ["whole_device", "zone", "cell"],
            }
        )
    return {
        "effects": effects,
        "color_input": {
            "preferred_format": "rgb_triplet",
            "rgb_triplet_format": ["R", "G", "B"],
            "rgb_component_range": [0, 255],
            "hex_format": "#RRGGBB",
            "exact_rgb_format": "#RRGGBB",
            "named_colors_are_legacy_convenience_only": True,
        },
        "brightness_range": [0.0, 1.0],
    }


def build_lighting_catalog() -> dict[str, Any]:
    """Return EDL's current native control vocabulary.

    Device entries are deliberately empty here. The live bridge adds only the
    devices actually owned by the current renderer session.
    """
    return {
        "catalog_version": CATALOG_VERSION,
        "devices": [],
        "effect_control": build_effect_catalog(),
    }

