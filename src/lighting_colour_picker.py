#!/usr/bin/env python3
"""Reusable Qt colour/palette controls for Elite Dangerous Lighting.

This module owns UI interaction only. It does not know about rules, profiles,
Chroma transport, Elite state, or effect timing.
"""

from __future__ import annotations

from typing import Iterable

from lighting_settings import app_settings

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

RGB = tuple[int, int, int]
Palette = tuple[RGB, ...]


def rgb_to_hex(rgb: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def qcolor_to_rgb(colour: QColor) -> RGB:
    return colour.red(), colour.green(), colour.blue()


def fit_spinbox_to_contents(spin, *, minimum_width: int = 118) -> None:
    """Give a Qt spin box enough width for every value it can display.

    This is the common sizing rule for numeric UI cells. It deliberately avoids
    per-field maximum widths, because native spin-button chrome consumes a
    platform-dependent amount of horizontal space.
    """
    candidates: list[str] = []
    for value in (spin.minimum(), spin.maximum()):
        candidates.append(f"{spin.prefix()}{spin.textFromValue(value)}{spin.suffix()}")
    if spin.specialValueText():
        candidates.append(spin.specialValueText())
    text_width = max(spin.fontMetrics().horizontalAdvance(text) for text in candidates)
    spin.setMinimumWidth(max(minimum_width, text_width + 72))
    spin.setMaximumWidth(16777215)


def palette_flow_geometry(
    visual_width: int,
    count: int,
    *,
    slot_size: int = 34,
    minimum_gap: int = 5,
) -> tuple[tuple[tuple[int, int], ...], int]:
    """Return fixed-square slot positions constrained to the picker visual width.

    The first slot always starts at x=0. A complete row ends exactly at the
    spectrum+hue boundary. Intermediate gaps absorb at most pixel rounding,
    while fixed-size squares never stretch. Additional slots wrap vertically.
    """
    width = max(int(visual_width), slot_size)
    item_count = max(1, int(count))
    columns = max(1, (width + minimum_gap) // (slot_size + minimum_gap))

    if columns == 1:
        x_positions = (0,)
    else:
        span = width - slot_size
        x_positions = tuple(
            round(index * span / (columns - 1))
            for index in range(columns)
        )

    positions = tuple(
        (
            x_positions[index % columns],
            (index // columns) * (slot_size + minimum_gap),
        )
        for index in range(item_count)
    )
    rows = (item_count + columns - 1) // columns
    height = rows * slot_size + (rows - 1) * minimum_gap
    return positions, height


def compact_flow_geometry(
    visual_width: int,
    count: int,
    *,
    slot_size: int = 34,
    gap: int = 5,
) -> tuple[tuple[tuple[int, int], ...], int]:
    """Return left-packed fixed-square positions for a bounded viewport."""
    width = max(int(visual_width), slot_size)
    item_count = max(1, int(count))
    columns = max(1, (width + gap) // (slot_size + gap))
    positions = tuple(
        (
            (index % columns) * (slot_size + gap),
            (index // columns) * (slot_size + gap),
        )
        for index in range(item_count)
    )
    rows = (item_count + columns - 1) // columns
    height = rows * slot_size + (rows - 1) * gap
    return positions, height


def _validate_rgb(rgb: RGB) -> RGB:
    if not isinstance(rgb, tuple) or len(rgb) != 3:
        raise ValueError("colour must be an (R, G, B) tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in rgb):
        raise ValueError("RGB components must be integers")
    if any(value < 0 or value > 255 for value in rgb):
        raise ValueError("RGB components must be 0..255")
    return rgb


def _validate_palette(colours: Iterable[RGB]) -> Palette:
    palette = tuple(colours)
    if not palette:
        raise ValueError("palette must contain at least one colour")
    for colour in palette:
        _validate_rgb(colour)
    return palette


class SpectrumSquare(QWidget):
    """HSV saturation/value field with a movable selection point."""

    colourChanged = Signal(QColor)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hue = 0.0
        self._saturation = 1.0
        self._value = 1.0
        self.setMinimumSize(220, 175)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(270, 205)

    def current_colour(self) -> QColor:
        return QColor.fromHsvF(self._hue, self._saturation, self._value)

    def set_hue(self, hue: float, *, emit: bool = True) -> None:
        self._hue = min(1.0, max(0.0, float(hue)))
        self.update()
        if emit:
            self.colourChanged.emit(self.current_colour())

    def set_colour(self, colour: QColor, *, emit: bool = False) -> None:
        if not colour.isValid():
            return
        hue = colour.hsvHueF()
        if hue >= 0.0:
            self._hue = hue
        self._saturation = colour.hsvSaturationF()
        self._value = colour.valueF()
        self.update()
        if emit:
            self.colourChanged.emit(self.current_colour())

    def _set_from_position(self, x: float, y: float) -> None:
        rect = self.rect().adjusted(1, 1, -1, -1)
        width = max(1, rect.width())
        height = max(1, rect.height())
        self._saturation = min(1.0, max(0.0, (x - rect.left()) / width))
        self._value = 1.0 - min(1.0, max(0.0, (y - rect.top()) / height))
        self.update()
        self.colourChanged.emit(self.current_colour())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_from_position(event.position().x(), event.position().y())

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_position(event.position().x(), event.position().y())

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)

        hue_colour = QColor.fromHsvF(self._hue, 1.0, 1.0)
        horizontal = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        horizontal.setColorAt(0.0, QColor(255, 255, 255))
        horizontal.setColorAt(1.0, hue_colour)
        painter.fillRect(rect, horizontal)

        vertical = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        vertical.setColorAt(0.0, QColor(0, 0, 0, 0))
        vertical.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(rect, vertical)

        x = rect.left() + self._saturation * rect.width()
        y = rect.top() + (1.0 - self._value) * rect.height()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0), 3))
        painter.drawEllipse(int(x - 7), int(y - 7), 14, 14)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawEllipse(int(x - 6), int(y - 6), 12, 12)


class HueBar(QWidget):
    """Vertical full-spectrum hue control."""

    hueChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hue = 0.0
        self.setFixedWidth(26)
        self.setMinimumHeight(175)

    def set_hue(self, hue: float) -> None:
        self._hue = min(1.0, max(0.0, float(hue)))
        self.update()

    def _set_from_y(self, y: float) -> None:
        rect = self.rect().adjusted(1, 1, -1, -1)
        height = max(1, rect.height())
        self._hue = min(1.0, max(0.0, (y - rect.top()) / height))
        self.update()
        self.hueChanged.emit(self._hue)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_from_y(event.position().y())

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_y(event.position().y())

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        rect = self.rect().adjusted(1, 1, -1, -1)
        gradient = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        stops = (
            (0.0, QColor(255, 0, 0)),
            (1 / 6, QColor(255, 255, 0)),
            (2 / 6, QColor(0, 255, 0)),
            (3 / 6, QColor(0, 255, 255)),
            (4 / 6, QColor(0, 0, 255)),
            (5 / 6, QColor(255, 0, 255)),
            (1.0, QColor(255, 0, 0)),
        )
        for position, colour in stops:
            gradient.setColorAt(position, colour)
        painter.fillRect(rect, gradient)

        y = rect.top() + self._hue * rect.height()
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawLine(rect.left() - 1, int(y), rect.right() + 1, int(y))
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawLine(rect.left(), int(y + 2), rect.right(), int(y + 2))


class ColourPicker(QWidget):
    """Embedded spectrum picker with numerical HEX/R/G/B entry."""

    colourChanged = Signal(QColor)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.spectrum = SpectrumSquare()
        self.hue_bar = HueBar()
        layout.addWidget(self.spectrum, 1)
        layout.addWidget(self.hue_bar)

        values = QVBoxLayout()
        values.setSpacing(7)
        values_label = QLabel("Colour values")
        values_label.setStyleSheet("font-weight: 600;")
        values.addWidget(values_label)

        hex_label = QLabel("HEX")
        hex_label.setStyleSheet("font-weight: 600;")
        values.addWidget(hex_label)
        self.hex_edit = QLineEdit("#FF0000")
        self.hex_edit.setMinimumWidth(145)
        self.hex_edit.setMinimumHeight(32)
        self.hex_edit.editingFinished.connect(self._hex_edited)
        values.addWidget(self.hex_edit)

        rgb_row = QHBoxLayout()
        rgb_row.setSpacing(6)
        self.spin_r = self._make_spin("R")
        self.spin_g = self._make_spin("G")
        self.spin_b = self._make_spin("B")
        for label, spin in (("R", self.spin_r), ("G", self.spin_g), ("B", self.spin_b)):
            group = QVBoxLayout()
            group.setSpacing(3)
            channel = QLabel(label)
            channel.setStyleSheet("font-weight: 600;")
            group.addWidget(channel)
            group.addWidget(spin)
            rgb_row.addLayout(group)
        values.addLayout(rgb_row)
        values.addStretch(1)
        layout.addLayout(values)

        self.spectrum.colourChanged.connect(self._spectrum_changed)
        self.hue_bar.hueChanged.connect(self._hue_changed)
        self.set_colour(QColor(255, 0, 0), emit=False)

    def visual_width(self) -> int:
        layout = self.layout()
        spacing = layout.spacing() if layout is not None else 10
        return max(0, self.spectrum.width()) + spacing + self.hue_bar.width()

    def _make_spin(self, name: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setObjectName(f"colour{name}")
        spin.setRange(0, 255)
        spin.setMinimumHeight(32)
        fit_spinbox_to_contents(spin)
        spin.valueChanged.connect(self._rgb_edited)
        return spin

    def colour(self) -> QColor:
        return QColor(self.spin_r.value(), self.spin_g.value(), self.spin_b.value())

    def set_colour(self, colour: QColor, *, emit: bool = False) -> None:
        if not colour.isValid():
            return
        self._updating = True
        try:
            self.spectrum.set_colour(colour, emit=False)
            hue = colour.hsvHueF()
            if hue >= 0.0:
                self.hue_bar.set_hue(hue)
            self.spin_r.setValue(colour.red())
            self.spin_g.setValue(colour.green())
            self.spin_b.setValue(colour.blue())
            self.hex_edit.setText(colour.name(QColor.NameFormat.HexRgb).upper())
        finally:
            self._updating = False
        if emit:
            self.colourChanged.emit(colour)

    def _spectrum_changed(self, colour: QColor) -> None:
        if self._updating:
            return
        self.set_colour(colour, emit=True)

    def _hue_changed(self, hue: float) -> None:
        if self._updating:
            return
        self.spectrum.set_hue(hue, emit=True)

    def _rgb_edited(self) -> None:
        if self._updating:
            return
        self.set_colour(self.colour(), emit=True)

    def _hex_edited(self) -> None:
        if self._updating:
            return
        text = self.hex_edit.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        colour = QColor(text)
        if not colour.isValid():
            self.hex_edit.setText(self.colour().name(QColor.NameFormat.HexRgb).upper())
            return
        self.set_colour(colour, emit=True)


class PaletteSwatchButton(QAbstractButton):
    """Compact fixed square colour slot, isolated from QPushButton styling."""

    def __init__(self, colour: RGB, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rgb = _validate_rgb(colour)
        self._selected = False
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(34, 34)
        self.refresh_style(False)

    def set_rgb(self, colour: RGB) -> None:
        self.rgb = _validate_rgb(colour)
        self.setToolTip(f"{rgb_to_hex(self.rgb)}  ·  RGB {self.rgb[0]}, {self.rgb[1]}, {self.rgb[2]}")
        self.update()

    def refresh_style(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.setChecked(self._selected)
        self.setToolTip(f"{rgb_to_hex(self.rgb)}  ·  RGB {self.rgb[0]}, {self.rgb[1]}, {self.rgb[2]}")
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect().adjusted(2, 2, -3, -3)
        painter.fillRect(rect, QColor(*self.rgb))
        border = QColor(255, 255, 255) if self._selected else QColor(138, 138, 138)
        painter.setPen(QPen(border, 2 if self._selected else 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)


class PaletteStripWidget(QWidget):
    """Compact read-only palette strip for the main rule table."""

    def __init__(self, colours: Iterable[RGB], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        palette = _validate_palette(colours)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(3)
        visible = palette[:7]
        for colour in visible:
            swatch = QLabel()
            swatch.setFixedSize(20, 18)
            swatch.setStyleSheet(
                f"background-color: {rgb_to_hex(colour)}; border: 1px solid #666666;"
            )
            swatch.setToolTip(rgb_to_hex(colour))
            layout.addWidget(swatch)
        if len(palette) > len(visible):
            extra = QLabel(f"+{len(palette) - len(visible)}")
            extra.setToolTip(f"{len(palette)} colours total")
            layout.addWidget(extra)
        layout.addStretch(1)


class PaletteEditor(QFrame):
    """N-colour palette editor with one responsive geometry for every effect."""

    paletteChanged = Signal(object)
    SETTINGS_KEY = "colour_favourites"
    SLOT_SIZE = 34
    SLOT_GAP = 5
    SAVED_VISIBLE_ROWS = 2
    SAVED_VIEWPORT_HEIGHT = SLOT_SIZE * SAVED_VISIBLE_ROWS + SLOT_GAP * (SAVED_VISIBLE_ROWS - 1) + 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._palette: list[RGB] = [(255, 255, 255)]
        self._active_index = 0
        self._loading = False
        self._selected_favourite: str | None = None
        self._slot_buttons: list[PaletteSwatchButton] = []
        self._favourite_buttons: list[PaletteSwatchButton] = []
        self._settings = app_settings()

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 9, 10, 9)
        root.setSpacing(10)

        palette_header = QHBoxLayout()
        palette_header.setSpacing(7)
        title = QLabel("Colours")
        title.setStyleSheet("font-weight: 600;")
        palette_header.addWidget(title)
        palette_header.addStretch(1)

        self.add_button = QPushButton("+ Add colour")
        self.add_button.setToolTip("Add another colour to this effect palette")
        self.add_button.setMinimumWidth(108)
        self.add_button.clicked.connect(self._add_colour)
        palette_header.addWidget(self.add_button)

        self.remove_button = QPushButton("− Remove")
        self.remove_button.setToolTip("Remove the selected colour")
        self.remove_button.setMinimumWidth(98)
        self.remove_button.clicked.connect(self._remove_colour)
        palette_header.addWidget(self.remove_button)
        root.addLayout(palette_header)

        self.slots_host = QWidget()
        self.slots_host.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        root.addWidget(self.slots_host, 0, Qt.AlignmentFlag.AlignLeft)

        self.picker = ColourPicker()
        self.picker.colourChanged.connect(self._picker_changed)
        root.addWidget(self.picker)

        saved_row = QHBoxLayout()
        saved_row.setSpacing(7)
        saved_label = QLabel("Saved")
        saved_label.setStyleSheet("font-weight: 600;")
        saved_row.addWidget(saved_label, 0, Qt.AlignmentFlag.AlignTop)

        self.favourites_scroll = QScrollArea()
        self.favourites_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.favourites_scroll.setWidgetResizable(False)
        self.favourites_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.favourites_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.favourites_scroll.setFixedHeight(self.SAVED_VIEWPORT_HEIGHT)
        self.favourites_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.favourites_host = QWidget()
        self.favourites_host.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.favourites_scroll.setWidget(self.favourites_host)
        saved_row.addWidget(self.favourites_scroll, 1, Qt.AlignmentFlag.AlignTop)

        self.remove_saved_button = QPushButton("Remove saved")
        self.remove_saved_button.setToolTip("Remove the selected colour from Saved colours")
        self.remove_saved_button.setEnabled(False)
        self.remove_saved_button.clicked.connect(self._remove_selected_favourite)
        saved_row.addWidget(self.remove_saved_button, 0, Qt.AlignmentFlag.AlignTop)

        save_button = QPushButton("Save colour")
        save_button.setToolTip("Save the current colour for reuse in other rules")
        save_button.clicked.connect(self._save_favourite)
        saved_row.addWidget(save_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(saved_row)

        self._rebuild_slots()
        self._rebuild_favourites()
        self.set_palette(((255, 255, 255),))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_slot_geometry()
        self._sync_favourite_geometry()

    def palette(self) -> Palette:
        return tuple(self._palette)

    def set_palette(self, colours: Iterable[RGB]) -> None:
        palette = _validate_palette(colours)
        self._loading = True
        try:
            self._palette = list(palette)
            self._active_index = min(self._active_index, len(self._palette) - 1)
            self._rebuild_slots()
            self.picker.set_colour(QColor(*self._palette[self._active_index]), emit=False)
        finally:
            self._loading = False
        self._sync_slot_geometry()

    def _sync_slot_geometry(self) -> None:
        if not self._slot_buttons:
            return
        visual_width = max(self.SLOT_SIZE, self.picker.visual_width())
        positions, height = palette_flow_geometry(
            visual_width,
            len(self._slot_buttons),
            slot_size=self.SLOT_SIZE,
            minimum_gap=self.SLOT_GAP,
        )
        self.slots_host.setFixedSize(visual_width, height)
        for button, (x, y) in zip(self._slot_buttons, positions):
            button.setGeometry(x, y, self.SLOT_SIZE, self.SLOT_SIZE)

    def _sync_favourite_geometry(self) -> None:
        viewport_width = self.favourites_scroll.viewport().width()
        if viewport_width <= 0:
            viewport_width = self.SLOT_SIZE * 8 + self.SLOT_GAP * 7
        positions, content_height = compact_flow_geometry(
            viewport_width,
            max(1, len(self._favourite_buttons)),
            slot_size=self.SLOT_SIZE,
            gap=self.SLOT_GAP,
        )
        self.favourites_host.setFixedSize(viewport_width, max(content_height, self.SLOT_SIZE))
        for button, (x, y) in zip(self._favourite_buttons, positions):
            button.setGeometry(x, y, self.SLOT_SIZE, self.SLOT_SIZE)

    def _rebuild_slots(self) -> None:
        for button in self._slot_buttons:
            button.hide()
            button.deleteLater()
        self._slot_buttons = []

        for index, colour in enumerate(self._palette):
            button = PaletteSwatchButton(colour, self.slots_host)
            button.clicked.connect(lambda checked=False, i=index: self._select_slot(i))
            button.refresh_style(index == self._active_index)
            button.show()
            self._slot_buttons.append(button)

        self.remove_button.setEnabled(len(self._palette) > 1)
        self._sync_slot_geometry()

    def _select_slot(self, index: int) -> None:
        if index < 0 or index >= len(self._palette):
            return
        self._active_index = index
        for slot_index, button in enumerate(self._slot_buttons):
            button.refresh_style(slot_index == index)
        self.picker.set_colour(QColor(*self._palette[index]), emit=False)

    def _add_colour(self) -> None:
        colour = qcolor_to_rgb(self.picker.colour())
        self._palette.append(colour)
        self._active_index = len(self._palette) - 1
        self._rebuild_slots()
        self._emit_palette()

    def _remove_colour(self) -> None:
        if len(self._palette) <= 1:
            return
        self._palette.pop(self._active_index)
        self._active_index = min(self._active_index, len(self._palette) - 1)
        self._rebuild_slots()
        self.picker.set_colour(QColor(*self._palette[self._active_index]), emit=False)
        self._emit_palette()

    def _picker_changed(self, colour: QColor) -> None:
        if self._loading:
            return
        rgb = qcolor_to_rgb(colour)
        self._palette[self._active_index] = rgb
        if 0 <= self._active_index < len(self._slot_buttons):
            self._slot_buttons[self._active_index].set_rgb(rgb)
        current_hex = colour.name(QColor.NameFormat.HexRgb).upper()
        if self._selected_favourite is not None and current_hex != self._selected_favourite:
            self._selected_favourite = None
            self._refresh_favourite_selection()
        self._emit_palette()

    def _emit_palette(self) -> None:
        if not self._loading:
            self.paletteChanged.emit(self.palette())

    def _favourites(self) -> list[str]:
        raw = self._settings.value(self.SETTINGS_KEY, [])
        if raw is None:
            return []
        if isinstance(raw, str):
            values = [raw]
        else:
            try:
                values = list(raw)
            except TypeError:
                values = []
        result: list[str] = []
        for value in values:
            colour = QColor(str(value))
            if colour.isValid():
                canonical = colour.name(QColor.NameFormat.HexRgb).upper()
                if canonical not in result:
                    result.append(canonical)
        return result[:24]

    def _save_favourite(self) -> None:
        current = self.picker.colour().name(QColor.NameFormat.HexRgb).upper()
        favourites = self._favourites()
        if current not in favourites:
            favourites.append(current)
            self._settings.setValue(self.SETTINGS_KEY, favourites[:24])
            self._settings.sync()
        self._selected_favourite = current
        self._rebuild_favourites()

    def _select_favourite(self, hex_colour: str) -> None:
        colour = QColor(hex_colour)
        if not colour.isValid():
            return
        self._selected_favourite = colour.name(QColor.NameFormat.HexRgb).upper()
        self.picker.set_colour(colour, emit=True)
        self._selected_favourite = colour.name(QColor.NameFormat.HexRgb).upper()
        self._refresh_favourite_selection()

    def _remove_selected_favourite(self) -> None:
        if self._selected_favourite is None:
            return
        favourites = self._favourites()
        favourites = [value for value in favourites if value != self._selected_favourite]
        self._settings.setValue(self.SETTINGS_KEY, favourites)
        self._settings.sync()
        self._selected_favourite = None
        self._rebuild_favourites()

    def _refresh_favourite_selection(self) -> None:
        favourites = self._favourites()
        if self._selected_favourite not in favourites:
            self._selected_favourite = None
        for index, button in enumerate(self._favourite_buttons):
            selected = (
                self._selected_favourite is not None
                and index < len(favourites)
                and favourites[index] == self._selected_favourite
            )
            button.refresh_style(selected)
        self.remove_saved_button.setEnabled(self._selected_favourite is not None)

    def _rebuild_favourites(self) -> None:
        for button in self._favourite_buttons:
            button.hide()
            button.deleteLater()
        self._favourite_buttons = []

        favourites = self._favourites()
        if self._selected_favourite not in favourites:
            self._selected_favourite = None
        for hex_colour in favourites:
            colour = QColor(hex_colour)
            button = PaletteSwatchButton(qcolor_to_rgb(colour), self.favourites_host)
            button.refresh_style(hex_colour == self._selected_favourite)
            button.setToolTip(f"Use saved colour {hex_colour}")
            button.clicked.connect(
                lambda checked=False, value=hex_colour: self._select_favourite(value)
            )
            button.show()
            self._favourite_buttons.append(button)
        self.remove_saved_button.setEnabled(self._selected_favourite is not None)
        self._sync_favourite_geometry()
