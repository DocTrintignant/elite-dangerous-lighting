#!/usr/bin/env python3
"""UI-facing live Elite Status diagnostics using the canonical watcher only.

This module deliberately owns no Status semantics. ``EliteStatusWatcher`` and
``elite_status.decode_status`` remain the sole state authority. The adapter only
keeps the newest complete canonical snapshot and formats values for operator
presentation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from elite_status import FLAG2_BITS, FLAG_BITS, SCALAR_KEYS
from elite_status_watch import EliteStatusWatcher, StatusUpdate, default_status_path

CANONICAL_DIAGNOSTIC_KEYS: tuple[str, ...] = (
    "RawFlags",
    "RawFlags2",
    *FLAG_BITS.keys(),
    *FLAG2_BITS.keys(),
    *SCALAR_KEYS,
)


def format_live_value(value: Any) -> str:
    """Compact, lossless-enough operator display without inventing semantics."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


class EliteStatusDiagnostics:
    """One UI observer over the accepted canonical Status watcher."""

    def __init__(self, status_path: Path | str | None = None) -> None:
        resolved = Path(status_path) if status_path is not None else default_status_path()
        self.status_path: Path | None = resolved
        self._watcher = EliteStatusWatcher(resolved) if resolved is not None else None
        self._state: dict[str, Any] = {}

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    @property
    def configured(self) -> bool:
        return self._watcher is not None and self.status_path is not None

    def set_status_path(self, status_path: Path | str) -> None:
        path = Path(status_path)
        self.status_path = path
        self._watcher = EliteStatusWatcher(path)
        self._state = {}

    def poll_once(self) -> StatusUpdate | None:
        watcher = self._watcher
        if watcher is None:
            return None
        update = watcher.poll_once()
        if update is not None:
            self._state = dict(update.state)
        return update

    def current_value(self, source: str) -> Any:
        return self._state.get(source)
