#!/usr/bin/env python3
"""One compact target selector shared by EDL Rules and COVAS:NEXT Modes.

The accepted target/address domain stays unchanged. This module changes only
how target choices are presented:

* the existing MultiTargetSelector compatibility surface is preserved so the
  established Chroma-zone, ChromaLink-cell, Govee-zone and independent-output
  adapters keep working;
* choices move from an always-visible horizontal checkbox strip into one compact
  popup checklist;
* COVAS:NEXT Modes uses this exact same selector class rather than maintaining a
  separate QComboBox implementation.

The selector remains genuinely multi-target. EDL can therefore keep legacy
uniform multi-target output groups, while a Mode selection of several targets is
expanded into the existing independent ModeOutput leaves with one shared effect.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLayout,
    QFrame,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lighting_chromalink_cells import CHROMALINK_CELL_TARGETS
from lighting_effect_config import WAVE, WAVE_DIRECTIONS, validate_effect_configuration
from keyboard_input import virtual_key_code
from lighting_modes import ModeOutput


_GOVEE_WAVE_DIRECTIONS = ("LEFT_TO_RIGHT", "RIGHT_TO_LEFT")


class _TargetCheckBox(QCheckBox):
    """Target checkbox that keeps the selector summary aligned with label changes."""

    def __init__(self, text: str, selector: "CompactTargetSelector") -> None:
        super().__init__(text, selector._menu_host)
        self._selector = selector

    def setText(self, text: str) -> None:  # type: ignore[override]
        super().setText(text)
        selector = getattr(self, "_selector", None)
        if selector is not None:
            selector._refresh_button()


class CompactTargetSelector(QWidget):
    """Compact popup checklist with the historical MultiTargetSelector API.

    All target checkboxes live inside one persistent menu widget. Device adapters
    can therefore hide/show/enable individual boxes normally without coupling
    checkbox visibility to QWidgetAction visibility. That avoids the Qt teardown
    recursion produced by one QWidgetAction per checkbox.
    """

    currentTextChanged = Signal(str)
    KNOWN_TARGETS = ("KEYBOARD", "MOUSE", "CHROMALINK")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._changing = False
        self._boxes: dict[str, _TargetCheckBox] = {}
        self._order: list[str] = []

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.button = QToolButton(self)
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.button.setMinimumWidth(230)
        self.menu = QMenu(self.button)
        self.button.setMenu(self.menu)
        row.addWidget(self.button, 1)

        self._menu_host = QWidget(self.menu)
        self._menu_host.setMinimumWidth(520)
        self._menu_host.setMinimumHeight(300)
        host_layout = QVBoxLayout(self._menu_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        self._menu_scroll = QScrollArea(self._menu_host)
        self._menu_scroll.setWidgetResizable(True)
        self._menu_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._menu_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._menu_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._menu_scroll.setMinimumWidth(520)
        self._menu_scroll.setMinimumHeight(280)
        self._menu_scroll.setMaximumHeight(360)

        self._menu_content = QWidget(self._menu_scroll)
        self._menu_layout = QVBoxLayout(self._menu_content)
        self._menu_layout.setContentsMargins(12, 10, 12, 10)
        self._menu_layout.setSpacing(7)
        self._menu_scroll.setWidget(self._menu_content)
        host_layout.addWidget(self._menu_scroll)

        self._menu_action = QWidgetAction(self.menu)
        self._menu_action.setDefaultWidget(self._menu_host)
        self.menu.addAction(self._menu_action)

        for target in self.KNOWN_TARGETS:
            self._ensure_target(target)
        self.set_targets(("KEYBOARD",), emit=False)

    def _ensure_target(self, target: str) -> None:
        if target in self._boxes:
            return
        box = _TargetCheckBox(target, self)
        box.toggled.connect(lambda checked, value=target: self._toggled(value, checked))
        self._menu_layout.addWidget(box)
        self._boxes[target] = box
        self._order.append(target)
        if target == "GLOBAL":
            box.setText("All connected devices")
            box.setToolTip(
                "Apply this output to every currently available lighting device "
                "that EDL is allowed to use under Setup → Lighting devices."
            )
        self._refresh_button()

    def _label(self, target: str) -> str:
        box = self._boxes.get(target)
        if box is None:
            return target
        return box.text().strip() or target

    def _sort_menu_rows(self) -> None:
        try:
            ordered = sorted(
                self._boxes.items(),
                key=lambda item: (self._label(item[0]).casefold(), self._label(item[0])),
            )
            for index, (_target, box) in enumerate(ordered):
                self._menu_layout.insertWidget(index, box)
        except RuntimeError:
            return

    def _refresh_button(self) -> None:
        button = getattr(self, "button", None)
        if button is None:
            return
        self._sort_menu_rows()
        selected = self.targets()
        labels = tuple(self._label(target) for target in selected)
        if not labels:
            text = "Choose lights"
        elif len(labels) == 1:
            text = labels[0]
        else:
            text = f"{labels[0]} + {len(labels) - 1} more"
        try:
            button.setText(text)
            button.setToolTip(
                "\n".join(labels) if labels else "Choose one or more lighting targets"
            )
        except RuntimeError:
            # Qt may already own/destruct the native widget during application
            # shutdown. Presentation refreshes have no meaning at that point.
            return

    def targets(self) -> tuple[str, ...]:
        return tuple(
            target
            for target in self._order
            if target in self._boxes and self._boxes[target].isChecked()
        )

    def currentText(self) -> str:
        selected = self.targets()
        return selected[0] if selected else ""

    def currentData(self, *_args):
        return self.currentText() or None

    def currentIndex(self) -> int:
        selected = self.targets()
        return self.findData(selected[0]) if selected else -1

    def display_text(self) -> str:
        return " + ".join(self._label(target) for target in self.targets())

    def set_targets(self, targets, *, emit: bool = True) -> None:
        selected = tuple(targets)
        if any(not isinstance(target, str) or not target.strip() for target in selected):
            raise ValueError("targets must contain only non-empty strings")
        if len(set(selected)) != len(selected):
            raise ValueError("targets must not contain duplicates")
        if "GLOBAL" in selected and selected != ("GLOBAL",):
            raise ValueError("GLOBAL is exclusive and cannot be combined with other targets")
        for target in selected:
            self._ensure_target(target)

        self._changing = True
        try:
            for target, box in self._boxes.items():
                box.setChecked(target in selected)
        finally:
            self._changing = False
        self._refresh_button()
        if emit:
            self.currentTextChanged.emit(self.currentText())

    def _toggled(self, target: str, checked: bool) -> None:
        if self._changing:
            return
        if checked and target == "GLOBAL":
            self.set_targets(("GLOBAL",))
            return
        global_box = self._boxes.get("GLOBAL")
        if checked and target != "GLOBAL" and global_box is not None and global_box.isChecked():
            selected = tuple(
                value
                for value in self._order
                if value != "GLOBAL"
                and (value == target or self._boxes[value].isChecked())
            )
            self.set_targets(selected)
            return
        self._refresh_button()
        self.currentTextChanged.emit(self.currentText())

    # Compatibility with code/tests that historically treated the selector like
    # a light QComboBox. UserRole data remains the canonical target ID.
    def count(self) -> int:
        return len(self._order)

    def itemText(self, index: int) -> str:
        return self._label(self._order[index])

    def itemData(self, index: int, role: int = int(Qt.ItemDataRole.UserRole)):
        target = self._order[index]
        if int(role) == int(Qt.ItemDataRole.ToolTipRole):
            return self._boxes[target].toolTip()
        return target

    def setItemData(self, index: int, value, role: int = int(Qt.ItemDataRole.UserRole)) -> None:
        target = self._order[index]
        if int(role) == int(Qt.ItemDataRole.ToolTipRole):
            self._boxes[target].setToolTip("" if value is None else str(value))

    def findText(self, target: str, *_args) -> int:
        for index, value in enumerate(self._order):
            if value == target or self._label(value) == target:
                return index
        return -1

    def findData(self, target, *_args) -> int:
        try:
            return self._order.index(target)
        except ValueError:
            return -1

    def addItem(self, label: str, data=None) -> None:
        target = str(data if data is not None else label)
        self._ensure_target(target)
        self._boxes[target].setText(str(label))
        self._refresh_button()

    def clear(self) -> None:
        self._changing = True
        try:
            while self._menu_layout.count():
                item = self._menu_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            self._boxes.clear()
            self._order.clear()
        finally:
            self._changing = False
        self._refresh_button()

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._order):
            self.set_targets((self._order[index],))

    def setCurrentText(self, text: str) -> None:
        index = self.findData(text)
        if index < 0:
            index = self.findText(text)
        if index >= 0:
            self.setCurrentIndex(index)

    def setEditText(self, target: str) -> None:
        self.set_targets((target,))


def _replace_widget(layout: QLayout, old: QWidget, new: QWidget) -> bool:
    """Replace a widget inside an arbitrarily nested Qt layout tree."""
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is old:
            layout.replaceWidget(old, new)
            old.hide()
            old.deleteLater()
            return True
        child = item.layout()
        if child is not None and _replace_widget(child, old, new):
            return True
    return False


def _populate_mode_targets(surface) -> None:
    selector = surface.target_combo
    selector.blockSignals(True)
    try:
        selector.clear()
        labels = surface._ui_module_friendly_target_labels()
        for target, label in labels.items():
            selector.addItem(label, target)
        for target in CHROMALINK_CELL_TARGETS:
            if selector.findData(target) < 0:
                selector.addItem(f"ChromaLink · {target.split('::', 1)[1]}", target)

        # Modes never use legacy GLOBAL fallback as an explicit output target.
        global_box = selector._boxes.get("GLOBAL")
        if global_box is not None:
            global_box.setVisible(False)
            global_box.setChecked(False)

    finally:
        selector.blockSignals(False)
    selector._refresh_button()


def _mode_target_effect_controls(surface) -> None:
    if not hasattr(surface, "effect_controls") or not hasattr(surface, "effect_combo"):
        return
    if surface.effect_combo.currentText().strip().upper() != WAVE:
        return
    targets = tuple(surface.target_combo.targets())
    only_govee = bool(targets) and all(
        target.startswith("GOVEE_ENHANCED::") for target in targets
    )

    combo = surface.effect_controls.direction[1]
    allowed = _GOVEE_WAVE_DIRECTIONS if only_govee else WAVE_DIRECTIONS
    current = combo.currentData()
    if current is None:
        current = allowed[0]
    existing = tuple(str(combo.itemData(index)) for index in range(combo.count()))
    if existing != allowed:
        combo.blockSignals(True)
        try:
            combo.clear()
            for value in allowed:
                combo.addItem(value.replace("_", " ").title(), value)
            index = combo.findData(str(current))
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(False)
    arrange = getattr(surface.effect_controls, "_arrange", None)
    if callable(arrange):
        arrange()


def apply_shared_target_ui(ui_module: Any, mode_module: Any) -> None:
    """Install one selector class on both authoring surfaces."""
    ui_module.MultiTargetSelector = CompactTargetSelector

    surface_class = mode_module.ModeEditorSurface
    if getattr(surface_class, "_edl_shared_target_ui_applied", False):
        return

    previous_build_editor = surface_class._build_editor_panel
    previous_effect_changed = surface_class._effect_changed

    # The mode module owns the canonical configured-target catalogue. Keep it
    # behind a tiny method so this shared presentation layer does not duplicate
    # Chroma/Govee configuration parsing.
    import lighting_mode_ui as base_mode_ui

    surface_class._ui_module_friendly_target_labels = staticmethod(
        base_mode_ui._friendly_target_labels
    )

    def build_editor_panel(self):
        panel = previous_build_editor(self)
        old = self.target_combo
        parent_layout = old.parentWidget().layout() if old.parentWidget() is not None else None
        selector = CompactTargetSelector(old.parentWidget())
        selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if parent_layout is None or not _replace_widget(parent_layout, old, selector):
            raise RuntimeError(
                "could not replace Mode target selector with shared compact selector"
            )
        self.target_combo = selector
        _populate_mode_targets(self)
        selector.currentTextChanged.connect(self._target_changed)
        return panel

    def populate_targets(self) -> None:
        # During the copied editor's construction target_combo is still its
        # temporary QComboBox. Populate it enough for construction; the final
        # shared selector is installed immediately afterwards by build_editor_panel.
        selector = self.target_combo
        labels = base_mode_ui._friendly_target_labels()
        if isinstance(selector, CompactTargetSelector):
            _populate_mode_targets(self)
            return
        current = selector.currentData() if selector.count() else None
        selector.blockSignals(True)
        try:
            selector.clear()
            for target, label in labels.items():
                selector.addItem(label, target)
            for target in CHROMALINK_CELL_TARGETS:
                if selector.findData(target) < 0:
                    selector.addItem(
                        f"ChromaLink · {target.split('::', 1)[1]}", target
                    )
            if current is not None:
                index = selector.findData(current)
                if index >= 0:
                    selector.setCurrentIndex(index)
        finally:
            selector.blockSignals(False)

    def load_output(self, index: int) -> None:
        phase = self._current_phase()
        index = max(0, min(index, len(phase.outputs) - 1))
        output = phase.outputs[index]
        self._output_index = index
        self._loading = True
        try:
            self.target_combo.set_targets((output.target,), emit=False)
            self.output_enabled.setChecked(output.enabled)
            self.effect_combo.setCurrentText(output.effect)
            self.effect_controls.set_effect(output.effect, output.effect_parameters)
            self.palette_editor.set_effect(output.effect)
            self.palette_editor.set_palette(output.colours)
            _mode_target_effect_controls(self)
        finally:
            self._loading = False

    def capture_output(self) -> tuple[ModeOutput, ...]:
        targets = tuple(self.target_combo.targets())
        if not targets:
            raise ValueError("Choose at least one lighting target")
        effect = self.effect_combo.currentText().strip().upper()
        colours = tuple(self.palette_editor.palette())
        parameters = self.effect_controls.parameters()
        validate_effect_configuration(effect, colours, parameters)
        enabled = self.output_enabled.isChecked()
        return tuple(
            ModeOutput(target, effect, colours, parameters, enabled)
            for target in targets
        )

    def target_changed(self, _text: str = "") -> None:
        if self._loading:
            return
        _mode_target_effect_controls(self)
        self._changed()

    def effect_changed(self, effect: str) -> None:
        previous_effect_changed(self, effect)
        _mode_target_effect_controls(self)

    surface_class._build_editor_panel = build_editor_panel
    surface_class._populate_targets = populate_targets
    surface_class._load_output = load_output
    surface_class._capture_output = capture_output
    surface_class._target_changed = target_changed
    surface_class._effect_changed = effect_changed
    surface_class._edl_shared_target_ui_applied = True


# Co-located target alias integrity at the shared selector boundary.
def apply_target_alias_integrity(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_target_alias_integrity_applied", False):
        return

    # Do not call the existing ui_main implementation here: that implementation
    # replaces only ``targets`` and therefore can fail during dataclass
    # validation before an outer adapter gets control back. Call its immediate
    # superclass builder, then perform the same multi-target/keyboard work with
    # an atomic alias+tuple replacement.
    def build_rule_from_editor(self):
        rule = super(window_class, self)._build_rule_from_editor()
        selected_targets = tuple(self.target_combo.targets())
        if not selected_targets:
            raise ValueError("Select at least one lighting target")

        rule = replace(
            rule,
            target=selected_targets[0],
            targets=selected_targets,
        )
        if rule.keyboard is not None:
            for key in rule.keyboard.keys:
                virtual_key_code(key)
        return rule

    window_class._build_rule_from_editor = build_rule_from_editor
    window_class._edl_target_alias_integrity_applied = True
