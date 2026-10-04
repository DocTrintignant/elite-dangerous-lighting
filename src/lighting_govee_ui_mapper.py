#!/usr/bin/env python3
"""Composed Govee setup mapper.

This module owns the complete operator-facing native-Govee setup dialog:
position editing, guided discovery, evidence-gated calibration, multi-device
staging, prototype normalization, and disconnected-section orientation.
Discovery truth, configuration formats, realtime transport and renderers remain
owned by their existing non-UI modules.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import lighting_govee_ui as legacy
from lighting_ui_tokens import DIALOG_TITLE_PT, HELPER_PT, TEXT_SECONDARY
from lighting_govee_calibration import GoveeCalibrationDialog
from lighting_govee_config import (
    GEOMETRY_USER_CALIBRATED,
    GEOMETRY_VERIFIED_MODEL,
    GoveeConfiguration,
    GoveeEnhancedDevice,
    GoveeZone,
    SECTION_FORWARD,
    SECTION_REVERSED,
    VERIFIED_SEGMENT_COUNTS,
    contiguous_segment_sections,
    device_id_for,
    make_h61c3_device,
    save_govee_configuration,
)
from lighting_govee_discovery import discover_govee_devices
from lighting_govee_geometry_hints import geometry_hint_for
from lighting_govee_orientation import load_visual_reversed, save_visual_reversed
from lighting_govee_transport import GoveeRealtimeSession
from lighting_settings import app_settings


TRACE_UNSELECTED = (0, 0, 12)
TRACE_INTERVAL_MS = 50
TRACE_ACTIVATION_WAIT_SECONDS = 0.100
DEFAULT_TEST_COLOUR = (255, 255, 255)
VISUAL_REVERSE_KEY = "govee/h61c3_reverse_visual_order"


class SegmentStrip(QWidget):
    """Directly editable left-to-right view of the 42 user-facing positions."""

    selectionChanged = Signal(object)

    def __init__(self, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.count = count
        self.selected: set[int] = set()
        self.claimed: set[int] = set()
        self._drag_mode: bool | None = None
        self._last_drag_position: int | None = None
        self.setMinimumHeight(112)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(
            "Click one position to toggle it, or hold the left mouse button and drag across several positions."
        )

    def set_state(self, selected: set[int], claimed: set[int]) -> None:
        self.selected = set(selected)
        self.claimed = set(claimed)
        self.update()

    def _strip_rect(self) -> QRect:
        return self.rect().adjusted(1, 30, -1, -40)

    def _position_at(self, x: float) -> int | None:
        strip = self._strip_rect()
        if strip.width() <= 0 or x < strip.left() or x > strip.right():
            return None
        fraction = (x - strip.left()) / max(1, strip.width())
        position = int(fraction * self.count) + 1
        return max(1, min(self.count, position))

    def _apply_drag_position(self, position: int) -> None:
        if position in self.claimed or position == self._last_drag_position:
            return
        if self._drag_mode:
            self.selected.add(position)
        else:
            self.selected.discard(position)
        self._last_drag_position = position
        self.update()
        self.selectionChanged.emit(set(self.selected))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        position = self._position_at(event.position().x())
        if position is None or position in self.claimed:
            event.accept()
            return
        self._drag_mode = position not in self.selected
        self._last_drag_position = None
        self._apply_drag_position(position)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_mode is not None and event.buttons() & Qt.MouseButton.LeftButton:
            position = self._position_at(event.position().x())
            if position is not None:
                self._apply_drag_position(position)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = None
            self._last_drag_position = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        strip = self._strip_rect()
        gap = 1
        width = max(1.0, (strip.width() - gap * (self.count - 1)) / self.count)
        painter.setPen(QPen(QColor("#777777")))

        for index in range(self.count):
            position = index + 1
            x = strip.left() + index * (width + gap)
            cell = QRect(int(x), strip.top(), max(1, int(width)), strip.height())
            if position in self.selected:
                painter.fillRect(cell, QColor("#D9EFFF"))
            elif position in self.claimed:
                painter.fillRect(cell, QColor("#555555"))
            else:
                painter.fillRect(cell, QColor("#252525"))
            painter.drawRect(cell)

        painter.setPen(QColor("#D0D0D0"))
        painter.drawText(
            self.rect(), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, "Position 1"
        )
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            f"Position {self.count}",
        )
        painter.setPen(QColor("#999999"))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            "Click positions individually, or drag across several",
        )


class AccessibleGoveeZoneMapperDialog(QDialog):
    """Task-oriented editor for named regions of one supported Govee device."""

    def __init__(
        self,
        configuration: GoveeConfiguration,
        *,
        used_targets: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Govee zone setup")
        self.setMinimumSize(940, 670)
        self.resize(1100, 760)
        self._configuration = configuration
        self._used_targets = set() if used_targets is None else set(used_targets)
        self._device = next(
            (d for d in configuration.devices if d.device_id == legacy.DEFAULT_DEVICE_ID),
            configuration.devices[0] if configuration.devices else make_h61c3_device(),
        )
        self._zones = list(self._device.zones)
        self._editing_zone_id: str | None = None
        self._selected_positions: set[int] = set()
        self._test_colour = DEFAULT_TEST_COLOUR
        self._trace_session: GoveeRealtimeSession | None = None
        self._trace_timer = QTimer(self)
        self._trace_timer.setInterval(TRACE_INTERVAL_MS)
        self._trace_timer.timeout.connect(self._render_trace)

        settings = app_settings()
        stored_reverse = settings.value(VISUAL_REVERSE_KEY, True)
        if isinstance(stored_reverse, str):
            self._reverse_visual_order = stored_reverse.strip().lower() not in {"0", "false", "no"}
        else:
            self._reverse_visual_order = bool(stored_reverse)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel("Set up named zones on a Govee light")
        title.setStyleSheet(f"font-size:{DIALOG_TITLE_PT}pt; font-weight:600;")
        root.addWidget(title)
        intro = QLabel(
            "Create named areas of a supported Govee light so Rules can target them independently."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{TEXT_SECONDARY};")
        root.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(14)

        # Left sidebar: choose/configure the physical Govee, then manage its
        # saved logical zones. Device setup no longer consumes the full dialog
        # width above the actual mapping task.
        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.Shape.StyledPanel)
        left_panel.setMinimumWidth(300)
        left_panel.setMaximumWidth(380)
        self._device_sidebar = left_panel
        left = QVBoxLayout(left_panel)
        left.setContentsMargins(12, 12, 12, 12)
        left.setSpacing(8)

        device_heading = QLabel("Device")
        device_heading.setStyleSheet("font-weight:600;")
        left.addWidget(device_heading)

        self.device_panel = QFrame(left_panel)
        device_layout = QVBoxLayout(self.device_panel)
        device_layout.setContentsMargins(0, 0, 0, 4)
        device_layout.setSpacing(6)

        device_layout.addWidget(QLabel("Govee device"))
        self.device_combo = QComboBox()
        self.device_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.device_combo.setToolTip(
            "Choose a configured Govee or a discovered device. Verified model layouts can be used immediately; unknown native layouts require physical calibration."
        )
        configured = list(configuration.devices)
        if not any(d.device_id == self._device.device_id for d in configured):
            configured.append(self._device)
        for configured_device in configured:
            self.device_combo.addItem(
                f"{configured_device.name} ({configured_device.sku})",
                configured_device.device_id,
            )
        self.device_combo.setCurrentIndex(
            max(0, self.device_combo.findData(self._device.device_id))
        )
        device_layout.addWidget(self.device_combo)

        network_row = QHBoxLayout()
        network_label = QLabel("Network address")
        network_label.setToolTip(
            "The local IP address EDL uses to contact this Govee light. Normally leave it unchanged; edit it only if the device's local address changes."
        )
        network_row.addWidget(network_label)
        self.ip_edit = QLineEdit(self._device.ip)
        self.ip_edit.setMaximumWidth(150)
        self.ip_edit.setPlaceholderText("192.168.0.22")
        self.ip_edit.setToolTip(
            "The local IP address EDL uses to contact this Govee light. Normally leave it unchanged; edit it only if the device's local address changes."
        )
        network_row.addWidget(self.ip_edit, 1)
        device_layout.addLayout(network_row)

        # Retain these compatibility surfaces for existing adapters, but their
        # explanatory content now lives in tooltips/contextual state instead of
        # permanently consuming working space.
        self.address_help = QLabel(
            "Network address is only needed to reach this Govee on your local network."
        )
        self.address_help.setWordWrap(True)
        self.address_help.setProperty("edlTextRole", "tertiary")
        self.address_help.setStyleSheet(f"font-size:{HELPER_PT}pt;")
        self.address_help.hide()
        device_layout.addWidget(self.address_help)

        self.ownership_note = QLabel(
            "Runtime ownership is chosen in Lighting devices; this screen only configures this Govee and its zones."
        )
        self.ownership_note.setWordWrap(True)
        self.ownership_note.setProperty("edlTextRole", "tertiary")
        self.ownership_note.setStyleSheet(f"font-size:{HELPER_PT}pt;")
        self.ownership_note.hide()
        device_layout.addWidget(self.ownership_note)

        self.mode_explanation = QLabel()
        self.mode_explanation.setWordWrap(True)
        self.mode_explanation.setProperty("edlTextRole", "tertiary")
        self.mode_explanation.setStyleSheet(f"font-size:{HELPER_PT}pt;")
        self.mode_explanation.hide()
        device_layout.addWidget(self.mode_explanation)

        left.addWidget(self.device_panel)

        zones_heading = QLabel("Saved Govee zones")
        zones_heading.setStyleSheet("font-weight:600;")
        left.addWidget(zones_heading)
        hint = QLabel("Select a saved zone to edit it.")
        hint.setWordWrap(True)
        hint.setProperty("edlTextRole", "tertiary")
        hint.setStyleSheet(f"font-size:{HELPER_PT}pt;")
        left.addWidget(hint)

        self.zone_list = QListWidget()
        self.zone_list.itemDoubleClicked.connect(lambda _item: self._edit_selected_zone())
        self.zone_list.itemSelectionChanged.connect(self._zone_list_selection_changed)
        left.addWidget(self.zone_list, 1)

        zone_actions = QHBoxLayout()
        new_button = QPushButton("New")
        new_button.clicked.connect(self._new_zone)
        zone_actions.addWidget(new_button)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._edit_selected_zone)
        zone_actions.addWidget(self.edit_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete_zone)
        zone_actions.addWidget(self.delete_button)
        left.addLayout(zone_actions)

        self.overlap_note = QLabel(
            "Gray positions already belong to another saved Govee zone and cannot be selected here."
        )
        self.overlap_note.setWordWrap(True)
        self.overlap_note.setProperty("edlTextRole", "tertiary")
        self.overlap_note.setStyleSheet(f"font-size:{HELPER_PT}pt;")
        left.addWidget(self.overlap_note)

        # Right side: the primary task. Zone name, position mapping and physical
        # test stay visible together at normal desktop sizes.
        right_panel = QFrame()
        right_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self._zone_editor_panel = right_panel
        right = QVBoxLayout(right_panel)
        right.setContentsMargins(14, 12, 14, 12)
        right.setSpacing(10)

        map_heading = QLabel("Zone editor")
        map_heading.setStyleSheet("font-weight:600;")
        right.addWidget(map_heading)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Zone name"))
        self.zone_name = QLineEdit()
        self.zone_name.setPlaceholderText("Example: Left console")
        self.zone_name.textChanged.connect(self._update_controls)
        name_row.addWidget(self.zone_name, 1)
        right.addLayout(name_row)

        map_help = QLabel(
            "Choose the positions that belong to this zone. Click one, or drag across several."
        )
        map_help.setWordWrap(True)
        map_help.setStyleSheet(f"color:{TEXT_SECONDARY};")
        right.addWidget(map_help)

        orientation_row = QHBoxLayout()
        self.reverse_check = QCheckBox(
            "Reverse address order so Position 1 maps to the left end"
        )
        self.reverse_check.setChecked(self._reverse_visual_order)
        self.reverse_check.setToolTip(
            "The Govee native address order may run opposite to the way the light is installed. "
            "This changes only the setup diagram's mapping; the native renderer and saved physical addresses stay correct."
        )
        self.reverse_check.toggled.connect(self._reverse_order_changed)
        orientation_row.addWidget(self.reverse_check)
        orientation_row.addStretch(1)
        right.addLayout(orientation_row)

        self.strip = SegmentStrip(self._device.segment_count)
        self.strip.selectionChanged.connect(self._strip_selection_changed)
        right.addWidget(self.strip)

        selection_row = QHBoxLayout()
        self.selection_label = QLabel("No positions selected")
        self.selection_label.setStyleSheet(f"color:{TEXT_SECONDARY};")
        selection_row.addWidget(self.selection_label, 1)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(lambda: self._set_selected_positions(set()))
        selection_row.addWidget(clear_button)
        select_all_button = QPushButton("Select all")
        select_all_button.clicked.connect(self._select_all_available)
        selection_row.addWidget(select_all_button)
        right.addLayout(selection_row)

        test_box = QFrame()
        test_box.setFrameShape(QFrame.Shape.StyledPanel)
        self.live_test_panel = test_box
        test_layout = QVBoxLayout(test_box)
        test_layout.setContentsMargins(10, 9, 10, 9)
        test_layout.setSpacing(6)

        test_title = QLabel("Live position test")
        test_title.setStyleSheet("font-weight:600;")
        test_layout.addWidget(test_title)

        self.live_test_safety = QLabel(
            "Before testing: enable LAN Control in Govee Desktop and deselect this same device "
            "in Razer Chroma Connect so EDL is its only realtime owner."
        )
        self.live_test_safety.setWordWrap(True)
        self.live_test_safety.setProperty("edlTextRole", "tertiary")
        self.live_test_safety.setStyleSheet(f"font-size:{HELPER_PT}pt;")
        self.live_test_safety.setToolTip(
            "Other Govee devices may remain under Govee Desktop control. Only the physical device being tested here must not have a second realtime owner."
        )
        test_layout.addWidget(self.live_test_safety)

        test_row = QHBoxLayout()
        self.trace_button = QPushButton("Start live test")
        self.trace_button.clicked.connect(self._toggle_trace)
        test_row.addWidget(self.trace_button)
        self.test_colour_button = QPushButton("Test colour")
        self.test_colour_button.clicked.connect(self._choose_test_colour)
        test_row.addWidget(self.test_colour_button)
        self.trace_status = QLabel("Not testing")
        self.trace_status.setProperty("edlTextRole", "tertiary")
        test_row.addWidget(self.trace_status, 1)
        test_layout.addLayout(test_row)
        right.addWidget(test_box)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_zone_button = QPushButton("Add Govee zone")
        self.save_zone_button.clicked.connect(self._save_zone)
        save_row.addWidget(self.save_zone_button)
        right.addLayout(save_row)

        body.addWidget(left_panel, 0)
        body.addWidget(right_panel, 1)
        root.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        save = QPushButton("Save Govee setup")
        save.clicked.connect(self._save_configuration)
        bottom.addWidget(save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        root.addLayout(bottom)

        self._refresh_zone_list()
        self._new_zone()
        self._update_test_colour_button()
        self._update_mode_explanation()

    @property
    def configuration(self) -> GoveeConfiguration:
        return self._configuration

    def _visual_to_native(self, position: int) -> int:
        if self.reverse_check.isChecked():
            return self._device.segment_count + 1 - position
        return position

    def _native_to_visual(self, segment: int) -> int:
        if self.reverse_check.isChecked():
            return self._device.segment_count + 1 - segment
        return segment

    def _native_set_to_visual(self, segments: set[int]) -> set[int]:
        return {self._native_to_visual(segment) for segment in segments}

    def _visual_set_to_native(self, positions: set[int]) -> set[int]:
        return {self._visual_to_native(position) for position in positions}

    def _reverse_order_changed(self, checked: bool) -> None:
        settings = app_settings()
        settings.setValue(VISUAL_REVERSE_KEY, bool(checked))
        settings.sync()
        # Preserve the same physical/native selection while changing only how it
        # is presented left-to-right to the operator.
        native_selected = self._visual_set_to_native(self._selected_positions)
        self._selected_positions = self._native_set_to_visual(native_selected)
        self._refresh_strip_state()
        if self._trace_session is not None:
            self._render_trace()

    def _update_mode_explanation(self, *_args) -> None:
        self.mode_explanation.setText(
            "When this device is Selected + Available with Control via Enhanced Govee "
            "in Lighting devices, Start lighting owns it through the native Govee renderer. "
            "Keep the same physical device deselected in Govee Desktop's Razer Chroma Connect "
            "list to avoid two realtime owners."
        )

    def _claimed_native_segments(self, *, excluding: str | None = None) -> set[int]:
        return {
            segment
            for zone in self._zones
            if zone.zone_id != excluding
            for segment in zone.segments
        }

    def _refresh_strip_state(self) -> None:
        claimed_visual = self._native_set_to_visual(
            self._claimed_native_segments(excluding=self._editing_zone_id)
        )
        self.strip.set_state(self._selected_positions, claimed_visual)

    def _set_selected_positions(self, positions: set[int]) -> None:
        claimed_visual = self._native_set_to_visual(
            self._claimed_native_segments(excluding=self._editing_zone_id)
        )
        self._selected_positions = {p for p in positions if p not in claimed_visual}
        self._refresh_strip_state()
        self._selection_changed()

    def _strip_selection_changed(self, positions) -> None:
        self._selected_positions = set(positions)
        self._selection_changed()

    def _select_all_available(self) -> None:
        claimed_visual = self._native_set_to_visual(
            self._claimed_native_segments(excluding=self._editing_zone_id)
        )
        self._set_selected_positions(
            {
                position
                for position in range(1, self._device.segment_count + 1)
                if position not in claimed_visual
            }
        )

    def _selection_changed(self) -> None:
        selected = tuple(sorted(self._selected_positions))
        if selected:
            summary = legacy._segment_summary(selected)
            self.selection_label.setText(
                f"{len(selected)} of {self._device.segment_count} positions selected — {summary}"
            )
        else:
            self.selection_label.setText("No positions selected")
        self._update_controls()
        if self._trace_session is not None:
            self._render_trace()

    def _update_controls(self, *_args) -> None:
        ready = bool(self.zone_name.text().strip()) and bool(self._selected_positions)
        self.save_zone_button.setEnabled(ready)
        self.trace_button.setEnabled(bool(self._selected_positions) or self._trace_session is not None)
        has_saved_selection = self.zone_list.currentItem() is not None
        self.edit_button.setEnabled(has_saved_selection)
        self.delete_button.setEnabled(has_saved_selection)

    def _visual_summary_for_zone(self, zone: GoveeZone) -> str:
        positions = tuple(sorted(self._native_set_to_visual(set(zone.segments))))
        return legacy._segment_summary(positions)

    def _refresh_zone_list(self, selected_id: str | None = None) -> None:
        self.zone_list.blockSignals(True)
        self.zone_list.clear()
        selected_row = -1
        for row, zone in enumerate(self._zones):
            item = QListWidgetItem(
                f"{zone.name}\n{len(zone.segments)} positions · {self._visual_summary_for_zone(zone)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, zone.zone_id)
            self.zone_list.addItem(item)
            if zone.zone_id == selected_id:
                selected_row = row
        self.zone_list.blockSignals(False)
        if selected_row >= 0:
            self.zone_list.setCurrentRow(selected_row)
        self._update_controls()

    def _zone_list_selection_changed(self) -> None:
        self._update_controls()

    def _new_zone(self) -> None:
        self.zone_list.clearSelection()
        self._editing_zone_id = None
        self.zone_name.clear()
        self.save_zone_button.setText("Add Govee zone")
        self._set_selected_positions(set())
        self._update_controls()

    def _edit_selected_zone(self) -> None:
        current = self.zone_list.currentItem()
        if current is None:
            return
        zone_id = str(current.data(Qt.ItemDataRole.UserRole))
        zone = next((value for value in self._zones if value.zone_id == zone_id), None)
        if zone is None:
            return
        self._editing_zone_id = zone.zone_id
        self.zone_name.setText(zone.name)
        self.save_zone_button.setText("Save changes")
        positions = self._native_set_to_visual(set(zone.segments))
        self._set_selected_positions(positions)

    def _unique_zone_id(self, name: str) -> str:
        base = legacy._zone_id_from_name(name)
        used = {zone.zone_id for zone in self._zones}
        if base not in used:
            return base
        suffix = 2
        while f"{base}-{suffix}" in used:
            suffix += 1
        return f"{base}-{suffix}"

    def _save_zone(self) -> None:
        name = self.zone_name.text().strip()
        native_segments = tuple(sorted(self._visual_set_to_native(self._selected_positions)))
        if not name or not native_segments:
            return
        if self._editing_zone_id is None:
            zone = GoveeZone(self._unique_zone_id(name), name, native_segments)
            self._zones.append(zone)
        else:
            row = next(
                (i for i, z in enumerate(self._zones) if z.zone_id == self._editing_zone_id),
                None,
            )
            if row is None:
                return
            zone = GoveeZone(self._editing_zone_id, name, native_segments)
            self._zones[row] = zone
        self._editing_zone_id = zone.zone_id
        self._refresh_zone_list(zone.zone_id)
        self._set_selected_positions(self._native_set_to_visual(set(zone.segments)))

    def _delete_zone(self) -> None:
        item = self.zone_list.currentItem()
        if item is None:
            return
        zone_id = str(item.data(Qt.ItemDataRole.UserRole))
        zone = next((value for value in self._zones if value.zone_id == zone_id), None)
        if zone is None:
            return
        target = self._device.target_for_zone(zone)
        if target in self._used_targets:
            QMessageBox.warning(
                self,
                "Govee zone is used by the current profile",
                "This Govee zone is referenced by the loaded profile. Change those rules before deleting it.",
            )
            return
        answer = QMessageBox.question(self, "Delete Govee zone", f"Delete {zone.name!r}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._zones = [value for value in self._zones if value.zone_id != zone_id]
        self._refresh_zone_list()
        self._new_zone()

    def _update_test_colour_button(self) -> None:
        red, green, blue = self._test_colour
        luminance = 0.299 * red + 0.587 * green + 0.114 * blue
        foreground = "#000000" if luminance > 150 else "#FFFFFF"
        self.test_colour_button.setStyleSheet(
            f"QPushButton {{ background-color: rgb({red},{green},{blue}); color:{foreground}; }}"
        )
        self.test_colour_button.setText(f"Test colour  {red}, {green}, {blue}")

    def _choose_test_colour(self) -> None:
        current = QColor(*self._test_colour)
        colour = QColorDialog.getColor(current, self, "Choose Govee test colour")
        if not colour.isValid():
            return
        self._test_colour = (colour.red(), colour.green(), colour.blue())
        self._update_test_colour_button()
        if self._trace_session is not None:
            self._render_trace()

    def _trace_frame(self) -> tuple[tuple[int, int, int], ...]:
        selected_native = self._visual_set_to_native(self._selected_positions)
        return tuple(
            self._test_colour if native_index in selected_native else TRACE_UNSELECTED
            for native_index in range(1, self._device.segment_count + 1)
        )

    def _render_trace(self) -> None:
        if self._trace_session is None:
            return
        try:
            self._trace_session.render_segments(self._trace_frame())
        except Exception as exc:
            self._stop_trace()
            QMessageBox.warning(self, "Govee test stopped", str(exc))

    def _toggle_trace(self) -> None:
        if self._trace_session is not None:
            self._stop_trace()
            return
        if not self._selected_positions:
            return
        try:
            session = GoveeRealtimeSession(self.ip_edit.text().strip())
            session.start()
            time.sleep(TRACE_ACTIVATION_WAIT_SECONDS)
            self._trace_session = session
            self._render_trace()
            self._trace_timer.start()
        except Exception as exc:
            try:
                session.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            QMessageBox.warning(self, "Cannot test Govee H61C3", str(exc))
            return
        self.trace_button.setText("Stop live test")
        self.trace_status.setText(
            "LIVE — click or drag the 42-box strip above; the real H61C3 updates immediately"
        )
        self._update_controls()

    def _stop_trace(self) -> None:
        self._trace_timer.stop()
        session = self._trace_session
        self._trace_session = None
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                self.trace_status.setText(f"Release warning: {exc}")
                self._update_controls()
                return
        self.trace_button.setText("Start live test")
        self.trace_status.setText("Test stopped; direct Govee control released")
        self._update_controls()

    def _save_configuration(self) -> None:
        if self._trace_session is not None:
            self._stop_trace()
        try:
            replacement = GoveeEnhancedDevice(
                device_id=self._device.device_id,
                name=self._device.name,
                sku=self._device.sku,
                ip=self.ip_edit.text().strip(),
                zones=tuple(self._zones),
            )
            devices = [
                d
                for d in self._configuration.devices
                if d.device_id != replacement.device_id
            ]
            devices.append(replacement)
            configuration = GoveeConfiguration(tuple(devices))
            save_govee_configuration(configuration)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot save Govee setup", str(exc))
            return
        self._device = replacement
        self._configuration = configuration
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        if self._trace_session is not None:
            self._stop_trace()
        super().done(result)


CANDIDATE_PREFIX = "candidate:"


def _layout_containing(layout: QLayout | None, widget) -> QLayout | None:
    if layout is None:
        return None
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is widget:
            return layout
        child = item.layout()
        found = _layout_containing(child, widget)
        if found is not None:
            return found
    return None


class GuidedGoveeZoneMapperDialog(AccessibleGoveeZoneMapperDialog):
    """Device-aware zone editor with optional experimental calibration."""

    def __init__(self, *args, **kwargs) -> None:
        original_configuration = args[0] if args else kwargs.get("configuration", GoveeConfiguration())
        super().__init__(*args, **kwargs)

        self.reverse_check.setVisible(False)
        self.swap_direction_button = QPushButton("Swap left and right")
        self.swap_direction_button.setToolTip(
            "Use this only if the live test shows the left side of the on-screen position strip lighting the physical right side, or vice versa. "
            "It changes only this device's setup map; it does not change the realtime protocol."
        )
        self.swap_direction_button.clicked.connect(
            lambda: self.reverse_check.setChecked(not self.reverse_check.isChecked())
        )
        containing = _layout_containing(self.layout(), self.reverse_check)
        if containing is not None:
            containing.addWidget(self.swap_direction_button)

        self.find_devices_button = QPushButton("Find Govee devices")
        self.find_devices_button.setToolTip(
            "Listen for Govee LAN discovery replies. A reply only claims a model and network address; "
            "it does not prove physical device identity or native geometry."
        )
        self.find_devices_button.clicked.connect(self._find_devices)

        self.use_device_button = QPushButton("Use selected device")
        self.use_device_button.setVisible(False)
        self.use_device_button.clicked.connect(self._use_selected_candidate)

        self.discovery_status = QLabel("")
        self.discovery_status.setWordWrap(True)
        self.discovery_status.setStyleSheet(f"color:#999999; font-size:{HELPER_PT}pt;")
        self.discovery_status.hide()

        self.geometry_status_label = QLabel("")
        self.geometry_status_label.setWordWrap(True)
        self.geometry_status_label.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:{HELPER_PT}pt;")

        parent = self.device_combo.parentWidget()
        if parent is not None and parent.layout() is not None:
            row = QHBoxLayout()
            row.addWidget(self.find_devices_button)
            row.addWidget(self.use_device_button)
            row.addStretch(1)
            parent.layout().addLayout(row)
            parent.layout().addWidget(self.discovery_status)
            parent.layout().addWidget(self.geometry_status_label)

        self.device_combo.currentIndexChanged.connect(self._device_choice_changed)
        self._refresh_configured_combo_labels()

        if not original_configuration.devices:
            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            self.device_combo.addItem("No Govee device configured", None)
            self.device_combo.blockSignals(False)
            self.geometry_status_label.setText(
                "No native-zone Govee is configured yet. Use Find Govee devices to begin; other lighting devices do not require this screen."
            )
            self._set_device_editor_available(False)
        else:
            self._switch_to_configured_device(self._device)

        self._install_human_tooltips()
        self._refresh_dynamic_copy()

    # ---------- device selection / discovery ----------

    def _configured_device(self, device_id: str) -> GoveeEnhancedDevice | None:
        return next(
            (device for device in self._configuration.devices if device.device_id == device_id),
            None,
        )

    def _physical_key(self, sku: str, ip: str) -> tuple[str, str]:
        return str(sku).strip().upper(), str(ip).strip()

    def _configured_physical_keys(self) -> set[tuple[str, str]]:
        return {
            self._physical_key(device.sku, device.ip)
            for device in self._configuration.devices
        }

    def _candidate_data(self, sku: str, ip: str) -> str:
        return f"{CANDIDATE_PREFIX}{sku.upper()}:{ip}"

    def _parse_candidate(self, data: object) -> tuple[str, str] | None:
        if not isinstance(data, str) or not data.startswith(CANDIDATE_PREFIX):
            return None
        payload = data[len(CANDIDATE_PREFIX):]
        try:
            sku, ip = payload.split(":", 1)
        except ValueError:
            return None
        return sku.upper(), ip

    def _device_combo_label(self, device: GoveeEnhancedDevice) -> str:
        if device.geometry_status == GEOMETRY_VERIFIED_MODEL:
            trust = f"verified · {device.segment_count} positions"
        elif device.geometry_status == GEOMETRY_USER_CALIBRATED:
            trust = f"calibrated here · {device.segment_count} positions"
        else:
            trust = f"{device.segment_count} positions"
        return f"{device.name} ({device.sku}) · {trust}"

    def _refresh_configured_combo_labels(self) -> None:
        for index in range(self.device_combo.count()):
            data = self.device_combo.itemData(index)
            if not isinstance(data, str):
                continue
            device = self._configured_device(data)
            if device is not None:
                self.device_combo.setItemText(index, self._device_combo_label(device))

    def _find_devices(self) -> None:
        self.find_devices_button.setEnabled(False)
        self.discovery_status.setVisible(True)
        self.discovery_status.setText("Searching the local network for Govee devices…")
        self.repaint()
        try:
            discovered = discover_govee_devices()
        except Exception as exc:
            self.discovery_status.setText(
                f"The network search could not complete: {exc}. Existing configured devices are unchanged."
            )
            self.find_devices_button.setEnabled(True)
            return

        configured_keys = self._configured_physical_keys()
        existing_candidate_keys: set[tuple[str, str]] = set()
        for index in range(self.device_combo.count()):
            parsed = self._parse_candidate(self.device_combo.itemData(index))
            if parsed is not None:
                existing_candidate_keys.add(parsed)

        added = 0
        for found in discovered:
            key = self._physical_key(found.sku, found.ip)
            if key in configured_keys or key in existing_candidate_keys:
                continue
            if found.sku in VERIFIED_SEGMENT_COUNTS:
                label = f"{found.sku} — {found.ip} · LAN claim · verified model geometry available"
            else:
                label = f"{found.sku} — {found.ip} · LAN claim · calibration required for native zones"
            self.device_combo.addItem(label, self._candidate_data(found.sku, found.ip))
            existing_candidate_keys.add(key)
            added += 1

        if not discovered:
            self.discovery_status.setText(
                "No Govee device replied to the local scan. Discovery is optional; already configured devices remain usable."
            )
        elif added:
            self.discovery_status.setText(
                f"Received {len(discovered)} consistent Govee LAN reply/replies and added {added} inventory claim(s) not already configured. "
                "A reply is not identity proof; select one explicitly to reuse verified model geometry or start physical calibration."
            )
        else:
            self.discovery_status.setText(
                "Every consistent Govee LAN claim is already represented in this list; duplicate model/address replies were ignored."
            )
        self.find_devices_button.setEnabled(True)

    def _device_choice_changed(self, index: int) -> None:
        data = self.device_combo.itemData(index)
        if isinstance(data, str):
            configured = self._configured_device(data)
            if configured is not None:
                self.use_device_button.setVisible(False)
                self.discovery_status.setVisible(False)
                self._switch_to_configured_device(configured)
                return

        candidate = self._parse_candidate(data)
        if candidate is None:
            self.use_device_button.setVisible(False)
            self.discovery_status.setVisible(False)
            return

        self.discovery_status.setVisible(True)

        sku, ip = candidate
        self.ip_edit.setText(ip)
        self._set_device_editor_available(False)
        self.use_device_button.setVisible(True)
        if sku in VERIFIED_SEGMENT_COUNTS:
            count = VERIFIED_SEGMENT_COUNTS[sku]
            self.use_device_button.setText("Add with verified layout")
            self.use_device_button.setToolTip(
                f"EDL already has physically verified model geometry for {sku}: {count} positions. "
                "Use that model-wide layout only if you recognize and intend to configure this LAN claim."
            )
            self.discovery_status.setText(
                f"LAN reply claims {sku} at {ip}. Confirm this is the device you intend to configure."
            )
            self.geometry_status_label.setText(
                f"Verified model geometry available — {count} native positions. This verifies the {sku} layout, not the identity of the LAN peer; confirm the device before saving."
            )
        else:
            self.use_device_button.setText("Calibrate this device")
            self.use_device_button.setToolTip(
                "EDL can see this Govee but does not yet trust a native section layout for it. "
                "Run a moving-marker experiment and accept it only if the real device behaves correctly."
            )
            self.discovery_status.setText(
                f"LAN reply claims {sku} at {ip}. Confirm this is the device you intend to calibrate."
            )
            self.geometry_status_label.setText(
                "Native layout not verified. You can keep using normal Govee/Razer Chroma compatibility, or run an experimental physical calibration to unlock native zones on this device."
            )

    def _use_selected_candidate(self) -> None:
        candidate = self._parse_candidate(self.device_combo.currentData())
        if candidate is None:
            return
        sku, ip = candidate
        if sku in VERIFIED_SEGMENT_COUNTS:
            device = GoveeEnhancedDevice(
                device_id=device_id_for(sku, ip),
                name=f"Govee {sku}",
                sku=sku,
                ip=ip,
                zones=(),
                segment_count_override=VERIFIED_SEGMENT_COUNTS[sku],
                geometry_status=GEOMETRY_VERIFIED_MODEL,
            )
        else:
            dialog = GoveeCalibrationDialog(sku=sku, ip=ip, parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            device = dialog.calibrated_device
            if device is None:
                return

        devices = list(self._configuration.devices)
        devices.append(device)
        try:
            self._configuration = GoveeConfiguration(tuple(devices))
        except Exception as exc:
            QMessageBox.warning(self, "Cannot add Govee device", str(exc))
            return

        current_index = self.device_combo.currentIndex()
        self.device_combo.blockSignals(True)
        self.device_combo.setItemText(current_index, self._device_combo_label(device))
        self.device_combo.setItemData(current_index, device.device_id)
        self.device_combo.blockSignals(False)
        self.use_device_button.setVisible(False)
        self._switch_to_configured_device(device)
        self.discovery_status.setVisible(True)
        self.discovery_status.setText(
            f"{device.sku} is staged. Create its zones, then choose Save Govee setup."
        )

    # ---------- configured device editor ----------

    def _set_device_editor_available(self, available: bool) -> None:
        for widget in (
            self.ip_edit,
            self.zone_list,
            self.zone_name,
            self.strip,
            self.trace_button,
            self.test_colour_button,
            self.save_zone_button,
            self.swap_direction_button,
        ):
            widget.setEnabled(bool(available))

        secondary_texts = {
            "New zone",
            "Edit selected",
            "Delete",
            "Clear",
            "Select all",
            "Save Govee setup",
        }
        for button in self.findChildren(QPushButton):
            if button.text() in secondary_texts:
                button.setEnabled(bool(available))

        if available:
            self._update_controls()
        else:
            self.edit_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.save_zone_button.setEnabled(False)

    def _switch_to_configured_device(self, device: GoveeEnhancedDevice) -> None:
        if self._trace_session is not None:
            self._stop_trace()
        self._device = device
        self._zones = list(device.zones)
        self._editing_zone_id = None
        self._selected_positions = set()
        self.ip_edit.setText(device.ip)

        reversed_order = load_visual_reversed(
            device.device_id,
            legacy_h61c3=device.sku == "H61C3",
        )
        self.reverse_check.blockSignals(True)
        self.reverse_check.setChecked(reversed_order)
        self.reverse_check.blockSignals(False)

        self.strip.count = device.segment_count
        self.strip.set_state(set(), set())
        self.strip.update()
        self.zone_name.clear()
        self.save_zone_button.setText("Add Govee zone")
        self._refresh_zone_list()
        self._new_zone()
        self._set_device_editor_available(True)
        self._update_mode_explanation()
        self.geometry_status_label.setText(self._geometry_text())
        self._refresh_dynamic_copy()

    def _reverse_order_changed(self, checked: bool) -> None:
        count = self._device.segment_count
        self._selected_positions = {
            count + 1 - position for position in self._selected_positions
        }
        save_visual_reversed(self._device.device_id, bool(checked))
        self._refresh_zone_list(
            self._editing_zone_id if self._editing_zone_id is not None else None
        )
        self._refresh_strip_state()
        self._selection_changed()

    def _update_mode_explanation(self, *_args) -> None:
        if not hasattr(self, "_device"):
            return
        self.mode_explanation.setText(
            f"Start lighting can own this {self._device.sku} when it is Selected + Available "
            "with Control via Enhanced Govee in Lighting devices."
        )

    def _toggle_trace(self) -> None:
        if self._trace_session is not None:
            self._stop_trace()
            return
        if not self._selected_positions:
            return
        try:
            session = GoveeRealtimeSession(self.ip_edit.text().strip())
            session.start()
            time.sleep(TRACE_ACTIVATION_WAIT_SECONDS)
            self._trace_session = session
            self._render_trace()
            self._trace_timer.start()
        except Exception as exc:
            try:
                session.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            QMessageBox.warning(self, "Cannot start Govee live test", str(exc))
            return
        self.trace_button.setText("Stop live test")
        self.trace_status.setText(
            f"LIVE — click or drag the {self._device.segment_count}-position strip; the selected {self._device.sku} updates immediately"
        )
        self._update_controls()

    def _stop_trace(self) -> None:
        self._trace_timer.stop()
        session = self._trace_session
        self._trace_session = None
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                self.trace_status.setText(f"Release warning: {exc}")
                self._update_controls()
                return
        self.trace_button.setText("Start live test")
        self.trace_status.setText("Test stopped; direct Govee control released")
        self._update_controls()

    def _save_configuration(self) -> None:
        if self._trace_session is not None:
            self._stop_trace()
        try:
            replacement = GoveeEnhancedDevice(
                device_id=self._device.device_id,
                name=self._device.name,
                sku=self._device.sku,
                ip=self.ip_edit.text().strip(),
                zones=tuple(self._zones),
                segment_count_override=self._device.segment_count,
                geometry_status=self._device.geometry_status,
                geometry_note=self._device.geometry_note,
            )
            devices = [
                device
                for device in self._configuration.devices
                if device.device_id != replacement.device_id
            ]
            devices.append(replacement)
            configuration = GoveeConfiguration(tuple(devices))
            save_govee_configuration(configuration)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot save Govee setup", str(exc))
            return
        self._device = replacement
        self._configuration = configuration
        self.accept()

    # ---------- explanatory copy ----------

    def _geometry_text(self) -> str:
        if self._device.geometry_status == GEOMETRY_VERIFIED_MODEL:
            return (
                f"Verified model layout — EDL has physically accepted {self._device.segment_count} native positions for {self._device.sku}."
            )
        if self._device.geometry_status == GEOMETRY_USER_CALIBRATED:
            return (
                f"Calibrated on this computer — this physical {self._device.sku} was accepted with {self._device.segment_count} native positions. "
                "This is trusted for this configured device, not claimed as model-wide verification."
            )
        return "Native geometry status is unknown."

    def _refresh_dynamic_copy(self) -> None:
        if not hasattr(self, "_device"):
            return
        for label in self.findChildren(QLabel):
            text = label.text()
            if text.startswith("EDL can control supported Govee lights section-by-section") or text.startswith("EDL can give supported Govee lights") or text.startswith("EDL can control Govee lights through Razer Chroma"):
                label.setText(
                    "Native Govee zones let Rules target parts of a supported light. "
                    "Verified layouts work immediately; other discovered devices require physical calibration."
                )
            elif text.startswith("1. Open Govee Desktop") or text.startswith("Before a live zone test"):
                label.setText(
                    "1. In Govee Desktop, enable LAN Control for this device.\n"
                    "2. In Razer Chroma Connect, deselect this same device so EDL is its only realtime owner.\n"
                    "Other Govee devices may remain under Govee Desktop control."
                )
            elif text.startswith("Choose which parts of the H61C3") or text.startswith("Choose which parts of the"):
                label.setText(
                    f"Choose which positions on this {self._device.sku} belong to this zone"
                )
            elif text.startswith("The 42 boxes below") or text.startswith("The boxes below represent") or text.startswith("The ") and "positions below read from left to right" in text:
                label.setText(
                    f"Click or drag the {self._device.segment_count} positions below. "
                    "During a live test, changes appear immediately on the selected Govee."
                )
            elif text.startswith("Choose a colour and start the live test"):
                label.setText(
                    "Choose a colour, start the live test, then click or drag positions above."
                )

        self.device_combo.setToolTip(
            "Choose a configured Govee, or a newly discovered device. Configured devices show whether their native layout is model-verified or physically calibrated on this computer."
        )
        self.ip_edit.setToolTip(
            "The local network address EDL uses to contact this selected Govee. Normally leave it alone; change it only if the device's local address changes."
        )

    def _install_human_tooltips(self) -> None:
        fixed = {
            "New zone": "Start defining another named area on the selected Govee. Nothing is saved until you add the zone and then save the Govee setup.",
            "Edit selected": "Load the highlighted saved zone so you can change its name or selected positions.",
            "Delete": "Remove the highlighted saved Govee zone. A zone used by the current lighting profile cannot be deleted here.",
            "Clear": "Remove every currently selected position from the zone being edited.",
            "Select all": "Select every position on this Govee that is not already assigned to another saved zone.",
            "Start live Govee test": "Temporarily send your selected test colour to the positions chosen above so you can identify them on the real installed Govee.",
            "Save Govee setup": "Save the configured devices, their trusted geometry, connection addresses, direct-control settings and named zones on this computer.",
            "Swap left and right": self.swap_direction_button.toolTip(),
        }
        for button in self.findChildren(QPushButton):
            text = button.text()
            tooltip = fixed.get(text)
            if text.startswith("Test colour"):
                tooltip = "Choose the colour EDL will use while you identify selected positions on the real Govee."
            elif text in {"Add Govee zone", "Save changes"}:
                tooltip = "Save the current zone name and selected positions into this device's in-memory Govee setup. Use Save Govee setup when you are finished to write the complete setup to disk."
            if tooltip:
                button.setToolTip(tooltip)
                button.setToolTipDuration(30000)


class HintAwareGoveeZoneMapperDialog(GuidedGoveeZoneMapperDialog):
    def _device_choice_changed(self, index: int) -> None:
        # Let the general layer handle configured devices and verified models.
        super()._device_choice_changed(index)
        candidate = self._parse_candidate(self.device_combo.itemData(index))
        if candidate is None:
            return
        sku, ip = candidate
        if sku in VERIFIED_SEGMENT_COUNTS:
            return

        hint = geometry_hint_for(sku)
        if hint is None:
            self.use_device_button.setVisible(False)
            self.discovery_status.setText(f"{sku} found at {ip}.")
            self.geometry_status_label.setText(
                "EDL has not yet found trustworthy model-specific evidence for native segmented control on this device. "
                "It is therefore not safe to guess a native layout here. Keep using its normal Govee/Razer Chroma compatibility path until a candidate mapping is added."
            )
            return

        self.use_device_button.setVisible(True)
        self.use_device_button.setText("Calibrate candidate layout")
        self.use_device_button.setToolTip(
            "EDL has non-verified model-specific evidence that can seed a native layout experiment. "
            "The candidate remains untrusted until you run the physical moving-marker test and explicitly accept the result."
        )
        self.discovery_status.setText(f"{sku} found at {ip}.")
        candidate_text = (
            f"Candidate evidence available — suggested starting count: {hint.suggested_count}. "
            if hint.suggested_count is not None
            else "Candidate evidence available, but it does not imply one fixed total position count. "
        )
        self.geometry_status_label.setText(
            candidate_text
            + "You may run an experimental physical calibration; normal Govee/Razer Chroma compatibility remains available regardless."
        )

    def _use_selected_candidate(self) -> None:
        candidate = self._parse_candidate(self.device_combo.currentData())
        if candidate is None:
            return
        sku, ip = candidate
        if sku in VERIFIED_SEGMENT_COUNTS:
            super()._use_selected_candidate()
            return

        hint = geometry_hint_for(sku)
        if hint is None:
            QMessageBox.information(
                self,
                "No native calibration candidate yet",
                "EDL found this Govee on the network, but it does not yet have trustworthy model-specific evidence for a native segmented layout. "
                "The device can continue through its normal Govee/Razer Chroma compatibility path.",
            )
            return

        evidence_text = f"{hint.explanation} Source: {hint.evidence}."
        dialog = GoveeCalibrationDialog(
            sku=sku,
            ip=ip,
            suggested_count=hint.suggested_count,
            evidence_text=evidence_text,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        device = dialog.calibrated_device
        if device is None:
            return

        devices = list(self._configuration.devices)
        devices.append(device)
        try:
            self._configuration = GoveeConfiguration(tuple(devices))
        except Exception as exc:
            QMessageBox.warning(self, "Cannot add Govee device", str(exc))
            return

        current_index = self.device_combo.currentIndex()
        self.device_combo.blockSignals(True)
        self.device_combo.setItemText(current_index, self._device_combo_label(device))
        self.device_combo.setItemData(current_index, device.device_id)
        self.device_combo.blockSignals(False)
        self.use_device_button.setVisible(False)
        self._switch_to_configured_device(device)
        self.discovery_status.setText(
            f"{device.sku} is staged with your physically accepted {device.segment_count}-position layout. "
            "Create its zones, then choose Save Govee setup."
        )


class MultiDeviceGoveeZoneMapperDialog(HintAwareGoveeZoneMapperDialog):
    def __init__(self, *args, **kwargs) -> None:
        self._active_device_id: str | None = None
        self._switching_device = False
        super().__init__(*args, **kwargs)

    def _stage_current_device(self) -> bool:
        device_id = self._active_device_id
        if not device_id or not hasattr(self, "_device"):
            return True
        try:
            staged = GoveeEnhancedDevice(
                device_id=self._device.device_id,
                name=self._device.name,
                sku=self._device.sku,
                ip=self.ip_edit.text().strip(),
                zones=tuple(self._zones),
                segment_count_override=self._device.segment_count,
                geometry_status=self._device.geometry_status,
                geometry_note=self._device.geometry_note,
            )
            devices = [
                device
                for device in self._configuration.devices
                if device.device_id != staged.device_id
            ]
            devices.append(staged)
            self._configuration = GoveeConfiguration(tuple(devices))
            self._device = staged
            self._refresh_configured_combo_labels()
            return True
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Cannot switch Govee device",
                "EDL could not keep the changes for the device you were editing:\n\n"
                f"{exc}\n\nCorrect that value before switching to another device.",
            )
            return False

    def _restore_active_combo_selection(self) -> None:
        if not self._active_device_id:
            return
        index = self.device_combo.findData(self._active_device_id)
        if index < 0:
            return
        self.device_combo.blockSignals(True)
        self.device_combo.setCurrentIndex(index)
        self.device_combo.blockSignals(False)

    def _device_choice_changed(self, index: int) -> None:
        if self._switching_device:
            return
        data = self.device_combo.itemData(index)
        if data == self._active_device_id:
            return super()._device_choice_changed(index)

        if self._active_device_id and not self._stage_current_device():
            self._restore_active_combo_selection()
            return

        self._switching_device = True
        try:
            super()._device_choice_changed(index)
        finally:
            self._switching_device = False

    def _switch_to_configured_device(self, device: GoveeEnhancedDevice) -> None:
        # During an explicit switch, the previous device has already been staged
        # by _device_choice_changed. Calls from calibration/verified-device setup
        # may arrive directly, so stage an existing active device there as well.
        if (
            not self._switching_device
            and self._active_device_id
            and self._active_device_id != device.device_id
        ):
            if not self._stage_current_device():
                self._restore_active_combo_selection()
                return
        super()._switch_to_configured_device(device)
        self._active_device_id = device.device_id


class FinalGoveeZoneMapperDialog(MultiDeviceGoveeZoneMapperDialog):
    def __init__(self, *args, **kwargs) -> None:
        original = args[0] if args else kwargs.get("configuration", GoveeConfiguration())
        super().__init__(*args, **kwargs)

        if not isinstance(original, GoveeConfiguration) or not original.devices:
            return

        # Discard any historical synthetic/default entry that the inherited
        # prototype may have created while constructing its controls.
        self._configuration = original
        self._active_device_id = None
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for device in original.devices:
            self.device_combo.addItem(self._device_combo_label(device), device.device_id)
        self.device_combo.setCurrentIndex(0)
        self.device_combo.blockSignals(False)
        self.use_device_button.setVisible(False)
        self._switch_to_configured_device(original.devices[0])


MAX_VISIBLE_SECTION_ROWS = 4


class SectionOrientationGoveeZoneMapperDialog(FinalGoveeZoneMapperDialog):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._section_orientation_by_native_section: dict[tuple[int, ...], str] = {}
        self._section_orientation_combos: list[QComboBox] = []
        self._section_orientation_control_sections: list[tuple[int, ...]] = []

        # The base dialog was sized before this optional panel existed. Keep the
        # existing desktop footprint, while making the dynamic section block own
        # an explicit amount of space instead of competing with the strip below.
        self.setMinimumHeight(max(self.minimumHeight(), 800))
        if self.height() < 820:
            self.resize(self.width(), 820)

        self.section_orientation_panel = QFrame()
        self.section_orientation_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self.section_orientation_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        panel = QVBoxLayout(self.section_orientation_panel)
        panel.setContentsMargins(10, 8, 10, 10)
        panel.setSpacing(6)
        self._section_orientation_panel_layout = panel

        self._section_orientation_title = QLabel("Disconnected section start/end")
        self._section_orientation_title.setStyleSheet("font-weight:600;")
        panel.addWidget(self._section_orientation_title)

        self._section_orientation_help = QLabel(
            "Choose what Start means on each separate section. REACTIVE ‘Each section start/end’ uses these choices; centre is unchanged."
        )
        self._section_orientation_help.setWordWrap(True)
        self._section_orientation_help.setStyleSheet(f"color:#999999; font-size:{HELPER_PT}pt;")
        panel.addWidget(self._section_orientation_help)

        self.section_orientation_rows = QWidget()
        self.section_orientation_grid = QGridLayout(self.section_orientation_rows)
        self.section_orientation_grid.setContentsMargins(0, 2, 0, 2)
        self.section_orientation_grid.setHorizontalSpacing(12)
        self.section_orientation_grid.setVerticalSpacing(6)
        self.section_orientation_grid.setColumnStretch(0, 1)

        # The rows live in their own bounded viewport. Two, three and four
        # sections are shown in full; larger selections scroll here rather than
        # compressing the position strip or the rest of the dialog.
        self.section_orientation_scroll = QScrollArea()
        self.section_orientation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.section_orientation_scroll.setWidgetResizable(True)
        self.section_orientation_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.section_orientation_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.section_orientation_scroll.setWidget(self.section_orientation_rows)
        panel.addWidget(self.section_orientation_scroll)

        # Keep topology controls beside the topology they describe: immediately
        # below Zone name and above the physical position strip.
        parent = self.zone_name.parentWidget()
        if parent is not None and parent.layout() is not None:
            layout = parent.layout()
            strip_index = layout.indexOf(self.strip)
            if strip_index >= 0 and hasattr(layout, "insertWidget"):
                layout.insertWidget(strip_index, self.section_orientation_panel)
            else:
                layout.addWidget(self.section_orientation_panel)

        self.section_orientation_panel.setVisible(False)
        self._replace_overlap_guidance()
        self._refresh_section_orientation_editor()

    # Overlapping logical zones are intentional. The renderer resolves a shared
    # physical position deterministically by saved zone order; later zones win.
    # Returning no claimed positions removes only the old authoring lockout.
    def _claimed_native_segments(self, *, excluding: str | None = None) -> set[int]:
        return set()

    def _replace_overlap_guidance(self) -> None:
        for label in self.findChildren(QLabel):
            if label.text().startswith("Gray positions already belong"):
                label.setText(
                    "Overlaps are allowed; the later saved zone wins shared positions."
                )
                label.setWordWrap(True)
                break

    def _selected_native_sections(self) -> tuple[tuple[int, ...], ...]:
        native = tuple(sorted(self._visual_set_to_native(self._selected_positions)))
        return contiguous_segment_sections(native)

    def _clear_orientation_rows(self) -> None:
        self._section_orientation_combos.clear()
        self._section_orientation_control_sections.clear()
        while self.section_orientation_grid.count():
            item = self.section_orientation_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _set_orientation_panel_height(self, section_count: int) -> None:
        if section_count <= 1:
            return

        row_height = max(
            30,
            max(
                (combo.sizeHint().height() for combo in self._section_orientation_combos),
                default=30,
            ),
        )
        visible_rows = min(section_count, MAX_VISIBLE_SECTION_ROWS)
        grid_spacing = self.section_orientation_grid.verticalSpacing()
        scroll_height = (
            visible_rows * row_height
            + max(0, visible_rows - 1) * grid_spacing
            + 6
        )
        self.section_orientation_scroll.setFixedHeight(scroll_height)

        margins = self._section_orientation_panel_layout.contentsMargins()
        panel_spacing = self._section_orientation_panel_layout.spacing()
        header_height = (
            self._section_orientation_title.sizeHint().height()
            + self._section_orientation_help.sizeHint().height()
        )
        panel_height = (
            margins.top()
            + margins.bottom()
            + header_height
            + scroll_height
            + panel_spacing * 2
        )
        self.section_orientation_panel.setFixedHeight(panel_height)
        self.section_orientation_panel.updateGeometry()

        parent = self.section_orientation_panel.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().invalidate()
            parent.layout().activate()

    def _remember_orientation_controls(self) -> None:
        for section, combo in zip(
            self._section_orientation_control_sections,
            self._section_orientation_combos,
        ):
            value = combo.currentData()
            if value in {SECTION_FORWARD, SECTION_REVERSED}:
                self._section_orientation_by_native_section[section] = str(value)

    def _orientation_label(self, section: tuple[int, ...]) -> str:
        visual = tuple(sorted(self._native_set_to_visual(set(section))))
        if len(visual) == 1:
            return f"Position {visual[0]}"
        return f"Positions {visual[0]}–{visual[-1]}"

    def _forward_means_visual_left(self, section: tuple[int, ...]) -> bool:
        first_visual = self._native_to_visual(section[0])
        last_visual = self._native_to_visual(section[-1])
        return first_visual <= last_visual

    def _refresh_section_orientation_editor(self) -> None:
        self._remember_orientation_controls()
        sections = self._selected_native_sections()
        self._clear_orientation_rows()
        self.section_orientation_panel.setVisible(len(sections) > 1)
        if len(sections) <= 1:
            return

        for row_index, section in enumerate(sections):
            label = QLabel(
                f"Section {row_index + 1} · {self._orientation_label(section)}"
            )
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            self.section_orientation_grid.addWidget(label, row_index, 0)

            combo = QComboBox()
            combo.setMinimumWidth(190)
            combo.setMaximumWidth(230)
            if self._forward_means_visual_left(section):
                combo.addItem("Start at left end", SECTION_FORWARD)
                combo.addItem("Start at right end", SECTION_REVERSED)
            else:
                combo.addItem("Start at left end", SECTION_REVERSED)
                combo.addItem("Start at right end", SECTION_FORWARD)
            wanted = self._section_orientation_by_native_section.get(
                section,
                SECTION_FORWARD,
            )
            wanted_index = combo.findData(wanted)
            combo.setCurrentIndex(max(0, wanted_index))
            combo.setToolTip(
                "This changes only the logical Start/End used by spatial effects inside this section. "
                "It does not reorder or remap the physical Govee segments."
            )
            self.section_orientation_grid.addWidget(combo, row_index, 1)
            self._section_orientation_control_sections.append(section)
            self._section_orientation_combos.append(combo)

        self.section_orientation_grid.invalidate()
        self.section_orientation_grid.activate()
        self._set_orientation_panel_height(len(sections))

    def _selection_changed(self) -> None:
        super()._selection_changed()
        if hasattr(self, "section_orientation_panel"):
            self._refresh_section_orientation_editor()

    def _new_zone(self) -> None:
        self._section_orientation_by_native_section = {}
        super()._new_zone()
        if hasattr(self, "section_orientation_panel"):
            self._refresh_section_orientation_editor()

    def _edit_selected_zone(self) -> None:
        super()._edit_selected_zone()
        zone = next(
            (value for value in self._zones if value.zone_id == self._editing_zone_id),
            None,
        )
        self._section_orientation_by_native_section = {}
        if zone is not None:
            self._section_orientation_by_native_section = dict(
                zip(zone.sections, zone.effective_section_orientations)
            )
        self._refresh_section_orientation_editor()

    def _reverse_order_changed(self, checked: bool) -> None:
        self._remember_orientation_controls()
        super()._reverse_order_changed(checked)
        self._refresh_section_orientation_editor()

    def _save_zone(self) -> None:
        self._remember_orientation_controls()
        name = self.zone_name.text().strip()
        native_segments = tuple(sorted(self._visual_set_to_native(self._selected_positions)))
        if not name or not native_segments:
            return
        sections = contiguous_segment_sections(native_segments)
        orientations = tuple(
            self._section_orientation_by_native_section.get(section, SECTION_FORWARD)
            for section in sections
        )

        if self._editing_zone_id is None:
            zone = GoveeZone(
                self._unique_zone_id(name),
                name,
                native_segments,
                orientations,
            )
            self._zones.append(zone)
        else:
            row = next(
                (i for i, value in enumerate(self._zones) if value.zone_id == self._editing_zone_id),
                None,
            )
            if row is None:
                return
            zone = GoveeZone(
                self._editing_zone_id,
                name,
                native_segments,
                orientations,
            )
            self._zones[row] = zone

        self._editing_zone_id = zone.zone_id
        self._section_orientation_by_native_section = dict(
            zip(zone.sections, zone.effective_section_orientations)
        )
        self._refresh_zone_list(zone.zone_id)
        self._set_selected_positions(self._native_set_to_visual(set(zone.segments)))
        self._refresh_section_orientation_editor()


def apply_composed_govee_mapper() -> None:
    legacy.GoveeZoneMapperDialog = SectionOrientationGoveeZoneMapperDialog


def apply_govee_drag_scrub() -> None:
    if getattr(SegmentStrip, "_edl_drag_scrub_applied", False):
        return

    original_press = SegmentStrip.mousePressEvent
    original_move = SegmentStrip.mouseMoveEvent
    original_release = SegmentStrip.mouseReleaseEvent

    def apply_range(self, current: int) -> None:
        anchor = getattr(self, "_edl_drag_anchor", None)
        baseline = getattr(self, "_edl_drag_baseline", None)
        add_mode = getattr(self, "_edl_drag_add_mode", None)
        if anchor is None or baseline is None or add_mode is None:
            return

        selected = set(baseline)
        start, end = sorted((anchor, current))
        for position in range(start, end + 1):
            if position in self.claimed:
                continue
            if add_mode:
                selected.add(position)
            else:
                selected.discard(position)

        self.selected = selected
        self._last_drag_position = current
        self.update()
        self.selectionChanged.emit(set(self.selected))

    def mouse_press(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return original_press(self, event)
        position = self._position_at(event.position().x())
        if position is None or position in self.claimed:
            event.accept()
            return

        self._edl_drag_anchor = position
        self._edl_drag_baseline = set(self.selected)
        self._edl_drag_add_mode = position not in self.selected
        self._drag_mode = self._edl_drag_add_mode
        self._last_drag_position = position
        apply_range(self, position)
        event.accept()

    def mouse_move(self, event) -> None:  # type: ignore[override]
        if (
            getattr(self, "_edl_drag_anchor", None) is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            position = self._position_at(event.position().x())
            if position is not None and position != self._last_drag_position:
                apply_range(self, position)
            event.accept()
            return
        original_move(self, event)

    def mouse_release(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._edl_drag_anchor = None
            self._edl_drag_baseline = None
            self._edl_drag_add_mode = None
            self._drag_mode = None
            self._last_drag_position = None
            event.accept()
            return
        original_release(self, event)

    SegmentStrip.mousePressEvent = mouse_press
    SegmentStrip.mouseMoveEvent = mouse_move
    SegmentStrip.mouseReleaseEvent = mouse_release
    SegmentStrip._edl_drag_scrub_applied = True


def _find_layout_slot(layout, widget):
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is widget:
            return layout, index
        child = item.layout()
        if child is not None:
            found = _find_layout_slot(child, widget)
            if found is not None:
                return found
    return None


def apply_govee_editor_overflow() -> None:
    base_class = legacy.GoveeZoneMapperDialog
    if getattr(base_class, "_edl_editor_overflow_applied", False):
        return

    class OverflowSafeGoveeZoneMapperDialog(base_class):
        _edl_editor_overflow_applied = True

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._edl_strip_drag_active = False
            self.strip.installEventFilter(self)
            self._edl_editor_panel = self.zone_name.parentWidget()
            self._edl_editor_scroll = None
            self._install_editor_scroll()
            self._refresh_editor_minimum()
            QTimer.singleShot(0, self._refresh_editor_minimum)

        def eventFilter(self, watched, event):  # type: ignore[override]
            if watched is self.strip:
                if (
                    event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton
                ):
                    self._edl_strip_drag_active = True
                elif (
                    event.type() == QEvent.Type.MouseButtonRelease
                    and event.button() == Qt.MouseButton.LeftButton
                ):
                    self._edl_strip_drag_active = False
                    QTimer.singleShot(0, self._refresh_section_orientation_editor)
            return super().eventFilter(watched, event)

        def _install_editor_scroll(self) -> None:
            panel = self._edl_editor_panel
            root = self.layout()
            if panel is None or root is None:
                return
            found = _find_layout_slot(root, panel)
            if found is None:
                return
            owner, index = found

            stretch = 0
            if hasattr(owner, "stretch"):
                try:
                    stretch = owner.stretch(index)
                except Exception:
                    stretch = 0

            owner.removeWidget(panel)
            scroll = QScrollArea(self)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            scroll.setWidget(panel)

            owner.insertWidget(index, scroll, stretch)
            self._edl_editor_scroll = scroll

        def _refresh_editor_minimum(self) -> None:
            panel = self._edl_editor_panel
            if panel is None or panel.layout() is None:
                return
            panel.setMinimumHeight(0)
            panel.layout().invalidate()
            panel.layout().activate()
            required = panel.sizeHint().height()
            if required > 0:
                panel.setMinimumHeight(required)
            panel.updateGeometry()
            if self._edl_editor_scroll is not None:
                self._edl_editor_scroll.updateGeometry()

        def _refresh_section_orientation_editor(self) -> None:
            if getattr(self, "_edl_strip_drag_active", False):
                return
            super()._refresh_section_orientation_editor()
            if hasattr(self, "_edl_editor_panel"):
                self._refresh_editor_minimum()
                QTimer.singleShot(0, self._refresh_editor_minimum)

    legacy.GoveeZoneMapperDialog = OverflowSafeGoveeZoneMapperDialog


def apply_govee_editor_lifecycle(ui_module: Any) -> None:
    # The final Govee mapper and disconnected-section mapper are already active
    # at this point. Add only the operator-surface corrections now.
    apply_govee_drag_scrub()
    apply_govee_editor_overflow()

    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_govee_editor_lifecycle_applied", False):
        return

    previous_set_editor_enabled = window_class._set_editor_enabled

    def set_editor_enabled(self, enabled: bool) -> None:
        previous_set_editor_enabled(self, enabled)
        configuration = getattr(self, "_govee_configuration", None)
        combo = getattr(self, "target_combo", None)
        if configuration is None or combo is None or not hasattr(combo, "_boxes"):
            return
        for target, (device, _zone) in configuration.target_map().items():
            box = combo._boxes.get(target)
            if box is not None:
                box.setEnabled(bool(enabled))

    window_class._set_editor_enabled = set_editor_enabled
    window_class._edl_govee_editor_lifecycle_applied = True
