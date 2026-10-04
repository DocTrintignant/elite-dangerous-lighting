#!/usr/bin/env python3
"""Non-invasive first-run environment inspection.

No Chroma session is created here and no HID device is opened.  This module only
reports prerequisites/discovery so product startup failures are easier to
understand without inventing a transport-readiness signal.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from elite_status_watch import default_status_path
from lighting_paths import user_data_dir


@dataclass(frozen=True)
class EnvironmentReport:
    os_name: str
    platform: str
    python: str
    pyside6_available: bool
    user_data_dir: Path
    status_path: Path | None
    status_exists: bool

    @property
    def windows(self) -> bool:
        return self.os_name == "nt"

    def ui_blockers(self) -> tuple[str, ...]:
        blockers = []
        if not self.pyside6_available:
            blockers.append("PySide6 is not installed")
        return tuple(blockers)

    def live_hardware_notes(self) -> tuple[str, ...]:
        notes = []
        if not self.windows:
            notes.append("Physical HID/Chroma runtime requires Windows")
        if self.status_path is None:
            notes.append("Elite Status.json was not auto-discovered")
        elif not self.status_exists:
            notes.append(f"Elite Status.json is not present at {self.status_path}")
        return tuple(notes)


def inspect_environment() -> EnvironmentReport:
    status = default_status_path()
    return EnvironmentReport(
        os_name=os.name,
        platform=platform.platform(),
        python=sys.version.split()[0],
        pyside6_available=importlib.util.find_spec("PySide6") is not None,
        user_data_dir=user_data_dir(),
        status_path=status,
        status_exists=bool(status is not None and status.exists()),
    )
