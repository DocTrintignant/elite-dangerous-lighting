#!/usr/bin/env python3
"""Final target-aware presentation for spatial effect controls.

The effect domain deliberately remains hardware-neutral. This adapter only hides
controls that have no operator meaning for the currently selected target and
limits direction choices when native Govee geometry is a one-dimensional strip.
Existing saved values are preserved if an older profile contains a direction
that is not meaningful on a linear Govee target.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QWidget

from lighting_effect_config import RIPPLE_DIRECTIONS, WAVE_DIRECTIONS
from lighting_govee_config import parse_all_target, parse_zone_target
from lighting_govee_rule_ui import _device_id_from_ui_target

_GOVEE_WAVE_DIRECTIONS = ("LEFT_TO_RIGHT", "RIGHT_TO_LEFT")
_GOVEE_RIPPLE_DIRECTIONS = (
    "CENTER_OUT",
    "EDGES_IN",
    "LEFT_TO_RIGHT",
    "RIGHT_TO_LEFT",
)


def _set_pair_visible(pair, visible: bool) -> None:
    pair[0].setVisible(bool(visible))
    pair[1].setVisible(bool(visible))


def _govee_selection(window) -> tuple[bool, bool, bool]:
    """Return (only_govee, uses_all, uses_custom_zone) for current output editor."""
    selector = getattr(window, "target_combo", None)
    if selector is None or not hasattr(selector, "targets"):
        return False, False, False
    targets = tuple(selector.targets())
    if not targets:
        return False, False, False

    govee_parents = [target for target in targets if _device_id_from_ui_target(target) is not None]
    concrete_all = [target for target in targets if parse_all_target(target) is not None]
    concrete_zones = [target for target in targets if parse_zone_target(target) is not None]
    govee_count = len(govee_parents) + len(concrete_all) + len(concrete_zones)
    only_govee = govee_count == len(targets)
    if not only_govee:
        return False, False, False

    uses_all = bool(concrete_all)
    uses_custom = bool(concrete_zones)
    actions = getattr(window, "_govee_all_actions", {})
    boxes_by_device = getattr(window, "_govee_zone_boxes", {})
    for parent in govee_parents:
        device_id = _device_id_from_ui_target(parent)
        if device_id is None:
            continue
        action = actions.get(device_id)
        boxes = boxes_by_device.get(device_id, {})
        if action is None or action.isChecked():
            uses_all = True
        elif any(box.isChecked() for box in boxes.values()):
            uses_custom = True
    return True, uses_all, uses_custom


def _display_direction(value: str) -> str:
    return value.replace("_", " ").title()


def _set_direction_choices(
    editor,
    allowed: tuple[str, ...],
    *,
    preserve_unavailable: bool,
) -> None:
    combo = editor.direction[1]
    current = combo.currentData()
    if current is None:
        current = allowed[0]
    current = str(current)

    desired = allowed
    if preserve_unavailable and current not in allowed:
        desired = (*allowed, current)
    existing = tuple(str(combo.itemData(index)) for index in range(combo.count()))
    if existing == desired:
        return

    combo.blockSignals(True)
    try:
        combo.clear()
        for value in allowed:
            combo.addItem(_display_direction(value), value)
        if preserve_unavailable and current not in allowed:
            combo.addItem(
                f"{_display_direction(current)} — not meaningful on linear Govee",
                current,
            )
            item = combo.model().item(combo.count() - 1)
            if item is not None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
    finally:
        combo.blockSignals(False)


def apply_effect_target_ui(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_effect_target_ui_applied", False):
        return

    previous_init = window_class.__init__
    previous_load_draft = window_class._load_draft
    previous_update_test_output_state = window_class._update_test_output_state

    def refresh(self) -> None:
        controls = getattr(self, "effect_controls", None)
        combo = getattr(self, "effect_combo", None)
        if controls is None or combo is None:
            return
        effect = combo.currentText()
        only_govee, uses_all, uses_custom = _govee_selection(self)

        # Origin is currently a native-Govee custom-zone placement control.
        # REACTIVE whole-device ALL keeps the accepted complete-surface invariant,
        # so neither Origin nor Response size has a visible meaning there.
        if effect == "REACTIVE":
            _set_pair_visible(controls.direction, only_govee and uses_custom and not uses_all)
            _set_pair_visible(
                controls.response_size,
                (uses_custom and not uses_all) if only_govee else True,
            )

        # Native Govee geometry is rendered as a one-dimensional segment line;
        # vertical directions therefore have no travel axis on that target.
        if effect == "WAVE":
            _set_direction_choices(
                controls,
                _GOVEE_WAVE_DIRECTIONS if only_govee else WAVE_DIRECTIONS,
                preserve_unavailable=only_govee,
            )
        elif effect == "RIPPLE":
            _set_direction_choices(
                controls,
                _GOVEE_RIPPLE_DIRECTIONS if only_govee else RIPPLE_DIRECTIONS,
                preserve_unavailable=only_govee,
            )

        arrange = getattr(controls, "_arrange", None)
        if callable(arrange):
            arrange()

    def window_init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        refresh(self)

    def load_draft(self, rule, *, title: str) -> None:
        previous_load_draft(self, rule, title=title)
        refresh(self)

    def update_test_output_state(self) -> None:
        previous_update_test_output_state(self)
        refresh(self)

    window_class.__init__ = window_init
    window_class._load_draft = load_draft
    window_class._update_test_output_state = update_test_output_state
    window_class._edl_effect_target_ui_applied = True


# Accepted effect promotion and target-aware controls share one authoring owner.
PROMOTED_CONTINUOUS_EFFECTS = frozenset({"SPECTRUM", "WAVE", "STARLIGHT", "FIRE"})
PROMOTED_TRIGGER_EFFECTS = frozenset({"REACTIVE", "RIPPLE"})
PROMOTED_TRIGGER_EFFECT_ORDER = ("REACTIVE", "RIPPLE")
LIVE_ONLY_EFFECTS = PROMOTED_TRIGGER_EFFECTS
MULTI_COLOUR_EFFECTS = frozenset({"FLASH", "BREATH", "WAVE", "STARLIGHT", "FIRE", "RIPPLE"})


def _ensure_promoted_trigger_effects(window) -> None:
    combo = getattr(window, "effect_combo", None)
    if combo is None:
        return
    for effect in PROMOTED_TRIGGER_EFFECT_ORDER:
        if combo.findText(effect) < 0:
            combo.addItem(effect)
        index = combo.findText(effect)
        combo.setItemData(
            index,
            "Triggered effect — starts once on each real source transition and persists independently until its own duration expires.",
            Qt.ItemDataRole.ToolTipRole,
        )


def apply_effect_ui_promotion(ui_main) -> None:
    parameter_class = ui_main.UnifiedEffectParametersEditor
    palette_class = ui_main.UnifiedPaletteEditor
    window_class = ui_main.MainWindow

    if getattr(window_class, "_edl_effect_promotion_applied", False):
        return

    # REACTIVE/RIPPLE are accepted ordinary Rule choices with trigger-scoped
    # runtime behavior. Do not expose them through the old red/non-selectable
    # pending-effect presentation owned by lighting_ui_main.
    ui_main.PENDING_EFFECTS = ()

    original_window_init = window_class.__init__
    original_load_draft = window_class._load_draft
    original_update_profile_buttons = window_class._update_profile_buttons
    original_parameter_init = parameter_class.__init__
    original_parameter_set_effect = parameter_class.set_effect

    def window_init(self, *args, **kwargs) -> None:
        original_window_init(self, *args, **kwargs)
        _ensure_promoted_trigger_effects(self)

    def load_draft(self, rule, *, title: str) -> None:
        _ensure_promoted_trigger_effects(self)
        original_load_draft(self, rule, title=title)

    def update_profile_buttons(self) -> None:
        original_update_profile_buttons(self)
        _ensure_promoted_trigger_effects(self)

    def parameter_init(self, parent=None):
        original_parameter_init(self, parent)
        for pair in (
            self.duration_seconds,
            self.density,
            self.speed,
            self.width,
            self.response_size,
            self.direction,
        ):
            label, control = pair
            if label in self._groups:
                continue
            self._row.removeWidget(label)
            self._row.removeWidget(control)
            group = QWidget(self)
            line = QHBoxLayout(group)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(5)
            line.addWidget(label)
            line.addWidget(control)
            line.addStretch(1)
            self._groups[label] = group
        self._arrange()

    def arrange(self):
        while self._grid.count():
            self._grid.takeAt(0)
        visible = [pair for pair in self._all_pairs() if not pair[0].isHidden()]
        for index, pair in enumerate(visible):
            group = self._groups.get(pair[0])
            if group is None:
                continue
            group.setVisible(True)
            self._grid.addWidget(group, index // 2, index % 2)
        used = {pair[0] for pair in visible}
        for label, group in self._groups.items():
            if label not in used:
                group.setVisible(False)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)

    def parameter_set_effect(self, effect, parameters) -> None:
        original_parameter_set_effect(self, effect, parameters)

        # The base unified editor predates the promoted effects, so its '?' help
        # text for these shared controls must be updated to match their real
        # effect-specific meaning rather than describing Pulse/Flash only.
        brightness_help = self._help_buttons.get(self.brightness[0])
        if brightness_help is not None:
            brightness_help.set_help(
                "<b>Brightness</b><br><br>How bright this effect can be. "
                "100% uses its selected colours at full brightness; lower values dim it."
            )

        cycle_help = self._help_buttons.get(self.cycle_seconds[0])
        if cycle_help is not None:
            if effect == "SPECTRUM":
                cycle_help.set_help(
                    "<b>Cycle time</b><br><br>How long one complete trip through the rainbow takes."
                )
            elif effect == "WAVE":
                cycle_help.set_help(
                    "<b>Travel time</b><br><br>How long the moving colour pattern takes to travel one complete cycle across the device. Lower is faster."
                )
            elif effect == "STARLIGHT":
                cycle_help.set_help(
                    "<b>Twinkle time</b><br><br>How long one sparkle takes to brighten and fade again."
                )

        step_help = self._help_buttons.get(self.step_seconds[0])
        if step_help is not None:
            if effect == "FIRE":
                step_help.set_help(
                    "<b>Flicker time</b><br><br>How quickly the fire pattern changes. "
                    "Lower is faster and more nervous; higher is slower and smoother."
                )
            elif effect == "FLASH":
                step_help.set_help(
                    "<b>Time per colour</b><br><br>How long each Flash colour stays on before EDL moves to the next colour."
                )
        if hasattr(self, "_grid"):
            self._arrange()

    parameter_class.__init__ = parameter_init
    parameter_class._arrange = arrange
    parameter_class.set_effect = parameter_set_effect

    def palette_controls(self):
        ui_main.EffectPaletteEditor._update_cardinality_controls(self)
        show = self._effect in MULTI_COLOUR_EFFECTS
        self.add_button.setVisible(show)
        self.remove_button.setVisible(show)
        self.add_button.setToolTip("")
        self.remove_button.setToolTip("")

    palette_class._update_cardinality_controls = palette_controls

    original_effect_changed = window_class._effect_changed

    def effect_changed(self, effect: str) -> None:
        original_effect_changed(self, effect)
        palette = tuple(self.palette_editor.palette())
        if not palette:
            palette = ((255, 255, 255),)

        if effect in {"WAVE", "FIRE"} and len(palette) < 2:
            primary = palette[0]
            if effect == "WAVE":
                secondary = (0, 0, 0) if primary != (0, 0, 0) else (255, 255, 255)
            else:
                secondary = (255, 245, 120) if primary != (255, 245, 120) else (255, 24, 0)
            self.palette_editor.set_palette((primary, secondary))
        elif effect in {"SPECTRUM", "REACTIVE"} and len(palette) != 1:
            self.palette_editor.set_palette((palette[0],))

        self.palette_editor.setVisible(effect != "SPECTRUM")
        self._update_test_output_state()

    window_class._effect_changed = effect_changed

    original_update_preview = window_class._update_test_output_state

    def update_preview_state(self) -> None:
        original_update_preview(self)
        if not hasattr(self, "preview_button"):
            return
        effect = self.effect_combo.currentText() if hasattr(self, "effect_combo") else ""
        if effect in LIVE_ONLY_EFFECTS and not self._preview_running():
            self.preview_button.setEnabled(False)
            self.preview_button.setToolTip(
                "Use Start lighting for this trigger effect. REACTIVE and RIPPLE begin from real source transitions, so Preview cannot reproduce their trigger behavior."
            )

    window_class.__init__ = window_init
    window_class._load_draft = load_draft
    window_class._update_profile_buttons = update_profile_buttons
    window_class._update_test_output_state = update_preview_state
    window_class._edl_effect_promotion_applied = True
