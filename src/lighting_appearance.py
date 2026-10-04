#!/usr/bin/env python3
"""Application appearance preference for the EDL desktop UI.

Presentation only. This module owns System / Light / Dark palette selection and
the final theme stylesheet. It does not touch profile data, lighting runtime,
renderer ownership, device routing, Rule/Mode semantics or COVAS behavior.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QActionGroup, QColor, QPalette
from PySide6.QtWidgets import QApplication

APPEARANCE_KEY = "ui/appearance"
APPEARANCE_OPTIONS = ("System", "Light", "Dark")
DEFAULT_APPEARANCE = "System"


def normalize_appearance(value: object) -> str:
    text = str(value or "").strip().casefold()
    for option in APPEARANCE_OPTIONS:
        if text == option.casefold():
            return option
    return DEFAULT_APPEARANCE


def _scheme_name(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(value).rsplit(".", 1)[-1]


def effective_appearance(preference: str, system_scheme: object) -> str:
    normalized = normalize_appearance(preference)
    if normalized != "System":
        return normalized
    # Preserve the accepted dark presentation when Qt cannot identify the
    # platform scheme (for example on an offscreen test runner).
    return "Light" if _scheme_name(system_scheme).casefold() == "light" else "Dark"


def _theme_values(effective: str) -> dict[str, str]:
    if effective == "Light":
        return {
            "window": "#F5F5F7",
            "base": "#FFFFFF",
            "alternate": "#F0F0F3",
            "raised": "#ECECF0",
            "control": "#E7E7EB",
            "disabled": "#E3E3E7",
            "tooltip": "#FFFFFF",
            "text": "#202124",
            "secondary": "#555A64",
            "tertiary": "#737782",
            "border": "#C7C7CE",
            "border_strong": "#A9A9B2",
            "accent": "#6E3A86",
            "accent_border": "#6E3A86",
            "highlight_text": "#FFFFFF",
            "ok": "#16784A",
            "warning": "#8A5A00",
            "error": "#B42318",
            "hover": "#DEDEE4",
        }
    return {
        "window": "#1A1A1C",
        "base": "#202023",
        "alternate": "#242427",
        "raised": "#202023",
        "control": "#27272A",
        "disabled": "#252525",
        "tooltip": "#2D2D2D",
        "text": "#ECECEF",
        "secondary": "#B4B4BB",
        "tertiary": "#8E8E97",
        "border": "#2E2E33",
        "border_strong": "#414147",
        "accent": "#6E3A86",
        "accent_border": "#8E5AA5",
        "highlight_text": "#FFFFFF",
        "ok": "#4CC38A",
        "warning": "#D7AA57",
        "error": "#E05A5A",
        "hover": "#333338",
    }


def palette_for_appearance(application: QApplication, effective: str) -> QPalette:
    values = _theme_values(effective)
    palette = application.style().standardPalette()
    role_values = {
        QPalette.ColorRole.Window: values["window"],
        QPalette.ColorRole.WindowText: values["text"],
        QPalette.ColorRole.Base: values["base"],
        QPalette.ColorRole.AlternateBase: values["alternate"],
        QPalette.ColorRole.ToolTipBase: values["tooltip"],
        QPalette.ColorRole.ToolTipText: values["text"],
        QPalette.ColorRole.Text: values["text"],
        QPalette.ColorRole.Button: values["control"],
        QPalette.ColorRole.ButtonText: values["text"],
        QPalette.ColorRole.Highlight: values["accent"],
        QPalette.ColorRole.HighlightedText: values["highlight_text"],
        QPalette.ColorRole.PlaceholderText: values["tertiary"],
        QPalette.ColorRole.Mid: values["border_strong"],
        QPalette.ColorRole.Midlight: values["border"],
    }
    for role, colour in role_values.items():
        palette.setColor(role, QColor(colour))

    disabled = QPalette.ColorGroup.Disabled
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(disabled, role, QColor(values["tertiary"]))
    palette.setColor(disabled, QPalette.ColorRole.Button, QColor(values["disabled"]))
    return palette


def _application_apply_plan(
    application: QApplication,
    desired_palette: QPalette,
    desired_stylesheet: str,
) -> tuple[bool, bool]:
    """Return whether the application palette and stylesheet need changing."""
    return (
        application.palette() != desired_palette,
        application.styleSheet() != desired_stylesheet,
    )


def theme_stylesheet(effective: str) -> str:
    v = _theme_values(effective)
    return f"""
