#!/usr/bin/env python3
"""Adapt saved lighting Modes into the accepted temporary authority.

This module deliberately does not introduce another authority controller. It
constructs the same immutable ``SceneRun`` / ``ScenePhase`` values already
consumed by ``lighting_scene_runtime`` and installs them into the existing
``SceneAuthorityController`` lifecycle. The accepted compositor, direct/operator
precedence, renderers and restoration behavior therefore remain unchanged.
"""

from __future__ import annotations

import time

from lighting_authority import resolve_direct_static_target
from lighting_direct_effects import DirectEffectOverride
from lighting_modes import LightingMode
from lighting_scene_authority import (
    SceneAuthorityController,
    ScenePhase,
    SceneRun,
    scene_authority,
)


def start_lighting_mode(
    mode: LightingMode,
    *,
    controller: SceneAuthorityController = scene_authority,
    started_at: float | None = None,
) -> SceneRun:
    """Start one data-driven Mode at the existing temporary-authority seam."""
    if not isinstance(mode, LightingMode):
        raise ValueError("mode must be a LightingMode")

    targets = tuple(
        dict.fromkeys(
            resolve_direct_static_target(target)
            for target in mode.targets
        )
    )
    if not targets:
        raise ValueError("mode has no enabled lighting outputs")

    active = set(controller._authority.active_targets)
    missing = [target for target in targets if target not in active]
    if missing:
        raise RuntimeError(
            "mode target(s) not active in the current lighting session: "
            + ", ".join(missing)
        )

    started = time.perf_counter() if started_at is None else float(started_at)
    elapsed = 0.0
    phases: list[ScenePhase] = []
    for phase in mode.phases:
        phase_start = started + elapsed
        effects = tuple(
            DirectEffectOverride(
                target=output.target,
                effect=output.effect,
                colours=output.colours,
                parameters=output.effect_parameters,
                started_at=phase_start,
            )
            for output in phase.outputs
            if output.enabled
        )
        phases.append(ScenePhase(elapsed, effects))
        elapsed += phase.duration_seconds

    # An empty phase is valid choreography: it reveals the underlying state for
    # that interval while the mode still owns its overall bounded timeline.
    run = SceneRun(
        name=mode.key,
        targets=targets,
        started_at=started,
        duration_seconds=mode.duration_seconds,
        effects=phases[0].effects,
        phases=tuple(phases),
    )

    # Preserve one scripted-scene authority rather than adding a parallel mode
    # controller. Admission and installation are one authority-level operation,
    # so concurrent callers cannot both report success while replacing each other.
    return controller.install_if_idle(run, now=started)


def stop_lighting_mode(
    *,
    controller: SceneAuthorityController = scene_authority,
) -> bool:
    """Stop the active temporary Mode through the accepted lifecycle."""
    return controller.stop()
