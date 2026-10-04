#!/usr/bin/env python3
"""Generic temporary scripted-lighting authority.

This layer exists only to run data-driven EDL Modes. It owns one temporary
SceneRun above continuously calculated Rule state and below explicit direct
operator/COVAS overrides. It contains no built-in named scenes and no hardware
or transport logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from threading import RLock
from typing import Iterable

from lighting_authority import (
    LightingAuthorityController,
    lighting_authority,
    resolve_direct_static_target,
)
from lighting_direct_effects import DirectEffectOverride


@dataclass(frozen=True)
class ScenePhase:
    """One effect set that becomes active at an elapsed Mode offset."""

    starts_at_seconds: float
    effects: tuple[DirectEffectOverride, ...]


@dataclass(frozen=True)
class SceneRun:
    """One bounded data-driven Mode activation."""

    name: str
    targets: tuple[str, ...]
    started_at: float
    duration_seconds: float
    effects: tuple[DirectEffectOverride, ...]
    phases: tuple[ScenePhase, ...] = ()

    def elapsed(self, now: float | None = None) -> float:
        timestamp = time.perf_counter() if now is None else float(now)
        return max(0.0, timestamp - self.started_at)

    def remaining(self, now: float | None = None) -> float:
        return max(0.0, self.duration_seconds - self.elapsed(now))

    def expired(self, now: float | None = None) -> bool:
        return self.remaining(now) <= 0.0

    def _phase_effects(self, now: float | None = None) -> tuple[DirectEffectOverride, ...]:
        if not self.phases:
            return self.effects
        elapsed = self.elapsed(now)
        selected = self.phases[0]
        for phase in self.phases[1:]:
            if elapsed < phase.starts_at_seconds:
                break
            selected = phase
        return selected.effects

    def effect_for(
        self,
        target: str,
        *,
        now: float | None = None,
    ) -> DirectEffectOverride | None:
        normalized = resolve_direct_static_target(target)
        return next(
            (
                effect
                for effect in self._phase_effects(now)
                if effect.target == normalized
            ),
            None,
        )


class SceneAlreadyActiveError(RuntimeError):
    """Raised when another Mode already owns the temporary scene layer."""

    def __init__(self, active_scene: str) -> None:
        self.active_scene = active_scene
        super().__init__(f"{active_scene} already owns the scripted Mode layer")


class SceneAuthorityController:
    """Thread-safe single-Mode temporary authority."""

    def __init__(
        self,
        authority: LightingAuthorityController = lighting_authority,
    ) -> None:
        self._authority = authority
        self._lock = RLock()
        self._active: SceneRun | None = None

    @staticmethod
    def _filter_phase_targets(
        phases: tuple[ScenePhase, ...],
        kept_targets: set[str],
    ) -> tuple[ScenePhase, ...]:
        return tuple(
            ScenePhase(
                starts_at_seconds=phase.starts_at_seconds,
                effects=tuple(
                    effect for effect in phase.effects if effect.target in kept_targets
                ),
            )
            for phase in phases
        )

    def _prune_locked(self, now: float) -> None:
        run = self._active
        if run is None:
            return
        if run.expired(now):
            self._active = None
            return

        active = set(self._authority.active_targets)
        kept_targets = tuple(target for target in run.targets if target in active)
        if kept_targets == run.targets:
            return
        if not kept_targets:
            self._active = None
            return

        kept = set(kept_targets)
        self._active = SceneRun(
            name=run.name,
            targets=kept_targets,
            started_at=run.started_at,
            duration_seconds=run.duration_seconds,
            effects=tuple(effect for effect in run.effects if effect.target in kept),
            phases=self._filter_phase_targets(run.phases, kept),
        )

    def install_if_idle(
        self,
        run: SceneRun,
        *,
        now: float | None = None,
    ) -> SceneRun:
        """Atomically install one already-built Mode run without replacement."""
        if not isinstance(run, SceneRun):
            raise ValueError("run must be a SceneRun")
        timestamp = time.perf_counter() if now is None else float(now)
        normalized = tuple(
            dict.fromkeys(resolve_direct_static_target(target) for target in run.targets)
        )
        if not normalized:
            raise ValueError("Mode run requires at least one target")

        active = set(self._authority.active_targets)
        missing = [target for target in normalized if target not in active]
        if missing:
            raise RuntimeError(
                "Mode target(s) not active in the current lighting session: "
                + ", ".join(missing)
            )

        normalized_run = (
            run
            if normalized == run.targets
            else SceneRun(
                name=run.name,
                targets=normalized,
                started_at=run.started_at,
                duration_seconds=run.duration_seconds,
                effects=run.effects,
                phases=run.phases,
            )
        )

        with self._lock:
            self._prune_locked(timestamp)
            if self._active is not None:
                raise SceneAlreadyActiveError(self._active.name)
            self._active = normalized_run
            return normalized_run

    def active_scene(self, *, now: float | None = None) -> SceneRun | None:
        timestamp = time.perf_counter() if now is None else float(now)
        with self._lock:
            self._prune_locked(timestamp)
            return self._active

    def active_targets_at(self, *, now: float | None = None) -> tuple[str, ...]:
        run = self.active_scene(now=now)
        return () if run is None else run.targets

    @property
    def active_targets(self) -> tuple[str, ...]:
        return self.active_targets_at()

    def override_for(
        self,
        target: str,
        *,
        now: float | None = None,
    ) -> DirectEffectOverride | None:
        normalized = resolve_direct_static_target(target)
        timestamp = time.perf_counter() if now is None else float(now)
        run = self.active_scene(now=timestamp)
        if run is None:
            return None
        return run.effect_for(normalized, now=timestamp)

    def remaining_seconds(self, *, now: float | None = None) -> float:
        timestamp = time.perf_counter() if now is None else float(now)
        run = self.active_scene(now=timestamp)
        return 0.0 if run is None else run.remaining(timestamp)

    def stop(self) -> bool:
        with self._lock:
            existed = self._active is not None
            self._active = None
            return existed

    def clear_targets(self, targets: Iterable[str]) -> int:
        released = {
            resolve_direct_static_target(target)
            for target in tuple(targets)
        }
        if not released:
            return 0
        with self._lock:
            run = self._active
            if run is None:
                return 0
            kept_targets = tuple(target for target in run.targets if target not in released)
            removed = len(run.targets) - len(kept_targets)
            if removed == 0:
                return 0
            if not kept_targets:
                self._active = None
                return removed
            kept = set(kept_targets)
            self._active = SceneRun(
                name=run.name,
                targets=kept_targets,
                started_at=run.started_at,
                duration_seconds=run.duration_seconds,
                effects=tuple(effect for effect in run.effects if effect.target in kept),
                phases=self._filter_phase_targets(run.phases, kept),
            )
            return removed


scene_authority = SceneAuthorityController()
