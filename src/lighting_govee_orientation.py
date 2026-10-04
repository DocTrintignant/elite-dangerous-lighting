#!/usr/bin/env python3
"""Per-device installation orientation shared by Govee UI and live rendering.

Orientation is topology metadata only. Native renderer segment addresses remain
unchanged; this setting controls how the operator's physical left/right view
maps onto native segment order for editing and spatial effect coordinates.
"""

from __future__ import annotations

from lighting_settings import app_settings

LEGACY_H61C3_REVERSE_KEY = "govee/h61c3_reverse_visual_order"
KEY_PREFIX = "govee/device_visual_reverse/"


def _key(device_id: str) -> str:
    return KEY_PREFIX + str(device_id)


def load_visual_reversed(device_id: str, *, legacy_h61c3: bool = False) -> bool:
    settings = app_settings()
    value = settings.value(_key(device_id), None)
    if value is None and legacy_h61c3:
        value = settings.value(LEGACY_H61C3_REVERSE_KEY, True)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def save_visual_reversed(device_id: str, reversed_order: bool) -> None:
    settings = app_settings()
    settings.setValue(_key(device_id), bool(reversed_order))
    settings.sync()
