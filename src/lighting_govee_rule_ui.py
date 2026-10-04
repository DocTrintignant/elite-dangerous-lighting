#!/usr/bin/env python3
"""Govee device, ALL and saved-zone rule authoring.

Rule/domain storage remains unchanged: the engine receives stable native Govee
whole-device and zone targets. This module owns only their operator-facing
parent/leaf presentation and resolution into concrete persisted targets.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lighting_govee_config import GoveeConfiguration, parse_all_target, parse_zone_target

UI_DEVICE_PREFIX = "GOVEE_DEVICE::"


def _ui_device_target(device_id: str) -> str:
    return f"{UI_DEVICE_PREFIX}{device_id}"


def _device_id_from_ui_target(target: str) -> str | None:
    if not isinstance(target, str) or not target.startswith(UI_DEVICE_PREFIX):
        return None
    value = target[len(UI_DEVICE_PREFIX):]
    return value or None


def apply_govee_rule_ui(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_govee_rule_ui_applied", False):
        return

    previous_init = window_class.__init__
    previous_build_target_section = window_class._build_target_section
    previous_load_draft = window_class._load_draft
    previous_build_rule = window_class._build_rule_from_editor
    previous_target_changed = window_class._target_changed
    previous_set_editor_enabled = window_class._set_editor_enabled
    previous_update_profile_buttons = window_class._update_profile_buttons

    def configuration(self) -> GoveeConfiguration:
        value = getattr(self, "_govee_configuration", None)
        return value if isinstance(value, GoveeConfiguration) else GoveeConfiguration()

    def zone_target_map(self):
        return configuration(self).target_map()

    def hide_leaf_targets(self) -> None:
        combo = getattr(self, "target_combo", None)
        if combo is None or not hasattr(combo, "_boxes"):
            return
        for leaf in zone_target_map(self):
            box = combo._boxes.get(leaf)
            if box is None:
                continue
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
            box.setVisible(False)

    def ensure_device_targets(self) -> None:
        combo = getattr(self, "target_combo", None)
        if combo is None or not hasattr(combo, "_ensure_target"):
            return
        devices = configuration(self).devices
        same_sku_counts: dict[str, int] = {}
        for device in devices:
            same_sku_counts[device.sku] = same_sku_counts.get(device.sku, 0) + 1
        active_ui_targets = set()
        for device in devices:
            parent = _ui_device_target(device.device_id)
            active_ui_targets.add(parent)
            combo._ensure_target(parent)
            box = combo._boxes[parent]
            label = f"Govee {device.sku}"
            if same_sku_counts.get(device.sku, 0) > 1:
                label += f" · {device.name}"
            box.setText(label)
            if not device.zones:
                box.setToolTip(
                    f"{label} has no saved zones yet. Open Govee zone setup first."
                )
            else:
                box.setToolTip(
                    f"Select {label}, then choose one or more of its saved Govee zones below."
                )
            box.setVisible(True)
        for target, box in combo._boxes.items():
            if target.startswith(UI_DEVICE_PREFIX) and target not in active_ui_targets:
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
                box.setVisible(False)
        hide_leaf_targets(self)

    def current_selected_zone_targets(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        boxes = getattr(self, "_govee_zone_boxes", {})
        for device_id, device_boxes in boxes.items():
            result[device_id] = {
                target for target, box in device_boxes.items() if box.isChecked()
            }
        return result

    def expand_ui_targets(self, ui_targets: tuple[str, ...]) -> tuple[str, ...]:
        """Resolve UI-only Govee device parents to concrete persisted zone leaves.

        This must happen *before* the normal rule builder validates LightingRule.
        ``GOVEE_DEVICE::...`` is presentation-only and is never a domain target.
        """
        selected_by_device = current_selected_zone_targets(self)
        expanded: list[str] = []
        for target in ui_targets:
            device_id = _device_id_from_ui_target(target)
            if device_id is None:
                expanded.append(target)
                continue
            device = next(
                (
                    value
                    for value in configuration(self).devices
                    if value.device_id == device_id
                ),
                None,
            )
            if device is None:
                raise ValueError("The selected Govee device is no longer configured")
            selected = selected_by_device.get(device_id, set())
            ordered = [
                device.target_for_zone(zone)
                for zone in device.zones
                if device.target_for_zone(zone) in selected
            ]
            if not ordered:
                raise ValueError(f"Choose at least one zone for {device.name}")
            expanded.extend(ordered)
        if not expanded:
            raise ValueError("Select at least one lighting target")
        if len(set(expanded)) != len(expanded):
            raise ValueError("lighting targets must not contain duplicates")
        return tuple(expanded)

    def update_zone_button_text(self, device_id: str) -> None:
        button = getattr(self, "_govee_zone_buttons", {}).get(device_id)
        boxes = getattr(self, "_govee_zone_boxes", {}).get(device_id, {})
        if button is None:
            return
        device = next(
            (value for value in configuration(self).devices if value.device_id == device_id),
            None,
        )
        if device is None:
            return
        selected_names = [
            zone.name
            for zone in device.zones
            if (
                (box := boxes.get(device.target_for_zone(zone))) is not None
                and box.isChecked()
            )
        ]
        if not selected_names:
            text = "Choose zones"
        elif len(selected_names) == 1:
            text = selected_names[0]
        else:
            text = f"{selected_names[0]} + {len(selected_names) - 1} more"
        button.setText(text)

    def zone_changed(self, device_id: str | None = None) -> None:
        if device_id is not None:
            update_zone_button_text(self, device_id)
        if getattr(self, "_loading_editor", False):
            return
        self._set_editor_dirty(True)
        self._update_test_output_state()

    def rebuild_zone_controls(self, preserve: dict[str, set[str]] | None = None) -> None:
        host = getattr(self, "_govee_zone_host", None)
        layout = getattr(self, "_govee_zone_layout", None)
        if host is None or layout is None:
            return
        if preserve is None:
            preserve = current_selected_zone_targets(self)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                while child_layout.count():
                    sub = child_layout.takeAt(0)
                    sub_widget = sub.widget()
                    if sub_widget is not None:
                        sub_widget.deleteLater()
        self._govee_zone_boxes = {}
        self._govee_zone_buttons = {}
        self._govee_zone_rows = {}
        for device in configuration(self).devices:
            row_widget = QWidget(host)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(9)
            label = QLabel(f"{device.name} zones")
            label.setToolTip(
                "Choose which saved zones on this Govee should receive this rule. "
                "Zone names come from Govee zone setup."
            )
            row.addWidget(label)
            device_boxes: dict[str, QCheckBox] = {}
            wanted = preserve.get(device.device_id, set())
            if device.zones:
                button = QToolButton(row_widget)
                button.setMinimumWidth(150)
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                button.setToolTip(
                    "Choose one or more saved Govee zones for this rule. "
                    "Ticked zones are all used by the rule."
                )
                menu = QMenu(button)
                button.setMenu(menu)
                for zone in device.zones:
                    target = device.target_for_zone(zone)
                    box = QCheckBox(zone.name, menu)
                    box.setChecked(target in wanted)
                    box.setToolTip(
                        f"Use the saved Govee zone {zone.name!r} for this rule."
                    )
                    box.toggled.connect(
                        lambda _checked=False, device_id=device.device_id: zone_changed(
                            self, device_id
                        )
                    )
                    action = QWidgetAction(menu)
                    action.setDefaultWidget(box)
                    menu.addAction(action)
                    device_boxes[target] = box
                row.addWidget(button)
                self._govee_zone_buttons[device.device_id] = button
            else:
                empty = QLabel("No zones saved — open Govee zone setup first")
                empty.setStyleSheet("color:#999999;")
                row.addWidget(empty)
            row.addStretch(1)
            self._govee_zone_boxes[device.device_id] = device_boxes
            self._govee_zone_rows[device.device_id] = row_widget
            layout.addWidget(row_widget)
            update_zone_button_text(self, device.device_id)
        update_zone_control_state(self)

    def update_zone_control_state(self) -> None:
        combo = getattr(self, "target_combo", None)
        boxes = getattr(self, "_govee_zone_boxes", {})
        buttons = getattr(self, "_govee_zone_buttons", {})
        rows = getattr(self, "_govee_zone_rows", {})
        host = getattr(self, "_govee_zone_host", None)
        if combo is None:
            return
        selected = set(combo.targets())
        editor_enabled = bool(getattr(self, "_govee_editor_enabled", False))
        devices = {device.device_id: device for device in configuration(self).devices}
        any_visible = False
        for device_id, device_boxes in boxes.items():
            device = devices.get(device_id)
            selected_parent = _ui_device_target(device_id) in selected
            row_widget = rows.get(device_id)
            if row_widget is not None:
                row_widget.setVisible(selected_parent)
            any_visible = any_visible or selected_parent
            active = (
                editor_enabled
                and selected_parent
                and device is not None
            )
            for box in device_boxes.values():
                box.setEnabled(active)
            button = buttons.get(device_id)
            if button is not None:
                button.setEnabled(active and bool(device_boxes))
        if host is not None:
            host.setVisible(any_visible)

    def build_target_section(self):
        section = previous_build_target_section(self)
        self._govee_zone_host = QWidget(section)
        self._govee_zone_layout = QVBoxLayout(self._govee_zone_host)
        self._govee_zone_layout.setContentsMargins(0, 2, 0, 0)
        self._govee_zone_layout.setSpacing(5)
        section.root.addWidget(self._govee_zone_host)
        ensure_device_targets(self)
        rebuild_zone_controls(self)
        QTimer.singleShot(
            0,
            lambda: (
                ensure_device_targets(self),
                hide_leaf_targets(self),
                update_zone_control_state(self),
            ),
        )
        return section

    def __init__(self) -> None:
        previous_init(self)
        ensure_device_targets(self)
        hide_leaf_targets(self)
        if hasattr(self, "_govee_zone_host"):
            rebuild_zone_controls(self)

    def set_editor_enabled(self, enabled: bool) -> None:
        previous_set_editor_enabled(self, enabled)
        self._govee_editor_enabled = bool(enabled)
        ensure_device_targets(self)
        devices = {device.device_id: device for device in configuration(self).devices}
        combo = getattr(self, "target_combo", None)
        if combo is not None and hasattr(combo, "_boxes"):
            for device_id, device in devices.items():
                box = combo._boxes.get(_ui_device_target(device_id))
                if box is not None:
                    box.setEnabled(bool(enabled) and bool(device.zones))
        update_zone_control_state(self)
        hide_leaf_targets(self)

    def load_draft(self, rule, *, title: str) -> None:
        original_targets = tuple(rule.targets)
        mapping = zone_target_map(self)
        selected_zones: dict[str, set[str]] = {}
        collapsed: list[str] = []
        seen_devices: set[str] = set()
        for target in original_targets:
            entry = mapping.get(target)
            if entry is None:
                collapsed.append(target)
                continue
            device, _zone = entry
            selected_zones.setdefault(device.device_id, set()).add(target)
            if device.device_id not in seen_devices:
                collapsed.append(_ui_device_target(device.device_id))
                seen_devices.add(device.device_id)
        if not collapsed:
            collapsed = list(original_targets)
        collapsed_rule = replace(
            rule,
            target=collapsed[0],
            targets=tuple(collapsed),
        )
        previous_load_draft(self, collapsed_rule, title=title)
        previous_loading = self._loading_editor
        self._loading_editor = True
        try:
            ensure_device_targets(self)
            rebuild_zone_controls(self, selected_zones)
        finally:
            self._loading_editor = previous_loading
        update_zone_control_state(self)
        hide_leaf_targets(self)

    def build_rule_from_editor(self):
        # The parent selector exists only for operator clarity. Resolve it to
        # concrete leaf targets before the existing rule builders construct and
        # validate LightingRule. Otherwise the temporary UI parent can violate
        # the domain's target == targets[0] compatibility invariant before this
        # adapter gets a chance to expand it.
        ui_targets = tuple(self.target_combo.targets())
        concrete_targets = expand_ui_targets(self, ui_targets)

        previous_loading = self._loading_editor
        self._loading_editor = True
        combo = self.target_combo
        combo.blockSignals(True)
        try:
            combo.set_targets(concrete_targets)
            rule = previous_build_rule(self)
        finally:
            combo.set_targets(ui_targets)
            combo.blockSignals(False)
            self._loading_editor = previous_loading
            update_zone_control_state(self)
            hide_leaf_targets(self)

        # Downstream adapters (for example ChromaLink cells) may have expanded
        # other target categories while building. At this point Govee parents
        # are already gone, so only concrete persisted targets remain.
        if not rule.targets:
            raise ValueError("Select at least one lighting target")
        return replace(rule, target=rule.targets[0], targets=tuple(rule.targets))

    def target_changed(self, value: str) -> None:
        previous_target_changed(self, value)
        update_zone_control_state(self)

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        preserve = current_selected_zone_targets(self)
        ensure_device_targets(self)
        hide_leaf_targets(self)
        current_targets = {
            target
            for boxes in getattr(self, "_govee_zone_boxes", {}).values()
            for target in boxes
        }
        configured_targets = set(zone_target_map(self))
        if current_targets != configured_targets:
            rebuild_zone_controls(self, preserve)
        update_zone_control_state(self)

    window_class.__init__ = __init__
    window_class._build_target_section = build_target_section
    window_class._set_editor_enabled = set_editor_enabled
    window_class._load_draft = load_draft
    window_class._build_rule_from_editor = build_rule_from_editor
    window_class._target_changed = target_changed
    window_class._update_profile_buttons = update_profile_buttons
    window_class._edl_govee_rule_ui_applied = True


def apply_govee_all_ui(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_govee_all_ui_applied", False):
        return

    previous_init = window_class.__init__
    previous_build_rule = window_class._build_rule_from_editor
    previous_load_draft = window_class._load_draft
    previous_set_editor_enabled = window_class._set_editor_enabled
    previous_target_changed = window_class._target_changed
    previous_update_profile_buttons = window_class._update_profile_buttons

    def configuration(self) -> GoveeConfiguration:
        value = getattr(self, "_govee_configuration", None)
        return value if isinstance(value, GoveeConfiguration) else GoveeConfiguration()

    def all_actions(self) -> dict[str, QAction]:
        if not hasattr(self, "_govee_all_actions"):
            self._govee_all_actions = {}
        return self._govee_all_actions

    def mark_changed(self) -> None:
        if getattr(self, "_loading_editor", False):
            return
        self._set_editor_dirty(True)
        self._update_test_output_state()

    def update_text(self, device_id: str) -> None:
        button = getattr(self, "_govee_zone_buttons", {}).get(device_id)
        if button is None:
            return
        action = all_actions(self).get(device_id)
        boxes = getattr(self, "_govee_zone_boxes", {}).get(device_id, {})
        if action is not None and action.isChecked():
            button.setText("ALL")
            return
        device = next((d for d in configuration(self).devices if d.device_id == device_id), None)
        if device is None:
            return
        selected = [
            zone.name
            for zone in device.zones
            if (box := boxes.get(device.target_for_zone(zone))) is not None and box.isChecked()
        ]
        if not selected:
            button.setText("ALL")
            if action is not None:
                action.blockSignals(True)
                action.setChecked(True)
                action.blockSignals(False)
        elif len(selected) == 1:
            button.setText(selected[0])
        else:
            button.setText(f"{selected[0]} + {len(selected) - 1} more")

    def choose_all(self, device_id: str, checked: bool) -> None:
        action = all_actions(self).get(device_id)
        boxes = getattr(self, "_govee_zone_boxes", {}).get(device_id, {})
        if action is None:
            return
        if checked:
            for box in boxes.values():
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
        elif not any(box.isChecked() for box in boxes.values()):
            action.blockSignals(True)
            action.setChecked(True)
            action.blockSignals(False)
        update_text(self, device_id)
        mark_changed(self)

    def zone_toggled(self, device_id: str) -> None:
        action = all_actions(self).get(device_id)
        boxes = getattr(self, "_govee_zone_boxes", {}).get(device_id, {})
        any_zone = any(box.isChecked() for box in boxes.values())
        if action is not None:
            action.blockSignals(True)
            action.setChecked(not any_zone)
            action.blockSignals(False)
        update_text(self, device_id)

    def ensure_all_controls(self) -> None:
        devices = {device.device_id: device for device in configuration(self).devices}
        actions = all_actions(self)
        buttons = getattr(self, "_govee_zone_buttons", {})
        rows = getattr(self, "_govee_zone_rows", {})
        boxes_by_device = getattr(self, "_govee_zone_boxes", {})

        for device_id, device in devices.items():
            button = buttons.get(device_id)
            if button is None:
                row_widget = rows.get(device_id)
                if row_widget is None or row_widget.layout() is None:
                    continue
                row = row_widget.layout()
                for label in row_widget.findChildren(QLabel):
                    if "No zones saved" in label.text():
                        label.hide()
                button = QToolButton(row_widget)
                button.setMinimumWidth(150)
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                button.setMenu(QMenu(button))
                row.insertWidget(max(1, row.count() - 1), button)
                buttons[device_id] = button

            menu = button.menu()
            if menu is None:
                menu = QMenu(button)
                button.setMenu(menu)

            action = actions.get(device_id)
            if action is None or action.parent() is not menu:
                action = QAction("ALL", menu)
                action.setCheckable(True)
                action.setToolTip(f"Use every native position on {device.name}.")
                first = menu.actions()[0] if menu.actions() else None
                if first is None:
                    menu.addAction(action)
                else:
                    menu.insertAction(first, action)
                action.triggered.connect(
                    lambda checked=False, did=device_id: choose_all(self, did, checked)
                )
                actions[device_id] = action

            boxes = boxes_by_device.get(device_id, {})
            for box in boxes.values():
                if bool(box.property("edl_govee_all_sync_connected")):
                    continue
                box.toggled.connect(
                    lambda _checked=False, did=device_id: zone_toggled(self, did)
                )
                box.setProperty("edl_govee_all_sync_connected", True)

            if not any(box.isChecked() for box in boxes.values()):
                action.blockSignals(True)
                action.setChecked(True)
                action.blockSignals(False)
            button.setToolTip(
                "ALL uses the complete device. Choose one or more saved zones to address only those positions."
            )
            update_text(self, device_id)

        editor_enabled = bool(getattr(self, "_govee_editor_enabled", False))
        selected = set(self.target_combo.targets()) if hasattr(self, "target_combo") else set()
        for device_id, device in devices.items():
            parent_box = getattr(self.target_combo, "_boxes", {}).get(_ui_device_target(device_id))
            if parent_box is not None:
                parent_box.setEnabled(editor_enabled)
                parent_box.setToolTip(
                    f"Select Govee {device.sku}, then choose ALL or one or more saved zones."
                )
            button = buttons.get(device_id)
            if button is not None:
                button.setEnabled(
                    editor_enabled
                    and _ui_device_target(device_id) in selected
                )
            action = actions.get(device_id)
            if action is not None:
                action.setEnabled(editor_enabled)

    def concrete_targets(self, ui_targets: tuple[str, ...]) -> tuple[str, ...]:
        result: list[str] = []
        devices = {device.device_id: device for device in configuration(self).devices}
        actions = all_actions(self)
        boxes_by_device = getattr(self, "_govee_zone_boxes", {})
        for target in ui_targets:
            device_id = _device_id_from_ui_target(target)
            if device_id is None:
                result.append(target)
                continue
            device = devices.get(device_id)
            if device is None:
                raise ValueError("The selected Govee device is no longer configured")
            action = actions.get(device_id)
            if action is None or action.isChecked():
                result.append(device.all_target)
                continue
            wanted = {
                leaf for leaf, box in boxes_by_device.get(device_id, {}).items() if box.isChecked()
            }
            ordered = [
                device.target_for_zone(zone)
                for zone in device.zones
                if device.target_for_zone(zone) in wanted
            ]
            if not ordered:
                result.append(device.all_target)
            else:
                result.extend(ordered)
        if not result:
            raise ValueError("Select at least one lighting target")
        if len(set(result)) != len(result):
            raise ValueError("lighting targets must not contain duplicates")
        return tuple(result)

    def build_rule_from_editor(self):
        ui_targets = tuple(self.target_combo.targets())
        if not any(_device_id_from_ui_target(target) is not None for target in ui_targets):
            return previous_build_rule(self)

        resolved = concrete_targets(self, ui_targets)
        previous_loading = self._loading_editor
        combo = self.target_combo
        self._loading_editor = True
        combo.blockSignals(True)
        try:
            combo.set_targets(resolved)
            rule = previous_build_rule(self)
        finally:
            combo.set_targets(ui_targets)
            combo.blockSignals(False)
            self._loading_editor = previous_loading
            ensure_all_controls(self)
        return replace(rule, target=rule.targets[0], targets=tuple(rule.targets))

    def load_draft(self, rule, *, title: str) -> None:
        original_targets = tuple(rule.targets)
        previous_load_draft(self, rule, title=title)
        ensure_all_controls(self)
        actions = all_actions(self)
        boxes_by_device = getattr(self, "_govee_zone_boxes", {})
        previous_loading = self._loading_editor
        self._loading_editor = True
        try:
            for device in configuration(self).devices:
                has_all = device.all_target in original_targets
                zone_targets = {
                    target
                    for target in original_targets
                    if (parsed := parse_zone_target(target)) is not None
                    and parsed[0] == device.device_id
                }
                action = actions.get(device.device_id)
                if action is not None:
                    action.setChecked(has_all or not zone_targets)
                for target, box in boxes_by_device.get(device.device_id, {}).items():
                    box.setChecked(target in zone_targets and not has_all)
                update_text(self, device.device_id)
        finally:
            self._loading_editor = previous_loading

    def window_init(self) -> None:
        previous_init(self)
        ensure_all_controls(self)
        # The older zone adapter queues one initialization-state pass that still
        # assumes a device with zero custom zones is unusable. Queue our ALL
        # semantics after it so whole-device availability remains authoritative.
        QTimer.singleShot(0, lambda: ensure_all_controls(self))

    def set_editor_enabled(self, enabled: bool) -> None:
        previous_set_editor_enabled(self, enabled)
        ensure_all_controls(self)

    def target_changed(self, value: str) -> None:
        previous_target_changed(self, value)
        ensure_all_controls(self)

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        ensure_all_controls(self)

    window_class.__init__ = window_init
    window_class._build_rule_from_editor = build_rule_from_editor
    window_class._load_draft = load_draft
    window_class._set_editor_enabled = set_editor_enabled
    window_class._target_changed = target_changed
    window_class._update_profile_buttons = update_profile_buttons
    window_class._edl_govee_all_ui_applied = True
