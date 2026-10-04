#!/usr/bin/env python3
"""Visual zone editor for Razer's generic Chroma lighting layouts.

This editor owns reusable zone authoring only.  Whole-device control remains
implicit and always available.  The live renderer is deliberately handled by
separate modules so this UI can evolve without changing the accepted Chroma
session lifecycle.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lighting_chroma_surfaces import CHROMA_SURFACES, ChromaSurface, chroma_surface
from lighting_ui_tokens import MICRO_PT
from lighting_chroma_zone_config import (
    ChromaZone,
    ChromaZoneConfiguration,
    friendly_target_name,
    load_chroma_zone_configuration,
    parse_zone_target,
    save_chroma_zone_configuration,
    slugify_zone_name,
)


SURFACE_GUIDANCE = {
    "KEYBOARD": "Pick the keys that should belong to this zone. You can also use Press keys to add and simply press them on your keyboard.",
    "MOUSE": "These are the lighting positions Razer exposes for mice. Your mouse may use only some of them. Mouse buttons and mouse lights are separate, so pressing a mouse button cannot reliably identify one of these lighting positions.",
    "MOUSEPAD": "Pick the lighting positions you want in the zone. Razer numbers these around the edge in order.",
    "HEADSET": "Razer's general headset view exposes a left side and a right side.",
    "KEYPAD": "Pick the keypad positions you want in the zone.",
    "CHROMALINK": "Pick one or more of the five ChromaLink lighting positions.",
}


class _ZoneCellButton(QPushButton):
    dragStarted = Signal(str, bool)
    dragFinished = Signal()

    def __init__(self, cell_id: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.cell_id = cell_id
        self.setCheckable(True)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            wanted = not self.isChecked()
            self.setChecked(wanted)
            self.dragStarted.emit(self.cell_id, wanted)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ChromaZoneEditorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chroma zone setup")
        self.setMinimumSize(1040, 680)
        self.resize(1180, 760)
        self.configuration = load_chroma_zone_configuration()
        self._editing_zone_id: str | None = None
        self._cell_buttons: dict[str, _ZoneCellButton] = {}
        self._drag_active = False
        self._drag_value = True
        self._app = QApplication.instance()
        if self._app is not None:
            self._app.installEventFilter(self)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Choose a device type, then select the lights that should belong to a named zone. "
            "You can always use the whole device without creating a zone."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-weight:600;")
        root.addWidget(intro)

        surface_row = QHBoxLayout()
        surface_row.addWidget(QLabel("Device type"))
        self.surface_combo = QComboBox()
        for surface in CHROMA_SURFACES:
            self.surface_combo.addItem(surface.display_name, surface.surface_id)
        self.surface_combo.setMinimumWidth(220)
        self.surface_combo.currentIndexChanged.connect(self._surface_changed)
        surface_row.addWidget(self.surface_combo)
        surface_row.addSpacing(16)
        self.whole_note = QLabel("Whole device is always available")
        self.whole_note.setProperty("edlState", "ok")
        self.whole_note.setStyleSheet("font-weight:600;")
        surface_row.addWidget(self.whole_note)
        surface_row.addStretch(1)
        root.addLayout(surface_row)

        self.source_note = QLabel()
        self.source_note.setWordWrap(True)
        self.source_note.setProperty("edlTextRole", "secondary")
        root.addWidget(self.source_note)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_root = QVBoxLayout(left)
        left_root.setContentsMargins(0, 0, 6, 0)
        selection_actions = QHBoxLayout()
        self.selection_label = QLabel("0 selected")
        selection_actions.addWidget(self.selection_label)
        self.press_keys_button = QPushButton("Press keys to add")
        self.press_keys_button.setCheckable(True)
        self.press_keys_button.toggled.connect(self._keyboard_listening_changed)
        selection_actions.addWidget(self.press_keys_button)
        self.capture_status = QLabel("")
        self.capture_status.setProperty("edlTextRole", "secondary")
        selection_actions.addWidget(self.capture_status)
        selection_actions.addStretch(1)
        all_button = QPushButton("Select all")
        all_button.clicked.connect(self._select_all)
        selection_actions.addWidget(all_button)
        clear_button = QPushButton("Clear selection")
        clear_button.clicked.connect(self._clear_cells)
        selection_actions.addWidget(clear_button)
        left_root.addLayout(selection_actions)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.surface_host = QWidget()
        self.surface_grid = QGridLayout(self.surface_host)
        self.surface_grid.setContentsMargins(12, 12, 12, 12)
        self.surface_grid.setHorizontalSpacing(5)
        self.surface_grid.setVerticalSpacing(5)
        self.scroll.setWidget(self.surface_host)
        left_root.addWidget(self.scroll, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_root = QVBoxLayout(right)
        right_root.setContentsMargins(6, 0, 0, 0)
        title = QLabel("Saved zones")
        title.setStyleSheet("font-weight:600;")
        right_root.addWidget(title)
        self.zone_list = QListWidget()
        self.zone_list.itemSelectionChanged.connect(self._zone_selected)
        right_root.addWidget(self.zone_list, 1)

        right_root.addWidget(QLabel("Zone name"))
        self.zone_name = QLineEdit()
        self.zone_name.setPlaceholderText("e.g. WASD, Function row, Outer edge")
        right_root.addWidget(self.zone_name)

        zone_actions = QHBoxLayout()
        new_button = QPushButton("New zone")
        new_button.clicked.connect(self._new_zone)
        zone_actions.addWidget(new_button)
        self.save_button = QPushButton("Save zone")
        self.save_button.clicked.connect(self._save_zone)
        zone_actions.addWidget(self.save_button)
        self.delete_button = QPushButton("Delete zone")
        self.delete_button.clicked.connect(self._delete_zone)
        self.delete_button.setEnabled(False)
        zone_actions.addWidget(self.delete_button)
        right_root.addLayout(zone_actions)

        explanation = QLabel(
            "A light can belong to more than one zone. If two active rules try to use the same light, "
            "the lower rule in your rule list wins."
        )
        explanation.setWordWrap(True)
        explanation.setProperty("edlTextRole", "secondary")
        right_root.addWidget(explanation)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)

        self._surface_changed()

    def done(self, result: int) -> None:  # type: ignore[override]
        if self._app is not None:
            self._app.removeEventFilter(self)
        super().done(result)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if self._drag_active and event.type() == QEvent.Type.MouseMove:
            global_position = getattr(event, "globalPosition", None)
            if callable(global_position):
                widget = QApplication.widgetAt(global_position().toPoint())
                while widget is not None and not isinstance(widget, _ZoneCellButton):
                    widget = widget.parentWidget()
                if isinstance(widget, _ZoneCellButton) and widget.cell_id in self._cell_buttons:
                    widget.setChecked(self._drag_value)
        elif self._drag_active and event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_active = False
        return super().eventFilter(watched, event)

    def current_surface(self) -> ChromaSurface:
        return chroma_surface(str(self.surface_combo.currentData()))

    def _clear_grid(self) -> None:
        while self.surface_grid.count():
            item = self.surface_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cell_buttons = {}

    def _begin_drag(self, _cell_id: str, wanted: bool) -> None:
        self._drag_active = True
        self._drag_value = bool(wanted)

    def _finish_drag(self) -> None:
        self._drag_active = False

    def _surface_changed(self, _index: int = -1) -> None:
        surface = self.current_surface()
        self.source_note.setText(SURFACE_GUIDANCE[surface.surface_id])
        self.press_keys_button.setVisible(surface.surface_id == "KEYBOARD")
        self.press_keys_button.setChecked(False)
        self.capture_status.clear()
        self._editing_zone_id = None
        self.zone_name.clear()
        self._clear_grid()

        for cell in surface.cells:
            button = _ZoneCellButton(cell.cell_id, cell.label, self.surface_host)
            button.setMinimumSize(34, 30)
            if surface.surface_id == "KEYBOARD":
                button.setMinimumWidth(42)
                button.setMaximumHeight(34)
                button.setStyleSheet(
                    f"QPushButton {{ font-size:{MICRO_PT}pt; padding:2px; }}"
                )
            elif surface.surface_id == "MOUSE":
                button.setMinimumSize(58, 36)
            elif surface.surface_id == "HEADSET":
                button.setMinimumSize(130, 75)
            elif surface.surface_id == "CHROMALINK":
                button.setMinimumSize(82, 46)
            button.setToolTip(cell.tooltip)
            button.toggled.connect(self._update_selection_count)
            button.dragStarted.connect(self._begin_drag)
            button.dragFinished.connect(self._finish_drag)
            self.surface_grid.addWidget(button, cell.row, cell.column)
            self._cell_buttons[cell.cell_id] = button

        for row in range(surface.rows):
            self.surface_grid.setRowStretch(row, 1)
        for column in range(surface.columns):
            self.surface_grid.setColumnStretch(column, 1)

        self._refresh_zone_list()
        self._update_selection_count()

    def _refresh_zone_list(self) -> None:
        surface = self.current_surface()
        self.zone_list.clear()
        for zone in self.configuration.for_surface(surface.surface_id):
            item = QListWidgetItem(f"{zone.name}  ({len(zone.cells)})")
            item.setData(Qt.ItemDataRole.UserRole, zone.zone_id)
            item.setToolTip(f"{zone.name}: {len(zone.cells)} selected light(s)")
            self.zone_list.addItem(item)
        self.delete_button.setEnabled(False)

    def _selected_cells(self) -> tuple[str, ...]:
        surface = self.current_surface()
        return tuple(
            cell.cell_id
            for cell in surface.cells
            if self._cell_buttons[cell.cell_id].isChecked()
        )

    def _set_selected_cells(self, cells: tuple[str, ...]) -> None:
        selected = set(cells)
        for cell_id, button in self._cell_buttons.items():
            button.setChecked(cell_id in selected)
        self._update_selection_count()

    def _update_selection_count(self, _checked: bool = False) -> None:
        self.selection_label.setText(f"{len(self._selected_cells())} selected")

    def _select_all(self) -> None:
        for button in self._cell_buttons.values():
            button.setChecked(True)

    def _clear_cells(self) -> None:
        for button in self._cell_buttons.values():
            button.setChecked(False)

    def _new_zone(self) -> None:
        self.zone_list.clearSelection()
        self._editing_zone_id = None
        self.zone_name.clear()
        self._clear_cells()
        self.zone_name.setFocus()
        self.delete_button.setEnabled(False)

    def _zone_selected(self) -> None:
        items = self.zone_list.selectedItems()
        if not items:
            self._editing_zone_id = None
            self.delete_button.setEnabled(False)
            return
        zone_id = str(items[0].data(Qt.ItemDataRole.UserRole))
        zone = next(
            value
            for value in self.configuration.for_surface(self.current_surface().surface_id)
            if value.zone_id == zone_id
        )
        self._editing_zone_id = zone.zone_id
        self.zone_name.setText(zone.name)
        self._set_selected_cells(zone.cells)
        self.delete_button.setEnabled(True)

    def _save_zone(self) -> None:
        name = self.zone_name.text().strip()
        cells = self._selected_cells()
        if not name:
            QMessageBox.warning(self, "Zone name required", "Give this zone a name before saving it.")
            return
        if not cells:
            QMessageBox.warning(
                self,
                "Nothing selected",
                "Select at least one light or key. You can already choose Whole device in a rule without creating a zone.",
            )
            return
        try:
            zone_id = self._editing_zone_id or slugify_zone_name(name)
            zone = ChromaZone(
                surface_id=self.current_surface().surface_id,
                zone_id=zone_id,
                name=name,
                cells=cells,
            )
            self.configuration = self.configuration.replace_zone(zone)
            save_chroma_zone_configuration(self.configuration)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot save zone", str(exc))
            return
        self._editing_zone_id = zone.zone_id
        self._refresh_zone_list()
        for index in range(self.zone_list.count()):
            item = self.zone_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == zone.zone_id:
                self.zone_list.setCurrentItem(item)
                break

    def _delete_zone(self) -> None:
        if self._editing_zone_id is None:
            return
        surface = self.current_surface()
        zone = next(
            value
            for value in self.configuration.for_surface(surface.surface_id)
            if value.zone_id == self._editing_zone_id
        )
        answer = QMessageBox.question(
            self,
            "Delete zone",
            f"Delete the saved {surface.display_name} zone '{zone.name}'?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.configuration = self.configuration.delete_zone(surface.surface_id, zone.zone_id)
        save_chroma_zone_configuration(self.configuration)
        self._new_zone()
        self._refresh_zone_list()

    def _keyboard_listening_changed(self, listening: bool) -> None:
        if listening:
            self.press_keys_button.setText("Stop listening")
            self.capture_status.setText("Press keys to add them. Esc stops listening.")
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.press_keys_button.setText("Press keys to add")
            self.capture_status.clear()

    def _label_cell(self, label: str, *, prefer_numpad: bool = False) -> str | None:
        surface = self.current_surface()
        matches = [cell for cell in surface.cells if cell.label.casefold() == label.casefold()]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0].cell_id
        if prefer_numpad:
            numpad = [cell for cell in matches if cell.column >= 18]
            if len(numpad) == 1:
                return numpad[0].cell_id
        main = [cell for cell in matches if cell.column < 18]
        return main[0].cell_id if main else matches[0].cell_id

    def _keyboard_cell_for_event(self, event) -> str | None:
        key = event.key()
        modifiers = event.modifiers()
        keypad = bool(modifiers & Qt.KeyboardModifier.KeypadModifier)
        native_vk = int(event.nativeVirtualKey())

        special = {
            int(Qt.Key.Key_Escape): "R0C1",
            int(Qt.Key.Key_Tab): "R2C1",
            int(Qt.Key.Key_CapsLock): "R3C1",
            int(Qt.Key.Key_Backspace): "R1C14",
            int(Qt.Key.Key_Insert): "R1C15",
            int(Qt.Key.Key_Home): "R1C16",
            int(Qt.Key.Key_PageUp): "R1C17",
            int(Qt.Key.Key_Delete): "R2C15",
            int(Qt.Key.Key_End): "R2C16",
            int(Qt.Key.Key_PageDown): "R2C17",
            int(Qt.Key.Key_Left): "R5C15",
            int(Qt.Key.Key_Down): "R5C16",
            int(Qt.Key.Key_Right): "R5C17",
            int(Qt.Key.Key_Up): "R4C16",
            int(Qt.Key.Key_Space): "R5C7",
        }
        if key in special:
            return special[key]
        if int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F12):
            return f"R0C{3 + key - int(Qt.Key.Key_F1)}"

        if key in {int(Qt.Key.Key_Return), int(Qt.Key.Key_Enter)}:
            return "R4C21" if keypad else "R3C14"
        if key == int(Qt.Key.Key_Control):
            return "R5C14" if native_vk == 0xA3 else "R5C1"
        if key == int(Qt.Key.Key_Shift):
            return "R4C14" if native_vk == 0xA1 else "R4C1"
        if key == int(Qt.Key.Key_Alt):
            return "R5C11" if native_vk == 0xA5 else "R5C3"
        if key == int(Qt.Key.Key_Meta):
            return "R5C2"

        if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
            return self._label_cell(chr(ord("A") + key - int(Qt.Key.Key_A)))
        if int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
            return self._label_cell(chr(ord("0") + key - int(Qt.Key.Key_0)), prefer_numpad=keypad)

        keypad_labels = {
            int(Qt.Key.Key_Slash): "/",
            int(Qt.Key.Key_Asterisk): "*",
            int(Qt.Key.Key_Minus): "-",
            int(Qt.Key.Key_Plus): "+",
            int(Qt.Key.Key_Period): ".",
        }
        if keypad and key in keypad_labels:
            return self._label_cell(keypad_labels[key], prefer_numpad=True)
        return None

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if self.current_surface().surface_id == "KEYBOARD" and self.press_keys_button.isChecked():
            if event.key() == int(Qt.Key.Key_Escape):
                self.press_keys_button.setChecked(False)
                event.accept()
                return
            cell_id = self._keyboard_cell_for_event(event)
            if cell_id is not None and cell_id in self._cell_buttons:
                self._cell_buttons[cell_id].setChecked(True)
                self.capture_status.setText(f"Added {self._cell_buttons[cell_id].text()}")
            else:
                self.capture_status.setText("That key is not clear in this layout. Click it in the picture instead.")
            event.accept()
            return
        super().keyPressEvent(event)


def apply_chroma_zone_ui(ui_module: Any) -> None:
    """Add direct Chroma-zone setup without changing the live renderer."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_chroma_zone_ui_applied", False):
        return

    previous_init = window_class.__init__

    def open_chroma_zone_setup(self) -> None:
        dialog = ChromaZoneEditorDialog(self)
        dialog.exec()
        # A newly saved zone should be available to the rule editor immediately.
        self._update_profile_buttons()

    def __init__(self) -> None:
        previous_init(self)
        button = QPushButton("Chroma zones")
        button.setToolTip(
            "Create named areas such as WASD or Function row. Whole device is always available."
        )
        button.setToolTipDuration(30000)
        button.clicked.connect(lambda: open_chroma_zone_setup(self))
        self.chroma_zone_setup_button = button

        central = self.centralWidget()
        outer = central.layout() if central is not None else None
        header = outer.itemAt(0).layout() if outer is not None and outer.count() else None
        runtime = getattr(header, "runtime_row", None)
        govee = getattr(header, "govee_button", None)
        live_data = getattr(header, "live_data_button", None)
        if runtime is not None and govee is not None:
            runtime.removeWidget(govee)
            group = QWidget(self)
            row = QHBoxLayout(group)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(7)
            label = QLabel("Lighting setup")
            label.setStyleSheet("font-weight:600;")
            row.addWidget(label)
            row.addWidget(govee)
            row.addWidget(button)
            self.lighting_setup_group = group
            index = runtime.indexOf(live_data) if live_data is not None else runtime.count()
            runtime.insertWidget(max(0, index), group)
        elif runtime is not None:
            runtime.addWidget(button)
        else:
            button.setParent(self)
            button.hide()

    window_class.__init__ = __init__
    window_class.open_chroma_zone_setup = open_chroma_zone_setup
    window_class._edl_chroma_zone_ui_applied = True


