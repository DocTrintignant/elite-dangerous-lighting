#!/usr/bin/env python3
"""Canonical desktop header composition and final disposition.

This module owns both the accepted two-row header foundation (including the
persisted render-rate control) and the later final menu disposition. Both are
presentation-only: existing widgets/callbacks are reused and runtime, renderer,
device, transport, Rule and Mode mechanisms remain unchanged.
"""

from __future__ import annotations

from typing import Any

from lighting_render_rate import DEFAULT_RENDER_FPS, set_render_fps
from lighting_settings import app_settings
from lighting_ui_language import (
    ADD_MODE,
    RULES_TAB,
    RULES_TAB_HELP,
    RUN_MODE,
    SCRIPTED_MODES_TAB,
    SCRIPTED_MODES_TAB_HELP,
    STOP_MODE,
)
from lighting_ui_tokens import STATUS_ERROR

from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)


RENDER_RATE_SETTING_KEY = "lighting/render_fps"
RENDER_RATE_CHOICES = (15, 20, 30, 45, 60)
RENDER_RATE_TOOLTIP = (
    "Choose how many lighting update cycles EDL runs each second. 30 FPS is the normal default. "
    "Lower values reduce update frequency; higher values can make some animations look smoother. "
    "Changing this does not make an effect run faster or slower because effect timing uses elapsed time. "
    "In the current runner, Elite Status, keyboard and HID observation occur in the same loop, so lower FPS "
    "or slow backend work can reduce how often brief inputs are observed."
)
GOVEE_SETUP_TOOLTIP = (
    "Set up named zones on supported Govee devices that EDL can control section-by-section. "
    "Keyboard, mouse and ordinary Chroma-compatible devices continue through Razer Chroma and do not need zone setup here."
)
LIVE_DATA_TOOLTIP = (
    "See what Elite Dangerous is reporting right now, try rule conditions without changing the game or lights, "
    "and turn troubleshooting logging on or off."
)


HEADER_ACTIONS = {
    "Open profile…": ("Open profile", "Open an EDL lighting profile from your computer."),
    "Open profile": ("Open profile", "Open an EDL lighting profile from your computer."),
    "Save profile": ("Save profile", "Save your changes to the EDL profile you currently have open."),
    "Save as…": ("Save profile as", "Save a copy of the current EDL profile under a new file name."),
    "Import VIRPIL…": (
        "Import VIRPIL profile",
        "Read a VIRPIL Link Tool .led.json file and create an editable EDL profile from it. "
        "EDL does not overwrite the VIRPIL file you selected; when you save, EDL creates its own profile file.",
    ),
    "Elite status…": (
        "Live Elite data",
        "Show the live game values EDL is receiving from Elite Dangerous, such as landing gear, FSD state, fuel and legal state.",
    ),
    "Elite Dangerous data…": (
        "Select Elite Status.json",
        "Choose the Elite Dangerous Status.json file EDL uses for live Argument Rules, Test rules and diagnostics.",
    ),
    "Select Elite Status.json…": (
        "Select Elite Status.json",
        "Choose the Elite Dangerous Status.json file EDL uses for live Argument Rules, Test rules and diagnostics.",
    ),
    "Rule Simulator…": (
        "Test rules",
        "Try different Elite state or input values, then temporarily show the calculated result on the selected lighting devices.",
    ),
    "Test rules…": (
        "Test rules",
        "Try different Elite state or input values, then temporarily show the calculated result on the selected lighting devices.",
    ),
    "Start lighting": (
        "Start lighting",
        "Start applying the current profile to the live game and cockpit state.",
    ),
    "Stop lighting": (
        "Stop lighting",
        "Stop EDL lighting output and release the devices it currently controls.",
    ),
    "Logs": (
        "Save troubleshooting log",
        "Keep a local record of EDL actions and runtime events so a problem can be investigated later.",
    ),
    "Govee zones…": (
        "Govee H61C3",
        "Open zone setup for the supported Govee H61C3. Other Chroma-compatible devices keep using Razer Chroma and are not configured here.",
    ),
}


def _clarify_header_action(widget) -> None:
    text_method = getattr(widget, "text", None)
    set_text = getattr(widget, "setText", None)
    if not callable(text_method) or not callable(set_text):
        return
    replacement = HEADER_ACTIONS.get(text_method())
    if replacement is None:
        return
    label, tooltip = replacement
    set_text(label)
    if hasattr(widget, "setToolTip"):
        widget.setToolTip(tooltip)
    if hasattr(widget, "setToolTipDuration"):
        widget.setToolTipDuration(30000)


