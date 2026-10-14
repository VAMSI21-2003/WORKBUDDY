"""The pure modules the PDF worker is built on: cell text normal form, and ruling-line geometry.

No PDF library is involved. ``geometry.find_grids`` is the Inspector's own reading of a table's
structure, so its edge cases (a rule missing, two tables, a lone box, rules that nearly touch) are
tested here directly on the segments a page would draw.
"""

from __future__ import annotations

import pytest

from skills.pdf._worker import geometry, textnorm


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Apple", "Apple"),
        ("  Apple  ", "Apple"),
        ("Acme\nCorp", "Acme Corp"),
        ("Acme \r\n  Corp\t Ltd", "Acme Corp Ltd"),
        ("\ufb01nal", "final"),  # a ligature
        ("o\ufb03ce \ufb00 \ufb02 \ufb03 \ufb04 \ufb05 \ufb06", "office ff fl ffi ffl st st"),
        # Nothing that is another value is changed: NFKC would have written these 21⁄2, m2, 103,
        # TM, 1 and half-width forms.
        ("2\u00bd", "2\u00bd"),
        ("12 m\u00b2", "12 m\u00b2"),
        ("10\u00b3 W", "10\u00b3 W"),
        ("Acme\u2122", "Acme\u2122"),
        ("\u2460", "\u2460"),
        ("\uff11\uff12", "\uff11\uff12"),
        ("e\u0301", "\u00e9"),  # a letter and its accent are one letter (NFC)
        ("a b", "a b"),  # no-break space
        ("a​b", "ab"),  # zero-width space is dropped, not turned into a space
        ("a­b", "ab"),  # soft hyphen
        ("a\x00b\x07c", "a b c"),  # control characters are spaces
        ("\ud800x", "x"),  # a lone surrogate
        ("x", "x"),  # private use
        ("", ""),
        ("   \n\t ", ""),
    ],
)
def test_normalize_cell(raw: str, expected: str) -> None:
    assert textnorm.normalize_cell(raw) == expected


def test_normalize_cell_is_idempotent() -> None:
    for raw in ("  a  b\n", "ﬁ​ x", "=SUM(A1)", "007", "1,234.50"):
        once = textnorm.normalize_cell(raw)
        assert textnorm.normalize_cell(once) == once


def test_normalize_cell_leaves_text_that_looks_like_a_formula_alone() -> None:
    # Stored as text by the skill (data_type "s"); the normal form must not touch it.
    assert textnorm.normalize_cell("=SUM(A1)") == "=SUM(A1)"
    assert textnorm.normalize_cell("@cmd") == "@cmd"


def _lines(xs: list[float], ys: list[float]) -> tuple[list, list]:
    """The horizontal and vertical rules of a full grid with rules at ``xs`` and ``ys``."""
    h = [(y, xs[0], xs[-1]) for y in ys]
    v = [(x, ys[-1], ys[0]) for x in xs]
    return h, v


def test_a_full_grid_is_one_plain_table() -> None:
    h, v = _lines([50, 140, 230, 320], [700, 680, 660, 640])
    (grid,) = geometry.find_grids(h, v)
    assert grid.plain
    assert (grid.rows, grid.columns) == (3, 3)
    assert grid.cell_box(0, 0) == (50, 680, 140, 700)
    assert grid.cell_box(2, 2) == (230, 640, 320, 660)


def test_a_missing_interior_rule_is_not_plain() -> None:
    h, v = _lines([50, 140, 230], [700, 680, 660])
    v = [s for s in v if s[0] != 140] + [(140, 680, 700)]  # the middle rule stops after row 1
    (grid,) = geometry.find_grids(h, v)
    assert not grid.plain


def test_a_missing_horizontal_stretch_is_not_plain() -> None:
    h, v = _lines([50, 140, 230], [700, 680, 660])
    h = [s for s in h if s[0] != 680] + [(680, 50, 140)]  # the row rule stops at the middle
    (grid,) = geometry.find_grids(h, v)
    assert not grid.plain


def test_two_separate_tables_are_two_grids_sorted_from_the_top() -> None:
    h1, v1 = _lines([50, 140, 230], [300, 280, 260])
    h2, v2 = _lines([50, 140, 230], [700, 680, 660])
    grids = geometry.find_grids(h1 + h2, v1 + v2)
    assert len(grids) == 2
    assert grids[0].ys[0] == 700 and grids[1].ys[0] == 300
    assert all(g.plain for g in grids)


def test_a_lone_box_is_not_a_table() -> None:
    h, v = _lines([50, 140], [700, 680])
    assert geometry.find_grids(h, v) == []


def test_rules_only_one_way_are_not_a_table() -> None:
    assert geometry.find_grids([(700, 50, 300), (680, 50, 300)], []) == []
    assert geometry.find_grids([], [(50, 600, 700), (300, 600, 700)]) == []


def test_rules_that_nearly_meet_still_connect_and_nearby_lines_are_one_line() -> None:
    xs, ys = [50, 140, 230], [700, 680, 660]
    h, v = _lines(xs, ys)
    # A drawn line split in two with a small gap, and a rule 1 pt off the others.
    h = [s for s in h if s[0] != 680] + [(680, 50, 139), (680.8, 141, 230)]
    v = [s for s in v if s[0] != 230] + [(231, 660, 700)]
    (grid,) = geometry.find_grids(h, v)
    assert grid.plain
    assert (grid.rows, grid.columns) == (2, 2)


def test_ticks_shorter_than_a_rule_are_ignored() -> None:
    h, v = _lines([50, 140, 230], [700, 680, 660])
    h.append((690, 60, 61))
    v.append((100, 690, 691))
    (grid,) = geometry.find_grids(h, v)
    assert (grid.rows, grid.columns) == (2, 2)


def test_a_stray_rule_that_meets_nothing_is_not_part_of_the_table() -> None:
    h, v = _lines([50, 140, 230], [700, 680, 660])
    v.append((300, 660, 700))  # 70 pt to the right of the grid: meets no horizontal rule
    (grid,) = geometry.find_grids(h, v)
    assert grid.plain
    assert (grid.rows, grid.columns) == (2, 2)


@pytest.mark.parametrize(
    ("cells", "plain", "rows", "columns"),
    [
        # 2 x 2, x0 top x1 bottom
        ([(0, 0, 10, 10), (10, 0, 20, 10), (0, 10, 10, 20), (10, 10, 20, 20)], True, 2, 2),
        # a cell spans two columns in the top row
        ([(0, 0, 20, 10), (0, 10, 10, 20), (10, 10, 20, 20)], False, 2, 2),
        # a cell is missing
        ([(0, 0, 10, 10), (10, 0, 20, 10), (0, 10, 10, 20)], False, 2, 2),
        # the same grid with the coordinates a point out
        ([(0, 0, 10, 10), (10.9, 0.4, 20, 10.2), (0, 10, 10, 20), (10, 10, 20.5, 20)], True, 2, 2),
    ],
)
def test_cells_are_plain_grid(cells, plain: bool, rows: int, columns: int) -> None:  # type: ignore[no-untyped-def]
    assert geometry.cells_are_plain_grid(cells) == (plain, rows, columns)