/* EDL final appearance overlay: {effective} */
QMainWindow, QDialog {{
    background-color: {v["window"]};
    color: {v["text"]};
}}
QLabel, QCheckBox, QRadioButton, QGroupBox {{
    color: {v["text"]};
}}
QLabel[edlTextRole="secondary"] {{ color: {v["secondary"]}; }}
QLabel[edlTextRole="tertiary"] {{ color: {v["tertiary"]}; }}
QLabel[edlTextRole="status"] {{ color: {v["text"]}; }}
QLabel[edlState="ok"], QPushButton[edlState="ok"] {{ color: {v["ok"]}; }}
QLabel[edlState="warning"], QPushButton[edlState="warning"] {{ color: {v["warning"]}; }}
QLabel[edlState="attention"], QPushButton[edlState="attention"] {{ color: {v["error"]}; }}
QToolTip {{
    color: {v["text"]};
    background-color: {v["tooltip"]};
    border: 1px solid {v["border_strong"]};
}}
QMenu {{
    color: {v["text"]};
    background-color: {v["window"]};
}}
QMenu::item:selected {{ background-color: {v["control"]}; }}
QMenu::item:disabled {{ color: {v["tertiary"]}; }}
QPushButton, QToolButton {{
    color: {v["text"]};
    background-color: {v["control"]};
    border: 1px solid {v["border"]};
}}
QPushButton:hover, QToolButton:hover {{ background-color: {v["hover"]}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QListWidget, QTableWidget, QTreeWidget, QTextEdit, QPlainTextEdit {{
    color: {v["text"]};
    background-color: {v["base"]};
    border-color: {v["border"]};
}}
QComboBox QAbstractItemView {{
    color: {v["text"]};
    background-color: {v["base"]};
    selection-background-color: {v["accent"]};
    selection-color: {v["highlight_text"]};
}}
QFrame#sectionBox {{ border-color: {v["border"]}; }}
QTableWidget {{
    gridline-color: {v["border"]};
    alternate-background-color: {v["alternate"]};
}}
QTableWidget::item:selected {{
    background: {v["accent"]};
    color: {v["highlight_text"]};
    font-weight: 600;
}}
QHeaderView::section {{
    color: {v["secondary"]};
    background-color: {v["raised"]};
    border-color: {v["border"]};
}}
QPushButton:disabled, QToolButton:disabled, QLineEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {v["tertiary"]};
    background-color: {v["disabled"]};
}}
QPushButton#workspaceTab:checked {{
    color: {v["highlight_text"]};
    background-color: {v["accent"]};
    border-color: {v["accent_border"]};
}}
QPushButton#previewAction:enabled, QPushButton#modePreviewAction:enabled {{
    border: 1px solid {v["accent_border"]};
    font-weight: 600;
}}
QPushButton#primaryLightingAction[edlActionState="ready"] {{
    color: {v["highlight_text"]};
    background: {v["accent"]};
    border: 1px solid {v["accent_border"]};
    font-weight: 600;
}}
QPushButton#primaryLightingAction[edlActionState="running"] {{
    color: {v["text"]};
    background: {v["control"]};
    border: 1px solid {v["ok"]};
    font-weight: 600;
}}
QStatusBar {{
    color: {v["text"]};
    background: {v["window"]};
    border-top: 1px solid {v["border_strong"]};
}}
QSplitter::handle:horizontal {{ background: {v["border_strong"]}; }}
QSplitter::handle:horizontal:hover {{ background: {v["tertiary"]}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {v["tooltip"]}; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {v["tertiary"]}; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {v["secondary"]}; }}
"""


class AppearanceController(QObject):
    """Persist and apply one application-wide appearance preference."""

    changed = Signal(str, str)

    def __init__(
        self,
        application: QApplication,
        settings: QSettings,
        base_stylesheet: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._application = application
        self._settings = settings
        self._base_stylesheet = base_stylesheet
        self._preference = normalize_appearance(
            settings.value(APPEARANCE_KEY, DEFAULT_APPEARANCE)
        )
        self._style_hints = application.styleHints()
        self._applying = False
        self._last_effective: str | None = None
        signal = getattr(self._style_hints, "colorSchemeChanged", None)
        if signal is not None:
            signal.connect(self._system_scheme_changed)
        self.apply()

    @property
    def preference(self) -> str:
        return self._preference

    @property
    def effective(self) -> str:
        return effective_appearance(
            self._preference,
            self._style_hints.colorScheme(),
        )

    def set_preference(self, preference: str) -> None:
        normalized = normalize_appearance(preference)
        self._preference = normalized
        self._settings.setValue(APPEARANCE_KEY, normalized)
        self._settings.sync()
        self.apply()

    def apply(self) -> None:
        if self._applying:
            return
        self._applying = True
        try:
            effective = self.effective
            desired_palette = palette_for_appearance(self._application, effective)
            desired_stylesheet = self._base_stylesheet + "\n" + theme_stylesheet(effective)
            needs_palette, needs_stylesheet = _application_apply_plan(
                self._application,
                desired_palette,
                desired_stylesheet,
            )
            # QApplication palette/stylesheet changes repolish every existing widget.
            # MainWindow construction may happen repeatedly in tests and diagnostics,
            # so do not reapply identical application-wide presentation state.
            if needs_palette:
                self._application.setPalette(desired_palette)
            if needs_stylesheet:
                self._application.setStyleSheet(desired_stylesheet)
            self._application.setProperty("edlAppearancePreference", self._preference)
            self._application.setProperty("edlAppearanceEffective", effective)
            self._last_effective = effective
            self.changed.emit(self._preference, effective)
        finally:
            self._applying = False

    def _system_scheme_changed(self, *_args: object) -> None:
        if self._preference != "System" or self._applying:
            return
        current = self.effective
        if current != self._last_effective:
            self.apply()


def apply_appearance_ui(ui_module: Any) -> None:
    """Bind the Setup > Appearance actions after header composition exists."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_appearance_ui_applied", False):
        return

    previous_init = window_class.__init__

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        application = QApplication.instance()
        if application is None:
            return

        self._appearance_controller = AppearanceController(
            application,
            self._settings,
            ui_module.base.APP_STYLESHEET,
            self,
        )
        actions = getattr(self, "header_appearance_actions", {})
        group = QActionGroup(self)
        group.setExclusive(True)
        self.header_appearance_action_group = group

        def sync_actions(preference: str, _effective: str) -> None:
            for name, action in actions.items():
                action.blockSignals(True)
                action.setChecked(name == preference)
                action.blockSignals(False)

        for name, action in actions.items():
            group.addAction(action)
            action.triggered.connect(
                lambda checked=False, option=name: (
                    self._appearance_controller.set_preference(option)
                    if checked else None
                )
            )

        self._appearance_controller.changed.connect(sync_actions)
        sync_actions(
            self._appearance_controller.preference,
            self._appearance_controller.effective,
        )

    window_class.__init__ = init
    window_class._edl_appearance_ui_applied = True
