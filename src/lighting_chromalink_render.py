#!/usr/bin/env python3
"""ChromaLink five-cell rendering on the accepted persistent Chroma session.

This module adds addressing only.  Session acquisition, heartbeat, continuous
rendering and DELETE handback stay owned by the already accepted Chroma session
classes.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable

from lighting_chroma_preview import PreviewChromaSession, _request_json, _require_success
from lighting_chromalink_cells import (
    CHROMALINK_CELL_COUNT,
    CHROMALINK_CELL_TARGETS,
    normalize_cell_targets,
)
from lighting_intent import LightingIntent
from lighting_render_rate import frame_period_seconds
from lighting_rules import rgb_to_chroma_colorref
from lighting_runtime import RuntimeIntentSample, render_runtime_tick

RGB = tuple[int, int, int]
BLACK: RGB = (0, 0, 0)


def chromalink_custom_payload(colours: Iterable[RGB]) -> dict:
    values = tuple(colours)
    if len(values) != CHROMALINK_CELL_COUNT:
        raise ValueError(
            f"ChromaLink frame requires exactly {CHROMALINK_CELL_COUNT} cell colours"
        )
    return {
        "effect": "CHROMA_CUSTOM",
        "param": [rgb_to_chroma_colorref(rgb) for rgb in values],
    }


def render_chromalink_cells(session, colours: Iterable[RGB]) -> None:
    """Render one complete CL1..CL5 frame through an already-active session."""
    uri = getattr(session, "uri", None)
    if uri is None:
        raise RuntimeError("No active Chroma session")
    heartbeat_error = getattr(session, "_heartbeat_error", None)
    if heartbeat_error:
        raise RuntimeError(f"Chroma heartbeat failed: {heartbeat_error}")
    response = _request_json(
        "PUT",
        f"{uri}/chromalink",
        chromalink_custom_payload(colours),
    )
    _require_success("chromalink cell render", response)


def _runtime_sample(intent: LightingIntent, elapsed_seconds: float) -> RuntimeIntentSample:
    if intent.effect == "STATIC":
        return RuntimeIntentSample(intent)
    if intent.effect in {"FLASH", "PULSE", "BREATH"}:
        return RuntimeIntentSample(intent, elapsed_seconds=elapsed_seconds)
    raise ValueError(f"effect {intent.effect!r} is not wired for UI hardware preview")


def run_chromalink_cell_preview(
    intent: LightingIntent,
    cells: Iterable[str],
    stop_event: threading.Event,
) -> None:
    """Preview one effect on an explicit subset of CL1..CL5.

    Unselected cells are deliberately black for the temporary preview so the
    operator can see exactly which virtual ChromaLink positions are addressed.
    This does not change live rule semantics.
    """
    if not isinstance(intent, LightingIntent):
        raise ValueError("preview intent must be a LightingIntent")
    if intent.target != "CHROMALINK":
        raise ValueError("ChromaLink cell preview requires a CHROMALINK intent")
    if not isinstance(stop_event, threading.Event):
        raise ValueError("stop_event must be threading.Event")

    selected = set(normalize_cell_targets(cells))
    render_runtime_tick((_runtime_sample(intent, 0.0),))
    session = PreviewChromaSession("CHROMALINK")

    try:
        session.start()
        started = time.perf_counter()
        next_frame = started
        while not stop_event.is_set():
            elapsed = time.perf_counter() - started
            frame = render_runtime_tick((_runtime_sample(intent, elapsed),))
            rgb = frame["CHROMALINK"]
            colours = tuple(
                rgb if target in selected else BLACK
                for target in CHROMALINK_CELL_TARGETS
            )
            render_chromalink_cells(session, colours)

            next_frame += frame_period_seconds()
            remaining = next_frame - time.perf_counter()
            if remaining > 0:
                stop_event.wait(remaining)
            else:
                next_frame = time.perf_counter()
    finally:
        if session.active:
            session.release()
