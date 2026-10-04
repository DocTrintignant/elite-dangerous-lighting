#!/usr/bin/env python3
"""Hardware-neutral in-memory RGB composition primitives.

The compositor combines already-generated logical RGB samples before any
geometry mapping or device transport occurs. It knows nothing about Chroma,
Elite state, rule matching, scenes, or HID input.

V1 composition vocabulary:
- REPLACE: incoming colour replaces current colour
- MAX: per-channel maximum (brighten overlay)
- BLEND: explicit linear interpolation using caller-supplied opacity

BLEND deliberately has no implicit default opacity. The architecture names the
operation but does not define a project-wide blend amount, so callers must make
that parameter explicit rather than inheriting an arbitrary 50/50 policy.
"""

from __future__ import annotations

RGB = tuple[int, int, int]

REPLACE = "REPLACE"
MAX = "MAX"
BLEND = "BLEND"
SUPPORTED_COMPOSITIONS = (REPLACE, MAX, BLEND)


def _validate_rgb(rgb: RGB) -> RGB:
    if not isinstance(rgb, tuple) or len(rgb) != 3:
        raise ValueError("colour must be an (R, G, B) tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in rgb):
        raise ValueError("RGB components must be integers")
    if any(value < 0 or value > 255 for value in rgb):
        raise ValueError("RGB components must be 0..255")
    return rgb


def replace_rgb(current: RGB, incoming: RGB) -> RGB:
    """Return the incoming colour after validating both inputs."""
    _validate_rgb(current)
    return _validate_rgb(incoming)


def max_rgb(current: RGB, incoming: RGB) -> RGB:
    """Return the per-channel maximum of current and incoming."""
    a = _validate_rgb(current)
    b = _validate_rgb(incoming)
    return tuple(max(left, right) for left, right in zip(a, b))


def blend_rgb(current: RGB, incoming: RGB, opacity: float) -> RGB:
    """Linearly blend incoming over current using explicit 0..1 opacity.

    ``opacity=0`` preserves current. ``opacity=1`` yields incoming.
    Intermediate values are rounded to the nearest integer RGB component.
    """
    a = _validate_rgb(current)
    b = _validate_rgb(incoming)
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
        raise ValueError("opacity must be numeric")
    if opacity < 0.0 or opacity > 1.0:
        raise ValueError("opacity must be between 0 and 1")
    return tuple(
        round(left + (right - left) * opacity)
        for left, right in zip(a, b)
    )


def composite_rgb(
    current: RGB,
    incoming: RGB,
    mode: str,
    *,
    opacity: float | None = None,
) -> RGB:
    """Apply one explicit v1 composition operation.

    BLEND requires ``opacity``. REPLACE and MAX reject an opacity argument so
    unused configuration cannot be silently ignored.
    """
    if mode not in SUPPORTED_COMPOSITIONS:
        raise ValueError(
            f"unsupported composition {mode!r}; expected one of {SUPPORTED_COMPOSITIONS}"
        )

    if mode == REPLACE:
        if opacity is not None:
            raise ValueError("REPLACE does not accept opacity")
        return replace_rgb(current, incoming)

    if mode == MAX:
        if opacity is not None:
            raise ValueError("MAX does not accept opacity")
        return max_rgb(current, incoming)

    if opacity is None:
        raise ValueError("BLEND requires explicit opacity")
    return blend_rgb(current, incoming, opacity)
