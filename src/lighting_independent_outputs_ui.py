#!/usr/bin/env python3
"""Author one source rule with multiple independent output definitions.

This is deliberately a final UI adapter.  The existing Target and Effect
editors already own accepted Chroma-zone, ChromaLink-cell, Govee ALL/zone,
palette and effect-parameter semantics.  Rather than duplicate those mechanisms,
this layer turns them into the editor for the *currently selected output*.

The source section remains singular.  Each output is persisted as one or more
``RuleOutput`` leaves with its own target(s), effect, palette, parameters and
enabled state.  A target editor selection that expands to several concrete
leaves (for example several saved zones) is kept as one temporary UI group, but
is flattened to canonical RuleOutput leaves when the rule is applied.

Legacy/uniform multi-target rules reopen as one UI output group so accepted
multi-zone and ChromaLink-cell selectors retain their existing physical-editor
semantics. Genuinely heterogeneous v5 rules reopen as independent output rows.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from lighting_ui_tokens import TEXT_SECONDARY

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lighting_effect_config import validate_effect_configuration
from lighting_govee_config import load_govee_configuration
from lighting_govee_rule_ui import UI_DEVICE_PREFIX
from lighting_openrgb_targets import (
    fallback_target_label as openrgb_target_label,
    is_openrgb_target,
)
from lighting_rules import LightingRule, RuleOutput


OUTPUT_HELP = (
    "One source can drive several independent lighting outputs. Select an output "
    "here, then use the existing Lights and Effect controls below to edit only "
    "that output. Each output keeps its own target, effect, colours and parameters."
)


def _target_label(target: str) -> str:
    if target.startswith(UI_DEVICE_PREFIX):
        device_id = target[len(UI_DEVICE_PREFIX):]
        try:
            configuration = load_govee_configuration()
        except Exception:
            configuration = None
        if configuration is not None:
            device = next(
                (value for value in configuration.devices if value.device_id == device_id),
                None,
            )
            if device is not None:
                return device.name
        return "Govee light"
    if target == "GLOBAL":
        return "All connected devices"
    if target in {"KEYBOARD", "MOUSE", "CHROMALINK"}:
        return target
    if target.startswith("CHROMALINK::"):
        return "CHROMALINK / " + target.split("::", 1)[1]
    if target.startswith("CHROMA_ZONE::"):
        parts = target.split("::")
        if len(parts) >= 3:
            return f"{parts[1]} / {parts[-1]}"
    if target.startswith("GOVEE_ENHANCED::"):
        parts = target.split("::")
        if len(parts) >= 3 and parts[-1] == "ALL":
            return f"Govee {parts[1]} / ALL"
        if len(parts) >= 4 and "ZONE" in parts:
            return f"Govee {parts[1]} / {parts[-1]}"
    if is_openrgb_target(target):
        return openrgb_target_label(target)
    return target


def _compact_labels(labels: Sequence[str]) -> str:
    values = tuple(str(value) for value in labels if str(value).strip())
    if not values:
        return "Choose lights"
    if len(values) == 1:
        return values[0]
    return f"{values[0]} + {len(values) - 1} more"


def _group_label(index: int, outputs: Sequence[RuleOutput]) -> str:
    if not outputs:
        return f"Output {index + 1}"
    targets = _compact_labels(
        tuple(_target_label(output.target) for output in outputs)
    )
    effects = tuple(dict.fromkeys(output.effect for output in outputs))
    effect_text = effects[0] if len(effects) == 1 else " + ".join(effects)
    enabled = all(output.enabled for output in outputs)
    suffix = "" if enabled else "  [off]"
    return f"{index + 1}. {targets}  —  {effect_text}{suffix}"


def _editor_rule_from_source(
    source_rule: LightingRule,
    outputs: Sequence[RuleOutput],
) -> LightingRule:
    """Build a temporary legacy-compatible rule for the existing output editor."""
    values = tuple(outputs)
    if not values:
        raise ValueError("An output group cannot be empty")

    editor_outputs = tuple(replace(output, enabled=True) for output in values)
    first = editor_outputs[0]
    return LightingRule(
        name=source_rule.name,
        enabled=source_rule.enabled,
        conditions=source_rule.conditions,
        keyboard=source_rule.keyboard,
        button=source_rule.button,
        axis=source_rule.axis,
        colour=first.colour,
        colours=first.colours,
        effect=first.effect,
        effect_parameters=first.effect_parameters,
        axis_modulation=first.axis_modulation,
        target=first.target,
        targets=tuple(output.target for output in editor_outputs),
        outputs=editor_outputs,
    )


def _final_rule_from_source(
    source_rule: LightingRule,
    outputs: Sequence[RuleOutput],
) -> LightingRule:
    values = tuple(outputs)
    if not values:
        raise ValueError("A rule requires at least one output")

    targets = tuple(output.target for output in values)
    if len(set(targets)) != len(targets):
        duplicates = tuple(
            target
            for target in dict.fromkeys(targets)
            if targets.count(target) > 1
        )
        raise ValueError(
            "Each output target may appear only once in a rule. Duplicate target(s): "
            + ", ".join(duplicates)
        )
    if "GLOBAL" in targets and targets != ("GLOBAL",):
        raise ValueError("Legacy GLOBAL cannot be combined with physical outputs")

    for output in values:
        validate_effect_configuration(
            output.effect,
            output.colours,
            output.effect_parameters,
        )

    first = values[0]
    return LightingRule(
        name=source_rule.name,
        enabled=source_rule.enabled,
        conditions=source_rule.conditions,
        keyboard=source_rule.keyboard,
        button=source_rule.button,
        axis=source_rule.axis,
        colour=first.colour,
        colours=first.colours,
        effect=first.effect,
        effect_parameters=first.effect_parameters,
        axis_modulation=first.axis_modulation,
        target=first.target,
        targets=targets,
        outputs=values,
    )


def apply_independent_outputs_ui(ui_module: Any) -> None:
    """Install independent-output authoring on the final launcher MainWindow."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_independent_outputs_ui_applied", False):
        return

    previous_init = window_class.__init__
    previous_build_target_section = window_class._build_target_section
    previous_build_rule = window_class._build_rule_from_editor
    previous_load_draft = window_class._load_draft
    previous_set_editor_enabled = window_class._set_editor_enabled
    previous_refresh_table = window_class._refresh_table

    def groups(self) -> list[tuple[RuleOutput, ...]]:
        value = getattr(self, "_edl_output_groups", None)
        if value is None:
            value = []
            self._edl_output_groups = value
        return value

    def current_index(self) -> int:
        return int(getattr(self, "_edl_output_index", -1))

    def switching(self) -> bool:
        return bool(getattr(self, "_edl_output_switching", False))

    def set_switching(self, value: bool) -> None:
        self._edl_output_switching = bool(value)

    def status(self, message: str) -> None:
        bar = self.statusBar()
        if bar is not None:
            bar.showMessage(message, 6500)

    def refresh_output_list(self, selected: int | None = None) -> None:
        if not hasattr(self, "output_list"):
            return
        if selected is None:
            selected = current_index(self)
        values = groups(self)
        self.output_list.blockSignals(True)
        try:
            self.output_list.clear()
            for index, output_group in enumerate(values):
                item = QListWidgetItem(_group_label(index, output_group))
                detail = "\n".join(
                    f"{_target_label(output.target)} — {output.effect}"
                    + ("" if output.enabled else " — disabled")
                    for output in output_group
                )
                item.setToolTip(detail)
                self.output_list.addItem(item)
            if values:
                selected = max(0, min(int(selected), len(values) - 1))
                self.output_list.setCurrentRow(selected)
            elif hasattr(self, "output_empty_hint"):
                self.output_empty_hint.show()
        finally:
            self.output_list.blockSignals(False)

        if hasattr(self, "output_empty_hint"):
            self.output_empty_hint.setVisible(not bool(values))

        enabled = bool(getattr(self, "_edl_outputs_editor_enabled", False))
        self.remove_output_button.setEnabled(enabled and len(values) > 1)
        self.add_output_button.setEnabled(enabled)
        self.output_enabled_check.setEnabled(enabled and bool(values))

    def current_visible_label(self) -> None:
        if switching(self) or getattr(self, "_loading_editor", False):
            return
        index = current_index(self)
        if index < 0 or not hasattr(self, "output_list"):
            return
        item = self.output_list.item(index)
        if item is None:
            return
        selector = getattr(self, "target_combo", None)
        if selector is None:
            return
        selected_targets = (
            tuple(selector.targets())
            if hasattr(selector, "targets")
            else ((selector.currentText(),) if selector.currentText() else ())
        )
        target_text = _compact_labels(
            tuple(_target_label(target) for target in selected_targets)
        )
        effect = self.effect_combo.currentText() if hasattr(self, "effect_combo") else ""
        state = "" if self.output_enabled_check.isChecked() else "  [off]"
        item.setText(f"{index + 1}. {target_text or 'Choose lights'}  —  {effect}{state}")

    def capture_current(self) -> LightingRule:
        """Commit the visible legacy editor into the selected output group."""
        built = previous_build_rule(self)
        values = groups(self)
        index = current_index(self)
        if values and 0 <= index < len(values):
            output_enabled = self.output_enabled_check.isChecked()
            captured = tuple(
                replace(output, enabled=output_enabled)
                for output in built.outputs
            )
            values[index] = captured
            refresh_output_list(self, index)
        return built

    def load_group(
        self,
        index: int,
        source_rule: LightingRule,
        *,
        preserve_dirty: bool,
    ) -> None:
        values = groups(self)
        if index < 0 or index >= len(values):
            return
        was_dirty = bool(getattr(self, "_editor_dirty", False))
        title = self.editor_title.text() if hasattr(self, "editor_title") else "Rule"
        editor_rule = _editor_rule_from_source(source_rule, values[index])

        set_switching(self, True)
        self._edl_output_index = index
        try:
            previous_load_draft(self, editor_rule, title=title)
            self.output_enabled_check.blockSignals(True)
            try:
                self.output_enabled_check.setChecked(
                    all(output.enabled for output in values[index])
                )
            finally:
                self.output_enabled_check.blockSignals(False)
            refresh_output_list(self, index)
        finally:
            set_switching(self, False)

        if preserve_dirty:
            self._set_editor_dirty(was_dirty)

    def output_row_changed(self, row: int) -> None:
        if switching(self) or getattr(self, "_loading_editor", False):
            return
        old = current_index(self)
        if row < 0 or row == old:
            return
        try:
            source_rule = capture_current(self)
        except Exception as exc:
            self.output_list.blockSignals(True)
            try:
                self.output_list.setCurrentRow(old)
            finally:
                self.output_list.blockSignals(False)
            status(self, f"Finish the current output before switching: {exc}")
            return
        load_group(self, row, source_rule, preserve_dirty=True)

    def output_enabled_changed(self, checked: bool) -> None:
        if switching(self) or getattr(self, "_loading_editor", False):
            return
        values = groups(self)
        index = current_index(self)
        if 0 <= index < len(values):
            values[index] = tuple(
                replace(output, enabled=bool(checked))
                for output in values[index]
            )
            refresh_output_list(self, index)
            self._set_editor_dirty(True)

    def add_output(self) -> None:
        try:
            source_rule = capture_current(self)
        except Exception as exc:
            status(self, f"Finish the current output before adding another: {exc}")
            return

        values = groups(self)
        index = current_index(self)
        if not values or index < 0:
            return
        template = values[index][0]
        used = {output.target for group in values for output in group}
        target = next(
            (candidate for candidate in ("KEYBOARD", "MOUSE", "CHROMALINK") if candidate not in used),
            template.target,
        )
        new_output = replace(template, target=target, enabled=True)
        values.append((new_output,))
        new_index = len(values) - 1
        load_group(self, new_index, source_rule, preserve_dirty=False)
        self._set_editor_dirty(True)
        if target == template.target:
            status(self, "New output added. Choose a different Lights target before Apply.")

    def remove_output(self) -> None:
        values = groups(self)
        if len(values) <= 1:
            status(self, "A rule must keep at least one output.")
            return
        try:
            source_rule = capture_current(self)
        except Exception as exc:
            status(self, f"Finish the current output before removing it: {exc}")
            return
        index = current_index(self)
        if index < 0:
            return
        del values[index]
        next_index = min(index, len(values) - 1)
        load_group(self, next_index, source_rule, preserve_dirty=False)
        self._set_editor_dirty(True)

    def build_target_section(self):
        section = previous_build_target_section(self)
        self._edl_output_groups = []
        self._edl_output_index = -1
        self._edl_output_switching = False
        self._edl_outputs_editor_enabled = False

        host = QWidget(section)
        root = QVBoxLayout(host)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(6)

        heading = QHBoxLayout()
        title = QLabel("Outputs for this source")
        title.setStyleSheet("font-weight: 600;")
        title.setToolTip(OUTPUT_HELP)
        heading.addWidget(title)
        hint = QLabel("Select one, then edit Lights + Effect below")
        hint.setStyleSheet("color:#999999; font-size:9.75pt;")
        hint.setToolTip(OUTPUT_HELP)
        heading.addWidget(hint)
        heading.addStretch(1)
        root.addLayout(heading)

        self.output_list = QListWidget(host)
        self.output_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.output_list.setMinimumHeight(78)
        self.output_list.setMaximumHeight(145)
        self.output_list.currentRowChanged.connect(
            lambda row: output_row_changed(self, row)
        )
        root.addWidget(self.output_list)

        self.output_empty_hint = QLabel("No outputs yet — click + Add output.")
        self.output_empty_hint.setStyleSheet(f"color:{TEXT_SECONDARY}; padding:4px 2px;")
        root.addWidget(self.output_empty_hint)

        actions = QHBoxLayout()
        self.add_output_button = QPushButton("+ Add output", host)
        self.add_output_button.setToolTip(
            "Add another independently configurable output to this same source rule."
        )
        self.add_output_button.clicked.connect(lambda: add_output(self))
        actions.addWidget(self.add_output_button)

        self.remove_output_button = QPushButton("Remove output", host)
        self.remove_output_button.clicked.connect(lambda: remove_output(self))
        actions.addWidget(self.remove_output_button)

        self.output_enabled_check = QCheckBox("Output enabled", host)
        self.output_enabled_check.setChecked(True)
        self.output_enabled_check.setToolTip(
            "Disable only this output while leaving the source rule and its other outputs active."
        )
        self.output_enabled_check.toggled.connect(
            lambda checked: output_enabled_changed(self, checked)
        )
        actions.addWidget(self.output_enabled_check)
        actions.addStretch(1)
        root.addLayout(actions)

        section.root.insertWidget(1, host)
        refresh_output_list(self)
        return section

    def window_init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        if hasattr(self, "target_combo"):
            self.target_combo.currentTextChanged.connect(
                lambda _text: current_visible_label(self)
            )
        if hasattr(self, "effect_combo"):
            self.effect_combo.currentTextChanged.connect(
                lambda _text: current_visible_label(self)
            )

    def load_draft(self, rule: LightingRule, *, title: str) -> None:
        output_values = tuple(rule.outputs)
        if not output_values:
            output_values = (
                RuleOutput(
                    target=rule.target,
                    colour=rule.colour,
                    effect=rule.effect,
                    colours=rule.colours,
                    effect_parameters=rule.effect_parameters,
                    axis_modulation=rule.axis_modulation,
                ),
            )

        if rule.has_independent_outputs:
            self._edl_output_groups = [(output,) for output in output_values]
        else:
            self._edl_output_groups = [output_values]
        self._edl_output_index = 0

        editor_rule = _editor_rule_from_source(rule, self._edl_output_groups[0])
        set_switching(self, True)
        try:
            previous_load_draft(self, editor_rule, title=title)
            self.output_enabled_check.blockSignals(True)
            try:
                self.output_enabled_check.setChecked(
                    all(output.enabled for output in self._edl_output_groups[0])
                )
            finally:
                self.output_enabled_check.blockSignals(False)
            refresh_output_list(self, 0)
        finally:
            set_switching(self, False)

    def build_rule_from_editor(self) -> LightingRule:
        source_rule = capture_current(self)
        flattened = tuple(
            output
            for output_group in groups(self)
            for output in output_group
        )
        return _final_rule_from_source(source_rule, flattened)

    def set_editor_enabled(self, enabled: bool) -> None:
        previous_set_editor_enabled(self, enabled)
        self._edl_outputs_editor_enabled = bool(enabled)
        if hasattr(self, "output_list"):
            self.output_list.setEnabled(bool(enabled))
            refresh_output_list(self)

    def refresh_table(self, selected_rows=None) -> None:
        previous_refresh_table(self, selected_rows)
        profile = getattr(self, "_profile", None)
        if profile is None:
            return
        for row, rule in enumerate(profile.rules):
            outputs = tuple(rule.outputs)
            if not outputs:
                continue
            effects = tuple(dict.fromkeys(output.effect for output in outputs))
            effect_item = self.table.item(row, 3)
            if effect_item is not None:
                effect_item.setText(effects[0] if len(effects) == 1 else " + ".join(effects))
            target_item = self.table.item(row, 1)
            if target_item is not None:
                target_item.setText(
                    _compact_labels(
                        tuple(_target_label(output.target) for output in outputs)
                    )
                )
            details = "\n".join(
                f"{index + 1}. {_target_label(output.target)} — {output.effect}"
                + ("" if output.enabled else " — disabled")
                for index, output in enumerate(outputs)
            )
            if target_item is not None:
                target_item.setToolTip(details)
            if effect_item is not None:
                effect_item.setToolTip(details)
            colour_item = self.table.item(row, 6)
            palettes = tuple(dict.fromkeys(output.colours for output in outputs))
            if colour_item is not None and len(palettes) > 1:
                colour_item.setText("Per output")
                colour_item.setToolTip(details)

    window_class.__init__ = window_init
    window_class._build_target_section = build_target_section
    window_class._load_draft = load_draft
    window_class._build_rule_from_editor = build_rule_from_editor
    window_class._set_editor_enabled = set_editor_enabled
    window_class._refresh_table = refresh_table
    window_class._edl_independent_outputs_ui_applied = True
