#!/usr/bin/env python3
"""Operator UI for explicit ChromaLink CL1..CL5 addressing.

The generic target selector continues to represent physical Chroma categories.
When CHROMALINK is selected, this adapter adds a second, device-category-specific
address row. Whole-device ``CHROMALINK`` remains whole until the operator
explicitly changes the CL1..CL5 selection; explicit cell rules use leaf targets
(``CHROMALINK::CLn``).
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Iterable

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMessageBox

from lighting_chromalink_cells import (
    CHROMALINK_CELL_NAMES,
    CHROMALINK_CELL_TARGETS,
    CHROMALINK_TARGET,
    is_chromalink_cell_target,
    physical_chroma_target,
)
from lighting_chromalink_render import run_chromalink_cell_preview
from lighting_shared_target_ui import apply_target_alias_integrity

CELL_TOOLTIP = (
    "<b>ChromaLink cells</b><br><br>"
    "ChromaLink exposes five virtual cells: <b>CL1–CL5</b>. A whole-device "
    "CHROMALINK target remains whole until you change this cell selection. "
    "Then the selected cells are saved explicitly, for example <b>CL2 + CL5</b>.<br><br>"
    "The connected device decides how these cells map to its physical LEDs. The cells "
    "are not guaranteed to be equal-sized zones, and some cells may have little or no "
    "visible effect on a particular device.<br><br>"
    "EDL controls which ChromaLink cells are used, but standard ChromaLink cannot "
    "redefine their physical placement."
)

TARGET_TOOLTIP = (
    "<b>Target</b><br><br>"
    "Target defines where the selected output sends its lighting.<br><br>"
    "A rule can contain more than one output. Select an output above, then use "
    "<b>Lights</b> to choose the destination for that output. Each output keeps its "
    "own destination, effect, colours and effect settings."
)


def _selected_cells(window) -> tuple[str, ...]:
    boxes = getattr(window, "chromalink_cell_boxes", {})
    return tuple(
        target
        for target, box in boxes.items()
        if box.isChecked()
    )


def _physical_targets(targets: Iterable[str]) -> tuple[str, ...]:
    values: list[str] = []
    for target in targets:
        physical = physical_chroma_target(target)
        if physical not in values:
            values.append(physical)
    return tuple(values)


def _display_targets(targets: Iterable[str]) -> str:
    values = tuple(targets)
    explicit_cells = tuple(
        target for target in CHROMALINK_CELL_TARGETS if target in values
    )
    if CHROMALINK_TARGET in values:
        cell_label = ""
    else:
        cell_label = " + ".join(target.split("::", 1)[1] for target in explicit_cells)

    parts: list[str] = []
    added_chromalink = False
    for target in values:
        if target == CHROMALINK_TARGET or is_chromalink_cell_target(target):
            if not added_chromalink:
                parts.append(
                    f"CHROMALINK [{cell_label}]" if cell_label else "CHROMALINK"
                )
                added_chromalink = True
        else:
            parts.append(target)
    return " + ".join(parts)


def apply_chromalink_cell_ui(ui_module: Any) -> None:
    """Install the CL1..CL5 editor surface on the final MainWindow class."""
    apply_target_alias_integrity(ui_module)
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_chromalink_cell_ui_applied", False):
        return

    previous_build_target_section = window_class._build_target_section
    previous_set_editor_enabled = window_class._set_editor_enabled
    previous_load_draft = window_class._load_draft
    previous_build_rule = window_class._build_rule_from_editor
    previous_target_changed = window_class._target_changed
    previous_refresh_table = window_class._refresh_table
    previous_runnable_targets = window_class._runnable_targets
    previous_update_preview_state = window_class._update_test_output_state
    previous_toggle_preview = window_class._toggle_preview

    def sync_cell_controls(self) -> None:
        if not hasattr(self, "chromalink_cell_boxes"):
            return
        selected = CHROMALINK_TARGET in self.target_combo.targets()
        editor_enabled = bool(getattr(self, "_chromalink_editor_enabled", False))
        enabled = selected and editor_enabled
        self.chromalink_cell_label.setEnabled(enabled)
        help_button = getattr(self, "chromalink_cell_help", None)
        if help_button is not None:
            help_button.setEnabled(enabled)
        for box in self.chromalink_cell_boxes.values():
            box.setEnabled(enabled)

    def cell_changed(self, _checked: bool = False) -> None:
        if getattr(self, "_loading_editor", False):
            return
        # Changing any CL checkbox is the explicit opt-in boundary between the
        # legacy whole-device target and CL1..CL5 leaf addressing.
        self._chromalink_cells_explicit = True
        self._set_editor_dirty(True)
        self._update_test_output_state()

    def build_target_section(self):
        section = previous_build_target_section(self)

        # The general TARGET help explains only the physical target categories.
        # ChromaLink cell semantics live on their own dedicated help control.
        heading = section.root.itemAt(0).layout()
        if isinstance(heading, QHBoxLayout):
            for index in range(heading.count()):
                widget = heading.itemAt(index).widget()
                if isinstance(widget, ui_module.HelpButton):
                    widget.set_help(TARGET_TOOLTIP)
                    break

        row = QHBoxLayout()
        row.setSpacing(10)
        self.chromalink_cell_label = QLabel("ChromaLink cells")
        row.addWidget(self.chromalink_cell_label)
        self.chromalink_cell_help = ui_module.HelpButton(CELL_TOOLTIP, section)
        row.addWidget(self.chromalink_cell_help)
        self.chromalink_cell_boxes = {}
        self._chromalink_cells_explicit = False
        for name, target in zip(CHROMALINK_CELL_NAMES, CHROMALINK_CELL_TARGETS):
            box = QCheckBox(name)
            box.setChecked(True)
            box.toggled.connect(cell_changed.__get__(self, type(self)))
            self.chromalink_cell_boxes[target] = box
            row.addWidget(box)
        row.addStretch(1)
        section.root.addLayout(row)
        self._chromalink_editor_enabled = True
        sync_cell_controls(self)
        return section

    def set_editor_enabled(self, enabled: bool) -> None:
        previous_set_editor_enabled(self, enabled)
        self._chromalink_editor_enabled = bool(enabled)
        sync_cell_controls(self)

    def load_draft(self, rule, *, title: str) -> None:
        original_targets = tuple(rule.targets)
        explicit_cells = tuple(
            target for target in CHROMALINK_CELL_TARGETS if target in original_targets
        )
        legacy_whole = CHROMALINK_TARGET in original_targets
        physical_targets = _physical_targets(original_targets)
        collapsed = replace(
            rule,
            target=physical_targets[0],
            targets=physical_targets,
        )
        previous_load_draft(self, collapsed, title=title)

        wanted = (
            set(CHROMALINK_CELL_TARGETS)
            if legacy_whole
            else set(explicit_cells)
        )
        if not wanted and CHROMALINK_TARGET not in physical_targets:
            wanted = set(CHROMALINK_CELL_TARGETS)
        previous_loading = self._loading_editor
        self._loading_editor = True
        try:
            for target, box in self.chromalink_cell_boxes.items():
                box.setChecked(target in wanted)
            # Loading an explicit leaf rule preserves explicit addressing. Loading
            # a legacy whole-device rule (or a non-ChromaLink rule) does not
            # silently promote it to CL1..CL5 addressing.
            self._chromalink_cells_explicit = bool(explicit_cells)
        finally:
            self._loading_editor = previous_loading
        sync_cell_controls(self)
        self._update_test_output_state()

    def build_rule_from_editor(self):
        rule = previous_build_rule(self)
        expanded: list[str] = []
        selected_cells = _selected_cells(self)
        explicit_cells = bool(getattr(self, "_chromalink_cells_explicit", False))
        for target in rule.targets:
            if target == CHROMALINK_TARGET and explicit_cells:
                if not selected_cells:
                    raise ValueError("Select at least one ChromaLink cell (CL1–CL5)")
                expanded.extend(selected_cells)
            else:
                expanded.append(target)
        if len(set(expanded)) != len(expanded):
            raise ValueError("lighting targets must not contain duplicates")
        return replace(rule, target=expanded[0], targets=tuple(expanded))

    def target_changed(self, value: str) -> None:
        previous_target_changed(self, value)
        sync_cell_controls(self)

    def refresh_table(self, selected_rows=None) -> None:
        previous_refresh_table(self, selected_rows)
        profile = getattr(self, "_profile", None)
        if profile is None:
            return
        for row, rule in enumerate(profile.rules):
            item = self.table.item(row, 1)
            if item is not None:
                item.setText(_display_targets(rule.targets))

    def runnable_targets(self) -> set[str]:
        result = set(previous_runnable_targets(self))
        profile = getattr(self, "_profile", None)
        if profile is not None and any(
            is_chromalink_cell_target(target)
            for rule in profile.rules
            if rule.enabled
            for target in rule.targets
        ):
            result.add(CHROMALINK_TARGET)
        return result

    def update_test_output_state(self) -> None:
        previous_update_preview_state(self)
        if not hasattr(self, "preview_button"):
            return
        targets = self.target_combo.targets()
        explicit_cells = bool(getattr(self, "_chromalink_cells_explicit", False))
        if (
            targets == (CHROMALINK_TARGET,)
            and explicit_cells
            and not _selected_cells(self)
        ):
            self.preview_button.setEnabled(False)
            self.preview_button.setToolTip(
                "Select at least one ChromaLink cell (CL1–CL5) to preview."
            )

    def toggle_preview(self) -> None:
        targets = self.target_combo.targets()
        explicit_cells = bool(getattr(self, "_chromalink_cells_explicit", False))
        if targets != (CHROMALINK_TARGET,) or not explicit_cells:
            previous_toggle_preview(self)
            return
        if self._preview_running():
            previous_toggle_preview(self)
            return
        if self._live_running():
            previous_toggle_preview(self)
            return

        cells = _selected_cells(self)
        if not cells:
            QMessageBox.information(
                self,
                "Choose ChromaLink cells",
                "Select at least one of CL1, CL2, CL3, CL4 or CL5 before previewing.",
            )
            return
        try:
            intent = self._preview_intent()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot preview output", str(exc))
            return

        stop_event = threading.Event()
        self._preview_stop_event = stop_event
        self.preview_button.setText("Stop preview")
        self.preview_button.setEnabled(True)
        names = " + ".join(target.split("::", 1)[1] for target in cells)
        self.preview_status.setText(
            f"Previewing {intent.effect} on ChromaLink {names} — unselected cells are black"
        )
        self._logger.event(
            "PREVIEW_START",
            target=CHROMALINK_TARGET,
            chromalink_cells=list(cells),
            effect=intent.effect,
            colours=intent.colours,
            brightness=intent.brightness,
        )

        def worker() -> None:
            error = None
            try:
                run_chromalink_cell_preview(intent, cells, stop_event)
            except Exception as exc:
                error = exc
            self._preview_signals.finished.emit(error)

        self._preview_thread = threading.Thread(
            target=worker,
            name="ui-chromalink-cell-preview",
            daemon=True,
        )
        self._preview_thread.start()
        self._update_profile_buttons()

    window_class._build_target_section = build_target_section
    window_class._set_editor_enabled = set_editor_enabled
    window_class._load_draft = load_draft
    window_class._build_rule_from_editor = build_rule_from_editor
    window_class._target_changed = target_changed
    window_class._refresh_table = refresh_table
    window_class._runnable_targets = runnable_targets
    window_class._update_test_output_state = update_test_output_state
    window_class._toggle_preview = toggle_preview
    window_class._edl_chromalink_cell_ui_applied = True
