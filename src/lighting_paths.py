#!/usr/bin/env python3
"""Product-owned filesystem locations for development and distributed use."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

DATA_DIR_ENV = "ELITE_DANGEROUS_LIGHTING_DATA_DIR"
WINDOWS_APP_DIR = "Elite Dangerous Lighting"
FALLBACK_APP_DIR = ".elite-dangerous-lighting"
DEVELOPMENT_DATA_DIR = ".edl-dev-data"

CONFIG_DIR = "Config"
PROFILES_DIR = "Profiles"
LOGS_DIR = "Logs"
SETTINGS_FILE = "settings.ini"


def _runtime_is_frozen(frozen: bool | None = None) -> bool:
    if frozen is not None:
        return bool(frozen)
    return bool(getattr(sys, "frozen", False))


def development_data_dir(*, source_root: Path | None = None) -> Path:
    """Return the repository-local state root used by source/development runs."""
    root = Path(__file__).resolve().parents[1] if source_root is None else Path(source_root)
    return root / DEVELOPMENT_DATA_DIR


def user_data_dir(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    os_name: str | None = None,
    frozen: bool | None = None,
    source_root: Path | None = None,
) -> Path:
    """Return the active writable application-data root without touching disk.

    Source/development execution is deliberately isolated inside the repository.
    Frozen/distributed execution uses the normal per-user production location.
    An explicit environment override always wins so tests and diagnostics remain
    hermetic.
    """
    env = os.environ if environ is None else environ
    override = env.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()

    if not _runtime_is_frozen(frozen):
        return development_data_dir(source_root=source_root)

    platform_name = os.name if os_name is None else os_name
    if platform_name == "nt":
        local = env.get("LOCALAPPDATA")
        if local:
            return Path(local) / WINDOWS_APP_DIR

    base_home = Path.home() if home is None else Path(home)
    return base_home / FALLBACK_APP_DIR


def config_dir(**kwargs) -> Path:
    return user_data_dir(**kwargs) / CONFIG_DIR


def profiles_dir(**kwargs) -> Path:
    return user_data_dir(**kwargs) / PROFILES_DIR


def settings_path(**kwargs) -> Path:
    return config_dir(**kwargs) / SETTINGS_FILE


def log_dir(**kwargs) -> Path:
    return user_data_dir(**kwargs) / LOGS_DIR
