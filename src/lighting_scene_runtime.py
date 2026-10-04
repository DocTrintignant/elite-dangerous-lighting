#!/usr/bin/env python3
"""Temporary Mode composition at the existing final authority seam.

The accepted direct-authority runtime already owns renderer/device geometry,
zone specificity, Govee orientation and the final Chroma/Govee calls. Scenes
therefore extend only its three authority helpers:

    calculated/trigger state -> temporary Mode -> direct/operator -> accepted renderer

No renderer wrapper, transport, rule engine or effect sampler is duplicated.
"""

from __future__ import annotations

import time
from typing import Iterable, Sequence

import lighting_authority_runtime as authority_runtime
from lighting_scene_authority import SceneAuthorityController, scene_authority

RGB = tuple[int, int, int]
Point = tuple[float, float]
Frame = tuple[RGB, ...]
TargetSpec = tuple[str, tuple[int, ...] | None, tuple[str, ...]]


def _scene_target_snapshot(
    *,
    now: float | None = None,
    controller: SceneAuthorityController = scene_authority,
) -> set[str]:
    return set(controller.active_targets_at(now=now))


def _has_scene_for(
    targets: Iterable[str],
    *,
    now: float | None = None,
    controller: SceneAuthorityController = scene_authority,
) -> bool:
    return bool(set(targets) & _scene_target_snapshot(now=now, controller=controller))


def _scene_override_rgb(
    target: str,
    *,
    now: float,
    controller: SceneAuthorityController = scene_authority,
) -> RGB | None:
    override = controller.override_for(target, now=now)
    if override is None:
        return None
    return override.sample_uniform(now)


def _apply_scene_target_frames(
    base_frame: Sequence[RGB],
    points: Sequence[Point],
    target_specs: Sequence[TargetSpec],
    *,
    now: float,
    controller: SceneAuthorityController = scene_authority,
) -> Frame:
    """Overlay the active temporary Mode before direct/operator authority."""
    values = list(base_frame)
    point_values = tuple(points)
    if not values or len(values) != len(point_values):
        raise ValueError("scene authority frame and geometry must have equal non-zero length")

    for target, mask, _section_orientations in target_specs:
        override = controller.override_for(target, now=now)
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

        indices = tuple(mask)
        if not indices:
            continue
        sampled = override.sample_frame(
            point_values,
            now=now,
            whole_target=False,
        )
        if sampled is not None:
            for index in indices:
                values[index] = sampled[index]

    return tuple(values)


def apply_scene_runtime() -> None:
    """Extend the accepted authority compositor once; do not wrap renderers again."""
    if getattr(authority_runtime, "_edl_scene_authority_applied", False):
        return

    previous_has_direct_for = authority_runtime._has_direct_for
    previous_direct_override_rgb = authority_runtime._direct_override_rgb
    previous_apply_direct_target_frames = authority_runtime._apply_direct_target_frames

    def has_authority_for(targets: Iterable[str]) -> bool:
        # Target collections are sometimes generators. Materialize once so the
        # accepted direct test and the temporary-Mode test inspect the same values.
        values = tuple(targets)
        if previous_has_direct_for(values):
            return True
        return _has_scene_for(values)

    def final_override_rgb(target: str, *, now: float | None = None):
        timestamp = time.perf_counter() if now is None else float(now)

        # Existing direct/operator authority stays final.
        direct = previous_direct_override_rgb(target, now=timestamp)
        if direct is not None:
            return direct
        return _scene_override_rgb(target, now=timestamp)

    def compose_authority_frame(
        base_frame: Sequence[RGB],
        points: Sequence[Point],
        target_specs: Sequence[TargetSpec],
        *,
        now: float,
    ) -> Frame:
        scene_frame = _apply_scene_target_frames(
            base_frame,
            points,
            target_specs,
            now=now,
        )
        return previous_apply_direct_target_frames(
            scene_frame,
            points,
            target_specs,
            now=now,
        )

    authority_runtime._has_direct_for = has_authority_for
    authority_runtime._direct_override_rgb = final_override_rgb
    authority_runtime._apply_direct_target_frames = compose_authority_frame
    authority_runtime._edl_scene_authority_applied = True
