#!/usr/bin/env python3
"""Small thread-lifecycle helpers for operator-facing worker shutdown.

This module does not own Chroma, HID, Status, or rule semantics.  It only makes
UI shutdown explicit: request the existing worker to stop and wait for its
normal cleanup path to finish before allowing the process to disappear.
"""

from __future__ import annotations

import threading

DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 6.0


def request_stop_and_join(
    thread: threading.Thread | None,
    stop_event: threading.Event | None,
    *,
    timeout_seconds: float = DEFAULT_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
) -> bool:
    """Request worker stop and return True only after that worker has terminated."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if stop_event is not None:
        stop_event.set()
    if thread is None or not thread.is_alive():
        return True
    if thread is threading.current_thread():
        raise RuntimeError("a worker cannot join itself")
    thread.join(timeout=timeout_seconds)
    return not thread.is_alive()
