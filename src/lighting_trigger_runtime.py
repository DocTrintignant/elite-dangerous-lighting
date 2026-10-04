#!/usr/bin/env python3
"""Transport-free runtime for REACTIVE/RIPPLE trigger effects.

Triggered effects are evaluated separately from normal calculated rule output.
A trigger rule uses the existing VPC-style source grammar, but only a false->true
transition starts its temporary effect. The first *observable* sample for each
rule establishes baseline and never fires by itself.

Temporary-effect persistence is per trigger event:
- holding a source does not repeatedly fire;
- leaving and re-entering the source condition fires again immediately;
- every new trigger event gets its own lifetime, including repeated triggers of
  the same rule on the same target;
- different trigger rules on the same target coexist until each expires;
- different targets are independent;
- expiry removes only that activation, revealing the still-current underlying
  state plus any other active temporary effects.

Keyboard-triggered events also preserve the key that completed the source
transition. Renderers with a matching physical keyboard geometry may use that as
a spatial origin; unrelated/non-spatial targets continue to use their accepted
center/default origin policy.

Temporary frames are composited as emissive overlays using per-channel maximum.
Black therefore means "no trigger contribution" rather than "erase the other
trigger/base output".
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Any, Iterable, Mapping, Sequence

from lighting_effect_config import (
    REACTIVE,
    RIPPLE,
    TRIGGERED_EFFECTS,
    default_parameters_for_effect,
)
from lighting_effect_frames import (
    FrameEffectParameters,
    Point,
    TriggerFrameContext,
    sample_effect_frame,
)
from lighting_rules import HidAddress, LightingRule, rule_matches

RGB = tuple[int, int, int]
Frame = tuple[RGB, ...]


@dataclass(frozen=True)
class TriggerActivation:
    """One independent temporary effect instance for one logical target."""

    rule: LightingRule
    target: str
    started_at: float
    rule_index: int
    trigger_key: str | None = None

    def elapsed(self, now: float) -> float:
        return max(0.0, float(now) - self.started_at)


def _duration_for(rule: LightingRule) -> float:
    configured = rule.effect_parameters.duration_seconds
    if configured is not None:
        return configured
    default = default_parameters_for_effect(rule.effect).duration_seconds
    if default is None:
        raise ValueError(f"triggered effect {rule.effect!r} has no duration")
    return default


def _source_observable(
    rule: LightingRule,
    state: Mapping[str, Any],
    button_states: Mapping[HidAddress, bool],
    axis_values: Mapping[HidAddress, Real],
) -> bool:
    """Return whether this sample actually knows the rule source state."""
    if rule.keyboard is not None:
        return True
    if rule.button is not None:
        return (rule.button.device, rule.button.button) in button_states
    if rule.axis is not None:
        return (rule.axis.device, rule.axis.axis) in axis_values
    return all(condition.source in state for condition in rule.conditions)


def centered_response_indices(count: int, response_size: int) -> tuple[int, ...]:
    """Return a deterministic centered local response on a linearized surface."""
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("count must be a positive integer")
    if isinstance(response_size, bool) or not isinstance(response_size, int) or response_size <= 0:
        raise ValueError("response_size must be a positive integer")
    width = min(count, response_size)
    start = (count - width) // 2
    return tuple(range(start, start + width))


def centroid(points: Sequence[Point]) -> Point:
    values = tuple(points)
    if not values:
        raise ValueError("points must contain at least one position")
    return (
        sum(float(point[0]) for point in values) / len(values),
        sum(float(point[1]) for point in values) / len(values),
    )


def frame_parameters_from_trigger_rule(rule: LightingRule) -> FrameEffectParameters:
    """Adapt persisted trigger parameters to the accepted frame engine."""
    if rule.effect not in TRIGGERED_EFFECTS:
        raise ValueError(f"effect {rule.effect!r} is not trigger-scoped")
    parameters = rule.effect_parameters
    if rule.effect == REACTIVE:
        return FrameEffectParameters(duration_seconds=parameters.duration_seconds)
    return FrameEffectParameters(
        direction=parameters.direction,
        speed=parameters.speed,
        width=parameters.width,
        duration_seconds=parameters.duration_seconds,
    )


def sample_trigger_activation_frame(
    activation: TriggerActivation,
    points: Sequence[Point],
    now: float,
    *,
    indices: tuple[int, ...] | None = None,
    origin: Point | None = None,
) -> Frame:
    """Sample one active REACTIVE/RIPPLE frame for a concrete target geometry."""
    values = tuple(points)
    if not values:
        raise ValueError("trigger effect requires at least one point")
    elapsed = activation.elapsed(now)
    rule = activation.rule
    parameters = frame_parameters_from_trigger_rule(rule)

    if rule.effect == REACTIVE:
        if indices is None:
            response_size = rule.effect_parameters.response_size
            if response_size is None:
                response_size = default_parameters_for_effect(REACTIVE).response_size or 1
            indices = centered_response_indices(len(values), response_size)
        trigger = TriggerFrameContext(elapsed_seconds=elapsed, indices=tuple(indices))
    elif rule.effect == RIPPLE:
        direction = rule.effect_parameters.direction or default_parameters_for_effect(RIPPLE).direction
        if direction == "CENTER_OUT" and origin is None:
            origin = centroid(values)
        trigger = TriggerFrameContext(elapsed_seconds=elapsed, origin=origin)
    else:
        raise ValueError(f"effect {rule.effect!r} is not trigger-scoped")

    return sample_effect_frame(
        rule.effect,
        values,
        elapsed,
        rule.colours,
        brightness=rule.effect_parameters.brightness,
        trigger=trigger,
        parameters=parameters,
    )


def compose_trigger_frames(
    base_frame: Sequence[RGB],
    effect_frames: Sequence[Sequence[RGB]],
) -> Frame:
    """Compose temporary emissive frames over the current underlying frame."""
    base = tuple(base_frame)
    if not base:
        raise ValueError("base frame must contain at least one position")
    frames = tuple(tuple(frame) for frame in effect_frames)
    for frame in frames:
        if len(frame) != len(base):
            raise ValueError("trigger frames must have the same length as the base frame")

    result: list[RGB] = []
    for index, base_rgb in enumerate(base):
        r, g, b = base_rgb
        for frame in frames:
            fr, fg, fb = frame[index]
            r = max(r, fr)
            g = max(g, fg)
            b = max(b, fb)
        result.append((r, g, b))
    return tuple(result)


def sample_composited_trigger_frame(
    base_frame: Sequence[RGB],
    activations: Sequence[TriggerActivation],
    points: Sequence[Point],
    now: float,
) -> Frame:
    """Sample all active trigger instances and overlay them on current base state."""
    return compose_trigger_frames(
        base_frame,
        tuple(
            sample_trigger_activation_frame(activation, points, now)
            for activation in activations
        ),
    )


class TriggerEffectRuntime:
    """Detect source transitions and own independent per-event lifetimes."""

    def __init__(self, rules: Iterable[LightingRule]) -> None:
        values = tuple(rules)
        if any(not isinstance(rule, LightingRule) for rule in values):
            raise ValueError("trigger rules must contain only LightingRule values")
        invalid = [rule.effect for rule in values if rule.effect not in TRIGGERED_EFFECTS]
        if invalid:
            raise ValueError(
                "TriggerEffectRuntime accepts only REACTIVE/RIPPLE rules; got "
                + ", ".join(invalid)
            )
        self._rules = values
        # None means this rule's source has not been observed yet (or became
        # unknown again after disconnect/state loss). Its next known sample is
        # baseline-only and cannot manufacture a trigger.
        self._previous_matches: list[bool | None] = [None] * len(values)
        self._previous_pressed_keys: frozenset[str] | None = None
        self._active: dict[int, TriggerActivation] = {}
        self._next_activation_id = 0
        self._last_now: float | None = None

    @property
    def rules(self) -> tuple[LightingRule, ...]:
        return self._rules

    def _validate_now(self, now: float) -> float:
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("now must be numeric")
        timestamp = float(now)
        if self._last_now is not None and timestamp < self._last_now:
            raise ValueError("trigger runtime time must not move backwards")
        self._last_now = timestamp
        return timestamp

    def _prune(self, now: float) -> None:
        expired = [
            activation_id
            for activation_id, activation in self._active.items()
            if activation.elapsed(now) >= _duration_for(activation.rule)
        ]
        for activation_id in expired:
            del self._active[activation_id]

    def _by_target(self) -> dict[str, tuple[TriggerActivation, ...]]:
        grouped: dict[str, list[TriggerActivation]] = {}
        for activation in self._active.values():
            grouped.setdefault(activation.target, []).append(activation)
        return {
            target: tuple(sorted(values, key=lambda item: (item.started_at, item.rule_index)))
            for target, values in grouped.items()
        }

    def _append_activation(
        self,
        *,
        rule: LightingRule,
        target: str,
        started_at: float,
        rule_index: int,
        trigger_key: str | None,
    ) -> None:
        activation_id = self._next_activation_id
        self._next_activation_id += 1
        self._active[activation_id] = TriggerActivation(
            rule=rule,
            target=target,
            started_at=started_at,
            rule_index=rule_index,
            trigger_key=trigger_key,
        )

    @staticmethod
    def _keyboard_trigger_key(
        rule: LightingRule,
        pressed: frozenset[str],
        newly_pressed: frozenset[str],
    ) -> str | None:
        if rule.keyboard is None:
            return None
        ordered = tuple(str(key).strip().upper() for key in rule.keyboard.keys)
        candidates = [key for key in ordered if key in newly_pressed]
        if candidates:
            return candidates[-1]
        # Defensive fallback for an unusually coarse polling interval/state
        # transition: prefer the last configured key that is currently down.
        held = [key for key in ordered if key in pressed]
        return held[-1] if held else None

    def update(
        self,
        now: float,
        state: Mapping[str, Any],
        *,
        pressed_keys: Iterable[str] = (),
        button_states: Mapping[HidAddress, bool] | None = None,
        axis_values: Mapping[HidAddress, Real] | None = None,
    ) -> dict[str, tuple[TriggerActivation, ...]]:
        """Observe one source-state sample and return activations grouped by target."""
        timestamp = self._validate_now(now)
        self._prune(timestamp)
        pressed = frozenset(str(key).strip().upper() for key in pressed_keys)
        buttons = button_states or {}
        axes = axis_values or {}
        newly_pressed = (
            frozenset()
            if self._previous_pressed_keys is None
            else pressed - self._previous_pressed_keys
        )

        for index, rule in enumerate(self._rules):
            if not _source_observable(rule, state, buttons, axes):
                self._previous_matches[index] = None
                continue
            is_matching = rule_matches(
                rule,
                state,
                pressed_keys=pressed,
                button_states=buttons,
                axis_values=axes,
            )
            was_matching = self._previous_matches[index]
            if was_matching is None:
                self._previous_matches[index] = is_matching
                continue
            if is_matching and not was_matching:
                trigger_key = self._keyboard_trigger_key(rule, pressed, newly_pressed)
                for target in rule.targets:
                    self._append_activation(
                        rule=rule,
                        target=target,
                        started_at=timestamp,
                        rule_index=index,
                        trigger_key=trigger_key,
                    )
            self._previous_matches[index] = is_matching

        self._previous_pressed_keys = pressed
        return self._by_target()

    def active(self, now: float) -> dict[str, tuple[TriggerActivation, ...]]:
        """Return active effects after expiry pruning without sampling sources."""
        timestamp = self._validate_now(now)
        self._prune(timestamp)
        return self._by_target()

    def clear(self) -> None:
        self._previous_matches = [None] * len(self._rules)
        self._previous_pressed_keys = None
        self._active.clear()
        self._next_activation_id = 0
        self._last_now = None
