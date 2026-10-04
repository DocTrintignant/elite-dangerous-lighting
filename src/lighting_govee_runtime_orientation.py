#!/usr/bin/env python3
"""Apply saved Govee installation orientation to live spatial-effect geometry.

Native segment addressing stays unchanged.  This adapter only changes the
normalized logical coordinates supplied to the existing effect samplers so
LEFT/RIGHT direction names match the operator's physically calibrated view.
"""

from __future__ import annotations

from functools import lru_cache
import time

import lighting_authority_runtime as authority_runtime
import lighting_profile_runner as profile_runner
from lighting_govee_config import (
    GoveeEnhancedDevice,
    load_govee_configuration,
    parse_all_target,
    parse_zone_target,
)
from lighting_govee_orientation import load_visual_reversed
from lighting_trigger_production import linear_points

Point = tuple[float, float]


def _target_device_id(target: str) -> str | None:
    device_id = parse_all_target(target)
    if device_id is not None:
        return device_id
    zone = parse_zone_target(target)
    return None if zone is None else zone[0]


@lru_cache(maxsize=None)
def _configured_device(device_id: str) -> GoveeEnhancedDevice | None:
    configuration = load_govee_configuration()
    return next(
        (device for device in configuration.devices if device.device_id == device_id),
        None,
    )


def oriented_device_points(
    device: GoveeEnhancedDevice,
    *,
    y: float = 0.0,
) -> tuple[Point, ...]:
    """Return logical effect coordinates aligned to the saved physical view.

    Position order in the returned tuple still matches native segment order.
    Only the coordinate assigned to each native position is mirrored when the
    operator's installation is visually reversed.
    """
    points = linear_points(device.segment_count, y=y)
    reversed_order = load_visual_reversed(
        device.device_id,
        legacy_h61c3=device.sku == "H61C3",
    )
    return tuple(reversed(points)) if reversed_order else points


def _points_for_target(
    target: str,
    count: int,
    *,
    y: float = 0.0,
) -> tuple[Point, ...] | None:
    device_id = _target_device_id(target)
    if device_id is None:
        return None
    device = _configured_device(device_id)
    if device is None or device.segment_count != count:
        return None
    return oriented_device_points(device, y=y)


def apply_govee_runtime_orientation() -> None:
    """Install the orientation correction once at existing renderer seams."""
    if getattr(profile_runner, "_edl_govee_runtime_orientation_applied", False):
        return

    previous_linear_frame = profile_runner._linear_frame
    previous_overlay_govee_device_frame = authority_runtime._overlay_govee_device_frame

    def linear_frame(
        output,
        target: str,
        count: int,
        elapsed_seconds: float,
        *,
        y: float = 0.0,
    ):
        points = _points_for_target(target, count, y=y)
        if points is None:
            return previous_linear_frame(
                output,
                target,
                count,
                elapsed_seconds,
                y=y,
            )

        rule = profile_runner._as_rule(output, target)
        if profile_runner.is_continuous_frame_effect(rule.effect):
            return profile_runner.sample_persisted_effect_frame(
                rule.effect,
                points,
                elapsed_seconds,
                rule.colours,
                rule.effect_parameters,
            )
        rgb = profile_runner._uniform_rgb(output, target, elapsed_seconds)
        return tuple(rgb for _point in points)

    def overlay_govee_device_frame(device, frame, *, now: float | None = None):
        timestamp = time.perf_counter() if now is None else float(now)
        points = oriented_device_points(device)
        return authority_runtime._apply_direct_target_frames(
            tuple(frame),
            points,
            authority_runtime._govee_target_specs(device),
            now=timestamp,
        )

    profile_runner._linear_frame = linear_frame
    authority_runtime._overlay_govee_device_frame = overlay_govee_device_frame
    profile_runner._edl_govee_runtime_orientation_applied = True
    authority_runtime._edl_govee_runtime_orientation_applied = True

    # The configuration itself is immutable while Start Lighting owns it.  Clear
    # any discovery-time lookup made before this adapter was installed so the
    # first live Govee frame resolves the current saved device data.
    _configured_device.cache_clear()
