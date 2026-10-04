#!/usr/bin/env python3
"""Production OpenRGB renderer for stable EDL OpenRGB target leaves.

The renderer owns one persistent OpenRGB SDK client for one live run, resolves
saved targets against the fresh SDK inventory, captures the current complete
colour frames before the first write, renders only requested target leaves, and
restores those captured frames on release.

Topology/configuration remains outside realtime ownership: this module never
resizes zones and exposes no configuration write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from lighting_openrgb_targets import OpenRGBTargetBinding, bindings_from_inventory
from lighting_openrgb_transport import OpenRGBRealtimeClient, OpenRGBTransportError


RGB = tuple[int, int, int]


@dataclass(frozen=True)
class _CapturedSurface:
    surface: object
    frame: tuple[RGB, ...]


class OpenRGBRenderer:
    """Own the requested OpenRGB target leaves for one Start Lighting run."""

    def __init__(
        self,
        requested_targets: set[str] | frozenset[str],
        *,
        client: OpenRGBRealtimeClient | None = None,
    ) -> None:
        values = frozenset(str(target) for target in requested_targets)
        if not values:
            raise ValueError("OpenRGB renderer requires at least one requested target")
        self._requested_targets = values
        self._client = client or OpenRGBRealtimeClient()
        self._bindings: dict[str, OpenRGBTargetBinding] = {}
        self._captured: tuple[_CapturedSurface, ...] = ()
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def requested_targets(self) -> frozenset[str]:
        return self._requested_targets

    @property
    def bindings(self) -> Mapping[str, OpenRGBTargetBinding]:
        return dict(self._bindings)

    def start(self) -> None:
        if self._active:
            return

        inventory = self._client.start()
        available = bindings_from_inventory(inventory)
        missing = sorted(self._requested_targets - set(available))
        if missing:
            self._client.close()
            raise OpenRGBTransportError(
                "OpenRGB target(s) are not present in the current SDK inventory: "
                + ", ".join(missing)
            )

        bindings = {target: available[target] for target in self._requested_targets}

        # Capture every distinct physical OpenRGB surface that may be modified.
        # Restoration is whole-surface so several zone leaves on one motherboard
        # cannot leave a partially modified controller behind on Stop.
        captured_by_identity: dict[str, _CapturedSurface] = {}
        for binding in bindings.values():
            surface = binding.surface
            identity = binding.spec.surface_identity
            frame = tuple(surface.colors)
            if len(frame) != int(surface.led_count) or not frame:
                self._client.close()
                raise OpenRGBTransportError(
                    f"Cannot safely acquire OpenRGB surface {surface.name!r}: "
                    f"captured {len(frame)} colours for {surface.led_count} LEDs."
                )
            captured_by_identity.setdefault(
                identity,
                _CapturedSurface(surface=surface, frame=frame),
            )

        self._bindings = bindings
        self._captured = tuple(captured_by_identity.values())
        self._active = True

    def _binding(self, target: str) -> OpenRGBTargetBinding:
        if not self._active:
            raise RuntimeError("OpenRGB renderer is not active")
        if target not in self._bindings:
            raise ValueError(f"OpenRGB target {target!r} is not owned by this renderer")
        return self._bindings[target]

    @staticmethod
    def _surface_zone_slice(binding: OpenRGBTargetBinding) -> slice:
        if binding.zone is None or binding.zone_index is None:
            raise ValueError("whole-device OpenRGB target has no zone slice")

        start = 0
        for index, zone in enumerate(binding.surface.zones):
            count = int(zone.led_count)
            if count < 0:
                raise OpenRGBTransportError(
                    f"OpenRGB zone {zone.name!r} on {binding.surface.name!r} "
                    "reported a negative LED count"
                )
            if index == int(binding.zone_index):
                end = start + count
                if end > int(binding.surface.led_count):
                    raise OpenRGBTransportError(
                        f"OpenRGB zone {zone.name!r} on {binding.surface.name!r} "
                        "extends beyond the controller LED frame"
                    )
                return slice(start, end)
            start += count

        raise OpenRGBTransportError(
            f"OpenRGB zone index {binding.zone_index} is not present on "
            f"{binding.surface.name!r}"
        )

    def _captured_frame_for(self, binding: OpenRGBTargetBinding) -> tuple[RGB, ...]:
        identity = binding.spec.surface_identity
        for captured in self._captured:
            if binding.surface is captured.surface:
                return captured.frame
        # bindings_from_inventory reuses the exact surface objects, but keep a
        # metadata fallback so tests/future catalogue refactors cannot silently
        # lose the preserved base frame.
        for captured in self._captured:
            surface = captured.surface
            if (
                getattr(surface, "name", None) == binding.surface.name
                and getattr(surface, "serial", None) == binding.surface.serial
                and getattr(surface, "location", None) == binding.surface.location
            ):
                return captured.frame
        raise OpenRGBTransportError(
            f"No captured OpenRGB base frame exists for {identity!r}"
        )

    @staticmethod
    def _validate_frame(
        target: str,
        expected: int,
        values: Sequence[RGB],
    ) -> tuple[RGB, ...]:
        frame = tuple(values)
        if len(frame) != expected:
            raise ValueError(
                f"OpenRGB frame for {target} requires {expected} colours, got {len(frame)}"
            )
        if not frame:
            raise ValueError(f"OpenRGB frame for {target} cannot be empty")
        return frame

    def render_target_frames(
        self,
        target_frames: Mapping[str, Sequence[RGB]],
    ) -> int:
        if not self._active:
            raise RuntimeError("OpenRGB renderer is not active")
        unknown = sorted(set(target_frames) - self._requested_targets)
        if unknown:
            raise ValueError(
                "OpenRGB frame supplied for unowned target(s): " + ", ".join(unknown)
            )

        # OpenRGB direct/custom mode is controller-wide even when EDL owns only
        # one logical zone. Compose requested leaves into a complete controller
        # frame so untouched sibling zones retain their captured colours instead
        # of being cleared when the controller enters custom mode.
        composed: dict[int, tuple[object, list[RGB]]] = {}
        touched_targets = 0

        for target in sorted(self._requested_targets):
            values = target_frames.get(target)
            if values is None:
                continue
            binding = self._binding(target)
            frame = self._validate_frame(target, binding.led_count, values)
            key = id(binding.surface)

            if binding.zone is None:
                composed[key] = (binding.surface, list(frame))
            else:
                if key not in composed:
                    base = self._captured_frame_for(binding)
                    composed[key] = (binding.surface, list(base))
                _surface, device_frame = composed[key]
                zone_slice = self._surface_zone_slice(binding)
                if (zone_slice.stop - zone_slice.start) != len(frame):
                    raise OpenRGBTransportError(
                        f"OpenRGB zone {binding.zone.name!r} on "
                        f"{binding.surface.name!r} does not match its controller slice"
                    )
                device_frame[zone_slice] = frame
            touched_targets += 1

        for surface, frame in composed.values():
            self._client.write_device_frame(surface, tuple(frame))

        return touched_targets

    def release(self) -> None:
        if not self._active:
            self._client.close()
            return

        first_error: Exception | None = None
        try:
            # Restore captured colours while still in Direct/Custom mode, then
            # restore the pre-EDL controller mode as the final device write.
            for captured in self._captured:
                try:
                    self._client.write_device_frame(captured.surface, captured.frame)
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                try:
                    self._client.restore_device_mode(captured.surface)
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        finally:
            self._active = False
            self._bindings = {}
            self._captured = ()
            self._client.close()

        if first_error is not None:
            raise first_error
