#!/usr/bin/env python3
"""Renderer-facing ChromaLink cell addressing.

ChromaLink exposes one physical Chroma device category with five virtual cells.
EDL keeps the existing ``CHROMALINK`` target as the backward-compatible whole-
device alias and adds explicit leaf addresses for new rules:

    CHROMALINK::CL1 ... CHROMALINK::CL5

The generic rule engine remains unchanged: these leaf addresses are ordinary
ordered targets. Renderer code may collapse them back to the one physical
``CHROMALINK`` REST endpoint when acquiring a Chroma session.
"""

from __future__ import annotations

from typing import Iterable

CHROMALINK_TARGET = "CHROMALINK"
CHROMALINK_CELL_NAMES = ("CL1", "CL2", "CL3", "CL4", "CL5")
CHROMALINK_CELL_TARGETS = tuple(
    f"{CHROMALINK_TARGET}::{name}" for name in CHROMALINK_CELL_NAMES
)
CHROMALINK_CELL_COUNT = len(CHROMALINK_CELL_TARGETS)


def is_chromalink_cell_target(target: str) -> bool:
    return isinstance(target, str) and target.strip().upper() in CHROMALINK_CELL_TARGETS


def is_chromalink_target(target: str) -> bool:
    if not isinstance(target, str):
        return False
    normalized = target.strip().upper()
    return normalized == CHROMALINK_TARGET or normalized in CHROMALINK_CELL_TARGETS


def physical_chroma_target(target: str) -> str:
    """Collapse one logical ChromaLink cell to the physical Chroma category.

    Non-ChromaLink target identifiers are returned byte-for-byte apart from
    surrounding whitespace. This is important for preserved foreign addresses
    such as VIRPIL device/LED identifiers.
    """
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string")
    original = target.strip()
    normalized = original.upper()
    if normalized == CHROMALINK_TARGET or normalized in CHROMALINK_CELL_TARGETS:
        return CHROMALINK_TARGET
    return original


def cell_target(cell: str) -> str:
    if not isinstance(cell, str):
        raise ValueError("ChromaLink cell must be a string")
    normalized = cell.strip().upper()
    if normalized in CHROMALINK_CELL_TARGETS:
        return normalized
    if normalized in CHROMALINK_CELL_NAMES:
        return f"{CHROMALINK_TARGET}::{normalized}"
    raise ValueError(
        f"unsupported ChromaLink cell {cell!r}; expected one of {CHROMALINK_CELL_NAMES}"
    )


def normalize_cell_targets(cells: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(cell_target(cell) for cell in cells)
    if not selected:
        raise ValueError("select at least one ChromaLink cell")
    if len(set(selected)) != len(selected):
        raise ValueError("ChromaLink cells must not contain duplicates")
    selected_set = set(selected)
    return tuple(target for target in CHROMALINK_CELL_TARGETS if target in selected_set)


def display_cell_targets(targets: Iterable[str]) -> str:
    normalized = {value.strip().upper() for value in targets}
    selected = [
        target.split("::", 1)[1]
        for target in CHROMALINK_CELL_TARGETS
        if target in normalized
    ]
    return " + ".join(selected)
