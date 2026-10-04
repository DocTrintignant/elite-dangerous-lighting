#!/usr/bin/env python3
"""Production composition/rendering for enhanced Govee targets.

Each configured device owns an implicit whole-device ``...::ALL`` target plus
zero or more saved user-zone leaves. The renderer keeps the already accepted
native session/20 FPS behavior and only changes target-to-segment composition.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping, Sequence

from lighting_govee_config import (
    GoveeConfiguration,
    GoveeEnhancedDevice,
)
from lighting_govee_transport import GoveeRealtimeSession

RGB = tuple[int, int, int]
BLACK: RGB = (0, 0, 0)
ACCEPTED_MAX_FPS = 20.0
ACCEPTED_MIN_PERIOD = 1.0 / ACCEPTED_MAX_FPS
ACCEPTED_ACTIVATION_WAIT_SECONDS = 0.100


def _validate_rgb(colour: RGB, label: str) -> RGB:
    if (
        not isinstance(colour, tuple)
        or len(colour) != 3
        or any(isinstance(value, bool) or not isinstance(value, int) for value in colour)
        or any(value < 0 or value > 255 for value in colour)
    ):
        raise ValueError(f"invalid RGB output for {label}: {colour!r}")
    return colour


def compose_device_frame(
    device: GoveeEnhancedDevice,
    target_colours: Mapping[str, RGB],
) -> tuple[RGB, ...]:
    """Compose uniform whole-device and saved-zone colours.

    ALL is the base layer. Named zones overwrite only their own native positions,
    preserving deterministic target specificity without changing rule order.
    """
    all_colour = _validate_rgb(target_colours.get(device.all_target, BLACK), device.all_target)
    frame = [all_colour] * device.segment_count
    for zone in device.zones:
        target = device.target_for_zone(zone)
        if target not in target_colours:
            continue
        colour = _validate_rgb(target_colours[target], target)
        for segment in zone.segments:
            frame[segment - 1] = colour
    return tuple(frame)


def _validate_complete_frame(
    device: GoveeEnhancedDevice,
    target: str,
    values: Sequence[RGB],
) -> tuple[RGB, ...]:
    values = tuple(values)
    if len(values) != device.segment_count:
        raise ValueError(
            f"native frame for {target} must contain exactly {device.segment_count} positions"
        )
    for index, colour in enumerate(values):
        _validate_rgb(colour, f"{target}[{index + 1}]")
    return values


def compose_device_target_frames(
    device: GoveeEnhancedDevice,
    target_frames: Mapping[str, Sequence[RGB]],
) -> tuple[RGB, ...]:
    """Compose whole-device and zone-masked addressable frames."""
    all_values = target_frames.get(device.all_target)
    if all_values is None:
        frame = [BLACK] * device.segment_count
    else:
        frame = list(_validate_complete_frame(device, device.all_target, all_values))

    for zone in device.zones:
        target = device.target_for_zone(zone)
        values = target_frames.get(target)
        if values is None:
            continue
        values = _validate_complete_frame(device, target, values)
        for segment in zone.segments:
            frame[segment - 1] = values[segment - 1]
    return tuple(frame)


@dataclass
class _OwnedDevice:
    device: GoveeEnhancedDevice
    session: GoveeRealtimeSession
    last_send: float | None = None


class EnhancedGoveeRenderer:
    """Own only explicitly requested enhanced devices for one live run."""

    def __init__(
        self,
        configuration: GoveeConfiguration,
        requested_targets: set[str] | frozenset[str],
    ) -> None:
        if not isinstance(configuration, GoveeConfiguration):
            raise ValueError("configuration must be GoveeConfiguration")
        target_map = configuration.target_map()
        unknown = sorted(target for target in requested_targets if target not in target_map)
        if unknown:
            raise ValueError(
                "Enhanced Govee target(s) are not configured on this computer: "
                + ", ".join(unknown)
            )

        devices: dict[str, GoveeEnhancedDevice] = {}
        for target in requested_targets:
            device, _zone = target_map[target]
            devices[device.device_id] = device
        self._owned = tuple(
            _OwnedDevice(device, GoveeRealtimeSession(device.ip))
            for device in devices.values()
        )
        self._requested_targets = frozenset(requested_targets)
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def requested_targets(self) -> frozenset[str]:
        return self._requested_targets

    @property
    def devices(self) -> tuple[GoveeEnhancedDevice, ...]:
        return tuple(owned.device for owned in self._owned)

    def start(self) -> None:
        if self._active:
            return
        started: list[_OwnedDevice] = []
        try:
            for owned in self._owned:
                owned.session.start()
                started.append(owned)
            if started:
                time.sleep(ACCEPTED_ACTIVATION_WAIT_SECONDS)
            self._active = True
        except Exception:
            for owned in reversed(started):
                try:
                    owned.session.close()
                except Exception:
                    pass
            raise

    @staticmethod
    def _due(owned: _OwnedDevice, timestamp: float, force: bool) -> bool:
        return (
            force
            or owned.last_send is None
            or timestamp - owned.last_send >= ACCEPTED_MIN_PERIOD
        )

    def render(
        self,
        target_colours: Mapping[str, RGB],
        *,
        now: float | None = None,
        force: bool = False,
    ) -> int:
        if not self._active:
            raise RuntimeError("Enhanced Govee renderer is not active")
        timestamp = time.perf_counter() if now is None else float(now)
        sent = 0
        for owned in self._owned:
            if not self._due(owned, timestamp, force):
                continue
            frame = compose_device_frame(owned.device, target_colours)
            owned.session.render_segments(frame)
            owned.last_send = timestamp
            sent += 1
        return sent

    def render_target_frames(
        self,
        target_frames: Mapping[str, Sequence[RGB]],
        *,
        now: float | None = None,
        force: bool = False,
    ) -> int:
        if not self._active:
            raise RuntimeError("Enhanced Govee renderer is not active")
        timestamp = time.perf_counter() if now is None else float(now)
        sent = 0
        for owned in self._owned:
            if not self._due(owned, timestamp, force):
                continue
            frame = compose_device_target_frames(owned.device, target_frames)
            owned.session.render_segments(frame)
            owned.last_send = timestamp
            sent += 1
        return sent

    def render_device_frames(
        self,
        device_frames: Mapping[str, Sequence[RGB]],
        *,
        now: float | None = None,
        force: bool = False,
    ) -> int:
        """Render already-composited final frames without changing transport ownership.

        This is the narrow production seam used by temporary trigger overlays:
        ordinary whole/zone composition happens first, then REACTIVE/RIPPLE are
        overlaid on the current physical frame, and this method dispatches that
        final frame through the same accepted realtime session/rate limiter.
        """
        if not self._active:
            raise RuntimeError("Enhanced Govee renderer is not active")
        timestamp = time.perf_counter() if now is None else float(now)
        owned_ids = {owned.device.device_id for owned in self._owned}
        unknown = sorted(set(device_frames) - owned_ids)
        if unknown:
            raise ValueError(
                "final frame supplied for unowned Govee device(s): " + ", ".join(unknown)
            )
        sent = 0
        for owned in self._owned:
            if not self._due(owned, timestamp, force):
                continue
            values = device_frames.get(owned.device.device_id)
            if values is None:
                continue
            frame = _validate_complete_frame(
                owned.device,
                f"device {owned.device.device_id}",
                values,
            )
            owned.session.render_segments(frame)
            owned.last_send = timestamp
            sent += 1
        return sent

    def release(self) -> None:
        if not self._active:
            return
        errors: list[Exception] = []
        for owned in reversed(self._owned):
            try:
                owned.session.close()
            except Exception as exc:
                errors.append(exc)
        self._active = False
        if errors:
            raise errors[0]
