#!/usr/bin/env python3
"""Pure read-only presentation helpers for the Elite Dangerous Lighting UI.

Kept separate from PySide6 so deterministic tests and non-UI engine work do not
acquire a Qt runtime dependency.
"""

from __future__ import annotations

from pathlib import Path

from lighting_rules import LightingRule


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        return value
    return repr(value)


def display_device_name(device: str) -> str:
    """Return the operator-facing HID name without the adapter namespace."""
    prefix = "rawinput:"
    if device.lower().startswith(prefix):
        return device[len(prefix) :]
    return device


def source_detail_table(rule: LightingRule) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Return headers and rows for the selected rule's structured source display."""
    if rule.keyboard is not None:
        return ("Field", "Value"), (("Combo", " + ".join(rule.keyboard.keys)),)

    if rule.button is not None:
        return (
            ("Field", "Value"),
            (
                ("Device", display_device_name(rule.button.device)),
                ("Button", str(rule.button.button)),
                ("State", rule.button.state),
            ),
        )

    if rule.axis is not None:
        rows: list[tuple[str, str]] = [
            ("Device", display_device_name(rule.axis.device)),
            ("Axis", str(rule.axis.axis)),
            ("Condition", rule.axis.operator),
            ("Value", _format_value(rule.axis.value)),
        ]
        if rule.axis.secondary_value is not None:
            rows.append(("Secondary value", _format_value(rule.axis.secondary_value)))
        return ("Field", "Value"), tuple(rows)

    return (
        ("Argument", "Condition", "Value"),
        tuple(
            (
                condition.source,
                condition.operator,
                _format_value(condition.value),
            )
            for condition in rule.conditions
        ),
    )


def rgb_text(rgb: tuple[int, int, int]) -> str:
    """Return compact decimal RGB text for the read-only UI."""
    return f"{rgb[0]}, {rgb[1]}, {rgb[2]}"


def is_virpil_link_profile_path(path: str | Path) -> bool:
    """Return whether a path uses Link Tool's conventional ``.led.json`` name."""
    return Path(path).name.lower().endswith(".led.json")
