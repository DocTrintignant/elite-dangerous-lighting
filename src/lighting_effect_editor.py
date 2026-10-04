#!/usr/bin/env python3
"""Qt controls for effect-aware colour cardinality, timing and brightness."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

from lighting_colour_picker import PaletteEditor, fit_spinbox_to_contents
from lighting_effect_config import (
    BREATH,
    FIRE,
    FLASH,
    PULSE,
    REACTIVE,
    REACTIVE_ORIGINS,
    RIPPLE,
    SPECTRUM,
    STARLIGHT,
    STATIC,
    WAVE,
    RIPPLE_DIRECTIONS,
    WAVE_DIRECTIONS,
    EffectParameters,
    colour_cardinality,
)


class EffectPaletteEditor(PaletteEditor):
    """Palette editor whose add/remove controls follow selected-effect semantics."""

    PALETTE_VISIBLE_ROWS = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        self._effect = STATIC
        self._minimum_colours = 1
        self._maximum_colours: int | None = 1
        super().__init__(parent)
        self._install_palette_viewport()
        self.picker.setMinimumHeight(205)
        self.paletteChanged.connect(lambda _: self._update_cardinality_controls())
        self._update_cardinality_controls()

    def _install_palette_viewport(self) -> None:
        root = self.layout()
        if root is None:
            return
        index = root.indexOf(self.slots_host)
        if index < 0:
            return
        root.removeWidget(self.slots_host)

        self.palette_scroll = QScrollArea()
        self.palette_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.palette_scroll.setWidgetResizable(False)
        self.palette_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.palette_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        visible_height = (
            self.SLOT_SIZE * self.PALETTE_VISIBLE_ROWS
            + self.SLOT_GAP * (self.PALETTE_VISIBLE_ROWS - 1)
            + 4
        )
        self.palette_scroll.setFixedHeight(visible_height)
        self.palette_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.palette_scroll.setWidget(self.slots_host)
        root.insertWidget(index, self.palette_scroll)

    def set_effect(self, effect: str) -> None:
        self._effect = effect
        self._minimum_colours, self._maximum_colours = colour_cardinality(effect)
        self._update_cardinality_controls()

    def set_palette(self, colours) -> None:  # type: ignore[override]
        super().set_palette(colours)
        self._update_cardinality_controls()

    def _update_cardinality_controls(self) -> None:
        count = len(self.palette())
        can_add = self._maximum_colours is None or count < self._maximum_colours
        can_remove = count > self._minimum_colours
        show_actions = self._effect in {FLASH, BREATH, WAVE, STARLIGHT, FIRE, RIPPLE}

        self.add_button.setVisible(show_actions)
        self.remove_button.setVisible(show_actions)
        self.add_button.setEnabled(can_add)
        self.remove_button.setEnabled(can_remove)

        if self._maximum_colours == self._minimum_colours:
            cardinality = f"exactly {self._minimum_colours}"
        elif self._maximum_colours is None:
            cardinality = f"{self._minimum_colours} or more"
        else:
            cardinality = f"{self._minimum_colours} to {self._maximum_colours}"
        self.add_button.setToolTip(
            f"{self._effect} uses {cardinality} colour(s). Add another colour when allowed."
        )
        self.remove_button.setToolTip(
            f"{self._effect} uses {cardinality} colour(s). Remove the selected colour when allowed."
        )


class EffectParametersEditor(QFrame):
    """Compact effect timing, motion and brightness editor."""

    parametersChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._effect = STATIC
        self._loading = False
        self._parameters = EffectParameters()

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(10, 9, 10, 9)
        self._row.setSpacing(10)

        title = QLabel("Effect controls")
        title.setStyleSheet("font-weight: 600;")
        self._row.addWidget(title)
        self._row.addSpacing(6)

        self.step_seconds = self._seconds_control(
            "Time per colour",
            "How long each FLASH colour stays on before switching to the next. For example, three colours at 0.5 s each make a 1.5 s full loop.",
        )
        self.cycle_seconds = self._seconds_control(
            "Cycle time",
            "How long one complete effect cycle takes.",
        )
        self.duration_seconds = self._seconds_control(
            "Duration",
            "How long one triggered response remains alive after it starts.",
        )
        self.brightness = self._percent_control(
            "Brightness",
            tooltip="How bright the effect can be. 100% uses the selected colours at full brightness.",
        )
        self.minimum_brightness = self._percent_control(
            "Minimum brightness",
            optional=True,
            tooltip="The darkest point reached during the PULSE cycle.",
        )
        self.maximum_brightness = self._percent_control(
            "Maximum brightness",
            optional=True,
            tooltip="The brightest point reached during the PULSE cycle.",
        )
        self.density = self._percent_control(
            "Density",
            optional=True,
            tooltip="How likely each addressable light is to sparkle during STARLIGHT. Higher values produce more simultaneous stars on average.",
        )
        self.speed = self._number_control(
            "Speed",
            minimum=0.01,
            maximum=10.0,
            step=0.05,
            tooltip="How fast a RIPPLE travels. Higher is faster. On a straight normalized strip, 1.0 is roughly one full strip length per second.",
        )
        self.width = self._number_control(
            "Width",
            minimum=0.01,
            maximum=1.0,
            step=0.01,
            tooltip="How thick the moving RIPPLE band is. Small values make a tight moving dot or packet; large values make a broad glowing wave. Hardware can never show less than one addressable LED or segment.",
        )
        self.response_size = self._integer_control(
            "Response size",
            minimum=1,
            maximum=255,
            tooltip="How many addressable lights react around each REACTIVE origin inside a custom zone. Whole-device ALL intentionally uses the complete surface instead.",
        )
        self.direction = self._direction_control()

        self._row.addStretch(1)
        self.set_effect(STATIC, EffectParameters())

    def _all_pairs(self):
        return (
            self.step_seconds,
            self.cycle_seconds,
            self.duration_seconds,
            self.brightness,
            self.minimum_brightness,
            self.maximum_brightness,
            self.density,
            self.speed,
            self.width,
            self.response_size,
            self.direction,
        )

    def _clear_dynamic(self) -> None:
        for pair in self._all_pairs():
            pair[0].setVisible(False)
            pair[1].setVisible(False)

    def _seconds_control(
        self,
        label_text: str,
        tooltip: str,
    ) -> tuple[QLabel, QDoubleSpinBox]:
        label = QLabel(label_text)
        label.setStyleSheet("font-weight: 600;")
        label.setToolTip(tooltip)
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(0.0, 60.0)
        spin.setSingleStep(0.05)
        spin.setSuffix(" s")
        spin.setSpecialValueText("Not set")
        fit_spinbox_to_contents(spin, minimum_width=128)
        spin.setToolTip(tooltip)
        spin.valueChanged.connect(self._changed)
        self._row.addWidget(label)
        self._row.addWidget(spin)
        return label, spin

    def _number_control(
        self,
        label_text: str,
        *,
        minimum: float,
        maximum: float,
        step: float,
        tooltip: str,
    ) -> tuple[QLabel, QDoubleSpinBox]:
        label = QLabel(label_text)
        label.setStyleSheet("font-weight: 600;")
        label.setToolTip(tooltip)
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        fit_spinbox_to_contents(spin, minimum_width=110)
        spin.setToolTip(tooltip)
        spin.valueChanged.connect(self._changed)
        self._row.addWidget(label)
        self._row.addWidget(spin)
        return label, spin

    def _integer_control(
        self,
        label_text: str,
        *,
        minimum: int,
        maximum: int,
        tooltip: str,
    ) -> tuple[QLabel, QSpinBox]:
        label = QLabel(label_text)
        label.setStyleSheet("font-weight: 600;")
        label.setToolTip(tooltip)
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setToolTip(tooltip)
        spin.valueChanged.connect(self._changed)
        self._row.addWidget(label)
        self._row.addWidget(spin)
        return label, spin

    def _direction_control(self) -> tuple[QLabel, QComboBox]:
        label = QLabel("Direction")
        label.setStyleSheet("font-weight: 600;")
        combo = QComboBox()
        combo.setMinimumWidth(150)
        combo.currentTextChanged.connect(self._changed)
        self._row.addWidget(label)
        self._row.addWidget(combo)
        return label, combo

    def _percent_control(
        self,
        label_text: str,
        *,
        optional: bool = False,
        tooltip: str | None = None,
    ) -> tuple[QLabel, QDoubleSpinBox]:
        label = QLabel(label_text)
        label.setStyleSheet("font-weight: 600;")
        if tooltip:
            label.setToolTip(tooltip)
        spin = QDoubleSpinBox()
        spin.setDecimals(0)
        spin.setRange(-1.0 if optional else 0.0, 100.0)
        spin.setSingleStep(5.0)
        spin.setSuffix(" %")
        if optional:
            spin.setSpecialValueText("Not set")
        if tooltip:
            spin.setToolTip(tooltip)
        fit_spinbox_to_contents(spin, minimum_width=118)
        spin.valueChanged.connect(self._changed)
        self._row.addWidget(label)
        self._row.addWidget(spin)
        return label, spin

    @staticmethod
    def _set_pair_visible(pair, visible: bool) -> None:
        pair[0].setVisible(visible)
        pair[1].setVisible(visible)

    @staticmethod
    def _set_pair_tooltip(pair, text: str) -> None:
        pair[0].setToolTip(text)
        pair[1].setToolTip(text)

    def set_effect(self, effect: str, parameters: EffectParameters) -> None:
        if not isinstance(parameters, EffectParameters):
            raise ValueError("parameters must be EffectParameters")
        self._effect = effect
        self._parameters = parameters

        self._loading = True
        try:
            self._clear_dynamic()
            self.direction[0].setText("Direction")
            self._set_pair_tooltip(
                self.direction,
                "Which way the moving lighting pattern travels.",
            )
            if effect == STATIC:
                self._set_pair_visible(self.brightness, True)
                self.brightness[1].setValue(parameters.brightness * 100.0)
            elif effect == FLASH:
                self.step_seconds[0].setText("Time per colour")
                self._set_pair_visible(self.step_seconds, True)
                self._set_pair_visible(self.brightness, True)
                self.step_seconds[1].setValue(
                    0.0 if parameters.step_seconds is None else parameters.step_seconds
                )
                self.brightness[1].setValue(parameters.brightness * 100.0)
            elif effect == PULSE:
                self.cycle_seconds[0].setText("Cycle time")
                self._set_pair_visible(self.cycle_seconds, True)
                self._set_pair_visible(self.minimum_brightness, True)
                self._set_pair_visible(self.maximum_brightness, True)
                self.cycle_seconds[1].setValue(
                    0.0 if parameters.cycle_seconds is None else parameters.cycle_seconds
                )
                self.minimum_brightness[1].setValue(
                    -1.0 if parameters.minimum_brightness is None else parameters.minimum_brightness * 100.0
                )
                self.maximum_brightness[1].setValue(
                    -1.0 if parameters.maximum_brightness is None else parameters.maximum_brightness * 100.0
                )
            elif effect in {BREATH, SPECTRUM}:
                self.cycle_seconds[0].setText("Cycle time")
                if effect == BREATH:
                    self._set_pair_tooltip(
                        self.cycle_seconds,
                        "How long BREATH takes to move through every selected colour and return to the first.",
                    )
                else:
                    self._set_pair_tooltip(
                        self.cycle_seconds,
                        "How long one complete trip through the SPECTRUM rainbow takes.",
                    )
                self._set_pair_visible(self.cycle_seconds, True)
                self._set_pair_visible(self.brightness, True)
                self.cycle_seconds[1].setValue(
                    0.0 if parameters.cycle_seconds is None else parameters.cycle_seconds
                )
                self.brightness[1].setValue(parameters.brightness * 100.0)
            elif effect == WAVE:
                self.cycle_seconds[0].setText("Travel time")
                self._set_pair_tooltip(
                    self.cycle_seconds,
                    "How long the moving colour pattern takes to travel one complete cycle across the device. Lower is faster.",
                )
                self._set_pair_visible(self.cycle_seconds, True)
                self._set_pair_visible(self.direction, True)
                self._set_pair_visible(self.brightness, True)
                self.cycle_seconds[1].setValue(parameters.cycle_seconds or 4.0)
                self._set_direction_items(WAVE_DIRECTIONS, parameters.direction or "LEFT_TO_RIGHT")
                self.brightness[1].setValue(parameters.brightness * 100.0)
            elif effect == STARLIGHT:
                self.cycle_seconds[0].setText("Twinkle time")
                self._set_pair_tooltip(
                    self.cycle_seconds,
                    "How long one STARLIGHT sparkle takes to brighten and fade again.",
                )
                self._set_pair_visible(self.cycle_seconds, True)
                self._set_pair_visible(self.density, True)
                self._set_pair_visible(self.brightness, True)
                self.cycle_seconds[1].setValue(parameters.cycle_seconds or 1.35)
                self.density[1].setValue((0.34 if parameters.density is None else parameters.density) * 100.0)
                self.brightness[1].setValue(parameters.brightness * 100.0)
            elif effect == FIRE:
                self.step_seconds[0].setText("Flicker time")
                self._set_pair_tooltip(
                    self.step_seconds,
                    "How quickly the FIRE pattern changes. Lower is faster and more nervous; higher is slower and smoother.",
                )
                self._set_pair_visible(self.step_seconds, True)
                self._set_pair_visible(self.brightness, True)
                self.step_seconds[1].setValue(parameters.step_seconds or 0.10)
                self.brightness[1].setValue(parameters.brightness * 100.0)
            elif effect == REACTIVE:
                self.duration_seconds[0].setText("Duration")
                self._set_pair_tooltip(
                    self.duration_seconds,
                    "How long the REACTIVE response remains visible after the trigger, fading away during that time.",
                )
                self.direction[0].setText("Origin")
                self._set_pair_tooltip(
                    self.direction,
                    "Where the REACTIVE response is placed inside a custom zone. 'Each section' treats disconnected runs, such as two stand legs, independently. Auto keeps the normal trigger/fallback placement.",
                )
                self._set_pair_visible(self.duration_seconds, True)
                self._set_pair_visible(self.response_size, True)
                self._set_pair_visible(self.direction, True)
                self._set_pair_visible(self.brightness, True)
                self.duration_seconds[1].setValue(parameters.duration_seconds or 0.95)
                self.response_size[1].setValue(parameters.response_size or 1)
                self._set_direction_items(REACTIVE_ORIGINS, parameters.direction or "AUTO")
                self.brightness[1].setValue(parameters.brightness * 100.0)
            elif effect == RIPPLE:
                self.duration_seconds[0].setText("Duration")
                self._set_pair_tooltip(
                    self.duration_seconds,
                    "The maximum lifetime of one RIPPLE. It does not repeat. When this time ends, the ripple disappears.",
                )
                self._set_pair_visible(self.duration_seconds, True)
                self._set_pair_visible(self.direction, True)
                self._set_pair_visible(self.speed, True)
                self._set_pair_visible(self.width, True)
                self._set_pair_visible(self.brightness, True)
                self.duration_seconds[1].setValue(parameters.duration_seconds or 1.55)
                self.speed[1].setValue(parameters.speed or 0.72)
                self.width[1].setValue(parameters.width or 0.11)
                self._set_direction_items(RIPPLE_DIRECTIONS, parameters.direction or "CENTER_OUT")
                self.brightness[1].setValue(parameters.brightness * 100.0)
            else:
                self._set_pair_visible(self.brightness, True)
                self.brightness[1].setValue(parameters.brightness * 100.0)
        finally:
            self._loading = False

    def _set_direction_items(self, values: tuple[str, ...], selected: str) -> None:
        combo = self.direction[1]
        display = {
            "EACH_SECTION_START": "Each section start",
            "EACH_SECTION_CENTER": "Each section centre",
            "EACH_SECTION_END": "Each section end",
            "ZONE_START": "Zone first",
            "ZONE_END": "Zone last",
        }
        combo.clear()
        for value in values:
            combo.addItem(display.get(value, value.replace("_", " ").title()), value)
        index = combo.findData(selected)
        combo.setCurrentIndex(max(0, index))

    @staticmethod
    def _optional_seconds(spin: QDoubleSpinBox) -> float | None:
        value = spin.value()
        return None if value == 0.0 else value

    @staticmethod
    def _optional_percent(spin: QDoubleSpinBox) -> float | None:
        value = spin.value()
        return None if value < 0.0 else value / 100.0

    def parameters(self) -> EffectParameters:
        brightness = self.brightness[1].value() / 100.0
        if self._effect == FLASH:
            return EffectParameters(brightness=brightness, step_seconds=self._optional_seconds(self.step_seconds[1]))
        if self._effect == PULSE:
            return EffectParameters(
                cycle_seconds=self._optional_seconds(self.cycle_seconds[1]),
                minimum_brightness=self._optional_percent(self.minimum_brightness[1]),
                maximum_brightness=self._optional_percent(self.maximum_brightness[1]),
            )
        if self._effect in {BREATH, SPECTRUM}:
            return EffectParameters(brightness=brightness, cycle_seconds=self._optional_seconds(self.cycle_seconds[1]))
        if self._effect == WAVE:
            return EffectParameters(
                brightness=brightness,
                cycle_seconds=self._optional_seconds(self.cycle_seconds[1]),
                direction=self.direction[1].currentData(),
            )
        if self._effect == STARLIGHT:
            return EffectParameters(
                brightness=brightness,
                cycle_seconds=self._optional_seconds(self.cycle_seconds[1]),
                density=self._optional_percent(self.density[1]),
            )
        if self._effect == FIRE:
            return EffectParameters(brightness=brightness, step_seconds=self._optional_seconds(self.step_seconds[1]))
        if self._effect == REACTIVE:
            return EffectParameters(
                brightness=brightness,
                direction=self.direction[1].currentData(),
                duration_seconds=self._optional_seconds(self.duration_seconds[1]),
                response_size=self.response_size[1].value(),
            )
        if self._effect == RIPPLE:
            return EffectParameters(
                brightness=brightness,
                direction=self.direction[1].currentData(),
                speed=self.speed[1].value(),
                width=self.width[1].value(),
                duration_seconds=self._optional_seconds(self.duration_seconds[1]),
            )
        return EffectParameters(brightness=brightness)

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        try:
            parameters = self.parameters()
        except ValueError:
            return
        self._parameters = parameters
        self.parametersChanged.emit(parameters)


# ---------------------------------------------------------------------------
# Generic software effect preview
# ---------------------------------------------------------------------------
# Presentation-only preview kept with the effect authoring controls. It samples
# existing hardware-neutral effect primitives and never acquires hardware.

import time
from collections.abc import Sequence

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from lighting_effect_config import EffectParameters, validate_effect_configuration
from lighting_effect_runtime_frames import (
    is_continuous_frame_effect,
    sample_persisted_effect_frame,
)
from lighting_effects import (
    breathe_palette_rgb,
    flash_palette_rgb,
    pulse_rgb,
    scale_rgb,
)

RGB = tuple[int, int, int]

EFFECT_PREVIEW_HELP = (
    "<b>Preview effect</b><br><br>"
    "Shows the selected effect, colours and effect controls on a generic "
    "device-independent surface.<br><br>"
    "It does not use lighting hardware, the Rule source/activation condition, "
    "or Controller response. Spatial effects are only a visual approximation; "
    "their exact shape depends on the real device geometry."
)


def _preview_points(columns: int, rows: int):
    return tuple(
        (
            0.5 if columns <= 1 else column / (columns - 1),
            0.5 if rows <= 1 else row / (rows - 1),
        )
        for row in range(rows)
        for column in range(columns)
    )


def sample_effect_preview(
    effect: str,
    colours: Sequence[RGB],
    parameters: EffectParameters,
    elapsed_seconds: float,
    *,
    columns: int = 24,
    rows: int = 4,
) -> tuple[RGB, ...]:
    """Sample one generic preview frame without inventing hardware semantics."""
    effect = str(effect).strip().upper()
    palette = tuple(colours)
    validate_effect_configuration(effect, palette, parameters)
    points = _preview_points(columns, rows)

    if is_continuous_frame_effect(effect):
        return tuple(
            sample_persisted_effect_frame(
                effect,
                points,
                max(0.0, float(elapsed_seconds)),
                palette,
                parameters,
            )
        )

    if effect == "STATIC":
        rgb = scale_rgb(palette[0], parameters.brightness)
    elif effect == "FLASH":
        # Imported Virpil Flashing may intentionally have no real-time cadence.
        # In that case show its first configured colour rather than invent timing.
        if parameters.step_seconds is None:
            rgb = scale_rgb(palette[0], parameters.brightness)
        else:
            phase = int(max(0.0, float(elapsed_seconds)) / parameters.step_seconds)
            rgb = scale_rgb(flash_palette_rgb(palette, phase), parameters.brightness)
    elif effect == "PULSE":
        rgb = pulse_rgb(
            palette[0],
            max(0.0, float(elapsed_seconds)),
            period_seconds=parameters.cycle_seconds,
            minimum_intensity=parameters.minimum_brightness,
            maximum_intensity=parameters.maximum_brightness,
        )
    elif effect == "BREATH":
        rgb = scale_rgb(
            breathe_palette_rgb(
                palette,
                max(0.0, float(elapsed_seconds)),
                period_seconds=parameters.cycle_seconds,
            ),
            parameters.brightness,
        )
    else:
        rgb = scale_rgb(palette[0], parameters.brightness)

    return tuple(rgb for _point in points)


class EffectPreviewStrip(QWidget):
    """Small generic animated surface; callers own the Preview effect button."""

    COLUMNS = 24
    ROWS = 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._effect = "STATIC"
        self._colours: tuple[RGB, ...] = ((255, 255, 255),)
        self._parameters = EffectParameters()
        self._started = time.perf_counter()
        self._error = ""
        self.setMinimumHeight(52)
        self.setMaximumHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(
            "Generic software preview only. Real device geometry may render spatial effects differently."
        )
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.update)

    def set_configuration(
        self,
        effect: str,
        colours: Sequence[RGB],
        parameters: EffectParameters,
    ) -> None:
        self._effect = str(effect).strip().upper()
        self._colours = tuple(colours)
        self._parameters = parameters
        try:
            validate_effect_configuration(
                self._effect,
                self._colours,
                self._parameters,
            )
            self._error = ""
        except Exception as exc:
            self._error = str(exc)
        self._started = time.perf_counter()
        self.update()

    def start(self) -> None:
        self._started = time.perf_counter()
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt API
        super().setVisible(visible)
        if visible:
            self.start()
        else:
            self.stop()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        if self._error:
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(
                self.rect().adjusted(8, 4, -8, -4),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._error,
            )
            return

        elapsed = max(0.0, time.perf_counter() - self._started)
        try:
            frame = sample_effect_preview(
                self._effect,
                self._colours,
                self._parameters,
                elapsed,
                columns=self.COLUMNS,
                rows=self.ROWS,
            )
        except Exception as exc:
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(
                self.rect().adjusted(8, 4, -8, -4),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                str(exc),
            )
            return

        width = max(1.0, self.width() / self.COLUMNS)
        height = max(1.0, self.height() / self.ROWS)
        index = 0
        for row in range(self.ROWS):
            for column in range(self.COLUMNS):
                rgb = frame[index]
                index += 1
                painter.fillRect(
                    int(column * width),
                    int(row * height),
                    max(1, int((column + 1) * width) - int(column * width) + 1),
                    max(1, int((row + 1) * height) - int(row * height) + 1),
                    QColor(*rgb),
                )

