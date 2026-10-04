#!/usr/bin/env python3
"""Addressable-effect helpers for generic Razer Chroma surfaces.

Existing uniform zone composition remains in ``lighting_chroma_zone_render``.
This module adds only the first proven addressable boundary: sample a logical
frame over EDL's generic Keyboard/Mouse geometry, convert it to the documented
custom matrix, and combine saved zones by existing rule-order priority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from lighting_chroma_surfaces import chroma_surface
from lighting_chroma_zone_config import ChromaZoneConfiguration

RGB = tuple[int, int, int]
BLACK: RGB = (0, 0, 0)
SUPPORTED_SURFACES = ("KEYBOARD", "MOUSE")


def surface_cell_points(surface_id: str) -> tuple[tuple[float, float], ...]:
    surface = chroma_surface(str(surface_id).upper())
    x_denominator = max(1, surface.columns - 1)
    y_denominator = max(1, surface.rows - 1)
    return tuple(
        (cell.column / x_denominator, cell.row / y_denominator)
        for cell in surface.cells
    )


def frame_to_surface_matrix(
    surface_id: str,
    frame: Sequence[RGB],
) -> tuple[tuple[RGB, ...], ...]:
    surface_id = str(surface_id).upper()
    if surface_id not in SUPPORTED_SURFACES:
        raise ValueError(f"addressable Chroma effects are not wired for {surface_id}")
    surface = chroma_surface(surface_id)
    values = tuple(frame)
    if len(values) != len(surface.cells):
        raise ValueError(
            f"{surface_id} effect frame must contain exactly {len(surface.cells)} logical cells"
        )
    matrix: list[list[RGB]] = [
        [BLACK for _column in range(surface.columns)]
        for _row in range(surface.rows)
    ]
    for cell, colour in zip(surface.cells, values):
        matrix[cell.row][cell.column] = colour
    return tuple(tuple(row) for row in matrix)


def uniform_surface_matrix(
    surface_id: str,
    colour: RGB,
) -> tuple[tuple[RGB, ...], ...]:
    surface_id = str(surface_id).upper()
    if surface_id not in SUPPORTED_SURFACES:
        raise ValueError(f"saved Chroma zones are not live-wired for {surface_id}")
    surface = chroma_surface(surface_id)
    return tuple(
        tuple(colour for _column in range(surface.columns))
        for _row in range(surface.rows)
    )


def compose_surface_matrices(
    surface_id: str,
    configuration: ChromaZoneConfiguration,
    matrices: Mapping[str, Sequence[Sequence[RGB]]],
    priorities: Mapping[str, int],
) -> tuple[tuple[RGB, ...], ...]:
    """Compose whole-surface + zone matrices with the accepted rule-order winner."""
    surface_id = str(surface_id).upper()
    if surface_id not in SUPPORTED_SURFACES:
        raise ValueError(f"saved Chroma zones are not live-wired for {surface_id}")
    surface = chroma_surface(surface_id)

    base = matrices.get(surface_id)
    if base is None:
        result = [
            [BLACK for _column in range(surface.columns)]
            for _row in range(surface.rows)
        ]
    else:
        base_rows = tuple(tuple(row) for row in base)
        if len(base_rows) != surface.rows or any(len(row) != surface.columns for row in base_rows):
            raise ValueError(f"base matrix for {surface_id} has wrong dimensions")
        result = [list(row) for row in base_rows]

    base_priority = priorities.get(surface_id, -2)
    cell_priority = [
        [base_priority for _column in range(surface.columns)]
        for _row in range(surface.rows)
    ]

    for zone in configuration.for_surface(surface_id):
        target = zone.target
        source = matrices.get(target)
        if source is None:
            continue
        source_rows = tuple(tuple(row) for row in source)
        if len(source_rows) != surface.rows or any(len(row) != surface.columns for row in source_rows):
            raise ValueError(f"zone matrix for {target} has wrong dimensions")
        priority = priorities.get(target, -2)
        for cell_id in zone.cells:
            cell = surface.cell(cell_id)
            if priority >= cell_priority[cell.row][cell.column]:
                result[cell.row][cell.column] = source_rows[cell.row][cell.column]
                cell_priority[cell.row][cell.column] = priority

    return tuple(tuple(row) for row in result)
