#!/usr/bin/env python3
"""Hardware-neutral direct effect overrides above the calculated EDL state.

This module owns temporary operator/COVAS effect intent only. It reuses the
accepted native EDL effect definitions and samplers; renderers and transports
remain unchanged. Direct effects therefore sit above continuously calculated
rule state without copying effect semantics into the COVAS plugin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from threading import RLock
from typing import Iterable, Sequence

from lighting_authority import (
    LightingAuthorityController,
    lighting_authority,
    resolve_direct_static_target,
)
from lighting_effect_config import (
    BREATH,
    FIRE,
    FLASH,
    KNOWN_EFFECTS,
    PULSE,
    REACTIVE,
    RIPPLE,
    SPECTRUM,
    STARLIGHT,
    STATIC,
    TRIGGERED_EFFECTS,
    WAVE,
    EffectParameters,
    default_parameters_for_effect,
    validate_effect_configuration,
)
from lighting_effect_frames import (
    FrameEffectParameters,
    Point,
    TriggerFrameContext,
    sample_effect_frame,
)
from lighting_effect_runtime_frames import sample_persisted_effect_frame
from lighting_intent import (
    BreathParameters,
    FlashParameters,
    LightingIntent,
    PulseParameters,
)
from lighting_intent_eval import sample_intent
from lighting_trigger_runtime import centered_response_indices, centroid

RGB = tuple[int, int, int]
Palette = tuple[RGB, ...]
DIRECT_EFFECTS = (*KNOWN_EFFECTS, *TRIGGERED_EFFECTS)


def _validate_rgb(colour: RGB) -> RGB:
    if (
        not isinstance(colour, tuple)
        or len(colour) != 3
        or any(isinstance(value, bool) or not isinstance(value, int) for value in colour)
        or any(value < 0 or value > 255 for value in colour)
    ):
        raise ValueError("effect override colours must be RGB tuples with values 0..255")
    return colour


def _effective_parameters(effect: str, supplied: EffectParameters | None) -> EffectParameters:
    """Fill omitted direct-control fields from the engine's native defaults."""
    defaults = default_parameters_for_effect(effect)
    if supplied is None:
        return defaults
    if not isinstance(supplied, EffectParameters):
        raise ValueError("effect parameters must be EffectParameters")
    return EffectParameters(
        brightness=supplied.brightness,
        step_seconds=(
            supplied.step_seconds
            if supplied.step_seconds is not None
            else defaults.step_seconds
        ),
        cycle_seconds=(
            supplied.cycle_seconds
            if supplied.cycle_seconds is not None
            else defaults.cycle_seconds
        ),
        minimum_brightness=(
            supplied.minimum_brightness
            if supplied.minimum_brightness is not None
            else defaults.minimum_brightness
        ),
        maximum_brightness=(
            supplied.maximum_brightness
            if supplied.maximum_brightness is not None
            else defaults.maximum_brightness
        ),
        direction=(
            supplied.direction if supplied.direction is not None else defaults.direction
        ),
        density=(supplied.density if supplied.density is not None else defaults.density),
        speed=(supplied.speed if supplied.speed is not None else defaults.speed),
        width=(supplied.width if supplied.width is not None else defaults.width),
        duration_seconds=(
            supplied.duration_seconds
            if supplied.duration_seconds is not None
            else defaults.duration_seconds
        ),
        response_size=(
            supplied.response_size
            if supplied.response_size is not None
            else defaults.response_size
        ),
    )


def _uniform_intent(
    target: str,
    effect: str,
    colours: Palette,
    parameters: EffectParameters,
) -> LightingIntent:
    if effect == STATIC:
        return LightingIntent(
            target=target,
            effect=effect,
            color_one=colours[0],
            colours=colours,
            brightness=parameters.brightness,
        )
    if effect == FLASH:
        assert parameters.step_seconds is not None
        return LightingIntent(
            target=target,
            effect=effect,
            color_one=colours[0],
            color_two=colours[1],
            colours=colours,
            flash=FlashParameters(parameters.step_seconds),
            brightness=parameters.brightness,
        )
    if effect == PULSE:
        assert parameters.cycle_seconds is not None
        assert parameters.minimum_brightness is not None
        assert parameters.maximum_brightness is not None
        return LightingIntent(
            target=target,
            effect=effect,
            color_one=colours[0],
            colours=colours,
            pulse=PulseParameters(
                period_seconds=parameters.cycle_seconds,
                minimum_intensity=parameters.minimum_brightness,
                maximum_intensity=parameters.maximum_brightness,
            ),
        )
    if effect == BREATH:
        assert parameters.cycle_seconds is not None
        return LightingIntent(
            target=target,
            effect=effect,
            color_one=colours[0],
            color_two=colours[1],
            colours=colours,
            breath=BreathParameters(parameters.cycle_seconds),
            brightness=parameters.brightness,
        )
    raise ValueError(f"{effect} is not a uniform intent effect")


