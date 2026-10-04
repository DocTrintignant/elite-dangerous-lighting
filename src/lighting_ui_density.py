#!/usr/bin/env python3
"""Shared presentation density for EDL authoring workspaces.

Presentation only. This module centralizes section/editor spacing and compact
output-list sizing so Rules and Scripted modes cannot drift independently.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lighting_chromalink_cells import CHROMALINK_TARGET
from lighting_ui_tokens import SPACE_SM, SPACE_MD


SECTION_MARGIN_H = 10
SECTION_MARGIN_TOP = 8
SECTION_MARGIN_BOTTOM = 9
SECTION_SPACING = SPACE_MD - 2

EDITOR_MARGIN_LEFT = 2
EDITOR_MARGIN_TOP = 2
EDITOR_MARGIN_RIGHT = SPACE_SM
EDITOR_MARGIN_BOTTOM = SPACE_SM
EDITOR_STACK_SPACING = SPACE_MD - 2

MAX_VISIBLE_OUTPUT_ROWS = 3
MIN_OUTPUT_ROW_HEIGHT = 28
OUTPUT_EXTRA_VERTICAL_PADDING = 4


def compact_section(widget, *, maximum_height: bool = False):
    """Apply the canonical authoring-section density to one SectionBox."""
    layout = widget.layout()
    if layout is not None:
        layout.setContentsMargins(
            SECTION_MARGIN_H,
            SECTION_MARGIN_TOP,
            SECTION_MARGIN_H,
            SECTION_MARGIN_BOTTOM,
        )
        layout.setSpacing(SECTION_SPACING)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    if maximum_height:
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    return widget


def compact_editor_scroll(scroll) -> None:
    """Apply canonical content margins/spacing to an editor scroll area."""
    content = scroll.widget() if scroll is not None else None
    layout = content.layout() if content is not None else None
    if layout is None:
        return
    layout.setContentsMargins(
        EDITOR_MARGIN_LEFT,
        EDITOR_MARGIN_TOP,
        EDITOR_MARGIN_RIGHT,
        EDITOR_MARGIN_BOTTOM,
    )
    layout.setSpacing(EDITOR_STACK_SPACING)


def compact_output_list(widget: QListWidget) -> int:
    """Size an output list to 1–3 visible rows and return the visible-row count."""
    count = widget.count()
    visible_rows = max(1, min(count, MAX_VISIBLE_OUTPUT_ROWS))

    if count:
        widget.doItemsLayout()
        row_height = widget.sizeHintForRow(0)
    else:
        row_height = widget.fontMetrics().height() + 12
    row_height = max(MIN_OUTPUT_ROW_HEIGHT, int(row_height))

    frame = widget.frameWidth() * 2
    height = frame + visible_rows * row_height + OUTPUT_EXTRA_VERTICAL_PADDING
    widget.setFixedHeight(height)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    widget.setProperty("edlVisibleOutputRows", visible_rows)
    widget.updateGeometry()
    return visible_rows


def _resize_output_list(window) -> None:
    widget = getattr(window, "output_list", None)
    if not isinstance(widget, QListWidget):
        return

    try:
        compact_output_list(widget)
    except RuntimeError:
        # Qt may already have destroyed the native QListWidget during teardown.
        return

    target_section = getattr(window, "_edl_compact_target_section", None)
    if target_section is not None:
        target_section.updateGeometry()
    scroll = getattr(window, "editor_scroll", None)
    if scroll is not None and scroll.widget() is not None:
        scroll.widget().updateGeometry()


def apply_compact_outputs_ui(ui_module: Any) -> None:
    """Make the normal EDL output list height content-aware."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_compact_outputs_ui_applied", False):
        return

    previous_build_target = window_class._build_target_section

    def build_target_section(self):
        section = previous_build_target(self)
        widget = getattr(self, "output_list", None)
        if isinstance(widget, QListWidget):
            model = widget.model()
            model.rowsInserted.connect(lambda *_args: _resize_output_list(self))
            model.rowsRemoved.connect(lambda *_args: _resize_output_list(self))
            model.modelReset.connect(lambda *_args: _resize_output_list(self))
            model.layoutChanged.connect(lambda *_args: _resize_output_list(self))
            _resize_output_list(self)
        return section

    window_class._build_target_section = build_target_section
    window_class._edl_compact_outputs_ui_applied = True


def _tighten_section(widget):
    return compact_section(widget)


def _cap_section(widget):
    return compact_section(widget, maximum_height=True)


def _tighten_editor_stack(window) -> None:
    compact_editor_scroll(getattr(window, "editor_scroll", None))


def _sync_chromalink_row(window) -> None:
    selector = getattr(window, "target_combo", None)
    if selector is None or not hasattr(selector, "targets"):
        return
    selected = CHROMALINK_TARGET in tuple(selector.targets())

    label = getattr(window, "chromalink_cell_label", None)
    if label is not None:
        label.setVisible(selected)
    help_button = getattr(window, "chromalink_cell_help", None)
    if help_button is not None:
        help_button.setVisible(selected)
    for box in getattr(window, "chromalink_cell_boxes", {}).values():
        box.setVisible(selected)

    target_section = getattr(window, "_edl_compact_target_section", None)
    if target_section is not None:
        target_section.updateGeometry()
    editor = getattr(window, "editor_scroll", None)
    if editor is not None and editor.widget() is not None:
        editor.widget().updateGeometry()