# Final Chroma setup presentation belongs to the zone-authoring owner.
def _restore_setup_buttons(window) -> None:
    central = window.centralWidget()
    outer = central.layout() if central is not None else None
    header = outer.itemAt(0).layout() if outer is not None and outer.count() else None
    runtime = getattr(header, "runtime_row", None)
    if runtime is None:
        return

    rate_label = getattr(window, "render_rate_label", None)
    rate_combo = getattr(window, "render_rate_combo", None)
    start = getattr(window, "start_lighting_button", None)
    live_data = getattr(header, "live_data_button", None)
    govee = getattr(header, "govee_button", None)
    chroma = getattr(window, "chroma_zone_setup_button", None)
    old_group = getattr(window, "lighting_setup_group", None)

    known = {
        widget
        for widget in (rate_label, rate_combo, start, live_data, govee, chroma, old_group)
        if isinstance(widget, QWidget)
    }
    preserved: list[QWidget] = []

    while runtime.count():
        item = runtime.takeAt(0)
        widget = item.widget()
        if widget is not None and widget not in known:
            preserved.append(widget)

    if isinstance(old_group, QWidget):
        group_layout = old_group.layout()
        if group_layout is not None:
            if isinstance(govee, QWidget):
                group_layout.removeWidget(govee)
            if isinstance(chroma, QWidget):
                group_layout.removeWidget(chroma)
        old_group.hide()
        old_group.deleteLater()

    for widget in (rate_label, rate_combo, start, live_data, govee, chroma):
        if isinstance(widget, QWidget):
            widget.setVisible(True)

    if isinstance(rate_label, QWidget):
        runtime.addWidget(rate_label)
    if isinstance(rate_combo, QWidget):
        runtime.addWidget(rate_combo)
    runtime.addStretch(1)
    if isinstance(start, QWidget):
        runtime.addWidget(start)
    if isinstance(live_data, QWidget):
        runtime.addWidget(live_data)
    if isinstance(govee, QWidget):
        runtime.addWidget(govee)
    if isinstance(chroma, QWidget):
        runtime.addWidget(chroma)
    for widget in preserved:
        runtime.addWidget(widget)


