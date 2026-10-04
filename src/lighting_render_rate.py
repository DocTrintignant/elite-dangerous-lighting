#!/usr/bin/env python3
"""Application-wide continuous lighting update cadence.

The renderer keeps one persistent Chroma session. This module owns the cadence
value used by the current profile-runner loop. Effect timing remains elapsed-
time based, but Status/keyboard/HID observation currently occurs in that same
loop, so observation frequency can vary with FPS and blocking backend work.
This module does not change rule semantics, effect math or transport lifecycle.
"""

from __future__ import annotations

import math
import threading

DEFAULT_RENDER_FPS = 30.0
MIN_RENDER_FPS = 10.0
MAX_RENDER_FPS = 60.0

_lock = threading.RLock()
_render_fps = DEFAULT_RENDER_FPS


def validate_render_fps(value: int | float) -> float:
    """Return a finite supported FPS value or fail loudly."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("lighting update rate must be a number")
    fps = float(value)
    if not math.isfinite(fps):
        raise ValueError("lighting update rate must be finite")
    if not MIN_RENDER_FPS <= fps <= MAX_RENDER_FPS:
        raise ValueError(
            f"lighting update rate must be between {MIN_RENDER_FPS:g} and {MAX_RENDER_FPS:g} FPS"
        )
    return fps


def set_render_fps(value: int | float) -> float:
    """Set the process-wide renderer cadence and return the normalized value."""
    fps = validate_render_fps(value)
    global _render_fps
    with _lock:
        _render_fps = fps
    return fps


def current_render_fps() -> float:
    """Return the current renderer cadence."""
    with _lock:
        return _render_fps


def frame_period_seconds() -> float:
    """Return one frame period for the current renderer cadence."""
    return 1.0 / current_render_fps()
