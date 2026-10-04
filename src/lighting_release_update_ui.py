#!/usr/bin/env python3
"""Passive stable-release awareness for the EDL desktop application.

The pure release parser remains in lighting_release_update.py. This module owns
only the non-critical network fetch and the startup update-notification popup.

V1 contract:
- check once per application launch after the main UI is usable;
- published stable GitHub Releases only;
- short timeout and silent offline/API/metadata failure;
- no telemetry or machine/profile/device data;
- no self-update;
- a newer stable version shows one Download / Not now popup and Download opens
  the official release page.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

from lighting_release_update import UpdateAssessment, assess_github_release
from lighting_version import EDL_VERSION


LATEST_RELEASE_API_URL = (
    "https://api.github.com/repos/DocTrintignant/"
    "elite-dangerous-lighting/releases/latest"
)
OFFICIAL_RELEASE_URL_PREFIX = (
    "https://github.com/DocTrintignant/"
    "elite-dangerous-lighting/releases/"
)
NETWORK_TIMEOUT_SECONDS = 2.5
MAX_RELEASE_RESPONSE_BYTES = 512 * 1024
INITIAL_CHECK_DELAY_MS = 3000
BUSY_RETRY_DELAY_MS = 1000
DISABLE_UPDATE_CHECK_ENV = "EDL_DISABLE_UPDATE_CHECK"


def update_check_disabled() -> bool:
    value = os.environ.get(DISABLE_UPDATE_CHECK_ENV, "")
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def fetch_latest_release_assessment(
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout_seconds: float = NETWORK_TIMEOUT_SECONDS,
) -> UpdateAssessment:
    """Fetch and assess GitHub's latest published release.

    Transport errors intentionally propagate to the caller. The background UI
    boundary catches them silently because update awareness is non-critical.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    request = urllib.request.Request(
        LATEST_RELEASE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Elite-Dangerous-Lighting/{EDL_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    with opener(request, timeout=timeout_seconds) as response:
        raw = response.read(MAX_RELEASE_RESPONSE_BYTES + 1)

    if len(raw) > MAX_RELEASE_RESPONSE_BYTES:
        raise ValueError("GitHub Release response exceeded the bounded V1 size")
    payload = json.loads(raw.decode("utf-8"))
    assessment = assess_github_release(payload)

    release = assessment.release
    if release is not None and not release.html_url.startswith(
        OFFICIAL_RELEASE_URL_PREFIX
    ):
        raise ValueError("GitHub Release URL is outside the official EDL repository")
    return assessment


class _UpdateSignals(QObject):
    assessed = Signal(object)


def apply_release_update_notification(ui_module: Any) -> None:
    """Install one non-blocking stable-release check on the product window."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_release_update_applied", False):
        return

    previous_init = window_class.__init__

    def startup_surface_busy(self) -> bool:
        if not self.isVisible():
            return True
        overlay = getattr(self, "_guided_tour_overlay", None)
        if overlay is not None and getattr(overlay, "isVisible", lambda: False)():
            return True
        modal = QApplication.activeModalWidget()
        update_dialog = getattr(self, "_edl_update_dialog", None)
        return modal is not None and modal is not update_dialog

    def present_update(self, assessment: object) -> None:
        if not isinstance(assessment, UpdateAssessment):
            return
        if not assessment.update_available or assessment.release is None:
            return
        if getattr(self, "_edl_update_notified", False):
            return

        # The result may arrive while the operator is still inside a startup
        # chooser or another modal. Keep the result and show it immediately
        # after that startup surface has cleared instead of stacking dialogs.
        if self._edl_update_startup_surface_busy():
            QTimer.singleShot(
                BUSY_RETRY_DELAY_MS,
                lambda result=assessment: self._edl_present_release_update(result),
            )
            return

        release = assessment.release
        self._edl_update_notified = True
        self._edl_update_release_url = release.html_url

        box = QMessageBox(self)
        box.setWindowTitle("EDL update available")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("A newer version of Elite Dangerous Lighting is available.")
        box.setInformativeText(
            f"Installed: {assessment.current_version}\n"
            f"Latest: {release.version}\n\n"
            "Download opens the official GitHub release page."
        )
        download = box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        not_now = box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(download)
        box.setEscapeButton(not_now)

        self._edl_update_dialog = box
        self._edl_update_download_button = download
        self._edl_update_not_now_button = not_now

        def clicked(button) -> None:
            if button is download:
                self._edl_open_update_release()

        def finished(_result: int) -> None:
            self._edl_update_dialog = None

        box.buttonClicked.connect(clicked)
        box.finished.connect(finished)
        box.open()
        box.raise_()
        box.activateWindow()

    def open_release(self) -> None:
        url = getattr(self, "_edl_update_release_url", None)
        if not isinstance(url, str) or not url:
            return
        QDesktopServices.openUrl(QUrl(url))

    def start_check(self) -> None:
        if update_check_disabled():
            return
        if getattr(self, "_edl_update_check_started", False):
            return

        # Do not compete with first-run onboarding or another startup modal.
        if self._edl_update_startup_surface_busy():
            QTimer.singleShot(BUSY_RETRY_DELAY_MS, self._edl_start_update_check)
            return

        self._edl_update_check_started = True

        def worker() -> None:
            try:
                assessment = fetch_latest_release_assessment()
            except Exception:
                # Offline, API, TLS, malformed metadata and GitHub 404 (no
                # published release yet) are deliberately silent in V1.
                return
            self._edl_update_signals.assessed.emit(assessment)

        thread = threading.Thread(
            target=worker,
            name="edl-release-update-check",
            daemon=True,
        )
        self._edl_update_thread = thread
        thread.start()

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)
        self._edl_update_check_started = False
        self._edl_update_notified = False
        self._edl_update_release_url = None
        self._edl_update_thread = None
        self._edl_update_dialog = None
        self._edl_update_download_button = None
        self._edl_update_not_now_button = None

        signals = _UpdateSignals(self)
        signals.assessed.connect(self._edl_present_release_update)
        self._edl_update_signals = signals

        if not update_check_disabled():
            QTimer.singleShot(INITIAL_CHECK_DELAY_MS, self._edl_start_update_check)

    window_class.__init__ = init
    window_class._edl_update_startup_surface_busy = startup_surface_busy
    window_class._edl_present_release_update = present_update
    window_class._edl_open_update_release = open_release
    window_class._edl_start_update_check = start_check
    window_class._edl_release_update_applied = True
