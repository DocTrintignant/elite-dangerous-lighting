#!/usr/bin/env python3
"""Coherent operator-facing desktop UI for Elite Dangerous Lighting.

This module replaces the accumulated 2026-09-03 launcher patch stack with one
explicit MainWindow subclass.  The frozen Status decoder, rule engine, profile
format, effect runtime and Chroma transport remain in their existing modules.

Two small Argument-row adapters are still applied once at import time because
they own the canonical human reference catalogue and categorical mappings.  All
window/editor workflow behaviour lives directly in this class rather than being
patched by a chain of independent modules.
"""

from __future__ import annotations

import sys
import threading
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QObject, QEvent, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QDoubleValidator,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QProgressBar,
    QStatusBar,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from keyboard_input import virtual_key_code
from lighting_chroma_quick_effects import (
    CONTINUOUS_RULE_EFFECTS,
    TRIGGERED_SCENE_EFFECTS,
)
from lighting_effect_config import (
    BREATH,
    FLASH,
    PULSE,
    STATIC,
    default_parameters_for_effect,
)
from lighting_effect_editor import EffectPaletteEditor, EffectParametersEditor
from lighting_elite_diagnostics import (
    CANONICAL_DIAGNOSTIC_KEYS,
    EliteStatusDiagnostics,
    format_live_value,
)
from lighting_hid_bind import capture_moved_axis, iter_button_presses
from lighting_hid_capture import HidDevice, capture_next_button, enumerate_hid_devices
from lighting_hid_state import AxisLiveMonitor, axis_position_display
from lighting_profile_runner import format_run_error, profile_uses_elite_status, run_profile
from lighting_device_availability import filter_profile_for_runtime
from lighting_preview_runtime import run_calculated_preview
from lighting_paths import log_dir as default_log_dir, profiles_dir
from lighting_rule_simulator import (
    build_simulated_state,
    parse_synthetic_value,
    referenced_argument_sources,
    referenced_axis_addresses,
    referenced_button_addresses,
    simulate_profile,
)
from lighting_profiles import NativeLightingProfile, ProfileDefault, make_default_profile
from lighting_rules import ArgumentCondition
from lighting_ui_argument_reference import (
    apply_argument_reference_ui,
    apply_categorical_argument_inputs,
    display_name_for,
    resolve_edl_name,
)
from lighting_ui_help import HELP_TEXT, SECTION_HELP
from lighting_ui_language import APPLY_RULE_CHANGES, CANCEL_RULE_CHANGES
from lighting_ui_tokens import (
    ACCENT,
    BORDER_STRONG,
    SURFACE_BAR,
    STATUS_ERROR,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
)
from lighting_ui_log import UiSessionLogger as RealUiSessionLogger
from lighting_settings import app_settings
from lighting_virpil_ui import VirpilUiImport, load_virpil_for_ui
from lighting_worker_lifecycle import request_stop_and_join


# Preserved native editor base formerly housed in lighting_ui_v2.py.
import os
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from elite_status import FLAG2_BITS, FLAG_BITS, SCALAR_KEYS
from lighting_chroma_preview import run_effect_preview
from lighting_effect_config import (
    BREATH,
    FLASH,
    KNOWN_EFFECTS,
    PULSE,
    STATIC,
    EffectParameters,
    default_parameters_for_effect,
    validate_effect_configuration,
)
from lighting_hid_capture import HidDevice, capture_next_button, enumerate_hid_devices
from lighting_intent import intent_from_rule
from lighting_profiles import NativeLightingProfile, load_profile, save_profile
from lighting_rules import (
    AXIS_BETWEEN_OPERATOR,
    BUTTON_STATES,
    SUPPORTED_AXIS_OPERATORS,
    SUPPORTED_OPERATORS,
    ArgumentCondition,
    AxisSource,
    ButtonSource,
    KeyboardCombination,
    LightingRule,
)
from lighting_ui_log import UiSessionLogger
from lighting_paths import log_dir as default_log_dir, profiles_dir
from lighting_ui_presenter import is_virpil_link_profile_path
from lighting_ui_tokens import SEMANTIC_QSS, TEXT_TERTIARY

try:
    from PySide6.QtCore import QObject, QItemSelectionModel, Qt, Signal
    from PySide6.QtGui import QBrush, QColor, QDoubleValidator, QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QDoubleSpinBox,
        QSplitter,
        QStackedWidget,
        QStyle,
        QStyledItemDelegate,
        QTableWidget,
        QTableWidgetItem,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
    from lighting_colour_picker import PaletteStripWidget
    from lighting_effect_editor import EffectPaletteEditor, EffectParametersEditor
except ModuleNotFoundError as exc:  # pragma: no cover
    if exc.name and exc.name.startswith("PySide6"):
        raise SystemExit(
            "PySide6 is required for the UI development build.\n"
            "Install it with: py -m pip install PySide6"
        ) from exc
    raise


APP_TITLE = "Elite Dangerous Lighting"
APP_ICON_PATH = Path(__file__).resolve().parent / "assets" / "edl_icon.svg"
APP_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def load_application_icon() -> QIcon:
    """Render the canonical EDL icon into the Windows/UI size set."""
    renderer = QSvgRenderer(str(APP_ICON_PATH))
    icon = QIcon()
    if not renderer.isValid():
        return icon

    for size in APP_ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


SPLITTER_KEY = "ui/main_splitter_state"

BOOLEAN_ARGUMENTS = tuple(dict.fromkeys((*FLAG_BITS.keys(), *FLAG2_BITS.keys())))
NUMERIC_ARGUMENTS = {
    "RawFlags",
    "RawFlags2",
    "FireGroup",
    "GuiFocus",
    "FuelMain",
    "FuelReservoir",
    "Cargo",
    "Latitude",
    "Altitude",
    "Longitude",
    "Heading",
    "PlanetRadius",
    "Balance",
    "Oxygen",
    "Health",
    "Temperature",
    "Gravity",
}
RAW_ARGUMENTS = ("RawFlags", "RawFlags2")
NORMAL_ARGUMENTS = tuple(
    dict.fromkeys(
        (
            *BOOLEAN_ARGUMENTS,
            *(key for key in SCALAR_KEYS if key not in RAW_ARGUMENTS),
        )
    )
)

APP_STYLESHEET = """
QToolTip {
    color: #F2F2F2;
    background-color: #2D2D2D;
    border: 1px solid #666666;
    padding: 6px 8px;
}
QMenu {
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px 6px 10px;
}
QMenu::item:selected {
    background-color: #3A3A3A;
}
QMenu::item:disabled {
    color: #969696;
}
QComboBox QAbstractItemView {
    padding: 4px;
    selection-background-color: #6E3A86;
}
QComboBox QAbstractItemView::item {
    min-height: 28px;
    padding: 4px 8px;
}
QPushButton, QLineEdit {
    min-height: 34px;
    padding: 3px 8px;
}
QComboBox {
    min-height: 34px;
    padding: 0px 28px 0px 10px;
}
QSpinBox, QDoubleSpinBox {
    min-height: 34px;
}
QFrame#sectionBox {
    border: 1px solid rgba(255, 255, 255, 42);
    border-radius: 5px;
}
QFrame#deleteConfirm {
    border: 1px solid #A45C3B;
    border-radius: 4px;
}
QTableWidget {
    gridline-color: rgba(255, 255, 255, 24);
}
QTableWidget::item {
    padding-left: 6px;
    padding-right: 6px;
}
QTableWidget::item:selected {
    background: #6E3A86;
    color: white;
}
QHeaderView::section {
    font-weight: 600;
    padding: 5px 6px;
}
QPushButton:disabled, QToolButton:disabled, QLineEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: #969696;
    background-color: #252525;
}
QPushButton#workspaceTab:checked {
    color: #FFFFFF;
    background-color: #6E3A86;
    border: 1px solid #8E5AA5;
}
QSplitter::handle:horizontal {
    width: 9px;
    margin: 0 2px;
    background: #555555;
}
QSplitter::handle:horizontal:hover {
    background: #777777;
}
QScrollBar:vertical {
    width: 15px;
    margin: 0px;
    background: #2D2D2D;
}
QScrollBar::handle:vertical {
    min-height: 36px;
    border-radius: 6px;
    margin: 2px;
    background: #777777;
}
QScrollBar::handle:vertical:hover {
    background: #999999;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    height: 14px;
    background: #2D2D2D;
}
QScrollBar::handle:horizontal {
    min-width: 36px;
    border-radius: 6px;
    margin: 2px;
    background: #777777;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
""" + SEMANTIC_QSS


class NoCellFocusDelegate(QStyledItemDelegate):
    """Remove the spreadsheet-like current-cell focus rectangle."""

    def initStyleOption(self, option, index) -> None:  # type: ignore[override]
        super().initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_HasFocus


