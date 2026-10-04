#!/usr/bin/env python3
"""COVAS:NEXT mode authoring inside the existing EDL profile workspace.

The normal EDL rule editor remains unchanged. This adapter adds a top-level
EDL rules / Scripted modes selector and reuses the same two-pane geometry,
target selector, effect controls and palette editor classes.

Timed modes remain a separate domain model from condition-driven LightingRule,
but both domains are persisted through the same user-facing EDL profile file.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import lighting_profiles as profile_store
import lighting_mode_ui as mode_ui
from lighting_authority import lighting_authority
from lighting_chroma_zone_config import friendly_target_name, load_chroma_zone_configuration
from lighting_chromalink_cells import CHROMALINK_CELL_TARGETS
from lighting_ui_density import compact_editor_scroll, compact_output_list, compact_section
from lighting_ui_language import (
    ADD_MODE,
    MEDIA_SECTION_SUBTITLE,
    MEDIA_SECTION_TITLE,
    MODE_EDITOR_TITLE,
    MODE_PREVIEW_HELP,
    MODE_SECTION_SUBTITLE,
    MODES_HEADING,
    PHASE_SECTION_SUBTITLE,
    RULES_TAB,
    RULES_TAB_HELP,
    RUN_MODE,
    SCRIPTED_MODES_TAB,
    SCRIPTED_MODES_TAB_HELP,
    STOP_MODE,
    TARGET_SUBTITLE,
    UNSAVED_MODE_CHANGES,
)
from lighting_ui_tokens import STATUS_WARNING, TEXT_SECONDARY
from lighting_direct_effects import DIRECT_EFFECTS
from lighting_effect_config import (
    BREATH,
    FLASH,
    KNOWN_EFFECTS,
    PULSE,
    WAVE,
    WAVE_DIRECTIONS,
    EffectParameters,
    colour_cardinality,
    default_parameters_for_effect,
    validate_effect_configuration,
)
from lighting_govee_config import load_govee_configuration
from lighting_mode_catalog import mode_catalog
from lighting_mode_runtime import start_lighting_mode, stop_lighting_mode
from lighting_modes import LightingMode, ModeLibrary, ModeOutput, ModePhase, canonical_mode_name, make_default_mode
from lighting_profiles import make_default_profile
from lighting_profile_modes import load_profile_modes, save_profile_modes
from lighting_scene_authority import scene_authority
from lighting_ui_presenter import is_virpil_link_profile_path


def _default_colours(effect: str) -> tuple[tuple[int, int, int], ...]:
    minimum, _maximum = colour_cardinality(effect)
    seed = ((0, 160, 255), (180, 0, 255), (255, 100, 0))
    return tuple(seed[index % len(seed)] for index in range(max(1, minimum)))


def _friendly_target_labels() -> dict[str, str]:
    labels = {
        "KEYBOARD": "KEYBOARD",
        "MOUSE": "MOUSE",
        "CHROMALINK": "CHROMALINK",
    }
    try:
        chroma = load_chroma_zone_configuration()
        for target in chroma.target_map():
            labels[target] = friendly_target_name(chroma, target)
    except Exception:
        pass
    try:
        govee = load_govee_configuration()
        for device in govee.devices:
            labels[device.all_target] = f"Govee {device.name} · Whole device"
            for zone in device.zones:
                labels[device.target_for_zone(zone)] = f"Govee {device.name} · {zone.name}"
    except Exception:
        pass
    return labels


def _output_label(index: int, output: ModeOutput) -> str:
    label = _friendly_target_labels().get(output.target, output.target)
    state = "" if output.enabled else " [off]"
    return f"{index + 1}. {label} — {output.effect}{state}"


def _unique_copy_name(library: ModeLibrary, source: LightingMode) -> str:
    existing = {mode.key for mode in library.modes}
    base = f"{source.name} COPY"
    candidate = base
    number = 2
    from lighting_modes import canonical_mode_name

    while canonical_mode_name(candidate) in existing:
        candidate = f"{base} {number}"
        number += 1
    return candidate


class ModeEditorSurface(QWidget):
    """Two-pane ordered-step editor using normal EDL output controls."""

    def __init__(self, ui_module, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ui = ui_module
        self._library = ModeLibrary((self._acceptance_seed(),))
        self._mode_index = 0
        self._phase_index = 0
        self._output_index = 0
        self._loading = False
        self._dirty = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        root.addLayout(self._build_mode_row())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(9)
        self.sequence_panel = self._build_sequence_panel()
        self.sequence_panel.setMinimumWidth(500)
        self.editor_panel = self._build_editor_panel()
        self.editor_panel.setMinimumWidth(720)
        splitter.addWidget(self.sequence_panel)
        splitter.addWidget(self.editor_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([680, 900])
        root.addWidget(splitter, 1)
        self.splitter = splitter

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(100)
        self._status_timer.timeout.connect(self._refresh_run_state)
        self._status_timer.start()
        self._load_library(self._library, mode_index=0, dirty=True)

    def _acceptance_seed(self) -> LightingMode:
        govee_targets = [
            target
            for target in _friendly_target_labels()
            if target.startswith("GOVEE_ENHANCED::") and target.endswith("::ALL")
        ]
        second_target = govee_targets[0] if govee_targets else "MOUSE"
        spatial = second_target.startswith("GOVEE_ENHANCED::")
        return LightingMode(
            "EDITOR_ACCEPTANCE",
            (
                ModePhase(
                    "Charge",
                    4.0,
                    (
                        ModeOutput(
                            "KEYBOARD",
                            PULSE,
                            ((180, 0, 255),),
                            EffectParameters(
                                cycle_seconds=1.8,
                                minimum_brightness=0.12,
                                maximum_brightness=1.0,
                            ),
                        ),
                        ModeOutput(
                            second_target,
                            WAVE if spatial else FLASH,
                            ((0, 255, 255), (0, 40, 255)),
                            EffectParameters(cycle_seconds=2.8, direction="LEFT_TO_RIGHT")
                            if spatial
                            else EffectParameters(step_seconds=0.35),
                        ),
                    ),
                ),
                ModePhase(
                    "Transition",
                    6.0,
                    (
                        ModeOutput(
                            "KEYBOARD",
                            BREATH,
                            ((255, 0, 190), (70, 0, 160)),
                            EffectParameters(cycle_seconds=1.1),
                        ),
                        ModeOutput(
                            second_target,
                            WAVE if spatial else FLASH,
                            ((255, 255, 255), (0, 170, 255)),
                            EffectParameters(cycle_seconds=1.3, direction="LEFT_TO_RIGHT")
                            if spatial
                            else EffectParameters(step_seconds=0.2),
                        ),
                    ),
                ),
                ModePhase(
                    "Stabilize",
                    4.0,
                    (
                        ModeOutput("KEYBOARD", "STATIC", ((190, 60, 255),), EffectParameters()),
                        ModeOutput(second_target, "STATIC", ((0, 150, 255),), EffectParameters()),
                    ),
                ),
            ),
        )

    def _build_mode_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(7)
        row.addWidget(QLabel("Mode"))
        self.mode_selector = QComboBox()
        self.mode_selector.setMinimumWidth(280)
        self.mode_selector.currentIndexChanged.connect(self._mode_selected)
        row.addWidget(self.mode_selector)
        self.new_mode_button = QPushButton("+ New mode")
        self.new_mode_button.clicked.connect(self.new_mode)
        row.addWidget(self.new_mode_button)
        self.duplicate_mode_button = QPushButton("Duplicate")
        self.duplicate_mode_button.clicked.connect(self.duplicate_mode)
        row.addWidget(self.duplicate_mode_button)
        self.delete_mode_button = QPushButton("Delete")
        self.delete_mode_button.clicked.connect(self.delete_mode)
        row.addWidget(self.delete_mode_button)
        row.addStretch(1)
        self.mode_count = QLabel("")
        row.addWidget(self.mode_count)
        return row

    def _build_sequence_panel(self) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 5, 0)
        root.setSpacing(7)

        heading = QHBoxLayout()
        title = QLabel("Phases")
        title.setStyleSheet("font-size:12pt; font-weight:600;")
        heading.addWidget(title)
        heading.addStretch(1)
        self.total_time = QLabel("")
        heading.addWidget(self.total_time)
        root.addLayout(heading)

        toolbar = QHBoxLayout()
        self.add_step_button = QPushButton("+ Add phase")
        self.add_step_button.clicked.connect(self.add_phase)
        toolbar.addWidget(self.add_step_button)
        self.delete_step_button = QPushButton("Delete")
        self.delete_step_button.clicked.connect(self.delete_phase)
        toolbar.addWidget(self.delete_step_button)
        self.move_up_button = QPushButton("↑")
        self.move_up_button.setFixedWidth(38)
        self.move_up_button.clicked.connect(lambda: self.move_phase(-1))
        toolbar.addWidget(self.move_up_button)
        self.move_down_button = QPushButton("↓")
        self.move_down_button.setFixedWidth(38)
        self.move_down_button.clicked.connect(lambda: self.move_phase(1))
        toolbar.addWidget(self.move_down_button)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        self.phase_table = QTableWidget(0, 4)
        self.phase_table.setHorizontalHeaderLabels(("#", "Step", "Duration", "Outputs"))
        self.phase_table.verticalHeader().setVisible(False)
        self.phase_table.verticalHeader().setDefaultSectionSize(38)
        self.phase_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.phase_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.phase_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.phase_table.itemSelectionChanged.connect(self._phase_selected)
        header = self.phase_table.horizontalHeader()
        header.setStretchLastSection(True)
        root.addWidget(self.phase_table, 1)
        return panel

    def _section(self, title: str, subtitle: str):
        return self._ui.base.SectionBox(title, subtitle)

    def _build_editor_panel(self) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(5, 0, 0, 0)
        root.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("Mode editor")
        title.setStyleSheet("font-size:12pt; font-weight:600;")
        header.addWidget(title)
        header.addStretch(1)
        self.run_button = QPushButton("Run mode")
        self.run_button.clicked.connect(self.run_mode)
        header.addWidget(self.run_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_mode)
        header.addWidget(self.stop_button)
        root.addLayout(header)

        self.run_status = QLabel("IDLE")
        self.run_status.setStyleSheet(f"color:{TEXT_SECONDARY}; font-weight:600;")
        root.addWidget(self.run_status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        content = QWidget()
        content.setMinimumWidth(680)
        content_root = QVBoxLayout(content)
        content_root.setContentsMargins(2, 2, 8, 8)
        content_root.setSpacing(10)

        mode_section = self._section("MODE", "name used to invoke this choreography")
        mode_grid = QGridLayout()
        mode_grid.addWidget(QLabel("Mode name"), 0, 0)
        self.mode_name = QLineEdit()
        self.mode_name.textChanged.connect(self._changed)
        mode_grid.addWidget(self.mode_name, 0, 1)
        mode_grid.setColumnStretch(1, 1)
        mode_section.root.addLayout(mode_grid)
        content_root.addWidget(mode_section)

        phase_section = self._section("STEP", "ordered top-to-bottom timing block")
        phase_grid = QGridLayout()
        phase_grid.addWidget(QLabel("Phase name"), 0, 0)
        self.phase_name = QLineEdit()
        self.phase_name.textChanged.connect(self._changed)
        phase_grid.addWidget(self.phase_name, 0, 1)
        phase_grid.addWidget(QLabel("Duration"), 1, 0)
        self.phase_duration = QDoubleSpinBox()
        self.phase_duration.setRange(0.1, 300.0)
        self.phase_duration.setDecimals(1)
        self.phase_duration.setSuffix(" s")
        self.phase_duration.valueChanged.connect(self._changed)
        phase_grid.addWidget(self.phase_duration, 1, 1)
        phase_grid.setColumnStretch(1, 1)
        phase_section.root.addLayout(phase_grid)
        content_root.addWidget(phase_section)

        target_section = self._section("TARGET", "simultaneous outputs during this step")
        output_heading = QHBoxLayout()
        output_heading.addWidget(QLabel("Outputs in this phase"))
        output_heading.addStretch(1)
        self.add_output_button = QPushButton("+ Add output")
        self.add_output_button.clicked.connect(self.add_output)
        output_heading.addWidget(self.add_output_button)
        self.remove_output_button = QPushButton("Remove output")
        self.remove_output_button.clicked.connect(self.remove_output)
        output_heading.addWidget(self.remove_output_button)
        target_section.root.addLayout(output_heading)

        self.output_list = QListWidget()
        self.output_list.setMinimumHeight(80)
        self.output_list.setMaximumHeight(150)
        self.output_list.currentRowChanged.connect(self._output_selected)
        target_section.root.addWidget(self.output_list)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Lights"))
        self.target_selector = self._ui.MultiTargetSelector()
        self._populate_targets()
        self.target_selector.currentTextChanged.connect(lambda _text: self._changed())
        target_row.addWidget(self.target_selector, 1)
        self.output_enabled = QCheckBox("Output enabled")
        self.output_enabled.setChecked(True)
        self.output_enabled.toggled.connect(self._changed)
        target_row.addWidget(self.output_enabled)
        target_section.root.addLayout(target_row)
        content_root.addWidget(target_section)

        effect_section = self._section("EFFECT", "same EDL effect vocabulary and controls")
        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("Effect"))
        self.effect_combo = QComboBox()
        self.effect_combo.addItems(DIRECT_EFFECTS)
        self.effect_combo.currentTextChanged.connect(self._effect_changed)
        effect_row.addWidget(self.effect_combo)
        effect_row.addStretch(1)
        effect_section.root.addLayout(effect_row)
        self.effect_controls = self._ui.UnifiedEffectParametersEditor()
        self.effect_controls.parametersChanged.connect(lambda _value: self._changed())
        effect_section.root.addWidget(self.effect_controls)
        self.palette_editor = self._ui.UnifiedPaletteEditor()
        self.palette_editor.paletteChanged.connect(lambda _value: self._changed())
        effect_section.root.addWidget(self.palette_editor)
        content_root.addWidget(effect_section)
        content_root.addStretch(1)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        self.editor_scroll = scroll
        return panel

    def _populate_targets(self) -> None:
        labels = _friendly_target_labels()
        selector = self.target_selector
        global_box = selector._boxes.get("GLOBAL")
        if global_box is not None:
            global_box.setVisible(False)
            global_box.setChecked(False)
        for target, label in labels.items():
            selector._ensure_target(target)
            selector._boxes[target].setText(label)
        selector.set_targets(("KEYBOARD",), emit=False)

    def _window(self):
        return self.window()

    def _mark_profile_dirty(self) -> None:
        self._dirty = True
        window = self._window()
        if getattr(window, "_profile", None) is not None:
            window._profile_dirty = True
            updater = getattr(window, "_update_profile_buttons", None)
            if callable(updater):
                updater()

    def mark_clean(self) -> None:
        self._dirty = False

    def library(self) -> ModeLibrary:
        self._commit_current_if_available()
        return self._library

    def set_library(self, library: ModeLibrary) -> None:
        self._load_library(library, mode_index=0, dirty=False)

    def _load_library(self, library: ModeLibrary, *, mode_index: int, dirty: bool) -> None:
        self._loading = True
        try:
            self._library = library
            self._dirty = dirty
            self.mode_selector.clear()
            for mode in library.modes:
                self.mode_selector.addItem(mode.name)
            if library.modes:
                self._mode_index = max(0, min(mode_index, len(library.modes) - 1))
                self.mode_selector.setCurrentIndex(self._mode_index)
                self._load_mode(self._library.modes[self._mode_index])
                self._set_mode_controls_enabled(True)
            else:
                self._mode_index = -1
                self._phase_index = 0
                self._output_index = 0
                self.phase_table.setRowCount(0)
                self.output_list.clear()
                self.total_time.setText("No mode selected")
                self.mode_name.clear()
                self._set_mode_controls_enabled(False)
            self._refresh_mode_count()
        finally:
            self._loading = False

    def _set_mode_controls_enabled(self, enabled: bool) -> None:
        self.sequence_panel.setEnabled(enabled)
        self.editor_panel.setEnabled(enabled)
        self.duplicate_mode_button.setEnabled(enabled)
        self.delete_mode_button.setEnabled(enabled)

    def _refresh_mode_count(self) -> None:
        count = len(self._library.modes)
        self.mode_count.setText(f"{count} mode" + ("" if count == 1 else "s"))

    def _current_mode(self) -> LightingMode:
        if self._mode_index < 0 or self._mode_index >= len(self._library.modes):
            raise ValueError("Create or select a scripted mode first")
        return self._library.modes[self._mode_index]

    def _current_phase(self) -> ModePhase:
        return self._current_mode().phases[self._phase_index]

    def _replace_current_mode(self, mode: LightingMode) -> None:
        modes = list(self._library.modes)
        modes[self._mode_index] = mode
        self._library = ModeLibrary(tuple(modes))
        self._refresh_mode_selector_name()

    def _refresh_mode_selector_name(self) -> None:
        if self._mode_index < 0:
            return
        self.mode_selector.blockSignals(True)
        try:
            self.mode_selector.setItemText(self._mode_index, self._current_mode().name)
        finally:
            self.mode_selector.blockSignals(False)

    def _load_mode(self, mode: LightingMode) -> None:
        self._loading = True
        try:
            self._phase_index = 0
            self._output_index = 0
            self.mode_name.setText(mode.name)
            self._refresh_phase_table()
            self._load_phase(0)
        finally:
            self._loading = False

    def _refresh_phase_table(self) -> None:
        if self._mode_index < 0:
            self.phase_table.setRowCount(0)
            return
        mode = self._current_mode()
        self.phase_table.blockSignals(True)
        try:
            self.phase_table.setRowCount(len(mode.phases))
            for row, phase in enumerate(mode.phases):
                values = (
                    str(row + 1),
                    phase.name or f"Step {row + 1}",
                    f"{phase.duration_seconds:.1f} s",
                    str(len(phase.outputs)),
                )
                for column, value in enumerate(values):
                    self.phase_table.setItem(row, column, QTableWidgetItem(value))
            self.phase_table.resizeColumnsToContents()
            self.phase_table.horizontalHeader().setStretchLastSection(True)
            self.phase_table.selectRow(self._phase_index)
        finally:
            self.phase_table.blockSignals(False)
        self.total_time.setText(f"{len(mode.phases)} steps · {mode.duration_seconds:.1f} s")

    def _load_phase(self, index: int) -> None:
        mode = self._current_mode()
        index = max(0, min(index, len(mode.phases) - 1))
        self._phase_index = index
        phase = mode.phases[index]
        self._loading = True
        try:
            self.phase_name.setText(phase.name)
            self.phase_duration.setValue(phase.duration_seconds)
            self.output_list.clear()
            for output_index, output in enumerate(phase.outputs):
                self.output_list.addItem(_output_label(output_index, output))
            self._output_index = 0
            self.output_list.setCurrentRow(0)
            self._load_output(0)
        finally:
            self._loading = False

    def _load_output(self, index: int) -> None:
        phase = self._current_phase()
        index = max(0, min(index, len(phase.outputs) - 1))
        output = phase.outputs[index]
        self._output_index = index
        self._loading = True
        try:
            self.target_selector.set_targets((output.target,), emit=False)
            self.output_enabled.setChecked(output.enabled)
            self.effect_combo.setCurrentText(output.effect)
            self.effect_controls.set_effect(output.effect, output.effect_parameters)
            self.palette_editor.set_effect(output.effect)
            self.palette_editor.set_palette(output.colours)
        finally:
            self._loading = False

    def _capture_output(self) -> tuple[ModeOutput, ...]:
        targets = tuple(self.target_selector.targets())
        if not targets:
            raise ValueError("Choose at least one lighting target")
        effect = self.effect_combo.currentText().strip().upper()
        colours = tuple(self.palette_editor.palette())
        parameters = self.effect_controls.parameters()
        validate_effect_configuration(effect, colours, parameters)
        return tuple(
            ModeOutput(target, effect, colours, parameters, self.output_enabled.isChecked())
            for target in targets
        )

    def _commit_current(self) -> None:
        if self._loading or self._mode_index < 0:
            return
        mode = self._current_mode()
        phases = list(mode.phases)
        phase = phases[self._phase_index]
        outputs = list(phase.outputs)
        replacement = self._capture_output()
        outputs[self._output_index:self._output_index + 1] = replacement
        phases[self._phase_index] = ModePhase(
            self.phase_name.text().strip() or f"Step {self._phase_index + 1}",
            self.phase_duration.value(),
            tuple(outputs),
        )
        updated = LightingMode(self.mode_name.text().strip() or "NEW MODE", tuple(phases))
        self._replace_current_mode(updated)
        self._refresh_phase_table()
        self._refresh_output_list()
        self._mark_profile_dirty()

    def _commit_current_if_available(self) -> None:
        if self._mode_index >= 0 and not self._loading:
            self._commit_current()

    def _refresh_output_list(self) -> None:
        phase = self._current_phase()
        self.output_list.blockSignals(True)
        try:
            self.output_list.clear()
            for index, output in enumerate(phase.outputs):
                self.output_list.addItem(_output_label(index, output))
            self.output_list.setCurrentRow(min(self._output_index, len(phase.outputs) - 1))
        finally:
            self.output_list.blockSignals(False)

    def _changed(self, *_args) -> None:
        if self._loading or self._mode_index < 0:
            return
        try:
            self._commit_current()
        except Exception:
            self._mark_profile_dirty()

    def _effect_changed(self, effect: str) -> None:
        if self._loading:
            return
        effect = effect.strip().upper()
        self._loading = True
        try:
            parameters = default_parameters_for_effect(effect)
            colours = _default_colours(effect)
            self.effect_controls.set_effect(effect, parameters)
            self.palette_editor.set_effect(effect)
            self.palette_editor.set_palette(colours)
        finally:
            self._loading = False
        self._changed()

    def _mode_selected(self, index: int) -> None:
        if self._loading or index < 0 or index == self._mode_index:
            return
        try:
            self._commit_current_if_available()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot switch mode", str(exc))
            self.mode_selector.setCurrentIndex(self._mode_index)
            return
        self._mode_index = index
        self._load_mode(self._current_mode())

    def _phase_selected(self) -> None:
        if self._loading:
            return
        rows = sorted({item.row() for item in self.phase_table.selectedItems()})
        if not rows:
            return
        row = rows[0]
        if row == self._phase_index:
            return
        try:
            self._commit_current()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot switch phase", str(exc))
            self.phase_table.selectRow(self._phase_index)
            return
        self._load_phase(row)

    def _output_selected(self, row: int) -> None:
        if self._loading or row < 0 or row == self._output_index:
            return
        try:
            self._commit_current()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot switch output", str(exc))
            self.output_list.setCurrentRow(self._output_index)
            return
        self._load_output(row)

    def new_mode(self) -> None:
        window = self._window()
        if getattr(window, "_profile", None) is None:
            QMessageBox.information(self, "Open or create a profile", "Scripted modes are saved inside the active EDL profile.")
            return
        try:
            self._commit_current_if_available()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot add mode", str(exc))
            return
        existing = {mode.key for mode in self._library.modes}
        index = len(self._library.modes) + 1
        name = "NEW MODE"
        from lighting_modes import canonical_mode_name
        while canonical_mode_name(name) in existing:
            name = f"NEW MODE {index}"
            index += 1
        modes = (*self._library.modes, make_default_mode(name))
        self._load_library(ModeLibrary(modes), mode_index=len(modes) - 1, dirty=True)
        self._mark_profile_dirty()

    def duplicate_mode(self) -> None:
        if self._mode_index < 0:
            return
        try:
            self._commit_current()
            source = self._current_mode()
            copy = replace(source, name=_unique_copy_name(self._library, source))
            modes = list(self._library.modes)
            modes.insert(self._mode_index + 1, copy)
            self._load_library(ModeLibrary(tuple(modes)), mode_index=self._mode_index + 1, dirty=True)
            self._mark_profile_dirty()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot duplicate mode", str(exc))

    def delete_mode(self) -> None:
        if self._mode_index < 0:
            return
        modes = list(self._library.modes)
        del modes[self._mode_index]
        next_index = min(self._mode_index, len(modes) - 1)
        self._load_library(ModeLibrary(tuple(modes)), mode_index=max(0, next_index), dirty=True)
        self._mark_profile_dirty()

    def add_phase(self) -> None:
        try:
            self._commit_current()
            mode = self._current_mode()
            phases = list(mode.phases)
            phases.append(
                ModePhase(
                    f"Step {len(phases) + 1}",
                    10.0,
                    (ModeOutput("KEYBOARD", "STATIC", ((0, 128, 255),), default_parameters_for_effect("STATIC")),),
                )
            )
            self._replace_current_mode(LightingMode(mode.name, tuple(phases)))
            self._phase_index = len(phases) - 1
            self._refresh_phase_table()
            self._load_phase(self._phase_index)
            self._mark_profile_dirty()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot add phase", str(exc))

    def delete_phase(self) -> None:
        mode = self._current_mode()
        if len(mode.phases) <= 1:
            QMessageBox.information(self, "Keep one phase", "A mode must contain at least one phase.")
            return
        phases = list(mode.phases)
        del phases[self._phase_index]
        self._phase_index = min(self._phase_index, len(phases) - 1)
        self._replace_current_mode(LightingMode(mode.name, tuple(phases)))
        self._refresh_phase_table()
        self._load_phase(self._phase_index)
        self._mark_profile_dirty()

    def move_phase(self, delta: int) -> None:
        try:
            self._commit_current()
            mode = self._current_mode()
            target = self._phase_index + delta
            if target < 0 or target >= len(mode.phases):
                return
            phases = list(mode.phases)
            phases[self._phase_index], phases[target] = phases[target], phases[self._phase_index]
            self._replace_current_mode(LightingMode(mode.name, tuple(phases)))
            self._phase_index = target
            self._refresh_phase_table()
            self._load_phase(target)
            self._mark_profile_dirty()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot move phase", str(exc))

    def add_output(self) -> None:
        try:
            self._commit_current()
            phase = self._current_phase()
            used = {output.target for output in phase.outputs}
            candidate = next((target for target in _friendly_target_labels() if target not in used), "KEYBOARD")
            outputs = (*phase.outputs, ModeOutput(candidate, "STATIC", ((0, 128, 255),), EffectParameters()))
            mode = self._current_mode()
            phases = list(mode.phases)
            phases[self._phase_index] = replace(phase, outputs=outputs)
            self._replace_current_mode(LightingMode(mode.name, tuple(phases)))
            self._output_index = len(outputs) - 1
            self._refresh_output_list()
            self._load_output(self._output_index)
            self._mark_profile_dirty()
        except Exception as exc:
            QMessageBox.warning(self, "Cannot add output", str(exc))

    def remove_output(self) -> None:
        phase = self._current_phase()
        if len(phase.outputs) <= 1:
            QMessageBox.information(self, "Keep one output", "A phase must contain at least one output.")
            return
        outputs = list(phase.outputs)
        del outputs[self._output_index]
        mode = self._current_mode()
        phases = list(mode.phases)
        phases[self._phase_index] = replace(phase, outputs=tuple(outputs))
        self._replace_current_mode(LightingMode(mode.name, tuple(phases)))
        self._output_index = min(self._output_index, len(outputs) - 1)
        self._refresh_output_list()
        self._load_output(self._output_index)
        self._mark_profile_dirty()

    def run_mode(self) -> None:
        try:
            self._commit_current()
            start_lighting_mode(self._current_mode())
        except Exception as exc:
            QMessageBox.warning(self, "Cannot run mode", str(exc))
        self._refresh_run_state()

    def stop_mode(self) -> None:
        stop_lighting_mode()
        self._refresh_run_state()

    def _refresh_run_state(self) -> None:
        run = scene_authority.active_scene()
        if run is None:
            self.run_status.setText("IDLE — Start lighting first to expose live targets.")
            self.run_status.setStyleSheet(f"color:{TEXT_SECONDARY}; font-weight:600;")
            self.stop_button.setEnabled(False)
            self.run_button.setEnabled(self._mode_index >= 0 and bool(lighting_authority.active_targets))
        else:
            remaining = scene_authority.remaining_seconds()
            self.run_status.setText(f"ACTIVE — {run.name} — {remaining:.1f}s remaining")
            self.run_status.setStyleSheet("color:#F2C66D; font-weight:600;")
            self.stop_button.setEnabled(True)
            self.run_button.setEnabled(False)


def apply_mode_ui(ui_module) -> None:
    """Add one-profile/two-workspace mode authoring to the accepted EDL window."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_mode_ui_applied", False):
        return

    previous_init = window_class.__init__
    previous_close_event = window_class.closeEvent
    previous_load_profile = window_class.load_profile
    original_profile_from_dict = profile_store.profile_from_dict

    # Existing load_profile() remains the canonical native-rule loader. Permit the
    # combined document's optional modes field without changing its rule semantics.
    def profile_from_dict_with_modes(value):
        if isinstance(value, dict) and "modes" in value:
            value = dict(value)
            value.pop("modes", None)
        return original_profile_from_dict(value)

    profile_store.profile_from_dict = profile_from_dict_with_modes

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        central = self.centralWidget()
        root = central.layout() if central is not None else None
        if root is None:
            return

        selector = QHBoxLayout()
        selector.setSpacing(6)
        self.edl_rules_mode_button = QPushButton("Rules")
        self.edl_rules_mode_button.setObjectName("workspaceTab")
        self.edl_rules_mode_button.setCheckable(True)
        self.edl_rules_mode_button.setChecked(True)
        self.covas_modes_mode_button = QPushButton("Scripted modes")
        self.covas_modes_mode_button.setObjectName("workspaceTab")
        self.covas_modes_mode_button.setCheckable(True)
        selector.addWidget(self.edl_rules_mode_button)
        selector.addWidget(self.covas_modes_mode_button)
        selector.addStretch(1)
        root.insertLayout(1, selector)

        self._edl_rules_workspace = self.splitter
        self._covas_modes_workspace = ModeEditorSurface(ui_module, self)
        self._covas_modes_workspace.splitter.setSizes(self.splitter.sizes())
        root.insertWidget(2, self._covas_modes_workspace, 1)
        self._covas_modes_workspace.hide()

        def show_rules() -> None:
            self.edl_rules_mode_button.setChecked(True)
            self.covas_modes_mode_button.setChecked(False)
            self._edl_rules_workspace.setSizes(self._covas_modes_workspace.splitter.sizes())
            self._covas_modes_workspace.hide()
            self._edl_rules_workspace.show()

        def show_modes() -> None:
            self.edl_rules_mode_button.setChecked(False)
            self.covas_modes_mode_button.setChecked(True)
            self._covas_modes_workspace.splitter.setSizes(self._edl_rules_workspace.sizes())
            self._edl_rules_workspace.hide()
            self._covas_modes_workspace.show()

        self.edl_rules_mode_button.clicked.connect(show_rules)
        self.covas_modes_mode_button.clicked.connect(show_modes)
        self.show_edl_rules_workspace = show_rules
        self.show_covas_modes_workspace = show_modes

    def load_profile(self, path: Path) -> None:
        candidate = Path(path)
        if is_virpil_link_profile_path(candidate):
            previous_load_profile(self, candidate)
            return
        try:
            _profile, modes = load_profile_modes(candidate)
        except Exception as exc:
            QMessageBox.critical(self, "Open profile failed", str(exc))
            return
        previous_load_profile(self, candidate)
        current = getattr(self, "_profile_path", None)
        if current is not None and Path(current).resolve(strict=False) == candidate.resolve(strict=False):
            self._covas_modes_workspace.set_library(modes)

    def write_profile(self, path: Path) -> None:
        if self._profile is None:
            return
        try:
            modes = self._covas_modes_workspace.library()
            save_profile_modes(path, self._profile, modes)
        except Exception as exc:
            QMessageBox.critical(self, "Save profile failed", str(exc))
            return
        self._profile_path = Path(path)
        self._profile_dirty = False
        self._covas_modes_workspace.mark_clean()
        self._logger.event("PROFILE_SAVE", path=path, profile=self._profile.name, modes=len(modes.modes))
        self.statusBar().showMessage(f"Saved {path}", 4000)
        self._update_profile_buttons()

    def close_event(self, event) -> None:
        previous_close_event(self, event)
        if event.isAccepted():
            stop_lighting_mode()

    window_class.__init__ = init
    window_class.load_profile = load_profile
    window_class._write_profile = write_profile
    window_class.closeEvent = close_event
    window_class._edl_mode_ui_applied = True


