#!/usr/bin/env python3
"""Non-authoritative starting hints for experimental Govee calibration.

These values are deliberately separate from ``VERIFIED_SEGMENT_COUNTS``.  They
may help an operator choose a first candidate, but they never make a device
renderable without the physical calibration acceptance step.

Current community evidence source:
https://github.com/fu-raz/signalrgb-govee-direct-connect
README notes:
- H6046: two bars, 10 LEDs per bar when using the add-on's Duplicate mode.
- H6062 Glide: 9 LEDs per straight bar and 3 LEDs per corner; installed total is
  composition-dependent, so no single candidate total is asserted here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeometryHint:
    suggested_count: int | None
    explanation: str
    evidence: str


_HINTS = {
    "H6046": GeometryHint(
        suggested_count=10,
        explanation=(
            "Community direct-connect tooling describes H6046 as two bars with 10 LEDs per bar. "
            "EDL treats 10 only as an experimental starting count; our native realtime mapping still requires your physical sweep test."
        ),
        evidence="SignalRGB Govee Direct Connect README",
    ),
    "H6062": GeometryHint(
        suggested_count=None,
        explanation=(
            "Community direct-connect tooling describes H6062 Glide components as 9 LEDs per straight bar and 3 per corner. "
            "Because the installed component count varies, EDL does not guess one total position count."
        ),
        evidence="SignalRGB Govee Direct Connect README",
    ),
}


def geometry_hint_for(sku: str) -> GeometryHint | None:
    return _HINTS.get(str(sku).strip().upper())