class RuleTableWidget(QTableWidget):
    """Finished-rule list with single-row drag reorder and row-only semantics."""

    moveRequested = Signal(int, int)

    def __init__(self, rows: int, columns: int, parent: QWidget | None = None) -> None:
        super().__init__(rows, columns, parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(False)
        self.setItemDelegate(NoCellFocusDelegate(self))
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        selected = self.selectionModel().selectedRows()
        if len(selected) != 1:
            event.ignore()
            return
        source = selected[0].row()
        target = self.indexAt(event.position().toPoint()).row()
        if target < 0:
            target = self.rowCount() - 1
        if source != target:
            self.moveRequested.emit(source, target)
        event.ignore()


class SectionBox(QFrame):
    """Simple structural group: title + content, no hidden behavior."""

    def __init__(
        self,
        title: str,
        subtitle: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sectionBox")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(12, 10, 12, 12)
        self.root.setSpacing(9)

        heading_row = QHBoxLayout()
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 12pt; font-weight: 600;")
        heading_row.addWidget(heading)
        if subtitle:
            helper = QLabel(subtitle)
            helper.setProperty("edlTextRole", "secondary")
            helper.setStyleSheet("font-size:9.75pt;")
            heading_row.addWidget(helper)
        heading_row.addStretch(1)
        self.root.addLayout(heading_row)


class CaptureSignals(QObject):
    buttonDetected = Signal(object, int)
    error = Signal(str)


class PreviewSignals(QObject):
    finished = Signal(object)


class KeyCaptureEdit(QLineEdit):
    """Keyboard combination field that can capture a real Qt key chord."""

    combinationCaptured = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._capture_armed = False

    def arm_capture(self) -> None:
        self._capture_armed = True
        self.setPlaceholderText("Press the key combination now…")
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.selectAll()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if not self._capture_armed:
            super().keyPressEvent(event)
            return
        if event.key() in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            event.accept()
            return

        text = QKeySequence(event.keyCombination()).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        if text:
            self.setText(text)
            self._capture_armed = False
            self.setPlaceholderText("Example: Ctrl+F12")
            self.combinationCaptured.emit(text)
        event.accept()


class ArgumentConditionRow(QFrame):
    changed = Signal()
    removeRequested = Signal(object)

    def __init__(
        self,
        condition: ArgumentCondition | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._loading = False

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(7)

        self.source = QComboBox()
        self.source.setMinimumWidth(190)
        self._populate_sources()
        self.source.currentTextChanged.connect(self._source_changed)
        row.addWidget(self.source, 2)

        self.operator = QComboBox()
        self.operator.setMinimumWidth(130)
        self.operator.currentTextChanged.connect(lambda _text: self._emit_changed())
        row.addWidget(self.operator, 1)

        self.value_stack = QStackedWidget()
        self.bool_value = QComboBox()
        self.bool_value.addItems(("True", "False"))
        self.bool_value.currentTextChanged.connect(lambda _text: self._emit_changed())
        self.value_stack.addWidget(self.bool_value)

        self.numeric_value = QLineEdit()
        self.numeric_value.setValidator(QDoubleValidator(self.numeric_value))
        self.numeric_value.setMinimumWidth(120)
        self.numeric_value.textChanged.connect(lambda _text: self._emit_changed())
        self.value_stack.addWidget(self.numeric_value)

        self.text_value = QLineEdit()
        self.text_value.setMinimumWidth(140)
        self.text_value.textChanged.connect(lambda _text: self._emit_changed())
        self.value_stack.addWidget(self.text_value)

        row.addWidget(self.value_stack, 2)

        remove = QPushButton("Remove")
        remove.setToolTip("Remove this AND condition")
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        row.addWidget(remove)

        if condition is None:
            condition = ArgumentCondition("Docked", "Equal", True)
        self.set_condition(condition)

    def _populate_sources(self) -> None:
        self.source.clear()
        for name in NORMAL_ARGUMENTS:
            self.source.addItem(name)
        self.source.insertSeparator(self.source.count())
        self.source.addItem("RawFlags")
        self.source.addItem("RawFlags2")

    def _source_kind(self, name: str) -> str:
        if name in BOOLEAN_ARGUMENTS:
            return "bool"
        if name in NUMERIC_ARGUMENTS:
            return "numeric"
        return "text"

    def _source_changed(self, name: str) -> None:
        self._loading = True
        try:
            current_operator = self.operator.currentText()
            self.operator.clear()
            if self._source_kind(name) == "numeric":
                self.operator.addItems(SUPPORTED_OPERATORS)
            else:
                self.operator.addItems(("Equal", "Not Equal"))
            index = self.operator.findText(current_operator)
            if index >= 0:
                self.operator.setCurrentIndex(index)

            kind = self._source_kind(name)
            self.value_stack.setCurrentIndex(
                0 if kind == "bool" else 1 if kind == "numeric" else 2
            )
        finally:
            self._loading = False
        self._emit_changed()

    def set_condition(self, condition: ArgumentCondition) -> None:
        self._loading = True
        try:
            index = self.source.findText(condition.source)
            if index < 0:
                self.source.addItem(condition.source)
                index = self.source.findText(condition.source)
            self.source.setCurrentIndex(index)
            self._source_changed(condition.source)

            op_index = self.operator.findText(condition.operator)
            if op_index < 0:
                self.operator.addItem(condition.operator)
                op_index = self.operator.findText(condition.operator)
            self.operator.setCurrentIndex(op_index)

            kind = self._source_kind(condition.source)
            if kind == "bool":
                self.bool_value.setCurrentText("True" if condition.value is True else "False")
            elif kind == "numeric":
                self.numeric_value.setText(str(condition.value))
            else:
                self.text_value.setText("" if condition.value is None else str(condition.value))
        finally:
            self._loading = False

    def condition(self) -> ArgumentCondition:
        source = self.source.currentText().strip()
        operator = self.operator.currentText().strip()
        kind = self._source_kind(source)
        if kind == "bool":
            value: object = self.bool_value.currentText() == "True"
        elif kind == "numeric":
            text = self.numeric_value.text().strip()
            if not text:
                raise ValueError(f"{source} requires a numeric value")
            try:
                value = int(text, 10)
            except ValueError:
                value = float(text)
        else:
            value = self.text_value.text()
        return ArgumentCondition(source, operator, value)

    def _emit_changed(self) -> None:
        if not self._loading:
            self.changed.emit()


def _separator() -> QFrame:
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.VLine)
    frame.setFrameShadow(QFrame.Shadow.Sunken)
    return frame


def _parse_keyboard_keys(text: str) -> tuple[str, ...]:
    keys = tuple(part.strip().upper() for part in text.split("+") if part.strip())
    if not keys:
        raise ValueError("Keyboard source requires a key combination")
    return keys


class BaseMainWindow(QMainWindow):
    COLUMNS = ("#", "Target", "Rule type", "Effect", "Comment", "Status", "Colours")

    def __init__(self) -> None:
        super().__init__()
        self._profile: NativeLightingProfile | None = None
        self._profile_path: Path | None = None
        self._profile_dirty = False
        self._loading_editor = False
        self._table_loading = False
        self._editor_dirty = False
        self._editing_row: int | None = None
        self._new_rule_draft = False
        self._clipboard: tuple[LightingRule, ...] = ()
        self._argument_rows: list[ArgumentConditionRow] = []
        self._hid_devices: tuple[HidDevice, ...] = ()
        self._button_capture_thread: threading.Thread | None = None
        self._preview_thread: threading.Thread | None = None
        self._preview_stop_event: threading.Event | None = None

        self._logger = UiSessionLogger(default_log_dir())
        self._logger.event(
            "SESSION_START",
            application=APP_TITLE,
            log_file=self._logger.path,
        )
        self._settings = app_settings()
        self._capture_signals = CaptureSignals()
        self._capture_signals.buttonDetected.connect(self._button_detected)
        self._capture_signals.error.connect(self._button_detect_error)
        self._preview_signals = PreviewSignals()
        self._preview_signals.finished.connect(self._preview_finished)

        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1320, 780)
        self.resize(1580, 980)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 10)
        root_layout.setSpacing(9)
        self.setCentralWidget(root)

        root_layout.addLayout(self._build_header())
        self.splitter = self._build_splitter()
        root_layout.addWidget(self.splitter, 1)

        saved_splitter = self._settings.value(SPLITTER_KEY)
        if saved_splitter is not None:
            try:
                self.splitter.restoreState(saved_splitter)
            except Exception:
                self.splitter.setSizes([680, 900])
        else:
            self.splitter.setSizes([680, 900])

        self._install_shortcuts()
        self._set_empty_state()
        self.refresh_hid_devices(log=False)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(9)

        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size: 15.75pt; font-weight: 600;")
        row.addWidget(title)
        row.addStretch(1)

        self.profile_name = QLabel("No profile loaded")
        self.profile_name.setStyleSheet("font-weight: 600;")
        row.addWidget(self.profile_name)

        open_button = QPushButton("Open profile…")
        open_button.clicked.connect(self.open_profile_dialog)
        row.addWidget(open_button)

        self.save_button = QPushButton("Save profile")
        self.save_button.clicked.connect(self.save_profile_current)
        self.save_button.setEnabled(False)
        row.addWidget(self.save_button)

        self.save_as_button = QPushButton("Save as…")
        self.save_as_button.clicked.connect(self.save_profile_as)
        self.save_as_button.setEnabled(False)
        row.addWidget(self.save_as_button)
        return row

    def _build_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(7)

        self.rules_panel = self._build_rules_panel()
        self.rules_panel.setMinimumWidth(500)

        self.editor_panel = self._build_editor_panel()
        self.editor_panel.setMinimumWidth(720)

        splitter.addWidget(self.rules_panel)
        splitter.addWidget(self.editor_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 7)
        return splitter

    def _build_rules_panel(self) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 5, 0)
        root.setSpacing(7)

        heading_row = QHBoxLayout()
        heading = QLabel("Rules")
        heading.setStyleSheet("font-size: 12pt; font-weight: 600;")
        heading_row.addWidget(heading)
        heading_row.addStretch(1)
        self.rule_count = QLabel("0 rules")
        heading_row.addWidget(self.rule_count)
        root.addLayout(heading_row)

        self.rule_order_hint = QLabel("Lower rules win when enabled rules target the same lights.")
        self.rule_order_hint.setProperty("edlTextRole", "tertiary")
        self.rule_order_hint.setStyleSheet("font-size:9.75pt;")
        self.rule_order_hint.setWordWrap(True)
        root.addWidget(self.rule_order_hint)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(5)

        self.add_rule_button = QPushButton("+ Add rule")
        self.add_rule_button.clicked.connect(self.add_rule)
        toolbar.addWidget(self.add_rule_button)

        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.clicked.connect(self.duplicate_rules)
        toolbar.addWidget(self.duplicate_button)

        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.request_delete_rules)
        toolbar.addWidget(self.delete_button)

        toolbar.addWidget(_separator())

        self.move_up_button = QPushButton("↑")
        self.move_up_button.setToolTip("Move selected rule(s) up")
        self.move_up_button.setFixedWidth(38)
        self.move_up_button.clicked.connect(lambda: self.move_selected_rules(-1))
        toolbar.addWidget(self.move_up_button)

        self.move_down_button = QPushButton("↓")
        self.move_down_button.setToolTip("Move selected rule(s) down")
        self.move_down_button.setFixedWidth(38)
        self.move_down_button.clicked.connect(lambda: self.move_selected_rules(1))
        toolbar.addWidget(self.move_down_button)

        toolbar.addStretch(1)

        self.more_button = QToolButton()
        self.more_button.setText("More ▾")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more_button)
        self.copy_action = menu.addAction("Copy")
        self.copy_action.triggered.connect(self.copy_rules)
        self.cut_action = menu.addAction("Cut")
        self.cut_action.triggered.connect(self.cut_rules)
        self.paste_action = menu.addAction("Paste")
        self.paste_action.triggered.connect(self.paste_rules)
        self.more_button.setMenu(menu)
        toolbar.addWidget(self.more_button)
        root.addLayout(toolbar)

        self.delete_confirm = QFrame()
        self.delete_confirm.setObjectName("deleteConfirm")
        confirm_row = QHBoxLayout(self.delete_confirm)
        confirm_row.setContentsMargins(8, 5, 8, 5)
        confirm_row.setSpacing(7)
        self.delete_confirm_label = QLabel("Delete selected rule?")
        confirm_row.addWidget(self.delete_confirm_label)
        confirm_row.addStretch(1)
        confirm = QPushButton("Confirm delete")
        confirm.clicked.connect(self.confirm_delete_rules)
        confirm_row.addWidget(confirm)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self._hide_delete_confirmation)
        confirm_row.addWidget(cancel)
        self.delete_confirm.setVisible(False)
        root.addWidget(self.delete_confirm)

        self.table = RuleTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.moveRequested.connect(self.drag_move_rule)

        header = self.table.horizontalHeader()
        for column in (0, 1, 2, 3, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(6, 170)
        for column in range(self.table.columnCount()):
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )

        self.rules_empty_hint = QLabel("No rules yet — click + Add rule to begin.")
        self.rules_empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rules_empty_hint.setProperty("edlTextRole", "secondary")
        self.rules_empty_hint.setStyleSheet("padding:12px;")
        self.rules_empty_hint.setWordWrap(True)
        root.addWidget(self.rules_empty_hint)
        root.addWidget(self.table, 1)
        return panel

    def _build_editor_panel(self) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(5, 0, 0, 0)
        root.setSpacing(7)

        header = QHBoxLayout()
        self.editor_title = QLabel("Rule editor")
        self.editor_title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        header.addWidget(self.editor_title)

        self.draft_state = QLabel("")
        self.draft_state.setProperty("edlState", "warning")
        self.draft_state.setStyleSheet("font-weight: 600;")
        self.draft_state.setWordWrap(True)
        self.draft_state.setMinimumWidth(0)
        self.draft_state.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        header.addWidget(self.draft_state, 1)

        self.cancel_rule_button = QPushButton("Cancel changes")
        self.cancel_rule_button.clicked.connect(self.cancel_rule_changes)
        header.addWidget(self.cancel_rule_button)

        self.apply_rule_button = QPushButton("Apply changes")
        self.apply_rule_button.clicked.connect(self.apply_rule)
        header.addWidget(self.apply_rule_button)
        root.addLayout(header)

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

        content_root.addWidget(self._build_rule_section())
        content_root.addWidget(self._build_source_section())
        content_root.addWidget(self._build_target_section())
        content_root.addWidget(self._build_effect_section())
        content_root.addStretch(1)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        self.editor_scroll = scroll
        return panel

    def _build_rule_section(self) -> QWidget:
        section = SectionBox("RULE", "identity and enabled state")
        grid = QGridLayout()
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(7)

        grid.addWidget(QLabel("Comment"), 0, 0)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("Describe what this rule does")
        self.comment_edit.textChanged.connect(self._editor_changed)
        grid.addWidget(self.comment_edit, 0, 1)

        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.toggled.connect(self._editor_changed)
        grid.addWidget(self.enabled_check, 0, 2)
        grid.setColumnStretch(1, 1)
        section.root.addLayout(grid)
        return section

    def _build_source_section(self) -> QWidget:
        section = SectionBox("SOURCE", "what activates this rule")

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Rule type"))
        self.source_type_combo = QComboBox()
        self.source_type_combo.setMinimumWidth(180)
        self.source_type_combo.addItems(("Argument", "Keyboard", "Button", "Axis"))
        self.source_type_combo.currentTextChanged.connect(self._source_type_changed)
        type_row.addWidget(self.source_type_combo)
        type_row.addStretch(1)
        section.root.addLayout(type_row)

        self.source_stack = QStackedWidget()
        self.source_stack.addWidget(self._build_argument_source())
        self.source_stack.addWidget(self._build_keyboard_source())
        self.source_stack.addWidget(self._build_button_source())
        self.source_stack.addWidget(self._build_axis_source())
        section.root.addWidget(self.source_stack)
        return section

    def _build_argument_source(self) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        helper = QLabel("All conditions below must match (AND). Raw bitfields are available at the end of the source list.")
        helper.setWordWrap(True)
        helper.setProperty("edlTextRole", "secondary")
        root.addWidget(helper)

        self.argument_rows_host = QWidget()
        self.argument_rows_layout = QVBoxLayout(self.argument_rows_host)
        self.argument_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.argument_rows_layout.setSpacing(5)
        root.addWidget(self.argument_rows_host)

        add = QPushButton("+ Add condition")
        add.clicked.connect(lambda: self._add_argument_condition())
        root.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        return panel

    def _build_keyboard_source(self) -> QWidget:
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)

        grid.addWidget(QLabel("Combination"), 0, 0)
        self.keyboard_keys = KeyCaptureEdit()
        self.keyboard_keys.setPlaceholderText("Example: Ctrl+F12")
        self.keyboard_keys.textChanged.connect(self._editor_changed)
        self.keyboard_keys.combinationCaptured.connect(
            lambda text: self._logger.event("KEYBOARD_CAPTURED", combination=text)
        )
        grid.addWidget(self.keyboard_keys, 0, 1)

        record = QPushButton("Record keys…")
        record.clicked.connect(self.keyboard_keys.arm_capture)
        grid.addWidget(record, 0, 2)
        grid.setColumnStretch(1, 1)
        return panel

    def _build_button_source(self) -> QWidget:
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)

        grid.addWidget(QLabel("Device"), 0, 0)
        self.button_device = QComboBox()
        self.button_device.setMinimumWidth(320)
        self.button_device.currentIndexChanged.connect(self._editor_changed)
        grid.addWidget(self.button_device, 0, 1)

        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self.refresh_hid_devices)
        grid.addWidget(refresh, 0, 2)

        grid.addWidget(QLabel("Button"), 1, 0)
        self.button_number = QSpinBox()
        self.button_number.setRange(0, 4096)
        self.button_number.setMinimumWidth(120)
        self.button_number.valueChanged.connect(self._editor_changed)
        grid.addWidget(self.button_number, 1, 1)

        self.detect_button = QPushButton("Detect button…")
        self.detect_button.setToolTip(
            "Press a physical HID button. Device and button number are captured together."
        )
        self.detect_button.clicked.connect(self.start_button_detection)
        grid.addWidget(self.detect_button, 1, 2)

        grid.addWidget(QLabel("State"), 2, 0)
        self.button_state = QComboBox()
        self.button_state.setMinimumWidth(150)
        self.button_state.addItems(BUTTON_STATES)
        self.button_state.currentTextChanged.connect(self._editor_changed)
        grid.addWidget(self.button_state, 2, 1)

        self.button_capture_status = QLabel("")
        self.button_capture_status.setWordWrap(True)
        self.button_capture_status.setProperty("edlTextRole", "secondary")
        grid.addWidget(self.button_capture_status, 3, 0, 1, 3)
        grid.setColumnStretch(1, 1)
        return panel

    def _build_axis_source(self) -> QWidget:
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)

        grid.addWidget(QLabel("Device"), 0, 0)
        self.axis_device = QComboBox()
        self.axis_device.setMinimumWidth(320)
        self.axis_device.currentIndexChanged.connect(self._editor_changed)
        grid.addWidget(self.axis_device, 0, 1)

        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self.refresh_hid_devices)
        grid.addWidget(refresh, 0, 2)

        grid.addWidget(QLabel("Axis"), 1, 0)
        self.axis_number = QSpinBox()
        self.axis_number.setRange(0, 128)
        self.axis_number.setMinimumWidth(120)
        self.axis_number.valueChanged.connect(self._editor_changed)
        grid.addWidget(self.axis_number, 1, 1)

        grid.addWidget(QLabel("Condition"), 2, 0)
        self.axis_operator = QComboBox()
        self.axis_operator.setMinimumWidth(160)
        self.axis_operator.addItems(SUPPORTED_AXIS_OPERATORS)
        self.axis_operator.currentTextChanged.connect(self._axis_operator_changed)
        grid.addWidget(self.axis_operator, 2, 1)

        grid.addWidget(QLabel("Value"), 3, 0)
        self.axis_value = QDoubleSpinBox()
        self.axis_value.setRange(-1000000.0, 1000000.0)
        self.axis_value.setDecimals(3)
        self.axis_value.setMinimumWidth(150)
        self.axis_value.valueChanged.connect(self._editor_changed)
        grid.addWidget(self.axis_value, 3, 1)

        self.axis_secondary_label = QLabel("To")
        grid.addWidget(self.axis_secondary_label, 4, 0)
        self.axis_secondary = QDoubleSpinBox()
        self.axis_secondary.setRange(-1000000.0, 1000000.0)
        self.axis_secondary.setDecimals(3)
        self.axis_secondary.setMinimumWidth(150)
        self.axis_secondary.valueChanged.connect(self._editor_changed)
        grid.addWidget(self.axis_secondary, 4, 1)

        grid.setColumnStretch(1, 1)
        return panel

    def _build_target_section(self) -> QWidget:
        section = SectionBox("TARGET", "where the lighting is sent")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)

        grid.addWidget(QLabel("Lighting target"), 0, 0)
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.target_combo.setMinimumWidth(240)
        self.target_combo.addItems(("KEYBOARD", "MOUSE", "GLOBAL"))
        self.target_combo.currentTextChanged.connect(self._target_changed)
        grid.addWidget(self.target_combo, 0, 1)

        self.target_scope = QLabel("Whole device")
        self.target_scope.setProperty("edlTextRole", "secondary")
        grid.addWidget(self.target_scope, 1, 1)
        grid.setColumnStretch(1, 1)
        section.root.addLayout(grid)
        return section

    def _build_effect_section(self) -> QWidget:
        section = SectionBox("EFFECT", "what the target should do")

        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("Effect"))
        self.effect_combo = QComboBox()
        self.effect_combo.setMinimumWidth(180)
        self.effect_combo.addItems(KNOWN_EFFECTS)
        self.effect_combo.currentTextChanged.connect(self._effect_changed)
        effect_row.addWidget(self.effect_combo)
        effect_row.addStretch(1)
        section.root.addLayout(effect_row)

        self.effect_controls = EffectParametersEditor()
        self.effect_controls.parametersChanged.connect(self._editor_changed)
        section.root.addWidget(self.effect_controls)

        self.palette_editor = EffectPaletteEditor()
        self.palette_editor.paletteChanged.connect(self._editor_changed)
        section.root.addWidget(self.palette_editor)

        preview_row = QHBoxLayout()
        self.preview_status = QLabel("")
        self.preview_status.setProperty("edlTextRole", "secondary")
        preview_row.addWidget(self.preview_status)
        preview_row.addStretch(1)
        self.preview_button = QPushButton("Preview output")
        self.preview_button.setToolTip(
            "Temporarily show the current editor effect on one supported lighting target without starting the full profile."
        )
        self.preview_button.clicked.connect(self._toggle_preview)
        preview_row.addWidget(self.preview_button)
        section.root.addLayout(preview_row)
        return section

    def open_profile_dialog(self) -> None:
        if not self._can_leave_current_draft():
            return
        start_dir = self._profile_path.parent if self._profile_path is not None else profiles_dir()
        start_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open native lighting profile",
            str(start_dir),
            "Lighting profile (*.json);;All files (*)",
        )
        if path:
            self.load_profile(Path(path))

    def load_profile(self, path: Path) -> None:
        if is_virpil_link_profile_path(path):
            QMessageBox.information(
                self,
                "Virpil profile",
                "This is a Virpil .led.json profile. Native Open/Save remains separate "
                "from the faithful Virpil import workflow.",
            )
            return
        try:
            profile = load_profile(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open profile failed", str(exc))
            return

        self._profile = profile
        self._profile_path = path
        self._profile_dirty = False
        self.profile_name.setText(profile.name)
        self.save_as_button.setEnabled(True)
        self._logger.event(
            "PROFILE_LOAD",
            path=path,
            profile=profile.name,
            rules=len(profile.rules),
        )
        self._refresh_table()
        if profile.rules:
            self._select_rows((0,))
        else:
            self._clear_editor()
        self._update_profile_buttons()

    def save_profile_current(self) -> None:
        if self._profile is None:
            return
        if self._editor_dirty:
            self.statusBar().showMessage(
                "Apply or cancel the current rule draft before saving the profile.",
                5000,
            )
            return
        if self._profile_path is None:
            self.save_profile_as()
            return
        self._write_profile(self._profile_path)

    def save_profile_as(self) -> None:
        if self._profile is None:
            return
        if self._editor_dirty:
            self.statusBar().showMessage(
                "Apply or cancel the current rule draft before saving the profile.",
                5000,
            )
            return
        if self._profile_path is not None:
            start_path = self._profile_path
        else:
            directory = profiles_dir()
            directory.mkdir(parents=True, exist_ok=True)
            start_path = directory / "lighting_profile.json"
        start = str(start_path)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save native lighting profile",
            start,
            "Lighting profile (*.json);;All files (*)",
        )
        if path:
            destination = Path(path)
            if destination.suffix.lower() != ".json":
                destination = destination.with_suffix(".json")
            self._write_profile(destination)

    def _write_profile(self, path: Path) -> None:
        assert self._profile is not None
        try:
            save_profile(path, self._profile)
        except Exception as exc:
            QMessageBox.critical(self, "Save profile failed", str(exc))
            return
        self._profile_path = path
        self._profile_dirty = False
        self._logger.event("PROFILE_SAVE", path=path, profile=self._profile.name)
        self.statusBar().showMessage(f"Saved {path}", 4000)
        self._update_profile_buttons()

    def _update_profile_buttons(self) -> None:
        loaded = self._profile is not None
        self.save_as_button.setEnabled(loaded)
        self.save_button.setEnabled(loaded and self._profile_dirty)
        if not loaded:
            self.profile_name.setText("No profile loaded")
            self.setWindowTitle(APP_TITLE)
            return
        marker = " *" if self._profile_dirty else ""
        self.profile_name.setText(f"{self._profile.name}{marker}")
        self.setWindowTitle(f"{APP_TITLE} — {self._profile.name}{marker}")

    def _refresh_table(self, selected_rows: Iterable[int] | None = None) -> None:
        if self._profile is None:
            self.table.setRowCount(0)
            self.rule_count.setText("0 rules")
            if hasattr(self, "rules_empty_hint"):
                self.rules_empty_hint.setVisible(True)
            return
        if selected_rows is None:
            selected_rows = self.selected_rows()
        selected = tuple(
            row for row in selected_rows
            if 0 <= row < len(self._profile.rules)
        )

        self._table_loading = True
        self.table.blockSignals(True)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(self._profile.rules))
            if hasattr(self, "rules_empty_hint"):
                self.rules_empty_hint.setVisible(not bool(self._profile.rules))
            for row, rule in enumerate(self._profile.rules):
                values = (
                    str(row + 1),
                    " + ".join(rule.targets),
                    rule.source_type,
                    rule.effect,
                    rule.name or "",
                    "Enabled" if rule.enabled else "Disabled",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsDragEnabled
                    )
                    if not rule.enabled:
                        item.setForeground(QBrush(QColor(TEXT_TERTIARY)))
                    self.table.setItem(row, column, item)
                palette_widget = PaletteStripWidget(rule.colours)
                palette_widget.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                    True,
                )
                palette_widget.setEnabled(rule.enabled)
                self.table.setCellWidget(row, 6, palette_widget)
            self.rule_count.setText(
                f"{len(self._profile.rules)} rule"
                + ("" if len(self._profile.rules) == 1 else "s")
            )
            self.table.clearSelection()
            selection_model = self.table.selectionModel()
            for row in selected:
                index = self.table.model().index(row, 0)
                selection_model.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        finally:
            self.table.blockSignals(False)
            self._table_loading = False

    def selected_rows(self) -> tuple[int, ...]:
        return tuple(
            sorted(index.row() for index in self.table.selectionModel().selectedRows())
        )

    def _select_rows(self, rows: Iterable[int]) -> None:
        rows = tuple(sorted(set(rows)))
        self.table.blockSignals(True)
        try:
            self.table.clearSelection()
            selection_model = self.table.selectionModel()
            for row in rows:
                if 0 <= row < self.table.rowCount():
                    index = self.table.model().index(row, 0)
                    selection_model.select(
                        index,
                        QItemSelectionModel.SelectionFlag.Select
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
            if rows:
                self.table.setCurrentCell(rows[-1], 0)
        finally:
            self.table.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        if self._table_loading or self._profile is None:
            return
        rows = self.selected_rows()
        self._hide_delete_confirmation()

        if self._editor_dirty:
            allowed = (
                not self._new_rule_draft
                and self._editing_row is not None
                and rows == (self._editing_row,)
            )
            if not allowed:
                self.table.blockSignals(True)
                try:
                    self.table.clearSelection()
                    if self._editing_row is not None:
                        index = self.table.model().index(self._editing_row, 0)
                        self.table.selectionModel().select(
                            index,
                            QItemSelectionModel.SelectionFlag.Select
                            | QItemSelectionModel.SelectionFlag.Rows,
                        )
                        self.table.setCurrentCell(self._editing_row, 0)
                finally:
                    self.table.blockSignals(False)
                self.statusBar().showMessage(
                    "Apply or cancel the current draft before selecting another rule.",
                    4500,
                )
                return

        self._logger.event("RULE_SELECTION", rows=[row + 1 for row in rows])
        self._update_rule_action_state()

        if len(rows) == 1:
            self._load_rule_into_editor(rows[0])
        elif len(rows) > 1:
            self._show_multi_selection(rows)
        else:
            self._clear_editor()

    def _update_rule_action_state(self) -> None:
        count = len(self.selected_rows())
        self.duplicate_button.setEnabled(count > 0)
        self.delete_button.setEnabled(count > 0)
        self.move_up_button.setEnabled(count > 0)
        self.move_down_button.setEnabled(count > 0)
        self.copy_action.setEnabled(count > 0)
        self.cut_action.setEnabled(count > 0)
        self.paste_action.setEnabled(bool(self._clipboard))

    def add_rule(self) -> None:
        if self._profile is None:
            return
        if not self._can_leave_current_draft():
            return
        self.table.clearSelection()
        self._new_rule_draft = True
        self._editing_row = None
        self._logger.event("RULE_ADD_DRAFT")
        self._load_draft(
            LightingRule(
                name="New rule",
                conditions=(ArgumentCondition("Docked", "Equal", True),),
                colour=(255, 255, 255),
                target="KEYBOARD",
                effect=STATIC,
                effect_parameters=default_parameters_for_effect(STATIC),
            ),
            title="New rule",
        )
        self._set_editor_dirty(False)
        self.apply_rule_button.setText("Add rule")

    def duplicate_rules(self) -> None:
        if self._profile is None:
            return
        rows = self.selected_rows()
        if not rows or not self._can_leave_current_draft():
            return
        rules = list(self._profile.rules)
        insertion = rows[-1] + 1
        copies = [
            replace(rule, name=(f"{rule.name} copy" if rule.name else "Copy"))
            for rule in (rules[row] for row in rows)
        ]
        rules[insertion:insertion] = copies
        self._set_profile_rules(rules)
        new_rows = tuple(range(insertion, insertion + len(copies)))
        self._logger.event(
            "RULE_DUPLICATE",
            source_rows=[row + 1 for row in rows],
            new_rows=[row + 1 for row in new_rows],
        )
        self._refresh_table(new_rows)
        self._selection_changed()

    def request_delete_rules(self) -> None:
        rows = self.selected_rows()
        if not rows or not self._can_leave_current_draft():
            return
        self.delete_confirm_label.setText(
            f"Delete {len(rows)} selected rule"
            + ("" if len(rows) == 1 else "s")
            + "?"
        )
        self.delete_confirm.setVisible(True)

    def _hide_delete_confirmation(self) -> None:
        self.delete_confirm.setVisible(False)

    def confirm_delete_rules(self) -> None:
        if self._profile is None:
            return
        rows = self.selected_rows()
        if not rows:
            self._hide_delete_confirmation()
            return
        rules = list(self._profile.rules)
        for row in reversed(rows):
            del rules[row]
        self._set_profile_rules(rules)
        self._logger.event("RULE_DELETE", rows=[row + 1 for row in rows])
        self._hide_delete_confirmation()
        if rules:
            target = min(rows[0], len(rules) - 1)
            self._refresh_table((target,))
            self._selection_changed()
        else:
            self._refresh_table(())
            self._clear_editor()

    def copy_rules(self) -> None:
        if self._profile is None:
            return
        rows = self.selected_rows()
        if not rows:
            return
        self._clipboard = tuple(self._profile.rules[row] for row in rows)
        self.paste_action.setEnabled(True)
        self._logger.event("RULE_COPY", rows=[row + 1 for row in rows])

    def cut_rules(self) -> None:
        if self._profile is None:
            return
        rows = self.selected_rows()
        if not rows or not self._can_leave_current_draft():
            return
        self._clipboard = tuple(self._profile.rules[row] for row in rows)
        rules = list(self._profile.rules)
        for row in reversed(rows):
            del rules[row]
        self._set_profile_rules(rules)
        self._logger.event("RULE_CUT", rows=[row + 1 for row in rows])
        self._refresh_table(())
        if rules:
            self._select_rows((min(rows[0], len(rules) - 1),))
        else:
            self._clear_editor()

    def paste_rules(self) -> None:
        if self._profile is None or not self._clipboard:
            return
        if not self._can_leave_current_draft():
            return
        rows = self.selected_rows()
        insertion = rows[-1] + 1 if rows else len(self._profile.rules)
        rules = list(self._profile.rules)
        rules[insertion:insertion] = list(self._clipboard)
        self._set_profile_rules(rules)
        pasted = tuple(range(insertion, insertion + len(self._clipboard)))
        self._logger.event("RULE_PASTE", rows=[row + 1 for row in pasted])
        self._refresh_table(pasted)
        self._selection_changed()

    def move_selected_rules(self, direction: int) -> None:
        if self._profile is None or direction not in (-1, 1):
            return
        rows = list(self.selected_rows())
        if not rows or not self._can_leave_current_draft():
            return

        rules = list(self._profile.rules)
        selected = set(rows)
        if direction < 0:
            for row in rows:
                if row > 0 and row - 1 not in selected:
                    rules[row - 1], rules[row] = rules[row], rules[row - 1]
                    selected.remove(row)
                    selected.add(row - 1)
        else:
            for row in reversed(rows):
                if row < len(rules) - 1 and row + 1 not in selected:
                    rules[row + 1], rules[row] = rules[row], rules[row + 1]
                    selected.remove(row)
                    selected.add(row + 1)

        new_rows = tuple(sorted(selected))
        if new_rows == tuple(rows):
            return
        self._set_profile_rules(rules)
        self._logger.event(
            "RULE_MOVE",
            direction="up" if direction < 0 else "down",
            from_rows=[row + 1 for row in rows],
            to_rows=[row + 1 for row in new_rows],
        )
        self._refresh_table(new_rows)
        self._selection_changed()

    def drag_move_rule(self, source: int, target: int) -> None:
        if self._profile is None or source == target:
            return
        if self._editor_dirty:
            self.statusBar().showMessage(
                "Apply or cancel the current draft before reordering rules.",
                4500,
            )
            return
        rules = list(self._profile.rules)
        rule = rules.pop(source)
        rules.insert(target, rule)
        self._set_profile_rules(rules)
        self._logger.event(
            "RULE_DRAG_MOVE",
            from_row=source + 1,
            to_row=target + 1,
        )
        self._refresh_table((target,))
        self._selection_changed()

    def _set_profile_rules(self, rules: Iterable[LightingRule]) -> None:
        assert self._profile is not None
        self._profile = replace(self._profile, rules=tuple(rules))
        self._profile_dirty = True
        self._update_profile_buttons()

    def _set_empty_state(self) -> None:
        self.table.setRowCount(0)
        self.rule_count.setText("0 rules")
        if hasattr(self, "rules_empty_hint"):
            self.rules_empty_hint.setVisible(True)
        self._clear_editor()
        self._update_rule_action_state()
        self._update_profile_buttons()

    def _clear_editor(self) -> None:
        self._editing_row = None
        self._new_rule_draft = False
        self._set_editor_dirty(False)
        self.editor_title.setText("Rule editor")
        self._set_editor_enabled(False)

    def _show_multi_selection(self, rows: tuple[int, ...]) -> None:
        self._editing_row = None
        self._new_rule_draft = False
        self._set_editor_dirty(False)
        self.editor_title.setText(f"{len(rows)} rules selected")
        self.draft_state.setText("Use the rule toolbar for bulk operations.")
        self._set_editor_enabled(False)

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self.comment_edit,
            self.enabled_check,
            self.source_type_combo,
            self.source_stack,
            self.target_combo,
            self.effect_combo,
            self.effect_controls,
            self.palette_editor,
            self.preview_button,
            self.cancel_rule_button,
            self.apply_rule_button,
        ):
            widget.setEnabled(enabled)

    def _load_rule_into_editor(self, row: int) -> None:
        assert self._profile is not None
        rule = self._profile.rules[row]
        self._editing_row = row
        self._new_rule_draft = False
        self._load_draft(rule, title=f"Rule {row + 1}")
        self._set_editor_dirty(False)
        self.apply_rule_button.setText("Apply changes")

    def _load_draft(self, rule: LightingRule, *, title: str) -> None:
        self._set_editor_enabled(True)
        self._loading_editor = True
        try:
            self.editor_title.setText(title)
            self.comment_edit.setText(rule.name or "")
            self.enabled_check.setChecked(rule.enabled)

            self.source_type_combo.setCurrentText(rule.source_type)
            self.source_stack.setCurrentIndex(
                {"Argument": 0, "Keyboard": 1, "Button": 2, "Axis": 3}[rule.source_type]
            )
            self._load_source(rule)

            self._set_target_text(rule.target)

            self.effect_combo.setCurrentText(rule.effect)
            self.palette_editor.set_effect(rule.effect)
            self.effect_controls.set_effect(rule.effect, rule.effect_parameters)
            self.palette_editor.set_palette(rule.colours)
            self._update_test_output_state()
        finally:
            self._loading_editor = False

    def _load_source(self, rule: LightingRule) -> None:
        self._clear_argument_rows()
        if rule.source_type == "Argument":
            for condition in rule.conditions:
                self._add_argument_condition(condition, mark_dirty=False)
        elif rule.source_type == "Keyboard":
            assert rule.keyboard is not None
            self.keyboard_keys.setText(" + ".join(rule.keyboard.keys))
        elif rule.source_type == "Button":
            assert rule.button is not None
            self._populate_device_combo(self.button_device, rule.button.device)
            self.button_number.setValue(rule.button.button)
            self.button_state.setCurrentText(rule.button.state)
        elif rule.source_type == "Axis":
            assert rule.axis is not None
            self._populate_device_combo(self.axis_device, rule.axis.device)
            self.axis_number.setValue(rule.axis.axis)
            self.axis_operator.setCurrentText(rule.axis.operator)
            self.axis_value.setValue(float(rule.axis.value))
            self.axis_secondary.setValue(
                0.0 if rule.axis.secondary_value is None
                else float(rule.axis.secondary_value)
            )
            self._axis_operator_changed(rule.axis.operator)

    def _set_editor_dirty(self, dirty: bool) -> None:
        self._editor_dirty = dirty
        if dirty:
            self.draft_state.setText("Unsaved rule changes")
        elif self._new_rule_draft:
            self.draft_state.setText("New rule draft")
        else:
            self.draft_state.setText("")

        actionable = bool(self._new_rule_draft or dirty)
        if hasattr(self, "apply_rule_button"):
            self.apply_rule_button.setEnabled(actionable)
        if hasattr(self, "cancel_rule_button"):
            self.cancel_rule_button.setEnabled(actionable)

    def _editor_changed(self, *_args) -> None:
        if self._loading_editor:
            return
        self._set_editor_dirty(True)

    def _can_leave_current_draft(self) -> bool:
        if not self._editor_dirty:
            return True
        self.statusBar().showMessage(
            "Apply or cancel the current rule draft first.",
            4500,
        )
        return False

    def cancel_rule_changes(self) -> None:
        self._stop_preview(request_log=True)
        if self._profile is None:
            return
        if self._new_rule_draft:
            self._new_rule_draft = False
            self._set_editor_dirty(False)
            rows = self.selected_rows()
            if rows:
                self._selection_changed()
            elif self._profile.rules:
                self._select_rows((0,))
            else:
                self._clear_editor()
            self._logger.event("RULE_DRAFT_CANCEL", kind="new")
            return
        if self._editing_row is not None:
            row = self._editing_row
            self._load_rule_into_editor(row)
            self._logger.event("RULE_DRAFT_CANCEL", row=row + 1)

    def apply_rule(self) -> None:
        if self._profile is None:
            return
        try:
            rule = self._build_rule_from_editor()
            validate_effect_configuration(
                rule.effect,
                rule.colours,
                rule.effect_parameters,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Rule is incomplete", str(exc))
            return

        rules = list(self._profile.rules)
        if self._new_rule_draft:
            row = len(rules)
            rules.append(rule)
            action = "RULE_ADD"
        elif self._editing_row is not None:
            row = self._editing_row
            rules[row] = rule
            action = "RULE_APPLY"
        else:
            return

        self._set_profile_rules(rules)
        self._new_rule_draft = False
        self._editing_row = row
        self._set_editor_dirty(False)
        self._logger.event(
            action,
            row=row + 1,
            source_type=rule.source_type,
            target=rule.target,
            effect=rule.effect,
            comment=rule.name,
        )
        self._refresh_table((row,))
        self._selection_changed()

    def _build_rule_from_editor(self) -> LightingRule:
        source_type = self.source_type_combo.currentText()
        source_fields: dict[str, object] = {"conditions": ()}

        if source_type == "Argument":
            if not self._argument_rows:
                raise ValueError("Argument rule requires at least one condition")
            source_fields = {
                "conditions": tuple(row.condition() for row in self._argument_rows)
            }
        elif source_type == "Keyboard":
            source_fields = {
                "conditions": (),
                "keyboard": KeyboardCombination(
                    _parse_keyboard_keys(self.keyboard_keys.text())
                ),
            }
        elif source_type == "Button":
            device = self.button_device.currentData()
            if not isinstance(device, str) or not device:
                raise ValueError("Button rule requires a selected HID device")
            source_fields = {
                "conditions": (),
                "button": ButtonSource(
                    device,
                    self.button_number.value(),
                    self.button_state.currentText(),
                ),
            }
        elif source_type == "Axis":
            device = self.axis_device.currentData()
            if not isinstance(device, str) or not device:
                raise ValueError("Axis rule requires a selected HID device")
            secondary = (
                self.axis_secondary.value()
                if self.axis_operator.currentText() == AXIS_BETWEEN_OPERATOR
                else None
            )
            source_fields = {
                "conditions": (),
                "axis": AxisSource(
                    device,
                    self.axis_number.value(),
                    self.axis_operator.currentText(),
                    self.axis_value.value(),
                    secondary,
                ),
            }
        else:
            raise ValueError(f"Unsupported rule type {source_type!r}")

        palette = self.palette_editor.palette()
        parameters = self.effect_controls.parameters()
        comment = self.comment_edit.text().strip() or None
        target = self.target_combo.currentText().strip().upper()
        if not target:
            raise ValueError("Target must not be empty")

        return LightingRule(
            name=comment,
            enabled=self.enabled_check.isChecked(),
            colour=palette[0],
            colours=palette,
            effect=self.effect_combo.currentText(),
            effect_parameters=parameters,
            target=target,
            **source_fields,
        )

    def _source_type_changed(self, source_type: str) -> None:
        index = {"Argument": 0, "Keyboard": 1, "Button": 2, "Axis": 3}.get(
            source_type, 0
        )
        self.source_stack.setCurrentIndex(index)
        if self._loading_editor:
            return
        if source_type == "Argument" and not self._argument_rows:
            self._add_argument_condition(mark_dirty=False)
        if source_type == "Button":
            self._populate_device_combo(self.button_device, self.button_device.currentData())
        elif source_type == "Axis":
            self._populate_device_combo(self.axis_device, self.axis_device.currentData())
        self._logger.event("SOURCE_TYPE", value=source_type)
        self._set_editor_dirty(True)

    def _add_argument_condition(
        self,
        condition: ArgumentCondition | None = None,
        *,
        mark_dirty: bool = True,
    ) -> None:
        row = ArgumentConditionRow(condition)
        row.changed.connect(self._editor_changed)
        row.removeRequested.connect(self._remove_argument_condition)
        self._argument_rows.append(row)
        self.argument_rows_layout.addWidget(row)
        if mark_dirty and not self._loading_editor:
            self._set_editor_dirty(True)

    def _remove_argument_condition(self, row: ArgumentConditionRow) -> None:
        if row not in self._argument_rows:
            return
        self._argument_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._set_editor_dirty(True)

    def _clear_argument_rows(self) -> None:
        for row in self._argument_rows:
            row.setParent(None)
            row.deleteLater()
        self._argument_rows = []

    def refresh_hid_devices(self, *_args, log: bool = True) -> None:
        try:
            devices = enumerate_hid_devices()
        except Exception as exc:
            devices = ()
            if log:
                self.statusBar().showMessage(f"HID discovery failed: {exc}", 5000)
                self._logger.event("HID_DISCOVERY_ERROR", error=str(exc))
        self._hid_devices = devices

        current_button = self.button_device.currentData() if hasattr(self, "button_device") else None
        current_axis = self.axis_device.currentData() if hasattr(self, "axis_device") else None
        if hasattr(self, "button_device"):
            self._populate_device_combo(self.button_device, current_button)
        if hasattr(self, "axis_device"):
            self._populate_device_combo(self.axis_device, current_axis)
        if log:
            self._logger.event(
                "HID_DISCOVERY",
                count=len(devices),
                devices=[device.label for device in devices],
            )

    def _populate_device_combo(
        self,
        combo: QComboBox,
        selected_device_id: object = None,
    ) -> None:
        selected = selected_device_id if isinstance(selected_device_id, str) else None
        previous_loading = self._loading_editor
        self._loading_editor = True
        try:
            combo.clear()
            for device in self._hid_devices:
                combo.addItem(device.label, device.device_id)
            if selected:
                index = next(
                    (
                        i for i in range(combo.count())
                        if combo.itemData(i) == selected
                    ),
                    -1,
                )
                if index < 0:
                    label = selected
                    if len(label) > 90:
                        label = label[:40] + " … " + label[-40:]
                    combo.addItem(f"Stored device — {label}", selected)
                    index = combo.count() - 1
                combo.setCurrentIndex(index)
            elif combo.count():
                combo.setCurrentIndex(0)
            else:
                combo.setPlaceholderText("No HID devices found")
        finally:
            self._loading_editor = previous_loading

    def start_button_detection(self) -> None:
        if self._button_capture_thread is not None and self._button_capture_thread.is_alive():
            return
        self.detect_button.setEnabled(False)
        self.detect_button.setText("Waiting…")
        self.button_capture_status.setText("Press one physical button. Device and button are captured together.")
        self._logger.event("BUTTON_DETECT_START")

        def worker() -> None:
            try:
                device, button = capture_next_button(timeout_seconds=10.0)
            except Exception as exc:
                self._capture_signals.error.emit(str(exc))
                return
            self._capture_signals.buttonDetected.emit(device, button)

        self._button_capture_thread = threading.Thread(
            target=worker,
            name="ui-button-binding-capture",
            daemon=True,
        )
        self._button_capture_thread.start()

    def _button_detected(self, device: HidDevice, button: int) -> None:
        if all(existing.device_id != device.device_id for existing in self._hid_devices):
            self._hid_devices = (*self._hid_devices, device)
        self._populate_device_combo(self.button_device, device.device_id)
        self.button_number.setValue(button)
        self.detect_button.setEnabled(True)
        self.detect_button.setText("Detect button…")
        self.button_capture_status.setText(
            f"Detected {device.label} — Button {button}"
        )
        self._set_editor_dirty(True)
        self._logger.event(
            "BUTTON_DETECTED",
            device=device.label,
            device_id=device.device_id,
            button=button,
        )

    def _button_detect_error(self, message: str) -> None:
        self.detect_button.setEnabled(True)
        self.detect_button.setText("Detect button…")
        self.button_capture_status.setText(message)
        self._logger.event("BUTTON_DETECT_ERROR", error=message)

    def _axis_operator_changed(self, operator: str) -> None:
        between = operator == AXIS_BETWEEN_OPERATOR
        self.axis_secondary_label.setVisible(between)
        self.axis_secondary.setVisible(between)
        self._editor_changed()

    def _set_target_text(self, target: str) -> None:
        index = self.target_combo.findText(target, Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.target_combo.setCurrentIndex(index)
        else:
            self.target_combo.setCurrentText(target)
        self._update_test_output_state()

    def _target_changed(self, value: str) -> None:
        target = value.strip().upper()
        self.target_scope.setText(
            "Whole device" if target in {"KEYBOARD", "MOUSE"} else "Logical target"
        )
        self._update_test_output_state()
        if not self._loading_editor:
            self._logger.event("TARGET", value=target)
            self._set_editor_dirty(True)

    def _effect_changed(self, effect: str) -> None:
        self.palette_editor.set_effect(effect)
        if self._loading_editor:
            return

        palette = self.palette_editor.palette()
        if effect in {STATIC, PULSE}:
            palette = (palette[0],)
        elif effect in {FLASH, BREATH} and len(palette) < 2:
            palette = (palette[0], palette[0])

        self.palette_editor.set_palette(palette)
        self.effect_controls.set_effect(
            effect,
            default_parameters_for_effect(effect),
        )
        self._logger.event("EFFECT", value=effect)
        self._set_editor_dirty(True)

    def _update_test_output_state(self) -> None:
        if not hasattr(self, "preview_button"):
            return
        target = self.target_combo.currentText().strip().upper()
        usable = target in {"KEYBOARD", "MOUSE"}
        if self._preview_thread is None or not self._preview_thread.is_alive():
            self.preview_button.setEnabled(usable and self.comment_edit.isEnabled())
        self.preview_button.setToolTip(
            "Preview the current unsaved effect snapshot on this physical target."
            if usable
            else "This base Preview output path does not support the selected target."
        )

    def _preview_intent(self):
        target = self.target_combo.currentText().strip().upper()
        if target not in {"KEYBOARD", "MOUSE"}:
            raise ValueError("Preview output does not support the selected target on this path.")
        palette = self.palette_editor.palette()
        parameters = self.effect_controls.parameters()
        effect = self.effect_combo.currentText()
        validate_effect_configuration(effect, palette, parameters)

        preview_rule = LightingRule(
            name="UI preview snapshot",
            conditions=(ArgumentCondition("Docked", "Equal", True),),
            colour=palette[0],
            colours=palette,
            effect=effect,
            effect_parameters=parameters,
            target=target,
        )
        return intent_from_rule(preview_rule)

    def _toggle_preview(self) -> None:
        if self._preview_thread is not None and self._preview_thread.is_alive():
            self._stop_preview(request_log=True)
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
        self.preview_status.setText(
            f"Testing {intent.effect} on {intent.target} — snapshot"
        )
        self._logger.event(
            "PREVIEW_START",
            target=intent.target,
            effect=intent.effect,
            colours=intent.colours,
            brightness=intent.brightness,
        )

        def worker() -> None:
            error: Exception | None = None
            try:
                run_effect_preview(intent, intent.target, stop_event)
            except Exception as exc:
                error = exc
            self._preview_signals.finished.emit(error)

        self._preview_thread = threading.Thread(
            target=worker,
            name="ui-chroma-preview",
            daemon=True,
        )
        self._preview_thread.start()

    def _stop_preview(self, *, request_log: bool) -> None:
        if self._preview_stop_event is not None:
            self._preview_stop_event.set()
        if request_log and self._preview_thread is not None:
            self._logger.event("PREVIEW_STOP_REQUEST")

    def _preview_finished(self, error: object) -> None:
        self._preview_thread = None
        self._preview_stop_event = None
        self.preview_button.setText("Preview output")
        self.preview_status.setText("")
        self._update_test_output_state()
        if isinstance(error, Exception):
            self._logger.event("PREVIEW_ERROR", error=str(error))
            QMessageBox.warning(self, "Preview output stopped", str(error))
        else:
            self._logger.event("PREVIEW_STOPPED")

    def _install_shortcuts(self) -> None:
        self._shortcuts: list[QShortcut] = []
        for sequence, handler in (
            (QKeySequence.StandardKey.Copy, self.copy_rules),
            (QKeySequence.StandardKey.Cut, self.cut_rules),
            (QKeySequence.StandardKey.Paste, self.paste_rules),
            (QKeySequence(Qt.Key.Key_Delete), self.request_delete_rules),
        ):
            shortcut = QShortcut(sequence, self.table)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_preview(request_log=False)
        self._settings.setValue(SPLITTER_KEY, self.splitter.saveState())
        self._settings.sync()
        self._logger.event(
            "SESSION_END",
            profile=(self._profile.name if self._profile else None),
            profile_dirty=self._profile_dirty,
            draft_dirty=self._editor_dirty,
        )
        super().closeEvent(event)



# Stable compatibility namespace for adapters/tests that intentionally target
# the pre-final editor layer. Snapshot now so later final-UI names cannot
# overwrite base constants such as NORMAL_ARGUMENTS.
from types import SimpleNamespace as _SimpleNamespace
base = _SimpleNamespace(**{
    name: value for name, value in globals().items()
    if not name.startswith("__") and name != "base"
})
base.MainWindow = BaseMainWindow


APP_TITLE = base.APP_TITLE
SPLITTER_KEY = base.SPLITTER_KEY
ELITE_STATUS_PATH_KEY = "elite/status_json_path"
# Consolidated operator-facing layer formerly owned by lighting_ui_app.py.
# It remains a distinct class boundary temporarily so this checkpoint changes
# ownership only, not method-resolution order.
MODIFIER_ORDER = ("CTRL", "SHIFT", "ALT", "META")
ADVANCED_ARGUMENTS = ("Pips", "RawFlags", "RawFlags2")
NORMAL_ARGUMENTS = tuple(
    sorted(
        (
            name
            for name in base.NORMAL_ARGUMENTS
            if name not in ADVANCED_ARGUMENTS
        ),
        key=str.casefold,
    )
)


class HumanArgumentConditionRow(QFrame):
    """Compact typed Argument row; domain operators remain unchanged underneath."""

    changed = Signal()
    removeRequested = Signal(object)

    def __init__(
        self,
        condition: ArgumentCondition | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._loading = False
        self._stored_bool_operator = "Equal"

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(7)

        self.source = QComboBox()
        self.source.setEditable(True)
        self.source.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.source.setMinimumWidth(210)
        self.source.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._populate_sources()
        completer = self.source.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.source.currentTextChanged.connect(self._source_changed)
        row.addWidget(self.source, 3)

        self.bool_relation = QLabel("is")
        self.bool_relation.setMinimumWidth(30)
        row.addWidget(self.bool_relation)

        self.operator = QComboBox()
        self.operator.setMinimumWidth(145)
        self.operator.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.operator.currentTextChanged.connect(lambda _text: self._emit_changed())
        row.addWidget(self.operator, 2)

        self.value_stack = QStackedWidget()
        self.value_stack.setFixedHeight(38)
        self.value_stack.setMinimumWidth(150)
        self.value_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.bool_value = QComboBox()
        self.bool_value.addItems(("True", "False"))
        self.bool_value.currentTextChanged.connect(lambda _text: self._emit_changed())
        self.value_stack.addWidget(self.bool_value)

        self.numeric_value = QLineEdit()
        self.numeric_value.setValidator(QDoubleValidator(self.numeric_value))
        self.numeric_value.setPlaceholderText("Number")
        self.numeric_value.textChanged.connect(lambda _text: self._emit_changed())
        self.value_stack.addWidget(self.numeric_value)

        self.text_value = QLineEdit()
        self.text_value.setPlaceholderText("Value")
        self.text_value.textChanged.connect(lambda _text: self._emit_changed())
        self.value_stack.addWidget(self.text_value)
        row.addWidget(self.value_stack, 2)

        remove = QPushButton("Remove")
        remove.setToolTip("Remove this condition")
        remove.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        row.addWidget(remove)

        if condition is None:
            condition = ArgumentCondition("Docked", "Equal", True)
        self.set_condition(condition)

    def _populate_sources(self) -> None:
        self.source.clear()
        for name in NORMAL_ARGUMENTS:
            self.source.addItem(name)
        self.source.insertSeparator(self.source.count())
        for name in ADVANCED_ARGUMENTS:
            self.source.addItem(name)

    @staticmethod
    def _source_kind(name: str) -> str:
        if name in base.BOOLEAN_ARGUMENTS:
            return "bool"
        if name in base.NUMERIC_ARGUMENTS:
            return "numeric"
        return "text"

    def _source_changed(self, name: str) -> None:
        self._loading = True
        try:
            kind = self._source_kind(name)
            previous = self.operator.currentText()
            self.operator.clear()
            if kind == "numeric":
                self.operator.addItems(base.SUPPORTED_OPERATORS)
            elif kind == "text":
                self.operator.addItems(("Equal", "Not Equal"))

            if kind != "bool":
                index = self.operator.findText(previous)
                if index >= 0:
                    self.operator.setCurrentIndex(index)

            self.bool_relation.setVisible(kind == "bool")
            self.operator.setVisible(kind != "bool")
            self.value_stack.setCurrentIndex(
                0 if kind == "bool" else 1 if kind == "numeric" else 2
            )
        finally:
            self._loading = False
        self._emit_changed()

    def set_condition(self, condition: ArgumentCondition) -> None:
        self._loading = True
        try:
            index = self.source.findText(condition.source, Qt.MatchFlag.MatchFixedString)
            if index < 0:
                self.source.addItem(condition.source)
                index = self.source.findText(condition.source, Qt.MatchFlag.MatchFixedString)
            self.source.setCurrentIndex(index)
            self._source_changed(condition.source)

            kind = self._source_kind(condition.source)
            if kind == "bool":
                # Native UI normalizes the redundant forms `Not Equal True` and
                # `Not Equal False` into the directly readable Boolean state.
                value = condition.value is True
                if condition.operator == "Not Equal":
                    value = not value
                self._stored_bool_operator = "Equal"
                self.bool_value.setCurrentText("True" if value else "False")
            else:
                op_index = self.operator.findText(condition.operator)
                if op_index < 0:
                    self.operator.addItem(condition.operator)
                    op_index = self.operator.findText(condition.operator)
                self.operator.setCurrentIndex(op_index)
                if kind == "numeric":
                    self.numeric_value.setText(str(condition.value))
                else:
                    self.text_value.setText(
                        "" if condition.value is None else str(condition.value)
                    )
        finally:
            self._loading = False

    def condition(self) -> ArgumentCondition:
        source = self.source.currentText().strip()
        kind = self._source_kind(source)
        if not source:
            raise ValueError("Argument condition requires a field")

        if kind == "bool":
            return ArgumentCondition(
                source,
                "Equal",
                self.bool_value.currentText() == "True",
            )

        operator = self.operator.currentText().strip()
        if kind == "numeric":
            text = self.numeric_value.text().strip()
            if not text:
                raise ValueError(f"{source} requires a numeric value")
            try:
                value: object = int(text, 10)
            except ValueError:
                value = float(text)
        else:
            value = self.text_value.text()
        return ArgumentCondition(source, operator, value)

    def _emit_changed(self) -> None:
        if not self._loading:
            self.changed.emit()


class PersistentPaletteActionsEditor(EffectPaletteEditor):
    """Keep palette actions visible so cardinality is discoverable, not mysterious."""

    def _update_cardinality_controls(self) -> None:
        super()._update_cardinality_controls()
        self.add_button.setVisible(True)
        self.remove_button.setVisible(True)
        if self._effect in {STATIC, PULSE}:
            self.add_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            message = f"{self._effect} uses exactly one colour."
            self.add_button.setToolTip(message)
            self.remove_button.setToolTip(message)


class KeyboardCaptureDialog(QDialog):
    """Capture an arbitrary simultaneous key set; saving remains explicit."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record key combination")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._pressed: dict[int, str] = {}
        self._press_order: list[int] = []
        self._captured: tuple[str, ...] = ()

        root = QVBoxLayout(self)
        instruction = QLabel(
            "Hold the complete key combination you want to use. Nothing is saved "
            "until you choose Use combination."
        )
        instruction.setWordWrap(True)
        root.addWidget(instruction)

        self.preview = QLabel("Waiting for keys…")
        self.preview.setStyleSheet("font-size: 12pt; font-weight: 600;")
        self.preview.setMinimumHeight(42)
        root.addWidget(self.preview)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.use_button = QPushButton("Use combination")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.accept)
        buttons.addWidget(self.use_button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        root.addLayout(buttons)

    @staticmethod
    def _token_for_key(key: int) -> str:
        mapping = {
            int(Qt.Key.Key_Control): "CTRL",
            int(Qt.Key.Key_Shift): "SHIFT",
            int(Qt.Key.Key_Alt): "ALT",
            int(Qt.Key.Key_Meta): "META",
        }
        if key in mapping:
            return mapping[key]
        return QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText).upper()

    @staticmethod
    def _ordered(tokens: Iterable[str]) -> tuple[str, ...]:
        values = list(dict.fromkeys(token for token in tokens if token))
        modifiers = [name for name in MODIFIER_ORDER if name in values]
        others = [name for name in values if name not in MODIFIER_ORDER]
        return tuple((*modifiers, *others))

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.grabKeyboard()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.releaseKeyboard()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.isAutoRepeat():
            event.accept()
            return
        key = int(event.key())
        token = self._token_for_key(key)
        if not token:
            event.accept()
            return
        if key not in self._pressed:
            self._pressed[key] = token
            self._press_order.append(key)

        current = self._ordered(
            self._pressed[item]
            for item in self._press_order
            if item in self._pressed
        )
        if len(current) >= len(self._captured):
            self._captured = current
            self.preview.setText(" + ".join(current))
            self.use_button.setEnabled(bool(current))
        event.accept()

    def keyReleaseEvent(self, event) -> None:  # type: ignore[override]
        if not event.isAutoRepeat():
            self._pressed.pop(int(event.key()), None)
        event.accept()

    def combination(self) -> str:
        return " + ".join(self._captured)


class ButtonCandidateSignals(QWidget):
    candidate = Signal(object, int)
    failed = Signal(str)


class ButtonDetectDialog(QDialog):
    """Collect candidate physical button presses and require explicit confirmation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Detect button or switch")
        self.setModal(True)
        self.setMinimumSize(560, 330)
        self._stop = threading.Event()
        self._candidates: dict[tuple[str, int], tuple[HidDevice, int]] = {}
        self._selected: tuple[HidDevice, int] | None = None
        self._signals = ButtonCandidateSignals()
        self._signals.candidate.connect(self._candidate_detected)
        self._signals.failed.connect(self._capture_failed)

        root = QVBoxLayout(self)
        instruction = QLabel(
            "Operate the desired cockpit control through its normal positions. "
            "Detected inputs appear below; detection never changes the rule by itself. "
            "Select the intended input and choose Use selected."
        )
        instruction.setWordWrap(True)
        root.addWidget(instruction)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(self.list, 1)

        self.status = QLabel("Listening for controller button presses…")
        self.status.setStyleSheet("color: #B8B8B8;")
        root.addWidget(self.status)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.use_button = QPushButton("Use selected")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._accept_selected)
        actions.addWidget(self.use_button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        root.addLayout(actions)

        self._thread = threading.Thread(
            target=self._worker,
            name="ui-button-candidate-capture",
            daemon=True,
        )
        self._thread.start()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                device, button = capture_next_button(timeout_seconds=1.25)
            except TimeoutError:
                continue
            except Exception as exc:
                self._signals.failed.emit(str(exc))
                return
            self._signals.candidate.emit(device, button)

    def _candidate_detected(self, device: HidDevice, button: int) -> None:
        key = (device.device_id, button)
        if key in self._candidates:
            return
        self._candidates[key] = (device, button)
        item = QListWidgetItem(f"{friendly_device_label(device)} — Button {button}")
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setToolTip(technical_device_description(device))
        self.list.addItem(item)
        self.status.setText(
            "Keep operating the control if it has more than one physical position."
        )

    def _capture_failed(self, message: str) -> None:
        self.status.setText(message)

    def _selection_changed(self) -> None:
        self.use_button.setEnabled(bool(self.list.selectedItems()))

    def _accept_selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        key = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected = self._candidates.get(tuple(key))
        if self._selected is not None:
            self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        self._stop.set()
        super().done(result)

    def selected_candidate(self) -> tuple[HidDevice, int] | None:
        return self._selected


def friendly_device_label(device: HidDevice, ordinal: int | None = None) -> str:
    if device.vendor_id == 0x3344 and device.product_id == 0x025A:
        base_label = "VIRPIL VPC Panel #2"
    elif device.vendor_id == 0x1234:
        base_label = "vJoy virtual joystick"
    elif device.vendor_id == 0x231D:
        base_label = f"VKB controller (PID {device.product_id:04X})"
    elif device.vendor_id == 0x4098:
        base_label = f"WinWing controller (PID {device.product_id:04X})"
    else:
        base_label = f"Controller (VID {device.vendor_id:04X}, PID {device.product_id:04X})"
    if ordinal is not None:
        return f"{base_label} #{ordinal}"
    return base_label


def technical_device_description(device: HidDevice) -> str:
    return (
        f"VID {device.vendor_id:04X} / PID {device.product_id:04X} / "
        f"usage {device.usage_page:04X}:{device.usage:04X}\n{device.device_id}"
    )


LOG_SETTING_KEY = "ui/session_logging_enabled"
NEW_PROFILE_NAME = "New profile"
PHYSICAL_TARGETS = {"KEYBOARD", "MOUSE", "CHROMALINK"}
PENDING_EFFECTS = tuple(
    effect
    for effect in (*CONTINUOUS_RULE_EFFECTS, *TRIGGERED_SCENE_EFFECTS)
    if effect not in base.KNOWN_EFFECTS
)
PENDING_EFFECT_FOREGROUND = QColor(STATUS_ERROR)

RULE_HELP = (
    "<b>Rule</b><br><br>"
    "Think of a rule as: <b>WHEN this happens → make these lights do this.</b><br><br>"
    "Example: <b>When Gear down is True → make the keyboard green.</b><br><br>"
    "Rules are checked from top to bottom. If two rules are true at the same time "
    "and both try to control the same lights, the lower rule in the list wins."
)

AXIS_HELP = (
    "<b>Axis</b><br><br>"
    "Move the stick, throttle, slider or rotary you want to use. "
    "EDL will identify the control for you.<br><br>"
    "After it is selected, move it and watch the percentage. "
    "If the percentage rises in the direction you care about, use <b>Above</b>. "
    "If it falls, use <b>Below</b>."
)

AXIS_DETECT_HELP = (
    "<b>Detect axis</b><br><br>"
    "Move the stick, throttle, slider or rotary you want to use. "
    "EDL ignores small movement and identifies the control you moved.<br><br>"
    "Then keep moving it and watch Current position to choose Above, Below or Between."
)

AXIS_OPERATOR_LABELS = {
    "Less": "Below",
    "More": "Above",
    "Equal": "Equal to",
    "Not Equal": "Not equal to",
    "More or Equal": "At or above",
    "Less or Equal": "At or below",
    "BETWEEN": "Between",
}


class OptionalUiSessionLogger:
    """Semantic UI logging that can be disabled from the application."""

    def __init__(self, log_dir: Path) -> None:
        self._settings = app_settings()
        self._log_dir = log_dir
        self._enabled = str(self._settings.value(LOG_SETTING_KEY, False)).lower() not in {
            "false",
            "0",
        }
        self._logger = RealUiSessionLogger(log_dir) if self._enabled else None
        self.path = self._logger.path if self._logger is not None else None
        self.session_number = self._logger.session_number if self._logger is not None else 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._settings.setValue(LOG_SETTING_KEY, enabled)
        self._settings.sync()
        if enabled and self._logger is None:
            self._logger = RealUiSessionLogger(self._log_dir)
            self.path = self._logger.path
            self.session_number = self._logger.session_number
        self._enabled = enabled

    def event(self, event: str, **data: Any):
        if not self._enabled or self._logger is None:
            return None
        return self._logger.event(event, **data)


class HelpButton(QToolButton):
    """Deliberate help affordance: hover temporarily, click to pin."""

    def __init__(self, help_html: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText("?")
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(22, 22)
        self.setToolTip(f"<qt>{help_html}</qt>")
        self.setToolTipDuration(30000)
        self.setStyleSheet(
            "QToolButton { font-weight:600; border:1px solid #666; border-radius:11px; padding:0; }"
            "QToolButton:hover { border-color:#AFAFAF; }"
        )
        self.clicked.connect(self.show_pinned_help)

    def set_help(self, help_html: str) -> None:
        self.setToolTip(f"<qt>{help_html}</qt>")

    def show_hover_help(self) -> None:
        if self.toolTip():
            QToolTip.showText(
                self.mapToGlobal(self.rect().bottomLeft()),
                self.toolTip(),
                self,
                self.rect(),
                30000,
            )

    def show_pinned_help(self) -> None:
        if self.toolTip():
            QToolTip.showText(
                self.mapToGlobal(self.rect().bottomLeft()),
                self.toolTip(),
                self,
                None,
                30000,
            )

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self.show_hover_help()
        super().enterEvent(event)


class ComboClickFilter(QObject):
    """Let an editable combo open from the visible field, not only its arrow."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self.combo = combo

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.MouseButtonPress:
            self.combo.showPopup()
        return False


class RunnerSignals(QObject):
    finished = Signal(object)


class AxisSignals(QObject):
    detected = Signal(object, int, str, float)
    failed = Signal(str)


class AxisOperatorCombo(QComboBox):
    """Human labels in the UI while preserving accepted engine operator strings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        for operator in base.SUPPORTED_AXIS_OPERATORS:
            self.addItem(AXIS_OPERATOR_LABELS.get(operator, operator), operator)

    def currentText(self) -> str:  # type: ignore[override]
        operator = self.currentData()
        return str(operator) if operator is not None else super().currentText()

    def setCurrentText(self, text: str) -> None:  # type: ignore[override]
        index = self.findData(text)
        if index < 0:
            index = self.findText(text)
        if index >= 0:
            self.setCurrentIndex(index)

    def display_text(self) -> str:
        return super().currentText()


class MultiTargetSelector(QWidget):
    """Small checkable target selector with GLOBAL kept mutually exclusive."""

    currentTextChanged = Signal(str)
    KNOWN_TARGETS = ("KEYBOARD", "MOUSE", "CHROMALINK", "GLOBAL")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._changing = False
        self._boxes: dict[str, QCheckBox] = {}
        self._order: list[str] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)
        self._layout.addStretch(1)
        for target in self.KNOWN_TARGETS:
            self._ensure_target(target)
        self.set_targets(("KEYBOARD",), emit=False)

    def _ensure_target(self, target: str) -> None:
        if target in self._boxes:
            return
        box = QCheckBox(target)
        box.toggled.connect(lambda checked, value=target: self._toggled(value, checked))
        self._boxes[target] = box
        self._order.append(target)
        self._layout.insertWidget(max(0, self._layout.count() - 1), box)

    def targets(self) -> tuple[str, ...]:
        return tuple(target for target in self._order if self._boxes[target].isChecked())

    def currentText(self) -> str:
        selected = self.targets()
        return selected[0] if selected else ""

    def display_text(self) -> str:
        return " + ".join(self.targets())

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
        if emit:
            self.currentTextChanged.emit(self.currentText())

    def _toggled(self, target: str, checked: bool) -> None:
        if self._changing:
            return
        if checked and target == "GLOBAL":
            self.set_targets(("GLOBAL",))
            return
        if checked and target != "GLOBAL" and self._boxes["GLOBAL"].isChecked():
            selected = tuple(
                value
                for value in self._order
                if value != "GLOBAL" and (value == target or self._boxes[value].isChecked())
            )
            self.set_targets(selected)
            return
        self.currentTextChanged.emit(self.currentText())

    # Compatibility surface retained for existing UI smoke tests/helpers.
    def count(self) -> int:
        return len(self._order)

    def itemText(self, index: int) -> str:
        return self._order[index]

    def findText(self, target: str, *_args) -> int:
        try:
            return self._order.index(target)
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:
        self.set_targets((self._order[index],))

    def setEditText(self, target: str) -> None:
        self.set_targets((target,))


class ButtonCandidateSignals(QObject):
    candidate = Signal(object, int)
    failed = Signal(str)
    ready = Signal()


class ButtonDetectDialog(QDialog):
    """Persistent baseline-first button/switch detector across cockpit devices."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Detect button or switch")
        self.setModal(True)
        self.setMinimumSize(580, 350)
        self._stop = threading.Event()
        self._selected = None
        self._candidates: dict[tuple[str, int], tuple[object, int]] = {}
        self._signals = ButtonCandidateSignals()
        self._signals.candidate.connect(self._candidate)
        self._signals.failed.connect(self._failed)
        self._signals.ready.connect(self._ready)

        root = QVBoxLayout(self)
        instruction = QLabel(
            "Move or press the cockpit control you want to use. EDL first learns the "
            "controls that are already on, then shows new button/switch movements below. "
            "Choose the correct one and confirm it."
        )
        instruction.setWordWrap(True)
        root.addWidget(instruction)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(
            lambda: self.use_button.setEnabled(bool(self.list.selectedItems()))
        )
        root.addWidget(self.list, 1)

        self.status = QLabel("Learning the current cockpit switch positions…")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.use_button = QPushButton("Use selected")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._accept_selected)
        actions.addWidget(self.use_button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        root.addLayout(actions)

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        try:
            iterator = iter_button_presses(self._stop, baseline_seconds=0.8)
            self._signals.ready.emit()
            for device, button in iterator:
                if self._stop.is_set():
                    break
                self._signals.candidate.emit(device, button)
        except Exception as exc:
            self._signals.failed.emit(str(exc))

    def _ready(self) -> None:
        self.status.setText("Listening. Move or press the cockpit control you want to use.")

    def _candidate(self, device, button: int) -> None:
        key = (device.device_id, button)
        if key in self._candidates:
            return
        self._candidates[key] = (device, button)
        item = QListWidgetItem(f"{device.label} — Button {button}")
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.list.addItem(item)
        self.status.setText("Select the input that matches the control you moved, then confirm it.")

    def _failed(self, message: str) -> None:
        self.status.setText(message)

    def _accept_selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        key = tuple(items[0].data(Qt.ItemDataRole.UserRole))
        self._selected = self._candidates.get(key)
        if self._selected is not None:
            self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        self._stop.set()
        super().done(result)

    def selected_candidate(self):
        return self._selected


class AxisDetectDialog(QDialog):
    """Detect one deliberately moved analogue control across cockpit devices."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Detect axis")
        self.setModal(True)
        self.setMinimumWidth(570)
        self._selected = None
        self._signals = AxisSignals()
        self._signals.detected.connect(self._detected)
        self._signals.failed.connect(self._failed)

        root = QVBoxLayout(self)
        instruction = QLabel(
            "Move only the cockpit stick, throttle, slider or rotary you want to use, "
            "through a clear part of its travel. EDL will identify which controller and "
            "axis moved."
        )
        instruction.setWordWrap(True)
        root.addWidget(instruction)
        self.status = QLabel("Waiting for an analogue control to move…")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(50)
        root.addWidget(self.status)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.use_button = QPushButton("Use detected axis")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.accept)
        actions.addWidget(self.use_button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        root.addLayout(actions)

        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            device, axis, semantic_name, value = capture_moved_axis(
                timeout_seconds=12.0,
                movement_threshold=15.0,
            )
        except Exception as exc:
            self._signals.failed.emit(str(exc))
            return
        self._signals.detected.emit(device, axis, semantic_name, value)

    def _detected(self, device, axis: int, semantic_name: str, value: float) -> None:
        self._selected = (device, axis, semantic_name, value)
        self.status.setText(
            f"Detected {device.label} — {semantic_name}. Current position is about {value:.0f}%. "
            "Choose Use detected axis to assign it."
        )
        self.use_button.setEnabled(True)

    def _failed(self, message: str) -> None:
        self.status.setText(message)

    def selected_axis(self):
        return self._selected


class UnifiedEffectParametersEditor(EffectParametersEditor):
    """Effect parameters arranged in a stable responsive grid with matched help."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._groups: dict[QLabel, QWidget] = {}
        self._help_buttons: dict[QLabel, HelpButton] = {}
        super().__init__(parent)

        old = self._row
        host = QWidget(self)
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(7)

        help_by_pair = {
            self.step_seconds: HELP_TEXT["time_per_colour"],
            self.cycle_seconds: HELP_TEXT["pulse_cycle"],
            self.brightness: HELP_TEXT["brightness"],
            self.minimum_brightness: HELP_TEXT["minimum_brightness"],
            self.maximum_brightness: HELP_TEXT["maximum_brightness"],
        }
        for pair, help_html in help_by_pair.items():
            label, spin = pair
            old.removeWidget(label)
            old.removeWidget(spin)
            label.setToolTip("")
            spin.setToolTip("")
            group = QWidget(host)
            line = QHBoxLayout(group)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(5)
            help_button = HelpButton(help_html, group)
            line.addWidget(label)
            line.addWidget(help_button)
            line.addWidget(spin)
            line.addStretch(1)
            self._groups[label] = group
            self._help_buttons[label] = help_button

        old.insertWidget(2, host, 1)
        self._arrange()

    def _arrange(self) -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        pairs = (
            self.step_seconds,
            self.cycle_seconds,
            self.brightness,
            self.minimum_brightness,
            self.maximum_brightness,
        )
        visible = [pair for pair in pairs if not pair[0].isHidden()]
        for index, pair in enumerate(visible):
            group = self._groups[pair[0]]
            group.setVisible(True)
            self._grid.addWidget(group, index // 2, index % 2)
        used = {pair[0] for pair in visible}
        for label, group in self._groups.items():
            if label not in used:
                group.setVisible(False)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)

    def set_effect(self, effect, parameters) -> None:
        super().set_effect(effect, parameters)
        if hasattr(self, "_help_buttons"):
            cycle = self._help_buttons.get(self.cycle_seconds[0])
            if cycle is not None:
                cycle.set_help(
                    HELP_TEXT["breath_cycle"] if effect == BREATH else HELP_TEXT["pulse_cycle"]
                )
        if hasattr(self, "_grid"):
            self._arrange()


class UnifiedPaletteEditor(EffectPaletteEditor):
    """Palette semantics with one help point and no meaningless disabled actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Keep the colour editor useful on ultrawide windows without letting the
        # spectrum dominate the entire authoring workspace. Colour semantics,
        # palette cardinality and saved-colour storage are unchanged.
        self.picker.setMinimumHeight(170)
        self.picker.setMaximumHeight(190)
        self.picker.setMaximumWidth(900)
        self.picker.spectrum.setMinimumHeight(150)
        self.picker.spectrum.setMaximumHeight(180)
        self.picker.spectrum.setMaximumWidth(650)

        title = next((label for label in self.findChildren(QLabel) if label.text() == "Colours"), None)
        if title is not None:
            layout = title.parentWidget().layout() if title.parentWidget() else None
            if isinstance(layout, QHBoxLayout):
                index = layout.indexOf(title)
                layout.insertWidget(index + 1, HelpButton(HELP_TEXT["colours"], self))
        self._update_cardinality_controls()

    def _update_cardinality_controls(self) -> None:
        super()._update_cardinality_controls()
        show = self._effect in {FLASH, BREATH}
        self.add_button.setVisible(show)
        self.remove_button.setVisible(show)
        self.add_button.setToolTip("")
        self.remove_button.setToolTip("")


class EliteStatusDialog(QDialog):
    """Read-only view of the complete canonical Status snapshot."""

    def __init__(self, browse_callback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Elite Status diagnostics")
        self.setMinimumSize(760, 620)
        root = QVBoxLayout(self)

        self.path_label = QLabel("Status.json: not configured")
        self.path_label.setWordWrap(True)
        root.addWidget(self.path_label)
        self.state_label = QLabel("Waiting for a complete canonical Status.json snapshot…")
        self.state_label.setProperty("edlTextRole", "secondary")
        root.addWidget(self.state_label)

        self.table = QTableWidget(len(CANONICAL_DIAGNOSTIC_KEYS), 3, self)
        self.table.setHorizontalHeaderLabels(("Argument", "EDL key", "Current value"))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(False)
        self._status_value_items: dict[str, QTableWidgetItem] = {}
        for row_index, key in enumerate(CANONICAL_DIAGNOSTIC_KEYS):
            display = key if key in {"RawFlags", "RawFlags2"} else display_name_for(key)
            for column, value in enumerate((display, key, "—")):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row_index, column, item)
                if column == 2:
                    self._status_value_items[key] = item
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        root.addWidget(self.table, 1)

        note = QLabel(
            "This is the complete canonical state exposed by EDL's existing Status decoder. "
            "It is diagnostic/reference data only; lighting relevance is still decided by rules."
        )
        note.setWordWrap(True)
        note.setProperty("edlTextRole", "secondary")
        note.setStyleSheet("font-size:9.75pt;")
        root.addWidget(note)

        actions = QHBoxLayout()
        browse = QPushButton("Choose Status.json…")
        browse.clicked.connect(browse_callback)
        actions.addWidget(browse)
        actions.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        root.addLayout(actions)

    def refresh_snapshot(self, status_path: Path | None, state: dict[str, Any], error: str | None = None) -> None:
        self.path_label.setText(
            f"Status.json: {status_path}" if status_path is not None else "Status.json: not configured"
        )
        if error:
            self.state_label.setText(f"Status read error: {error}")
            self.state_label.setProperty("edlState", "attention")
            self.state_label.setProperty("edlTextRole", "")
        elif state:
            self.state_label.setText("Live canonical snapshot")
            self.state_label.setProperty("edlState", "")
            self.state_label.setProperty("edlTextRole", "secondary")
        else:
            self.state_label.setText("Waiting for a complete canonical Status.json snapshot…")
            self.state_label.setProperty("edlState", "")
            self.state_label.setProperty("edlTextRole", "secondary")
        style = self.state_label.style()
        style.unpolish(self.state_label)
        style.polish(self.state_label)
        for key in CANONICAL_DIAGNOSTIC_KEYS:
            item = self._status_value_items.get(key)
            if item is not None:
                item.setText(format_live_value(state.get(key)))


class _HiddenSortItem(QTableWidgetItem):
    """Non-rendering table item that keeps sortable data behind a cell widget."""

    SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, sort_text: str, *, source: str | None = None) -> None:
        super().__init__("")
        self.setData(Qt.ItemDataRole.UserRole, sort_text)
        if source is not None:
            self.setData(self.SOURCE_ROLE, source)

    def set_sort_text(self, sort_text: str) -> None:
        self.setData(Qt.ItemDataRole.UserRole, sort_text)

    def __lt__(self, other) -> bool:
        left = str(self.data(Qt.ItemDataRole.UserRole) or "").casefold()
        right = str(other.data(Qt.ItemDataRole.UserRole) or "").casefold()
        return left < right


class RuleSimulatorDialog(QDialog):
    """Simple hypothetical-state test bench over the accepted evaluator/preview path."""

    PREVIEW_KIND = "scenario"

    def __init__(
        self,
        profile: NativeLightingProfile,
        baseline_state: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._baseline_state = dict(baseline_state)
        self._current_state = build_simulated_state(self._baseline_state)
        self._testing_hardware = False
        self.setWindowTitle("Test rules")
        self.setMinimumSize(500, 380)
        root = QVBoxLayout(self)

        self.intro_label = QLabel(
            "Current = value from the selected Elite Status.json. "
            "Pretend = temporary value used only for this test. "
            "States used by this profile are listed first; all other Elite states follow."
        )
        self.intro_label.setWordWrap(True)
        self.intro_label.setProperty("edlTextRole", "secondary")
        self.intro_label.setStyleSheet("font-size:9.75pt;")
        root.addWidget(self.intro_label)

        self._profile_argument_sources = referenced_argument_sources(profile)
        self._argument_sources = tuple(
            dict.fromkeys((*self._profile_argument_sources, *NORMAL_ARGUMENTS))
        )

        self.state_search = QLineEdit(self)
        self.state_search.setPlaceholderText("Search Elite state…")
        self.state_search.setClearButtonEnabled(True)
        self.state_search.setToolTip("Filter the Elite state list by name.")
        root.addWidget(self.state_search)

        self.argument_table = QTableWidget(
            len(self._argument_sources),
            3,
            self,
        )
        self.argument_table.setHorizontalHeaderLabels(("Elite state", "Current", "Pretend"))
        self.argument_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.argument_table.verticalHeader().setVisible(False)
        self.argument_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.argument_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.argument_table.setStyleSheet(
            "QComboBox, QLineEdit {"
            " min-height: 0px; margin: 0px; padding: 0px 26px 0px 6px;"
            " border: none; border-radius: 0px; background: transparent;"
            "}"
            "QComboBox:hover, QComboBox:focus, QLineEdit:hover, QLineEdit:focus {"
            " background: rgba(255, 255, 255, 10);"
            "}"
        )
        state_header = self.argument_table.horizontalHeader()
        state_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            state_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            state_header.resizeSection(column, 150)
        state_header.setSectionsClickable(True)
        state_header.setSortIndicatorShown(False)
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        state_header.sectionClicked.connect(self._sort_elite_state_column)

        self._argument_inputs: dict[str, QWidget] = {}
        self._current_items: dict[str, _HiddenSortItem] = {}
        self._current_labels: dict[str, QLabel] = {}
        for row_index, source in enumerate(self._argument_sources):
            display_name = display_name_for(source)
            name_item = _HiddenSortItem(display_name, source=source)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.argument_table.setItem(row_index, 0, name_item)

            name_label = QLabel(display_name, self.argument_table)
            name_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            name_label.setContentsMargins(6, 0, 6, 0)
            name_label.setStyleSheet("background:transparent;")
            self.argument_table.setCellWidget(row_index, 0, name_label)

            current_value = self._current_state.get(source)
            current_text = (
                format_live_value(current_value) if self._baseline_state else "—"
            )
            current_item = _HiddenSortItem(current_text)
            current_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.argument_table.setItem(row_index, 1, current_item)
            self._current_items[source] = current_item

            current_label = QLabel(current_text, self.argument_table)
            current_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            current_label.setContentsMargins(6, 0, 6, 0)
            current_label.setStyleSheet("background:transparent;")
            self.argument_table.setCellWidget(row_index, 1, current_label)
            self._current_labels[source] = current_label

            pretend_sort_item = _HiddenSortItem("Use current")
            pretend_sort_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.argument_table.setItem(row_index, 2, pretend_sort_item)

            if isinstance(current_value, bool):
                editor = QComboBox(self.argument_table)
                editor.setFrame(False)
                editor.addItem("Use current", None)
                editor.addItem("Yes", True)
                editor.addItem("No", False)
                editor.currentTextChanged.connect(pretend_sort_item.set_sort_text)
            else:
                editor = QLineEdit(self.argument_table)
                editor.setFrame(False)
                editor.setPlaceholderText("Use current")
                editor.setToolTip("Leave blank to use the current value.")
                editor.textChanged.connect(
                    lambda text, item=pretend_sort_item: item.set_sort_text(
                        text.strip() or "Use current"
                    )
                )
            self._argument_inputs[source] = editor
            self.argument_table.setCellWidget(row_index, 2, editor)

        self.state_search.textChanged.connect(self._filter_elite_state_rows)
        root.addWidget(self.argument_table, 1)

        self.hardware_rows: list[tuple[str, tuple[str, int], QWidget]] = []
        button_addresses = referenced_button_addresses(profile)
        axis_addresses = referenced_axis_addresses(profile)
        uses_keyboard = any(rule.keyboard is not None for rule in profile.rules)
        uses_extra_inputs = uses_keyboard or bool(button_addresses) or bool(axis_addresses)

        self.pressed_keys: QLineEdit | None = None
        self.input_toggle: QToolButton | None = None
        self.input_panel: QWidget | None = None
        self.hardware_table: QTableWidget | None = None
        if uses_extra_inputs:
            self.input_toggle = QToolButton(self)
            self.input_toggle.setText("Keyboard or controller inputs")
            self.input_toggle.setCheckable(True)
            self.input_toggle.setChecked(False)
            self.input_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.input_toggle.setArrowType(Qt.ArrowType.RightArrow)
            root.addWidget(self.input_toggle)

            self.input_panel = QWidget(self)
            input_layout = QVBoxLayout(self.input_panel)
            input_layout.setContentsMargins(0, 0, 0, 0)
            input_layout.setSpacing(8)

            if uses_keyboard:
                keyboard_row = QHBoxLayout()
                keyboard_row.addWidget(QLabel("Pretend these keys are pressed"))
                self.pressed_keys = QLineEdit(self.input_panel)
                self.pressed_keys.setPlaceholderText("Example: CTRL + SHIFT + F10")
                keyboard_row.addWidget(self.pressed_keys, 1)
                input_layout.addLayout(keyboard_row)

            if button_addresses or axis_addresses:
                self.hardware_table = QTableWidget(
                    len(button_addresses) + len(axis_addresses), 4, self.input_panel
                )
                self.hardware_table.setHorizontalHeaderLabels(
                    ("Type", "Device", "Address", "Pretend")
                )
                self.hardware_table.verticalHeader().setVisible(False)
                self.hardware_table.setSelectionMode(
                    QAbstractItemView.SelectionMode.NoSelection
                )
                self.hardware_table.setEditTriggers(
                    QAbstractItemView.EditTrigger.NoEditTriggers
                )
                self.hardware_table.horizontalHeader().setSectionResizeMode(
                    3, QHeaderView.ResizeMode.Stretch
                )
                row_index = 0
                for kind, addresses in (("Button", button_addresses), ("Axis", axis_addresses)):
                    for device, address in addresses:
                        for column, value in enumerate((kind, device, str(address))):
                            item = QTableWidgetItem(value)
                            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                            self.hardware_table.setItem(row_index, column, item)

                        if kind == "Button":
                            editor = QComboBox(self.hardware_table)
                            editor.addItem("Current", None)
                            editor.addItem("Pressed", True)
                            editor.addItem("Released", False)
                        else:
                            editor = QLineEdit(self.hardware_table)
                            editor.setPlaceholderText("Current")
                            editor.setToolTip(
                                "Enter an axis position from 0 to 100, or leave blank."
                            )
                        self.hardware_table.setCellWidget(row_index, 3, editor)
                        self.hardware_rows.append((kind, (device, address), editor))
                        row_index += 1
                self.hardware_table.resizeColumnsToContents()
                input_layout.addWidget(self.hardware_table)

            self.input_panel.setVisible(False)
            self.input_toggle.toggled.connect(self._set_extra_inputs_visible)
            root.addWidget(self.input_panel)

        controls = QHBoxLayout()
        self.test_status = QLabel("Change a Pretend value, then choose Test lighting.")
        self.test_status.setWordWrap(True)
        self.test_status.setProperty("edlTextRole", "secondary")
        controls.addWidget(self.test_status, 1)

        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self._reset_test_values)
        controls.addWidget(self.reset_button)

        self.test_lighting_button = QPushButton("Test lighting")
        self.test_lighting_button.setObjectName("primaryLightingAction")
        self.test_lighting_button.clicked.connect(self._toggle_hardware_test)
        controls.addWidget(self.test_lighting_button)

        close = QPushButton("Close")
        close.clicked.connect(self.close)

        action_width = max(
            self.reset_button.sizeHint().width(),
            self.test_lighting_button.sizeHint().width(),
            close.sizeHint().width(),
        )
        for button in (self.reset_button, self.test_lighting_button, close):
            button.setFixedWidth(action_width)

        controls.addWidget(close)
        root.addLayout(controls)

        window = self._window()
        signals = getattr(window, "_preview_signals", None) if window is not None else None
        if signals is not None:
            signals.finished.connect(self._hardware_test_finished)

        self.resize(560, 420)

    def _window(self):
        parent = self.parentWidget()
        if parent is None:
            return None
        if not callable(getattr(parent, "_edl_start_shared_preview", None)):
            return None
        return parent

    def _refresh_current_state(self) -> None:
        window = self._window()
        diagnostics = getattr(window, "_elite_diagnostics", None) if window is not None else None
        live_state = getattr(diagnostics, "state", None)
        if live_state:
            self._baseline_state = dict(live_state)
            self._current_state = build_simulated_state(self._baseline_state)
        for source, item in self._current_items.items():
            current_text = (
                format_live_value(self._current_state.get(source))
                if live_state
                else "—"
            )
            item.set_sort_text(current_text)
            label = self._current_labels.get(source)
            if label is not None:
                label.setText(current_text)

    def _filter_elite_state_rows(self, text: str) -> None:
        query = text.strip().casefold()
        for row_index in range(self.argument_table.rowCount()):
            item = self.argument_table.item(row_index, 0)
            if item is None:
                continue
            source = item.data(_HiddenSortItem.SOURCE_ROLE)
            display_name = item.data(Qt.ItemDataRole.UserRole)
            searchable = f"{display_name or ''} {source or ''}".casefold()
            self.argument_table.setRowHidden(
                row_index,
                bool(query) and query not in searchable,
            )

    def _sort_elite_state_column(self, column: int) -> None:
        if self._sort_column == column:
            order = (
                Qt.SortOrder.DescendingOrder
                if self._sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            order = Qt.SortOrder.AscendingOrder

        self.argument_table.sortItems(column, order)
        self._sort_column = column
        self._sort_order = order
        header = self.argument_table.horizontalHeader()
        header.setSortIndicator(column, order)
        header.setSortIndicatorShown(True)
        self._filter_elite_state_rows(self.state_search.text())

    def _set_extra_inputs_visible(self, visible: bool) -> None:
        if self.input_panel is None or self.input_toggle is None:
            return
        self.input_panel.setVisible(visible)
        self.input_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )

    @staticmethod
    def _keys(text: str) -> tuple[str, ...]:
        normalized = text.replace(",", "+")
        return tuple(
            dict.fromkeys(
                part.strip().upper()
                for part in normalized.split("+")
                if part.strip()
            )
        )

    def _simulation_inputs(self):
        overrides: dict[str, Any] = {}
        for source, editor in self._argument_inputs.items():
            if isinstance(editor, QComboBox):
                value = editor.currentData()
                if value is not None:
                    overrides[source] = value
            elif isinstance(editor, QLineEdit):
                text = editor.text().strip()
                if text:
                    overrides[source] = parse_synthetic_value(text)

        buttons: dict[tuple[str, int], bool] = {}
        axes: dict[tuple[str, int], float] = {}
        for kind, address, editor in self.hardware_rows:
            if kind == "Button" and isinstance(editor, QComboBox):
                value = editor.currentData()
                if value is not None:
                    buttons[address] = bool(value)
                continue
            if kind == "Axis" and isinstance(editor, QLineEdit):
                text = editor.text().strip()
                if not text:
                    continue
                value = float(text)
                if value < 0.0 or value > 100.0:
                    raise ValueError(f"Axis {address} must be between 0 and 100")
                axes[address] = value

        keys = self._keys(self.pressed_keys.text()) if self.pressed_keys is not None else ()
        return overrides, keys, buttons, axes

    def _reset_test_values(self) -> None:
        if self._hardware_test_running():
            self.test_status.setText("Stop the current test before resetting values.")
            return
        for editor in self._argument_inputs.values():
            if isinstance(editor, QComboBox):
                editor.setCurrentIndex(0)
            elif isinstance(editor, QLineEdit):
                editor.clear()
        if self.pressed_keys is not None:
            self.pressed_keys.clear()
        for _kind, _address, editor in self.hardware_rows:
            if isinstance(editor, QComboBox):
                editor.setCurrentIndex(0)
            elif isinstance(editor, QLineEdit):
                editor.clear()
        self._refresh_current_state()
        self.test_status.setText("Pretend values reset to the current Elite state.")

    def _hardware_test_running(self) -> bool:
        window = self._window()
        if window is None or not callable(getattr(window, "_preview_running", None)):
            return False
        if not window._preview_running():
            return False
        kind = getattr(window, "_edl_preview_kind_value", lambda: None)()
        return kind == self.PREVIEW_KIND

    def _toggle_hardware_test(self) -> None:
        window = self._window()
        if window is None:
            self.test_status.setText("Hardware testing is available from the main EDL window.")
            return

        if self._hardware_test_running():
            window._edl_stop_shared_preview(self.PREVIEW_KIND)
            self.test_lighting_button.setText("Stopping…")
            self.test_lighting_button.setEnabled(False)
            self.test_status.setText("Stopping test and releasing lighting devices…")
            return

        if window._live_running():
            self.test_status.setText("Stop live lighting before testing a pretend situation.")
            return
        if window._preview_running():
            self.test_status.setText("Stop the current hardware preview before starting this test.")
            return

        try:
            self._refresh_current_state()
            overrides, keys, buttons, axes = self._simulation_inputs()
            result = simulate_profile(
                self._profile,
                baseline_state=self._baseline_state,
                state_overrides=overrides,
                pressed_keys=keys,
                button_states=buttons,
                axis_values=axes,
            )
            ownership_targets = tuple(sorted(window._available_runtime_targets()))
            if not ownership_targets:
                raise ValueError(
                    "Select at least one Available device in Setup → Lighting devices first."
                )
            outputs = {output.target: output.output for output in result.outputs}
            snapshot = window._edl_availability_snapshot()
        except Exception as exc:
            self.test_status.setText(str(exc))
            return

        started = window._edl_start_shared_preview(
            self.PREVIEW_KIND,
            "Test rules",
            lambda stop: run_calculated_preview(
                outputs,
                stop,
                ownership_targets=ownership_targets,
                profile_filter=lambda profile: filter_profile_for_runtime(
                    profile,
                    snapshot,
                ),
                govee_configuration=snapshot.govee_configuration,
            ),
            log_data={"ownership_targets": ownership_targets},
        )
        if not started:
            return

        self._testing_hardware = True
        self.test_lighting_button.setText("Stop test")
        self.test_lighting_button.setEnabled(True)
        self.test_status.setText(
            "Testing this pretend situation on the selected lighting devices."
        )

    def _hardware_test_finished(self, error: object) -> None:
        if not self._testing_hardware:
            return
        self._testing_hardware = False
        self.test_lighting_button.setText("Test lighting")
        self.test_lighting_button.setEnabled(True)
        if isinstance(error, Exception):
            self.test_status.setText(f"Test stopped: {format_run_error(error)}")
        else:
            self.test_status.setText(
                "Test stopped. Change a Pretend value and choose Test lighting again."
            )

    def closeEvent(self, event) -> None:  # type: ignore[override]
        window = self._window()
        if window is not None and self._hardware_test_running():
            window._edl_stop_shared_preview(self.PREVIEW_KIND)
        super().closeEvent(event)


class MainWindow(base.MainWindow):
    """Single coherent two-pane rule editor."""

    COLUMNS = ("#", "Target", "Source", "Effect", "Description", "Status", "Colours")

    def __init__(self) -> None:
        self._hid_display_labels: dict[str, str] = {}
        self._live_thread: threading.Thread | None = None
        self._live_stop_event: threading.Event | None = None
        self._live_signals = RunnerSignals()
        self._source_click_filters: list[ComboClickFilter] = []
        self._axis_monitor: AxisLiveMonitor | None = None
        self._axis_revision = -1
        self._virpil_import: VirpilUiImport | None = None
        self._elite_diagnostics = EliteStatusDiagnostics()
        self._elite_status_dialog: EliteStatusDialog | None = None
        self._elite_status_error: str | None = None
        self._axis_semantic_names: dict[tuple[str, int], str] = {}
        super().__init__()
        stored_status_path = self._settings.value(ELITE_STATUS_PATH_KEY, "")
        if isinstance(stored_status_path, str) and stored_status_path.strip():
            self._elite_diagnostics.set_status_path(
                Path(stored_status_path.strip()).expanduser()
            )
        # `palette(mid)` is nearly black in some Windows dark palettes. Structural
        # helper text must remain legible without competing with primary labels.
        for label in self.findChildren(QLabel):
            if label.text() in {
                "identity and enabled state",
                "what activates this rule",
                "where the lighting is sent",
                "what the target should do",
                "Logical target",
                "Whole device",
            }:
                label.setStyleSheet("color: #B8B8B8; font-size: 9.75pt;")
        self._axis_timer = QTimer(self)
        self._axis_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._axis_timer.setInterval(16)
        self._axis_timer.timeout.connect(self._poll_axis_position)
        self._elite_status_timer = QTimer(self)
        self._elite_status_timer.setInterval(100)
        self._elite_status_timer.timeout.connect(self._poll_elite_status)
        self._elite_status_timer.start()
        QTimer.singleShot(0, self._poll_elite_status)

        # Packaged applications may live in a non-writable install directory.
        # Session logs belong in product-owned per-user data instead of beside code.
        self._logger = OptionalUiSessionLogger(default_log_dir())
        self._logger.event("SESSION_START", application=APP_TITLE, log_file=self._logger.path)
        self._live_signals.finished.connect(self._live_finished)

        self.logging_check.blockSignals(True)
        self.logging_check.setChecked(self._logger.enabled)
        self.logging_check.blockSignals(False)
        self.logging_check.toggled.connect(self._set_logging_enabled)

        self.statusBar().setMinimumHeight(34)
        self.statusBar().setStyleSheet("QStatusBar { font-size:10.5pt; }")
        self.table.setStyleSheet(
            "QTableWidget::item:hover { background:transparent; }"
            "QTableWidget::item:selected, QTableWidget::item:selected:hover { "
            "font-weight:600; }"
        )
        self._update_profile_buttons()

    # ---------- construction ----------

    def _build_header(self):
        row = super()._build_header()
        self.import_virpil_button = QPushButton("Import VIRPIL…")
        self.import_virpil_button.clicked.connect(self.import_virpil_profile_dialog)
        row.addWidget(self.import_virpil_button)
        self.elite_status_button = QPushButton("Elite status…")
        self.elite_status_button.clicked.connect(self.show_elite_status_dialog)
        row.addWidget(self.elite_status_button)
        self.elite_data_button = QPushButton("Select Elite Status.json…")
        self.elite_data_button.setToolTip(
            "Choose the Elite Dangerous Status.json file used by live Argument Rules, "
            "Test rules, and diagnostics."
        )
        self.elite_data_button.clicked.connect(self.choose_elite_status_file)
        row.addWidget(self.elite_data_button)
        self.simulator_button = QPushButton("Test rules…")
        self.simulator_button.setToolTip(
            "Try different Elite state or input values, then temporarily show the calculated "
            "result on the selected lighting devices."
        )
        self.simulator_button.clicked.connect(self.show_rule_simulator)
        row.addWidget(self.simulator_button)
        self.start_lighting_button = QPushButton("Start lighting")
        self.start_lighting_button.setObjectName("primaryLightingAction")
        self.start_lighting_button.clicked.connect(self._toggle_live_lighting)
        row.addWidget(self.start_lighting_button)
        self.logging_check = QCheckBox("Troubleshooting log")
        self.logging_check.setToolTip(
            "Record EDL application/session diagnostics: profile operations, rule/input "
            "authoring actions, controller detection, target changes, lighting start/stop/errors, "
            "profile autoload results and shutdown/release problems. Lighting frames are not logged."
        )
        row.addWidget(self.logging_check)
        self.open_log_folder_button = QPushButton("Open log folder")
        self.open_log_folder_button.setToolTip(
            "Open EDL's per-user troubleshooting-log folder."
        )
        self.open_log_folder_button.clicked.connect(self._open_log_folder)
        row.addWidget(self.open_log_folder_button)
        return row

    def _build_rules_panel(self):
        panel = super()._build_rules_panel()
        self.rule_order_hint.setText(
            "Later rules override earlier ones when enabled rules target the same lights."
        )
        self.more_button.setVisible(False)
        root = panel.layout()
        toolbar = root.itemAt(1).layout() if root is not None and root.count() > 1 else None
        if isinstance(toolbar, QHBoxLayout):
            self.copy_button = QPushButton("Copy")
            self.copy_button.clicked.connect(self.copy_rules)
            self.cut_button = QPushButton("Cut")
            self.cut_button.clicked.connect(self.cut_rules)
            self.paste_button = QPushButton("Paste")
            self.paste_button.clicked.connect(self.paste_rules)
            toolbar.addWidget(self.copy_button)
            toolbar.addWidget(self.cut_button)
            toolbar.addWidget(self.paste_button)
        return panel

    def _section(self, title: str, subtitle: str, help_html: str):
        section = base.SectionBox(title, subtitle)
        heading = section.root.itemAt(0).layout()
        if isinstance(heading, QHBoxLayout):
            heading.setSpacing(7)
            subtitle_widget = None
            for index in range(heading.count()):
                widget = heading.itemAt(index).widget()
                if isinstance(widget, QLabel) and widget.text() == subtitle:
                    subtitle_widget = widget
                    break
            if isinstance(subtitle_widget, QLabel):
                subtitle_widget.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:9.75pt;")
                subtitle_widget.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        return section

    def _build_editor_panel(self):
        panel = super()._build_editor_panel()
        root = panel.layout()

        header = root.itemAt(0).layout() if root is not None and root.count() else None
        if isinstance(header, QHBoxLayout):
            insert_at = header.indexOf(self.cancel_rule_button)
            if insert_at < 0:
                insert_at = max(0, header.count() - 2)

            self.preview_header_action = QWidget(panel)
            self.preview_header_action.setObjectName("previewHeaderAction")
            preview_layout = QHBoxLayout(self.preview_header_action)
            preview_layout.setContentsMargins(8, 2, 8, 2)
            preview_layout.setSpacing(7)

            self.preview_title = QLabel("Preview")
            self.preview_title.setStyleSheet("font-weight:600;")
            preview_layout.addWidget(self.preview_title)

            self.preview_status = QLabel("Select a rule")
            self.preview_status.setProperty("edlTextRole", "secondary")
            self.preview_status.setMinimumWidth(110)
            self.preview_status.setMaximumWidth(260)
            self.preview_status.setToolTip(
                "Preview tests the current editor values without applying the rule "
                "or starting live lighting."
            )
            preview_layout.addWidget(self.preview_status)

            self.preview_button = QPushButton("Preview output")
            self.preview_button.setObjectName("previewAction")
            self.preview_button.setMinimumWidth(120)
            self.preview_button.clicked.connect(self._toggle_preview)
            preview_layout.addWidget(self.preview_button)

            self.preview_help = HelpButton(
                HELP_TEXT["test_output"],
                self.preview_header_action,
            )
            preview_layout.addWidget(self.preview_help)

            self.preview_separator = QFrame(panel)
            self.preview_separator.setFrameShape(QFrame.Shape.VLine)
            self.preview_separator.setFrameShadow(QFrame.Shadow.Plain)
            self.preview_separator.setObjectName("previewHeaderSeparator")

            header.insertWidget(insert_at, self.preview_header_action)
            header.insertWidget(insert_at + 1, self.preview_separator)

        self.rule_editor_empty_state = QWidget(panel)
        empty_layout = QVBoxLayout(self.rule_editor_empty_state)
        empty_layout.setContentsMargins(28, 28, 28, 28)
        empty_layout.setSpacing(12)
        empty_layout.addStretch(1)

        self.rule_editor_empty_text = QLabel(
            "No rule selected.\nAdd a rule to begin."
        )
        self.rule_editor_empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rule_editor_empty_text.setWordWrap(True)
        self.rule_editor_empty_text.setProperty("edlTextRole", "secondary")
        self.rule_editor_empty_text.setStyleSheet("font-size:11pt;")
        empty_layout.addWidget(self.rule_editor_empty_text)

        self.rule_editor_empty_add = QPushButton("+ Add rule")
        self.rule_editor_empty_add.setMinimumWidth(130)
        self.rule_editor_empty_add.clicked.connect(self.add_rule)
        empty_layout.addWidget(
            self.rule_editor_empty_add,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        empty_layout.addStretch(1)

        if root is not None:
            root.insertWidget(1, self.rule_editor_empty_state, 1)
        self.rule_editor_empty_state.hide()
        return panel

    def _set_rule_editor_empty(
        self,
        visible: bool,
        *,
        text: str | None = None,
        allow_add: bool = True,
    ) -> None:
        state = getattr(self, "rule_editor_empty_state", None)
        if state is None:
            return
        if text is not None:
            self.rule_editor_empty_text.setText(text)
        self.rule_editor_empty_add.setVisible(bool(allow_add))
        state.setVisible(bool(visible))
        if hasattr(self, "editor_scroll"):
            self.editor_scroll.setVisible(not bool(visible))

    def _build_rule_section(self):
        section = self._section("RULE", "identity and enabled state", RULE_HELP)
        grid = QGridLayout()
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(7)
        grid.addWidget(QLabel("Description"), 0, 0)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("Describe or name this rule")
        self.comment_edit.textChanged.connect(self._editor_changed)
        grid.addWidget(self.comment_edit, 0, 1)
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.toggled.connect(self._editor_changed)
        grid.addWidget(self.enabled_check, 0, 2)
        grid.setColumnStretch(1, 1)
        section.root.addLayout(grid)
        return section

    def _build_source_section(self):
        section = self._section("SOURCE", "what activates this rule", SECTION_HELP["SOURCE"])
        row = QHBoxLayout()
        row.addWidget(QLabel("Source type"))
        self.source_type_combo = QComboBox()
        self.source_type_combo.setMinimumWidth(180)
        self.source_type_combo.addItems(("Argument", "Keyboard", "Button", "Axis"))
        self.source_type_combo.currentTextChanged.connect(self._source_type_changed)
        row.addWidget(self.source_type_combo)
        row.addStretch(1)
        section.root.addLayout(row)

        self.source_stack = QStackedWidget()
        self.source_stack.addWidget(self._build_argument_source())
        self.source_stack.addWidget(self._build_keyboard_source())
        self.source_stack.addWidget(self._build_button_source())
        self.source_stack.addWidget(self._build_axis_source())
        section.root.addWidget(self.source_stack)
        return section

    def _build_argument_source(self) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        helper = QLabel(
            "Add one or more conditions. Every condition must be true for this rule to activate."
        )
        helper.setWordWrap(True)
        helper.setStyleSheet("color: #B8B8B8;")
        root.addWidget(helper)

        self.argument_rows_host = QWidget()
        self.argument_rows_layout = QVBoxLayout(self.argument_rows_host)
        self.argument_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.argument_rows_layout.setSpacing(5)
        self.argument_rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.argument_rows_host)

        add = QPushButton("+ Add condition")
        add.clicked.connect(lambda: self._add_argument_condition())
        root.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        return panel

    def _build_keyboard_source(self):
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)
        grid.addWidget(QLabel("Combination"), 0, 0)
        self.keyboard_keys = QLineEdit()
        self.keyboard_keys.setPlaceholderText("Example: CTRL + SHIFT + F10")
        self.keyboard_keys.textChanged.connect(self._editor_changed)
        grid.addWidget(self.keyboard_keys, 0, 1)
        record = QPushButton("Record keys…")
        record.clicked.connect(self.record_keyboard_combination)
        grid.addWidget(record, 0, 2)
        grid.addWidget(HelpButton(HELP_TEXT["keyboard_record"], panel), 0, 3)
        grid.setColumnStretch(1, 1)
        return panel

    def _label_help(self, text: str, help_html: str, parent: QWidget):
        host = QWidget(parent)
        line = QHBoxLayout(host)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        line.addWidget(QLabel(text))
        line.addWidget(HelpButton(help_html, host))
        line.addStretch(1)
        return host

    def _build_button_source(self):
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)
        grid.addWidget(self._label_help("Device", HELP_TEXT["button_device"], panel), 0, 0)
        self.button_device = QComboBox()
        self.button_device.setMinimumWidth(320)
        self.button_device.currentIndexChanged.connect(self._editor_changed)
        grid.addWidget(self.button_device, 0, 1, 1, 2)
        grid.addWidget(self._label_help("Button", HELP_TEXT["button_number"], panel), 1, 0)
        self.button_number = base.QSpinBox()
        self.button_number.setRange(0, 4096)
        self.button_number.valueChanged.connect(self._editor_changed)
        grid.addWidget(self.button_number, 1, 1)
        self.detect_button = QPushButton("Detect button / switch…")
        self.detect_button.clicked.connect(self.start_button_detection)
        grid.addWidget(self.detect_button, 1, 2)
        grid.addWidget(HelpButton(HELP_TEXT["button_detect"], panel), 1, 3)
        grid.addWidget(self._label_help("State", HELP_TEXT["button_state"], panel), 2, 0)
        self.button_state = QComboBox()
        self.button_state.addItems(base.BUTTON_STATES)
        self.button_state.currentTextChanged.connect(self._editor_changed)
        grid.addWidget(self.button_state, 2, 1)
        self.button_capture_status = QLabel("")
        self.button_capture_status.setWordWrap(True)
        self.button_capture_status.setProperty("edlTextRole", "secondary")
        grid.addWidget(self.button_capture_status, 3, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        return panel

    def _build_axis_source(self):
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)
        grid.addWidget(self._label_help("Controller", HELP_TEXT["axis_device"], panel), 0, 0)
        self.axis_device = QComboBox()
        self.axis_device.setMinimumWidth(320)
        self.axis_device.currentIndexChanged.connect(self._axis_binding_changed)
        grid.addWidget(self.axis_device, 0, 1, 1, 3)

        grid.addWidget(self._label_help("Control", AXIS_HELP, panel), 1, 0)
        self.axis_binding_label = QLabel("Use Detect axis and move the control you want to use.")
        self.axis_binding_label.setWordWrap(True)
        self.axis_binding_label.setStyleSheet("font-weight:600;")
        grid.addWidget(self.axis_binding_label, 1, 1, 1, 2)
        self.detect_axis_button = QPushButton("Detect axis…")
        self.detect_axis_button.clicked.connect(self.start_axis_detection)
        grid.addWidget(self.detect_axis_button, 1, 3)
        grid.addWidget(HelpButton(AXIS_DETECT_HELP, panel), 1, 4)

        # The numeric HID address is persistence/diagnostic data, not an
        # authoring concept. Keep the widget for the existing rule builder but
        # do not expose it in the normal UI after Detect axis has named it.
        self.axis_internal_label = QLabel("Internal axis")
        self.axis_internal_label.setVisible(False)
        self.axis_number = base.QSpinBox()
        self.axis_number.setRange(0, 128)
        self.axis_number.setMaximumWidth(90)
        self.axis_number.valueChanged.connect(self._axis_binding_changed)
        self.axis_number.setVisible(False)

        grid.addWidget(QLabel("Current position"), 2, 0)
        self.axis_position_text = QLabel("—")
        self.axis_position_text.setMinimumWidth(58)
        grid.addWidget(self.axis_position_text, 2, 1)
        self.axis_position_bar = QProgressBar()
        self.axis_position_bar.setRange(0, 100)
        self.axis_position_bar.setValue(0)
        self.axis_position_bar.setTextVisible(False)
        self.axis_position_bar.setMaximumHeight(12)
        # Force immediate chunk updates instead of the native Windows progress-bar
        # transition animation, which visibly trails fast analogue movement.
        self.axis_position_bar.setStyleSheet(
            "QProgressBar { border:1px solid palette(mid); background:palette(base); padding:0; }"
            "QProgressBar::chunk { background:palette(highlight); }"
        )
        grid.addWidget(self.axis_position_bar, 2, 2, 1, 2)

        direction_help = QLabel(
            "Move the control in the direction you care about. If the percentage rises, "
            "use Above. If it falls, use Below."
        )
        direction_help.setWordWrap(True)
        direction_help.setProperty("edlTextRole", "secondary")
        direction_help.setStyleSheet("font-size:9.75pt;")
        grid.addWidget(direction_help, 3, 1, 1, 3)

        grid.addWidget(self._label_help("Activates when", HELP_TEXT["axis_condition"], panel), 4, 0)
        self.axis_operator = AxisOperatorCombo()
        self.axis_operator.setMinimumWidth(150)
        self.axis_operator.currentTextChanged.connect(self._axis_operator_changed)
        grid.addWidget(self.axis_operator, 4, 1)
        grid.addWidget(QLabel("Value"), 5, 0)
        self.axis_value = base.QDoubleSpinBox()
        self.axis_value.setRange(0.0, 100.0)
        self.axis_value.setSuffix(" %")
        self.axis_value.setDecimals(1)
        self.axis_value.valueChanged.connect(self._editor_changed)
        grid.addWidget(self.axis_value, 5, 1)
        self.axis_secondary_label = QLabel("To")
        grid.addWidget(self.axis_secondary_label, 6, 0)
        self.axis_secondary = base.QDoubleSpinBox()
        self.axis_secondary.setRange(0.0, 100.0)
        self.axis_secondary.setSuffix(" %")
        self.axis_secondary.setDecimals(1)
        self.axis_secondary.valueChanged.connect(self._editor_changed)
        grid.addWidget(self.axis_secondary, 6, 1)
        grid.setColumnStretch(2, 1)
        return panel

    def _build_target_section(self):
        section = self._section("TARGET", "where the lights are sent", SECTION_HELP["TARGET"])
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.addWidget(QLabel("Lights"), 0, 0)
        self.target_combo = MultiTargetSelector()
        self.target_combo.currentTextChanged.connect(self._target_changed)
        grid.addWidget(self.target_combo, 0, 1)
        grid.setColumnStretch(1, 1)
        section.root.addLayout(grid)

        self.target_availability_status = QLabel("")
        self.target_availability_status.setProperty("edlTextRole", "secondary")
        self.target_availability_status.setWordWrap(True)
        self.target_combo._edl_availability_label = self.target_availability_status
        section.root.addWidget(self.target_availability_status)
        return section

    def _build_effect_section(self):
        section = self._section("EFFECT", "what the lights should do", SECTION_HELP["EFFECT"])
        row = QHBoxLayout()
        row.addWidget(QLabel("Effect"))
        self.effect_combo = QComboBox()
        self.effect_combo.setMinimumWidth(150)
        self.effect_combo.addItems(base.KNOWN_EFFECTS)
        self.effect_combo.insertSeparator(self.effect_combo.count())
        for pending_effect in PENDING_EFFECTS:
            index = self.effect_combo.count()
            self.effect_combo.addItem(pending_effect)
            self.effect_combo.setItemData(
                index,
                QBrush(PENDING_EFFECT_FOREGROUND),
                Qt.ItemDataRole.ForegroundRole,
            )
            self.effect_combo.setItemData(
                index,
                "Awaiting physical acceptance — visible for review, not selectable yet.",
                Qt.ItemDataRole.ToolTipRole,
            )
            item_method = getattr(self.effect_combo.model(), "item", None)
            item = item_method(index) if callable(item_method) else None
            if item is not None:
                item.setSelectable(False)
        self.effect_combo.currentTextChanged.connect(self._effect_changed)
        row.addWidget(self.effect_combo)
        row.addStretch(1)
        section.root.addLayout(row)

        self.effect_controls = UnifiedEffectParametersEditor()
        self.effect_controls.parametersChanged.connect(self._editor_changed)
        section.root.addWidget(self.effect_controls)
        self.palette_editor = UnifiedPaletteEditor()
        self.palette_editor.paletteChanged.connect(self._editor_changed)
        section.root.addWidget(self.palette_editor)
        return section

    # ---------- Argument row ----------

    def _add_argument_condition(self, condition=None, *, mark_dirty: bool = True) -> None:
        row = HumanArgumentConditionRow(condition)
        # Put the long Argument reference on a deliberate '?' rather than the combo.
        help_button = HelpButton("<b>Argument</b><br><br>Choose the Elite game state or value this condition should follow.", row)
        row.layout().insertWidget(1, help_button)

        def sync_help(*_args) -> None:
            tip = row.source.toolTip()
            if tip:
                help_button.setToolTip(tip)
            row.source.setToolTip("")
            for index in range(row.source.count()):
                row.source.setItemData(index, None, Qt.ItemDataRole.ToolTipRole)

        row.source.currentTextChanged.connect(sync_help)
        sync_help()
        line = row.source.lineEdit()
        if line is not None:
            filter_object = ComboClickFilter(row.source)
            line.installEventFilter(filter_object)
            self._source_click_filters.append(filter_object)
        row.changed.connect(self._editor_changed)
        row.removeRequested.connect(self._remove_argument_condition)
        live_value = QLabel("Live: —", row)
        live_value.setMinimumWidth(92)
        live_value.setProperty("edlTextRole", "secondary")
        live_value.setStyleSheet("font-size:9.75pt;")
        row.live_value_label = live_value
        layout = row.layout()
        if layout is not None:
            layout.insertWidget(max(0, layout.count() - 1), live_value)
        row.source.currentTextChanged.connect(lambda _text, current=row: self._update_argument_live_value(current))
        self._argument_rows.append(row)
        self.argument_rows_layout.addWidget(row)
        self._update_argument_live_value(row)
        if mark_dirty and not self._loading_editor:
            self._set_editor_dirty(True)

    # ---------- clean draft/source state ----------

    def _reset_source_widgets(self) -> None:
        previous = self._loading_editor
        self._loading_editor = True
        try:
            if hasattr(self, "keyboard_keys"):
                self.keyboard_keys.clear()
            if hasattr(self, "button_device"):
                self.button_device.setCurrentIndex(-1)
                self.button_number.setValue(0)
                self.button_capture_status.clear()
            if hasattr(self, "axis_device"):
                self._stop_axis_monitor()
                self.axis_device.setCurrentIndex(-1)
                self.axis_number.setValue(0)
                self.axis_value.setValue(0.0)
                self.axis_secondary.setValue(0.0)
                self.axis_semantic_names_clear_current()
                if hasattr(self, "axis_binding_label"):
                    self.axis_binding_label.setText("Use Detect axis and move the control you want to use.")
                if hasattr(self, "axis_position_text"):
                    self.axis_position_text.setText("—")
                    self.axis_position_bar.setValue(0)
        finally:
            self._loading_editor = previous

    def _load_source(self, rule) -> None:
        self._reset_source_widgets()
        super()._load_source(rule)
        self._update_axis_binding_label()
        self._refresh_axis_monitor()

    def _load_draft(self, rule, *, title: str) -> None:
        super()._load_draft(rule, title=title)
        self._set_rule_editor_empty(False)
        previous = self.target_combo.blockSignals(True)
        try:
            self.target_combo.set_targets(rule.targets, emit=False)
        finally:
            self.target_combo.blockSignals(previous)
        self._update_test_output_state()

    def add_rule(self) -> None:
        if self._profile is None:
            self._profile = make_default_profile(NEW_PROFILE_NAME)
            self._profile_path = None
            self._profile_dirty = True
            self.profile_name.setText(NEW_PROFILE_NAME)
            self._logger.event("PROFILE_NEW", profile=NEW_PROFILE_NAME)
            self._refresh_table(())
            self._update_profile_buttons()
        if not self._can_leave_current_draft():
            return
        self.table.clearSelection()
        self._new_rule_draft = True
        self._editing_row = None
        self._logger.event("RULE_ADD_DRAFT")
        self._load_draft(
            base.LightingRule(
                name="New rule",
                conditions=(ArgumentCondition("Docked", "Equal", True),),
                colour=(255, 255, 255),
                target="KEYBOARD",
                effect=STATIC,
                effect_parameters=default_parameters_for_effect(STATIC),
            ),
            title="New rule",
        )
        self._set_editor_dirty(False)
        self.apply_rule_button.setText(APPLY_RULE_CHANGES)
        self.cancel_rule_button.setText(CANCEL_RULE_CHANGES)
        self._show_pending_row()
        # Draft admission changed after the earlier profile-button refresh.
        # Recompute Start Lighting now so it cannot expose a stale readiness tooltip/state.
        self._update_profile_buttons()

    def duplicate_rules(self) -> None:
        if self._profile is None:
            return
        rows = self.selected_rows()
        if len(rows) != 1:
            self.statusBar().showMessage("Select one rule to duplicate and edit.", 4500)
            return
        if not self._can_leave_current_draft():
            return
        source_row = rows[0]
        source_rule = self._profile.rules[source_row]
        draft = replace(
            source_rule,
            name=(f"{source_rule.name} copy" if source_rule.name else "Copy"),
        )
        self.table.clearSelection()
        self._new_rule_draft = True
        self._editing_row = None
        self._logger.event("RULE_DUPLICATE_DRAFT", source_row=source_row + 1)
        self._load_draft(draft, title=f"Copy of rule {source_row + 1}")
        self._set_editor_dirty(False)
        self.apply_rule_button.setText(APPLY_RULE_CHANGES)
        self.cancel_rule_button.setText(CANCEL_RULE_CHANGES)
        self._show_pending_row()
        # Duplicating also creates a new unapplied draft; refresh readiness at
        # the point the draft becomes authoritative.
        self._update_profile_buttons()

    def _load_rule_into_editor(self, row: int) -> None:
        super()._load_rule_into_editor(row)
        self.apply_rule_button.setText(APPLY_RULE_CHANGES)
        self.cancel_rule_button.setText(CANCEL_RULE_CHANGES)

    def _show_pending_row(self) -> None:
        if not self._new_rule_draft or self._profile is None:
            return
        index = len(self._profile.rules)
        self.table.setRowCount(index + 1)
        values = (
            "—",
            self.target_combo.display_text(),
            self.source_type_combo.currentText(),
            self.effect_combo.currentText(),
            self.comment_edit.text().strip() or "New rule — waiting for Apply changes",
            "Draft",
            "",
        )
        background = QBrush(QColor(ACCENT))
        foreground = QBrush(QColor(TEXT_PRIMARY))
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setBackground(background)
            item.setForeground(foreground)
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            self.table.setItem(index, column, item)
        self.table.setRowHeight(index, 38)
        applied = len(self._profile.rules)
        self.rule_count.setText(f"{applied} applied · 1 draft")
        if hasattr(self, "rules_empty_hint"):
            self.rules_empty_hint.setVisible(False)

    def _set_editor_dirty(self, dirty: bool) -> None:
        super()._set_editor_dirty(dirty)
        if dirty:
            self.draft_state.setText("Unapplied rule changes")
        elif self._new_rule_draft:
            self.draft_state.setText("New rule — unapplied")

    def _editor_changed(self, *args) -> None:
        super()._editor_changed(*args)
        if self._new_rule_draft:
            self._show_pending_row()

    def _show_multi_selection(self, rows: tuple[int, ...]) -> None:
        super()._show_multi_selection(rows)
        self._set_rule_editor_empty(
            True,
            text=(
                f"{len(rows)} rules selected.\n"
                "Use the rule toolbar for bulk actions, or select one rule to edit it."
            ),
            allow_add=False,
        )

    def cancel_rule_changes(self) -> None:
        was_new = self._new_rule_draft
        super().cancel_rule_changes()
        if was_new and self._profile is not None:
            self._refresh_table(self.selected_rows())

    def apply_rule(self) -> None:
        super().apply_rule()
        if not self._new_rule_draft:
            self.apply_rule_button.setText(APPLY_RULE_CHANGES)
            self.cancel_rule_button.setText(CANCEL_RULE_CHANGES)
            self._update_profile_buttons()

    def _clear_editor(self) -> None:
        super()._clear_editor()
        if self._profile is not None and self.table.rowCount() != len(self._profile.rules):
            self._refresh_table(())
        has_rules = bool(self._profile is not None and self._profile.rules)
        self._set_rule_editor_empty(
            True,
            text=(
                "Select a rule to edit it, or add another rule."
                if has_rules
                else "No rules yet.\nAdd a rule to begin."
            ),
            allow_add=True,
        )

    # ---------- Test rules ----------

    def show_rule_simulator(self) -> None:
        if self._profile is None:
            QMessageBox.information(self, "Test rules", "Load or create a profile first.")
            return
        dialog = RuleSimulatorDialog(self._profile, self._elite_diagnostics.state, self)
        dialog.exec()

    # ---------- Elite live diagnostics ----------

    @staticmethod
    def _argument_source_name(row) -> str:
        index = row.source.currentIndex()
        if index >= 0:
            stored = row.source.itemData(index, Qt.ItemDataRole.UserRole)
            if isinstance(stored, str) and stored:
                return stored
        return resolve_edl_name(row.source.currentText())

    def _update_argument_live_value(self, row) -> None:
        label = getattr(row, "live_value_label", None)
        if label is None:
            return
        source = self._argument_source_name(row)
        value = self._elite_diagnostics.current_value(source) if source else None
        label.setText(f"Live: {format_live_value(value)}")
        path = self._elite_diagnostics.status_path
        label.setToolTip(
            f"Current canonical value for {source or 'this Argument'}"
            + (f" from {path}" if path is not None else ". Status.json is not configured.")
        )

    def _refresh_argument_live_values(self) -> None:
        for row in self._argument_rows:
            self._update_argument_live_value(row)

    def _refresh_elite_status_dialog(self) -> None:
        dialog = self._elite_status_dialog
        if dialog is None:
            return
        dialog.refresh_snapshot(
            self._elite_diagnostics.status_path,
            self._elite_diagnostics.state,
            self._elite_status_error,
        )

    def _poll_elite_status(self) -> None:
        try:
            update = self._elite_diagnostics.poll_once()
        except Exception as exc:
            self._elite_status_error = str(exc)
            self._refresh_elite_status_dialog()
            return
        if update is None:
            return
        self._elite_status_error = None
        self._refresh_argument_live_values()
        self._refresh_elite_status_dialog()

    def choose_elite_status_file(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose Elite Dangerous Status.json",
            str(self._elite_diagnostics.status_path.parent if self._elite_diagnostics.status_path else Path.home()),
            "Elite Status (Status.json);;JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        selected_path = Path(path).expanduser().resolve(strict=False)
        self._settings.setValue(ELITE_STATUS_PATH_KEY, str(selected_path))
        self._settings.sync()
        self._elite_diagnostics.set_status_path(selected_path)
        self._elite_status_error = None
        self._refresh_argument_live_values()
        self._poll_elite_status()
        self._refresh_elite_status_dialog()
        refresh_readiness = getattr(self, "_edl_refresh_readiness", None)
        if callable(refresh_readiness):
            refresh_readiness()
        self._logger.event("ELITE_STATUS_PATH", path=selected_path)

    def show_elite_status_dialog(self) -> None:
        if self._elite_status_dialog is None:
            self._elite_status_dialog = EliteStatusDialog(self.choose_elite_status_file, self)
        self._poll_elite_status()
        self._refresh_elite_status_dialog()
        self._elite_status_dialog.show()
        self._elite_status_dialog.raise_()
        self._elite_status_dialog.activateWindow()

    # ---------- VIRPIL import ----------

    def load_profile(self, path: Path) -> None:
        # Native Open and faithful VIRPIL Import are deliberately distinct.
        self._virpil_import = None
        super().load_profile(path)

    def import_virpil_profile_dialog(self) -> None:
        if not self._can_leave_current_draft():
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import VIRPIL Link Tool profile",
            "",
            "VIRPIL Link Tool profile (*.led.json);;JSON files (*.json);;All files (*)",
        )
        if path:
            self.import_virpil_profile(Path(path))

    def import_virpil_profile(self, path: Path) -> None:
        if not self._can_leave_current_draft():
            return
        try:
            imported = load_virpil_for_ui(path)
        except Exception as exc:
            QMessageBox.critical(self, "VIRPIL import failed", str(exc))
            return

        self._stop_axis_monitor()
        self._virpil_import = imported
        self._profile = imported.working_profile
        # The working view has no native file until the operator explicitly saves
        # a derivative. The original .led.json is never overwritten.
        self._profile_path = None
        self._profile_dirty = True
        self.profile_name.setText(imported.working_profile.name)
        self._refresh_table(())
        if imported.working_profile.rules:
            self._select_rows((0,))
        else:
            self._clear_editor()
        self._update_profile_buttons()

        summary = imported.summary
        self._logger.event(
            "VIRPIL_IMPORT",
            path=path,
            rules=summary.rules,
            argument_rules=summary.argument_rules,
            keyboard_rules=summary.keyboard_rules,
            button_rules=summary.button_rules,
            axis_rules=summary.axis_rules,
            steady_rules=summary.steady_rules,
            flashing_rules=summary.flashing_rules,
            unique_targets=summary.unique_targets,
        )
        QMessageBox.information(
            self,
            "VIRPIL import complete",
            (
                f"Imported {summary.rules} / {summary.rules} rules through the accepted VIRPIL importer.\n\n"
                f"Argument {summary.argument_rules}  |  Keyboard {summary.keyboard_rules}  |  "
                f"Button {summary.button_rules}  |  Axis {summary.axis_rules}\n"
                f"Steady {summary.steady_rules}  |  Flashing {summary.flashing_rules}\n"
                f"Preserved output targets: {summary.unique_targets}\n\n"
                "The complete VIRPIL source record is retained separately from the normalized "
                "editor view, including targets EDL cannot currently render. The source .led.json "
                "is never modified. Save/Save as creates an EDL native derivative only."
            ),
        )

    # ---------- input validation / binding ----------

    def record_keyboard_combination(self) -> None:
        dialog = KeyboardCaptureDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        combination = dialog.combination()
        if not combination:
            return
        keys = base._parse_keyboard_keys(combination)
        try:
            for key in keys:
                virtual_key_code(key)
        except ValueError as exc:
            QMessageBox.information(
                self,
                "Key not supported yet",
                f"{exc}. Choose a key that EDL's live keyboard reader supports.",
            )
            return
        self.keyboard_keys.setText(" + ".join(keys))
        self._set_editor_dirty(True)
        self._logger.event("KEYBOARD_CAPTURED", combination=" + ".join(keys))

    def _build_rule_from_editor(self):
        rule = super()._build_rule_from_editor()
        selected_targets = self.target_combo.targets()
        if not selected_targets:
            raise ValueError("Select at least one lighting target")
        rule = replace(rule, targets=selected_targets)
        if rule.keyboard is not None:
            for key in rule.keyboard.keys:
                virtual_key_code(key)
        return rule

    def start_button_detection(self) -> None:
        self.refresh_hid_devices(log=False)
        self._logger.event("BUTTON_DETECT_START")
        dialog = ButtonDetectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._logger.event("BUTTON_DETECT_CANCEL")
            return
        selected = dialog.selected_candidate()
        if selected is None:
            return
        device, button = selected
        self.refresh_hid_devices(log=False)
        self._populate_device_combo(self.button_device, device.device_id)
        self.button_number.setValue(button)
        self.button_capture_status.setText(
            f"Using Button {button} on {self.button_device.currentText()}."
        )
        self._set_editor_dirty(True)
        self._logger.event("BUTTON_DETECTED", device=self.button_device.currentText(), button=button)

    def start_axis_detection(self) -> None:
        self._stop_axis_monitor()
        self.refresh_hid_devices(log=False)
        self._logger.event("AXIS_DETECT_START")
        dialog = AxisDetectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._logger.event("AXIS_DETECT_CANCEL")
            self._refresh_axis_monitor()
            return
        selected = dialog.selected_axis()
        if selected is None:
            return
        device, axis, semantic_name, value = selected
        self.refresh_hid_devices(log=False)
        self._populate_device_combo(self.axis_device, device.device_id)
        self.axis_number.setValue(axis)
        self._axis_semantic_names[(device.device_id, axis)] = semantic_name
        self._update_axis_binding_label()
        position_text, position_percent = axis_position_display(value)
        self.axis_position_text.setText(position_text)
        self.axis_position_bar.setValue(position_percent)
        self._refresh_axis_monitor()
        self._set_editor_dirty(True)
        self.statusBar().showMessage(
            f"Detected {self.axis_device.currentText()} — {semantic_name}. Move it and use Above if the percentage rises, or Below if it falls.",
            8000,
        )
        self._logger.event(
            "AXIS_DETECTED",
            device=self.axis_device.currentText(),
            axis=axis,
            axis_name=semantic_name,
            observed_percent=round(value, 2),
        )

    # ---------- device presentation ----------

    def refresh_hid_devices(self, *_args, log: bool = True) -> None:
        try:
            devices = enumerate_hid_devices()
        except Exception as exc:
            devices = ()
            if log:
                self.statusBar().showMessage(f"Controller discovery failed: {exc}", 5000)
        def sort_key(device):
            virtual = 1 if device.vendor_id == 0x1234 else 0
            return virtual, friendly_device_label(device).casefold(), device.product_id
        self._hid_devices = tuple(sorted(devices, key=sort_key))
        counts = Counter(friendly_device_label(device) for device in self._hid_devices)
        seen: Counter[str] = Counter()
        self._hid_display_labels = {}
        for device in self._hid_devices:
            raw_label = friendly_device_label(device)
            seen[raw_label] += 1
            ordinal = seen[raw_label] if counts[raw_label] > 1 else None
            self._hid_display_labels[device.device_id] = friendly_device_label(device, ordinal)
        current_button = self.button_device.currentData() if hasattr(self, "button_device") else None
        current_axis = self.axis_device.currentData() if hasattr(self, "axis_device") else None
        if hasattr(self, "button_device"):
            self._populate_device_combo(self.button_device, current_button)
        if hasattr(self, "axis_device"):
            self._populate_device_combo(self.axis_device, current_axis)

    def _populate_device_combo(self, combo: QComboBox, selected_device_id: object = None) -> None:
        selected = selected_device_id if isinstance(selected_device_id, str) else None
        previous = self._loading_editor
        self._loading_editor = True
        try:
            combo.clear()
            for device in self._hid_devices:
                combo.addItem(self._hid_display_labels.get(device.device_id, device.label), device.device_id)
            if selected:
                index = next((i for i in range(combo.count()) if combo.itemData(i) == selected), -1)
                if index < 0:
                    combo.addItem("Stored controller (not currently detected)", selected)
                    index = combo.count() - 1
                combo.setCurrentIndex(index)
            elif combo.count():
                combo.setCurrentIndex(0)
            else:
                combo.setCurrentIndex(-1)
                combo.setPlaceholderText("No controller devices detected")
        finally:
            self._loading_editor = previous

    def _source_type_changed(self, source_type: str) -> None:
        index = {"Argument": 0, "Keyboard": 1, "Button": 2, "Axis": 3}.get(
            source_type, 0
        )
        self.source_stack.setCurrentIndex(index)
        if self._loading_editor:
            return
        if source_type == "Argument" and not self._argument_rows:
            self._add_argument_condition(mark_dirty=False)
        if source_type in {"Button", "Axis"}:
            # Discovery is automatic; the operator does not need a visible
            # maintenance button just to populate an ordinary selector.
            self.refresh_hid_devices(log=False)
        self._logger.event("SOURCE_TYPE", value=source_type)
        self._set_editor_dirty(True)

        if not self._loading_editor and source_type in {"Button", "Axis"}:
            self.refresh_hid_devices(log=False)
        if not self._loading_editor and source_type == "Axis":
            # New Axis-source authoring should be usable across the whole
            # physical travel by default. The previous first-item default
            # (Below 0%) can never match a normalized 0..100% axis.
            no_named_binding = self.axis_binding_label.text().startswith("Use Detect axis")
            untouched_thresholds = (
                self.axis_value.value() == 0.0
                and self.axis_secondary.value() == 0.0
            )
            if no_named_binding and untouched_thresholds:
                previous = self._loading_editor
                self._loading_editor = True
                try:
                    self.axis_operator.setCurrentText(base.AXIS_BETWEEN_OPERATOR)
                    self.axis_value.setValue(0.0)
                    self.axis_secondary.setValue(100.0)
                finally:
                    self._loading_editor = previous
                self._axis_operator_changed("")
        if not self._loading_editor:
            self._refresh_axis_monitor()

    def _axis_operator_changed(self, _display_text: str) -> None:
        between = self.axis_operator.currentText() == base.AXIS_BETWEEN_OPERATOR
        self.axis_secondary_label.setVisible(between)
        self.axis_secondary.setVisible(between)
        self._editor_changed()

    def _axis_binding_changed(self, *_args) -> None:
        if self._loading_editor:
            return
        self._editor_changed()
        self._update_axis_binding_label()
        self._refresh_axis_monitor()

    def axis_semantic_names_clear_current(self) -> None:
        # Presentation cache only; rule/domain storage deliberately remains numeric.
        return

    def _update_axis_binding_label(self) -> None:
        if not hasattr(self, "axis_binding_label"):
            return
        device_id = self.axis_device.currentData()
        if not isinstance(device_id, str) or not device_id:
            self.axis_binding_label.setText("Use Detect axis and move the control you want to use.")
            return
        axis = self.axis_number.value()
        semantic = self._axis_semantic_names.get((device_id, axis))
        if semantic:
            self.axis_binding_label.setText(semantic)
        else:
            self.axis_binding_label.setText(
                f"Stored axis index {axis} (use Detect axis to identify it by name)"
            )

    def _stop_axis_monitor(self) -> None:
        timer = getattr(self, "_axis_timer", None)
        if timer is not None:
            timer.stop()
        monitor = self._axis_monitor
        self._axis_monitor = None
        self._axis_revision = -1
        if monitor is not None:
            try:
                monitor.close()
            except Exception:
                pass

    def _refresh_axis_monitor(self) -> None:
        if not hasattr(self, "axis_position_text"):
            return
        self._stop_axis_monitor()
        self.axis_position_text.setText("—")
        self.axis_position_bar.setValue(0)
        if self.source_type_combo.currentText() != "Axis" or self._live_running():
            return
        device_id = self.axis_device.currentData()
        if not isinstance(device_id, str) or not device_id:
            return
        axis = self.axis_number.value()
        try:
            monitor = AxisLiveMonitor(device_id, axis)
        except Exception:
            return
        self._axis_monitor = monitor
        self._axis_timer.start()

    def _poll_axis_position(self) -> None:
        monitor = self._axis_monitor
        if monitor is None:
            return
        device_id = self.axis_device.currentData()
        if not isinstance(device_id, str):
            return
        axis = self.axis_number.value()
        if monitor.device_id != device_id or monitor.axis_index != axis:
            self._refresh_axis_monitor()
            return
        if monitor.error() is not None:
            self._stop_axis_monitor()
            return
        semantic = monitor.axis_name()
        key = (device_id, axis)
        if semantic and self._axis_semantic_names.get(key) != semantic:
            self._axis_semantic_names[key] = semantic
            self._update_axis_binding_label()
        value, revision = monitor.latest_snapshot()
        if value is None or revision == self._axis_revision:
            return
        self._axis_revision = revision
        position_text, position_percent = axis_position_display(value)
        self.axis_position_text.setText(position_text)
        self.axis_position_bar.setValue(position_percent)
        observed = getattr(self, "_axis_position_observed", None)
        if callable(observed):
            observed(device_id, axis, value, position_percent)

    # ---------- target/effect ----------

    def _set_target_text(self, target: str) -> None:
        self.target_combo.set_targets((target,))
        self._update_test_output_state()

    def _target_changed(self, _value: str) -> None:
        self._update_test_output_state()
        if not self._loading_editor:
            self._logger.event("TARGETS", values=list(self.target_combo.targets()))
            self._set_editor_dirty(True)

    def _effect_changed(self, effect: str) -> None:
        super()._effect_changed(effect)
        self._update_test_output_state()

    # ---------- preview / live ownership ----------

    def _preview_running(self) -> bool:
        return self._preview_thread is not None and self._preview_thread.is_alive()

    def _live_running(self) -> bool:
        return self._live_thread is not None and self._live_thread.is_alive()

    def _update_test_output_state(self) -> None:
        if not hasattr(self, "preview_button"):
            return
        targets = self.target_combo.targets()
        active_editor = self._new_rule_draft or self._editing_row is not None

        def show_status(text: str, state: str = "", *, detail: str | None = None) -> None:
            self.preview_status.setText(text)
            self.preview_status.setToolTip(
                detail
                or "Preview tests the current editor values without applying the rule "
                "or starting live lighting."
            )
            self.preview_status.setProperty("edlState", state)
            style = self.preview_status.style()
            style.unpolish(self.preview_status)
            style.polish(self.preview_status)
            self.preview_status.update()

        if self._preview_running():
            self.preview_button.setText("Stop preview")
            self.preview_button.setEnabled(True)
            target = targets[0] if len(targets) == 1 else "selected light"
            show_status(f"Previewing {target}", "ok")
            return

        self.preview_button.setText("Preview output")
        single_physical = len(targets) == 1 and targets[0] in PHYSICAL_TARGETS
        live_running = self._live_running()
        self.preview_button.setEnabled(
            active_editor and single_physical and not live_running
        )

        if not active_editor:
            show_status("Select a rule")
            self.preview_button.setToolTip(
                "Select or add a rule before using Preview output."
            )
        elif live_running:
            show_status("Stop live lighting", "warning")
            self.preview_button.setToolTip(
                "Stop live lighting before using Preview output."
            )
        elif len(targets) == 0:
            show_status("Choose one light")
            self.preview_button.setToolTip(
                "Choose exactly one supported physical target to use Preview output."
            )
        elif len(targets) > 1:
            show_status(
                "Choose one light",
                "",
                detail=(
                    "Preview works on one supported physical target at a time. "
                    "Live rules may still use multiple outputs."
                ),
            )
            self.preview_button.setToolTip(
                "Preview output works on one supported physical target at a time. "
                "Multi-output rules remain fully supported when live lighting is running."
            )
        elif single_physical:
            show_status(f"Ready: {targets[0]}", "ok")
            self.preview_button.setToolTip(
                "Temporarily show the current editor effect on this physical target "
                "without applying the rule or starting the full profile."
            )
        else:
            show_status(
                "Preview unavailable",
                "warning",
                detail="This target is not supported by the current Preview output path.",
            )
            self.preview_button.setToolTip(
                "Choose one supported lighting target to use Preview output."
            )

    def _toggle_preview(self) -> None:
        if self._live_running():
            self.statusBar().showMessage(
                "Stop live lighting before previewing an effect. One Chroma session owns the devices at a time.",
                6500,
            )
            return
        super()._toggle_preview()
        self._update_test_output_state()
        self._update_profile_buttons()

    def _preview_finished(self, error: object) -> None:
        super()._preview_finished(error)
        self._update_profile_buttons()

    def _runnable_targets(self) -> set[str]:
        if self._profile is None:
            return set()
        return {
            target
            for rule in self._profile.rules
            if rule.enabled
            for target in rule.targets
            if target in PHYSICAL_TARGETS
        }

    def _available_runtime_targets(self) -> set[str]:
        """Return the selected+Available target set from Lighting devices."""
        snapshot_method = getattr(self, "_edl_availability_snapshot", None)
        if not callable(snapshot_method):
            return set()
        snapshot = snapshot_method()
        return set(snapshot.available_runtime_targets())

    def _update_profile_buttons(self) -> None:
        super()._update_profile_buttons()
        if hasattr(self, "start_lighting_button"):
            readiness_method = getattr(self, "_edl_readiness_snapshot", None)
            snapshot = readiness_method() if callable(readiness_method) else None
            if snapshot is not None:
                running = snapshot.live_running
                can_start = snapshot.can_start
                action_state = "running" if running else "ready" if can_start else "blocked"
                self.start_lighting_button.setText(
                    "Stop lighting" if running else "Start lighting"
                )
                self.start_lighting_button.setEnabled(running or can_start)
                if running:
                    tip = (
                        "Lighting is running. Stop lighting to release the active "
                        "renderer sessions through the normal restoration path."
                    )
                elif can_start:
                    tip = "Ready. Start live lighting with the currently applied profile."
                else:
                    tip = snapshot.blocked_reason or "Lighting is not ready to start."
                if self.start_lighting_button.property("edlActionState") != action_state:
                    self.start_lighting_button.setProperty("edlActionState", action_state)
                    style = self.start_lighting_button.style()
                    style.unpolish(self.start_lighting_button)
                    style.polish(self.start_lighting_button)
                    self.start_lighting_button.update()
                self.start_lighting_button.setToolTip(tip)
            else:
                # Compatibility fallback for direct, uncomposed MainWindow use.
                running = self._live_running()
                runnable = bool(self._available_runtime_targets())
                preview = self._preview_running()
                draft_pending = bool(self._editor_dirty or self._new_rule_draft)
                self.start_lighting_button.setText(
                    "Stop lighting" if running else "Start lighting"
                )
                self.start_lighting_button.setEnabled(
                    running or (runnable and not preview and not draft_pending)
                )
                self.start_lighting_button.setToolTip(
                    "Lighting is running. Stop lighting and release the active renderer sessions."
                    if running
                    else "Start live lighting with the currently applied profile."
                    if runnable and not preview and not draft_pending
                    else "Lighting is not ready to start."
                )
        if hasattr(self, "copy_button"):
            count = len(self.selected_rows())
            self.copy_button.setEnabled(count > 0)
            self.cut_button.setEnabled(count > 0)
            self.paste_button.setEnabled(bool(self._clipboard))
            self.duplicate_button.setEnabled(count == 1)

    def _toggle_live_lighting(self) -> None:
        if self._live_running():
            if self._live_stop_event is not None:
                self._live_stop_event.set()
            self.start_lighting_button.setText("Stopping…")
            self.start_lighting_button.setEnabled(False)
            return
        if self._preview_running():
            self.statusBar().showMessage(
                "Stop the effect preview before starting live lighting. One Chroma session owns the devices at a time.",
                6500,
            )
            return
        ownership_targets = self._available_runtime_targets()
        if not ownership_targets:
            QMessageBox.information(
                self,
                "No available lighting devices",
                "Select at least one Available device in Lighting devices before starting lighting.",
            )
            return
        if self._editor_dirty or self._new_rule_draft:
            self.statusBar().showMessage(
                "Apply or cancel the current rule draft before starting lighting.", 6000
            )
            return
        profile = self._profile or make_default_profile(NEW_PROFILE_NAME)
        status_path = self._elite_diagnostics.status_path
        if profile_uses_elite_status(profile) and (
            status_path is None or not status_path.is_file()
        ):
            QMessageBox.information(
                self,
                "Elite Dangerous data required",
                "This profile contains Argument Rules that need Status.json. "
                "Choose the Elite Dangerous Status.json file under "
                "Setup → Elite Dangerous data, then start lighting again.",
            )
            return
        self._stop_axis_monitor()
        stop_event = threading.Event()
        self._live_stop_event = stop_event
        self.statusBar().showMessage(
            "Lighting is running. Selected Available devices are armed for Rules, Modes and direct COVAS control.",
            8000,
        )
        self._logger.event(
            "LIVE_START",
            rules=len(profile.rules),
            ownership_targets=sorted(ownership_targets),
        )

        def worker() -> None:
            error = None
            try:
                run_profile(
                    profile,
                    stop_event,
                    ownership_targets=ownership_targets,
                    status_path=status_path,
                    govee_configuration=getattr(self, "_govee_configuration", None),
                )
            except Exception as exc:
                error = exc
            self._live_signals.finished.emit(error)

        self._live_thread = threading.Thread(target=worker, daemon=True)
        self._live_thread.start()
        self._update_profile_buttons()
        self._update_test_output_state()

    def _live_finished(self, error: object) -> None:
        self._live_thread = None
        self._live_stop_event = None
        self._update_profile_buttons()
        self._update_test_output_state()
        self._refresh_axis_monitor()
        if isinstance(error, Exception):
            message = format_run_error(error)
            self._logger.event("LIVE_ERROR", error=message)
            QMessageBox.warning(self, "Lighting stopped", message)
        else:
            self._logger.event("LIVE_STOPPED")
            self.statusBar().showMessage("Lighting stopped. Synapse can take control again.", 5000)

    def _set_logging_enabled(self, enabled: bool) -> None:
        self._logger.set_enabled(enabled)
        if enabled and self._logger.path is not None:
            self.statusBar().showMessage(
                f"Troubleshooting log enabled — {self._logger.path.name}.",
                5000,
            )
        else:
            self.statusBar().showMessage("Troubleshooting log disabled.", 4000)

    def _open_log_folder(self) -> None:
        folder = default_log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            self.statusBar().showMessage(
                f"Could not open log folder: {folder}",
                6500,
            )

    def closeEvent(self, event) -> None:  # type: ignore[override]
        mode_surface = getattr(self, "_covas_modes_workspace", None)
        mode_dirty = bool(
            mode_surface is not None
            and getattr(mode_surface, "_mode_draft_dirty", False)
        )
        draft_dirty = bool(self._editor_dirty or self._new_rule_draft or mode_dirty)

        if draft_dirty:
            answer = QMessageBox.warning(
                self,
                "Unsaved editor changes",
                "There are unapplied Rule or Scripted mode changes. "
                "Close and discard those editor changes?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                event.ignore()
                return

            # Discard is an operation, not merely permission to continue.
            # Resolve the existing draft mechanisms before any applied-data Save
            # so serialization sees only the applied workspace.
            if self._editor_dirty or self._new_rule_draft:
                self.cancel_rule_changes()
            if mode_dirty:
                cancel_mode = getattr(mode_surface, "cancel_mode_changes", None)
                if callable(cancel_mode):
                    cancel_mode()

            mode_dirty = bool(
                mode_surface is not None
                and getattr(mode_surface, "_mode_draft_dirty", False)
            )
            if self._editor_dirty or self._new_rule_draft or mode_dirty:
                event.ignore()
                return

        if self._profile_dirty:
            answer = QMessageBox.question(
                self,
                "Save profile changes?",
                "The current profile has unsaved applied changes.",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                if self._profile_path is not None:
                    self._write_profile(self._profile_path)
                else:
                    start = str(Path.cwd() / "lighting_profile.json")
                    path, _ = QFileDialog.getSaveFileName(
                        self,
                        "Save profile before closing",
                        start,
                        "Lighting profile (*.json);;All files (*)",
                    )
                    if path:
                        destination = Path(path)
                        if destination.suffix.lower() != ".json":
                            destination = destination.with_suffix(".json")
                        self._write_profile(destination)
                if self._profile_dirty:
                    # Save As may have been cancelled or saving may have failed.
                    event.ignore()
                    return

        self._stop_axis_monitor()
        # Preview/live workers own persistent Chroma sessions.  Do not allow a
        # daemon-thread process exit to bypass their normal DELETE/handback path.
        preview_stopped = request_stop_and_join(
            getattr(self, "_preview_thread", None),
            getattr(self, "_preview_stop_event", None),
        )
        live_stopped = request_stop_and_join(
            self._live_thread,
            self._live_stop_event,
        )
        if not (preview_stopped and live_stopped):
            self._logger.event(
                "CLOSE_WAITING_FOR_WORKER",
                preview_stopped=preview_stopped,
                live_stopped=live_stopped,
            )
            self.statusBar().showMessage(
                "Still releasing lighting resources. Close again after shutdown completes.",
                7000,
            )
            event.ignore()
            return
        super().closeEvent(event)


# Apply canonical Argument/categorical adapters only after MainWindow exists.
# They still run at module import time, before any window instance is created.
apply_argument_reference_ui(sys.modules[__name__])
apply_categorical_argument_inputs(sys.modules[__name__])


def _ensure_product_ui_composition() -> None:
    """Install the accepted product UI adapters before the first window exists."""
    if getattr(MainWindow, "_edl_header_disposition_applied", False):
        return

    # When this file is executed directly, Python names it __main__.  Register
    # the canonical module name before importing the composition helper so that
    # every adapter patches this exact module/class instead of importing a
    # second copy of lighting_ui_main.
    if __name__ == "__main__":
        sys.modules.setdefault("lighting_ui_main", sys.modules[__name__])

    import lighting_ui  # noqa: F401 - installs the accepted product composition

    if not getattr(MainWindow, "_edl_header_disposition_applied", False):
        raise RuntimeError("EDL product UI composition did not install correctly")


def main(argv: list[str] | None = None) -> int:
    _ensure_product_ui_composition()
    args = list(sys.argv if argv is None else argv)
    application = QApplication(args)
    from lighting_ui_tokens import apply_application_typography
    apply_application_typography(application)
    application.setApplicationName(APP_TITLE)
    application_icon = load_application_icon()
    application.setWindowIcon(application_icon)
    application.setStyleSheet(base.APP_STYLESHEET)
    window = MainWindow()
    window.setWindowIcon(application_icon)
    window.show()
    if len(args) >= 2:
        window.load_profile(Path(args[1]))
    startup_flow = getattr(window, "_edl_run_startup_flow", None)
    if callable(startup_flow):
        QTimer.singleShot(0, startup_flow)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