@dataclass(frozen=True)
class DirectEffectOverride:
    """One native EDL effect anchored to the operator command activation time."""

    target: str
    effect: str
    colours: Palette
    parameters: EffectParameters
    started_at: float
    _intent: LightingIntent | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        target = resolve_direct_static_target(self.target)
        effect = str(self.effect).strip().upper()
        if effect not in DIRECT_EFFECTS:
            raise ValueError(
                f"unsupported direct effect {effect!r}; expected one of {DIRECT_EFFECTS}"
            )
        colours = tuple(_validate_rgb(colour) for colour in self.colours)
        parameters = _effective_parameters(effect, self.parameters)
        validate_effect_configuration(effect, colours, parameters)
        if isinstance(self.started_at, bool) or not isinstance(
            self.started_at, (int, float)
        ):
            raise ValueError("effect started_at must be numeric")

        intent = None
        if effect in {STATIC, FLASH, PULSE, BREATH}:
            intent = _uniform_intent(target, effect, colours, parameters)

        object.__setattr__(self, "target", target)
        object.__setattr__(self, "effect", effect)
        object.__setattr__(self, "colours", colours)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "started_at", float(self.started_at))
        object.__setattr__(self, "_intent", intent)

    def elapsed(self, now: float | None = None) -> float:
        timestamp = time.perf_counter() if now is None else float(now)
        return max(0.0, timestamp - self.started_at)

    def expired(self, now: float | None = None) -> bool:
        if self.effect not in TRIGGERED_EFFECTS:
            return False
        duration = self.parameters.duration_seconds
        if duration is None:
            duration = default_parameters_for_effect(self.effect).duration_seconds
        assert duration is not None
        return self.elapsed(now) >= duration

    def sample_uniform(self, now: float | None = None) -> RGB | None:
        """Sample one representative RGB; spatial renderers should use sample_frame."""
        if self.expired(now):
            return None
        elapsed = self.elapsed(now)
        if self._intent is not None:
            if self.effect == STATIC:
                return sample_intent(self._intent)
            return sample_intent(self._intent, elapsed_seconds=elapsed)

        if self.effect in {SPECTRUM, WAVE, STARLIGHT, FIRE}:
            return sample_persisted_effect_frame(
                self.effect,
                ((0.5, 0.5),),
                elapsed,
                self.colours,
                self.parameters,
            )[0]

        frame = self.sample_frame(((0.5, 0.5),), now=now, whole_target=True)
        return None if frame is None else frame[0]

    def sample_frame(
        self,
        points: Sequence[Point],
        *,
        now: float | None = None,
        whole_target: bool = False,
        reactive_indices: tuple[int, ...] | None = None,
        origin: Point | None = None,
    ) -> tuple[RGB, ...] | None:
        """Sample the effect over one renderer-supplied normalized geometry."""
        point_values = tuple(points)
        if not point_values:
            raise ValueError("direct effect frame requires at least one point")
        if self.expired(now):
            return None
        elapsed = self.elapsed(now)

        if self._intent is not None:
            if self.effect == STATIC:
                rgb = sample_intent(self._intent)
            else:
                rgb = sample_intent(self._intent, elapsed_seconds=elapsed)
            return tuple(rgb for _point in point_values)

        if self.effect in {SPECTRUM, WAVE, STARLIGHT, FIRE}:
            return tuple(
                sample_persisted_effect_frame(
                    self.effect,
                    point_values,
                    elapsed,
                    self.colours,
                    self.parameters,
                )
            )

        if self.effect == REACTIVE:
            if reactive_indices is None:
                if whole_target:
                    reactive_indices = tuple(range(len(point_values)))
                else:
                    response_size = self.parameters.response_size or 1
                    reactive_indices = centered_response_indices(
                        len(point_values),
                        min(len(point_values), response_size),
                    )
            trigger = TriggerFrameContext(
                elapsed_seconds=elapsed,
                indices=tuple(reactive_indices),
            )
            frame_parameters = FrameEffectParameters(
                duration_seconds=self.parameters.duration_seconds,
            )
        elif self.effect == RIPPLE:
            direction = self.parameters.direction or "CENTER_OUT"
            if direction == "CENTER_OUT" and origin is None:
                origin = centroid(point_values)
            trigger = TriggerFrameContext(
                elapsed_seconds=elapsed,
                origin=origin,
            )
            frame_parameters = FrameEffectParameters(
                direction=direction,
                speed=self.parameters.speed,
                width=self.parameters.width,
                duration_seconds=self.parameters.duration_seconds,
            )
        else:
            raise ValueError(f"unsupported direct effect {self.effect!r}")

        return tuple(
            sample_effect_frame(
                self.effect,
                point_values,
                elapsed,
                self.colours,
                brightness=self.parameters.brightness,
                trigger=trigger,
                parameters=frame_parameters,
            )
        )


