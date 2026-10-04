#!/usr/bin/env python3
"""Guided physical calibration for an unverified segmented Govee device.

This is deliberately a setup-time experiment, not a general renderer. A device
is promoted to trusted native geometry only after the operator runs a live sweep
and explicitly confirms the observed physical behavior.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from lighting_govee_config import (
    MAX_NATIVE_SEGMENTS,
    VERIFIED_SEGMENT_COUNTS,
    GoveeEnhancedDevice,
    make_calibrated_device,
)
from lighting_govee_orientation import save_visual_reversed
from lighting_govee_transport import GoveeRealtimeSession

CALIBRATION_FPS = 20
CALIBRATION_INTERVAL_MS = int(round(1000 / CALIBRATION_FPS))
CALIBRATION_ACTIVATION_WAIT_SECONDS = 0.100
BACKGROUND = (0, 0, 20)
MARKER = (255, 40, 20)


class GoveeCalibrationDialog(QDialog):
    """Physically test a candidate native frame length before trusting it."""

    def __init__(
        self,
        *,
        sku: str,
        ip: str,
        suggested_count: int | None = None,
        evidence_text: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.sku = str(sku).strip().upper()
        self.ip = str(ip).strip()
        self._session: GoveeRealtimeSession | None = None
        self._position = 0
        self._timer = QTimer(self)
        self._timer.setInterval(CALIBRATION_INTERVAL_MS)
        self._timer.timeout.connect(self._step)
        self._accepted_device: GoveeEnhancedDevice | None = None

        verified = VERIFIED_SEGMENT_COUNTS.get(self.sku)
        initial = suggested_count or verified or 20
        initial = max(1, min(MAX_NATIVE_SEGMENTS, int(initial)))

        self.setWindowTitle(f"EXPERIMENTAL — Calibrate Govee {self.sku}")
        self.setMinimumWidth(700)

        root = QVBoxLayout(self)
        title = QLabel(f"EXPERIMENTAL — native-zone calibration — {self.sku}")
        title.setStyleSheet("font-size:17px; font-weight:700; color:#E7C27D;")
        root.addWidget(title)

        experimental = QLabel(
            "Experimental feature: this model does not have a verified EDL native layout. "
            "Use this only if you are willing to physically test the light and reject the result if the movement is not correct."
        )
        experimental.setWordWrap(True)
        experimental.setStyleSheet("color:#E7C27D; font-weight:600;")
        root.addWidget(experimental)

        explanation = QLabel(
            "EDL found this Govee on your network, but its native realtime section layout is not yet trusted for this device. "
            "The test below sends a moving coloured marker using a candidate number of positions. Nothing is saved unless you confirm what the real light did."
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        warning = QLabel(
            "Before testing: Govee Desktop may stay open if you use it; it does not need to be open. "
            "In Govee Desktop → Razer Chroma Connect, deselect this same device and make sure LAN Control is enabled. "
            "This prevents Govee Desktop and EDL from both trying to control the same light at the same time."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#E7C27D;")
        root.addWidget(warning)

        form = QFormLayout()
        self.count = QSpinBox()
        self.count.setRange(1, MAX_NATIVE_SEGMENTS)
        self.count.setValue(initial)
        self.count.setToolTip(
            "How many independent entries EDL should send in one native realtime frame. "
            "If the sweep does not cover the light correctly, stop it, change this number and try again."
        )
        form.addRow("Candidate positions", self.count)

        if verified is not None:
            source = f"EDL has a verified model-wide layout of {verified} positions for {self.sku}."
        elif evidence_text:
            source = evidence_text
        elif suggested_count is not None:
            source = (
                f"A non-verified source suggested {suggested_count} positions. Treat this only as a starting point until your physical test succeeds."
            )
        else:
            source = (
                "EDL has no verified model-wide layout or trusted count for this SKU. "
                "The starting number below is only a convenient value to edit before testing; it is not evidence."
            )
        self.source_label = QLabel(source)
        self.source_label.setWordWrap(True)
        form.addRow("Starting evidence", self.source_label)

        self.first_position_end = QComboBox()
        self.first_position_end.addItem("Choose after you run the test", None)
        self.first_position_end.addItem("Left end of the installed light", "left")
        self.first_position_end.addItem("Right end of the installed light", "right")
        self.first_position_end.setToolTip(
            "When the test starts, native position 1 is shown first. Tell EDL which physical end of your installed light that first marker appeared on. "
            "EDL uses this only to make the later zone editor read naturally from left to right."
        )
        self.first_position_end.currentIndexChanged.connect(self._update_accept_state)
        form.addRow("Position 1 appeared at", self.first_position_end)
        root.addLayout(form)

        test_help = QLabel(
            "What to look for: when the test starts, note which physical end lights first. The coloured marker should then move in clear steps across the whole installed light. "
            "If it stops early, leaves part of the light unreachable, produces unstable colours, or jumps unpredictably, stop the test and do not accept that count."
        )
        test_help.setWordWrap(True)
        root.addWidget(test_help)

        controls = QHBoxLayout()
        self.test_button = QPushButton("Start moving-marker test")
        self.test_button.setToolTip(
            "Temporarily take direct control of this Govee and move one coloured position through the candidate native frame. Native position 1 is shown first."
        )
        self.test_button.clicked.connect(self._toggle_test)
        controls.addWidget(self.test_button)
        self.status = QLabel("Not testing")
        self.status.setStyleSheet("color:#999999;")
        controls.addWidget(self.status, 1)
        root.addLayout(controls)

        self.coverage_ok = QCheckBox("The marker reached the whole physical light")
        self.coverage_ok.setToolTip(
            "Confirm only if the moving marker traversed every part of the physical light you expect to control."
        )
        self.order_ok = QCheckBox("The movement was stable and positionally ordered")
        self.order_ok.setToolTip(
            "Confirm only if the marker moved consistently instead of flashing unrelated colours or jumping unpredictably."
        )
        self.coverage_ok.toggled.connect(self._update_accept_state)
        self.order_ok.toggled.connect(self._update_accept_state)
        root.addWidget(self.coverage_ok)
        root.addWidget(self.order_ok)

        note = QLabel(
            "Accepting this calibration trusts the tested position count for this physical device only. "
            "It does not claim that every device with the same model number has been verified by EDL."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#999999; font-size:12px;")
        root.addWidget(note)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.accept_button = QPushButton("Accept calibrated layout")
        self.accept_button.setEnabled(False)
        self.accept_button.setToolTip(
            "Save this position count and installation direction as physically calibrated geometry for this specific Govee device."
        )
        self.accept_button.clicked.connect(self._accept_calibration)
        actions.addWidget(self.accept_button)
        cancel = QPushButton("Cancel")
        cancel.setToolTip("Close without trusting or saving this experimental geometry.")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        root.addLayout(actions)

    @property
    def calibrated_device(self) -> GoveeEnhancedDevice | None:
        return self._accepted_device

    def _frame(self) -> tuple[tuple[int, int, int], ...]:
        count = self.count.value()
        marker_index = self._position % count
        return tuple(
            MARKER if index == marker_index else BACKGROUND
            for index in range(count)
        )

    def _step(self) -> None:
        session = self._session
        if session is None:
            return
        try:
            session.render_segments(self._frame())
        except Exception as exc:
            self._stop_test()
            QMessageBox.warning(self, "Calibration test stopped", str(exc))
            return
        self._position = (self._position + 1) % self.count.value()

    def _toggle_test(self) -> None:
        if self._session is not None:
            self._stop_test()
            return
        self.coverage_ok.setChecked(False)
        self.order_ok.setChecked(False)
        self.first_position_end.setCurrentIndex(0)
        self.count.setEnabled(False)
        try:
            session = GoveeRealtimeSession(self.ip)
            session.start()
            time.sleep(CALIBRATION_ACTIVATION_WAIT_SECONDS)
            self._session = session
            self._position = 0
            self._step()
            self._timer.start()
        except Exception as exc:
            try:
                session.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            self.count.setEnabled(True)
            QMessageBox.warning(self, "Cannot start Govee calibration", str(exc))
            return
        self.test_button.setText("Stop moving-marker test")
        self.status.setText(
            f"Testing {self.count.value()} candidate positions at {CALIBRATION_FPS} FPS — note which end lights first"
        )

    def _stop_test(self) -> None:
        self._timer.stop()
        session = self._session
        self._session = None
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                self.status.setText(f"Release warning: {exc}")
                self.count.setEnabled(True)
                self._update_accept_state()
                return
        self.count.setEnabled(True)
        self.test_button.setText("Start moving-marker test")
        self.status.setText("Test stopped; direct control released")
        self._update_accept_state()

    def _update_accept_state(self, *_args) -> None:
        self.accept_button.setEnabled(
            self._session is None
            and self.coverage_ok.isChecked()
            and self.order_ok.isChecked()
            and self.first_position_end.currentData() in {"left", "right"}
        )

    def _accept_calibration(self) -> None:
        if not self.accept_button.isEnabled():
            return
        self._accepted_device = make_calibrated_device(
            sku=self.sku,
            ip=self.ip,
            segment_count=self.count.value(),
            geometry_note=(
                "Operator confirmed moving-marker coverage and stable ordered motion "
                f"using {self.count.value()} native entries"
            ),
        )
        save_visual_reversed(
            self._accepted_device.device_id,
            self.first_position_end.currentData() == "right",
        )
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        if self._session is not None:
            self._stop_test()
        super().done(result)
