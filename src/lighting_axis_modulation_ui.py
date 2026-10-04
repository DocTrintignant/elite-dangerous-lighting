#!/usr/bin/env python3
"""Clear operator UI for optional HID-axis output modulation.

One selected analogue control may drive output brightness, STATIC colour, or
both. This adapter is deliberately installed before independent-output
authoring so each RuleOutput keeps its own modulation binding.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lighting_axis_modulation import (
    AxisOutputModulation,
    COLOUR_BLEND,
    COLOUR_HUE_SPECTRUM,
)

FIXED = "Fixed"
FOLLOW = "Follow movement"


class ColourEndpointButton(QPushButton):
    changed = Signal()

    def __init__(self, rgb=(255, 255, 255), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rgb = tuple(rgb)
        self.clicked.connect(self._choose)
        self._refresh()

    def rgb(self) -> tuple[int, int, int]:
        return self._rgb

    def set_rgb(self, rgb: tuple[int, int, int], *, emit: bool = False) -> None:
        self._rgb = tuple(int(v) for v in rgb)
        self._refresh()
        if emit:
            self.changed.emit()

    def _refresh(self) -> None:
        r, g, b = self._rgb
        foreground = "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"
        self.setText(f"#{r:02X}{g:02X}{b:02X}")
        self.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b}); color:{foreground}; "
            "font-weight:600; min-width:92px; }"
        )

    def _choose(self) -> None:
        colour = QColorDialog.getColor(QColor(*self._rgb), self, "Choose axis end colour")
        if colour.isValid():
            self.set_rgb((colour.red(), colour.green(), colour.blue()), emit=True)


class AxisOutputModulationEditor(QFrame):
    changed = Signal()
    detectRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._loading = False
        self._effect = "STATIC"
        self._rest_colour = (255, 255, 255)
        self._binding_from_source = False
        self._expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 9, 10, 9)
        root.setSpacing(8)

        heading = QHBoxLayout()
        title = QLabel("Controller response")
        title.setStyleSheet("font-weight:600;")
        heading.addWidget(title)
        self.summary = QLabel("Fixed")
        self.summary.setProperty("edlTextRole", "secondary")
        heading.addWidget(self.summary)
        heading.addStretch(1)
        self.configure_toggle = QToolButton(self)
        self.configure_toggle.setText("Configure")
        self.configure_toggle.setCheckable(True)
        self.configure_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.configure_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.configure_toggle.setToolTip(
            "Optional — let an analogue controller movement vary this output."
        )
        heading.addWidget(self.configure_toggle)
        root.addLayout(heading)

        self.details_host = QWidget(self)
        details = QVBoxLayout(self.details_host)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(8)

        mode_grid = QGridLayout()
        mode_grid.setHorizontalSpacing(8)
        self.brightness_mode_label = QLabel("Brightness")
        mode_grid.addWidget(self.brightness_mode_label, 0, 0)
        self.brightness_mode = QComboBox()
        self.brightness_mode.addItems((FIXED, FOLLOW))
        mode_grid.addWidget(self.brightness_mode, 0, 1)
        self.colour_mode_label = QLabel("Colour")
        mode_grid.addWidget(self.colour_mode_label, 0, 2)
        self.colour_mode = QComboBox()
        self.colour_mode.addItems((FIXED, FOLLOW))
        mode_grid.addWidget(self.colour_mode, 0, 3)
        mode_grid.setColumnStretch(4, 1)
        details.addLayout(mode_grid)

        self.follow_host = QWidget(self.details_host)
        follow = QGridLayout(self.follow_host)
        follow.setContentsMargins(0, 2, 0, 0)
        follow.setHorizontalSpacing(8)
        follow.setVerticalSpacing(7)

        follow.addWidget(QLabel("Controller input"), 0, 0)
        self.device = QComboBox()
        self.device.setVisible(False)
        self.axis = QSpinBox()
        self.axis.setRange(0, 255)
        self.axis.setVisible(False)
        self._binding_ready = False

        self.binding_status = QLabel(
            "No control chosen yet. Click Detect movement, then move the stick, throttle, slider or rotary you want."
        )
        self.binding_status.setWordWrap(True)
        self.binding_status.setStyleSheet("font-weight:600;")
        follow.addWidget(self.binding_status, 0, 1, 1, 3)
        self.detect = QPushButton("Detect movement…")
        follow.addWidget(self.detect, 0, 4)

        follow.addWidget(QLabel("Live movement"), 1, 0)
        movement_host = QWidget(self.follow_host)
        movement = QHBoxLayout(movement_host)
        movement.setContentsMargins(0, 0, 0, 0)
        movement.setSpacing(8)
        self.left_end_label = QLabel("Left end")
        movement.addWidget(self.left_end_label)
        self.movement_bar = QProgressBar()
        self.movement_bar.setRange(0, 100)
        self.movement_bar.setValue(50)
        self.movement_bar.setTextVisible(False)
        self.movement_bar.setMaximumHeight(12)
        self.movement_bar.setStyleSheet(
            "QProgressBar { border:1px solid palette(mid); background:palette(base); padding:0; }"
            "QProgressBar::chunk { background:palette(highlight); }"
        )
        movement.addWidget(self.movement_bar, 1)
        self.right_end_label = QLabel("Right end")
        movement.addWidget(self.right_end_label)
        self.movement_value = QLabel("—")
        self.movement_value.setMinimumWidth(48)
        movement.addWidget(self.movement_value)
        follow.addWidget(movement_host, 1, 1, 1, 4)

        self.advanced_toggle = QToolButton(self.follow_host)
        self.advanced_toggle.setText("Calibration")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setToolTip(
            "Optional fine tuning for a control whose centre is not halfway or whose analogue signal jitters."
        )
        follow.addWidget(self.advanced_toggle, 2, 1, 1, 1)

        self.advanced_host = QWidget(self.follow_host)
        advanced = QGridLayout(self.advanced_host)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setHorizontalSpacing(8)
        advanced.addWidget(QLabel("Centre position"), 0, 0)
        self.rest_position = QDoubleSpinBox()
        self.rest_position.setRange(0.0, 100.0)
        self.rest_position.setDecimals(1)
        self.rest_position.setSuffix(" %")
        self.rest_position.setValue(50.0)
        self.rest_position.setToolTip(
            "Where the control's centre or neutral point sits in its travel. Most centred controls use 50%."
        )
        advanced.addWidget(self.rest_position, 0, 1)

        advanced.addWidget(QLabel("Jitter tolerance"), 0, 2)
        self.rest_zone = QDoubleSpinBox()
        self.rest_zone.setRange(0.0, 25.0)
        self.rest_zone.setDecimals(1)
        self.rest_zone.setSuffix(" %")
        self.rest_zone.setValue(3.0)
        self.rest_zone.setToolTip(
            "Movement this close to the centre is treated as centred so small analogue noise does not flicker the lights."
        )
        advanced.addWidget(self.rest_zone, 0, 3)
        self.override_detect = QPushButton("Use a different control…", self.advanced_host)
        self.override_detect.setVisible(False)
        advanced.addWidget(self.override_detect, 1, 0, 1, 4)

        self.advanced_host.setVisible(False)
        def toggle_advanced(checked: bool) -> None:
            self.advanced_host.setVisible(checked)
            self.advanced_toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )
        self.advanced_toggle.toggled.connect(toggle_advanced)
        follow.addWidget(self.advanced_host, 3, 1, 1, 4)

        self.brightness_host = QWidget(self.follow_host)
        bg = QGridLayout(self.brightness_host)
        bg.setContentsMargins(0, 3, 0, 0)
        bg.setHorizontalSpacing(8)
        bg.setVerticalSpacing(6)
        bright_title = QLabel("Brightness")
        bright_title.setStyleSheet("font-weight:600;")
        bg.addWidget(bright_title, 0, 0, 1, 6)

        bg.addWidget(QLabel("Left end"), 1, 0)
        self.minus_change = QDoubleSpinBox()
        self.minus_change.setRange(0.0, 100.0)
        self.minus_change.setDecimals(0)
        self.minus_change.setSuffix(" %")
        self.minus_change.setValue(100.0)
        bg.addWidget(self.minus_change, 1, 1)

        bg.addWidget(QLabel("Centre"), 1, 2)
        self.at_rest = QDoubleSpinBox()
        self.at_rest.setRange(0.0, 100.0)
        self.at_rest.setDecimals(0)
        self.at_rest.setSuffix(" %")
        self.at_rest.setValue(100.0)
        bg.addWidget(self.at_rest, 1, 3)

        bg.addWidget(QLabel("Right end"), 1, 4)
        self.plus_change = QDoubleSpinBox()
        self.plus_change.setRange(0.0, 100.0)
        self.plus_change.setDecimals(0)
        self.plus_change.setSuffix(" %")
        self.plus_change.setValue(100.0)
        bg.addWidget(self.plus_change, 1, 5)
        self.minus_result = QLabel("")
        self.minus_result.setVisible(False)
        self.plus_result = QLabel("")
        self.plus_result.setVisible(False)
        follow.addWidget(self.brightness_host, 4, 0, 1, 5)

        self.colour_host = QWidget(self.follow_host)
        cg = QGridLayout(self.colour_host)
        cg.setContentsMargins(0, 3, 0, 0)
        cg.setHorizontalSpacing(8)
        cg.setVerticalSpacing(6)
        colour_title = QLabel("Colour")
        colour_title.setStyleSheet("font-weight:600;")
        cg.addWidget(colour_title, 0, 0, 1, 6)

        cg.addWidget(QLabel("Left end"), 1, 0)
        self.minus_colour = ColourEndpointButton((255, 0, 0), self.colour_host)
        cg.addWidget(self.minus_colour, 1, 1)

        cg.addWidget(QLabel("Centre"), 1, 2)
        self.rest_colour = ColourEndpointButton((255, 255, 255), self.colour_host)
        cg.addWidget(self.rest_colour, 1, 3)

        cg.addWidget(QLabel("Right end"), 1, 4)
        self.plus_colour = ColourEndpointButton((0, 0, 255), self.colour_host)
        cg.addWidget(self.plus_colour, 1, 5)

        cg.addWidget(QLabel("Transition"), 2, 0)
        self.colour_transition = QComboBox()
        self.colour_transition.addItem("Straight blend", COLOUR_BLEND)
        self.colour_transition.addItem("Around the colour spectrum", COLOUR_HUE_SPECTRUM)
        cg.addWidget(self.colour_transition, 2, 1, 1, 2)
        follow.addWidget(self.colour_host, 5, 0, 1, 5)

        follow.setColumnStretch(1, 1)
        follow.setColumnStretch(3, 1)
        details.addWidget(self.follow_host)
        root.addWidget(self.details_host)

        self.configure_toggle.toggled.connect(self._expanded_changed)
        self.brightness_mode.currentTextChanged.connect(self._mode_changed)
        self.colour_mode.currentTextChanged.connect(self._mode_changed)
        self.device.currentIndexChanged.connect(self._changed)
        self.axis.valueChanged.connect(self._changed)
        self.rest_position.valueChanged.connect(self._changed)
        self.rest_zone.valueChanged.connect(self._changed)
        self.at_rest.valueChanged.connect(self._brightness_changed)
        self.minus_change.valueChanged.connect(self._brightness_changed)
        self.plus_change.valueChanged.connect(self._brightness_changed)
        self.minus_colour.changed.connect(self._changed)
        self.rest_colour.changed.connect(self._changed)
        self.plus_colour.changed.connect(self._changed)
        self.colour_transition.currentIndexChanged.connect(self._changed)
        self.detect.clicked.connect(self.detectRequested)
        self.override_detect.clicked.connect(self.detectRequested)
        self._sync()

    def _changed(self, *_args) -> None:
        if not self._loading:
            self.changed.emit()

    def _sync_change_ranges(self) -> None:
        # The UI exposes absolute brightness at each position. The domain model
        # still stores signed changes from the normal-position brightness.
        for spin in (self.minus_change, self.plus_change):
            blocked = spin.blockSignals(True)
            try:
                spin.setRange(0.0, 100.0)
            finally:
                spin.blockSignals(blocked)

    def _brightness_changed(self, *_args) -> None:
        self._update_results()
        self._changed()

    def _mode_changed(self, *_args) -> None:
        following = (
            self.brightness_mode.currentText() == FOLLOW
            or self.colour_mode.currentText() == FOLLOW
        )
        if following:
            self._set_expanded(True)
        self._sync()
        self._changed()

    def _expanded_changed(self, checked: bool) -> None:
        self._set_expanded(bool(checked))

    def _set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        blocked = self.configure_toggle.blockSignals(True)
        try:
            self.configure_toggle.setChecked(self._expanded)
        finally:
            self.configure_toggle.blockSignals(blocked)
        self.configure_toggle.setArrowType(
            Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.RightArrow
        )
        self.details_host.setVisible(self._expanded)

    def _update_results(self) -> None:
        # Absolute endpoint values are already what the operator sees.
        return

    def _sync(self) -> None:
        brightness_follow = self.brightness_mode.currentText() == FOLLOW
        colour_follow = self.colour_mode.currentText() == FOLLOW
        if brightness_follow and colour_follow:
            self.summary.setText("Brightness + colour follow movement")
        elif brightness_follow:
            self.summary.setText("Brightness follows movement")
        elif colour_follow:
            self.summary.setText("Colour follows movement")
        else:
            self.summary.setText("Fixed")
        self.follow_host.setVisible(brightness_follow or colour_follow)
        self.brightness_host.setVisible(brightness_follow)
        self.colour_host.setVisible(colour_follow)
        self._sync_change_ranges()
        self._update_results()

    def set_effect(self, effect: str, rest_colour: tuple[int, int, int]) -> None:
        self._effect = effect
        self._rest_colour = tuple(rest_colour)
        self.rest_colour.set_rgb(self._rest_colour)

        self._loading = True
        try:
            # V1 product contract: Controller response is intentionally a
            # STATIC-only authoring feature.  The underlying modulation domain
            # remains readable for existing profiles, but animated effects keep
            # their own effect-local behaviour instead of exposing a second
            # controller-modulation layer in normal authoring.
            brightness_supported = effect == "STATIC"
            if not brightness_supported and self.brightness_mode.currentText() == FOLLOW:
                self.brightness_mode.setCurrentText(FIXED)
            self.brightness_mode.setVisible(brightness_supported)
            self.brightness_mode_label.setVisible(brightness_supported)
            self.brightness_mode.setToolTip(
                "Let controller movement vary STATIC output brightness continuously."
            )
            colour_supported = effect == "STATIC"
            if not colour_supported and self.colour_mode.currentText() == FOLLOW:
                self.colour_mode.setCurrentText(FIXED)
            self.colour_mode.setVisible(colour_supported)
            self.colour_mode_label.setVisible(colour_supported)
            self.colour_mode.setToolTip(
                "Colour can follow controller movement for STATIC outputs."
            )
            self.setVisible(brightness_supported or colour_supported)
        finally:
            self._loading = False
        self._sync()

    def populate_devices(
        self,
        devices: Iterable[object],
        labels: dict[str, str],
        *,
        selected: str | None = None,
    ) -> None:
        current = selected
        if current is None:
            data = self.device.currentData()
            current = data if isinstance(data, str) else None
        previous_loading = self._loading
        self._loading = True
        try:
            self.device.clear()
            for device in devices:
                device_id = getattr(device, "device_id", None)
                if not isinstance(device_id, str):
                    continue
                label = labels.get(device_id, getattr(device, "label", device_id))
                self.device.addItem(label, device_id)
            if current:
                index = next(
                    (i for i in range(self.device.count()) if self.device.itemData(i) == current),
                    -1,
                )
                if index < 0:
                    self.device.addItem("Stored controller (not currently detected)", current)
                    index = self.device.count() - 1
                self.device.setCurrentIndex(index)
            elif self.device.count():
                self.device.setCurrentIndex(0)
            else:
                self.device.setCurrentIndex(-1)
                self.device.setPlaceholderText("No controller devices detected")
        finally:
            self._loading = previous_loading

    def load(
        self,
        modulation: AxisOutputModulation | None,
        *,
        fixed_brightness: float,
        rest_colour: tuple[int, int, int],
        devices: Iterable[object],
        labels: dict[str, str],
    ) -> None:
        self._loading = True
        try:
            self.populate_devices(
                devices,
                labels,
                selected=(modulation.device if modulation is not None else None),
            )
            self.axis.setValue(0 if modulation is None else modulation.axis)
            self._binding_ready = modulation is not None
            self._binding_from_source = False
            detected_ids = {
                getattr(device, "device_id", None)
                for device in devices
                if isinstance(getattr(device, "device_id", None), str)
            }
            if modulation is None:
                self.binding_status.setText("No controller input selected.")
                self.detect.setVisible(True)
                self.detect.setText("Detect movement…")
            elif modulation.device not in detected_ids:
                self.binding_status.setText("Controller input unavailable.")
                self.detect.setVisible(True)
                self.detect.setText("Change control…")
            else:
                controller = self.device.currentText().strip() or "controller"
                self.binding_status.setText(
                    f"Axis {modulation.axis} on {controller}."
                )
                self.detect.setVisible(True)
                self.detect.setText("Change control…")
            self.override_detect.setVisible(False)
            self.rest_position.setValue(50.0 if modulation is None else modulation.rest_position)
            self.rest_zone.setValue(3.0 if modulation is None else modulation.rest_zone)

            if modulation is not None and modulation.brightness_enabled:
                self.brightness_mode.setCurrentText(FOLLOW)
                assert modulation.brightness_at_rest is not None
                rest_brightness = modulation.brightness_at_rest * 100.0
                self.at_rest.setValue(rest_brightness)
                self.minus_change.setValue(rest_brightness + modulation.brightness_minus_change * 100.0)
                self.plus_change.setValue(rest_brightness + modulation.brightness_plus_change * 100.0)
            else:
                self.brightness_mode.setCurrentText(FIXED)
                fixed_percent = fixed_brightness * 100.0
                self.at_rest.setValue(fixed_percent)
                self.minus_change.setValue(fixed_percent)
                self.plus_change.setValue(fixed_percent)

            if modulation is not None and modulation.colour_enabled:
                self.colour_mode.setCurrentText(FOLLOW)
                assert modulation.colour_minus is not None
                assert modulation.colour_plus is not None
                self.minus_colour.set_rgb(modulation.colour_minus)
                self.plus_colour.set_rgb(modulation.colour_plus)
                index = self.colour_transition.findData(modulation.colour_transition)
                self.colour_transition.setCurrentIndex(max(0, index))
            else:
                self.colour_mode.setCurrentText(FIXED)
                self.minus_colour.set_rgb(rest_colour)
                self.plus_colour.set_rgb(rest_colour)
                self.colour_transition.setCurrentIndex(0)
        finally:
            self._loading = False
        self._set_expanded(modulation is not None)
        self.set_effect(self._effect, rest_colour)
        self._update_results()

    def modulation(self) -> AxisOutputModulation | None:
        brightness_follow = self.brightness_mode.currentText() == FOLLOW
        colour_follow = self.colour_mode.currentText() == FOLLOW
        if not brightness_follow and not colour_follow:
            return None

        device = self.device.currentData()
        if not isinstance(device, str) or not device or not self._binding_ready:
            raise ValueError(
                "This rule is set to follow a controller movement, but no controller input has been chosen. Under Controller response, click Detect movement… and move the control you want to use, or set Brightness and Colour to Fixed."
            )

        rest_brightness = self.at_rest.value()
        return AxisOutputModulation(
            device=device,
            axis=self.axis.value(),
            rest_position=self.rest_position.value(),
            rest_zone=self.rest_zone.value(),
            brightness_at_rest=(rest_brightness / 100.0 if brightness_follow else None),
            brightness_minus_change=((self.minus_change.value() - rest_brightness) / 100.0 if brightness_follow else 0.0),
            brightness_plus_change=((self.plus_change.value() - rest_brightness) / 100.0 if brightness_follow else 0.0),
            colour_minus=(self.minus_colour.rgb() if colour_follow else None),
            colour_plus=(self.plus_colour.rgb() if colour_follow else None),
            colour_transition=self.colour_transition.currentData() or COLOUR_BLEND,
        )

    def use_source_binding(
        self,
        device_id: str,
        axis: int,
        semantic_name: str,
        label: str,
        *,
        devices: Iterable[object],
        labels: dict[str, str],
    ) -> None:
        previous = self._loading
        self._loading = True
        try:
            self.populate_devices(devices, labels, selected=device_id)
            self.axis.setValue(axis)
            self._binding_ready = True
            self._binding_from_source = True
            self.binding_status.setText(
                f"Using Rule control: {semantic_name} on {label}."
            )
            self.detect.setVisible(False)
            self.override_detect.setVisible(True)
        finally:
            self._loading = previous

    def set_live_position(self, percent: int | None) -> None:
        if percent is None:
            self.movement_value.setText("—")
            return
        value = max(0, min(100, int(percent)))
        self.movement_bar.setValue(value)
        self.movement_value.setText(f"{value}%")

    def detected(
        self,
        device_id: str,
        axis: int,
        semantic_name: str,
        value: float,
        label: str,
    ) -> None:
        self.populate_devices((), {}, selected=device_id)
        self.axis.setValue(axis)
        self._binding_ready = True
        self._binding_from_source = False
        self.binding_status.setText(
            f"Using {semantic_name} on {label}."
        )
        self.detect.setVisible(True)
        self.detect.setText("Change control…")
        self.override_detect.setVisible(False)
        self.changed.emit()


def apply_axis_modulation_ui(ui_module: Any) -> None:
    """Install the output-level Follow axis editor on the final Rules UI."""

    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_axis_modulation_ui_applied", False):
        return

    previous_build_effect = window_class._build_effect_section
    previous_load_draft = window_class._load_draft
    previous_build_rule = window_class._build_rule_from_editor
    previous_refresh_hid = window_class.refresh_hid_devices
    previous_effect_changed = window_class._effect_changed
    previous_set_editor_enabled = window_class._set_editor_enabled

    def build_effect_section(self):
        section = previous_build_effect(self)
        self.axis_modulation_editor = AxisOutputModulationEditor(self)
        self.axis_modulation_editor.changed.connect(self._editor_changed)
        self.axis_modulation_editor.detectRequested.connect(
            lambda: detect_modulation_axis(self)
        )
        self.palette_editor.paletteChanged.connect(
            lambda *_args: sync_rest_colour(self)
        )
        self.axis_modulation_editor.rest_colour.changed.connect(
            lambda: sync_palette_from_rest_colour(self)
        )
        # Keep output modulation beside the effect parameters it modifies.
        # The full colour picker is visually large, so placing this after it
        # makes Follow axis look detached or missing on a normal viewport.
        palette_index = section.root.indexOf(self.palette_editor)
        if palette_index >= 0:
            section.root.insertWidget(palette_index, self.axis_modulation_editor)
        else:
            section.root.addWidget(self.axis_modulation_editor)
        return section

    def load_draft(self, rule, *, title: str) -> None:
        previous_load_draft(self, rule, title=title)
        if not hasattr(self, "axis_modulation_editor"):
            return
        devices = getattr(self, "_hid_devices", ())
        labels = getattr(self, "_hid_display_labels", {})
        self.axis_modulation_editor._effect = rule.effect
        self.axis_modulation_editor.load(
            rule.axis_modulation,
            fixed_brightness=rule.effect_parameters.brightness,
            rest_colour=rule.colour,
            devices=devices,
            labels=labels,
        )
        sync_fixed_brightness(self)
        sync_colour_editor_visibility(self)
        source_axis = getattr(rule, "axis", None)
        modulation = rule.axis_modulation
        same_as_source = (
            source_axis is not None
            and modulation is not None
            and modulation.device == source_axis.device
            and modulation.axis == source_axis.axis
        )
        if modulation is None or same_as_source:
            sync_response_binding_from_rule_source(self)

    def build_rule_from_editor(self):
        rule = previous_build_rule(self)
        modulation = self.axis_modulation_editor.modulation()
        parameters = rule.effect_parameters
        if modulation is not None and modulation.brightness_enabled:
            parameters = replace(parameters, brightness=1.0)
        return replace(
            rule,
            effect_parameters=parameters,
            axis_modulation=modulation,
        )

    def refresh_hid_devices(self, *args, **kwargs) -> None:
        previous_refresh_hid(self, *args, **kwargs)
        editor = getattr(self, "axis_modulation_editor", None)
        if editor is not None:
            editor.populate_devices(
                getattr(self, "_hid_devices", ()),
                getattr(self, "_hid_display_labels", {}),
            )

    def sync_rest_colour(self) -> None:
        editor = getattr(self, "axis_modulation_editor", None)
        if editor is None:
            return
        palette = self.palette_editor.palette()
        if palette:
            editor.set_effect(self.effect_combo.currentText(), palette[0])

    def sync_palette_from_rest_colour(self) -> None:
        editor = getattr(self, "axis_modulation_editor", None)
        if editor is None or editor.colour_mode.currentText() != FOLLOW:
            return
        current = tuple(self.palette_editor.palette())
        if not current:
            current = (editor.rest_colour.rgb(),)
        else:
            current = (editor.rest_colour.rgb(), *current[1:])
        self.palette_editor.set_palette(current)
        sync_rest_colour(self)
        self._editor_changed()

    def sync_colour_editor_visibility(self) -> None:
        editor = getattr(self, "axis_modulation_editor", None)
        if editor is None:
            return
        follows_colour = editor.colour_mode.currentText() == FOLLOW
        effect = self.effect_combo.currentText().strip().upper()
        self.palette_editor.setVisible(
            not follows_colour and effect != "SPECTRUM"
        )

    def effect_changed(self, effect: str) -> None:
        previous_effect_changed(self, effect)
        sync_rest_colour(self)
        sync_fixed_brightness(self)
        sync_colour_editor_visibility(self)

    def set_editor_enabled(self, enabled: bool) -> None:
        previous_set_editor_enabled(self, enabled)
        editor = getattr(self, "axis_modulation_editor", None)
        if editor is not None:
            editor.setEnabled(bool(enabled))

    def sync_fixed_brightness(self) -> None:
        editor = getattr(self, "axis_modulation_editor", None)
        controls = getattr(self, "effect_controls", None)
        if editor is None or controls is None:
            return
        follow = editor.brightness_mode.currentText() == FOLLOW
        brightness_pair = getattr(controls, "brightness", None)
        if brightness_pair is None:
            return
        label, spin = brightness_pair
        label.setVisible(not follow)
        spin.setVisible(not follow)
        label.setToolTip(
            "Brightness is controlled by Controller response below."
            if follow
            else "Fixed brightness for this effect."
        )

    def sync_response_binding_from_rule_source(self) -> None:
        editor = getattr(self, "axis_modulation_editor", None)
        if editor is None:
            return
        following = (
            editor.brightness_mode.currentText() == FOLLOW
            or editor.colour_mode.currentText() == FOLLOW
        )
        if not following or self.source_type_combo.currentText() != "Axis":
            return
        device_id = self.axis_device.currentData()
        if not isinstance(device_id, str) or not device_id:
            return
        axis = self.axis_number.value()
        if editor._binding_ready and not editor._binding_from_source:
            current_device = editor.device.currentData()
            current_axis = editor.axis.value()
            if current_device != device_id or current_axis != axis:
                return
        semantic = self._axis_semantic_names.get((device_id, axis))
        if not semantic:
            shown = getattr(self, "axis_binding_label", None)
            text = shown.text().strip() if shown is not None else ""
            if text and "Detect axis" not in text and "stored axis" not in text.lower():
                semantic = text
        if not semantic:
            semantic = "selected axis"
        label = getattr(self, "_hid_display_labels", {}).get(
            device_id,
            self.axis_device.currentText() or "Selected controller",
        )
        editor.use_source_binding(
            device_id,
            axis,
            semantic,
            label,
            devices=getattr(self, "_hid_devices", ()),
            labels=getattr(self, "_hid_display_labels", {}),
        )

    def modulation_mode_changed(self) -> None:
        sync_fixed_brightness(self)
        sync_colour_editor_visibility(self)
        sync_response_binding_from_rule_source(self)
        self._editor_changed()

    def detect_modulation_axis(self) -> None:
        stop_source = getattr(self, "_stop_axis_monitor", None)
        if callable(stop_source):
            stop_source()
        self.refresh_hid_devices(log=False)
        dialog = ui_module.AxisDetectDialog(self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            selected = dialog.selected_axis()
            if selected is None:
                return
            device, axis, semantic_name, value = selected
            self.refresh_hid_devices(log=False)
            self._axis_semantic_names[(device.device_id, axis)] = semantic_name
            label = getattr(self, "_hid_display_labels", {}).get(device.device_id, device.label)
            self.axis_modulation_editor.populate_devices(
                getattr(self, "_hid_devices", ()),
                getattr(self, "_hid_display_labels", {}),
                selected=device.device_id,
            )
            self.axis_modulation_editor.axis.setValue(axis)
            self.axis_modulation_editor._binding_ready = True
            self.axis_modulation_editor._binding_from_source = False
            self.axis_modulation_editor.binding_status.setText(
                f"Using {semantic_name} on {label}."
            )
            self.axis_modulation_editor.detect.setVisible(True)
            self.axis_modulation_editor.detect.setText("Change control…")
            self.axis_modulation_editor.override_detect.setVisible(False)
            self.axis_modulation_editor.set_live_position(round(value))
            self._editor_changed()
        finally:
            refresh_source = getattr(self, "_refresh_axis_monitor", None)
            if callable(refresh_source):
                refresh_source()

    previous_axis_binding_changed = getattr(window_class, "_axis_binding_changed", None)

    def axis_binding_changed(self, *args) -> None:
        if callable(previous_axis_binding_changed):
            previous_axis_binding_changed(self, *args)
        editor = getattr(self, "axis_modulation_editor", None)
        if editor is None or not getattr(editor, "_binding_from_source", False):
            return
        sync_response_binding_from_rule_source(self)

    # Keep the fixed Brightness control and Follow axis mode mutually legible.
    original_mode_changed = AxisOutputModulationEditor._mode_changed

    def linked_mode_changed(editor, *args) -> None:
        original_mode_changed(editor, *args)
        owner = editor.window()
        if isinstance(owner, window_class):
            sync_fixed_brightness(owner)
            sync_colour_editor_visibility(owner)
            sync_response_binding_from_rule_source(owner)

    AxisOutputModulationEditor._mode_changed = linked_mode_changed

    def axis_position_observed(self, device_id: str, axis: int, _value: float, percent: int) -> None:
        editor = getattr(self, "axis_modulation_editor", None)
        if editor is None or not editor._binding_ready:
            return
        if editor.device.currentData() != device_id or editor.axis.value() != axis:
            return
        editor.set_live_position(percent)
        if editor._binding_from_source:
            semantic = self._axis_semantic_names.get((device_id, axis), "selected axis")
            label = getattr(self, "_hid_display_labels", {}).get(
                device_id,
                self.axis_device.currentText() or "selected controller",
            )
            editor.binding_status.setText(
                f"Using Rule control: {semantic} on {label}."
            )

    window_class._build_effect_section = build_effect_section
    window_class._load_draft = load_draft
    window_class._build_rule_from_editor = build_rule_from_editor
    window_class.refresh_hid_devices = refresh_hid_devices
    window_class._effect_changed = effect_changed
    window_class._set_editor_enabled = set_editor_enabled
    window_class._axis_position_observed = axis_position_observed
    if callable(previous_axis_binding_changed):
        window_class._axis_binding_changed = axis_binding_changed
    window_class._edl_axis_modulation_ui_applied = True
