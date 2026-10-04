#!/usr/bin/env python3
"""EDL live-target ownership registry.

This controller owns only the canonical set of targets acquired by the current
live renderers. Direct lighting intent lives in lighting_direct_effects; there is
no parallel STATIC override store.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Iterable, Iterator

from lighting_chroma_zone_config import is_chroma_zone_target
from lighting_chromalink_cells import is_chromalink_cell_target
from lighting_openrgb_targets import is_openrgb_target

RGB = tuple[int, int, int]
DIRECT_CHROMA_TARGETS = ("KEYBOARD", "MOUSE", "CHROMALINK")
GOVEE_TARGET_PREFIX = "GOVEE_ENHANCED::"

NAMED_COLOURS: dict[str, RGB] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "purple": (128, 0, 255),
    "orange": (255, 128, 0),
    "amber": (255, 191, 0),
    "pink": (255, 105, 180),
}

_TARGET_ALIASES = {
    "keyboard": "KEYBOARD",
    "whole keyboard": "KEYBOARD",
    "mouse": "MOUSE",
    "whole mouse": "MOUSE",
    "chromalink": "CHROMALINK",
    "chroma link": "CHROMALINK",
    "whole chromalink": "CHROMALINK",
    "whole chroma link": "CHROMALINK",
}


def _normalize_words(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def resolve_direct_static_target(value: str) -> str:
    """Normalize one canonical direct-control target.

    Friendly configured names are resolved by the COVAS catalog boundary. This
    authority layer accepts canonical whole-device, Chroma zone/ChromaLink cell,
    native Govee device/zone target IDs, and stable OpenRGB device/zone target IDs
    plus the small accepted whole-Chroma aliases.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target must be a non-empty string")

    stripped = value.strip()
    direct = stripped.upper()
    if direct in DIRECT_CHROMA_TARGETS:
        return direct
    if is_chromalink_cell_target(stripped):
        return direct
    if is_chroma_zone_target(stripped):
        return stripped

    target = _TARGET_ALIASES.get(_normalize_words(stripped))
    if target is not None:
        return target

    if stripped.startswith(GOVEE_TARGET_PREFIX):
        return stripped
    if is_openrgb_target(stripped):
        return stripped

    raise ValueError(
        "Direct control supports whole Keyboard, Mouse, ChromaLink, engine-defined "
        "Chroma zones/ChromaLink cells, configured Govee device/zone targets, and "
        "stable OpenRGB device/zone targets."
    )


def parse_static_colour(value: str) -> RGB:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("color must be a non-empty string")
    text = value.strip().lower()
    named = NAMED_COLOURS.get(text)
    if named is not None:
        return named
    if text.startswith("#") and len(text) == 7:
        try:
            return tuple(int(text[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]
        except ValueError:
            pass
    raise ValueError(
        "Unsupported color. Use a convenience named color or any exact #RRGGBB RGB value."
    )


def _validate_override_colour(colour: RGB) -> RGB:
    if (
        not isinstance(colour, tuple)
        or len(colour) != 3
        or any(isinstance(value, bool) or not isinstance(value, int) for value in colour)
        or any(value < 0 or value > 255 for value in colour)
    ):
        raise ValueError("override colour must be an RGB tuple with values 0..255")
    return colour


class LightingAuthorityController:
    """Thread-safe direct-override state shared by the UI runner and bridge."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active_targets: set[str] = set()

    @contextmanager
    def target_lifecycle(self) -> Iterator[None]:
        """Serialize command admission with target activation and teardown."""
        with self._lock:
            yield

    @property
    def active_targets(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._active_targets))

    def activate_targets(self, targets: Iterable[str]) -> None:
        normalized = {resolve_direct_static_target(target) for target in targets}
        with self._lock:
            self._active_targets.update(normalized)

    def deactivate_targets(self, targets: Iterable[str]) -> None:
        normalized = {resolve_direct_static_target(target) for target in targets}
        with self._lock:
            for target in normalized:
                self._active_targets.discard(target)

    def is_target_active(self, target: str) -> bool:
        normalized = resolve_direct_static_target(target)
        with self._lock:
            return normalized in self._active_targets



lighting_authority = LightingAuthorityController()