def _widget_text(widget) -> str:
    method = getattr(widget, "text", None)
    return str(method()) if callable(method) else ""


class OperatorHeaderLayout(QVBoxLayout):
    """Two task-oriented rows: profile management and live operation/testing."""

    def __init__(self, window, source: QHBoxLayout, app_module: Any) -> None:
        super().__init__()
        self.setSpacing(6)
        self._window = window
        self._menu_widgets: list[tuple[object, QWidget]] = []
        self._ready = False

        widgets: list[QWidget] = []
        while source.count():
            item = source.takeAt(0)
            widget = item.widget()
            if widget is not None:
                _clarify_header_action(widget)
                widgets.append(widget)

        self.profile_row = QHBoxLayout()
        self.profile_row.setSpacing(9)
        self.runtime_row = QHBoxLayout()
        self.runtime_row.setSpacing(9)
        super().addLayout(self.profile_row)
        super().addLayout(self.runtime_row)

        title = next(
            (
                w
                for w in widgets
                if isinstance(w, QLabel)
                and w is not getattr(window, "profile_name", None)
            ),
            None,
        )
        profile_name = getattr(window, "profile_name", None)
        save_button = getattr(window, "save_button", None)
        save_as_button = getattr(window, "save_as_button", None)
        open_button = next(
            (
                w
                for w in widgets
                if isinstance(w, QPushButton) and _widget_text(w) == "Open profile"
            ),
            None,
        )

        if title is not None:
            self.profile_row.addWidget(title)
        self.profile_row.addStretch(1)
        if profile_name is not None:
            profile_name.setMinimumWidth(150)
            profile_name.setToolTip("This is the name of the EDL lighting profile currently open.")
            self.profile_row.addWidget(profile_name)
        if open_button is not None:
            self.profile_row.addWidget(open_button)
        if save_button is not None:
            _clarify_header_action(save_button)
            self.profile_row.addWidget(save_button)
        if save_as_button is not None:
            _clarify_header_action(save_as_button)
            self.profile_row.addWidget(save_as_button)

        self._install_render_rate_control(app_module)
        self.runtime_row.addStretch(1)

        self.live_data_button, self.live_data_menu = self._menu_button(
            "Live data & testing", 154, LIVE_DATA_TOOLTIP
        )
        self.govee_button, self.govee_menu = self._menu_button(
            "Govee zones", 146, GOVEE_SETUP_TOOLTIP
        )
        self.runtime_row.addWidget(self.live_data_button)
        self.runtime_row.addWidget(self.govee_button)

        self.live_data_menu.aboutToShow.connect(self._sync_menu_state)
        self.govee_menu.aboutToShow.connect(self._sync_menu_state)
        self._ready = True

    @staticmethod
    def _menu_button(
        text: str,
        minimum_width: int,
        tooltip: str,
    ) -> tuple[QPushButton, QMenu]:
        button = QPushButton(text)
        button.setMinimumWidth(minimum_width)
        button.setToolTip(tooltip)
        button.setToolTipDuration(30000)
        menu = QMenu(button)
        menu.setToolTipsVisible(True)
        button.setMenu(menu)
        return button, menu

    @staticmethod
    def _show_action_tooltip(action) -> None:
        tooltip = action.toolTip()
        if tooltip:
            QToolTip.showText(QCursor.pos(), tooltip, None, None, 30000)

    def _add_button_action(self, menu: QMenu, widget: QPushButton) -> None:
        _clarify_header_action(widget)
        action = menu.addAction(_widget_text(widget))
        action.setToolTip(widget.toolTip())
        action.hovered.connect(lambda a=action: self._show_action_tooltip(a))
        action.triggered.connect(widget.click)
        widget.setVisible(False)
        self._menu_widgets.append((action, widget))

    def _add_check_action(self, menu: QMenu, widget: QCheckBox) -> None:
        _clarify_header_action(widget)
        action = menu.addAction(_widget_text(widget))
        action.setToolTip(widget.toolTip())
        action.hovered.connect(lambda a=action: self._show_action_tooltip(a))
        action.setCheckable(True)
        action.setChecked(widget.isChecked())
        action.toggled.connect(widget.setChecked)
        widget.toggled.connect(action.setChecked)
        widget.setVisible(False)
        self._menu_widgets.append((action, widget))

    def _install_render_rate_control(self, app_module: Any) -> None:
        settings = app_settings()
        stored = settings.value(RENDER_RATE_SETTING_KEY, DEFAULT_RENDER_FPS)
        try:
            selected = int(float(stored))
        except (TypeError, ValueError):
            selected = int(DEFAULT_RENDER_FPS)
        if selected not in RENDER_RATE_CHOICES:
            selected = int(DEFAULT_RENDER_FPS)
        set_render_fps(selected)

        label = QLabel("Lighting update rate")
        label.setToolTip(RENDER_RATE_TOOLTIP)
        label.setToolTipDuration(30000)
        combo = QComboBox()
        combo.setMinimumWidth(92)
        combo.setToolTip(RENDER_RATE_TOOLTIP)
        combo.setToolTipDuration(30000)
        for fps in RENDER_RATE_CHOICES:
            combo.addItem(f"{fps} FPS", fps)
        combo.setCurrentIndex(combo.findData(selected))

        def changed(_index: int) -> None:
            fps = int(combo.currentData())
            set_render_fps(fps)
            settings.setValue(RENDER_RATE_SETTING_KEY, fps)
            settings.sync()
            self._window.statusBar().showMessage(
                f"Lighting update rate set to {fps} FPS.", 3500
            )

        combo.currentIndexChanged.connect(changed)
        self._window.render_rate_label = label
        self._window.render_rate_combo = combo
        self.runtime_row.addWidget(label)
        self.runtime_row.addWidget(combo)

    def _sync_menu_state(self) -> None:
        for action, widget in self._menu_widgets:
            action.setEnabled(widget.isEnabled())
            if isinstance(widget, QCheckBox):
                action.setChecked(widget.isChecked())

    def addWidget(self, widget, *args) -> None:  # type: ignore[override]
        """Route later feature additions by user task rather than subsystem."""
        if not self._ready:
            self.profile_row.addWidget(widget, *args)
            return

        original_text = _widget_text(widget)
        _clarify_header_action(widget)
        text = _widget_text(widget)

        if isinstance(widget, QPushButton):
            if original_text in {"Start lighting", "Stop lighting"} or text in {
                "Start lighting",
                "Stop lighting",
            }:
                widget.setMinimumWidth(128)
                self.runtime_row.insertWidget(3, widget)
                return
            if original_text == "Import VIRPIL…" or text == "Import VIRPIL profile":
                widget.setMinimumWidth(146)
                self.profile_row.addWidget(widget)
                return
            if original_text in {"Elite status…", "Rule Simulator…"} or text in {
                "Live Elite data",
                "Simulate game and input state",
            }:
                self._add_button_action(self.live_data_menu, widget)
                return
            if original_text == "Govee zones…" or text == "Govee H61C3":
                self._add_button_action(self.govee_menu, widget)
                return

        if isinstance(widget, QCheckBox):
            if original_text == "Logs" or text == "Save troubleshooting log":
                if self.live_data_menu.actions():
                    self.live_data_menu.addSeparator()
                self._add_check_action(self.live_data_menu, widget)
                return

        self.runtime_row.addWidget(widget, *args)


