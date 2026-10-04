#!/usr/bin/env python3
"""Production seams for trigger-scoped REACTIVE/RIPPLE effects.

This module contains no transport ownership. It only separates trigger outputs
from continuously calculated profile state and overlays physically accepted
per-event trigger frames onto the *current* underlying device frame.

A source rule may now own independent outputs. Trigger classification therefore
happens per RuleOutput, not from the rule's compatibility ``effect`` alias.
Continuous outputs from a mixed rule remain together in ordered base
calculation; enabled REACTIVE/RIPPLE outputs become target-local trigger views
that retain a backlink to the original ordered source rule.

Product invariant: a whole-device target / ALL always owns the complete
addressable surface. Effects may vary spatially across that surface, but they
must not silently shrink ALL to a smaller writable subset. Saved/custom zones
remain explicit subsets and may apply local effect-footprint parameters.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from lighting_chroma_surface_effects import (
    frame_to_surface_matrix,
    surface_cell_points,
)
from lighting_chroma_surfaces import ChromaSurface, chroma_surface
from lighting_chroma_zone_config import ChromaZoneConfiguration
from lighting_chromalink_cells import CHROMALINK_CELL_TARGETS
from lighting_effect_config import (
    REACTIVE,
    REACTIVE_ORIGINS,
    RIPPLE,
    TRIGGERED_EFFECTS,
    default_parameters_for_effect,
)
from lighting_govee_config import (
    SECTION_FORWARD,
    SECTION_ORIENTATIONS,
    SECTION_REVERSED,
    GoveeEnhancedDevice,
)
from lighting_profiles import NativeLightingProfile
from lighting_rules import LightingRule, RuleOutput
from lighting_trigger_runtime import (
    TriggerActivation,
    centered_response_indices,
    compose_trigger_frames,
    sample_trigger_activation_frame,
)

RGB = tuple[int, int, int]
Point = tuple[float, float]
Frame = tuple[RGB, ...]
BLACK: RGB = (0, 0, 0)


class TriggerTargetMasks(dict[str, tuple[int, ...] | None]):
    """Target masks plus optional target-local disconnected-section metadata."""

    def __init__(
        self,
        values: Mapping[str, tuple[int, ...] | None] | None = None,
        *,
        section_orientations: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        super().__init__({} if values is None else values)
        self.section_orientations = {
            str(target): tuple(str(value).upper() for value in orientations)
            for target, orientations in (section_orientations or {}).items()
        }


# Windows keyboard source names -> Razer Generic Super Keyboard 6x22 logical
# cells. Generic CTRL/SHIFT/ALT use the left-hand physical key; side-specific
# names remain exact. Digits are the main alphanumeric row, not the numpad.
_KEYBOARD_KEY_CELLS: dict[str, str] = {
    "ESC": "R0C1",
    "BACKSPACE": "R1C14",
    "TAB": "R2C1",
    "ENTER": "R3C14",
    "SHIFT": "R4C1",
    "CTRL": "R5C1",
    "ALT": "R5C3",
    "SPACE": "R5C7",
    "INSERT": "R1C15",
    "HOME": "R1C16",
    "PAGEUP": "R1C17",
    "DELETE": "R2C15",
    "END": "R2C16",
    "PAGEDOWN": "R2C17",
    "LEFT": "R5C15",
    "UP": "R4C16",
    "RIGHT": "R5C17",
    "DOWN": "R5C16",
    "LCTRL": "R5C1",
    "RCTRL": "R5C14",
    "LSHIFT": "R4C1",
    "RSHIFT": "R4C14",
    "LALT": "R5C3",
    "RALT": "R5C11",
    **{str(value): f"R1C{value + 1}" for value in range(1, 10)},
    "0": "R1C11",
    **{
        key: f"R2C{column}"
        for key, column in zip("QWERTYUIOP", range(2, 12))
    },
    **{
        key: f"R3C{column}"
        for key, column in zip("ASDFGHJKL", range(2, 11))
    },
    **{
        key: f"R4C{column}"
        for key, column in zip("ZXCVBNM", range(3, 10))
    },
    **{f"F{value}": f"R0C{value + 2}" for value in range(1, 13)},
}


def _rule_with_outputs(
    rule: LightingRule,
    outputs: Sequence[RuleOutput],
) -> LightingRule:
    """Derive one ordered source-rule view containing exactly ``outputs``."""
    selected = tuple(outputs)
    if not selected:
        raise ValueError("derived rule view requires at least one output")
    if any(output not in rule.outputs for output in selected):
        raise ValueError("derived outputs must belong to the source rule")
    if selected == rule.outputs:
        return rule

    first = selected[0]
    derived = LightingRule(
        name=rule.name,
        enabled=rule.enabled,
        conditions=rule.conditions,
        keyboard=rule.keyboard,
        button=rule.button,
        axis=rule.axis,
        colour=first.colour,
        colours=first.colours,
        effect=first.effect,
        effect_parameters=first.effect_parameters,
        axis_modulation=first.axis_modulation,
        target=first.target,
        targets=tuple(output.target for output in selected),
        outputs=selected,
    )
    # ``source_rule`` is the stable identity used by diagnostics/order-sensitive
    # production seams after target-local output materialization.
    object.__setattr__(derived, "_source_rule", rule.source_rule)
    return derived


def split_trigger_profile(
    profile: NativeLightingProfile,
) -> tuple[NativeLightingProfile, tuple[LightingRule, ...]]:
    """Return base-calculation profile plus ordered trigger-output rule views."""
    if not isinstance(profile, NativeLightingProfile):
        raise ValueError("profile must be NativeLightingProfile")
    bad_defaults = [
        default.target
        for default in profile.defaults
        if default.effect in TRIGGERED_EFFECTS
    ]
    if bad_defaults:
        raise ValueError(
            "REACTIVE/RIPPLE cannot be profile defaults because trigger effects require a source transition: "
            + ", ".join(bad_defaults)
        )

    base_rules: list[LightingRule] = []
    trigger_rules: list[LightingRule] = []

    for rule in profile.rules:
        all_trigger_outputs = tuple(
            output for output in rule.outputs if output.effect in TRIGGERED_EFFECTS
        )
        bad_global_outputs = [
            output.target for output in all_trigger_outputs if output.target == "GLOBAL"
        ]
        if bad_global_outputs:
            raise ValueError(
                "REACTIVE/RIPPLE require concrete device or zone targets; legacy GLOBAL is not a trigger target"
            )

        base_outputs = tuple(
            output for output in rule.outputs if output.effect not in TRIGGERED_EFFECTS
        )
        active_trigger_outputs = tuple(
            output for output in all_trigger_outputs if output.enabled
        )

        if base_outputs:
            base_rules.append(_rule_with_outputs(rule, base_outputs))

        if not active_trigger_outputs:
            continue

        # Preserve the already-accepted legacy lifecycle for ordinary rules whose
        # complete output set is one shared trigger effect. Mixed/heterogeneous
        # rules require target-local views so every trigger carries its own
        # effect/palette/parameters without affecting sibling outputs.
        if (
            active_trigger_outputs == rule.outputs
            and not rule.has_independent_outputs
        ):
            trigger_rules.append(rule)
        else:
            trigger_rules.extend(
                _rule_with_outputs(rule, (output,))
                for output in active_trigger_outputs
            )

    return replace(profile, rules=tuple(base_rules)), tuple(trigger_rules)


def linear_points(count: int, *, y: float = 0.0) -> tuple[Point, ...]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("linear trigger surface requires a positive position count")
    denominator = max(1, count - 1)
    return tuple((index / denominator, float(y)) for index in range(count))


def _validated_mask(mask: Sequence[int], count: int) -> tuple[int, ...]:
    result: list[int] = []
    for index in mask:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("trigger target masks must contain integer indices")
        if index < 0 or index >= count:
            raise ValueError("trigger target mask index lies outside the target geometry")
        if index not in result:
            result.append(index)
    if not result:
        raise ValueError("trigger target mask must contain at least one position")
    return tuple(result)


def _normalize_local_points(points: Sequence[Point]) -> tuple[Point, ...]:
    """Renormalize a selected target subset to its own 0..1 coordinate space."""
    values = tuple(points)
    if not values:
        raise ValueError("target-local trigger geometry cannot be empty")
    xs = [float(point[0]) for point in values]
    ys = [float(point[1]) for point in values]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x
    span_y = max_y - min_y
    return tuple(
        (
            0.5 if span_x == 0.0 else (float(x) - min_x) / span_x,
            0.5 if span_y == 0.0 else (float(y) - min_y) / span_y,
        )
        for x, y in values
    )


def _contiguous_local_runs(parent_indices: Sequence[int]) -> tuple[tuple[int, ...], ...]:
    """Return local-index runs separated by gaps in the physical parent surface."""
    values = tuple(parent_indices)
    if not values:
        return ()
    runs: list[list[int]] = [[0]]
    for local_index in range(1, len(values)):
        if values[local_index] == values[local_index - 1] + 1:
            runs[-1].append(local_index)
        else:
            runs.append([local_index])
    return tuple(tuple(run) for run in runs)


def _reactive_indices_for_zone_origin(
    parent_indices: Sequence[int],
    response_size: int,
    origin: str,
    section_orientations: Sequence[str] = (),
) -> tuple[int, ...] | None:
    """Resolve REACTIVE Origin against contiguous runs of one explicit zone.

    AUTO returns None so the accepted centered fallback remains untouched.
    Section modes apply the response size independently to every disconnected
    run. Optional per-section orientation changes only logical section Start/End;
    Zone first/last and section Centre keep their already accepted physical
    semantics.
    """
    mode = str(origin or "AUTO").upper()
    if mode == "AUTO":
        return None
    if mode not in REACTIVE_ORIGINS:
        raise ValueError(f"unsupported REACTIVE origin: {origin!r}")
    runs = _contiguous_local_runs(parent_indices)
    if not runs:
        return ()
    width = max(1, int(response_size))

    orientations = tuple(str(value).upper() for value in section_orientations)
    if orientations:
        if len(orientations) != len(runs):
            raise ValueError(
                "REACTIVE section orientation count must match disconnected zone sections"
            )
        if any(value not in SECTION_ORIENTATIONS for value in orientations):
            raise ValueError("unsupported REACTIVE section orientation")
    else:
        orientations = (SECTION_FORWARD,) * len(runs)

    def start(run: tuple[int, ...]) -> tuple[int, ...]:
        return run[: min(width, len(run))]

    def center(run: tuple[int, ...]) -> tuple[int, ...]:
        local = centered_response_indices(len(run), min(width, len(run)))
        return tuple(run[index] for index in local)

    def end(run: tuple[int, ...]) -> tuple[int, ...]:
        return run[max(0, len(run) - width):]

    if mode == "ZONE_START":
        return start(runs[0])
    if mode == "ZONE_END":
        return end(runs[-1])

    result: list[int] = []
    for run_index, run in enumerate(runs):
        if mode == "EACH_SECTION_CENTER":
            result.extend(center(run))
            continue
        logical_run = (
            tuple(reversed(run))
            if orientations[run_index] == SECTION_REVERSED
            else run
        )
        if mode == "EACH_SECTION_START":
            result.extend(start(logical_run))
        elif mode == "EACH_SECTION_END":
            result.extend(end(logical_run))
        else:
            raise ValueError(f"unsupported REACTIVE origin: {origin!r}")
    return tuple(result)


def _is_named_keyboard_cell(surface: ChromaSurface, parent_index: int) -> bool:
    cell = surface.cells[parent_index]
    return cell.label != f"{cell.row},{cell.column}"


def _keyboard_spatial_context(
    surface: ChromaSurface,
    parent_indices: tuple[int, ...],
    target_points: tuple[Point, ...],
    activation: TriggerActivation,
) -> tuple[tuple[int, ...] | None, Point | None]:
    """Return local-zone REACTIVE indices / RIPPLE origin for keyboard sources.

    Whole-device REACTIVE coverage is enforced separately by
    ``overlay_targeted_frame`` because ALL must always mean the complete surface.
    If a saved keyboard zone excludes the triggering key, the accepted zone-center
    fallback remains in force.
    """
    if surface.surface_id != "KEYBOARD" or activation.trigger_key is None:
        return None, None
    cell_id = _KEYBOARD_KEY_CELLS.get(activation.trigger_key.upper())
    if cell_id is None:
        return None, None
    parent_index_by_id = {
        cell.cell_id: index for index, cell in enumerate(surface.cells)
    }
    parent_index = parent_index_by_id.get(cell_id)
    if parent_index is None or parent_index not in parent_indices:
        return None, None
    local_index = parent_indices.index(parent_index)
    origin = target_points[local_index]

    if activation.rule.effect == RIPPLE:
        direction = (
            activation.rule.effect_parameters.direction
            or default_parameters_for_effect(RIPPLE).direction
        )
        return None, origin if direction == "CENTER_OUT" else None

    if activation.rule.effect != REACTIVE:
        return None, None

    response_size = activation.rule.effect_parameters.response_size
    if response_size is None:
        response_size = default_parameters_for_effect(REACTIVE).response_size or 1
    response_size = max(1, min(int(response_size), len(parent_indices)))

    candidate_local_indices = [
        index
        for index, candidate_parent in enumerate(parent_indices)
        if _is_named_keyboard_cell(surface, candidate_parent)
    ]
    if local_index not in candidate_local_indices:
        candidate_local_indices.append(local_index)
    if len(candidate_local_indices) < response_size:
        candidate_local_indices.extend(
            index
            for index in range(len(parent_indices))
            if index not in candidate_local_indices
        )
    ox, oy = origin
    candidate_local_indices.sort(
        key=lambda index: (
            (target_points[index][0] - ox) ** 2 + (target_points[index][1] - oy) ** 2,
            index,
        )
    )
    return tuple(candidate_local_indices[:response_size]), None


def overlay_targeted_frame(
    base_frame: Sequence[RGB],
    points: Sequence[Point],
    activations_by_target: Mapping[str, Sequence[TriggerActivation]],
    target_masks: Mapping[str, Sequence[int] | None],
    now: float,
    *,
    chroma_surface_id: str | None = None,
) -> Frame:
    """Overlay active trigger instances using target-local geometry.

    ``None`` mask is the canonical whole-device/ALL marker and therefore always
    exposes the complete addressable surface to the effect. For REACTIVE this
    means every addressable position participates regardless of ``response_size``.
    A concrete mask is an explicit saved/custom zone; local footprint parameters
    may constrain the effect inside that zone.
    """
    base = tuple(base_frame)
    point_values = tuple(points)
    if not base or len(base) != len(point_values):
        raise ValueError("base frame and trigger geometry must have the same non-zero length")
    surface = chroma_surface(chroma_surface_id) if chroma_surface_id is not None else None
    section_orientation_map = getattr(target_masks, "section_orientations", {})

    effect_frames: list[Frame] = []
    for target, mask in target_masks.items():
        activations = tuple(activations_by_target.get(target, ()))
        if not activations:
            continue

        whole_target = mask is None
        if whole_target:
            indices = tuple(range(len(base)))
            target_points = point_values
        else:
            indices = _validated_mask(mask, len(base))
            target_points = _normalize_local_points(
                tuple(point_values[index] for index in indices)
            )

        for activation in activations:
            reactive_indices = (
                tuple(range(len(target_points)))
                if whole_target and activation.rule.effect == REACTIVE
                else None
            )
            if (
                reactive_indices is None
                and not whole_target
                and activation.rule.effect == REACTIVE
            ):
                response_size = activation.rule.effect_parameters.response_size
                if response_size is None:
                    response_size = default_parameters_for_effect(REACTIVE).response_size or 1
                reactive_indices = _reactive_indices_for_zone_origin(
                    indices,
                    response_size,
                    activation.rule.effect_parameters.direction or "AUTO",
                    section_orientation_map.get(target, ()),
                )
            ripple_origin = None
            if surface is not None:
                keyboard_reactive_indices, ripple_origin = _keyboard_spatial_context(
                    surface,
                    indices,
                    target_points,
                    activation,
                )
                if reactive_indices is None:
                    reactive_indices = keyboard_reactive_indices
            sampled_local = sample_trigger_activation_frame(
                activation,
                target_points,
                now,
                indices=reactive_indices,
                origin=ripple_origin,
            )
            sampled_full = [BLACK] * len(base)
            for index, colour in zip(indices, sampled_local):
                sampled_full[index] = colour
            effect_frames.append(tuple(sampled_full))

    return compose_trigger_frames(base, effect_frames)


def chroma_surface_target_masks(
    surface_id: str,
    configuration: ChromaZoneConfiguration,
) -> dict[str, tuple[int, ...] | None]:
    surface_id = str(surface_id).upper()
    surface = chroma_surface(surface_id)
    index_by_cell = {
        cell.cell_id: index for index, cell in enumerate(surface.cells)
    }
    masks: dict[str, tuple[int, ...] | None] = {surface_id: None}
    for zone in configuration.for_surface(surface_id):
        masks[zone.target] = tuple(index_by_cell[cell_id] for cell_id in zone.cells)
    return masks


def overlay_chroma_surface_matrix(
    surface_id: str,
    base_matrix: Sequence[Sequence[RGB]],
    configuration: ChromaZoneConfiguration,
    activations_by_target: Mapping[str, Sequence[TriggerActivation]],
    now: float,
):
    surface_id = str(surface_id).upper()
    surface = chroma_surface(surface_id)
    rows = tuple(tuple(row) for row in base_matrix)
    if len(rows) != surface.rows or any(len(row) != surface.columns for row in rows):
        raise ValueError(f"base matrix for {surface_id} has wrong dimensions")
    base_frame = tuple(rows[cell.row][cell.column] for cell in surface.cells)
    final_frame = overlay_targeted_frame(
        base_frame,
        surface_cell_points(surface_id),
        activations_by_target,
        chroma_surface_target_masks(surface_id, configuration),
        now,
        chroma_surface_id=surface_id,
    )
    return frame_to_surface_matrix(surface_id, final_frame)


def chromalink_target_masks() -> dict[str, tuple[int, ...] | None]:
    masks: dict[str, tuple[int, ...] | None] = {"CHROMALINK": None}
    masks.update(
        {target: (index,) for index, target in enumerate(CHROMALINK_CELL_TARGETS)}
    )
    return masks


def govee_target_masks(
    device: GoveeEnhancedDevice,
) -> TriggerTargetMasks:
    masks: dict[str, tuple[int, ...] | None] = {device.all_target: None}
    orientations: dict[str, tuple[str, ...]] = {}
    for zone in device.zones:
        target = device.target_for_zone(zone)
        masks[target] = tuple(segment - 1 for segment in zone.segments)
        orientations[target] = zone.effective_section_orientations
    return TriggerTargetMasks(masks, section_orientations=orientations)


def any_active_for_targets(
    activations_by_target: Mapping[str, Sequence[TriggerActivation]],
    targets: Sequence[str] | set[str] | frozenset[str],
) -> bool:
    return any(bool(activations_by_target.get(target)) for target in targets)
