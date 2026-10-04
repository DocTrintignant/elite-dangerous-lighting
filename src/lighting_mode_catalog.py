#!/usr/bin/env python3
"""Immutable live snapshot of user-authored lighting modes.

The desktop editor owns Mode authoring and persistence. COVAS runs on a separate
localhost bridge thread, so it must not reach into mutable Qt editor state.
Instead, Start lighting publishes the exact applied ModeLibrary captured by that
live session. Voice dispatch resolves names against that immutable snapshot and
then reuses the already-accepted lighting_mode_runtime execution path.
"""

from __future__ import annotations

import threading

from lighting_modes import LightingMode, ModeLibrary


class LiveModeCatalog:
    """Thread-safe immutable ModeLibrary snapshot for the current live session."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._library = ModeLibrary(())

    def set_library(self, library: ModeLibrary) -> None:
        if not isinstance(library, ModeLibrary):
            raise ValueError("live mode catalog requires a ModeLibrary")
        with self._lock:
            self._library = library

    def clear(self) -> None:
        self.set_library(ModeLibrary(()))

    def snapshot(self) -> ModeLibrary:
        with self._lock:
            return self._library

    def mode(self, name: str) -> LightingMode | None:
        return self.snapshot().mode(name)

    def names(self) -> tuple[str, ...]:
        return tuple(mode.name for mode in self.snapshot().modes)


mode_catalog = LiveModeCatalog()