def apply_current_ui_integration(app_module: Any) -> None:
    """Install the accepted two-row header foundation before later UI adapters."""
    window_class = app_module.MainWindow
    if getattr(window_class, "_edl_current_ui_integration_applied", False):
        return

    previous_build_header = window_class._build_header

    def build_header(self):
        source = previous_build_header(self)
        if hasattr(source, "primary"):
            primary = source.primary
            secondary = getattr(source, "secondary", None)
            if secondary is not None:
                while secondary.count():
                    item = secondary.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        primary.addWidget(widget)
            source = primary
        return OperatorHeaderLayout(self, source, app_module)

    window_class._build_header = build_header
    window_class._edl_current_ui_integration_applied = True


def _header_rows(
    window,
) -> tuple[QHBoxLayout | None, QHBoxLayout | None, object | None]:
    """Return the accepted profile/runtime header rows.

    The older product window exposed one direct QHBoxLayout. The accepted
    product composition now wraps that surface in OperatorHeaderLayout, which
    owns profile_row and runtime_row. Support both shapes without replacing
    either presentation mechanism.
    """
    central = window.centralWidget()
    outer = central.layout() if central is not None else None
    if outer is None or not outer.count():
        return None, None, None

    layout = outer.itemAt(0).layout()
    if isinstance(layout, QHBoxLayout):
        return layout, None, layout

    profile_row = getattr(layout, "profile_row", None)
    runtime_row = getattr(layout, "runtime_row", None)
    if isinstance(profile_row, QHBoxLayout) and isinstance(runtime_row, QHBoxLayout):
        return profile_row, runtime_row, layout

    return None, None, layout


