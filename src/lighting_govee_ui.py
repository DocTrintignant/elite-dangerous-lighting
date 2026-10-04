#!/usr/bin/env python3
"""Interactive Enhanced-Govee topology/zone editor for the desktop UI.

The mapper owns machine geometry only. It does not create new rule semantics.
Saved H61C3 zones become ordinary leaf targets in the existing multi-target rule
selector. Live tracing temporarily owns only the configured H61C3 and releases
it when tracing or the dialog ends.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lighting_ui_tokens import TEXT_SECONDARY

from lighting_govee_config import (
    GoveeConfiguration,
    GoveeEnhancedDevice,
    GoveeZone,
    friendly_target_name,
    load_govee_configuration,
    save_govee_configuration,
)
from lighting_govee_transport import GoveeRealtimeSession

DEFAULT_DEVICE_ID = "h61c3-desk"
TRACE_SELECTED = (255, 255, 255)
TRACE_UNSELECTED = (0, 0, 12)
TRACE_INTERVAL_MS = 50
TRACE_ACTIVATION_WAIT_SECONDS = 0.100


def _zone_id_from_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip().lower()).strip("-")
    return value or "zone"


def _segment_summary(segments: tuple[int, ...]) -> str:
    if not segments:
        return "none"
    ranges: list[str] = []
    start = previous = segments[0]
    for value in segments[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


class GoveeZoneMapperDialog(QDialog):
    """Map physical cockpit regions onto the proven 42-entry H61C3 address space."""

    def __init__(
        self,
        configuration: GoveeConfiguration,
        *,
        used_targets: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enhanced Govee cockpit zones")
        self.setMinimumSize(900, 720)
        self._configuration = configuration
        self._used_targets = set() if used_targets is None else set(used_targets)
        self._device = next(
            (
                device
                for device in configuration.devices
                if device.device_id == DEFAULT_DEVICE_ID
            ),
            next(
                (
                    device
                    for device in configuration.devices
                    if device.sku.upper() == "H61C3"
                ),
                None,
            ),
        )
        if self._device is None:
            raise ValueError(
                "No discovered or configured Govee H61C3 is available for zone mapping."
            )
        self._zones = list(self._device.zones)
        self._editing_zone_id: str | None = None
        self._trace_session: GoveeRealtimeSession | None = None
        self._trace_timer = QTimer(self)
        self._trace_timer.setInterval(TRACE_INTERVAL_MS)
        self._trace_timer.timeout.connect(self._render_trace)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Define broad physical cockpit regions on the native 42-segment H61C3. "
            "Logical segment 1 is the physically observed RIGHT end; increasing segment "
            "numbers move toward the LEFT. Zones cannot overlap."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        device_grid = QGridLayout()
        device_grid.addWidget(QLabel("Device"), 0, 0)
        device_grid.addWidget(QLabel("Govee H61C3 — 42 native segments"), 0, 1)
        device_grid.addWidget(QLabel("IPv4 address"), 1, 0)
        self.ip_edit = QLineEdit(self._device.ip)
        self.ip_edit.setPlaceholderText("Example: 192.168.1.50")
        device_grid.addWidget(self.ip_edit, 1, 1)
        device_grid.setColumnStretch(1, 1)
        root.addLayout(device_grid)

        body = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(QLabel("Named cockpit zones"))
        self.zone_list = QListWidget()
        self.zone_list.currentItemChanged.connect(self._zone_selected)
        left.addWidget(self.zone_list, 1)
        zone_actions = QHBoxLayout()
        new_button = QPushButton("New zone")
        new_button.clicked.connect(self._new_zone)
        zone_actions.addWidget(new_button)
        delete_button = QPushButton("Delete zone")
        delete_button.clicked.connect(self._delete_zone)
        zone_actions.addWidget(delete_button)
        left.addLayout(zone_actions)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        zone_name_row = QHBoxLayout()
        zone_name_row.addWidget(QLabel("Zone name"))
        self.zone_name = QLineEdit()
        self.zone_name.setPlaceholderText("Example: Left console")
        zone_name_row.addWidget(self.zone_name, 1)
        self.save_zone_button = QPushButton("Add zone")
        self.save_zone_button.clicked.connect(self._save_zone)
        zone_name_row.addWidget(self.save_zone_button)
        right.addLayout(zone_name_row)

        direction = QHBoxLayout()
        right_label = QLabel("PHYSICAL RIGHT  ←  segment 1")
        right_label.setStyleSheet("font-weight:600;")
        direction.addWidget(right_label)
        direction.addStretch(1)
        left_label = QLabel("segment 42  →  PHYSICAL LEFT")
        left_label.setStyleSheet("font-weight:600;")
        direction.addWidget(left_label)
        right.addLayout(direction)

        self.segment_buttons: dict[int, QPushButton] = {}
        segment_grid = QGridLayout()
        segment_grid.setHorizontalSpacing(4)
        segment_grid.setVerticalSpacing(4)
        for index in range(1, self._device.segment_count + 1):
            button = QPushButton(str(index))
            button.setCheckable(True)
            button.setMinimumSize(52, 34)
            button.toggled.connect(self._selection_changed)
            self.segment_buttons[index] = button
            segment_grid.addWidget(button, (index - 1) // 7, (index - 1) % 7)
        right.addLayout(segment_grid)

        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Select range"))
        self.range_start = QSpinBox()
        self.range_start.setRange(1, self._device.segment_count)
        self.range_start.setValue(1)
        range_row.addWidget(self.range_start)
        range_row.addWidget(QLabel("to"))
        self.range_end = QSpinBox()
        self.range_end.setRange(1, self._device.segment_count)
        self.range_end.setValue(self._device.segment_count)
        range_row.addWidget(self.range_end)
        select_range = QPushButton("Select")
        select_range.clicked.connect(self._select_range)
        range_row.addWidget(select_range)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_selection)
        range_row.addWidget(clear)
        range_row.addStretch(1)
        right.addLayout(range_row)

        self.selection_label = QLabel("No segments selected")
        self.selection_label.setStyleSheet(f"color:{TEXT_SECONDARY};")
        right.addWidget(self.selection_label)

        trace_row = QHBoxLayout()
        self.trace_button = QPushButton("Trace selected segments live")
        self.trace_button.setToolTip(
            "Temporarily acquire only this configured Govee device. Selected positions are bright white; "
            "all others are near-black blue. Stopping the trace releases the device."
        )
        self.trace_button.clicked.connect(self._toggle_trace)
        trace_row.addWidget(self.trace_button)
        self.trace_status = QLabel("")
        self.trace_status.setStyleSheet(f"color:{TEXT_SECONDARY};")
        trace_row.addWidget(self.trace_status, 1)
        right.addLayout(trace_row)
        body.addLayout(right, 3)
        root.addLayout(body, 1)

        note = QLabel(
            "Zone IDs remain stable when you rename a zone so saved lighting profiles keep their target address. "
            "A rule can select several named zones to address the whole rope without introducing overlapping output priorities."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:9.75pt;")
        root.addWidget(note)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        save = QPushButton("Save device mapping")
        save.clicked.connect(self._save_configuration)
        bottom.addWidget(save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        root.addLayout(bottom)

        self._refresh_zone_list()
        self._new_zone()

    @property
    def configuration(self) -> GoveeConfiguration:
        return self._configuration

    def _claimed_segments(self, *, excluding: str | None = None) -> set[int]:
        return {
            segment
            for zone in self._zones
            if zone.zone_id != excluding
            for segment in zone.segments
        }

    def _set_selected_segments(self, segments: set[int]) -> None:
        for index, button in self.segment_buttons.items():
            button.blockSignals(True)
            button.setChecked(index in segments)
            button.blockSignals(False)
        self._selection_changed()

    def _selected_segments(self) -> tuple[int, ...]:
        return tuple(index for index, button in self.segment_buttons.items() if button.isChecked())

    def _update_segment_availability(self) -> None:
        claimed = self._claimed_segments(excluding=self._editing_zone_id)
        for index, button in self.segment_buttons.items():
            button.setEnabled(index not in claimed)
            if index in claimed and button.isChecked():
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self, *_args) -> None:
        selected = self._selected_segments()
        self.selection_label.setText(
            f"Selected: {_segment_summary(selected)}" if selected else "No segments selected"
        )
        if self._trace_session is not None:
            self._render_trace()

    def _select_range(self) -> None:
        start = min(self.range_start.value(), self.range_end.value())
        end = max(self.range_start.value(), self.range_end.value())
        wanted = {
            index
            for index in range(start, end + 1)
            if self.segment_buttons[index].isEnabled()
        }
        self._set_selected_segments(wanted)

    def _clear_selection(self) -> None:
        self._set_selected_segments(set())

    def _refresh_zone_list(self, selected_id: str | None = None) -> None:
        self.zone_list.blockSignals(True)
        self.zone_list.clear()
        selected_row = -1
        for row, zone in enumerate(self._zones):
            item = QListWidgetItem(f"{zone.name} — {_segment_summary(zone.segments)}")
            item.setData(Qt.ItemDataRole.UserRole, zone.zone_id)
            self.zone_list.addItem(item)
            if zone.zone_id == selected_id:
                selected_row = row
        self.zone_list.blockSignals(False)
        if selected_row >= 0:
            self.zone_list.setCurrentRow(selected_row)

    def _new_zone(self) -> None:
        self.zone_list.clearSelection()
        self._editing_zone_id = None
        self.zone_name.clear()
        self.save_zone_button.setText("Add zone")
        self._set_selected_segments(set())
        self._update_segment_availability()

    def _zone_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        zone_id = current.data(Qt.ItemDataRole.UserRole)
        zone = next((value for value in self._zones if value.zone_id == zone_id), None)
        if zone is None:
            return
        self._editing_zone_id = zone.zone_id
        self.zone_name.setText(zone.name)
        self.save_zone_button.setText("Update zone")
        self._update_segment_availability()
        self._set_selected_segments(set(zone.segments))

    def _unique_zone_id(self, name: str) -> str:
        base = _zone_id_from_name(name)
        used = {zone.zone_id for zone in self._zones}
        if base not in used:
            return base
        suffix = 2
        while f"{base}-{suffix}" in used:
            suffix += 1
        return f"{base}-{suffix}"

    def _save_zone(self) -> None:
        name = self.zone_name.text().strip()
        segments = self._selected_segments()
        if not name:
            QMessageBox.information(self, "Zone name required", "Give this cockpit region a name first.")
            return
        if not segments:
            QMessageBox.information(self, "Select segments", "Select at least one physical H61C3 segment for this zone.")
            return

        if self._editing_zone_id is None:
            zone = GoveeZone(self._unique_zone_id(name), name, segments)
            self._zones.append(zone)
        else:
            row = next(
                (index for index, zone in enumerate(self._zones) if zone.zone_id == self._editing_zone_id),
                None,
            )
            if row is None:
                return
            zone = GoveeZone(self._editing_zone_id, name, segments)
            self._zones[row] = zone

        self._editing_zone_id = zone.zone_id
        self._refresh_zone_list(zone.zone_id)
        self._update_segment_availability()

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
                "Zone is used by the current profile",
                "This zone is referenced by the loaded profile. Change those rules before deleting its physical mapping.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Delete cockpit zone",
            f"Delete {zone.name!r}? Other saved profiles that reference this zone target would need to be updated.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._zones = [value for value in self._zones if value.zone_id != zone_id]
        self._refresh_zone_list()
        self._new_zone()

    def _trace_frame(self) -> tuple[tuple[int, int, int], ...]:
        selected = set(self._selected_segments())
        return tuple(
            TRACE_SELECTED if index in selected else TRACE_UNSELECTED
            for index in range(1, self._device.segment_count + 1)
        )

    def _render_trace(self) -> None:
        session = self._trace_session
        if session is None:
            return
        try:
            session.render_segments(self._trace_frame())
        except Exception as exc:
            self._stop_trace()
            QMessageBox.warning(self, "Live segment trace stopped", str(exc))

    def _toggle_trace(self) -> None:
        if self._trace_session is not None:
            self._stop_trace()
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
            QMessageBox.warning(self, "Cannot trace H61C3", str(exc))
            return
        self.trace_button.setText("Stop live trace")
        self.trace_status.setText("Native H61C3 ownership active — selected segments are white")

    def _stop_trace(self) -> None:
        self._trace_timer.stop()
        session = self._trace_session
        self._trace_session = None
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                self.trace_status.setText(f"Release warning: {exc}")
                return
        self.trace_button.setText("Trace selected segments live")
        self.trace_status.setText("H61C3 released to its previous state")

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
                device
                for device in self._configuration.devices
                if device.device_id != replacement.device_id
            ]
            devices.append(replacement)
            configuration = GoveeConfiguration(tuple(devices))
            save_govee_configuration(configuration)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot save Govee mapping", str(exc))
            return
        self._device = replacement
        self._configuration = configuration
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        if self._trace_session is not None:
            self._stop_trace()
        super().done(result)


def apply_govee_ui(ui_module: Any) -> None:
    """Expose configured enhanced zones in the existing final rule editor."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_govee_ui_applied", False):
        return

    previous_init = window_class.__init__
    previous_build_header = window_class._build_header
    previous_build_target_section = window_class._build_target_section
    previous_refresh_table = window_class._refresh_table
    previous_runnable_targets = window_class._runnable_targets
    previous_update_profile_buttons = window_class._update_profile_buttons

    def load_configuration(self) -> None:
        try:
            self._govee_configuration = load_govee_configuration()
            self._govee_configuration_error = None
        except Exception as exc:
            self._govee_configuration = GoveeConfiguration()
            self._govee_configuration_error = str(exc)

    def sync_target_selector(self) -> None:
        combo = getattr(self, "target_combo", None)
        if combo is None or not hasattr(combo, "_ensure_target"):
            return
        configuration = self._govee_configuration
        active_targets = set(configuration.target_map())
        for target, (device, zone) in configuration.target_map().items():
            combo._ensure_target(target)
            box = combo._boxes[target]
            if zone is None:
                box.setText(f"{device.name}: ALL")
                box.setToolTip(
                    f"Enhanced native Govee whole device — all {device.segment_count} native positions"
                )
            else:
                box.setText(f"{device.name}: {zone.name}")
                box.setToolTip(
                    f"Enhanced native Govee zone — segments {_segment_summary(zone.segments)}"
                )
            box.setVisible(True)
            box.setEnabled(self._editing_row is not None or self._new_rule_draft)
        for target, box in combo._boxes.items():
            if target.startswith("GOVEE_ENHANCED::") and target not in active_targets:
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
                box.setVisible(False)

    def __init__(self) -> None:
        load_configuration(self)
        previous_init(self)
        sync_target_selector(self)
        if self._govee_configuration_error:
            self.statusBar().showMessage(
                f"Enhanced Govee configuration could not be loaded: {self._govee_configuration_error}",
                9000,
            )

    def build_header(self):
        row = previous_build_header(self)
        self.govee_zones_button = QPushButton("Govee zones…")
        self.govee_zones_button.setToolTip(
            "Configure Enhanced Govee devices, verify or calibrate their native positions, and group those positions into named cockpit zones."
        )
        self.govee_zones_button.clicked.connect(self._show_govee_zone_mapper)
        row.addWidget(self.govee_zones_button)
        return row

    def build_target_section(self):
        section = previous_build_target_section(self)
        QTimer.singleShot(0, lambda: sync_target_selector(self))
        return section

    def show_govee_zone_mapper(self) -> None:
        if self._live_running() or self._preview_running():
            QMessageBox.information(
                self,
                "Stop lighting first",
                "Stop live lighting or effect preview before tracing physical Govee segments. One renderer owns a device at a time.",
            )
            return
        if getattr(self, "_editor_dirty", False) or getattr(self, "_new_rule_draft", False):
            QMessageBox.information(
                self,
                "Finish the current rule first",
                "Apply or cancel the current rule draft before changing physical zone topology.",
            )
            return
        if not any(
            device.sku.upper() == "H61C3"
            for device in self._govee_configuration.devices
        ):
            QMessageBox.information(
                self,
                "No Govee H61C3 available",
                "Scan Lighting devices first. A discovered H61C3 can be used without "
                "a saved Govee configuration file; custom zones become available "
                "after the device is discovered.",
            )
            return
        used_targets = {
            target
            for rule in self._profile.rules
            for target in rule.targets
        } if self._profile is not None else set()
        dialog = GoveeZoneMapperDialog(
            self._govee_configuration,
            used_targets=used_targets,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._govee_configuration = dialog.configuration
        self._govee_configuration_error = None
        sync_target_selector(self)
        self._refresh_table(self.selected_rows())
        self._update_profile_buttons()
        self._logger.event(
            "GOVEE_ZONE_MAPPING_SAVED",
            devices=len(self._govee_configuration.devices),
            zones=sum(len(device.zones) for device in self._govee_configuration.devices),
        )
        self.statusBar().showMessage("Enhanced Govee cockpit-zone mapping saved.", 5000)

    def refresh_table(self, selected_rows=None) -> None:
        previous_refresh_table(self, selected_rows)
        profile = getattr(self, "_profile", None)
        if profile is None:
            return
        configuration = self._govee_configuration
        for row, rule in enumerate(profile.rules):
            item = self.table.item(row, 1)
            if item is None:
                continue
            parts = [friendly_target_name(configuration, target) for target in rule.targets]
            # Preserve the already-applied ChromaLink UI's richer CL1..CL5 display
            # whenever no Enhanced-Govee target is present in this rule.
            if any(target.startswith("GOVEE_ENHANCED::") for target in rule.targets):
                item.setText(" + ".join(parts))

    def runnable_targets(self) -> set[str]:
        result = set(previous_runnable_targets(self))
        if self._profile is None:
            return result
        configured_targets = set(self._govee_configuration.target_map())
        result.update(
            target
            for rule in self._profile.rules
            if rule.enabled
            for target in rule.targets
            if target in configured_targets
        )
        return result

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        button = getattr(self, "govee_zones_button", None)
        if button is not None:
            button.setEnabled(not self._live_running() and not self._preview_running())
        sync_target_selector(self)

    window_class.__init__ = __init__
    window_class._build_header = build_header
    window_class._build_target_section = build_target_section
    window_class._show_govee_zone_mapper = show_govee_zone_mapper
    window_class._refresh_table = refresh_table
    window_class._runnable_targets = runnable_targets
    window_class._update_profile_buttons = update_profile_buttons
    window_class._edl_govee_ui_applied = True