class DirectEffectController:
    """Thread-safe temporary effect authority for currently live logical targets."""

    def __init__(
        self,
        authority: LightingAuthorityController = lighting_authority,
    ) -> None:
        self._authority = authority
        self._lock = RLock()
        self._effects: dict[str, DirectEffectOverride] = {}

    def _prune_locked(self, now: float) -> None:
        expired = [
            target
            for target, override in self._effects.items()
            if override.expired(now)
        ]
        for target in expired:
            self._effects.pop(target, None)

    @property
    def direct_override_targets(self) -> tuple[str, ...]:
        now = time.perf_counter()
        with self._lock:
            self._prune_locked(now)
            return tuple(sorted(self._effects))

    def override_for(
        self,
        target: str,
        *,
        now: float | None = None,
    ) -> DirectEffectOverride | None:
        normalized = resolve_direct_static_target(target)
        timestamp = time.perf_counter() if now is None else float(now)
        with self._lock:
            self._prune_locked(timestamp)
            return self._effects.get(normalized)

    def set_effect_overrides(
        self,
        targets: Iterable[str],
        effect: str,
        colours: Palette,
        *,
        parameters: EffectParameters | None = None,
        started_at: float | None = None,
    ) -> tuple[str, ...]:
        """Atomically apply one native effect configuration to active targets."""
        normalized = tuple(
            dict.fromkeys(resolve_direct_static_target(target) for target in targets)
        )
        if not normalized:
            raise ValueError("at least one effect override target is required")

        effect_name = str(effect).strip().upper()
        if effect_name not in DIRECT_EFFECTS:
            raise ValueError(
                f"unsupported direct effect {effect_name!r}; expected one of {DIRECT_EFFECTS}"
            )
        palette = tuple(_validate_rgb(colour) for colour in colours)
        effective = _effective_parameters(effect_name, parameters)
        validate_effect_configuration(effect_name, palette, effective)

        with self._authority.target_lifecycle():
            active = set(self._authority.active_targets)
            missing = [target for target in normalized if target not in active]
            if missing:
                raise RuntimeError(
                    "target(s) not active in the current lighting session: "
                    + ", ".join(missing)
                )

            started = time.perf_counter() if started_at is None else float(started_at)
            overrides = {
                target: DirectEffectOverride(
                    target=target,
                    effect=effect_name,
                    colours=palette,
                    parameters=effective,
                    started_at=started,
                )
                for target in normalized
            }

            with self._lock:
                self._effects.update(overrides)
        return normalized


    def sample_override_for(
        self,
        target: str,
        *,
        now: float | None = None,
    ) -> RGB | None:
        override = self.override_for(target, now=now)
        if override is None:
            return None
        return override.sample_uniform(now)

    def sample_override_frame(
        self,
        target: str,
        points: Sequence[Point],
        *,
        now: float | None = None,
        whole_target: bool = False,
        reactive_indices: tuple[int, ...] | None = None,
        origin: Point | None = None,
    ) -> tuple[RGB, ...] | None:
        override = self.override_for(target, now=now)
        if override is None:
            return None
        return override.sample_frame(
            points,
            now=now,
            whole_target=whole_target,
            reactive_indices=reactive_indices,
            origin=origin,
        )

    def clear_direct_overrides(self, target: str | None = None) -> int:
        with self._lock:
            if target is None:
                count = len(self._effects)
                self._effects.clear()
                return count
            normalized = resolve_direct_static_target(target)
            existed = normalized in self._effects
            self._effects.pop(normalized, None)
            return 1 if existed else 0

    def clear_targets(self, targets: Iterable[str]) -> int:
        cleared = 0
        for target in tuple(targets):
            cleared += self.clear_direct_overrides(target)
        return cleared


direct_effects = DirectEffectController()