def _direct_widgets(layout: QHBoxLayout) -> list[QWidget]:
    widgets: list[QWidget] = []
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        if isinstance(widget, QWidget):
            widgets.append(widget)
    return widgets


def _button_with_text(widgets: list[QWidget], *labels: str) -> QPushButton | None:
    wanted = set(labels)
    for widget in widgets:
        if isinstance(widget, QPushButton) and widget.text() in wanted:
            return widget
    return None


def _title_label(widgets: list[QWidget], app_title: str) -> QLabel | None:
    for widget in widgets:
        if isinstance(widget, QLabel) and widget.text() == app_title:
            return widget
    return None


def _menu_button(
    parent,
    text: str,
    tooltip: str,
) -> tuple[QPushButton, QMenu]:
    button = QPushButton(text, parent)
    button.setToolTip(tooltip)
    button.setToolTipDuration(30000)
    button.setStyleSheet(
        "QPushButton { padding-right: 26px; } "
        "QPushButton::menu-indicator { subcontrol-position:right center; right:8px; }"
    )
    menu = QMenu(button)
    menu.setToolTipsVisible(True)
    button.setMenu(menu)
    return button, menu


def _add_button_action(
    menu: QMenu,
    widget: QPushButton | None,
    bindings: list[tuple[object, QWidget]],
    *,
    label: str | None = None,
) -> None:
    if not isinstance(widget, QPushButton):
        return
    action = menu.addAction(label or widget.text())
    action.setToolTip(widget.toolTip())
    action.setEnabled(widget.isEnabled())
    action.triggered.connect(widget.click)
    widget.setVisible(False)
    bindings.append((action, widget))


def _add_attention_button_action(
    menu: QMenu,
    widget: QPushButton | None,
    bindings: list[tuple[object, QWidget]],
    *,
    label: str,
) -> tuple[QWidgetAction | None, QPushButton | None]:
    if not isinstance(widget, QPushButton):
        return None, None

    action = QWidgetAction(menu)
    action.setText(label)
    action.setToolTip(widget.toolTip())

    control = QPushButton(label, menu)
    control.setFlat(True)
    control.setToolTip(widget.toolTip())
    control.setStyleSheet(
        f"QPushButton {{ color:{STATUS_ERROR}; font-weight:600; text-align:left; "
        "border:none; background:transparent; padding:5px 18px 5px 8px; } "
        "QPushButton:hover { background:rgba(255,255,255,18); }"
    )

    def trigger() -> None:
        widget.click()
        menu.close()

    control.clicked.connect(trigger)
    action.setDefaultWidget(control)
    action.setEnabled(widget.isEnabled())
    menu.addAction(action)
    widget.setVisible(False)
    bindings.append((action, widget))
    return action, control


def _add_check_action(
    menu: QMenu,
    widget: QCheckBox | None,
    bindings: list[tuple[object, QWidget]],
) -> None:
    if not isinstance(widget, QCheckBox):
        return
    action = menu.addAction(widget.text())
    action.setToolTip(widget.toolTip())
    action.setCheckable(True)
    action.setChecked(widget.isChecked())
    action.setEnabled(widget.isEnabled())
    action.toggled.connect(widget.setChecked)
    widget.toggled.connect(action.setChecked)
    widget.setVisible(False)
    bindings.append((action, widget))


def _unique_widgets(*widgets) -> list[QWidget]:
    result: list[QWidget] = []
    seen: set[int] = set()
    for widget in widgets:
        if isinstance(widget, QWidget) and id(widget) not in seen:
            seen.add(id(widget))
            result.append(widget)
    return result


