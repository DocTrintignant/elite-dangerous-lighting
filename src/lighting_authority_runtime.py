#!/usr/bin/env python3
"""Final live-render authority adapter for EDL operator overrides.

This layer wraps only accepted final renderer calls. Calculated rules, trigger
overlays, persistent Chroma/Govee/OpenRGB session lifecycle, and transport payloads
keep running underneath it. Direct operator/COVAS intent therefore has final authority
without freezing the underlying state.

The direct boundary is deliberately geometry-aware only at the last renderer
seam: whole devices, engine-defined zones and ChromaLink cells all reuse EDL's
existing effect samplers and accepted physical addressing.
"""

from __future__ import annotations

import time
from typing import Iterable, Sequence

import lighting_profile_runner as profile_runner
from lighting_authority import lighting_authority
from lighting_chroma_live import LiveChromaSession
from lighting_chroma_surface_effects import (
    frame_to_surface_matrix,
    surface_cell_points,
    uniform_surface_matrix,
)
from lighting_chroma_surfaces import chroma_surface
from lighting_chroma_zone_config import (
    ChromaZoneConfiguration,
    load_chroma_zone_configuration,
)
from lighting_chromalink_cells import CHROMALINK_CELL_TARGETS
from lighting_direct_effects import direct_effects
from lighting_effect_config import REACTIVE, TRIGGERED_EFFECTS
from lighting_govee_render import (
    EnhancedGoveeRenderer,
    compose_device_frame,
    compose_device_target_frames,
)
from lighting_openrgb_render import OpenRGBRenderer
from lighting_scene_authority import scene_authority
from lighting_trigger_production import (
    _normalize_local_points,
    _reactive_indices_for_zone_origin,
    linear_points,
)

RGB = tuple[int, int, int]
Point = tuple[float, float]
Frame = tuple[RGB, ...]
CHROMALINK_POINTS: tuple[Point, ...] = tuple((index / 4.0, 0.5) for index in range(5))


def _direct_target_snapshot() -> set[str]:
    return set(direct_effects.direct_override_targets)


def _release_authority_targets(targets: Iterable[str]) -> None:
    """Release all temporary authority owned by one renderer target set."""
    target_values = tuple(targets)
    scene_authority.clear_targets(target_values)
    with lighting_authority.target_lifecycle():
        direct_effects.clear_targets(target_values)
        lighting_authority.deactivate_targets(target_values)


def _has_direct_for(targets: Iterable[str]) -> bool:
    return bool(set(targets) & _direct_target_snapshot())


def _direct_override_rgb(target: str, *, now: float | None = None):
    """Return one representative direct-effect RGB for a non-spatial seam."""
    return direct_effects.sample_override_for(target, now=now)


def _apply_direct_target_frames(
    base_frame: Sequence[RGB],
    points: Sequence[Point],
    target_specs: Sequence[tuple[str, tuple[int, ...] | None, tuple[str, ...]]],
    *,
    now: float,
) -> Frame:
    """Replace target-owned positions with current direct-authority output.

    ``None`` mask means the complete physical surface. Named zones/cells are
    applied afterwards in deterministic engine/configuration order, so the more
    specific direct target wins on shared positions just as the accepted Govee
    zone compositor already does.
    """
    values = list(base_frame)
    point_values = tuple(points)
    if not values or len(values) != len(point_values):
        raise ValueError("direct authority frame and geometry must have equal non-zero length")

    for target, mask, section_orientations in target_specs:
        if mask is None:
            indices = tuple(range(len(values)))
        else:
            indices = tuple(mask)
            if not indices:
                continue

        override = direct_effects.override_for(target, now=now)
        if override is None:
            continue

        if mask is None:
            sampled = override.sample_frame(
                point_values,
                now=now,
                whole_target=True,
            )
            if sampled is not None:
                values = list(sampled)
            continue

        if override.effect in TRIGGERED_EFFECTS:
            local_points = _normalize_local_points(
                tuple(point_values[index] for index in indices)
            )
            reactive_indices = None
            if override.effect == REACTIVE:
                response_size = override.parameters.response_size or 1
                reactive_indices = _reactive_indices_for_zone_origin(
                    indices,
                    response_size,
                    override.parameters.direction or "AUTO",
                    section_orientations,
                )
            sampled_local = override.sample_frame(
                local_points,
                now=now,
                whole_target=False,
                reactive_indices=reactive_indices,
            )
            if sampled_local is not None:
                for index, colour in zip(indices, sampled_local):
                    values[index] = colour
            continue

        # Normal continuous effects keep the existing profile semantics: sample
        # against complete device geometry, then expose only the zone positions.
        sampled_full = override.sample_frame(
            point_values,
            now=now,
            whole_target=False,
        )
        if sampled_full is not None:
            for index in indices:
                values[index] = sampled_full[index]

    return tuple(values)


