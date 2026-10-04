#!/usr/bin/env python3
"""One file-backed settings store for the active EDL runtime boundary."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from lighting_paths import settings_path


def app_settings() -> QSettings:
    """Return EDL settings from the active data root, creating Config as needed."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return QSettings(str(path), QSettings.Format.IniFormat)