def _apply_header_disposition(window, app_module: Any) -> None:
    profile_row, runtime_row, header_container = _header_rows(window)
    if profile_row is None:
        return

    rows = [profile_row]
    if runtime_row is not None:
        rows.append(runtime_row)

    # The two-row OperatorHeaderLayout itself is an accepted composition.
    # Continue to stop if an unknown nested layout appears *inside* either row.
    for row in rows:
        for index in range(row.count()):
            if row.itemAt(index).layout() is not None:
                return

    existing: list[QWidget] = []
    for row in rows:
        existing.extend(_direct_widgets(row))

    legacy_aggregate_buttons = _unique_widgets(
        getattr(header_container, "live_data_button", None),
        getattr(header_container, "govee_button", None),
    )
    title = _title_label(existing, app_module.APP_TITLE)
    profile_name = getattr(window, "profile_name", None)
    open_button = _button_with_text(existing, "Open profile…", "Open profile")
    save_button = getattr(window, "save_button", None)
    start_button = getattr(window, "start_lighting_button", None)

    save_as_button = getattr(window, "save_as_button", None)
    import_virpil_button = getattr(window, "import_virpil_button", None)
    load_last_profile_check = getattr(window, "load_last_profile_check", None)
    elite_status_button = getattr(window, "elite_status_button", None)
    elite_data_button = getattr(window, "elite_data_button", None)
    simulator_button = getattr(window, "simulator_button", None)
    logging_check = getattr(window, "logging_check", None)
    open_log_folder_button = getattr(window, "open_log_folder_button", None)
    lighting_devices_button = getattr(window, "lighting_devices_button", None)
    lighting_integrations_button = getattr(window, "lighting_integrations_button", None)
    govee_zones_button = getattr(window, "govee_zones_button", None)
    chroma_zones_button = getattr(window, "chroma_zone_setup_button", None)
    covas_button = getattr(window, "_covas_button", None)

    profile_widgets = _unique_widgets(
        save_as_button,
        import_virpil_button,
        load_last_profile_check,
    )
    tool_widgets = _unique_widgets(
        elite_status_button,
        simulator_button,
        logging_check,
        open_log_folder_button,
    )
    setup_widgets = _unique_widgets(
        elite_data_button,
        lighting_devices_button,
        lighting_integrations_button,
        govee_zones_button,
        chroma_zones_button,
        covas_button,
    )

    render_rate_label = getattr(window, "render_rate_label", None)
    render_rate_combo = getattr(window, "render_rate_combo", None)
    rate_widgets = _unique_widgets(render_rate_label, render_rate_combo)

    primary = _unique_widgets(title, profile_name, open_button, save_button, start_button)
    classified = {
        id(widget)
        for widget in (
            *primary,
            *profile_widgets,
            *tool_widgets,
            *setup_widgets,
            *rate_widgets,
            *legacy_aggregate_buttons,
        )
    }
    unknown = [widget for widget in existing if id(widget) not in classified]

    # Remove every direct widget first, then explicitly put every known or
    # unknown widget back. Rebuild only the accepted row presentation.
    for row in rows:
        while row.count():
            row.takeAt(0)

    for widget in legacy_aggregate_buttons:
        widget.setVisible(False)

    profile_menu_button, profile_menu = _menu_button(
        window,
        "Profile",
        "Less frequent profile operations.",
    )
    tools_menu_button, tools_menu = _menu_button(
        window,
        "Tools",
        "Diagnostics, simulation and troubleshooting controls.",
    )
    setup_menu_button, setup_menu = _menu_button(
        window,
        "Setup",
        "Lighting devices, integrations, zones and external plugin configuration.",
    )
    help_menu_button, help_menu = _menu_button(
        window,
        "Help",
        "Guided tour and product help.",
    )

    bindings: list[tuple[object, QWidget]] = []

    # Presentation labels deliberately omit trailing ellipses. Existing widgets
    # and callbacks remain unchanged underneath this final disposition layer.
    _add_button_action(profile_menu, save_as_button, bindings, label="Save as")
    _add_button_action(
        profile_menu,
        import_virpil_button,
        bindings,
        label="Import VIRPIL profile",
    )
    _add_button_action(tools_menu, simulator_button, bindings, label="Test rules")
    troubleshooting_menu = tools_menu.addMenu("Troubleshooting")
    troubleshooting_menu.setToolTipsVisible(True)
    _add_button_action(
        troubleshooting_menu,
        elite_status_button,
        bindings,
        label="Elite status",
    )
    if isinstance(logging_check, QCheckBox):
        _add_check_action(troubleshooting_menu, logging_check, bindings)
    _add_button_action(
        troubleshooting_menu,
        open_log_folder_button,
        bindings,
        label="Open log folder",
    )

    _add_button_action(
        setup_menu,
        elite_data_button,
        bindings,
        label="Select Elite Status.json",
    )
    _add_button_action(
        setup_menu,
        lighting_devices_button,
        bindings,
        label="Lighting devices",
    )
    _add_button_action(
        setup_menu,
        lighting_integrations_button,
        bindings,
        label="Lighting integrations",
    )

    zones_menu = setup_menu.addMenu("Zones")
    zones_menu.setToolTipsVisible(True)
    _add_button_action(
        zones_menu,
        chroma_zones_button,
        bindings,
        label="Chroma zones",
    )
    _add_button_action(
        zones_menu,
        govee_zones_button,
        bindings,
        label="Govee zones",
    )

    appearance_menu = setup_menu.addMenu("Appearance")
    appearance_menu.setToolTipsVisible(True)
    appearance_actions = {}
    for label in ("System", "Light", "Dark"):
        action = appearance_menu.addAction(label)
        action.setCheckable(True)
        action.setToolTip(
            "Follow Windows appearance."
            if label == "System"
            else f"Always use the EDL {label.lower()} appearance."
        )
        appearance_actions[label] = action

    _add_button_action(
        setup_menu,
        covas_button,
        bindings,
        label="COVAS:NEXT connection",
    )
    covas_action = next(
        (
            action
            for action in setup_menu.actions()
            if action.text() == "COVAS:NEXT connection"
        ),
        None,
    )

    def sync_menu_state() -> None:
        for action, widget in bindings:
            action.setEnabled(widget.isEnabled())
            if isinstance(widget, QCheckBox):
                action.setChecked(widget.isChecked())

    profile_menu.aboutToShow.connect(sync_menu_state)
    tools_menu.aboutToShow.connect(sync_menu_state)
    setup_menu.aboutToShow.connect(sync_menu_state)
    help_menu.aboutToShow.connect(sync_menu_state)

    if isinstance(open_button, QPushButton):
        open_button.setText("Open profile")

    if runtime_row is None:
        # Legacy one-row composition.
        if isinstance(title, QWidget):
            profile_row.addWidget(title)
        profile_row.addStretch(1)
        for widget in (
            profile_name,
            load_last_profile_check,
            open_button,
            save_button,
            start_button,
        ):
            if isinstance(widget, QWidget):
                widget.setVisible(True)
                profile_row.addWidget(widget)
        profile_row.addWidget(profile_menu_button)
        profile_row.addWidget(tools_menu_button)
        profile_row.addWidget(setup_menu_button)
        profile_row.addWidget(help_menu_button)
        for widget in unknown:
            profile_row.addWidget(widget)
    else:
        # Accepted two-row composition:
        # row 1 = application/profile identity and profile operations
        # row 2 = render cadence/runtime plus Tools and Setup
        if isinstance(title, QWidget):
            profile_row.addWidget(title)
        profile_row.addStretch(1)
        for widget in (
            profile_name,
            load_last_profile_check,
        ):
            if isinstance(widget, QWidget):
                widget.setVisible(True)
                profile_row.addWidget(widget)
        # Help is deliberately outside the three paired action columns below.
        # Open/Start, Save/Tools and Profile/Setup therefore retain the accepted
        # vertical alignment that existed before Help was added.
        profile_row.addWidget(help_menu_button)
        for widget in (open_button, save_button):
            if isinstance(widget, QWidget):
                widget.setVisible(True)
                profile_row.addWidget(widget)
        profile_row.addWidget(profile_menu_button)

        for widget in rate_widgets:
            widget.setVisible(True)
            runtime_row.addWidget(widget)
        runtime_row.addStretch(1)
        if isinstance(start_button, QWidget):
            start_button.setVisible(True)
            runtime_row.addWidget(start_button)
        runtime_row.addWidget(tools_menu_button)
        runtime_row.addWidget(setup_menu_button)

        # Preserve unknown future additive controls instead of silently dropping
        # them; the runtime row is their established insertion surface.
        for widget in unknown:
            runtime_row.addWidget(widget)

    # The two rows remain independent task rows, but their three rightmost
    # controls should read as one exact visual grid. Using only minimum widths
    # lets the two independent row layouts expand a pair by slightly different
    # amounts, which shows up as a one-pixel column drift on Windows.
    if runtime_row is not None:
        for upper, lower in (
            (open_button, start_button),
            (save_button, tools_menu_button),
            (profile_menu_button, setup_menu_button),
        ):
            if isinstance(upper, QPushButton) and isinstance(lower, QPushButton):
                width = max(upper.sizeHint().width(), lower.sizeHint().width())
                upper.setFixedWidth(width)
                lower.setFixedWidth(width)

    header_buttons = [
        widget
        for widget in (
            open_button,
            save_button,
            start_button,
            profile_menu_button,
            tools_menu_button,
            setup_menu_button,
            help_menu_button,
        )
        if isinstance(widget, QPushButton)
    ]
    if header_buttons:
        header_height = max(38, *(button.sizeHint().height() for button in header_buttons))
        for button in header_buttons:
            button.setMinimumHeight(header_height)

    window.header_profile_row = profile_row
    window.header_runtime_row = runtime_row
    window._header_legacy_aggregate_buttons = tuple(legacy_aggregate_buttons)
    window.header_profile_menu_button = profile_menu_button
    window.header_tools_menu_button = tools_menu_button
    window.header_setup_menu_button = setup_menu_button
    window.header_help_menu_button = help_menu_button
    window.header_profile_menu = profile_menu
    window.header_tools_menu = tools_menu
    window.header_troubleshooting_menu = troubleshooting_menu
    window.header_setup_menu = setup_menu
    window.header_help_menu = help_menu
    window.header_zones_menu = zones_menu
    window.header_appearance_menu = appearance_menu
    window.header_appearance_actions = appearance_actions
    window.header_covas_action = covas_action
    # Compatibility aliases retained for older diagnostics/tests. The operator
    # surface is now a normal QMenu action, not an attention/red pseudo-button.
    window.header_covas_attention_action = covas_action
    window.header_covas_attention_button = None
    window._header_disposition_bindings = tuple(bindings)
    window._header_unclassified_widgets = tuple(unknown)


