#!/usr/bin/env python3
"""Live Elite Dangerous Status.json watcher.

This module owns only the file-observation boundary:

    Status.json -> safe complete JSON read -> elite_status.decode_status() -> changes

It does not talk to Chroma, import Virpil profiles, or read Journal files.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from elite_status import FLAG2_BITS, FLAG_BITS, SCALAR_KEYS, decode_status

DEFAULT_POLL_SECONDS = 0.10
SAVED_GAMES_OVERRIDE_ENV = "ELITE_DANGEROUS_SAVED_GAMES"

# Timestamp/event identify a Status.json write rather than a lighting-relevant
# semantic change. RawFlags/RawFlags2 are retained in the decoded snapshot but
# named booleans are the canonical change vocabulary.
SEMANTIC_KEYS: tuple[str, ...] = (
    *FLAG_BITS.keys(),
    *FLAG2_BITS.keys(),
    *(key for key in SCALAR_KEYS if key not in {"Timestamp", "Event"}),
)


def _known_folder_saved_games() -> Path | None:
    """Resolve Windows' Saved Games known folder when the API is available."""
    if not hasattr(ctypes, "windll"):
        return None

    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    saved_games_id = GUID(
        0x4C5C32FF,
        0xBB9D,
        0x43B0,
        (ctypes.c_ubyte * 8)(0xB5, 0xB4, 0x2D, 0x72, 0xE5, 0x4E, 0xAA, 0xA4),
    )
    path_ptr = ctypes.c_wchar_p()

    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(saved_games_id), 0, None, ctypes.byref(path_ptr)
        )
        if result != 0 or not path_ptr.value:
            return None
        return Path(path_ptr.value)
    except Exception:
        return None
    finally:
        try:
            if path_ptr:
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)
        except Exception:
            pass


def discover_elite_saved_games() -> Path | None:
    """Find Elite's Saved Games directory without depending on cockpit code."""
    override = os.environ.get(SAVED_GAMES_OVERRIDE_ENV)
    if override:
        candidate = Path(override).expanduser()
        if candidate.exists():
            return candidate

    saved_games = _known_folder_saved_games()
    if saved_games is not None:
        candidate = saved_games / "Frontier Developments" / "Elite Dangerous"
        if candidate.exists():
            return candidate

    fallback = (
        Path.home()
        / "Saved Games"
        / "Frontier Developments"
        / "Elite Dangerous"
    )
    if fallback.exists():
        return fallback

    return None


def default_status_path() -> Path | None:
    directory = discover_elite_saved_games()
    if directory is None:
        return None
    return directory / "Status.json"


def load_status_file(path: Path | str) -> dict[str, Any]:
    """Read one complete Status.json object.

    JSONDecodeError is intentionally allowed to propagate. The watcher treats
    that as an in-progress write and retries on the next poll without accepting
    the file signature as successfully consumed.
    """
    status_path = Path(path)
    with status_path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise TypeError("Status.json root must be an object")
    return payload


def diff_semantic_states(
    previous: dict[str, Any], current: dict[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """Return only canonical semantic values that changed."""
    changed: dict[str, tuple[Any, Any]] = {}
    for key in SEMANTIC_KEYS:
        old = previous.get(key)
        new = current.get(key)
        if old != new:
            changed[key] = (old, new)
    return changed


@dataclass(frozen=True)
class StatusUpdate:
    initial: bool
    state: dict[str, Any]
    changed: dict[str, tuple[Any, Any]]


class EliteStatusWatcher:
    """Poll one Status.json path and emit only complete semantic updates."""

    def __init__(self, status_path: Path | str) -> None:
        self.status_path = Path(status_path)
        self._last_signature: tuple[int, int] | None = None
        self._last_state: dict[str, Any] | None = None

    def poll_once(self) -> StatusUpdate | None:
        try:
            stat = self.status_path.stat()
        except FileNotFoundError:
            return None

        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._last_signature:
            return None

        try:
            payload = load_status_file(self.status_path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            # Do not consume the signature. If Elite was part-way through the
            # write, the same file state is retried on the next normal poll.
            return None

        state = decode_status(payload)
        previous = self._last_state
        self._last_signature = signature
        self._last_state = state

        if previous is None:
            return StatusUpdate(initial=True, state=state, changed={})

        changed = diff_semantic_states(previous, state)
        if not changed:
            return None

        return StatusUpdate(initial=False, state=state, changed=changed)


def _print_initial(state: dict[str, Any]) -> None:
    active = [
        name
        for name in (*FLAG_BITS.keys(), *FLAG2_BITS.keys())
        if state.get(name) is True
    ]
    print("INITIAL STATUS")
    if active:
        print("  active: " + ", ".join(active))
    else:
        print("  active: none")


def _print_changes(changed: dict[str, tuple[Any, Any]]) -> None:
    print("STATUS CHANGE")
    for key, (old, new) in changed.items():
        print(f"  {key}: {old!r} -> {new!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch Elite Dangerous Status.json")
    parser.add_argument(
        "--status-file",
        type=Path,
        help="Explicit Status.json path; otherwise use Windows Saved Games discovery.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help=f"Polling interval in seconds (default: {DEFAULT_POLL_SECONDS:.2f}).",
    )
    args = parser.parse_args(argv)

    if args.interval <= 0:
        parser.error("--interval must be greater than zero")

    status_path = args.status_file or default_status_path()
    if status_path is None:
        print(
            "Elite Dangerous Saved Games directory was not found. "
            "Use --status-file or set " + SAVED_GAMES_OVERRIDE_ENV + "."
        )
        return 2

    watcher = EliteStatusWatcher(status_path)
    print(f"WATCHING: {status_path}")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            update = watcher.poll_once()
            if update is not None:
                if update.initial:
                    _print_initial(update.state)
                else:
                    _print_changes(update.changed)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nWATCHER STOPPED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
