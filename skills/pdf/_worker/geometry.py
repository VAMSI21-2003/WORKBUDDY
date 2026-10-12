"""Table geometry from ruling lines: the Inspector's own reading of a table's structure.

Pure Python, standard library only, no PDF library. Given the horizontal and vertical line
segments a page draws, ``find_grids`` says which connected sets of lines form a table, and whether
each is a *plain* grid: every cell bounded on all four sides, no cell spanning two columns or two
rows. The sandboxed worker's independent check (``main.py``, mode ``verify``) uses it with the
segments PDFium reports, so its idea of the table's structure doesn't come from the library the
skill used (pdfplumber). Coordinates are PDF user space: ``y`` grows upward.
"""

from __future__ import annotations

from dataclasses import dataclass

# Lines closer than this (in points) are the same line, and lines that stop this short of each
# other still meet. A table's rules are drawn exactly; this absorbs the rounding of generators.
TOLERANCE = 3.0
# A segment shorter than this is a tick or a dot, not a rule.
MIN_LENGTH = 2.0

HSegment = tuple[float, float, float]  # (y, x_from, x_to)
VSegment = tuple[float, float, float]  # (x, y_from, y_to)


@dataclass(frozen=True)
class Grid:
    """One table as drawn: the x and y of its rules, and whether it is a plain grid.

    Attributes:
        xs: Distinct x positions of the vertical rules, left to right.
        ys: Distinct y positions of the horizontal rules, top to bottom (descending y).
        plain: Every slot between neighbouring rules is bounded on all four sides.
    """

    xs: tuple[float, ...]
    ys: tuple[float, ...]
    plain: bool

    @property
    def rows(self) -> int:
        return len(self.ys) - 1

    @property
    def columns(self) -> int:
        return len(self.xs) - 1

    def cell_box(self, row: int, column: int) -> tuple[float, float, float, float]:
        """``(left, bottom, right, top)`` of a slot."""
        return (self.xs[column], self.ys[row + 1], self.xs[column + 1], self.ys[row])


def _cluster(values: list[float]) -> dict[float, float]:
    """Map each value to the representative of its cluster (values within ``TOLERANCE``)."""
    mapping: dict[float, float] = {}
    group: list[float] = []
    for value in sorted(set(values)):
        if group and value - group[0] > TOLERANCE:
            representative = sum(group) / len(group)
            mapping.update({member: representative for member in group})
            group = []
        group.append(value)
    if group:
        representative = sum(group) / len(group)
        mapping.update({member: representative for member in group})
    return mapping


def _merge(segments: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Join segments on the same line that overlap or nearly touch."""
    merged: list[tuple[float, float, float]] = []
    for line, start, end in sorted(segments):
        if merged and merged[-1][0] == line and start <= merged[-1][2] + TOLERANCE:
            last = merged[-1]
            merged[-1] = (line, last[1], max(last[2], end))
        else:
            merged.append((line, start, end))
    return merged


def _covers(
    segments: list[tuple[float, float, float]], line: float, start: float, end: float
) -> bool:
    """Whether one segment on ``line`` spans from ``start`` to ``end`` (within the tolerance)."""
    return any(
        s_line == line and s_start <= start + TOLERANCE and s_end >= end - TOLERANCE
        for s_line, s_start, s_end in segments
    )


def find_grids(h_segments: list[HSegment], v_segments: list[VSegment]) -> list[Grid]:
    """The tables a page's ruling lines draw, sorted from the top of the page down.

    A table is a connected set of rules (a horizontal and a vertical one are connected where they
    cross or meet). A set with fewer than two vertical rules or two horizontal ones, or with only
    one slot (a lone box), draws no table. Of the others, ``Grid.plain`` says whether every slot
    is closed on all four sides: an interior rule missing over a stretch means two cells run
    together (a merged or spanning cell), and the grid isn't plain.
    """
    h = [(y, min(a, b), max(a, b)) for y, a, b in h_segments if abs(b - a) >= MIN_LENGTH]
    v = [(x, min(a, b), max(a, b)) for x, a, b in v_segments if abs(b - a) >= MIN_LENGTH]
    y_of = _cluster([y for y, _, _ in h])
    x_of = _cluster([x for x, _, _ in v])
    hs = _merge([(y_of[y], a, b) for y, a, b in h])
    vs = _merge([(x_of[x], a, b) for x, a, b in v])

    # Union-find over the rules: connected where a horizontal and a vertical one meet.
    parent = list(range(len(hs) + len(vs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for hi, (y, xa, xb) in enumerate(hs):
        for vi, (x, ya, yb) in enumerate(vs):
            if xa - TOLERANCE <= x <= xb + TOLERANCE and ya - TOLERANCE <= y <= yb + TOLERANCE:
                parent[find(hi)] = find(len(hs) + vi)

    components: dict[int, tuple[list[HSegment], list[VSegment]]] = {}
    for hi, segment in enumerate(hs):
        components.setdefault(find(hi), ([], []))[0].append(segment)
    for vi, segment in enumerate(vs):
        components.setdefault(find(len(hs) + vi), ([], []))[1].append(segment)

    grids: list[Grid] = []
    for comp_h, comp_v in components.values():
        xs = sorted({x for x, _, _ in comp_v})
        ys = sorted({y for y, _, _ in comp_h}, reverse=True)
        if len(xs) < 2 or len(ys) < 2 or (len(xs) - 1) * (len(ys) - 1) < 2:
            continue
        plain = all(
            _covers(comp_h, ys[j], xs[i], xs[i + 1])
            and _covers(comp_h, ys[j + 1], xs[i], xs[i + 1])
            and _covers(comp_v, xs[i], ys[j + 1], ys[j])
            and _covers(comp_v, xs[i + 1], ys[j + 1], ys[j])
            for j in range(len(ys) - 1)
            for i in range(len(xs) - 1)
        )
        grids.append(Grid(tuple(xs), tuple(ys), plain))
    return sorted(grids, key=lambda grid: -grid.ys[0])


def cells_are_plain_grid(
    cells: list[tuple[float, float, float, float]],
) -> tuple[bool, int, int]:
    """Whether a library's detected cells form a plain grid, and its size.

    ``cells`` are ``(x0, top, x1, bottom)`` boxes (``top`` < ``bottom``). The grid is plain when
    every cell spans exactly one slot between neighbouring column edges and neighbouring row
    edges, and every slot has exactly one cell: no cell runs across two columns or two rows, and
    none is missing. Returns ``(plain, rows, columns)``; the sizes are those of the edges found,
    whether or not the grid is plain.
    """
    x_of = _cluster([c[0] for c in cells] + [c[2] for c in cells])
    y_of = _cluster([c[1] for c in cells] + [c[3] for c in cells])
    xs = sorted(set(x_of.values()))
    ys = sorted(set(y_of.values()))
    column = {x: i for i, x in enumerate(xs)}
    row = {y: i for i, y in enumerate(ys)}
    slots = set()
    for x0, top, x1, bottom in cells:
        i0, i1 = column[x_of[x0]], column[x_of[x1]]
        j0, j1 = row[y_of[top]], row[y_of[bottom]]
        if i1 != i0 + 1 or j1 != j0 + 1:
            return False, max(len(ys) - 1, 0), max(len(xs) - 1, 0)
        slots.add((j0, i0))
    full = (len(xs) - 1) * (len(ys) - 1)
    return len(slots) == len(cells) == full, len(ys) - 1, len(xs) - 1