def apply_header_disposition(app_module: Any) -> None:
    """Install the final presentation-only main-header hierarchy once."""
    window_class = app_module.MainWindow
    if getattr(window_class, "_edl_header_disposition_applied", False):
        return

    previous_init = window_class.__init__

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        _apply_header_disposition(self, app_module)

    window_class.__init__ = init
    window_class._edl_header_disposition_applied = True


# Co-located final operator-facing normalization.
def _clear_status_help(root: QWidget) -> None:
    # The status bar is reserved for application state and transient runtime
    # messages. Hover/documentation belongs in tooltips and '?' help only.
    root.setStatusTip("")
    for widget in root.findChildren(QWidget):
        widget.setStatusTip("")
    for action in root.findChildren(QAction):
        action.setStatusTip("")


def _normalize_workspace(window) -> None:
    rules = getattr(window, "edl_rules_mode_button", None)
    scenes = getattr(window, "covas_modes_mode_button", None)
    if isinstance(rules, QAbstractButton):
        rules.setText(RULES_TAB)
        rules.setToolTip(RULES_TAB_HELP)
    if isinstance(scenes, QAbstractButton):
        scenes.setText(SCRIPTED_MODES_TAB)
        scenes.setToolTip(SCRIPTED_MODES_TAB_HELP)

    surface = getattr(window, "_covas_modes_workspace", None)
    if surface is None:
        return
    for attribute, text in (
        ("new_mode_button", ADD_MODE),
        ("run_button", RUN_MODE),
        ("stop_button", STOP_MODE),
    ):
        widget = getattr(surface, attribute, None)
        if isinstance(widget, QAbstractButton):
            widget.setText(text)


def normalize_window(window) -> None:
    _normalize_workspace(window)
    _clear_status_help(window)


def apply_ui_normalization(ui_module: Any) -> None:
    """Install the final presentation contract after all other UI adapters."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_ui_normalization_applied", False):
        return

    previous_init = window_class.__init__

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        normalize_window(self)

    window_class.__init__ = init
    window_class._edl_normalize_presentation = normalize_window
    window_class._edl_ui_normalization_applied = True
