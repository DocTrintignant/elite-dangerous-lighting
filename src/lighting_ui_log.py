#!/usr/bin/env python3
"""Structured, numerically ordered operator-session logging for the desktop UI.

The log is intentionally event-oriented rather than frame-oriented. It records
meaningful operator decisions and product state transitions while excluding
high-frequency noise such as spectrum-drag pixels and 30 FPS Chroma frames.

The UI logging preference is honoured before a session file is created. Multiple
UI layers instantiated in one process share one session state, so composition
cannot accidentally create duplicate JSONL sessions.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SESSION_PREFIX = "ui_session_"
SESSION_SUFFIX = ".jsonl"
LOG_SETTING_KEY = "ui/session_logging_enabled"


def _logging_enabled() -> bool:
    """Read the persistent UI preference without making logging require Qt."""
    try:
        from lighting_settings import app_settings
    except Exception:
        return True
    value = app_settings().value(LOG_SETTING_KEY, False)
    return str(value).lower() not in {"false", "0"}


def _next_session_number(log_dir: Path) -> int:
    highest = 0
    if log_dir.exists():
        for path in log_dir.glob(f"{SESSION_PREFIX}*{SESSION_SUFFIX}"):
            stem = path.name[len(SESSION_PREFIX) : -len(SESSION_SUFFIX)]
            if stem.isdigit():
                highest = max(highest, int(stem))
    return highest + 1


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


@dataclass
class _SessionState:
    log_dir: Path
    session_number: int
    path: Path
    sequence: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    session_start_seen: bool = False
    io_error: str | None = None


_SHARED_LOCK = threading.Lock()
_SHARED_BY_DIR: dict[str, _SessionState] = {}


def _shared_state(log_dir: Path) -> _SessionState:
    resolved = str(log_dir.resolve())
    with _SHARED_LOCK:
        state = _SHARED_BY_DIR.get(resolved)
        if state is not None:
            return state
        log_dir.mkdir(parents=True, exist_ok=True)
        session_number = _next_session_number(log_dir)
        path = log_dir / f"{SESSION_PREFIX}{session_number:04d}{SESSION_SUFFIX}"
        path.touch(exist_ok=False)
        state = _SessionState(log_dir, session_number, path)
        _SHARED_BY_DIR[resolved] = state
        return state


class UiSessionLogger:
    """Append meaningful UI events to the process's one operator session."""

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = Path(log_dir)
        self._enabled = _logging_enabled()
        self._state = None
        self._io_error: str | None = None
        if self._enabled:
            try:
                self._state = _shared_state(self.log_dir)
            except OSError as exc:
                self._enabled = False
                self._io_error = str(exc)
        self.session_number = self._state.session_number if self._state is not None else 0
        self.path = self._state.path if self._state is not None else None

    @property
    def sequence(self) -> int:
        return self._state.sequence if self._state is not None else 0

    @property
    def io_error(self) -> str | None:
        state = self._state
        if state is not None and state.io_error is not None:
            return state.io_error
        return self._io_error

    def event(self, event: str, **data: Any) -> dict[str, Any] | None:
        if not isinstance(event, str) or not event.strip():
            raise ValueError("log event must be a non-empty string")
        if not self._enabled or self._state is None:
            return None

        normalized_event = event.strip().upper()
        state = self._state
        with state.lock:
            if state.io_error is not None:
                return None
            # A composed UI may construct more than one logger facade while one
            # window is being built. Preserve one semantic SESSION_START.
            if normalized_event == "SESSION_START" and state.session_start_seen:
                return None

            next_sequence = state.sequence + 1
            record = {
                "session": state.session_number,
                "seq": next_sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "event": normalized_event,
                "data": _json_safe(data),
            }
            try:
                with state.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
            except OSError as exc:
                state.io_error = str(exc)
                self._io_error = str(exc)
                return None
            state.sequence = next_sequence
            if normalized_event == "SESSION_START":
                state.session_start_seen = True

        detail = " ".join(f"{key}={value!r}" for key, value in record["data"].items())
        suffix = f" | {detail}" if detail else ""
        print(
            f"[UI {state.session_number:04d}:{state.sequence:06d}] "
            f"{record['event']}{suffix}",
            flush=True,
        )
        return record
