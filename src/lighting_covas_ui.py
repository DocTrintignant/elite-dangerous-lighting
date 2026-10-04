#!/usr/bin/env python3
"""Chromas Next plugin integration UI for Elite Dangerous Lighting.

This module owns only operator-facing plugin-path validation and lifecycle control
for the already-proven loopback status bridge. It does not import or control any
lighting renderer, rule engine, Elite state watcher, Chroma transport, or Govee
transport.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lighting_covas_bridge import (
    API_VERSION,
    CovasStatusBridge,
    default_plugin_install_path,
    validate_plugin_install,
)
from lighting_covas_mode_bridge import (
    MIN_CHROMAS_NEXT_RUNTIME_VERSION,
    MIN_COVASIFY_RUNTIME_VERSION,
    chromas_runtime_version_supported,
    covasify_runtime_version_supported,
    covas_runtime_admission,
    covas_runtime_snapshot,
)
from lighting_ui_tokens import DIALOG_TITLE_PT


PLUGIN_PATH_KEY = "covas/plugin_install_path"
COVAS_LOCAL_CONNECTION_NOTICE_KEY = "covas/local_connection_notice_v1"
COVAS_LOCAL_CONNECTION_NOTICE_TITLE = "Local COVAS:NEXT connection"
COVAS_LOCAL_CONNECTION_NOTICE_TEXT = (
    "EDL uses a local-only connection so the COVAS:NEXT plugin can communicate "
    "with EDL on this computer."
)
COVAS_LOCAL_CONNECTION_NOTICE_DETAIL = (
    "Windows may ask whether Elite Dangerous Lighting is allowed to communicate "
    "on the network when this connection starts.\n\n"
    "EDL's COVAS:NEXT listener is bound only to 127.0.0.1 (this PC), so it is "
    "reachable only through the local loopback interface. EDL does not bind this "
    "listener to your LAN or Internet-facing network interfaces."
)
COVASIFY_COMPATIBLE_SOURCE = (
    "https://github.com/DocTrintignant/covas-next-plugins/"
    "tree/main/Covasify"
)


def configured_plugin_path(settings: QSettings) -> Path:
    stored = settings.value(PLUGIN_PATH_KEY, "")
    if isinstance(stored, str) and stored.strip():
        return Path(stored.strip()).expanduser()
    return default_plugin_install_path()


def local_connection_notice_required(
    settings: QSettings,
    *,
    packaged: bool | None = None,
) -> bool:
    """Return whether a packaged first run still needs the loopback explanation."""
    is_packaged = bool(getattr(sys, "frozen", False)) if packaged is None else bool(packaged)
    if not is_packaged:
        return False
    return not settings.value(
        COVAS_LOCAL_CONNECTION_NOTICE_KEY,
        False,
        type=bool,
    )


def covas_next_program_running() -> bool | None:
    """Best-effort presence check for the COVAS:NEXT desktop application."""
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(
            [
                "tasklist",
                "/FI",
                "IMAGENAME eq COVAS NEXT.exe",
                "/FO",
                "CSV",
                "/NH",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return "covas next.exe" in result.stdout.lower()


def _covasify_status() -> tuple[str, str, str]:
    snapshot = covas_runtime_snapshot()
    covasify = snapshot["covasify"]
    if not covasify.get("fresh"):
        return (
            "Not connected",
            (
                f"Install Covasify {MIN_COVASIFY_RUNTIME_VERSION} or newer from "
                f"{COVASIFY_COMPATIBLE_SOURCE}, then restart COVAS:NEXT."
            ),
            "attention",
        )
    if not covasify_runtime_version_supported(covasify.get("plugin_version")):
        observed = str(covasify.get("plugin_version") or "unknown")
        return (
            f"Update required — v{observed}",
            (
                f"Install Covasify {MIN_COVASIFY_RUNTIME_VERSION} or newer from "
                f"{COVASIFY_COMPATIBLE_SOURCE}, then restart COVAS:NEXT."
            ),
            "attention",
        )
    if not covasify.get("media_listener_registered"):
        return (
            "EDL integration not ready",
            (
                f"Install Covasify {MIN_COVASIFY_RUNTIME_VERSION} or newer from "
                f"{COVASIFY_COMPATIBLE_SOURCE}, then restart COVAS:NEXT."
            ),
            "attention",
        )
    if covasify.get("spotify_connected"):
        return (
            f"Connected — v{covasify.get('plugin_version')}",
            "Covasify is connected and Spotify is ready.",
            "ok",
        )
    return (
        f"Connected — v{covasify.get('plugin_version')} · Spotify not ready",
        "Covasify is connected, but Spotify is not currently ready for playback.",
        "warning",
    )



def _connection_menu_state(
    settings: QSettings,
    bridge: CovasStatusBridge,
) -> tuple[str, str, str]:
    """Return (label, state, detail) for the always-on Setup menu health surface."""
    if not bridge.is_running:
        return (
            "COVAS:NEXT connection — Blocked",
            "blocked",
            "EDL could not start its local COVAS:NEXT connection listener.",
        )

    install = validate_plugin_install(configured_plugin_path(settings))
    if not install.valid:
        return (
            "COVAS:NEXT connection — Blocked",
            "blocked",
            f"Chromas Next plugin check failed: {install.message}",
        )
    if not chromas_runtime_version_supported(install.version):
        observed = install.version or "unknown"
        return (
            "COVAS:NEXT connection — Blocked",
            "blocked",
            (
                f"Installed Chromas Next plugin {observed} is too old. "
                f"Update to {MIN_CHROMAS_NEXT_RUNTIME_VERSION} or newer."
            ),
        )

    admission = covas_runtime_admission("run_mode")
    if not admission.ready:
        if admission.state.startswith("covasify_"):
            detail = _covasify_status()[1]
        elif admission.state == "chromas_runtime_missing":
            detail = "Start the COVAS:NEXT AI/session so Chromas Next can connect to EDL."
        else:
            detail = admission.message
        return (
            "COVAS:NEXT connection — Blocked",
            "blocked",
            detail,
        )

    snapshot = covas_runtime_snapshot()
    chromas = snapshot["chromas_next"]
    covasify = snapshot["covasify"]
    if chromas.get("covasify_bridge_enabled") and not covasify.get("spotify_connected"):
        return (
            "COVAS:NEXT connection — Connected",
            "connected",
            (
                "Chromas Next and Covasify handshakes are healthy. "
                "Spotify is not currently connected."
            ),
        )
    return (
        "COVAS:NEXT connection — Connected",
        "connected",
        admission.message,
    )


def _match_button_widths(*buttons) -> None:
    """Give corresponding header controls the same width without rebuilding rows."""
    actual = [button for button in buttons if isinstance(button, QPushButton)]
    if not actual:
        return
    width = max(button.sizeHint().width() for button in actual)
    for button in actual:
        button.setFixedWidth(width)


def _align_header_pairs(window) -> None:
    """Align the three right-side profile/setup columns across both header rows."""
    header = None
    central = window.centralWidget()
    outer = central.layout() if central is not None else None
    if outer is not None and outer.count():
        header = outer.itemAt(0).layout()

    govee = getattr(header, "govee_button", None) if header is not None else None
    chroma = getattr(window, "chroma_zone_setup_button", None)
    covas = getattr(window, "_covas_button", None)
    save = getattr(window, "save_button", None)
    save_as = getattr(window, "save_as_button", None)
    virpil = getattr(window, "import_virpil_button", None)

    _match_button_widths(save, govee)
    _match_button_widths(save_as, chroma)
    _match_button_widths(virpil, covas)


class CovasIntegrationDialog(QDialog):
    """Show Chromas Next plugin location and live integration status."""

    def __init__(
        self,
        settings: QSettings,
        bridge: CovasStatusBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._bridge = bridge

        self.setWindowTitle("COVAS:NEXT connection")
        self.setModal(False)
        self.setMinimumWidth(720)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        heading = QLabel("COVAS:NEXT connection")
        heading.setObjectName("covasDialogTitle")
        heading_font = heading.font()
        heading_font.setPointSizeF(DIALOG_TITLE_PT)
        heading_font.setWeight(QFont.Weight.DemiBold)
        heading.setFont(heading_font)
        root.addWidget(heading)

        explanation = QLabel(
            "EDL checks COVAS:NEXT and its lighting plugins automatically."
        )
        explanation.setObjectName("covasDialogIntro")
        explanation.setWordWrap(True)
        explanation.setProperty("edlTextRole", "secondary")
        root.addWidget(explanation)

        # Keep the configurable path as state, but do not expose filesystem detail
        # unless the operator chooses Locate.
        self.plugin_path = QLineEdit(str(configured_plugin_path(settings)))
        self.plugin_path.hide()
        self.plugin_path.editingFinished.connect(self._path_edited)

        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(12)
        status_grid.setVerticalSpacing(10)

        status_grid.addWidget(QLabel("COVAS:NEXT"), 0, 0)
        self.covas_program_status = QLabel("Checking…")
        self.covas_program_status.setObjectName("covasProgramStatus")
        self.covas_program_status.setStyleSheet("font-weight:600;")
        self.covas_program_status.setToolTipDuration(30000)
        status_grid.addWidget(self.covas_program_status, 0, 1)

        status_grid.addWidget(QLabel("Chromas Next"), 1, 0)
        self.install_status = QLabel("")
        self.install_status.setObjectName("covasPluginStatus")
        self.install_status.setWordWrap(True)
        self.install_status.setStyleSheet("font-weight:600;")
        status_grid.addWidget(self.install_status, 1, 1)
        self.plugin_version = QLabel("—")
        self.plugin_version.hide()

        browse = QPushButton("Locate…")
        browse.setToolTip("Choose the installed Chromas Next plugin folder.")
        browse.clicked.connect(self._browse_plugin_folder)
        status_grid.addWidget(browse, 1, 2)

        status_grid.addWidget(QLabel("Covasify bridge"), 2, 0)
        self.covasify_bridge_status = QLabel("")
        self.covasify_bridge_status.setObjectName("covasCovasifyBridgeStatus")
        self.covasify_bridge_status.setStyleSheet("font-weight:600;")
        self.covasify_bridge_status.setToolTipDuration(30000)
        status_grid.addWidget(self.covasify_bridge_status, 2, 1, 1, 2)

        self.covasify_label = QLabel("Covasify")
        self.covasify_runtime_status = QLabel("")
        self.covasify_runtime_status.setObjectName("covasCovasifyRuntimeStatus")
        self.covasify_runtime_status.setStyleSheet("font-weight:600;")
        self.covasify_runtime_status.setToolTipDuration(30000)
        status_grid.addWidget(self.covasify_label, 3, 0)
        status_grid.addWidget(self.covasify_runtime_status, 3, 1, 1, 2)

        status_grid.setColumnStretch(1, 1)
        root.addLayout(status_grid)

        external_note = QLabel("Checks update automatically.")
        external_note.setObjectName("covasExternalIntegrationNote")
        external_note.setProperty("edlTextRole", "secondary")
        root.addWidget(external_note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self._runtime_timer = QTimer(self)
        self._runtime_timer.setInterval(3000)
        self._runtime_timer.timeout.connect(self.refresh_external_integration_status)
        self._runtime_timer.start()

        self.refresh_installation_status()
        self.refresh_external_integration_status()

    def _browse_plugin_folder(self) -> None:
        start = self.plugin_path.text().strip() or str(default_plugin_install_path())
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select Chromas Next plugin folder",
            start,
        )
        if not selected:
            return
        self.plugin_path.setText(selected)
        self._path_edited()

    def _path_edited(self) -> None:
        path = self.plugin_path.text().strip()
        if path:
            self._settings.setValue(PLUGIN_PATH_KEY, path)
            self._settings.sync()
        self.refresh_installation_status()

    def refresh_installation_status(self, *, explicit: bool = False):
        raw_path = self.plugin_path.text().strip()
        if not raw_path:
            raw_path = str(default_plugin_install_path())
            self.plugin_path.setText(raw_path)

        result = validate_plugin_install(raw_path)
        if result.valid:
            normalized = str(result.path)
            if self.plugin_path.text() != normalized:
                self.plugin_path.setText(normalized)
            self._settings.setValue(PLUGIN_PATH_KEY, normalized)
            version = result.version or "Unknown"
            self.plugin_version.setText(version)
            if chromas_runtime_version_supported(result.version):
                self.install_status.setText(f"Found — v{version}")
                self.install_status.setToolTip("Chromas Next plugin files were found.")
                state = "ok"
            else:
                self.install_status.setText(
                    f"Update required — v{version} (need {MIN_CHROMAS_NEXT_RUNTIME_VERSION}+)"
                )
                self.install_status.setToolTip(
                    f"Install Chromas Next {MIN_CHROMAS_NEXT_RUNTIME_VERSION} or newer."
                )
                state = "attention"
        else:
            self.plugin_version.setText("—")
            self.install_status.setText("Not found")
            self.install_status.setToolTip(
                f"{result.message} Use Locate… if Chromas Next is installed elsewhere."
            )
            state = "attention"

        self.install_status.setProperty("edlState", state)
        style = self.install_status.style()
        style.unpolish(self.install_status)
        style.polish(self.install_status)
        self.install_status.update()
        self._settings.sync()
        return result


    def refresh_external_integration_status(self) -> None:
        running = covas_next_program_running()
        if running is True:
            program_text = "Running"
            program_detail = "COVAS:NEXT is running."
            program_state = "ok"
        elif running is False:
            program_text = "Not running"
            program_detail = "Start COVAS:NEXT."
            program_state = "attention"
        else:
            program_text = "Unknown"
            program_detail = "EDL could not determine whether COVAS:NEXT is running."
            program_state = ""

        self.covas_program_status.setText(program_text)
        self.covas_program_status.setToolTip(program_detail)
        self.covas_program_status.setProperty("edlState", program_state)

        install = validate_plugin_install(configured_plugin_path(self._settings))
        snapshot = covas_runtime_snapshot()
        chromas = snapshot["chromas_next"]

        if not install.valid:
            plugin_text = "Not found"
            plugin_detail = (
                f"{install.message} Use Locate… if Chromas Next is installed elsewhere."
            )
            plugin_state = "attention"
        elif not chromas_runtime_version_supported(install.version):
            observed = install.version or "unknown"
            plugin_text = f"Update required — v{observed}"
            plugin_detail = (
                f"Install Chromas Next {MIN_CHROMAS_NEXT_RUNTIME_VERSION} or newer."
            )
            plugin_state = "attention"
        elif not self._bridge.is_running:
            plugin_text = f"Found — v{install.version or 'unknown'} · EDL connection unavailable"
            plugin_detail = "EDL could not start its local COVAS:NEXT listener."
            plugin_state = "attention"
        elif chromas.get("fresh"):
            plugin_text = f"Connected — v{chromas.get('plugin_version') or install.version or 'unknown'}"
            plugin_detail = "Chromas Next is loaded in COVAS:NEXT and connected to EDL."
            plugin_state = "ok"
        else:
            plugin_text = f"Found — v{install.version or 'unknown'} · waiting for COVAS:NEXT session"
            plugin_detail = (
                "Chromas Next is installed. Start the COVAS:NEXT AI/session so the plugin runtime can connect to EDL."
            )
            plugin_state = "attention"

        self.install_status.setText(plugin_text)
        self.install_status.setToolTip(plugin_detail)
        self.install_status.setProperty("edlState", plugin_state)

        if chromas.get("fresh"):
            bridge_on = bool(chromas.get("covasify_bridge_enabled"))
            if bridge_on:
                bridge_text = "On — Mode music enabled"
                bridge_detail = (
                    "Chromas Next will pass Mode music cues to Covasify."
                )
                bridge_state = "ok"
            else:
                bridge_text = "Off — Mode music disabled"
                bridge_detail = (
                    "To use Mode music, enable the Covasify bridge in "
                    "COVAS:NEXT → Plugins → Chromas Next."
                )
                bridge_state = "attention"
        else:
            bridge_text = "Unknown — Chromas Next not active"
            bridge_detail = (
                "The Covasify bridge setting is reported by the running Chromas Next plugin."
            )
            bridge_state = "attention"

        self.covasify_bridge_status.setText(bridge_text)
        self.covasify_bridge_status.setToolTip(bridge_detail)
        self.covasify_bridge_status.setProperty("edlState", bridge_state)

        show_covasify = bool(
            chromas.get("fresh") and chromas.get("covasify_bridge_enabled")
        )
        self.covasify_label.setVisible(show_covasify)
        self.covasify_runtime_status.setVisible(show_covasify)
        if show_covasify:
            covasify_text, covasify_detail, covasify_state = _covasify_status()
            self.covasify_runtime_status.setText(covasify_text)
            self.covasify_runtime_status.setToolTip(covasify_detail)
            self.covasify_runtime_status.setProperty("edlState", covasify_state)

        for label in (
            self.covas_program_status,
            self.install_status,
            self.covasify_bridge_status,
            self.covasify_runtime_status,
        ):
            style = label.style()
            style.unpolish(label)
            style.polish(label)
            label.update()


    def refresh_bridge_status(self) -> None:
        self.refresh_external_integration_status()


def apply_covas_integration(app_module) -> None:
    """Add Chromas Next configuration after the complete accepted product UI stack."""

    window_class = app_module.MainWindow
    if getattr(window_class, "_edl_covas_integration_applied", False):
        return

    previous_init = window_class.__init__
    previous_close_event = window_class.closeEvent

    def init(self, *args, **kwargs) -> None:
        # Construct the accepted product window first. The plugin UI is an
        # additive outer boundary and must never replace or short-circuit the
        # existing lighting/device UI stack.
        previous_init(self, *args, **kwargs)

        self._covas_bridge = CovasStatusBridge()
        self._covas_dialog = None
        self._covas_local_connection_notice = None
        self._covas_bridge_start_attempted = False
        self._covas_closing = False

        button = QPushButton("COVAS:NEXT connection", self)
        button.setToolTip(
            "Check COVAS:NEXT, Chromas Next, and the optional Covasify music bridge."
        )
        button.setToolTipDuration(30000)
        button.clicked.connect(self.open_covas_integration)
        self._covas_button = button

        # Chroma-zone setup is already the last accepted lighting-configuration
        # control. Append this external integration immediately after it so the
        # operator sees setup controls as one coherent group.
        chroma_button = getattr(self, "chroma_zone_setup_button", None)
        setup_layout = None
        if chroma_button is not None and chroma_button.parentWidget() is not None:
            candidate = chroma_button.parentWidget().layout()
            if isinstance(candidate, QHBoxLayout):
                setup_layout = candidate
        if setup_layout is not None:
            setup_layout.addWidget(button)
        else:
            central = self.centralWidget()
            outer = central.layout() if central is not None else None
            header = outer.itemAt(0).layout() if outer is not None and outer.count() else None
            runtime = getattr(header, "runtime_row", None)
            if runtime is not None:
                runtime.addWidget(button)
            else:
                # Do not fall back to the status bar: configuration controls must
                # remain discoverable with the other operator setup actions.
                button.hide()

        _align_header_pairs(self)

        self._covas_health_trace_state = None
        self._covas_health_timer = QTimer(self)
        self._covas_health_timer.setInterval(1000)
        self._covas_health_timer.timeout.connect(self._refresh_covas_connection_health)

        # The product startup flow starts this listener after the combined
        # first-start compatibility/COVAS explanation has been shown.

    def start_covas_bridge(self) -> None:
        if self._covas_bridge_start_attempted or self._covas_closing:
            return
        self._covas_bridge_start_attempted = True
        snapshot_method = getattr(self, "_edl_availability_snapshot", None)
        if callable(snapshot_method):
            try:
                self._covas_bridge.set_runtime_availability(snapshot_method())
            except Exception:
                self._covas_bridge.set_runtime_availability(None)
        try:
            self._covas_bridge.start()
        except OSError as exc:
            self.statusBar().showMessage(
                f"COVAS:NEXT connection listener could not start: {exc}",
                7000,
            )
        self._covas_health_timer.start()
        QTimer.singleShot(0, self._refresh_covas_connection_health)

    def covas_local_connection_notice_finished(self, _result: int) -> None:
        if self._covas_closing:
            return
        self._settings.setValue(COVAS_LOCAL_CONNECTION_NOTICE_KEY, True)
        self._settings.sync()
        notice = self._covas_local_connection_notice
        self._covas_local_connection_notice = None
        if notice is not None:
            notice.deleteLater()
        self._start_covas_bridge()

    def start_covas_bridge_or_notice(self) -> None:
        if self._covas_closing or self._covas_bridge_start_attempted:
            return
        if not local_connection_notice_required(self._settings):
            self._start_covas_bridge()
            return
        if self._covas_local_connection_notice is not None:
            return

        notice = QMessageBox(self)
        notice.setIcon(QMessageBox.Icon.Information)
        notice.setWindowTitle(COVAS_LOCAL_CONNECTION_NOTICE_TITLE)
        notice.setText(COVAS_LOCAL_CONNECTION_NOTICE_TEXT)
        notice.setInformativeText(COVAS_LOCAL_CONNECTION_NOTICE_DETAIL)
        continue_button = notice.addButton(
            "Continue",
            QMessageBox.ButtonRole.AcceptRole,
        )
        notice.setDefaultButton(continue_button)
        notice.finished.connect(self._covas_local_connection_notice_finished)
        self._covas_local_connection_notice = notice
        notice.open()

    def refresh_covas_connection_health(self) -> None:
        snapshot_method = getattr(self, "_edl_availability_snapshot", None)
        if callable(snapshot_method):
            try:
                self._covas_bridge.set_runtime_availability(snapshot_method())
            except Exception:
                self._covas_bridge.set_runtime_availability(None)

        label, state, detail = _connection_menu_state(
            self._settings,
            self._covas_bridge,
        )

        self._covas_button.setText(label)
        self._covas_button.setToolTip(detail)

        action = getattr(self, "header_covas_action", None)
        if action is not None:
            action.setText(label)
            action.setToolTip(detail)

        current = (state, detail)
        if current != self._covas_health_trace_state:
            previous = self._covas_health_trace_state
            self._covas_health_trace_state = current
            logger = getattr(self, "_logger", None)
            if logger is not None:
                logger.event(
                    "COVAS_HEALTH",
                    state=state,
                    detail=detail,
                    bridge_running=self._covas_bridge.is_running,
                    previous_state=previous[0] if previous else None,
                )

        dialog = self._covas_dialog
        if dialog is not None and dialog.isVisible():
            dialog.refresh_external_integration_status()

    def open_covas_integration(self) -> None:
        dialog = self._covas_dialog
        if dialog is None:
            dialog = CovasIntegrationDialog(
                self._settings,
                self._covas_bridge,
                self,
            )
            self._covas_dialog = dialog
        dialog.refresh_installation_status()
        dialog.refresh_external_integration_status()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._refresh_covas_connection_health()

    def close_event(self, event) -> None:
        # Preserve the accepted close semantics first. If another UI layer vetoes
        # the close (for example because unsaved work still needs attention), the
        # bridge remains available because EDL is still running.
        previous_close_event(self, event)
        if event.isAccepted():
            self._covas_closing = True
            notice = self._covas_local_connection_notice
            if notice is not None:
                notice.close()
                self._covas_local_connection_notice = None
            self._covas_health_timer.stop()
            self._covas_bridge.stop()

    window_class.__init__ = init
    window_class._start_covas_bridge = start_covas_bridge
    window_class._covas_local_connection_notice_finished = covas_local_connection_notice_finished
    window_class._start_covas_bridge_or_notice = start_covas_bridge_or_notice
    window_class._refresh_covas_connection_health = refresh_covas_connection_health
    window_class.open_covas_integration = open_covas_integration
    window_class.closeEvent = close_event
    window_class._edl_covas_integration_applied = True