def _chroma_surface_specs(
    surface_id: str,
    configuration: ChromaZoneConfiguration,
) -> tuple[tuple[str, tuple[int, ...] | None, tuple[str, ...]], ...]:
    surface_id = str(surface_id).upper()
    surface = chroma_surface(surface_id)
    index_by_cell = {cell.cell_id: index for index, cell in enumerate(surface.cells)}
    specs: list[tuple[str, tuple[int, ...] | None, tuple[str, ...]]] = [
        (surface_id, None, ())
    ]
    for zone in configuration.for_surface(surface_id):
        specs.append(
            (
                zone.target,
                tuple(index_by_cell[cell_id] for cell_id in zone.cells),
                (),
            )
        )
    return tuple(specs)


def _chromalink_specs() -> tuple[tuple[str, tuple[int, ...] | None, tuple[str, ...]], ...]:
    return (
        ("CHROMALINK", None, ()),
        *tuple(
            (target, (index,), ())
            for index, target in enumerate(CHROMALINK_CELL_TARGETS)
        ),
    )


def _overlay_chroma_surface_matrix(
    surface_id: str,
    matrix,
    configuration: ChromaZoneConfiguration,
    *,
    now: float,
):
    surface_id = str(surface_id).upper()
    surface = chroma_surface(surface_id)
    rows = tuple(tuple(row) for row in matrix)
    if len(rows) != surface.rows or any(len(row) != surface.columns for row in rows):
        raise ValueError(f"base matrix for {surface_id} has wrong dimensions")
    base_frame = tuple(rows[cell.row][cell.column] for cell in surface.cells)
    final_frame = _apply_direct_target_frames(
        base_frame,
        surface_cell_points(surface_id),
        _chroma_surface_specs(surface_id, configuration),
        now=now,
    )
    return frame_to_surface_matrix(surface_id, final_frame)


def _logical_chroma_control_targets(
    physical_targets: Iterable[str],
    configuration: ChromaZoneConfiguration,
) -> tuple[str, ...]:
    result: list[str] = []
    for value in physical_targets:
        target = str(value).upper()
        if target not in result:
            result.append(target)
        if target in {"KEYBOARD", "MOUSE"}:
            for zone in configuration.for_surface(target):
                if zone.target not in result:
                    result.append(zone.target)
        elif target == "CHROMALINK":
            for cell_target in CHROMALINK_CELL_TARGETS:
                if cell_target not in result:
                    result.append(cell_target)
    return tuple(result)


def _openrgb_control_targets(renderer: OpenRGBRenderer) -> tuple[str, ...]:
    return tuple(renderer.bindings)


def _overlay_openrgb_target_frames(
    renderer: OpenRGBRenderer,
    target_frames,
    *,
    now: float | None = None,
):
    """Apply the shared temporary-authority compositor to OpenRGB target leaves."""
    timestamp = time.perf_counter() if now is None else float(now)
    values = dict(target_frames)
    bindings = renderer.bindings
    for target, frame in tuple(values.items()):
        binding = bindings.get(target)
        if binding is None:
            continue
        values[target] = _apply_direct_target_frames(
            tuple(frame),
            linear_points(binding.led_count),
            ((target, None, ()),),
            now=timestamp,
        )
    return values


def _govee_control_targets(renderer: EnhancedGoveeRenderer) -> tuple[str, ...]:
    values: list[str] = []
    for device in renderer.devices:
        values.append(device.all_target)
        values.extend(device.target_for_zone(zone) for zone in device.zones)
    return tuple(values)


def _govee_target_specs(device):
    specs: list[tuple[str, tuple[int, ...] | None, tuple[str, ...]]] = [
        (device.all_target, None, ())
    ]
    for zone in device.zones:
        specs.append(
            (
                device.target_for_zone(zone),
                tuple(segment - 1 for segment in zone.segments),
                zone.effective_section_orientations,
            )
        )
    return tuple(specs)


