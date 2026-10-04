#!/usr/bin/env python3
"""Sparse user-facing help for the right-hand lighting rule editor.

Long explanations live on deliberate '?' affordances rather than ordinary
controls. This module changes UI presentation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QRegion
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lighting_effect_config import BREATH
from lighting_ui_header_disposition import RENDER_RATE_TOOLTIP
from lighting_ui_language import EFFECT_HELP, MEDIA_HELP, MODE_HELP, PHASE_HELP, TARGET_HELP
from lighting_ui_tokens import ACCENT
from lighting_version import EDL_VERSION


EDL_PROJECT_URL = "https://github.com/DocTrintignant/elite-dangerous-lighting"
PLUGIN_PROJECT_URL = "https://github.com/DocTrintignant/covas-next-plugins"
CHROMAS_NEXT_URL = f"{PLUGIN_PROJECT_URL}/tree/main/ChromasNext"
COVASIFY_URL = f"{PLUGIN_PROJECT_URL}/tree/main/Covasify"


def _open_external_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class AboutEdlDialog(QDialog):
    """Small read-only product identity surface."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About EDL")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        title = QLabel("<b>Elite Dangerous Lighting</b>")
        title.setStyleSheet("font-size: 13pt;")
        layout.addWidget(title)

        self.version_label = QLabel(f"Version {EDL_VERSION}")
        self.version_label.setProperty("edlTextRole", "secondary")
        layout.addWidget(self.version_label)

        summary = QLabel(
            "Reactive RGB lighting for Elite Dangerous, driven by live game state, "
            "hardware input, Rules, scripted Modes, and optional COVAS:NEXT commands."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


SECTION_HELP = {
    "RULE": (
        "<b>Rule</b><br><br>"
        "A rule tells EDL when to react and what lighting to show.<br><br>"
        "Rules are checked from top to bottom. If more than one active rule controls "
        "the same output, the later matching rule controls that output."
    ),
    "SOURCE": (
        "<b>Source</b><br><br>"
        "Choose what activates this rule.<br><br>"
        "<b>Argument</b> follows Elite game state.<br>"
        "<b>Keyboard</b> follows a key combination.<br>"
        "<b>Button</b> follows a cockpit button or switch.<br>"
        "<b>Axis</b> follows an analogue control such as a stick, throttle or rotary.<br><br>"
        "An Argument rule may contain several conditions; every condition must be true "
        "at the same time."
    ),
    "TARGET": TARGET_HELP,
    "EFFECT": EFFECT_HELP,
    "MODE": MODE_HELP,
    "PHASE": PHASE_HELP,
    "MEDIA": MEDIA_HELP,
}

HELP_TEXT = {
    "keyboard_record": (
        "<b>Record keys</b><br><br>"
        "Press the full key combination you want to use. EDL shows what it captured, "
        "but nothing is assigned until you choose <b>Use combination</b>."
    ),
    "button_device": (
        "<b>Device</b><br><br>"
        "Choose the controller that contains the button or switch you want to use."
    ),
    "axis_device": (
        "<b>Device</b><br><br>"
        "Choose the controller that contains the axis you want to use."
    ),
    "button_number": (
        "<b>Button</b><br><br>"
        "The button or switch input on the selected controller.<br><br>"
        "If you do not know the number, use <b>Detect button / switch</b> instead."
    ),
    "button_detect": (
        "<b>Detect button / switch</b><br><br>"
        "Move or press the cockpit control you want to use.<br><br>"
        "EDL shows the inputs it detects. Choose the correct one and confirm it. "
        "Nothing is assigned until you confirm your choice."
    ),
    "button_state": (
        "<b>State</b><br><br>"
        "Choose which state activates the rule.<br><br>"
        "<b>Pressed</b> means the controller reports this button or switch as on.<br>"
        "<b>Released</b> means it reports it as off."
    ),
    "axis_index": (
        "<b>Axis index</b><br><br>"
        "Choose which analogue control on the selected controller you want to use.<br><br>"
        "The same controller may expose several axes for stick movement, throttle, brakes, "
        "sliders or rotary controls. The number identifies which one EDL reads."
    ),
    "axis_condition": (
        "<b>Axis condition</b><br><br>"
        "Choose which part of the axis movement activates the rule.<br><br>"
        "For example, <b>More than 70</b> activates above 70 and <b>Less than 20</b> "
        "activates below 20.<br><br>"
        "<b>BETWEEN</b> activates while the axis is between the two values, including both "
        "boundary values. The limits may be entered in either order."
    ),
    "colours": (
        "<b>Colours</b><br><br>"
        "These are the colours used by the selected effect, in the order shown. "
        "The editor limits the palette to the number of colours that has defined meaning "
        "for that effect.<br><br>"
        "For example, STATIC and PULSE use one configured colour; FLASH, BREATH, WAVE and "
        "FIRE can use several. SPECTRUM generates its own rainbow and therefore hides the "
        "palette."
    ),
    "brightness": (
        "<b>Brightness</b><br><br>"
        "For effects that expose this control, Brightness scales the effect's visible output. "
        "100% uses the selected colours at full brightness; lower values dim the effect.<br><br>"
        "PULSE uses its separate Minimum and Maximum brightness controls instead."
    ),
    "time_per_colour": (
        "<b>Time per colour</b><br><br>"
        "For FLASH, this is how long each colour stays on before EDL moves to the next colour."
    ),
    "pulse_cycle": (
        "<b>Cycle time</b><br><br>"
        "For PULSE, this is the time for one complete movement from minimum brightness to "
        "maximum brightness and back to minimum."
    ),
    "breath_cycle": (
        "<b>Cycle time</b><br><br>"
        "For BREATH, this is how long the effect takes to move through every configured colour "
        "and return to the first."
    ),
    "minimum_brightness": (
        "<b>Minimum brightness</b><br><br>The dimmest point reached during a PULSE."
    ),
    "maximum_brightness": (
        "<b>Maximum brightness</b><br><br>The brightest point reached during a PULSE."
    ),
    "test_output": (
        "<b>Test rule</b><br><br>"
        "Test only the Rule currently open in the editor on your real lights. "
        "The rest of the profile does not run.<br><br>"
        "EDL waits for this Rule's conditions or controls exactly as it normally would. "
        "If the Rule uses <b>All connected devices</b>, the test uses the lighting devices "
        "that are selected and available now.<br><br>"
        "Use <b>Preview effect</b> when you only want to see how the effect looks. "
        "Choose <b>Stop test</b> when you are done."
    ),
}


class HelpButton(QToolButton):
    """Small deliberate help target; ordinary controls stay quiet."""

    def __init__(self, help_html: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText("?")
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFixedSize(22, 22)
        self.setStyleSheet(
            "QToolButton { font-weight: 600; border: 1px solid #666666; "
            "border-radius: 11px; padding: 0px; } "
            "QToolButton:hover { border-color: #AFAFAF; }"
        )
        self.set_help(help_html)

    def set_help(self, help_html: str) -> None:
        self.setToolTip(f"<qt>{help_html}</qt>" if help_html else "")
        self.setToolTipDuration(30000)


def _layout_containing(root: QLayout | None, widget: QWidget) -> QLayout | None:
    if root is None:
        return None
    for index in range(root.count()):
        item = root.itemAt(index)
        if item.widget() is widget:
            return root
        nested = item.layout()
        if nested is not None:
            found = _layout_containing(nested, widget)
            if found is not None:
                return found
    return None


def _insert_after(layout: QLayout, widget: QWidget, new_widget: QWidget) -> None:
    index = layout.indexOf(widget)
    if index < 0:
        return
    if isinstance(layout, QHBoxLayout):
        layout.insertWidget(index + 1, new_widget)
    else:
        layout.addWidget(new_widget)


def _replace_grid_label_with_help(
    grid: QGridLayout,
    row: int,
    column: int,
    help_html: str,
) -> HelpButton | None:
    item = grid.itemAtPosition(row, column)
    label = item.widget() if item is not None else None
    if not isinstance(label, QLabel):
        return None
    grid.removeWidget(label)
    label.setToolTip("")
    host = QWidget()
    line = QHBoxLayout(host)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(4)
    line.addWidget(label)
    help_button = HelpButton(help_html, host)
    line.addWidget(help_button)
    line.addStretch(1)
    grid.addWidget(host, row, column)
    return help_button


def _technical_device_footer(window: Any, combo: Any) -> str:
    device_id = combo.currentData()
    if not isinstance(device_id, str) or not device_id:
        return ""
    device = next(
        (item for item in getattr(window, "_hid_devices", ()) if item.device_id == device_id),
        None,
    )
    if device is None:
        return "<br><br><hr><b>Technical:</b> saved controller is not currently detected."
    return (
        "<br><br><hr>"
        f"<b>Technical:</b> VID {device.vendor_id:04X}, PID {device.product_id:04X}"
    )


def install_section_help(base_module: Any) -> None:
    """Install one canonical section-help affordance on every SectionBox."""
    section_class = base_module.SectionBox
    if getattr(section_class, "_edl_section_help_installed", False):
        return

    original_section_init = section_class.__init__

    def section_init(self, title: str, subtitle: str | None = None, parent=None) -> None:
        original_section_init(self, title, subtitle, parent)
        help_html = SECTION_HELP.get(title)
        if not help_html:
            return
        heading_layout = self.root.itemAt(0).layout()
        if not isinstance(heading_layout, QHBoxLayout):
            return
        for index in range(heading_layout.count()):
            widget = heading_layout.itemAt(index).widget()
            if (
                isinstance(widget, QToolButton)
                and widget.objectName() == "sectionHelpButton"
            ):
                return
        button = HelpButton(help_html, self)
        button.setObjectName("sectionHelpButton")
        heading_layout.insertWidget(1, button)

    section_class.__init__ = section_init
    section_class._edl_section_help_installed = True


def apply_right_panel_help(app_module: Any) -> None:
    """Install the sparse help layer before MainWindow is constructed."""

    base = app_module.base
    install_section_help(base)

    # Move long Argument help from the ordinary combo to a deliberate '?'.
    row_class = app_module.HumanArgumentConditionRow
    original_row_init = row_class.__init__
    original_source_changed = row_class._source_changed

    def sync_argument_help(row) -> None:
        help_html = row.source.toolTip()
        if help_html:
            row._argument_help_html = help_html
        else:
            help_html = getattr(row, "_argument_help_html", "")
        row.source.setToolTip("")
        for index in range(row.source.count()):
            row.source.setItemData(index, None, Qt.ItemDataRole.ToolTipRole)
        button = getattr(row, "_argument_help_button", None)
        if button is not None:
            if help_html:
                button.set_help(help_html.removeprefix("<qt>").removesuffix("</qt>"))
            else:
                button.set_help(
                    "<b>Argument</b><br><br>Choose the Elite game state or value "
                    "you want this condition to follow."
                )

    def row_init(self, *args, **kwargs) -> None:
        original_row_init(self, *args, **kwargs)
        button = HelpButton(parent=self)
        self._argument_help_button = button
        layout = self.layout()
        if isinstance(layout, QHBoxLayout):
            layout.insertWidget(1, button)
        sync_argument_help(self)

    def source_changed(self, name: str) -> None:
        original_source_changed(self, name)
        sync_argument_help(self)

    row_class.__init__ = row_init
    row_class._source_changed = source_changed

    # Replace effect-parameter hover text with deliberate '?' help.
    original_parameters_class = app_module.EffectParametersEditor

    class HelpEffectParametersEditor(original_parameters_class):
        def __init__(self, parent=None) -> None:
            self._help_by_label = {}
            super().__init__(parent)
            pairs = (
                (self.step_seconds, "time_per_colour"),
                (self.cycle_seconds, "pulse_cycle"),
                (self.brightness, "brightness"),
                (self.minimum_brightness, "minimum_brightness"),
                (self.maximum_brightness, "maximum_brightness"),
            )
            for pair, help_key in pairs:
                label, spin = pair
                label.setToolTip("")
                spin.setToolTip("")
                button = HelpButton(HELP_TEXT[help_key], self)
                index = self._row.indexOf(label)
                self._row.insertWidget(index + 1, button)
                self._help_by_label[label] = button
            self._sync_help(self._effect)

        def _set_pair_visible(self, pair, visible: bool) -> None:
            super()._set_pair_visible(pair, visible)
            button = getattr(self, "_help_by_label", {}).get(pair[0])
            if button is not None:
                button.setVisible(visible)

        def _sync_help(self, effect: str) -> None:
            cycle_button = getattr(self, "_help_by_label", {}).get(self.cycle_seconds[0])
            if cycle_button is not None:
                cycle_button.set_help(
                    HELP_TEXT["breath_cycle"] if effect == BREATH else HELP_TEXT["pulse_cycle"]
                )
            for pair in (
                self.step_seconds,
                self.cycle_seconds,
                self.brightness,
                self.minimum_brightness,
                self.maximum_brightness,
            ):
                button = getattr(self, "_help_by_label", {}).get(pair[0])
                if button is not None:
                    button.setVisible(pair[0].isVisible())

        def set_effect(self, effect, parameters) -> None:
            super().set_effect(effect, parameters)
            self._sync_help(effect)

    app_module.EffectParametersEditor = HelpEffectParametersEditor

    # One palette help point. Add/Remove/Save are intentionally left to explain themselves.
    original_palette_class = app_module.PersistentPaletteActionsEditor

    class HelpPaletteEditor(original_palette_class):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            title = next(
                (label for label in self.findChildren(QLabel) if label.text() == "Colours"),
                None,
            )
            if title is not None:
                layout = _layout_containing(self.layout(), title)
                if layout is not None:
                    _insert_after(layout, title, HelpButton(HELP_TEXT["colours"], self))
            self.add_button.setToolTip("")
            self.remove_button.setToolTip("")
            self.remove_saved_button.setToolTip("")
            for button in self.findChildren(app_module.QPushButton):
                if button.text() == "Save colour":
                    button.setToolTip("")

        def _update_cardinality_controls(self) -> None:
            super()._update_cardinality_controls()
            self.add_button.setToolTip("")
            self.remove_button.setToolTip("")

    app_module.PersistentPaletteActionsEditor = HelpPaletteEditor

    window_class = app_module.MainWindow

    # Rewrite the detection dialog in the same user-facing tone.
    dialog_class = app_module.ButtonDetectDialog
    original_dialog_init = dialog_class.__init__

    def dialog_init(self, *args, **kwargs) -> None:
        original_dialog_init(self, *args, **kwargs)
        for label in self.findChildren(QLabel):
            if label.text().startswith("Operate the desired cockpit control"):
                label.setText(
                    "Move or press the cockpit control you want to use. "
                    "EDL will show the inputs it detects. Choose the correct one and "
                    "select Use selected. Nothing is assigned until you confirm your choice."
                )
            elif label.text() == "Listening for controller button presses…":
                label.setText("Waiting for you to move or press a cockpit control…")

    dialog_class.__init__ = dialog_init

    original_keyboard_builder = window_class._build_keyboard_source

    def build_keyboard_source(self):
        panel = original_keyboard_builder(self)
        record = next(
            (
                button for button in panel.findChildren(app_module.QPushButton)
                if button.text() == "Record keys…"
            ),
            None,
        )
        if record is not None:
            record.setToolTip("")
            layout = _layout_containing(panel.layout(), record)
            if layout is not None:
                _insert_after(layout, record, HelpButton(HELP_TEXT["keyboard_record"], panel))
        return panel

    window_class._build_keyboard_source = build_keyboard_source

    original_button_builder = window_class._build_button_source

    def build_button_source(self):
        panel = original_button_builder(self)
        grid = panel.layout()
        if isinstance(grid, QGridLayout):
            device_help = _replace_grid_label_with_help(grid, 0, 0, HELP_TEXT["button_device"])
            _replace_grid_label_with_help(grid, 1, 0, HELP_TEXT["button_number"])
            _replace_grid_label_with_help(grid, 2, 0, HELP_TEXT["button_state"])
            self._button_device_help = device_help
            self.detect_button.setToolTip("")
            grid.addWidget(HelpButton(HELP_TEXT["button_detect"], panel), 1, 3)

            if device_help is not None:
                def update_button_device_help(*_args) -> None:
                    device_help.set_help(
                        HELP_TEXT["button_device"] + _technical_device_footer(self, self.button_device)
                    )
                self.button_device.currentIndexChanged.connect(update_button_device_help)
                self._update_button_device_help = update_button_device_help
        return panel

    window_class._build_button_source = build_button_source

    original_axis_builder = window_class._build_axis_source

    def build_axis_source(self):
        panel = original_axis_builder(self)
        grid = panel.layout()
        if isinstance(grid, QGridLayout):
            device_help = _replace_grid_label_with_help(grid, 0, 0, HELP_TEXT["axis_device"])
            _replace_grid_label_with_help(grid, 1, 0, HELP_TEXT["axis_index"])
            _replace_grid_label_with_help(grid, 2, 0, HELP_TEXT["axis_condition"])
            self._axis_device_help = device_help

            axis_item = grid.itemAtPosition(1, 1)
            if axis_item is not None and axis_item.widget() is not None:
                axis_item.widget().setToolTip("")
            value_item = grid.itemAtPosition(3, 1)
            if value_item is not None and value_item.widget() is not None:
                value_item.widget().setToolTip("")

            if device_help is not None:
                def update_axis_device_help(*_args) -> None:
                    device_help.set_help(
                        HELP_TEXT["axis_device"] + _technical_device_footer(self, self.axis_device)
                    )
                self.axis_device.currentIndexChanged.connect(update_axis_device_help)
                self._update_axis_device_help = update_axis_device_help
        return panel

    window_class._build_axis_source = build_axis_source

    # Device cells stay quiet; VID/PID is exposed deliberately through Device '?'.
    original_populate_devices = window_class._populate_device_combo

    def populate_device_combo(self, combo, selected_device_id=None) -> None:
        original_populate_devices(self, combo, selected_device_id)
        combo.setToolTip("")
        for index in range(combo.count()):
            combo.setItemData(index, None, Qt.ItemDataRole.ToolTipRole)
        if combo is getattr(self, "button_device", None):
            updater = getattr(self, "_update_button_device_help", None)
            if updater is not None:
                updater()
        if combo is getattr(self, "axis_device", None):
            updater = getattr(self, "_update_axis_device_help", None)
            if updater is not None:
                updater()

    window_class._populate_device_combo = populate_device_combo

    original_effect_builder = window_class._build_effect_section

    def build_effect_section(self):
        section = original_effect_builder(self)
        self.preview_button.setToolTip("")
        layout = _layout_containing(section.layout(), self.preview_button)
        if layout is not None:
            self._test_output_help = HelpButton(HELP_TEXT["test_output"], section)
            _insert_after(layout, self.preview_button, self._test_output_help)
        return section

    window_class._build_effect_section = build_effect_section

    # Base code may rewrite Preview output's passive tooltip when the target changes.
    # Keep the button quiet and keep the deliberate '?' on the canonical Preview help.
    original_update_preview = window_class._update_test_output_state

    def update_test_output_state(self) -> None:
        original_update_preview(self)
        if hasattr(self, "preview_button"):
            self.preview_button.setToolTip("")
        help_button = getattr(self, "_test_output_help", None)
        if help_button is not None:
            help_button.set_help(HELP_TEXT["test_output"])

    window_class._update_test_output_state = update_test_output_state


# Plain-language hover help belongs to the same user-guidance owner.
BUTTON_HELP = {
    "Open profile": "Open an EDL lighting profile from your computer.",
    "Save profile": "Save the changes you have made to the EDL profile currently open.",
    "Save profile as": "Save the current EDL profile as a new file, leaving the original file unchanged.",
    "Import VIRPIL profile": (
        "Read a VIRPIL Link Tool .led.json file and create an editable EDL profile from it. "
        "When you later save, EDL writes its own profile file and does not overwrite the VIRPIL file you imported."
    ),
    "Live data & testing": (
        "Open tools for seeing the live Elite data EDL receives, trying rule conditions without changing hardware, and enabling troubleshooting logs."
    ),
    "Chroma zones": (
        "Create named areas on your Chroma devices, such as WASD, Function row or part of a mouse. "
        "Whole device is always available without creating a zone."
    ),
    "Chroma zone setup": (
        "Create named areas on your Chroma devices, such as WASD, Function row or part of a mouse. "
        "Whole device is always available without creating a zone."
    ),
    "Govee zone setup": (
        "Find and configure Govee lights for native named zones. Verified layouts work immediately; other discovered Govee devices can be physically calibrated. "
        "Other lighting devices do not require this Govee setup."
    ),
    "+ Add rule": "Create a new lighting rule. You will choose what activates it, which device it controls and what lighting effect it produces.",
    "Duplicate": "Make a copy of the selected rule or rules so you can modify the copy without rebuilding it from scratch.",
    "Delete": "Remove the selected rule or item. Where deletion needs confirmation, EDL will ask before applying it.",
    "↑": "Move the selected rule or rules one place higher. Rule order matters when more than one matching rule controls the same target.",
    "↓": "Move the selected rule or rules one place lower. Lower matching rules win when they control the same target.",
    "Copy": "Copy the selected rule or rules to EDL's rule clipboard.",
    "Cut": "Copy the selected rule or rules to EDL's rule clipboard and remove them from the current profile.",
    "Paste": "Insert the rules currently stored in EDL's rule clipboard.",
    "Cancel changes": "Discard the changes currently being made in the rule editor and return to the saved/applied rule state.",
    "Apply rule": "Apply the values currently shown in the editor to this rule. This changes the profile in memory; use Save profile to write it to disk.",
    "+ Add condition": "Add another game-state condition to this Argument rule. Every condition in the rule must be true at the same time.",
    "Remove": "Remove this condition from the current rule.",
    "Record keys…": "Press the keyboard combination you want this rule to react to and let EDL fill it in for you.",
    "Detect button / switch…": "Move or press the cockpit button or switch you want to use and let EDL identify it.",
    "Detect axis…": "Move the stick, throttle, slider or rotary you want to use and let EDL identify that axis.",
    "Preview output": "Temporarily show the current editor effect on one supported physical target without starting the full live profile.",
    "Stop preview": "Stop the temporary effect preview and release the previewed device.",
    "Confirm delete": "Confirm that the selected rule or rules should be removed from the current profile.",
    "Cancel": "Close or cancel this operation without applying the pending change.",
}

COMBO_HELP = {
    "render_rate_combo": RENDER_RATE_TOOLTIP,
}


def _set_button_help(button: QAbstractButton) -> None:
    help_text = BUTTON_HELP.get(button.text().strip())
    if not help_text:
        return
    button.setToolTip(help_text)
    if hasattr(button, "setToolTipDuration"):
        button.setToolTipDuration(30000)


def _find_visible_govee_button(window) -> QPushButton | None:
    return next(
        (
            button
            for button in window.findChildren(QPushButton)
            if button.text().strip() == "Govee zone setup"
        ),
        None,
    )


def _make_govee_setup_direct(window) -> None:
    hidden = getattr(window, "govee_zones_button", None)
    if not isinstance(hidden, QPushButton):
        return
    visible = _find_visible_govee_button(window)
    if visible is None:
        return
    visible.setMenu(None)
    visible.clicked.connect(hidden.click)
    visible.setEnabled(hidden.isEnabled())
    _set_button_help(visible)


def _refresh_dynamic_help(window) -> None:
    # Start/Stop help is owned by the readiness model so the tooltip can state
    # the current blocking reason rather than a generic action description.
    preview = getattr(window, "preview_button", None)
    if isinstance(preview, QAbstractButton) and not preview.toolTip():
        _set_button_help(preview)


def _apply_to_window(window) -> None:
    for button in window.findChildren(QAbstractButton):
        _set_button_help(button)

    for attribute, help_text in COMBO_HELP.items():
        widget = getattr(window, attribute, None)
        if isinstance(widget, QComboBox):
            widget.setToolTip(help_text)
            widget.setToolTipDuration(30000)

    _make_govee_setup_direct(window)
    _refresh_dynamic_help(window)


def apply_human_tooltips(ui_module: Any) -> None:
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_human_tooltips_applied", False):
        return

    previous_init = window_class.__init__
    previous_update_profile_buttons = window_class._update_profile_buttons

    def __init__(self) -> None:
        previous_init(self)
        _apply_to_window(self)

    def update_profile_buttons(self) -> None:
        previous_update_profile_buttons(self)
        _refresh_dynamic_help(self)
        visible = _find_visible_govee_button(self)
        hidden = getattr(self, "govee_zones_button", None)
        if visible is not None and isinstance(hidden, QPushButton):
            visible.setEnabled(hidden.isEnabled())

    window_class.__init__ = __init__
    window_class._update_profile_buttons = update_profile_buttons
    window_class._edl_human_tooltips_applied = True


# Guided first-run product tour. This deliberately stays in the existing
# user-guidance owner: it changes presentation only and never edits profiles,
# starts renderers, discovers devices, or acquires hardware.
GUIDED_TOUR_COMPLETED_KEY = "onboarding/guided_tour_v1_completed"


def guided_tour_completed(settings) -> bool:
    value = settings.value(GUIDED_TOUR_COMPLETED_KEY, False)
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def remember_guided_tour_completed(settings) -> None:
    settings.setValue(GUIDED_TOUR_COMPLETED_KEY, True)
    settings.sync()


@dataclass(frozen=True)
class GuidedTourStep:
    title: str
    body: str
    targets: tuple[str, ...] = ()
    workspace: str | None = None
    presentation: str | None = None


GUIDED_TOUR_STEPS = (
    GuidedTourStep(
        "Welcome to Elite Dangerous Lighting",
        (
            "This quick tour walks through the main EDL workflow: your profile, a Rule, "
            "a Scripted Mode, important setup choices, and Start Lighting. It focuses "
            "on the most common path; the rest of EDL remains available normally. "
            "Every example shown by the tour disappears when the tour closes."
        ),
    ),
    GuidedTourStep(
        "Your profile",
        (
            "A profile is one complete cockpit-lighting setup. Open it, edit it and "
            "save it here. Rules and Scripted Modes are stored together."
        ),
        ("header_profile_menu_button",),
        "rules",
    ),
    GuidedTourStep(
        "Rules",
        (
            "Rules handle normal reactive lighting. Choose + Add rule and the editor "
            "opens on the right. The next steps show that real editor without adding "
            "anything to your profile."
        ),
        ("add_rule_button",),
        "rules",
    ),
    GuidedTourStep(
        "Source — when should it happen?",
        (
            "Source is the trigger. This example uses two Elite states: InMainShip "
            "and LandingGearDown. When an Argument Rule has more than one condition, "
            "every condition must be true at the same time — they are ANDed."
        ),
        ("_edl_compact_source_section",),
        "rules",
        "rule_demo",
    ),
    GuidedTourStep(
        "Rule order matters",
        (
            "Rules are evaluated from top to bottom. If two matching Rules control "
            "the same output, the later Rule wins for that output. Use the arrows or "
            "drag the rows when you need to change that order."
        ),
        ("table", "move_up_button", "move_down_button"),
        "rules",
        "rule_demo",
    ),
    GuidedTourStep(
        "Target — which lights?",
        (
            "Target chooses the light, device or zone this Rule controls. One game "
            "state can therefore produce different results on different parts of "
            "your cockpit."
        ),
        ("_edl_compact_target_section",),
        "rules",
        "rule_demo",
    ),
    GuidedTourStep(
        "Effect — what should the lights do?",
        (
            "Effect chooses the visible result: colour, animation, timing and any "
            "effect-specific controls. EDL only shows controls that have meaning for "
            "the selected effect."
        ),
        ("_edl_compact_effect_section",),
        "rules",
        "rule_demo",
    ),
    GuidedTourStep(
        "Preview, then apply",
        (
            "Preview output lets you try the editor result before it becomes part of "
            "the Rule. Apply changes keeps the edit in the profile; Save profile writes "
            "the profile to disk."
        ),
        ("preview_header_action", "apply_rule_button"),
        "rules",
        "rule_demo",
    ),
    GuidedTourStep(
        "Scripted Modes",
        (
            "Modes are for timed sequences: alerts, power-up routines, show lighting "
            "and similar choreography. Choose + Add mode to create one. The next steps "
            "show a temporary Mode without adding it to your profile."
        ),
        ("covas_modes_mode_button", "_covas_modes_workspace.new_mode_button"),
        "modes",
    ),
    GuidedTourStep(
        "Mode name — also your COVAS:NEXT handle",
        (
            "Give the Mode a clear name. When Chromas Next is connected, COVAS:NEXT "
            "can invoke a saved EDL Mode by that name; the same Mode can still be "
            "previewed and used locally inside EDL."
        ),
        ("_covas_modes_workspace._edl_compact_mode_section",),
        "modes",
        "mode_demo",
    ),
    GuidedTourStep(
        "Optional music — with Covasify",
        (
            "A Mode can optionally carry a Spotify music request. Enter the track title "
            "and artist here. Covasify is the optional COVAS:NEXT Spotify plugin; this "
            "music request is used only when the Chromas Next Covasify bridge is enabled. "
            "The Mode lighting still works without Covasify."
        ),
        ("_covas_modes_workspace.media_cue_section",),
        "modes",
        "mode_demo",
    ),
    GuidedTourStep(
        "Phases — build the timeline",
        (
            "A Mode runs from top to bottom. Each row is one timed phase. In this "
            "temporary example, Alert runs first and Hold follows it."
        ),
        ("_covas_modes_workspace.phase_table",),
        "modes",
        "mode_demo",
    ),
    GuidedTourStep(
        "Phase details",
        (
            "Select a phase and set its name and duration here. These values control "
            "how long EDL stays on that step before moving to the next one."
        ),
        ("_covas_modes_workspace._edl_compact_phase_section",),
        "modes",
        "mode_demo",
    ),
    GuidedTourStep(
        "Outputs — what runs during this phase?",
        (
            "A phase can drive more than one lighting output at the same time. Each "
            "output chooses its own target, so one phase can coordinate several parts "
            "of the cockpit."
        ),
        ("_covas_modes_workspace._edl_compact_target_section",),
        "modes",
        "mode_demo",
    ),
    GuidedTourStep(
        "Effects inside a Mode",
        (
            "Each Mode output uses the same effect language as Rules. Choose the "
            "effect and colours here for the currently selected output."
        ),
        ("_covas_modes_workspace._edl_compact_effect_section",),
        "modes",
        "mode_demo",
    ),
    GuidedTourStep(
        "Preview the whole Mode",
        (
            "Preview mode runs the complete sequence so you can check the choreography "
            "and timing before applying the Mode."
        ),
        ("_covas_modes_workspace.preview_header_action",),
        "modes",
        "mode_demo",
    ),
    GuidedTourStep(
        "Devices & integrations",
        (
            "These are the first Setup items to check. Lighting devices shows what "
            "hardware EDL can see and which integration owns it. Lighting integrations "
            "is where the available lighting backends are configured."
        ),
        (
            "menu_action:header_setup_menu:Lighting devices",
            "menu_action:header_setup_menu:Lighting integrations",
        ),
        "rules",
        "setup_devices",
    ),
    GuidedTourStep(
        "Zones — choose smaller parts of a device",
        (
            "Rules and Modes can target named zones instead of a whole device. "
            "Configure Chroma zones or Govee zones here; once created, those named "
            "areas become normal lighting targets in the editors."
        ),
        (
            "menu_action:header_setup_menu:Zones",
            "menu_action:header_zones_menu:Chroma zones",
            "menu_action:header_zones_menu:Govee zones",
        ),
        "rules",
        "setup_zones",
    ),
    GuidedTourStep(
        "COVAS:NEXT connection",
        (
            "If you use Chromas Next, this is where you check and configure the "
            "COVAS:NEXT connection. It is not required for ordinary Rules or for "
            "using Modes locally."
        ),
        ("menu_action:header_setup_menu:COVAS:NEXT connection",),
        "rules",
        "setup_covas",
    ),
    GuidedTourStep(
        "Tools",
        (
            "Tools is mainly for inspection and troubleshooting: live Elite state, "
            "the Rule Simulator, and logs when you need them. You do not need these "
            "to build an ordinary Rule or Mode."
        ),
        ("header_tools_menu_button",),
        "rules",
    ),
    GuidedTourStep(
        "Start Lighting",
        (
            "When the profile and devices are ready, Start Lighting puts the setup "
            "live. Stop Lighting ends the session and gives the devices back to their "
            "normal lighting software."
        ),
        ("start_lighting_button",),
        "rules",
    ),
)



class GuidedTourOverlay(QWidget):
    """Read-only spotlight overlay over the existing product window."""

    def __init__(
        self,
        window: QWidget,
        *,
        steps: tuple[GuidedTourStep, ...] = GUIDED_TOUR_STEPS,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._steps = steps
        self._on_finished = on_finished
        self._step_index = 0
        self._highlight_rect: QRect | None = None
        self._rule_demo_active = False
        self._mode_demo_active = False
        self._mode_demo_snapshot = None
        self._tour_menus: list[QMenu] = []
        modes_button = getattr(window, "covas_modes_mode_button", None)
        self._restore_modes_workspace = bool(
            isinstance(modes_button, QAbstractButton) and modes_button.isChecked()
        )

        self.setObjectName("guidedTourOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setGeometry(window.rect())
        window.installEventFilter(self)

        self.card = QFrame(self)
        self.card.setObjectName("guidedTourCard")
        self.card.setStyleSheet(
            "QFrame#guidedTourCard { "
            "background: palette(window); "
            "border: 1px solid palette(mid); "
            "border-radius: 10px; "
            "}"
        )
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 18, 20, 16)
        card_layout.setSpacing(10)

        self.title_label = QLabel(self.card)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size:14pt; font-weight:600; border:none;")
        card_layout.addWidget(self.title_label)

        self.body_label = QLabel(self.card)
        self.body_label.setWordWrap(True)
        self.body_label.setTextFormat(Qt.TextFormat.RichText)
        self.body_label.setStyleSheet("border:none;")
        card_layout.addWidget(self.body_label)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.progress_label = QLabel(self.card)
        self.progress_label.setProperty("edlTextRole", "secondary")
        self.progress_label.setStyleSheet("border:none;")
        footer.addWidget(self.progress_label)
        footer.addStretch(1)

        self.skip_button = QPushButton("Skip tour", self.card)
        self.skip_button.clicked.connect(self.finish)
        footer.addWidget(self.skip_button)

        self.back_button = QPushButton("Back", self.card)
        self.back_button.clicked.connect(self.back)
        footer.addWidget(self.back_button)

        self.next_button = QPushButton("Next", self.card)
        self.next_button.clicked.connect(self.next)
        footer.addWidget(self.next_button)
        card_layout.addLayout(footer)

    @property
    def step_index(self) -> int:
        return self._step_index

    @property
    def highlight_rect(self) -> QRect | None:
        return QRect(self._highlight_rect) if self._highlight_rect is not None else None

    def start(self) -> None:
        self.setGeometry(self._window.rect())
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._show_step(0)

    def _set_workspace(self, workspace: str | None) -> None:
        method = None
        if workspace == "rules":
            method = getattr(self._window, "show_edl_rules_workspace", None)
        elif workspace == "modes":
            method = getattr(self._window, "show_covas_modes_workspace", None)
        if callable(method):
            method()

    def _restore_workspace(self) -> None:
        method = getattr(
            self._window,
            "show_covas_modes_workspace"
            if self._restore_modes_workspace
            else "show_edl_rules_workspace",
            None,
        )
        if callable(method):
            method()

    def _rule_demo_allowed(self) -> bool:
        profile = getattr(self._window, "_profile", None)
        return not bool(getattr(profile, "rules", ()))

    def _enter_rule_demo(self) -> None:
        if self._rule_demo_active or not self._rule_demo_allowed():
            return
        load_draft = getattr(self._window, "_load_draft", None)
        if not callable(load_draft):
            return

        from lighting_effect_config import STATIC, default_parameters_for_effect
        from lighting_rules import ArgumentCondition, LightingRule

        load_draft(
            LightingRule(
                name="Landing gear warning — tour example",
                conditions=(
                    ArgumentCondition("InMainShip", "Equal", True),
                    ArgumentCondition("LandingGearDown", "Equal", True),
                ),
                colour=(255, 120, 0),
                target="KEYBOARD",
                effect=STATIC,
                effect_parameters=default_parameters_for_effect(STATIC),
            ),
            title="Example Rule — tour only",
        )
        setter = getattr(self._window, "_set_editor_dirty", None)
        if callable(setter):
            setter(False)
        draft = getattr(self._window, "draft_state", None)
        if isinstance(draft, QLabel):
            draft.setText("Tour example — not saved")

        table = getattr(self._window, "table", None)
        if table is not None:
            rows = (
                ("1", "KEYBOARD", "Argument", "STATIC", "General ship lighting", "Tour example", ""),
                ("2", "KEYBOARD", "Argument", "STATIC", "Landing gear warning", "Tour example", ""),
            )
            table.setRowCount(len(rows))
            for row_index, values in enumerate(rows):
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    table.setItem(row_index, column, item)
                table.setRowHeight(row_index, 38)
        count = getattr(self._window, "rule_count", None)
        if isinstance(count, QLabel):
            count.setText("2 tour examples")
        hint = getattr(self._window, "rules_empty_hint", None)
        if isinstance(hint, QLabel):
            hint.hide()

        self._rule_demo_active = True

    def _exit_rule_demo(self) -> None:
        if not self._rule_demo_active:
            return
        clear = getattr(self._window, "_clear_editor", None)
        if callable(clear):
            clear()

        profile = getattr(self._window, "_profile", None)
        if not bool(getattr(profile, "rules", ())):
            table = getattr(self._window, "table", None)
            if table is not None:
                table.setRowCount(0)
            count = getattr(self._window, "rule_count", None)
            if isinstance(count, QLabel):
                count.setText("0 rules")
            hint = getattr(self._window, "rules_empty_hint", None)
            if isinstance(hint, QLabel):
                hint.show()

        self._rule_demo_active = False

    def _mode_demo_allowed(self) -> bool:
        surface = getattr(self._window, "_covas_modes_workspace", None)
        library = getattr(surface, "_library", None)
        return surface is not None and not bool(getattr(library, "modes", ()))

    def _enter_mode_demo(self) -> None:
        if self._mode_demo_active or not self._mode_demo_allowed():
            return
        surface = getattr(self._window, "_covas_modes_workspace", None)
        if surface is None:
            return

        from lighting_effect_config import default_parameters_for_effect
        from lighting_modes import LightingMode, ModeLibrary, ModeOutput, ModePhase

        self._mode_demo_snapshot = (
            getattr(surface, "_library", None),
            int(getattr(surface, "_mode_index", -1)),
            bool(getattr(surface, "_dirty", False)),
        )
        demo = LightingMode(
            "RED ALERT — TOUR EXAMPLE",
            (
                ModePhase(
                    "Alert",
                    2.0,
                    (
                        ModeOutput(
                            "KEYBOARD",
                            "FLASH",
                            ((255, 0, 0), (20, 0, 0)),
                            default_parameters_for_effect("FLASH"),
                        ),
                        ModeOutput(
                            "MOUSE",
                            "STATIC",
                            ((255, 0, 0),),
                            default_parameters_for_effect("STATIC"),
                        ),
                    ),
                ),
                ModePhase(
                    "Hold",
                    4.0,
                    (
                        ModeOutput(
                            "KEYBOARD",
                            "STATIC",
                            ((180, 0, 0),),
                            default_parameters_for_effect("STATIC"),
                        ),
                    ),
                ),
            ),
            media_cue="Danger Zone by Kenny Loggins",
        )
        surface._load_library(ModeLibrary((demo,)), mode_index=0, dirty=False)
        refresh = getattr(surface, "_refresh_run_state", None)
        if callable(refresh):
            refresh()
        draft = getattr(surface, "draft_state", None)
        if isinstance(draft, QLabel):
            draft.setText("Tour example — not saved")
        self._mode_demo_active = True

    def _exit_mode_demo(self) -> None:
        if not self._mode_demo_active:
            return
        surface = getattr(self._window, "_covas_modes_workspace", None)
        snapshot = self._mode_demo_snapshot
        if surface is not None and isinstance(snapshot, tuple):
            library, mode_index, dirty = snapshot
            if library is not None:
                surface._load_library(
                    library,
                    mode_index=max(0, mode_index),
                    dirty=bool(dirty),
                )
            refresh = getattr(surface, "_refresh_run_state", None)
            if callable(refresh):
                refresh()
        self._mode_demo_snapshot = None
        self._mode_demo_active = False

    def _close_tour_menus(self) -> None:
        for menu in reversed(self._tour_menus):
            try:
                menu.setActiveAction(None)
                menu.hide()
                menu.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                    False,
                )
            except RuntimeError:
                pass
        self._tour_menus = []

    def _popup_setup_menu(self) -> QMenu | None:
        menu = getattr(self._window, "header_setup_menu", None)
        button = getattr(self._window, "header_setup_menu_button", None)
        if not isinstance(menu, QMenu) or not isinstance(button, QWidget):
            return None
        menu.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        menu.popup(button.mapToGlobal(QPoint(0, button.height() + 2)))
        self._tour_menus.append(menu)
        return menu

    def _popup_zones_menu(self) -> None:
        setup = self._popup_setup_menu()
        zones = getattr(self._window, "header_zones_menu", None)
        if setup is None or not isinstance(zones, QMenu):
            return
        action = zones.menuAction()
        rect = setup.actionGeometry(action)
        zones.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        zones.popup(setup.mapToGlobal(QPoint(rect.right() + 2, rect.top())))
        self._tour_menus.append(zones)

    def _set_presentation(self, presentation: str | None) -> None:
        self._close_tour_menus()
        if presentation != "rule_demo":
            self._exit_rule_demo()
        if presentation != "mode_demo":
            self._exit_mode_demo()

        if presentation == "rule_demo":
            self._enter_rule_demo()
        elif presentation == "mode_demo":
            self._enter_mode_demo()
        elif presentation in {"setup_devices", "setup_covas"}:
            self._popup_setup_menu()
        elif presentation == "setup_zones":
            self._popup_zones_menu()

    def _ensure_target_visible(self, step: GuidedTourStep) -> None:
        for attribute_path in step.targets:
            if attribute_path.startswith("menu_action:"):
                continue
            widget = self._resolve_target(attribute_path)
            if widget is None:
                continue
            parent = widget.parentWidget()
            while parent is not None:
                if isinstance(parent, QScrollArea):
                    parent.ensureWidgetVisible(widget, 20, 20)
                    return
                parent = parent.parentWidget()

    def _resolve_target(self, attribute_path: str) -> QWidget | None:
        value: object = self._window
        for part in attribute_path.split("."):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value if isinstance(value, QWidget) else None

    def _focus_menu_targets(self, step: GuidedTourStep) -> None:
        focused_menus: set[int] = set()
        for target in step.targets:
            prefix = "menu_action:"
            if not target.startswith(prefix):
                continue
            remainder = target[len(prefix):]
            menu_attribute, separator, action_text = remainder.partition(":")
            if not separator:
                continue
            menu = getattr(self._window, menu_attribute, None)
            if not isinstance(menu, QMenu) or not menu.isVisible():
                continue
            if id(menu) in focused_menus:
                continue
            action = next(
                (item for item in menu.actions() if item.text() == action_text),
                None,
            )
            if action is not None:
                menu.setActiveAction(action)
                focused_menus.add(id(menu))

    def _menu_stack_rect(self) -> QRect | None:
        combined: QRect | None = None
        for menu in self._tour_menus:
            try:
                if not menu.isVisible():
                    continue
                top_left = self.mapFromGlobal(menu.mapToGlobal(QPoint(0, 0)))
                rect = QRect(top_left, menu.size())
            except RuntimeError:
                continue
            combined = rect if combined is None else combined.united(rect)
        return combined

    def _menu_action_rect(self, target: str) -> QRect | None:
        prefix = "menu_action:"
        if not target.startswith(prefix):
            return None
        remainder = target[len(prefix):]
        menu_attribute, separator, action_text = remainder.partition(":")
        if not separator:
            return None
        menu = getattr(self._window, menu_attribute, None)
        if not isinstance(menu, QMenu) or not menu.isVisible():
            return None
        action = next(
            (item for item in menu.actions() if item.text() == action_text),
            None,
        )
        if action is None:
            return None
        action_rect = menu.actionGeometry(action)
        top_left = self.mapFromGlobal(menu.mapToGlobal(action_rect.topLeft()))
        return QRect(top_left, action_rect.size()).adjusted(-4, -3, 4, 3)

    def _target_rect_for(self, step: GuidedTourStep) -> QRect | None:
        combined: QRect | None = None
        for target in step.targets:
            if target.startswith("menu_action:"):
                rect = self._menu_action_rect(target)
            else:
                widget = self._resolve_target(target)
                if widget is None:
                    continue
                visible = (
                    widget.isVisible()
                    if isinstance(widget, QMenu)
                    else widget.isVisibleTo(self._window)
                )
                if not visible:
                    continue
                top_left = self.mapFromGlobal(widget.mapToGlobal(QPoint(0, 0)))
                rect = QRect(top_left, widget.size()).adjusted(-6, -6, 6, 6)

            if rect is None:
                continue
            combined = rect if combined is None else combined.united(rect)

        if combined is None:
            return None
        return combined.intersected(self.rect().adjusted(4, 4, -4, -4))

    def _show_step(self, index: int) -> None:
        if not self._steps:
            self.finish()
            return
        self._step_index = max(0, min(index, len(self._steps) - 1))
        step = self._steps[self._step_index]
        self._set_workspace(step.workspace)
        self._set_presentation(step.presentation)
        self._focus_menu_targets(step)
        self.title_label.setText(step.title)
        self.body_label.setText(step.body)
        self.progress_label.setText(f"{self._step_index + 1} of {len(self._steps)}")
        self.back_button.setEnabled(self._step_index > 0)
        self.next_button.setText(
            "Finish" if self._step_index == len(self._steps) - 1 else "Next"
        )
        self._ensure_target_visible(step)
        self._refresh_geometry()
        QTimer.singleShot(0, self._refresh_geometry)
        QTimer.singleShot(25, self._refresh_geometry)
        self.raise_()
        self.card.raise_()

    def _refresh_geometry(self) -> None:
        if not self.isVisible() or not self._steps:
            return
        self.setGeometry(self._window.rect())
        step = self._steps[self._step_index]
        self._highlight_rect = self._target_rect_for(step)
        self._position_card()
        self.update()
        self.card.raise_()

    @staticmethod
    def _clamp(value: int, lower: int, upper: int) -> int:
        if upper < lower:
            return lower
        return max(lower, min(value, upper))

    @staticmethod
    def _overlap_area(left: QRect, right: QRect) -> int:
        overlap = left.intersected(right)
        return max(0, overlap.width()) * max(0, overlap.height())

    def _position_card(self) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return

        margin = 18
        gap = 18
        width = min(440, max(320, self.width() - (margin * 2)))
        self.card.setFixedWidth(width)
        if self.card.layout() is not None:
            self.card.layout().activate()
        self.card.adjustSize()
        height = min(
            max(160, self.card.sizeHint().height()),
            max(160, self.height() - (margin * 2)),
        )
        self.card.resize(width, height)

        bounds = self.rect().adjusted(margin, margin, -margin, -margin)

        step = self._steps[self._step_index] if self._steps else None
        if step is not None and str(step.presentation or "").startswith("setup_"):
            menu_stack = self._menu_stack_rect()
            if menu_stack is not None:
                left_x = menu_stack.left() - gap - width
                if left_x >= bounds.left():
                    y = self._clamp(
                        menu_stack.top(),
                        bounds.top(),
                        bounds.bottom() - height + 1,
                    )
                    self.card.setGeometry(QRect(left_x, y, width, height))
                    return

        highlight = self._highlight_rect
        if highlight is None:
            self.card.move(
                bounds.left() + max(0, (bounds.width() - width) // 2),
                bounds.top() + max(0, (bounds.height() - height) // 2),
            )
            return

        candidates: list[QRect] = []

        right_x = highlight.right() + gap
        if bounds.right() - right_x + 1 >= width:
            y = self._clamp(
                highlight.center().y() - height // 2,
                bounds.top(),
                bounds.bottom() - height + 1,
            )
            candidates.append(QRect(right_x, y, width, height))

        left_x = highlight.left() - gap - width
        if left_x >= bounds.left():
            y = self._clamp(
                highlight.center().y() - height // 2,
                bounds.top(),
                bounds.bottom() - height + 1,
            )
            candidates.append(QRect(left_x, y, width, height))

        below_y = highlight.bottom() + gap
        if bounds.bottom() - below_y + 1 >= height:
            x = self._clamp(
                highlight.center().x() - width // 2,
                bounds.left(),
                bounds.right() - width + 1,
            )
            candidates.append(QRect(x, below_y, width, height))

        above_y = highlight.top() - gap - height
        if above_y >= bounds.top():
            x = self._clamp(
                highlight.center().x() - width // 2,
                bounds.left(),
                bounds.right() - width + 1,
            )
            candidates.append(QRect(x, above_y, width, height))

        non_overlapping = [
            candidate
            for candidate in candidates
            if not candidate.intersects(highlight) and bounds.contains(candidate)
        ]
        if non_overlapping:
            self.card.setGeometry(non_overlapping[0])
            return

        # If the highlighted control is unusually large, choose the window
        # corner with the least overlap rather than covering the control by
        # clamping one arbitrary side candidate across it.
        corners = (
            QRect(bounds.left(), bounds.top(), width, height),
            QRect(bounds.right() - width + 1, bounds.top(), width, height),
            QRect(bounds.left(), bounds.bottom() - height + 1, width, height),
            QRect(
                bounds.right() - width + 1,
                bounds.bottom() - height + 1,
                width,
                height,
            ),
        )
        best = min(corners, key=lambda rect: self._overlap_area(rect, highlight))
        self.card.setGeometry(best)

    def next(self) -> None:
        if self._step_index >= len(self._steps) - 1:
            self.finish()
            return
        self._show_step(self._step_index + 1)

    def back(self) -> None:
        if self._step_index > 0:
            self._show_step(self._step_index - 1)

    def finish(self) -> None:
        if not self.isVisible() and getattr(self._window, "_guided_tour_overlay", None) is not self:
            return
        self.hide()
        self._window.removeEventFilter(self)
        self._set_presentation(None)
        self._restore_workspace()
        if getattr(self._window, "_guided_tour_overlay", None) is self:
            self._window._guided_tour_overlay = None
        callback = self._on_finished
        self._on_finished = None
        self.deleteLater()
        if callable(callback):
            QTimer.singleShot(0, callback)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self._window and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self._refresh_geometry()
        return False

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.finish()
        elif event.key() in {Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.next()
        elif event.key() == Qt.Key.Key_Left:
            self.back()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # The overlay is explanatory only. Underlying controls must not be
        # accidentally activated through the tour.
        event.accept()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dim = QColor(0, 0, 0, 136)
        if self._highlight_rect is None:
            painter.fillRect(self.rect(), dim)
            return

        visible_region = QRegion(self.rect()).subtracted(QRegion(self._highlight_rect))
        painter.save()
        painter.setClipRegion(visible_region)
        painter.fillRect(self.rect(), dim)
        painter.restore()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ACCENT), 3))
        painter.drawRoundedRect(self._highlight_rect, 8, 8)


def apply_guided_tour(ui_module: Any) -> None:
    """Install first-run/replayable onboarding above the accepted startup flow."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_guided_tour_applied", False):
        return

    previous_init = window_class.__init__
    previous_startup_flow = getattr(window_class, "_edl_run_startup_flow", None)

    def start_guided_tour(
        self,
        *,
        on_finished: Callable[[], None] | None = None,
        mark_completed: bool = False,
    ) -> None:
        existing = getattr(self, "_guided_tour_overlay", None)
        if isinstance(existing, GuidedTourOverlay) and existing.isVisible():
            existing.raise_()
            existing.setFocus(Qt.FocusReason.OtherFocusReason)
            return

        def finished() -> None:
            if mark_completed:
                remember_guided_tour_completed(self._settings)
            if callable(on_finished):
                on_finished()

        overlay = GuidedTourOverlay(self, on_finished=finished)
        self._guided_tour_overlay = overlay
        overlay.start()

    def show_about(self) -> None:
        existing = getattr(self, "_edl_about_dialog", None)
        if isinstance(existing, AboutEdlDialog) and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        dialog = AboutEdlDialog(self)
        self._edl_about_dialog = dialog
        dialog.finished.connect(
            lambda _result: setattr(self, "_edl_about_dialog", None)
        )
        dialog.open()

    def add_external_action(
        help_menu: QMenu,
        label: str,
        url: str,
        tooltip: str,
    ):
        action = help_menu.addAction(label)
        action.setData(url)
        action.setToolTip(tooltip)
        action.triggered.connect(
            lambda _checked=False, target=url: _open_external_url(target)
        )
        return action

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        self._guided_tour_overlay = None
        self._edl_about_dialog = None
        help_menu = getattr(self, "header_help_menu", None)
        if isinstance(help_menu, QMenu):
            action = help_menu.addAction("Take the tour")
            action.setToolTip("Show the guided introduction to the main EDL workspace.")
            action.triggered.connect(
                lambda _checked=False: self._edl_start_guided_tour()
            )
            self.header_take_tour_action = action

            help_menu.addSeparator()
            self.header_edl_project_action = add_external_action(
                help_menu,
                "EDL documentation / project page",
                EDL_PROJECT_URL,
                "Open the public Elite Dangerous Lighting project page and documentation.",
            )
            self.header_chromas_next_action = add_external_action(
                help_menu,
                "Chromas Next",
                CHROMAS_NEXT_URL,
                "Open the Chromas Next COVAS:NEXT plugin documentation.",
            )
            self.header_covasify_action = add_external_action(
                help_menu,
                "Covasify",
                COVASIFY_URL,
                "Open the Covasify COVAS:NEXT plugin documentation.",
            )

            help_menu.addSeparator()
            about_action = help_menu.addAction("About EDL")
            about_action.setToolTip("Show the EDL product name, version and project links.")
            about_action.triggered.connect(
                lambda _checked=False: self._edl_show_about()
            )
            self.header_about_edl_action = about_action

    window_class.__init__ = init
    window_class._edl_start_guided_tour = start_guided_tour
    window_class._edl_show_about = show_about

    if callable(previous_startup_flow):
        def run_startup_flow(self) -> None:
            from lighting_openrgb_devices_ui import startup_flow_disabled

            if startup_flow_disabled() or guided_tour_completed(self._settings):
                previous_startup_flow(self)
                return
            if getattr(self, "_guided_tour_startup_pending", False):
                return

            self._guided_tour_startup_pending = True

            def continue_startup() -> None:
                self._guided_tour_startup_pending = False
                previous_startup_flow(self)

            self._edl_start_guided_tour(
                on_finished=continue_startup,
                mark_completed=True,
            )

        window_class._edl_run_startup_flow = run_startup_flow

    window_class._edl_guided_tour_applied = True
