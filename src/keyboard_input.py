#!/usr/bin/env python3
"""Small Windows keyboard-state adapter for VPC-style Keyboard rules.

The adapter only reports current pressed state for configured keys. It does not
own rule semantics, hotkey actions, scenes, or lighting output.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable, Iterable

_VK_CODES: dict[str, int] = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "SHIFT": 0x10,
    "CTRL": 0x11,
    "ALT": 0x12,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "LCTRL": 0xA2,
    "RCTRL": 0xA3,
    "LSHIFT": 0xA0,
    "RSHIFT": 0xA1,
    "LALT": 0xA4,
    "RALT": 0xA5,
}
_VK_CODES.update({chr(code): code for code in range(ord("A"), ord("Z") + 1)})
_VK_CODES.update({str(value): 0x30 + value for value in range(10)})
_VK_CODES.update({f"F{value}": 0x6F + value for value in range(1, 25)})


def normalize_key_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("key name must be a non-empty string")
    return value.strip().upper()


def virtual_key_code(name: str) -> int:
    normalized = normalize_key_name(name)
    try:
        return _VK_CODES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported keyboard key {name!r}") from exc


def _windows_key_down(vk_code: int) -> bool:
    if os.name != "nt":
        raise RuntimeError("live keyboard capture is only available on Windows")
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)


class KeyboardInputAdapter:
    """Poll current down/up state for a fixed set of keyboard keys."""

    def __init__(
        self,
        watched_keys: Iterable[str],
        *,
        key_down: Callable[[int], bool] | None = None,
    ) -> None:
        normalized = tuple(normalize_key_name(key) for key in watched_keys)
        if not normalized:
            raise ValueError("watched_keys must contain at least one key")
        if len(set(normalized)) != len(normalized):
            raise ValueError("watched_keys must not contain duplicates")
        self._keys = normalized
        self._codes = {key: virtual_key_code(key) for key in normalized}
        self._key_down = key_down or _windows_key_down

    @property
    def watched_keys(self) -> tuple[str, ...]:
        return self._keys

    def snapshot(self) -> frozenset[str]:
        """Return the configured keys that are down at this instant."""
        return frozenset(
            key
            for key in self._keys
            if self._key_down(self._codes[key])
        )