def _overlay_govee_device_frame(device, frame, *, now: float | None = None):
    """Apply whole-device and saved-zone direct authority to one Govee frame."""
    timestamp = time.perf_counter() if now is None else float(now)
    return _apply_direct_target_frames(
        tuple(frame),
        linear_points(device.segment_count),
        _govee_target_specs(device),
        now=timestamp,
    )


def apply_runtime_authority() -> None:
    """Install the direct-override boundary once for Chroma, OpenRGB and Govee."""
    if not getattr(LiveChromaSession, "_edl_direct_authority_applied", False):
        previous_start = LiveChromaSession.start
        previous_release = LiveChromaSession.release
        previous_render_rgb = LiveChromaSession.render_rgb
        previous_render_custom_matrix = LiveChromaSession.render_custom_matrix
        previous_render_chromalink_cells = profile_runner.render_chromalink_cells

        def start(self) -> None:
            previous_start(self)
            configuration = load_chroma_zone_configuration()
            logical_targets = _logical_chroma_control_targets(self.targets, configuration)
            self._edl_direct_zone_configuration = configuration
            self._edl_direct_authority_targets = logical_targets
            lighting_authority.activate_targets(logical_targets)

        def release(self) -> None:
            targets = tuple(
                getattr(self, "_edl_direct_authority_targets", tuple(self.targets))
            )
            try:
                previous_release(self)
            finally:
                _release_authority_targets(targets)

        def render_rgb(self, target: str, rgb: tuple[int, int, int]) -> None:
            timestamp = time.perf_counter()
            normalized = str(target).upper()
            configuration = getattr(
                self,
                "_edl_direct_zone_configuration",
                ChromaZoneConfiguration(),
            )

            if normalized in {"KEYBOARD", "MOUSE"}:
                specs = _chroma_surface_specs(normalized, configuration)
                if _has_direct_for(spec[0] for spec in specs):
                    matrix = uniform_surface_matrix(normalized, rgb)
                    matrix = _overlay_chroma_surface_matrix(
                        normalized,
                        matrix,
                        configuration,
                        now=timestamp,
                    )
                    previous_render_custom_matrix(self, normalized, matrix)
                    return

            if normalized == "CHROMALINK":
                specs = _chromalink_specs()
                if _has_direct_for(spec[0] for spec in specs):
                    frame = _apply_direct_target_frames(
                        (rgb,) * len(CHROMALINK_POINTS),
                        CHROMALINK_POINTS,
                        specs,
                        now=timestamp,
                    )
                    previous_render_chromalink_cells(self, frame)
                    return

            override = _direct_override_rgb(normalized, now=timestamp)
            previous_render_rgb(self, normalized, override if override is not None else rgb)

        def render_custom_matrix(self, target: str, matrix) -> None:
            normalized = str(target).upper()
            if normalized not in {"KEYBOARD", "MOUSE"}:
                override = _direct_override_rgb(normalized, now=time.perf_counter())
                if override is not None:
                    matrix = tuple(tuple(override for _cell in row) for row in matrix)
                previous_render_custom_matrix(self, normalized, matrix)
                return

            configuration = getattr(
                self,
                "_edl_direct_zone_configuration",
                ChromaZoneConfiguration(),
            )
            matrix = _overlay_chroma_surface_matrix(
                normalized,
                matrix,
                configuration,
                now=time.perf_counter(),
            )
            previous_render_custom_matrix(self, normalized, matrix)

        def render_chromalink_cells(session, colours) -> None:
            timestamp = time.perf_counter()
            frame = _apply_direct_target_frames(
                tuple(colours),
                CHROMALINK_POINTS,
                _chromalink_specs(),
                now=timestamp,
            )
            previous_render_chromalink_cells(session, frame)

        LiveChromaSession.start = start
        LiveChromaSession.release = release
        LiveChromaSession.render_rgb = render_rgb
        LiveChromaSession.render_custom_matrix = render_custom_matrix
        LiveChromaSession._edl_direct_authority_applied = True
        profile_runner.render_chromalink_cells = render_chromalink_cells

    if not getattr(OpenRGBRenderer, "_edl_direct_authority_applied", False):
        previous_openrgb_start = OpenRGBRenderer.start
        previous_openrgb_release = OpenRGBRenderer.release
        previous_openrgb_render_target_frames = OpenRGBRenderer.render_target_frames

        def openrgb_start(self) -> None:
            previous_openrgb_start(self)
            targets = _openrgb_control_targets(self)
            self._edl_direct_authority_targets = targets
            lighting_authority.activate_targets(targets)

        def openrgb_release(self) -> None:
            targets = tuple(
                getattr(
                    self,
                    "_edl_direct_authority_targets",
                    _openrgb_control_targets(self),
                )
            )
            try:
                previous_openrgb_release(self)
            finally:
                _release_authority_targets(targets)

        def openrgb_render_target_frames(self, target_frames):
            # Selected OpenRGB targets remain in the continuous renderer path
            # even without a Rule. Their captured pre-EDL frame is the base
            # state, so direct authority can override it and Stand Down reveals
            # it again immediately.
            values = dict(target_frames)
            for target, binding in self.bindings.items():
                if target in values:
                    continue
                captured = self._captured_frame_for(binding)
                if binding.zone is None:
                    values[target] = captured
                else:
                    zone_slice = self._surface_zone_slice(binding)
                    values[target] = captured[zone_slice]

            values = _overlay_openrgb_target_frames(
                self,
                values,
                now=time.perf_counter(),
            )
            return previous_openrgb_render_target_frames(self, values)

        OpenRGBRenderer.start = openrgb_start
        OpenRGBRenderer.release = openrgb_release
        OpenRGBRenderer.render_target_frames = openrgb_render_target_frames
        OpenRGBRenderer._edl_direct_authority_applied = True

    if getattr(EnhancedGoveeRenderer, "_edl_direct_authority_applied", False):
        return

    previous_govee_start = EnhancedGoveeRenderer.start
    previous_govee_release = EnhancedGoveeRenderer.release
    previous_govee_render = EnhancedGoveeRenderer.render
    previous_govee_render_target_frames = EnhancedGoveeRenderer.render_target_frames
    previous_govee_render_device_frames = EnhancedGoveeRenderer.render_device_frames

    def govee_start(self) -> None:
        previous_govee_start(self)
        targets = _govee_control_targets(self)
        self._edl_direct_authority_targets = targets
        lighting_authority.activate_targets(targets)

    def govee_release(self) -> None:
        targets = tuple(
            getattr(self, "_edl_direct_authority_targets", _govee_control_targets(self))
        )
        try:
            previous_govee_release(self)
        finally:
            _release_authority_targets(targets)

    def govee_render(self, target_colours, *, now=None, force=False):
        timestamp = time.perf_counter() if now is None else float(now)
        if not _has_direct_for(_govee_control_targets(self)):
            return previous_govee_render(self, target_colours, now=now, force=force)

        device_frames = {
            device.device_id: _overlay_govee_device_frame(
                device,
                compose_device_frame(device, target_colours),
                now=timestamp,
            )
            for device in self.devices
        }
        return previous_govee_render_device_frames(
            self,
            device_frames,
            now=timestamp,
            force=force,
        )

    def govee_render_target_frames(self, target_frames, *, now=None, force=False):
        timestamp = time.perf_counter() if now is None else float(now)
        if not _has_direct_for(_govee_control_targets(self)):
            return previous_govee_render_target_frames(
                self,
                target_frames,
                now=now,
                force=force,
            )

        device_frames = {
            device.device_id: _overlay_govee_device_frame(
                device,
                compose_device_target_frames(device, target_frames),
                now=timestamp,
            )
            for device in self.devices
        }
        return previous_govee_render_device_frames(
            self,
            device_frames,
            now=timestamp,
            force=force,
        )

    def govee_render_device_frames(self, device_frames, *, now=None, force=False):
        timestamp = time.perf_counter() if now is None else float(now)
        if not _has_direct_for(_govee_control_targets(self)):
            return previous_govee_render_device_frames(
                self,
                device_frames,
                now=now,
                force=force,
            )

        values = dict(device_frames)
        for device in self.devices:
            frame = values.get(device.device_id)
            if frame is not None:
                values[device.device_id] = _overlay_govee_device_frame(
                    device,
                    frame,
                    now=timestamp,
                )
        return previous_govee_render_device_frames(
            self,
            values,
            now=timestamp,
            force=force,
        )

    EnhancedGoveeRenderer.start = govee_start
    EnhancedGoveeRenderer.release = govee_release
    EnhancedGoveeRenderer.render = govee_render
    EnhancedGoveeRenderer.render_target_frames = govee_render_target_frames
    EnhancedGoveeRenderer.render_device_frames = govee_render_device_frames
    EnhancedGoveeRenderer._edl_direct_authority_applied = True
