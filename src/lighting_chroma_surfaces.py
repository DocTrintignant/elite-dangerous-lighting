#!/usr/bin/env python3
"""Generic Razer Chroma lighting surfaces used by EDL zone authoring.

These definitions intentionally model Razer's published generic/virtual device
layouts rather than exact cosmetic hardware models.  They are capability data;
they do not acquire hardware and they do not change the accepted Chroma
transport/render lifecycle.

Authoring surface:
- Keyboard: Razer generic 6 x 22 logical/grid surface.  Razer also exposes an
  expanded 8 x 24 animation canvas; logical key positions remain on the 6 x 22
  baseline used here for user-created key zones.
- Mouse: Razer 9 x 7 virtual grid using the documented RZLED2 positions.
- Mousepad/ring: Razer expanded 20-position clockwise ring surface.
- Headset: the published Generic Super Headset left/right authoring view.
- Keypad: Razer generic 4 x 5 grid.
- ChromaLink: five virtual LEDs CL1..CL5.

"Whole device" is deliberately implicit and is never represented as a fake
saved zone.  Saved zones are named subsets of a generic surface and may overlap;
rule ordering, not zone-definition storage, will ultimately resolve overlapping
output writes when those targets are promoted into the live rule path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChromaSurfaceCell:
    cell_id: str
    label: str
    row: int
    column: int
    address: tuple[int, int] | int | str
    tooltip: str = ""

    def __post_init__(self) -> None:
        if not self.cell_id or not self.label:
            raise ValueError("Chroma surface cells require non-empty id and label")
        if self.row < 0 or self.column < 0:
            raise ValueError("Chroma surface cell display coordinates must be non-negative")


@dataclass(frozen=True)
class ChromaSurface:
    surface_id: str
    display_name: str
    endpoint: str
    rows: int
    columns: int
    cells: tuple[ChromaSurfaceCell, ...]
    layout_kind: str
    custom_effect: str
    source_note: str
    expanded_rows: int | None = None
    expanded_columns: int | None = None

    def __post_init__(self) -> None:
        if not self.surface_id or not self.display_name or not self.endpoint:
            raise ValueError("Chroma surface identity fields must be non-empty")
        if self.rows < 1 or self.columns < 1:
            raise ValueError("Chroma surface dimensions must be positive")
        ids = [cell.cell_id for cell in self.cells]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate cell id in {self.surface_id}")
        for cell in self.cells:
            if cell.row >= self.rows or cell.column >= self.columns:
                raise ValueError(
                    f"cell {cell.cell_id!r} lies outside {self.surface_id} display grid"
                )

    def cell(self, cell_id: str) -> ChromaSurfaceCell:
        for cell in self.cells:
            if cell.cell_id == cell_id:
                return cell
        raise KeyError(cell_id)

    @property
    def cell_ids(self) -> tuple[str, ...]:
        return tuple(cell.cell_id for cell in self.cells)


def _grid_cell(row: int, column: int, label: str | None = None) -> ChromaSurfaceCell:
    cell_id = f"R{row}C{column}"
    shown = label or f"{row},{column}"
    return ChromaSurfaceCell(
        cell_id=cell_id,
        label=shown,
        row=row,
        column=column,
        address=(row, column),
        tooltip=f"Razer generic keyboard grid row {row}, column {column}",
    )


# Razer RZKEY values encode row in the high byte and column in the low byte.
# Labels below cover the common generic-keyboard view while retaining every
# remaining 6x22 canvas position as a selectable coordinate for regional/model
# variants.  Locale-specific shared cells are labelled generically rather than
# pretending EDL knows the user's physical keycap layout.
_KEYBOARD_LABELS: dict[tuple[int, int], str] = {
    (0, 1): "Esc",
    **{(0, column): f"F{column - 2}" for column in range(3, 15)},
    (0, 15): "PrtSc",
    (0, 16): "ScrLk",
    (0, 17): "Pause",
    (0, 20): "Logo",
    (0, 21): "Locale",
    (1, 0): "M1",
    (2, 0): "M2",
    (3, 0): "M3",
    (4, 0): "M4",
    (5, 0): "M5",
    (1, 1): "~",
    **{(1, column): str(column - 1) for column in range(2, 11)},
    (1, 11): "0",
    (1, 12): "-",
    (1, 13): "=",
    (1, 14): "Back",
    (1, 15): "Ins",
    (1, 16): "Home",
    (1, 17): "PgUp",
    (1, 18): "Num",
    (1, 19): "/",
    (1, 20): "*",
    (1, 21): "-",
    (2, 1): "Tab",
    (2, 2): "Q",
    (2, 3): "W",
    (2, 4): "E",
    (2, 5): "R",
    (2, 6): "T",
    (2, 7): "Y",
    (2, 8): "U",
    (2, 9): "I",
    (2, 10): "O",
    (2, 11): "P",
    (2, 12): "[",
    (2, 13): "]",
    (2, 14): "\\",
    (2, 15): "Del",
    (2, 16): "End",
    (2, 17): "PgDn",
    (2, 18): "7",
    (2, 19): "8",
    (2, 20): "9",
    (2, 21): "+",
    (3, 1): "Caps",
    (3, 2): "A",
    (3, 3): "S",
    (3, 4): "D",
    (3, 5): "F",
    (3, 6): "G",
    (3, 7): "H",
    (3, 8): "J",
    (3, 9): "K",
    (3, 10): "L",
    (3, 11): ";",
    (3, 12): "'",
    (3, 13): "Locale",
    (3, 14): "Enter",
    (3, 18): "4",
    (3, 19): "5",
    (3, 20): "6",
    (4, 1): "Shift",
    (4, 2): "Locale",
    (4, 3): "Z",
    (4, 4): "X",
    (4, 5): "C",
    (4, 6): "V",
    (4, 7): "B",
    (4, 8): "N",
    (4, 9): "M",
    (4, 10): ",",
    (4, 11): ".",
    (4, 12): "/",
    (4, 13): "Locale",
    (4, 14): "Shift",
    (4, 16): "↑",
    (4, 18): "1",
    (4, 19): "2",
    (4, 20): "3",
    (4, 21): "Enter",
    (5, 1): "Ctrl",
    (5, 2): "Win",
    (5, 3): "Alt",
    (5, 4): "Locale",
    (5, 7): "Space",
    (5, 9): "Locale",
    (5, 10): "Locale",
    (5, 11): "AltGr",
    (5, 12): "Fn",
    (5, 13): "Menu",
    (5, 14): "Ctrl",
    (5, 15): "←",
    (5, 16): "↓",
    (5, 17): "→",
    (5, 19): "0",
    (5, 20): ".",
}

KEYBOARD_SURFACE = ChromaSurface(
    surface_id="KEYBOARD",
    display_name="Keyboard",
    endpoint="keyboard",
    rows=6,
    columns=22,
    cells=tuple(
        _grid_cell(row, column, _KEYBOARD_LABELS.get((row, column)))
        for row in range(6)
        for column in range(22)
    ),
    layout_kind="matrix",
    custom_effect="CHROMA_CUSTOM_KEY",
    source_note=(
        "Razer Generic Super Keyboard: 6x22 generic grid/logical-key baseline. "
        "The SDK also exposes an expanded 8x24 animation canvas."
    ),
    expanded_rows=8,
    expanded_columns=24,
)


def _mouse_cell(cell_id: str, label: str, row: int, column: int) -> ChromaSurfaceCell:
    return ChromaSurfaceCell(
        cell_id=cell_id,
        label=label,
        row=row,
        column=column,
        address=(row, column),
        tooltip=f"Razer mouse virtual LED {label}",
    )


_MOUSE_CELLS: list[ChromaSurfaceCell] = [
    _mouse_cell("SCROLL", "Scroll", 2, 3),
    _mouse_cell("BACKLIGHT", "Backlight", 4, 3),
    _mouse_cell("LOGO", "Logo", 7, 3),
]
for index in range(1, 8):
    _MOUSE_CELLS.append(_mouse_cell(f"LEFT{index}", f"L{index}", index, 0))
    _MOUSE_CELLS.append(_mouse_cell(f"RIGHT{index}", f"R{index}", index, 6))
for index in range(1, 6):
    _MOUSE_CELLS.append(_mouse_cell(f"BOTTOM{index}", f"B{index}", 8, index))

MOUSE_SURFACE = ChromaSurface(
    surface_id="MOUSE",
    display_name="Mouse",
    endpoint="mouse",
    rows=9,
    columns=7,
    cells=tuple(_MOUSE_CELLS),
    layout_kind="matrix",
    custom_effect="CHROMA_CUSTOM2",
    source_note=(
        "Razer Generic Super Mouse / RZLED2 9x7 virtual grid: seven left, seven right, "
        "five bottom LEDs plus scroll wheel, logo and backlight."
    ),
)


def _ring_cells() -> tuple[ChromaSurfaceCell, ...]:
    # Razer's 20-position ring starts at top-right and advances clockwise.
    positions: list[tuple[int, int]] = []
    positions.extend((row, 5) for row in range(0, 5))
    positions.extend((5, column) for column in range(5, 0, -1))
    positions.extend((row, 0) for row in range(5, 0, -1))
    positions.extend((0, column) for column in range(0, 5))
    return tuple(
        ChromaSurfaceCell(
            cell_id=f"LED{index + 1}",
            label=str(index + 1),
            row=row,
            column=column,
            address=index,
            tooltip=f"Razer ring/mousepad virtual LED {index + 1} (clockwise)",
        )
        for index, (row, column) in enumerate(positions)
    )


MOUSEPAD_SURFACE = ChromaSurface(
    surface_id="MOUSEPAD",
    display_name="Mousepad / ring",
    endpoint="mousepad",
    rows=6,
    columns=6,
    cells=_ring_cells(),
    layout_kind="ring",
    custom_effect="CHROMA_CUSTOM",
    source_note=(
        "Razer Generic Super Ring Pattern: 20 clockwise positions, starting at the top-right. "
        "This is the expanded mousepad/ring virtual surface."
    ),
)

HEADSET_SURFACE = ChromaSurface(
    surface_id="HEADSET",
    display_name="Headset",
    endpoint="headset",
    rows=1,
    columns=2,
    cells=(
        ChromaSurfaceCell("LEFT", "Left", 0, 0, "LEFT", "Razer generic headset left side"),
        ChromaSurfaceCell("RIGHT", "Right", 0, 1, "RIGHT", "Razer generic headset right side"),
    ),
    layout_kind="semantic",
    custom_effect="CHROMA_CUSTOM",
    source_note=(
        "Razer Generic Super Headset authoring profile: one individual zone on the left and right. "
        "The REST custom payload has five slots; exact model-specific slot use remains a renderer capability question."
    ),
)

KEYPAD_SURFACE = ChromaSurface(
    surface_id="KEYPAD",
    display_name="Keypad",
    endpoint="keypad",
    rows=4,
    columns=5,
    cells=tuple(
        ChromaSurfaceCell(
            cell_id=f"R{row}C{column}",
            label=str(row * 5 + column + 1),
            row=row,
            column=column,
            address=(row, column),
            tooltip=f"Razer generic keypad row {row}, column {column}",
        )
        for row in range(4)
        for column in range(5)
    ),
    layout_kind="matrix",
    custom_effect="CHROMA_CUSTOM",
    source_note="Razer Generic Super Keypad: 4x5 grid.",
)

CHROMALINK_SURFACE = ChromaSurface(
    surface_id="CHROMALINK",
    display_name="ChromaLink",
    endpoint="chromalink",
    rows=1,
    columns=5,
    cells=tuple(
        ChromaSurfaceCell(
            cell_id=f"CL{index}",
            label=f"CL{index}",
            row=0,
            column=index - 1,
            address=index - 1,
            tooltip=(
                "ChromaLink CL1 is Razer's base/all-device layer"
                if index == 1
                else f"ChromaLink optional overlay virtual LED CL{index}"
            ),
        )
        for index in range(1, 6)
    ),
    layout_kind="linear",
    custom_effect="CHROMA_CUSTOM",
    source_note=(
        "Razer ChromaLink: five virtual LEDs. CL1 is the base/all-device layer; CL2-CL5 are optional overlays."
    ),
)

CHROMA_SURFACES: tuple[ChromaSurface, ...] = (
    KEYBOARD_SURFACE,
    MOUSE_SURFACE,
    MOUSEPAD_SURFACE,
    HEADSET_SURFACE,
    KEYPAD_SURFACE,
    CHROMALINK_SURFACE,
)
CHROMA_SURFACE_BY_ID = {surface.surface_id: surface for surface in CHROMA_SURFACES}


def chroma_surface(surface_id: str) -> ChromaSurface:
    try:
        return CHROMA_SURFACE_BY_ID[surface_id.upper()]
    except (AttributeError, KeyError) as exc:
        raise ValueError(f"unknown Chroma surface: {surface_id!r}") from exc