# Co-located live Mode catalogue publication.
def apply_live_mode_catalog(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_live_mode_catalog_applied", False):
        return

    previous_toggle_live = window_class._toggle_live_lighting
    previous_live_finished = window_class._live_finished

    def toggle_live_lighting(self) -> None:
        starting = not bool(self._live_running())
        library: ModeLibrary | None = None

        if starting:
            surface = getattr(self, "_covas_modes_workspace", None)
            if surface is not None and not bool(
                getattr(surface, "_mode_draft_dirty", False)
            ):
                getter = getattr(surface, "library", None)
                if callable(getter):
                    candidate = getter()
                    if isinstance(candidate, ModeLibrary):
                        library = candidate

        previous_toggle_live(self)
        running = bool(self._live_running())

        if starting:
            if running:
                # This is the same clean Mode library whose target owners were
                # acquired by lighting_mode_live_ui for this live session.
                mode_catalog.set_library(library or ModeLibrary(()))
            else:
                # A rejected/failed Start never publishes stale editor state.
                mode_catalog.clear()

    def live_finished(self, error: object) -> None:
        # The worker has actually exited when this signal is delivered. Clear
        # the voice-visible catalog at that real lifecycle boundary, for both
        # normal Stop and runtime failure, before any UI completion handling.
        mode_catalog.clear()
        previous_live_finished(self, error)

    window_class._toggle_live_lighting = toggle_live_lighting
    window_class._live_finished = live_finished
    window_class._edl_live_mode_catalog_applied = True


# Co-located Mode media authoring.

MEDIA_TITLE_TOOLTIP = (
    "Optional track title for this scripted mode. Leave blank for a lighting-only scene."
)
MEDIA_ARTIST_TOOLTIP = (
    "Optional artist used to identify the intended recording more precisely."
)
MEDIA_VALIDATION_MESSAGE = "Track title is required when an artist is provided"

def _split_media_cue(value: str | None) -> tuple[str, str]:
    if value is None:
        return "", ""
    cue = value.strip()
    if not cue:
        return "", ""
    title, separator, artist = cue.rpartition(" by ")
    if separator and title.strip() and artist.strip():
        return title.strip(), artist.strip()
    return cue, ""


def _media_editor_valid(surface) -> bool:
    title = surface.media_title.text().strip()
    artist = surface.media_artist.text().strip()
    return not (artist and not title)


def _cue_from_editor(surface) -> str | None:
    title = surface.media_title.text().strip()
    artist = surface.media_artist.text().strip()
    if artist and not title:
        # Defensive invariant for non-UI/programmatic commit paths. Normal user
        # interaction is blocked inline before Apply can be invoked.
        raise ValueError(MEDIA_VALIDATION_MESSAGE)
    if not title:
        return None
    if artist:
        return f"{title} by {artist}"
    return title


def apply_mode_media_ui(ui_module) -> None:
    """Expose LightingMode.media_cue without changing accepted Mode/media runtime."""
    surface_class = mode_ui.ModeEditorSurface
    if getattr(surface_class, "_edl_mode_media_ui_applied", False):
        return

    previous_build_editor_panel = surface_class._build_editor_panel
    previous_load_mode = surface_class._load_mode
    previous_load_library = surface_class._load_library
    previous_replace_current_mode = surface_class._replace_current_mode
    previous_refresh_run_state = surface_class._refresh_run_state

    def refresh_media_validation(self) -> None:
        if not hasattr(self, "media_title"):
            return

        valid = _media_editor_valid(self)
        if hasattr(self, "media_validation"):
            self.media_validation.setText("" if valid else MEDIA_VALIDATION_MESSAGE)

        # Preserve the copied editor's existing Apply enablement contract while
        # preventing an invalid media draft from reaching its modal exception path.
        if hasattr(self, "apply_mode_button"):
            window = self._window()
            running = bool(getattr(window, "_live_running", lambda: False)())
            self.apply_mode_button.setEnabled(
                valid
                and not running
                and self._mode_index >= 0
                and bool(getattr(self, "_mode_draft_dirty", False))
            )

    def media_changed(self, _text: str) -> None:
        self._changed()
        refresh_media_validation(self)

    def build_editor_panel(self):
        panel = previous_build_editor_panel(self)

        self.media_title = QLineEdit()
        self.media_title.setPlaceholderText("Track title")
        self.media_title.setToolTip(MEDIA_TITLE_TOOLTIP)
        self.media_title.textChanged.connect(lambda text: media_changed(self, text))

        self.media_artist = QLineEdit()
        self.media_artist.setPlaceholderText("Artist (recommended)")
        self.media_artist.setToolTip(MEDIA_ARTIST_TOOLTIP)
        self.media_artist.textChanged.connect(lambda text: media_changed(self, text))

        section = compact_section(
            self._ui.base.SectionBox(
                MEDIA_SECTION_TITLE,
                MEDIA_SECTION_SUBTITLE,
            ),
            maximum_height=True,
        )
        grid = QGridLayout()
        title_label = QLabel("Track title")
        title_label.setToolTip(MEDIA_TITLE_TOOLTIP)
        artist_label = QLabel("Artist")
        artist_label.setToolTip(MEDIA_ARTIST_TOOLTIP)
        grid.addWidget(title_label, 0, 0)
        grid.addWidget(self.media_title, 0, 1)
        grid.addWidget(artist_label, 1, 0)
        grid.addWidget(self.media_artist, 1, 1)

        self.media_validation = QLabel("")
        self.media_validation.setStyleSheet(f"color:{STATUS_WARNING}; font-weight:600;")
        grid.addWidget(self.media_validation, 2, 1)

        grid.setColumnStretch(1, 1)
        section.root.addLayout(grid)
        self.media_cue_section = section

        content = self.editor_scroll.widget()
        layout = content.layout() if content is not None else None
        if layout is not None:
            # Keep media metadata next to scene identity and before phase/output choreography.
            layout.insertWidget(1, section)
        refresh_media_validation(self)
        return panel

    def load_mode(self, mode: LightingMode) -> None:
        previous_load_mode(self, mode)
        title, artist = _split_media_cue(mode.media_cue)
        self.media_title.blockSignals(True)
        self.media_artist.blockSignals(True)
        try:
            self.media_title.setText(title)
            self.media_artist.setText(artist)
        finally:
            self.media_artist.blockSignals(False)
            self.media_title.blockSignals(False)
        refresh_media_validation(self)

    def load_library(self, library, *, mode_index: int, dirty: bool) -> None:
        previous_load_library(self, library, mode_index=mode_index, dirty=dirty)
        if not library.modes and hasattr(self, "media_title"):
            self.media_title.blockSignals(True)
            self.media_artist.blockSignals(True)
            try:
                self.media_title.clear()
                self.media_artist.clear()
            finally:
                self.media_artist.blockSignals(False)
                self.media_title.blockSignals(False)
        refresh_media_validation(self)

    def replace_current_mode(self, mode: LightingMode) -> None:
        # Several accepted structural-edit helpers rebuild LightingMode from name
        # + phases. Reattach the editor's Mode-level metadata at this single seam
        # so adding/moving phases or outputs cannot silently discard the cue.
        cue = _cue_from_editor(self)
        if mode.media_cue != cue:
            mode = replace(mode, media_cue=cue)
        previous_replace_current_mode(self, mode)

    def commit_current(self) -> None:
        # This is the copied Mode editor's existing draft commit logic with the
        # already-supported media_cue included in equality/validation. Nothing
        # below this UI boundary changes.
        if self._loading or self._mode_index < 0:
            return
        current = self._current_mode()
        phases = list(current.phases)
        phase = phases[self._phase_index]
        outputs = list(phase.outputs)
        replacement = self._capture_output()
        outputs[self._output_index:self._output_index + 1] = replacement
        phases[self._phase_index] = ModePhase(
            self.phase_name.text().strip() or f"Phase {self._phase_index + 1}",
            self.phase_duration.value(),
            tuple(outputs),
        )
        updated = LightingMode(
            self.mode_name.text().strip() or "NEW SCENE",
            tuple(phases),
            _cue_from_editor(self),
        )
        if updated == current:
            return
        _begin_mode_draft(self)
        self._replace_current_mode(updated)
        self._refresh_phase_table()
        self._refresh_output_list()
        self._mark_profile_dirty()

    def refresh_run_state(self) -> None:
        previous_refresh_run_state(self)
        if not hasattr(self, "media_title"):
            return
        window = self._window()
        running = bool(getattr(window, "_live_running", lambda: False)())
        enabled = self._mode_index >= 0 and not running
        self.media_title.setEnabled(enabled)
        self.media_artist.setEnabled(enabled)
        refresh_media_validation(self)

    surface_class._build_editor_panel = build_editor_panel
    surface_class._load_mode = load_mode
    surface_class._load_library = load_library
    surface_class._replace_current_mode = replace_current_mode
    surface_class._commit_current = commit_current
    surface_class._refresh_run_state = refresh_run_state
    surface_class._edl_mode_media_ui_applied = True


# Co-located Mode live-session integration.
def _same_run(left, right) -> bool:
    return (
        left is not None
        and right is not None
        and left.name == right.name
        and left.started_at == right.started_at
    )


def apply_mode_live_ui(ui_module: Any) -> None:
    surface_class = mode_ui.ModeEditorSurface
    window_class = ui_module.MainWindow
    if getattr(surface_class, "_edl_mode_live_ui_applied", False):
        return

    previous_surface_init = surface_class.__init__

    def surface_init(self, *args, **kwargs) -> None:
        previous_surface_init(self, *args, **kwargs)
        self._active_mode_run = None

    def run_mode(self) -> None:
        if getattr(self, "_mode_draft_dirty", False):
            QMessageBox.information(
                self,
                "Finish mode changes",
                "Apply or cancel the current mode changes before running the mode.",
            )
            return
        if self._mode_index < 0:
            return
        active = mode_ui.scene_authority.active_scene()
        if active is not None:
            QMessageBox.information(
                self,
                "Another mode is active",
                f"{active.name} is already running. Stop it before running another scripted mode.",
            )
            return
        try:
            self._active_mode_run = mode_ui.start_lighting_mode(self._current_mode())
        except Exception as exc:
            QMessageBox.warning(self, "Cannot run mode", str(exc))
        self._refresh_run_state()

    def stop_mode(self) -> None:
        active = mode_ui.scene_authority.active_scene()
        owned = getattr(self, "_active_mode_run", None)
        if not _same_run(active, owned):
            self._refresh_run_state()
            return
        mode_ui.stop_lighting_mode()
        self._active_mode_run = None
        self._refresh_run_state()

    def refresh_run_state(self) -> None:
        active = mode_ui.scene_authority.active_scene()
        owned = getattr(self, "_active_mode_run", None)
        live_targets = tuple(mode_ui.lighting_authority.active_targets)

        if active is None:
            self._active_mode_run = None
            if live_targets:
                self.run_status.setText("IDLE — Ready to run the selected mode.")
            else:
                window = self._window()
                readiness_method = getattr(window, "_edl_readiness_snapshot", None)
                readiness = readiness_method() if callable(readiness_method) else None
                if readiness is not None and not readiness.can_start and readiness.blocked_reason:
                    reason = readiness.blocked_reason.split(". ", 1)[0].rstrip(".")
                    self.run_status.setText(f"IDLE — {reason}.")
                else:
                    self.run_status.setText("IDLE — Start lighting first to expose live targets.")
            self.run_status.setStyleSheet(f"color:{TEXT_SECONDARY}; font-weight:600;")
            self.stop_button.setEnabled(False)
            self.run_button.setEnabled(self._mode_index >= 0 and bool(live_targets))
        elif _same_run(active, owned):
            remaining = mode_ui.scene_authority.remaining_seconds()
            self.run_status.setText(
                f"ACTIVE — {active.name} — {remaining:.1f}s remaining"
            )
            self.run_status.setStyleSheet("color:#F2C66D; font-weight:600;")
            self.stop_button.setEnabled(True)
            self.run_button.setEnabled(False)
        else:
            self.run_status.setText(
                f"MODE ACTIVE — {active.name} — stop it before running another mode."
            )
            self.run_status.setStyleSheet(f"color:{STATUS_WARNING}; font-weight:600;")
            self.stop_button.setEnabled(False)
            self.run_button.setEnabled(False)

        # The normal EDL live lock prevents mutation, but selection remains useful
        # for inspection and choosing which already-applied mode to run.
        _sync_live_authoring_lock(self)
        running = bool(getattr(self._window(), "_live_running", lambda: False)())
        if running and self._mode_index >= 0:
            self.mode_selector.setEnabled(True)
            self.phase_table.setEnabled(True)
            self.phase_table.setDragEnabled(False)
            self.output_list.setEnabled(True)

    surface_class.__init__ = surface_init
    surface_class.run_mode = run_mode
    surface_class.stop_mode = stop_mode
    surface_class._refresh_run_state = refresh_run_state
    surface_class._edl_mode_live_ui_applied = True

    previous_toggle_live = window_class._toggle_live_lighting
    previous_update_profile_buttons = window_class._update_profile_buttons

    def toggle_live_lighting(self) -> None:
        surface = getattr(self, "_covas_modes_workspace", None)
        starting = not bool(self._live_running())
        if (
            starting
            and surface is not None
            and getattr(surface, "_mode_draft_dirty", False)
        ):
            self.statusBar().showMessage(
                "Apply or cancel the current mode changes before starting lighting.",
                6500,
            )
            return
        previous_toggle_live(self)

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        surface = getattr(self, "_covas_modes_workspace", None)
        if surface is None:
            return
        dirty = bool(getattr(surface, "_mode_draft_dirty", False))
        running = bool(self._live_running())
        preview = bool(getattr(self, "_preview_running", lambda: False)())
        start = getattr(self, "start_lighting_button", None)

        # Start readiness is owned by the final proposed-session projection.
        # This wrapper retains only the Mode-draft edit lock.
        if dirty:
            for name in ("save_button", "save_as_button"):
                button = getattr(self, name, None)
                if button is not None:
                    button.setEnabled(False)
            if start is not None and not running:
                start.setEnabled(False)

        refresh = getattr(surface, "_refresh_run_state", None)
        if callable(refresh):
            refresh()

    window_class._toggle_live_lighting = toggle_live_lighting
    window_class._update_profile_buttons = update_profile_buttons


# Co-located Mode final guardrails.
def apply_mode_final_guardrails(ui_module: Any) -> None:
    surface_class = mode_ui.ModeEditorSurface
    window_class = ui_module.MainWindow
    if getattr(surface_class, "_edl_mode_final_guardrails_applied", False):
        return

    previous_surface_init = surface_class.__init__
    previous_populate_targets = surface_class._populate_targets
    previous_effect_changed = surface_class._effect_changed

    def populate_targets(self) -> None:
        previous_populate_targets(self)
        existing = {
            self.target_combo.itemData(index)
            for index in range(self.target_combo.count())
        }
        for target in CHROMALINK_CELL_TARGETS:
            if target in existing:
                continue
            cell = target.split("::", 1)[1]
            self.target_combo.addItem(f"ChromaLink · {cell}", target)

    def surface_init(self, *args, **kwargs) -> None:
        previous_surface_init(self, *args, **kwargs)
        root = self.layout()
        if root is not None and root.count() > 1:
            first = root.itemAt(0)
            legacy_row = first.layout() if first is not None else None
            if legacy_row is not None and legacy_row.count() == 0:
                root.takeAt(0)

    def effect_changed(self, effect: str) -> None:
        previous_effect_changed(self, effect)
        _refresh_target_effect_controls(self)

    surface_class.__init__ = surface_init
    surface_class._populate_targets = populate_targets
    surface_class._effect_changed = effect_changed
    surface_class._edl_mode_final_guardrails_applied = True

    previous_load_profile = window_class.load_profile
    previous_import_virpil = getattr(window_class, "import_virpil_profile_dialog", None)

    def unresolved_mode_draft(self) -> bool:
        surface = getattr(self, "_covas_modes_workspace", None)
        return bool(surface is not None and getattr(surface, "_mode_draft_dirty", False))

    def reject_profile_replacement(self) -> None:
        self.statusBar().showMessage(
            "Apply or cancel the current mode changes before replacing this profile.",
            6500,
        )

    def clear_modes_if_profile_changed(self, before_profile) -> None:
        surface = getattr(self, "_covas_modes_workspace", None)
        if surface is None:
            return
        if getattr(self, "_profile", None) is before_profile:
            return
        surface.set_library(ModeLibrary(()))

    def load_profile(self, path) -> None:
        if unresolved_mode_draft(self):
            reject_profile_replacement(self)
            return
        candidate = Path(path)
        before = getattr(self, "_profile", None)
        previous_load_profile(self, candidate)
        if is_virpil_link_profile_path(candidate):
            clear_modes_if_profile_changed(self, before)

    window_class.load_profile = load_profile

    if previous_import_virpil is not None:
        def import_virpil_profile_dialog(self, *args, **kwargs):
            if unresolved_mode_draft(self):
                reject_profile_replacement(self)
                return None
            before = getattr(self, "_profile", None)
            result = previous_import_virpil(self, *args, **kwargs)
            clear_modes_if_profile_changed(self, before)
            return result

        window_class.import_virpil_profile_dialog = import_virpil_profile_dialog


# Co-located accepted Mode editor workflow.
_GOVEE_WAVE_DIRECTIONS = ("LEFT_TO_RIGHT", "RIGHT_TO_LEFT")


def _display_direction(value: str) -> str:
    return value.replace("_", " ").title()


def _set_help(widget, text: str) -> None:
    widget.setToolTip(text)
    if hasattr(widget, "setToolTipDuration"):
        widget.setToolTipDuration(30000)


def _build_mode_row(self) -> QHBoxLayout:
    """Create Mode controls; the left pane places them in EDL list order."""
    self.mode_selector = QComboBox()
    self.mode_selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    self.mode_selector.currentIndexChanged.connect(self._mode_selected)

    self.new_mode_button = QPushButton(ADD_MODE)
    self.new_mode_button.clicked.connect(self.new_mode)
    self.duplicate_mode_button = QPushButton("Duplicate")
    self.duplicate_mode_button.clicked.connect(self.duplicate_mode)
    self.delete_mode_button = QPushButton("Delete")
    self.delete_mode_button.clicked.connect(self.delete_mode)
    self.mode_count = QLabel("")

    # Mode controls live inside the copied EDL left pane. The base constructor
    # still asks for this legacy row, so return a zero-content layout rather than
    # duplicating the controls above the splitter.
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    return row


def _build_phases_panel(self) -> QWidget:
    """Copy normal EDL's left list-pane hierarchy for Mode + Phases."""
    panel = QWidget()
    root = QVBoxLayout(panel)
    root.setContentsMargins(0, 0, 5, 0)
    root.setSpacing(7)

    mode_heading = QHBoxLayout()
    mode_title = QLabel(MODES_HEADING)
    mode_title.setStyleSheet("font-size:16px; font-weight:600;")
    mode_heading.addWidget(mode_title)
    mode_heading.addStretch(1)
    mode_heading.addWidget(self.mode_count)
    root.addLayout(mode_heading)

    root.addWidget(self.mode_selector)

    mode_toolbar = QHBoxLayout()
    mode_toolbar.setSpacing(5)
    mode_toolbar.addWidget(self.new_mode_button)
    mode_toolbar.addWidget(self.duplicate_mode_button)
    mode_toolbar.addWidget(self.delete_mode_button)
    mode_toolbar.addStretch(1)
    root.addLayout(mode_toolbar)

    divider = QFrame()
    divider.setFrameShape(QFrame.Shape.HLine)
    divider.setFrameShadow(QFrame.Shadow.Sunken)
    root.addWidget(divider)

    phase_heading = QHBoxLayout()
    phase_title = QLabel("Phases")
    phase_title.setStyleSheet("font-size:16px; font-weight:600;")
    phase_heading.addWidget(phase_title)
    phase_heading.addStretch(1)
    self.total_time = QLabel("")
    phase_heading.addWidget(self.total_time)
    root.addLayout(phase_heading)

    toolbar = QHBoxLayout()
    toolbar.setSpacing(5)

    self.add_phase_button = QPushButton("+ Add phase")
    self.add_phase_button.clicked.connect(self.add_phase)
    toolbar.addWidget(self.add_phase_button)

    self.duplicate_phase_button = QPushButton("Duplicate")
    self.duplicate_phase_button.clicked.connect(self.duplicate_phase)
    toolbar.addWidget(self.duplicate_phase_button)

    self.delete_phase_button = QPushButton("Delete")
    self.delete_phase_button.clicked.connect(self.delete_phase)
    toolbar.addWidget(self.delete_phase_button)

    toolbar.addWidget(self._ui.base._separator())

    self.move_up_button = QPushButton("↑")
    self.move_up_button.setFixedWidth(38)
    self.move_up_button.clicked.connect(lambda: self.move_phase(-1))
    toolbar.addWidget(self.move_up_button)

    self.move_down_button = QPushButton("↓")
    self.move_down_button.setFixedWidth(38)
    self.move_down_button.clicked.connect(lambda: self.move_phase(1))
    toolbar.addWidget(self.move_down_button)
    toolbar.addStretch(1)
    root.addLayout(toolbar)

    self.phase_table = self._ui.base.RuleTableWidget(0, 4)
    self.phase_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    self.phase_table.setHorizontalHeaderLabels(("#", "Phase", "Duration", "Outputs"))
    self.phase_table.verticalHeader().setVisible(False)
    self.phase_table.verticalHeader().setDefaultSectionSize(38)
    self.phase_table.itemSelectionChanged.connect(self._phase_selected)
    self.phase_table.moveRequested.connect(self.drag_move_phase)

    header = self.phase_table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(False)

    root.addWidget(self.phase_table, 1)
    return panel


def _section(self, title: str, subtitle: str):
    return compact_section(self._ui.base.SectionBox(title, subtitle), maximum_height=True)


def _compact_output_list_safely(widget: QListWidget) -> int:
    """Mirror the Rules output-list teardown guard for the Mode editor."""
    try:
        return compact_output_list(widget)
    except RuntimeError:
        # Qt may already have destroyed the native QListWidget during teardown.
        return 0


def _build_editor_panel(self) -> QWidget:
    """Build the Mode editor with the same explicit draft grammar as EDL Rules."""
    panel = QWidget()
    root = QVBoxLayout(panel)
    root.setContentsMargins(5, 0, 0, 0)
    root.setSpacing(7)

    header = QHBoxLayout()
    title = QLabel(MODE_EDITOR_TITLE)
    title.setStyleSheet("font-size:16px; font-weight:600;")
    header.addWidget(title)

    self.draft_state = QLabel("")
    self.draft_state.setProperty("edlState", "warning")
    self.draft_state.setStyleSheet("font-weight:600;")
    header.addWidget(self.draft_state)
    header.addStretch(1)

    self.preview_header_action = QWidget(panel)
    self.preview_header_action.setObjectName("modePreviewHeaderAction")
    preview_layout = QHBoxLayout(self.preview_header_action)
    preview_layout.setContentsMargins(8, 2, 8, 2)
    preview_layout.setSpacing(7)

    self.preview_title = QLabel("Preview")
    self.preview_title.setStyleSheet("font-weight:600;")
    preview_layout.addWidget(self.preview_title)

    self.run_status = QLabel("No mode selected")
    self.run_status.setProperty("edlTextRole", "secondary")
    self.run_status.setMinimumWidth(110)
    self.run_status.setMaximumWidth(260)
    preview_layout.addWidget(self.run_status)

    self.run_button = QPushButton(RUN_MODE)
    self.run_button.setObjectName("modePreviewAction")
    self.run_button.setMinimumWidth(112)
    self.run_button.clicked.connect(self.run_mode)
    preview_layout.addWidget(self.run_button)

    self.preview_help = self._ui.HelpButton(
        MODE_PREVIEW_HELP,
        self.preview_header_action,
    )
    preview_layout.addWidget(self.preview_help)
    header.addWidget(self.preview_header_action)

    self.preview_separator = QFrame(panel)
    self.preview_separator.setFrameShape(QFrame.Shape.VLine)
    self.preview_separator.setFrameShadow(QFrame.Shadow.Plain)
    self.preview_separator.setObjectName("modePreviewHeaderSeparator")
    header.addWidget(self.preview_separator)

    self.cancel_mode_button = QPushButton("Cancel changes")
    self.cancel_mode_button.clicked.connect(self.cancel_mode_changes)
    header.addWidget(self.cancel_mode_button)

    self.apply_mode_button = QPushButton("Apply changes")
    self.apply_mode_button.clicked.connect(self.apply_mode_changes)
    header.addWidget(self.apply_mode_button)

    # Compatibility surface retained for adapters/tests that still call the
    # historical stop method. The operator sees one Preview toggle instead.
    self.stop_button = QPushButton(STOP_MODE, panel)
    self.stop_button.clicked.connect(self.stop_mode)
    self.stop_button.hide()

    root.addLayout(header)

    self.mode_editor_empty_state = QWidget(panel)
    empty_layout = QVBoxLayout(self.mode_editor_empty_state)
    empty_layout.setContentsMargins(28, 28, 28, 28)
    empty_layout.setSpacing(12)
    empty_layout.addStretch(1)

    self.mode_editor_empty_text = QLabel(
        "No scripted mode selected.\nAdd a mode to begin."
    )
    self.mode_editor_empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
    self.mode_editor_empty_text.setWordWrap(True)
    self.mode_editor_empty_text.setProperty("edlTextRole", "secondary")
    self.mode_editor_empty_text.setStyleSheet("font-size:11pt;")
    empty_layout.addWidget(self.mode_editor_empty_text)

    self.mode_editor_empty_add = QPushButton("+ Add mode")
    self.mode_editor_empty_add.setMinimumWidth(130)
    self.mode_editor_empty_add.clicked.connect(self.new_mode)
    empty_layout.addWidget(
        self.mode_editor_empty_add,
        0,
        Qt.AlignmentFlag.AlignHCenter,
    )
    empty_layout.addStretch(1)
    root.addWidget(self.mode_editor_empty_state, 1)
    self.mode_editor_empty_state.hide()

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    content = QWidget()
    content.setMinimumWidth(680)
    content_root = QVBoxLayout(content)
    content_root.setContentsMargins(2, 2, 6, 6)
    content_root.setSpacing(7)

    mode_section = self._section("MODE", MODE_SECTION_SUBTITLE)
    self._edl_compact_mode_section = mode_section
    mode_grid = QGridLayout()
    mode_grid.addWidget(QLabel("Mode name"), 0, 0)
    self.mode_name = QLineEdit()
    self.mode_name.textChanged.connect(self._changed)
    mode_grid.addWidget(self.mode_name, 0, 1)
    mode_grid.setColumnStretch(1, 1)
    mode_section.root.addLayout(mode_grid)
    content_root.addWidget(mode_section)

    phase_section = self._section("PHASE", PHASE_SECTION_SUBTITLE)
    self._edl_compact_phase_section = phase_section
    phase_grid = QGridLayout()
    phase_grid.addWidget(QLabel("Phase name"), 0, 0)
    self.phase_name = QLineEdit()
    self.phase_name.textChanged.connect(self._changed)
    phase_grid.addWidget(self.phase_name, 0, 1)
    phase_grid.addWidget(QLabel("Duration"), 1, 0)
    self.phase_duration = QDoubleSpinBox()
    self.phase_duration.setRange(0.1, 300.0)
    self.phase_duration.setDecimals(1)
    self.phase_duration.setSuffix(" s")
    self.phase_duration.valueChanged.connect(self._changed)
    phase_grid.addWidget(self.phase_duration, 1, 1)
    phase_grid.setColumnStretch(1, 1)
    phase_section.root.addLayout(phase_grid)
    content_root.addWidget(phase_section)

    target_section = self._section("TARGET", TARGET_SUBTITLE)
    self._edl_compact_target_section = target_section
    output_heading = QHBoxLayout()
    output_heading.addWidget(QLabel("Outputs in this phase"))
    output_heading.addStretch(1)
    self.add_output_button = QPushButton("+ Add output")
    self.add_output_button.clicked.connect(self.add_output)
    output_heading.addWidget(self.add_output_button)
    self.remove_output_button = QPushButton("Remove output")
    self.remove_output_button.clicked.connect(self.remove_output)
    output_heading.addWidget(self.remove_output_button)
    target_section.root.addLayout(output_heading)

    self.output_list = QListWidget()
    self.output_list.currentRowChanged.connect(self._output_selected)
    output_model = self.output_list.model()
    output_list = self.output_list
    output_model.rowsInserted.connect(
        lambda *_args, widget=output_list: _compact_output_list_safely(widget)
    )
    output_model.rowsRemoved.connect(
        lambda *_args, widget=output_list: _compact_output_list_safely(widget)
    )
    output_model.modelReset.connect(
        lambda *_args, widget=output_list: _compact_output_list_safely(widget)
    )
    output_model.layoutChanged.connect(
        lambda *_args, widget=output_list: _compact_output_list_safely(widget)
    )
    target_section.root.addWidget(self.output_list)

    target_grid = QGridLayout()
    target_grid.setHorizontalSpacing(9)
    target_grid.addWidget(QLabel("Lights"), 0, 0)
    self.target_combo = QComboBox()
    self.target_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    self._populate_targets()
    self.target_combo.currentIndexChanged.connect(self._target_changed)
    target_grid.addWidget(self.target_combo, 0, 1)
    self.output_enabled = QCheckBox("Output enabled")
    self.output_enabled.setChecked(True)
    self.output_enabled.toggled.connect(self._changed)
    target_grid.addWidget(self.output_enabled, 0, 2)
    target_grid.setColumnStretch(1, 1)
    target_section.root.addLayout(target_grid)
    self.target_availability_status = QLabel("")
    self.target_availability_status.setProperty("edlTextRole", "secondary")
    self.target_availability_status.setWordWrap(True)
    self.target_combo._edl_availability_label = self.target_availability_status
    target_section.root.addWidget(self.target_availability_status)
    content_root.addWidget(target_section)

    effect_section = compact_section(
        self._ui.base.SectionBox("EFFECT", "what the selected output should do")
    )
    self._edl_compact_effect_section = effect_section
    effect_row = QHBoxLayout()
    effect_row.addWidget(QLabel("Effect"))
    self.effect_combo = QComboBox()
    self.effect_combo.addItems(KNOWN_EFFECTS)
    self.effect_combo.currentTextChanged.connect(self._effect_changed)
    effect_row.addWidget(self.effect_combo)
    effect_row.addStretch(1)
    effect_section.root.addLayout(effect_row)

    self.effect_controls = self._ui.UnifiedEffectParametersEditor()
    self.effect_controls.parametersChanged.connect(lambda _value: self._changed())
    effect_section.root.addWidget(self.effect_controls)

    self.palette_editor = self._ui.UnifiedPaletteEditor()
    self.palette_editor.paletteChanged.connect(lambda _value: self._changed())
    effect_section.root.addWidget(self.palette_editor)
    content_root.addWidget(effect_section)
    content_root.addStretch(1)

    scroll.setWidget(content)
    root.addWidget(scroll, 1)
    self.editor_scroll = scroll
    compact_editor_scroll(self.editor_scroll)
    compact_output_list(self.output_list)
    return panel


def _populate_targets(self) -> None:
    current = self.target_combo.currentData() if self.target_combo.count() else None
    labels = mode_ui._friendly_target_labels()
    self.target_combo.blockSignals(True)
    try:
        self.target_combo.clear()
        for target, label in labels.items():
            index = self.target_combo.count()
            self.target_combo.addItem(label, target)
            self.target_combo.setItemData(index, target, Qt.ItemDataRole.ToolTipRole)
        if current is not None:
            index = self.target_combo.findData(current)
            if index >= 0:
                self.target_combo.setCurrentIndex(index)
    finally:
        self.target_combo.blockSignals(False)


def _selected_target(self) -> str:
    target = self.target_combo.currentData()
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Choose a lighting target")
    return target


def _load_output(self, index: int) -> None:
    phase = self._current_phase()
    index = max(0, min(index, len(phase.outputs) - 1))
    output = phase.outputs[index]
    self._output_index = index
    self._loading = True
    try:
        target_index = self.target_combo.findData(output.target)
        if target_index < 0:
            self.target_combo.addItem(output.target, output.target)
            target_index = self.target_combo.findData(output.target)
        self.target_combo.setCurrentIndex(target_index)
        self.output_enabled.setChecked(output.enabled)
        self.effect_combo.setCurrentText(output.effect)
        self.effect_controls.set_effect(output.effect, output.effect_parameters)
        self.palette_editor.set_effect(output.effect)
        self.palette_editor.set_palette(output.colours)
        _refresh_target_effect_controls(self)
    finally:
        self._loading = False


def _capture_output(self) -> tuple[ModeOutput, ...]:
    target = _selected_target(self)
    effect = self.effect_combo.currentText().strip().upper()
    colours = tuple(self.palette_editor.palette())
    parameters = self.effect_controls.parameters()
    validate_effect_configuration(effect, colours, parameters)
    return (
        ModeOutput(
            target,
            effect,
            colours,
            parameters,
            self.output_enabled.isChecked(),
        ),
    )


def _set_direction_choices(editor, allowed: tuple[str, ...]) -> None:
    combo = editor.direction[1]
    current = combo.currentData()
    if current is None:
        current = allowed[0]
    current = str(current)
    existing = tuple(str(combo.itemData(index)) for index in range(combo.count()))
    if existing == allowed:
        return
    combo.blockSignals(True)
    try:
        combo.clear()
        for value in allowed:
            combo.addItem(_display_direction(value), value)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
    finally:
        combo.blockSignals(False)


def _refresh_target_effect_controls(self) -> None:
    if not hasattr(self, "effect_controls") or not hasattr(self, "effect_combo"):
        return
    effect = self.effect_combo.currentText().strip().upper()
    if effect != WAVE:
        return
    target = self.target_combo.currentData() if hasattr(self, "target_combo") else None
    govee = isinstance(target, str) and target.startswith("GOVEE_ENHANCED::")
    _set_direction_choices(
        self.effect_controls,
        _GOVEE_WAVE_DIRECTIONS if govee else WAVE_DIRECTIONS,
    )
    arrange = getattr(self.effect_controls, "_arrange", None)
    if callable(arrange):
        arrange()


def _target_changed(self, _index: int) -> None:
    if self._loading:
        return
    _refresh_target_effect_controls(self)
    self._changed()


def _refresh_scene_count(self) -> None:
    count = len(self._library.modes)
    self.mode_count.setText(f"{count} mode" + ("" if count == 1 else "s"))


def _refresh_phase_table(self) -> None:
    if self._mode_index < 0:
        self.phase_table.setRowCount(0)
        self.total_time.setText("No mode selected")
        return

    current = self._current_mode()
    self.phase_table.blockSignals(True)
    try:
        self.phase_table.setRowCount(len(current.phases))
        for row, phase in enumerate(current.phases):
            values = (
                str(row + 1),
                phase.name or f"Phase {row + 1}",
                f"{phase.duration_seconds:.1f} s",
                str(len(phase.outputs)),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsDragEnabled
                )
                self.phase_table.setItem(row, column, item)
        self.phase_table.selectRow(self._phase_index)
    finally:
        self.phase_table.blockSignals(False)

    count = len(current.phases)
    suffix = "phase" if count == 1 else "phases"
    self.total_time.setText(f"{count} {suffix} · {current.duration_seconds:.1f} s")


def _set_mode_draft_dirty(self, dirty: bool) -> None:
    self._mode_draft_dirty = bool(dirty)
    if hasattr(self, "draft_state"):
        self.draft_state.setText(UNSAVED_MODE_CHANGES if dirty else "")
    if hasattr(self, "apply_mode_button"):
        self.apply_mode_button.setEnabled(bool(dirty) and self._mode_index >= 0)
    if hasattr(self, "cancel_mode_button"):
        self.cancel_mode_button.setEnabled(bool(dirty) and self._mode_index >= 0)


def _begin_mode_draft(self) -> None:
    if self._mode_index < 0 or getattr(self, "_mode_draft_dirty", False):
        return
    self._mode_draft_baseline = self._current_mode()
    window = self._window()
    self._profile_dirty_before_mode_draft = bool(
        getattr(window, "_profile_dirty", False)
    )
    self._profile_before_mode_draft = getattr(window, "_profile", None)
    self._mode_dirty_before_mode_draft = bool(getattr(self, "_dirty", False))
    _set_mode_draft_dirty(self, True)


def _commit_current(self) -> None:
    if self._loading or self._mode_index < 0:
        return
    current = self._current_mode()
    phases = list(current.phases)
    phase = phases[self._phase_index]
    outputs = list(phase.outputs)
    replacement = self._capture_output()
    outputs[self._output_index:self._output_index + 1] = replacement
    phases[self._phase_index] = ModePhase(
        self.phase_name.text().strip() or f"Phase {self._phase_index + 1}",
        self.phase_duration.value(),
        tuple(outputs),
    )
    updated = LightingMode(
        self.mode_name.text().strip() or "NEW MODE",
        tuple(phases),
    )
    if updated == current:
        return
    _begin_mode_draft(self)
    self._replace_current_mode(updated)
    self._refresh_phase_table()
    self._refresh_output_list()
    self._mark_profile_dirty()


def _changed(self, *_args) -> None:
    if self._loading or self._mode_index < 0:
        return
    _begin_mode_draft(self)
    try:
        self._commit_current()
    except Exception:
        # Keep the explicit draft state visible even when an intermediate value
        # is not yet valid (for example while typing a duplicate mode name).
        self._mark_profile_dirty()


def _refresh_mode_selector_name(self) -> None:
    if self._mode_index < 0 or getattr(self, "_mode_draft_dirty", False):
        return
    self.mode_selector.blockSignals(True)
    try:
        self.mode_selector.setItemText(self._mode_index, self._current_mode().name)
    finally:
        self.mode_selector.blockSignals(False)


def _load_mode(self, mode: LightingMode) -> None:
    _ORIGINAL_LOAD_MODE(self, mode)
    self._mode_draft_baseline = mode
    _set_mode_draft_dirty(self, False)


def _library(self) -> ModeLibrary:
    if getattr(self, "_mode_draft_dirty", False):
        raise ValueError("Apply or cancel the current scene changes before saving the profile")
    return self._library


def _apply_mode_changes(self) -> None:
    if self._mode_index < 0:
        return
    try:
        self._commit_current()
        current = self._current_mode()
        # Rebuild the library once more to enforce canonical unique mode names.
        modes = list(self._library.modes)
        modes[self._mode_index] = current
        self._library = ModeLibrary(tuple(modes))
    except Exception as exc:
        QMessageBox.warning(self, "Cannot apply scene changes", str(exc))
        return

    self._mode_draft_baseline = current
    _set_mode_draft_dirty(self, False)
    self.mode_selector.blockSignals(True)
    try:
        self.mode_selector.setItemText(self._mode_index, current.name)
    finally:
        self.mode_selector.blockSignals(False)
    self._mark_profile_dirty()


def _cancel_mode_changes(self) -> None:
    if self._mode_index < 0 or not getattr(self, "_mode_draft_dirty", False):
        return
    baseline = getattr(self, "_mode_draft_baseline", None)
    if not isinstance(baseline, LightingMode):
        return

    profile_dirty_before = bool(
        getattr(self, "_profile_dirty_before_mode_draft", False)
    )
    profile_before = getattr(self, "_profile_before_mode_draft", None)
    mode_dirty_before = bool(
        getattr(self, "_mode_dirty_before_mode_draft", False)
    )

    modes = list(self._library.modes)
    modes[self._mode_index] = baseline
    self._library = ModeLibrary(tuple(modes))
    self.mode_selector.blockSignals(True)
    try:
        self.mode_selector.setItemText(self._mode_index, baseline.name)
    finally:
        self.mode_selector.blockSignals(False)
    self._load_mode(baseline)
    self._dirty = mode_dirty_before

    window = self._window()
    current_profile = getattr(window, "_profile", None)
    if current_profile is not None:
        rules_changed_during_draft = current_profile != profile_before
        window._profile_dirty = bool(
            profile_dirty_before or rules_changed_during_draft
        )
        updater = getattr(window, "_update_profile_buttons", None)
        if callable(updater):
            updater()


def _mode_selected(self, index: int) -> None:
    if self._loading or index < 0 or index == self._mode_index:
        return
    if getattr(self, "_mode_draft_dirty", False):
        self.mode_selector.blockSignals(True)
        try:
            self.mode_selector.setCurrentIndex(self._mode_index)
        finally:
            self.mode_selector.blockSignals(False)
        QMessageBox.information(
            self,
            "Finish scene changes",
            "Apply or cancel the current scene changes before selecting another scene.",
        )
        return
    self._mode_index = index
    self._load_mode(self._library.modes[index])


def _phase_selected(self) -> None:
    if self._loading:
        return
    rows = sorted({item.row() for item in self.phase_table.selectedItems()})
    if not rows:
        return
    row = rows[0]
    if row == self._phase_index:
        return
    try:
        self._commit_current()
    except Exception as exc:
        QMessageBox.warning(self, "Cannot switch phase", str(exc))
        self.phase_table.selectRow(self._phase_index)
        return
    self._load_phase(row)


def _ensure_clean_mode(self, action: str) -> bool:
    if not getattr(self, "_mode_draft_dirty", False):
        return True
    QMessageBox.information(
        self,
        f"Cannot {action}",
        "Apply or cancel the current scene changes first.",
    )
    return False


def _ensure_profile_for_mode(self) -> None:
    window = self._window()
    if getattr(window, "_profile", None) is not None:
        return
    name = getattr(self._ui, "NEW_PROFILE_NAME", "New profile")
    window._profile = make_default_profile(name)
    window._profile_path = None
    window._profile_dirty = True
    if hasattr(window, "profile_name"):
        window.profile_name.setText(name)
    logger = getattr(window, "_logger", None)
    if logger is not None:
        logger.event("PROFILE_NEW", profile=name)
    refresher = getattr(window, "_refresh_table", None)
    if callable(refresher):
        refresher(())
    updater = getattr(window, "_update_profile_buttons", None)
    if callable(updater):
        updater()


def _new_mode(self) -> None:
    if not _ensure_clean_mode(self, "add a scene"):
        return
    _ensure_profile_for_mode(self)
    existing = {mode.key for mode in self._library.modes}
    number = len(self._library.modes) + 1
    name = "NEW SCENE"
    while canonical_mode_name(name) in existing:
        name = f"NEW MODE {number}"
        number += 1
    modes = (*self._library.modes, make_default_mode(name))
    self._load_library(ModeLibrary(modes), mode_index=len(modes) - 1, dirty=True)
    self._mark_profile_dirty()


def _duplicate_mode(self) -> None:
    if self._mode_index < 0 or not _ensure_clean_mode(self, "duplicate this scene"):
        return
    source = self._current_mode()
    existing = {mode.key for mode in self._library.modes}
    base = f"{source.name} COPY"
    candidate = base
    number = 2
    while canonical_mode_name(candidate) in existing:
        candidate = f"{base} {number}"
        number += 1
    copy = replace(source, name=candidate)
    modes = list(self._library.modes)
    modes.insert(self._mode_index + 1, copy)
    self._load_library(
        ModeLibrary(tuple(modes)),
        mode_index=self._mode_index + 1,
        dirty=True,
    )
    self._mark_profile_dirty()


def _delete_mode(self) -> None:
    if self._mode_index < 0 or not _ensure_clean_mode(self, "delete this scene"):
        return
    modes = list(self._library.modes)
    del modes[self._mode_index]
    next_index = min(self._mode_index, len(modes) - 1)
    self._load_library(
        ModeLibrary(tuple(modes)),
        mode_index=max(0, next_index),
        dirty=True,
    )
    self._mark_profile_dirty()


def _begin_structural_edit(self) -> None:
    _begin_mode_draft(self)


def _add_phase(self) -> None:
    try:
        self._commit_current()
        _begin_structural_edit(self)
        current = self._current_mode()
        phases = list(current.phases)
        phases.append(
            ModePhase(
                f"Phase {len(phases) + 1}",
                10.0,
                (
                    ModeOutput(
                        "KEYBOARD",
                        "STATIC",
                        ((0, 128, 255),),
                        default_parameters_for_effect("STATIC"),
                    ),
                ),
            )
        )
        self._replace_current_mode(LightingMode(current.name, tuple(phases)))
        self._phase_index = len(phases) - 1
        self._refresh_phase_table()
        self._load_phase(self._phase_index)
        self._mark_profile_dirty()
    except Exception as exc:
        QMessageBox.warning(self, "Cannot add phase", str(exc))


def _duplicate_phase(self) -> None:
    try:
        self._commit_current()
        _begin_structural_edit(self)
        current = self._current_mode()
        source = current.phases[self._phase_index]
        duplicate = replace(
            source,
            name=f"{source.name} copy" if source.name else f"Phase {self._phase_index + 2}",
        )
        phases = list(current.phases)
        insertion = self._phase_index + 1
        phases.insert(insertion, duplicate)
        self._replace_current_mode(LightingMode(current.name, tuple(phases)))
        self._phase_index = insertion
        self._refresh_phase_table()
        self._load_phase(insertion)
        self._mark_profile_dirty()
    except Exception as exc:
        QMessageBox.warning(self, "Cannot duplicate phase", str(exc))


def _delete_phase(self) -> None:
    try:
        self._commit_current()
        current = self._current_mode()
        if len(current.phases) <= 1:
            QMessageBox.information(
                self,
                "Keep one phase",
                "A scene must contain at least one phase.",
            )
            return
        _begin_structural_edit(self)
        phases = list(current.phases)
        del phases[self._phase_index]
        self._phase_index = min(self._phase_index, len(phases) - 1)
        self._replace_current_mode(LightingMode(current.name, tuple(phases)))
        self._refresh_phase_table()
        self._load_phase(self._phase_index)
        self._mark_profile_dirty()
    except Exception as exc:
        QMessageBox.warning(self, "Cannot delete phase", str(exc))


def _move_phase(self, delta: int) -> None:
    try:
        self._commit_current()
        current = self._current_mode()
        target = self._phase_index + delta
        if target < 0 or target >= len(current.phases):
            return
        _begin_structural_edit(self)
        phases = list(current.phases)
        phases[self._phase_index], phases[target] = phases[target], phases[self._phase_index]
        self._replace_current_mode(LightingMode(current.name, tuple(phases)))
        self._phase_index = target
        self._refresh_phase_table()
        self._load_phase(target)
        self._mark_profile_dirty()
    except Exception as exc:
        QMessageBox.warning(self, "Cannot move phase", str(exc))


def _drag_move_phase(self, source: int, target: int) -> None:
    if source == target:
        return
    try:
        self._commit_current()
        _begin_structural_edit(self)
        current = self._current_mode()
        phases = list(current.phases)
        phase = phases.pop(source)
        phases.insert(target, phase)
        self._replace_current_mode(LightingMode(current.name, tuple(phases)))
        self._phase_index = target
        self._refresh_phase_table()
        self._load_phase(target)
        self._mark_profile_dirty()
    except Exception as exc:
        QMessageBox.warning(self, "Cannot reorder phases", str(exc))


def _add_output(self) -> None:
    try:
        self._commit_current()
        current = self._current_mode()
        phase = current.phases[self._phase_index]
        used = {output.target for output in phase.outputs}
        candidate = next(
            (
                target
                for target in mode_ui._friendly_target_labels()
                if target not in used
            ),
            None,
        )
        if candidate is None:
            QMessageBox.information(
                self,
                "No unused targets",
                "Every currently configured lighting target already has an output in this phase.",
            )
            return
        _begin_structural_edit(self)
        outputs = (
            *phase.outputs,
            ModeOutput(
                candidate,
                "STATIC",
                ((0, 128, 255),),
                default_parameters_for_effect("STATIC"),
            ),
        )
        phases = list(current.phases)
        phases[self._phase_index] = replace(phase, outputs=outputs)
        self._replace_current_mode(LightingMode(current.name, tuple(phases)))
        self._output_index = len(outputs) - 1
        self._refresh_output_list()
        self._load_output(self._output_index)
        self._mark_profile_dirty()
    except Exception as exc:
        QMessageBox.warning(self, "Cannot add output", str(exc))


def _remove_output(self) -> None:
    try:
        self._commit_current()
        phase = self._current_phase()
        if len(phase.outputs) <= 1:
            QMessageBox.information(
                self,
                "Keep one output",
                "A phase must contain at least one output.",
            )
            return
        _begin_structural_edit(self)
        outputs = list(phase.outputs)
        del outputs[self._output_index]
        current = self._current_mode()
        phases = list(current.phases)
        phases[self._phase_index] = replace(phase, outputs=tuple(outputs))
        self._replace_current_mode(LightingMode(current.name, tuple(phases)))
        self._output_index = min(self._output_index, len(outputs) - 1)
        self._refresh_output_list()
        self._load_output(self._output_index)
        self._mark_profile_dirty()
    except Exception as exc:
        QMessageBox.warning(self, "Cannot remove output", str(exc))


def _set_mode_controls_enabled(self, enabled: bool) -> None:
    """Keep Add mode reachable even when the library is empty."""
    self.sequence_panel.setEnabled(True)
    self.mode_selector.setEnabled(enabled)
    self.new_mode_button.setEnabled(True)
    self.duplicate_mode_button.setEnabled(enabled)
    self.delete_mode_button.setEnabled(enabled)
    for name in (
        "add_phase_button",
        "duplicate_phase_button",
        "delete_phase_button",
        "move_up_button",
        "move_down_button",
        "phase_table",
    ):
        control = getattr(self, name, None)
        if control is not None:
            control.setEnabled(enabled)
    if hasattr(self, "apply_mode_button"):
        self.apply_mode_button.setEnabled(
            enabled and bool(getattr(self, "_mode_draft_dirty", False))
        )
    if hasattr(self, "cancel_mode_button"):
        self.cancel_mode_button.setEnabled(
            enabled and bool(getattr(self, "_mode_draft_dirty", False))
        )


def _sync_live_authoring_lock(self) -> None:
    window = self._window()
    running = bool(getattr(window, "_live_running", lambda: False)())
    has_mode = self._mode_index >= 0
    authoring = has_mode and not running

    self.mode_selector.setEnabled(authoring)
    self.new_mode_button.setEnabled(not running)
    self.duplicate_mode_button.setEnabled(authoring)
    self.delete_mode_button.setEnabled(authoring)
    for name in (
        "add_phase_button",
        "duplicate_phase_button",
        "delete_phase_button",
        "move_up_button",
        "move_down_button",
        "phase_table",
        "mode_name",
        "phase_name",
        "phase_duration",
        "output_list",
        "add_output_button",
        "remove_output_button",
        "target_combo",
        "output_enabled",
        "effect_combo",
        "effect_controls",
        "palette_editor",
        "apply_mode_button",
        "cancel_mode_button",
    ):
        control = getattr(self, name, None)
        if control is not None:
            control.setEnabled(authoring)

    if running and hasattr(self, "draft_state"):
        self.draft_state.setText("Lighting running — stop lighting to edit this profile.")
    elif hasattr(self, "draft_state"):
        self.draft_state.setText(
            "Unapplied mode changes"
            if getattr(self, "_mode_draft_dirty", False)
            else ""
        )

    empty_state = getattr(self, "mode_editor_empty_state", None)
    editor_scroll = getattr(self, "editor_scroll", None)
    if empty_state is not None:
        empty_state.setVisible(not has_mode)
    if editor_scroll is not None:
        editor_scroll.setVisible(has_mode)

    if hasattr(self, "run_status") and not has_mode and not running:
        self.run_status.setText("No mode selected.")
        self.run_status.setProperty("edlTextRole", "secondary")
        self.run_status.setStyleSheet("font-weight:600;")


def _refresh_run_state(self) -> None:
    _ORIGINAL_REFRESH_RUN_STATE(self)
    _sync_live_authoring_lock(self)


def _apply_mode_help(surface) -> None:
    help_items = (
        (surface.mode_selector, "Select a scripted mode stored in the current profile."),
        (surface.new_mode_button, "Create a new scripted mode in the current profile."),
        (surface.duplicate_mode_button, "Copy the selected mode, including all phases and outputs."),
        (surface.delete_mode_button, "Remove the selected mode from the current profile."),
        (surface.add_phase_button, "Add a new timed phase to the end of the selected mode."),
        (surface.duplicate_phase_button, "Copy the selected phase, including its simultaneous outputs and effect settings."),
        (surface.delete_phase_button, "Remove the selected phase. A mode must keep at least one phase."),
        (surface.move_up_button, "Move the selected phase one place higher. Phases run from top to bottom."),
        (surface.move_down_button, "Move the selected phase one place lower. Phases run from top to bottom."),
        (surface.phase_table, "Ordered mode phases. Each phase runs for its duration, then EDL moves to the next phase."),
        (surface.mode_name, "Name used to identify this scripted mode and to invoke it through the COVAS:NEXT / Chromas Next bridge."),
        (surface.phase_name, "Name of this timed step in the mode."),
        (surface.phase_duration, "How long this phase stays active before EDL moves to the next phase."),
        (surface.output_list, "Lighting outputs that run together during the selected phase."),
        (surface.add_output_button, "Add another independently configurable output to this phase."),
        (surface.remove_output_button, "Remove the selected lighting output from this phase."),
        (surface.apply_mode_button, "Apply the current mode changes to this profile."),
        (surface.cancel_mode_button, "Discard the current mode changes and restore the last applied version."),
        (
            surface.run_button,
            "Preview the current Mode editor state on the real lighting hardware "
            "without applying it or starting the full profile.",
        ),
        (
            surface.stop_button,
            "Stop the local Mode preview and restore the previously owned lighting state.",
        ),
    )
    for widget, text in help_items:
        _set_help(widget, text)


def apply_mode_ui_copy(ui_module) -> None:
    """Apply the direct EDL presentation analogue before any window is built."""
    surface_class = mode_ui.ModeEditorSurface
    if getattr(surface_class, "_edl_copy_ui_applied", False):
        return

    global _ORIGINAL_LOAD_MODE, _ORIGINAL_REFRESH_RUN_STATE
    _ORIGINAL_LOAD_MODE = surface_class._load_mode
    _ORIGINAL_REFRESH_RUN_STATE = surface_class._refresh_run_state

    surface_class._build_mode_row = _build_mode_row
    surface_class._build_sequence_panel = _build_phases_panel
    surface_class._section = _section
    surface_class._build_editor_panel = _build_editor_panel
    surface_class._populate_targets = _populate_targets
    surface_class._load_output = _load_output
    surface_class._capture_output = _capture_output
    surface_class._target_changed = _target_changed
    surface_class._refresh_mode_count = _refresh_scene_count
    surface_class._refresh_phase_table = _refresh_phase_table
    surface_class._changed = _changed
    surface_class._commit_current = _commit_current
    surface_class._refresh_mode_selector_name = _refresh_mode_selector_name
    surface_class._load_mode = _load_mode
    surface_class.library = _library
    surface_class.apply_mode_changes = _apply_mode_changes
    surface_class.cancel_mode_changes = _cancel_mode_changes
    surface_class._mode_selected = _mode_selected
    surface_class._phase_selected = _phase_selected
    surface_class.new_mode = _new_mode
    surface_class.duplicate_mode = _duplicate_mode
    surface_class.delete_mode = _delete_mode
    surface_class.add_phase = _add_phase
    surface_class.duplicate_phase = _duplicate_phase
    surface_class.delete_phase = _delete_phase
    surface_class.move_phase = _move_phase
    surface_class.drag_move_phase = _drag_move_phase
    surface_class.add_output = _add_output
    surface_class.remove_output = _remove_output
    surface_class._set_mode_controls_enabled = _set_mode_controls_enabled
    surface_class._refresh_run_state = _refresh_run_state

    previous_surface_init = surface_class.__init__

    def surface_init(self, *args, **kwargs) -> None:
        previous_surface_init(self, *args, **kwargs)
        self._mode_draft_baseline = None
        self._mode_draft_dirty = False
        self._profile_dirty_before_mode_draft = False
        # Acceptance choreography lives in a fixture, not in every new user
        # profile. Start with an empty library and let + Add mode create intent.
        self.set_library(ModeLibrary(()))
        _set_mode_draft_dirty(self, False)
        _apply_mode_help(self)
        _sync_live_authoring_lock(self)

    surface_class.__init__ = surface_init
    surface_class._edl_copy_ui_applied = True

    window_class = ui_module.MainWindow
    previous_window_init = window_class.__init__
    previous_update_profile_buttons = window_class._update_profile_buttons

    def window_init(self, *args, **kwargs) -> None:
        previous_window_init(self, *args, **kwargs)
        rules_button = getattr(self, "edl_rules_mode_button", None)
        modes_button = getattr(self, "covas_modes_mode_button", None)
        if rules_button is not None:
            rules_button.setText(RULES_TAB)
            _set_help(rules_button, RULES_TAB_HELP)
        if modes_button is not None:
            modes_button.setText(SCRIPTED_MODES_TAB)
            _set_help(modes_button, SCRIPTED_MODES_TAB_HELP)

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        surface = getattr(self, "_covas_modes_workspace", None)
        if surface is None:
            return
        dirty = bool(getattr(surface, "_mode_draft_dirty", False))
        running = bool(getattr(self, "_live_running", lambda: False)())
        # Match normal EDL's immutable live snapshot: a mode draft must be
        # applied/cancelled before Save or Start lighting.
        for name in ("save_button", "save_as_button"):
            button = getattr(self, name, None)
            if button is not None and dirty:
                button.setEnabled(False)
        start = getattr(self, "start_lighting_button", None)
        if start is not None and dirty and not running:
            start.setEnabled(False)
        _sync_live_authoring_lock(surface)

    window_class.__init__ = window_init
    window_class._update_profile_buttons = update_profile_buttons