def apply_compact_rule_editor(ui_module: Any) -> None:
    """Make normal EDL sections consume content height rather than spare height."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_compact_rule_editor_applied", False):
        return

    previous_init = window_class.__init__
    previous_build_rule = window_class._build_rule_section
    previous_build_source = window_class._build_source_section
    previous_build_target = window_class._build_target_section
    previous_build_effect = window_class._build_effect_section
    previous_target_changed = window_class._target_changed
    previous_load_draft = window_class._load_draft

    def build_rule_section(self):
        section = _cap_section(previous_build_rule(self))
        self._edl_compact_rule_section = section
        return section

    def build_source_section(self):
        section = _cap_section(previous_build_source(self))
        self._edl_compact_source_section = section
        return section

    def build_target_section(self):
        section = _cap_section(previous_build_target(self))
        self._edl_compact_target_section = section
        return section

    def build_effect_section(self):
        section = _tighten_section(previous_build_effect(self))
        self._edl_compact_effect_section = section
        return section

    def target_changed(self, value: str) -> None:
        previous_target_changed(self, value)
        _sync_chromalink_row(self)

    def load_draft(self, rule, *, title: str) -> None:
        previous_load_draft(self, rule, title=title)
        _sync_chromalink_row(self)

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        _tighten_editor_stack(self)
        _sync_chromalink_row(self)

    window_class.__init__ = init
    window_class._build_rule_section = build_rule_section
    window_class._build_source_section = build_source_section
    window_class._build_target_section = build_target_section
    window_class._build_effect_section = build_effect_section
    window_class._target_changed = target_changed
    window_class._load_draft = load_draft
    window_class._edl_compact_rule_editor_applied = True


class CurrentPageStack(QWidget):
    """Small QStackedWidget-compatible host that sizes from the visible page only."""

    currentChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pages: list[QWidget] = []
        self._current_index = -1
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def addWidget(self, page: QWidget) -> int:
        index = len(self._pages)
        self._pages.append(page)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._layout.addWidget(page)
        if self._current_index < 0:
            self._current_index = 0
            page.show()
        else:
            page.hide()
        self.updateGeometry()
        return index

    def count(self) -> int:
        return len(self._pages)

    def widget(self, index: int) -> QWidget | None:
        if 0 <= index < len(self._pages):
            return self._pages[index]
        return None

    def currentIndex(self) -> int:
        return self._current_index

    def currentWidget(self) -> QWidget | None:
        return self.widget(self._current_index)

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._pages):
            return
        if index == self._current_index:
            current = self.currentWidget()
            if current is not None:
                current.show()
                current.updateGeometry()
            self.updateGeometry()
            return

        for page_index, page in enumerate(self._pages):
            page.setVisible(page_index == index)
        self._current_index = index
        current = self.currentWidget()
        if current is not None:
            current.updateGeometry()
        self.updateGeometry()
        self.currentChanged.emit(index)

    def sizeHint(self):  # type: ignore[override]
        current = self.currentWidget()
        return current.sizeHint() if current is not None else super().sizeHint()

    def minimumSizeHint(self):  # type: ignore[override]
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else super().minimumSizeHint()


def _cap_argument_page_to_content(window, panel) -> None:
    """Prevent the Argument rows host from becoming an elastic vertical spacer."""
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    layout = panel.layout()
    if layout is not None:
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    host = getattr(window, "argument_rows_host", None)
    if host is not None:
        host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        host_layout = host.layout()
        if host_layout is not None:
            host_layout.setAlignment(Qt.AlignmentFlag.AlignTop)


def apply_compact_source_ui(ui_module: Any) -> None:
    """Patch only construction/layout policy of the SOURCE section."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_compact_source_ui_applied", False):
        return

    previous_build_argument_source = window_class._build_argument_source

    def build_argument_source(self):
        panel = previous_build_argument_source(self)
        _cap_argument_page_to_content(self, panel)
        return panel

    def build_source_section(self):
        section = self._section(
            "SOURCE",
            "what activates this rule",
            ui_module.SECTION_HELP["SOURCE"],
        )
        row = QHBoxLayout()
        row.addWidget(QLabel("Source type"))
        self.source_type_combo = QComboBox()
        self.source_type_combo.setMinimumWidth(180)
        self.source_type_combo.addItems(("Argument", "Keyboard", "Button", "Axis"))
        self.source_type_combo.currentTextChanged.connect(self._source_type_changed)
        row.addWidget(self.source_type_combo)
        row.addStretch(1)
        section.root.addLayout(row)

        self.source_stack = CurrentPageStack()
        pages = (
            self._build_argument_source(),
            self._build_keyboard_source(),
            self._build_button_source(),
            self._build_axis_source(),
        )
        for page in pages:
            self.source_stack.addWidget(page)
        section.root.addWidget(self.source_stack)
        return section

    window_class._build_argument_source = build_argument_source
    window_class._build_source_section = build_source_section
    window_class._edl_compact_source_ui_applied = True
