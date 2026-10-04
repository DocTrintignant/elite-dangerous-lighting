#!/usr/bin/env python3
"""Shared semantic presentation tokens for the EDL desktop UI.

Presentation only. These values do not alter typography selection, profile/rule
semantics, runtime authority, renderer ownership or any hardware transport.
"""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase


# Text roles.
TEXT_PRIMARY = "#ECECEF"
TEXT_SECONDARY = "#B4B4BB"
TEXT_TERTIARY = "#8E8E97"

# Surface / divider roles for the current dark presentation.
SURFACE_BASE = "#1A1A1C"
SURFACE_BAR = "#141416"
SURFACE_RAISED = "#202023"
SURFACE_CONTROL = "#27272A"
SURFACE_DISABLED = "#252525"
SURFACE_TOOLTIP = "#2D2D2D"
BORDER_SUBTLE = "#2E2E33"
BORDER_STRONG = "#414147"

# State roles.
ACCENT = "#6E3A86"
ACCENT_BORDER = "#8E5AA5"
STATUS_OK = "#4CC38A"
STATUS_WARNING = "#D7AA57"
STATUS_ERROR = "#E05A5A"

# Shared spacing scale for presentation-only layout adapters.
SPACE_XS = 4
SPACE_SM = 6
SPACE_MD = 9
SPACE_LG = 12
SPACE_XL = 16

# Co-located application typography tokens.
# Readability-first EDL type scale in DPI-aware typographic points.
# Geometry/spacing remain separate concerns and may still use device pixels.
BODY_PT = 10.5
HELPER_PT = 9.75
MICRO_PT = 8.25
SECTION_PT = 12.0
DIALOG_TITLE_PT = 13.5
APP_TITLE_PT = 15.75
SEMIBOLD = 600


SEMANTIC_QSS = f"""
QLabel[edlTextRole="secondary"] {{
    color: {TEXT_SECONDARY};
}}
QLabel[edlTextRole="tertiary"] {{
    color: {TEXT_TERTIARY};
}}
QLabel[edlTextRole="status"] {{
    color: {TEXT_PRIMARY};
}}
QLabel[edlState="ok"], QPushButton[edlState="ok"] {{
    color: {STATUS_OK};
}}
QLabel[edlState="warning"], QPushButton[edlState="warning"] {{
    color: {STATUS_WARNING};
}}
QLabel[edlState="attention"], QPushButton[edlState="attention"] {{
    color: {STATUS_ERROR};
}}
QToolTip {{
    color: {TEXT_PRIMARY};
    background-color: {SURFACE_TOOLTIP};
    border: 1px solid {BORDER_STRONG};
}}
QMenu::item:selected {{
    background-color: {SURFACE_CONTROL};
}}
QMenu::item:disabled {{
    color: {TEXT_TERTIARY};
}}
QComboBox QAbstractItemView {{
    selection-background-color: {ACCENT};
}}
QFrame#sectionBox {{
    border-color: {BORDER_SUBTLE};
}}
QTableWidget {{
    gridline-color: {BORDER_SUBTLE};
}}
QTableWidget::item:selected {{
    background: {ACCENT};
    color: {TEXT_PRIMARY};
    font-weight: 600;
}}
QHeaderView::section {{
    color: {TEXT_SECONDARY};
    font-size: {BODY_PT}pt;
}}
QPushButton:disabled, QToolButton:disabled, QLineEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {TEXT_TERTIARY};
    background-color: {SURFACE_DISABLED};
}}
QPushButton#workspaceTab:checked {{
    color: {TEXT_PRIMARY};
    background-color: {ACCENT};
    border-color: {ACCENT_BORDER};
}}
QWidget#previewPanel {{
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_SUBTLE};
}}
QPushButton#previewAction:enabled {{
    border: 1px solid {ACCENT_BORDER};
    font-weight: 600;
}}
QPushButton#primaryLightingAction[edlActionState="ready"] {{
    color: {TEXT_PRIMARY};
    background: {ACCENT};
    border: 1px solid {ACCENT_BORDER};
    font-weight: 600;
}}
QPushButton#primaryLightingAction[edlActionState="running"] {{
    color: {TEXT_PRIMARY};
    background: {SURFACE_CONTROL};
    border: 1px solid {STATUS_OK};
    font-weight: 600;
}}
QSplitter::handle:horizontal {{
    width: 7px;
    background: {BORDER_STRONG};
}}
QSplitter::handle:horizontal:hover {{
    background: {TEXT_TERTIARY};
}}
QScrollBar:vertical {{
    width: 12px;
    background: {SURFACE_TOOLTIP};
}}
QScrollBar:horizontal {{
    height: 12px;
    background: {SURFACE_TOOLTIP};
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {TEXT_TERTIARY};
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_SECONDARY};
}}
"""

PRIMARY_ACTION_QSS = (
    f"QPushButton {{ background:{ACCENT}; color:{TEXT_PRIMARY}; font-weight:600; }}"
)


def apply_application_typography(application) -> str:
    """Use the platform UI font with EDL's DPI-aware type scale."""

    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPointSizeF(BODY_PT)
    font.setKerning(True)
    application.setFont(font)
    # QHeaderView can retain a smaller platform header font even after the
    # application font is set. Give headers the exact same accepted native
    # application font instead of introducing a dialog-local override.
    application.setFont(font, "QHeaderView")

    family = application.font().family()
    application.setProperty("edlTypographyFamily", family)
    application.setProperty("edlTypographyBundled", False)
    application.setProperty("edlTypographySource", "system")
    return family