def _compact_keyboard_dialog(dialog) -> None:
    try:
        surface = dialog.current_surface()
    except Exception:
        return
    if surface.surface_id != "KEYBOARD":
        return

    dialog.setMinimumSize(900, 570)
    dialog.resize(1060, 650)
    dialog.surface_grid.setContentsMargins(6, 6, 6, 6)
    dialog.surface_grid.setHorizontalSpacing(3)
    dialog.surface_grid.setVerticalSpacing(3)

    for row in range(surface.rows):
        dialog.surface_grid.setRowStretch(row, 0)
    for column in range(surface.columns):
        dialog.surface_grid.setColumnStretch(column, 0)

    for cell in surface.cells:
        button = dialog._cell_buttons.get(cell.cell_id)
        if button is None:
            continue
        # Generic Razer canvas positions without a human key identity stay in
        # the capability/configuration model but are blank spacing in the UI.
        anonymous = cell.label == f"{cell.row},{cell.column}"
        button.setVisible(not anonymous)
        if anonymous:
            continue
        if button.text() == "Locale":
            button.setText("Intl")
        button.setMinimumSize(31, 26)
        button.setMaximumSize(47, 28)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setStyleSheet(
            f"QPushButton {{ font-size:{MICRO_PT}pt; padding:1px 3px; }}"
        )


