#!/usr/bin/env python3
"""Outer desktop persistence boundary for native profiles plus EDL workspaces.

The native profile parser remains strict v1-v6. The earlier mode prototype
briefly tolerated an extra top-level ``modes`` field inside that native document;
this adapter removes that compatibility shim and handles the outer workspace
envelope explicitly.

It is also the first boundary that sees both applied Rules and Scripted modes,
so profile/workspace replacement is guarded here without changing either
persistence format. Plain native profiles still travel through the accepted
load wrapper stack unchanged after the replacement decision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QMessageBox

import lighting_profile_modes as profile_modes
import lighting_profiles as profile_store
from lighting_ui_presenter import is_virpil_link_profile_path
from lighting_profile_modes import is_workspace_document


def _unresolved_mode_draft(window) -> bool:
    surface = getattr(window, "_covas_modes_workspace", None)
    return bool(surface is not None and getattr(surface, "_mode_draft_dirty", False))


def _unapplied_editor_changes(window) -> bool:
    return bool(
        getattr(window, "_editor_dirty", False)
        or getattr(window, "_new_rule_draft", False)
        or _unresolved_mode_draft(window)
    )


def _can_replace_applied_workspace(window) -> bool:
    """Protect applied unsaved data without conflating it with editor drafts."""
    if _unapplied_editor_changes(window):
        window.statusBar().showMessage(
            "Apply or cancel the current Rule or Scripted mode changes before replacing this profile.",
            6500,
        )
        return False

    if not bool(getattr(window, "_profile_dirty", False)):
        return True

    answer = QMessageBox.question(
        window,
        "Replace current profile?",
        "The current profile has unsaved applied changes. Save them before replacing it?",
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Save,
    )
    if answer == QMessageBox.StandardButton.Cancel:
        return False
    if answer == QMessageBox.StandardButton.Discard:
        return True

    window.save_profile_current()
    # A failed Save, or a cancelled Save As, leaves the dirty marker set and
    # therefore vetoes replacement.
    return not bool(getattr(window, "_profile_dirty", False))


def _install_workspace(window, path: Path, profile, modes) -> None:
    """Install already-validated workspace data using native load UI semantics."""
    if hasattr(window, "_virpil_import"):
        window._virpil_import = None

    window._profile = profile
    window._profile_path = path
    window._profile_dirty = False
    window.profile_name.setText(profile.name)
    window.save_as_button.setEnabled(True)

    surface = getattr(window, "_covas_modes_workspace", None)
    if surface is not None:
        surface.set_library(modes)

    logger = getattr(window, "_logger", None)
    if logger is not None:
        logger.event(
            "PROFILE_LOAD",
            path=path,
            profile=profile.name,
            rules=len(profile.rules),
            workspace=True,
            modes=len(modes.modes),
        )

    window._refresh_table()
    if profile.rules:
        window._select_rows((0,))
    else:
        window._clear_editor()
    window._update_profile_buttons()


def apply_workspace_ui(ui_module: Any) -> None:
    """Install explicit workspace loading after the complete mode adapter stack."""
    window_class = ui_module.MainWindow
    if getattr(window_class, "_edl_workspace_ui_applied", False):
        return

    # Restore the canonical strict parser that the first mode prototype wrapped.
    # ``lighting_profile_modes`` captured this function before apply_mode_ui ran.
    profile_store.profile_from_dict = profile_modes.profile_from_dict

    previous_load_profile = window_class.load_profile

    def load_profile(self, path) -> None:
        candidate = Path(path)
        if is_virpil_link_profile_path(candidate):
            previous_load_profile(self, candidate)
            return

        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "Open profile failed", str(exc))
            return

        if not is_workspace_document(raw):
            try:
                # Validate first. Do not ask the operator to save/discard the
                # current applied workspace for a replacement that cannot load.
                profile_modes.profile_modes_from_dict(raw)
            except Exception as exc:
                QMessageBox.critical(self, "Open profile failed", str(exc))
                return
            if not _can_replace_applied_workspace(self):
                return
            previous_load_profile(self, candidate)
            return

        try:
            profile, modes = profile_modes.profile_modes_from_dict(raw)
        except Exception as exc:
            QMessageBox.critical(self, "Open workspace failed", str(exc))
            return

        if not _can_replace_applied_workspace(self):
            return
        _install_workspace(self, candidate, profile, modes)

    previous_import_virpil = getattr(window_class, "import_virpil_profile", None)

    def import_virpil_profile(self, path) -> None:
        if previous_import_virpil is None:
            return
        if not _can_replace_applied_workspace(self):
            return
        previous_import_virpil(self, path)

    window_class.load_profile = load_profile
    if previous_import_virpil is not None:
        window_class.import_virpil_profile = import_virpil_profile
    window_class._edl_can_replace_applied_workspace = _can_replace_applied_workspace
    window_class._edl_workspace_ui_applied = True


# Co-located profile startup preference at the workspace/profile UI boundary.
AUTO_LOAD_LAST_PROFILE_KEY = "profiles/load_last_at_startup"
LAST_NATIVE_PROFILE_KEY = "profiles/last_native_profile"


def configured_auto_load(settings) -> bool:
    """Return the persisted opt-in without depending on QSettings type coercion."""
    value = settings.value(AUTO_LOAD_LAST_PROFILE_KEY, False)
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def configured_last_profile(settings) -> Path | None:
    value = settings.value(LAST_NATIVE_PROFILE_KEY, "")
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value.strip()).expanduser()


def _normalized_path(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _same_path(left: Path, right: Path) -> bool:
    return _normalized_path(left) == _normalized_path(right)


def remember_native_profile(settings, path: Path) -> Path:
    remembered = _normalized_path(path)
    settings.setValue(LAST_NATIVE_PROFILE_KEY, str(remembered))
    settings.sync()
    return remembered


def _profile_header_row(window) -> QHBoxLayout | None:
    central = window.centralWidget()
    outer = central.layout() if central is not None else None
    if outer is None or not outer.count():
        return None
    header = outer.itemAt(0).layout()
    profile_row = getattr(header, "profile_row", None)
    if isinstance(profile_row, QHBoxLayout):
        return profile_row
    return header if isinstance(header, QHBoxLayout) else None


def apply_profile_startup_ui(app_module: Any) -> None:
    """Add the last-profile startup preference after the complete product UI."""

    window_class = app_module.MainWindow
    if getattr(window_class, "_edl_profile_startup_ui_applied", False):
        return

    previous_init = window_class.__init__
    previous_load_profile = window_class.load_profile
    previous_write_profile = window_class._write_profile

    def remember_if_current(self, path: Path) -> bool:
        current = getattr(self, "_profile_path", None)
        if current is None or getattr(self, "_profile", None) is None:
            return False
        if not _same_path(Path(current), Path(path)):
            return False
        remember_native_profile(self._settings, Path(current))
        return True

    def load_profile(self, path: Path) -> None:
        candidate = Path(path)
        previous_load_profile(self, candidate)
        remember_if_current(self, candidate)

    def write_profile(self, path: Path) -> None:
        candidate = Path(path)
        previous_write_profile(self, candidate)
        remember_if_current(self, candidate)

    def set_auto_load_last_profile(self, enabled: bool) -> None:
        self._settings.setValue(AUTO_LOAD_LAST_PROFILE_KEY, bool(enabled))
        self._settings.sync()
        self.statusBar().showMessage(
            "Last profile will load automatically at startup."
            if enabled
            else "Automatic last-profile loading disabled.",
            4000,
        )

    def auto_load_last_profile(self) -> None:
        # An explicit command-line profile is loaded after window construction but
        # before this zero-delay callback runs. Never replace an already loaded
        # profile with the remembered one.
        if getattr(self, "_profile", None) is not None:
            return
        if not configured_auto_load(self._settings):
            return

        path = configured_last_profile(self._settings)
        if path is None:
            self.statusBar().showMessage(
                "Load last profile is enabled, but no native EDL profile has been remembered yet.",
                6500,
            )
            return
        if not path.is_file():
            self._logger.event("PROFILE_AUTOLOAD_MISSING", path=path)
            self.statusBar().showMessage(
                f"Last profile could not be found: {path}",
                8000,
            )
            return

        self.load_profile(path)
        current = getattr(self, "_profile_path", None)
        if current is not None and _same_path(Path(current), path):
            self._logger.event("PROFILE_AUTOLOAD", path=path)
            self.statusBar().showMessage(
                f"Loaded last profile: {path.name}",
                5000,
            )
        else:
            self._logger.event("PROFILE_AUTOLOAD_FAILED", path=path)
            self.statusBar().showMessage(
                f"Last profile could not be loaded: {path}",
                8000,
            )

    def init(self, *args, **kwargs) -> None:
        previous_init(self, *args, **kwargs)

        check = QCheckBox("Load last at startup", self)
        check.setToolTip(
            "When EDL starts, reopen the last successfully loaded or saved native EDL profile. "
            "This does not start lighting automatically."
        )
        check.setToolTipDuration(30000)
        check.setChecked(configured_auto_load(self._settings))
        check.toggled.connect(self._set_auto_load_last_profile)
        self.load_last_profile_check = check

        row = _profile_header_row(self)
        if row is None:
            check.hide()
        else:
            profile_name = getattr(self, "profile_name", None)
            index = row.indexOf(profile_name) if profile_name is not None else -1
            if index >= 0:
                row.insertWidget(index + 1, check)
            else:
                row.addWidget(check)

        QTimer.singleShot(0, self._auto_load_last_profile)

    window_class.__init__ = init
    window_class.load_profile = load_profile
    window_class._write_profile = write_profile
    window_class._remember_profile_if_current = remember_if_current
    window_class._set_auto_load_last_profile = set_auto_load_last_profile
    window_class._auto_load_last_profile = auto_load_last_profile
    window_class._edl_profile_startup_ui_applied = True