def apply_chroma_presentation(ui_module: Any, zone_ui_module: Any) -> None:
    """Apply the final presentation correction once."""
    window_class = ui_module.MainWindow
    dialog_class = zone_ui_module.ChromaZoneEditorDialog
    if getattr(window_class, "_edl_chroma_presentation_applied", False):
        return

    previous_window_init = window_class.__init__
    previous_dialog_init = dialog_class.__init__
    previous_surface_changed = dialog_class._surface_changed

    def window_init(self) -> None:
        previous_window_init(self)
        _restore_setup_buttons(self)

    def dialog_init(self, parent=None) -> None:
        previous_dialog_init(self, parent)
        _compact_keyboard_dialog(self)

    def surface_changed(self, index: int = -1) -> None:
        previous_surface_changed(self, index)
        _compact_keyboard_dialog(self)

    window_class.__init__ = window_init
    dialog_class.__init__ = dialog_init
    dialog_class._surface_changed = surface_changed
    window_class._edl_chroma_presentation_applied = True


# Saved Chroma-zone target authoring belongs to the same zone UI owner.
ZONE_SURFACES = ("KEYBOARD", "MOUSE")


def apply_chroma_zone_rule_ui(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_chroma_zone_rule_ui_applied", False):
        return

    previous_build_target_section = window_class._build_target_section
    previous_load_draft = window_class._load_draft
    previous_build_rule = window_class._build_rule_from_editor
    previous_target_changed = window_class._target_changed
    previous_set_editor_enabled = window_class._set_editor_enabled
    previous_update_profile_buttons = window_class._update_profile_buttons
    previous_runnable_targets = window_class._runnable_targets

    def configuration(self) -> ChromaZoneConfiguration:
        try:
            return load_chroma_zone_configuration()
        except Exception:
            return ChromaZoneConfiguration()

    def current_zone_targets(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for surface_id, boxes in getattr(self, "_chroma_zone_boxes", {}).items():
            result[surface_id] = {
                target for target, box in boxes.items() if box.isChecked()
            }
        return result

    def update_button_text(self, surface_id: str) -> None:
        button = getattr(self, "_chroma_zone_buttons", {}).get(surface_id)
        if button is None:
            return
        config = configuration(self)
        boxes = getattr(self, "_chroma_zone_boxes", {}).get(surface_id, {})
        selected = [
            zone.name
            for zone in config.for_surface(surface_id)
            if (box := boxes.get(zone.target)) is not None and box.isChecked()
        ]
        if not selected:
            button.setText("ALL")
        elif len(selected) == 1:
            button.setText(selected[0])
        else:
            button.setText(f"{selected[0]} + {len(selected) - 1} more")

    def sync_all_action(self, surface_id: str) -> None:
        button = getattr(self, "_chroma_zone_buttons", {}).get(surface_id)
        boxes = getattr(self, "_chroma_zone_boxes", {}).get(surface_id, {})
        action = getattr(button, "_edl_all_action", None) if button is not None else None
        if not isinstance(action, QAction):
            return
        any_zone = any(box.isChecked() for box in boxes.values())
        action.blockSignals(True)
        action.setChecked(not any_zone)
        action.blockSignals(False)
        if not any_zone:
            button.setText("ALL")

    def choose_all(self, surface_id: str, checked: bool) -> None:
        button = getattr(self, "_chroma_zone_buttons", {}).get(surface_id)
        boxes = getattr(self, "_chroma_zone_boxes", {}).get(surface_id, {})
        action = getattr(button, "_edl_all_action", None) if button is not None else None
        if not isinstance(action, QAction):
            return
        if not checked:
            if not any(box.isChecked() for box in boxes.values()):
                sync_all_action(self, surface_id)
            return
        for box in boxes.values():
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
        sync_all_action(self, surface_id)
        if not getattr(self, "_loading_editor", False):
            self._set_editor_dirty(True)
            self._update_test_output_state()

    def zone_changed(self, surface_id: str, target: str, checked: bool) -> None:
        update_button_text(self, surface_id)
        sync_all_action(self, surface_id)
        if not getattr(self, "_loading_editor", False):
            self._set_editor_dirty(True)
            self._update_test_output_state()

    def rebuild_zone_controls(self, preserve: dict[str, set[str]] | None = None) -> None:
        host = getattr(self, "_chroma_zone_host", None)
        layout = getattr(self, "_chroma_zone_layout", None)
        if host is None or layout is None:
            return
        if preserve is None:
            preserve = current_zone_targets(self)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._chroma_zone_boxes = {}
        self._chroma_zone_buttons = {}
        self._chroma_zone_rows = {}
        config = configuration(self)
        selector = getattr(self, "target_combo", None)
        if selector is not None and hasattr(selector, "_ensure_target"):
            configured_targets = set(config.target_map())
            for zone in config.zones:
                selector._ensure_target(zone.target)
                box = selector._boxes[zone.target]
                box.setText(friendly_target_name(config, zone.target))
                box.setToolTip(
                    "Saved Chroma zone. Select it directly as a lighting target, "
                    "or select the whole device and use its area control below."
                )
                box.setProperty("edl_chroma_zone_target", True)
            for target, box in tuple(selector._boxes.items()):
                if (
                    bool(box.property("edl_chroma_zone_target"))
                    and target not in configured_targets
                    and not box.isChecked()
                ):
                    box.setVisible(False)
        for surface_id in ZONE_SURFACES:
            zones = config.for_surface(surface_id)
            row_widget = QWidget(host)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(9)
            label = QLabel(f"{surface_id.title()} area")
            row.addWidget(label)
            button = QToolButton(row_widget)
            button.setMinimumWidth(170)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(button)
            button.setMenu(menu)
            all_action = QAction("ALL", menu)
            all_action.setCheckable(True)
            all_action.setToolTip(f"Use the entire {surface_id.title()} device.")
            all_action.triggered.connect(
                lambda checked=False, sid=surface_id: choose_all(self, sid, checked)
            )
            menu.addAction(all_action)
            button._edl_all_action = all_action
            boxes: dict[str, QCheckBox] = {}
            wanted = preserve.get(surface_id, set())
            if zones:
                for zone in zones:
                    box = QCheckBox(zone.name, menu)
                    box.setChecked(zone.target in wanted)
                    box.setToolTip(f"Use the saved {surface_id.title()} zone '{zone.name}'.")
                    box.toggled.connect(
                        lambda checked=False, sid=surface_id, target=zone.target: zone_changed(
                            self, sid, target, checked
                        )
                    )
                    action = QWidgetAction(menu)
                    action.setDefaultWidget(box)
                    menu.addAction(action)
                    boxes[zone.target] = box
            if boxes:
                button.setToolTip(
                    "ALL uses the complete device. Tick one or more saved zones to use only those areas."
                )
            else:
                button.setToolTip(
                    "ALL uses the complete device. No custom zones are saved yet; use Chroma zones if you want to create one."
                )
            row.addWidget(button)
            hint = QLabel("No saved zones" if not zones else "")
            hint.setStyleSheet("color:#999999;")
            row.addWidget(hint)
            row.addStretch(1)
            self._chroma_zone_boxes[surface_id] = boxes
            self._chroma_zone_buttons[surface_id] = button
            self._chroma_zone_rows[surface_id] = row_widget
            layout.addWidget(row_widget)
            update_button_text(self, surface_id)
            sync_all_action(self, surface_id)
        if selector is not None:
            refresh = getattr(selector, "_refresh_button", None)
            if callable(refresh):
                refresh()
        update_zone_control_state(self)

    def update_zone_control_state(self) -> None:
        selector = getattr(self, "target_combo", None)
        if selector is None:
            return
        selected = set(selector.targets())
        enabled = bool(getattr(self, "_chroma_zone_editor_enabled", False))
        any_visible = False
        for surface_id in ZONE_SURFACES:
            row = getattr(self, "_chroma_zone_rows", {}).get(surface_id)
            visible = surface_id in selected
            if row is not None:
                row.setVisible(visible)
            any_visible = any_visible or visible
            button = getattr(self, "_chroma_zone_buttons", {}).get(surface_id)
            if button is not None:
                button.setEnabled(enabled and visible)
                action = getattr(button, "_edl_all_action", None)
                if isinstance(action, QAction):
                    action.setEnabled(enabled and visible)
            for box in getattr(self, "_chroma_zone_boxes", {}).get(surface_id, {}).values():
                box.setEnabled(enabled and visible)
        host = getattr(self, "_chroma_zone_host", None)
        if host is not None:
            host.setVisible(any_visible)

    def build_target_section(self):
        section = previous_build_target_section(self)
        self._chroma_zone_host = QWidget(section)
        self._chroma_zone_layout = QVBoxLayout(self._chroma_zone_host)
        self._chroma_zone_layout.setContentsMargins(0, 2, 0, 0)
        self._chroma_zone_layout.setSpacing(5)
        section.root.addWidget(self._chroma_zone_host)
        rebuild_zone_controls(self)
        return section

    def set_editor_enabled(self, enabled: bool) -> None:
        previous_set_editor_enabled(self, enabled)
        self._chroma_zone_editor_enabled = bool(enabled)
        update_zone_control_state(self)

    def load_draft(self, rule, *, title: str) -> None:
        original_targets = tuple(rule.targets)
        selected_zones: dict[str, set[str]] = {}
        collapsed: list[str] = []
        seen_surfaces: set[str] = set()
        for target in original_targets:
            parsed = parse_zone_target(target)
            if parsed is None or parsed[0] not in ZONE_SURFACES:
                collapsed.append(target)
                continue
            surface_id, _zone_id = parsed
            selected_zones.setdefault(surface_id, set()).add(target)
            if surface_id not in seen_surfaces:
                collapsed.append(surface_id)
                seen_surfaces.add(surface_id)
        if not collapsed:
            collapsed = list(original_targets)
        collapsed_rule = replace(rule, target=collapsed[0], targets=tuple(collapsed))
        previous_load_draft(self, collapsed_rule, title=title)
        previous_loading = self._loading_editor
        self._loading_editor = True
        try:
            rebuild_zone_controls(self, selected_zones)
        finally:
            self._loading_editor = previous_loading
        update_zone_control_state(self)

    def build_rule_from_editor(self):
        rule = previous_build_rule(self)
        selected_by_surface = current_zone_targets(self)
        expanded: list[str] = []
        for target in rule.targets:
            if target in ZONE_SURFACES and selected_by_surface.get(target):
                config = configuration(self)
                wanted = selected_by_surface[target]
                ordered = [zone.target for zone in config.for_surface(target) if zone.target in wanted]
                expanded.extend(ordered)
            else:
                expanded.append(target)
        if not expanded:
            raise ValueError("Select at least one lighting target")
        if len(set(expanded)) != len(expanded):
            raise ValueError("lighting targets must not contain duplicates")
        return replace(rule, target=expanded[0], targets=tuple(expanded))

    def target_changed(self, value: str) -> None:
        previous_target_changed(self, value)
        update_zone_control_state(self)

    def runnable_targets(self) -> set[str]:
        result = set(previous_runnable_targets(self))
        profile = getattr(self, "_profile", None)
        if profile is None:
            return result
        configured = set(configuration(self).target_map())
        result.update(
            target
            for rule in profile.rules
            if rule.enabled
            for target in rule.targets
            if target in configured
        )
        return result

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        preserve = current_zone_targets(self)
        config_targets = set(configuration(self).target_map())
        current_targets = {
            target
            for boxes in getattr(self, "_chroma_zone_boxes", {}).values()
            for target in boxes
        }
        expected_targets = {
            zone.target
            for surface_id in ZONE_SURFACES
            for zone in configuration(self).for_surface(surface_id)
        }
        if current_targets != config_targets & expected_targets:
            rebuild_zone_controls(self, preserve)
        update_zone_control_state(self)

    window_class._build_target_section = build_target_section
    window_class._set_editor_enabled = set_editor_enabled
    window_class._load_draft = load_draft
    window_class._build_rule_from_editor = build_rule_from_editor
    window_class._target_changed = target_changed
    window_class._runnable_targets = runnable_targets
    window_class._update_profile_buttons = update_profile_buttons
    window_class._edl_chroma_zone_rule_ui_applied = True
