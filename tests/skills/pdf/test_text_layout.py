"""Where a table's text sits: whether each cell's text is really that cell's.

Round 3 of ``feat/pdf-extract`` found two faults in the first version of this check. A sentence
that the PDF draws as several text objects (a note with one bold word, or a generator that draws
word by word) was delivered split across two cells when the rule fell in a word space (R3-H1); and
an ordinary cell such as ``Mon - Fri`` made the whole table be refused as having unruled columns
(R3-M1). The check now rebuilds every line from the characters' positions and measures the gap
between neighbours in ems (``geometry.text_layout_problem``).

The first half tests that function on characters placed by hand. The second half uses the real
sandboxed worker on PDFs written by ``tests/pdf_fixtures.py``, with the rule swept through a
sentence so that it falls in every letter gap and every word space.
"""

from __future__ import annotations

import random
import sys

import pytest

from skills.pdf import sandbox
from skills.pdf._worker import geometry
from skills.pdf._worker.geometry import Glyph
from skills.pdf.extract_table import REFUSALS
from tests import pdf_fixtures as fixtures

# -- The pure rule, on characters placed by hand -----------------------------------------------

A, B = (1, 0), (1, 1)  # two neighbouring cells of one row


def _pair(gap_em: float, first: tuple[int, int], second: tuple[int, int], size: float = 10.0):
    """Two characters on one baseline, ``gap_em`` ems apart."""
    return [
        Glyph(700.0, 100.0, 105.0, size, first),
        Glyph(700.0, 105.0 + gap_em * size, 110.0 + gap_em * size, size, second),
    ]


@pytest.mark.parametrize(
    ("gap_em", "expected"),
    [(-0.1, geometry.OVERFLOW), (0.0, geometry.OVERFLOW), (0.28, geometry.OVERFLOW),
     (0.49, geometry.OVERFLOW), (0.5, None), (0.8, None), (3.0, None)],
)  # fmt: skip
def test_neighbours_in_different_cells_must_be_further_apart_than_a_word_space(
    gap_em: float, expected: str | None
) -> None:
    assert geometry.text_layout_problem(_pair(gap_em, A, B)) == expected


@pytest.mark.parametrize(
    ("gap_em", "expected"),
    [(0.0, None), (0.3, None), (0.83, None), (1.0, None), (1.01, geometry.UNRULED),
     (2.2, geometry.UNRULED)],
)  # fmt: skip
def test_neighbours_in_one_cell_are_two_columns_only_beyond_any_run_of_spaces(
    gap_em: float, expected: str | None
) -> None:
    assert geometry.text_layout_problem(_pair(gap_em, A, A)) == expected


def test_the_gap_is_measured_in_the_smaller_font_size_of_the_two() -> None:
    big_then_small = [
        Glyph(700.0, 100.0, 112.0, 12.0, A),
        Glyph(700.0, 112.0 + 4.0, 117.0, 8.0, B),  # 4 pt: 0.5 em of 8 pt, 0.33 em of 12 pt
    ]
    assert geometry.text_layout_problem(big_then_small) is None
    assert (
        geometry.text_layout_problem([big_then_small[0], Glyph(700.0, 112.0 + 3.9, 117.0, 8.0, B)])
        == geometry.OVERFLOW
    )


def test_the_order_the_pdf_drew_the_characters_in_does_not_matter() -> None:
    """A sentence in three objects, drawn in any order, is still one line left to right."""
    line = [
        Glyph(700.0, 100.0 + 6 * k, 106.0 + 6 * k, 10.0, A if k < 5 else B) for k in range(10)
    ]  # the rule falls between two letters
    assert geometry.text_layout_problem(line) == geometry.OVERFLOW
    for seed in range(20):
        shuffled = line[:]
        random.Random(seed).shuffle(shuffled)
        assert geometry.text_layout_problem(shuffled) == geometry.OVERFLOW


def test_characters_on_different_lines_are_not_neighbours() -> None:
    two_lines = [
        Glyph(700.0, 100.0, 106.0, 10.0, A),
        Glyph(688.0, 106.0, 112.0, 10.0, B),  # a line below, directly under the first one's end
    ]
    assert geometry.text_layout_problem(two_lines) is None


def test_a_word_space_that_the_rule_cuts_in_two_is_overflow_not_a_column() -> None:
    """0.28 em between "Needs" and "manager" with the rule in it: two cells, text split."""
    assert geometry.text_layout_problem(_pair(0.28, A, B)) == geometry.OVERFLOW


def test_no_characters_and_one_character_are_no_problem() -> None:
    assert geometry.text_layout_problem([]) is None
    assert geometry.text_layout_problem(_pair(0.0, A, A)[:1]) is None


# -- The real worker --------------------------------------------------------------------------

pytestmark_worker = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox is Linux-only (it fails closed elsewhere)",
)

SENTENCE = "Paid in full on delivery"
NOTE_ROWS = [["Name", "Note", "Qty"], [SENTENCE, "", "10"], ["Bob", "short", "3"]]


def _delivered(pdf: bytes) -> list[list[str]] | None:
    """The rows the product would hand back for ``pdf``, or ``None`` if it hands back nothing.

    The skill's reading and the Inspector's must both succeed and agree cell for cell.
    """
    verified = sandbox.run("verify", pdf)
    if not verified.ok:
        return None
    extracted = sandbox.run("extract", pdf)
    if not extracted.ok or extracted.data["rows"] != verified.data["rows"]:
        return None
    return verified.data["rows"]


@pytestmark_worker
@pytest.mark.parametrize(
    ("text_unit", "column_major", "font_size"),
    [
        ("rich", False, 10),  # a note with one bold word: three text objects
        ("rich", True, 10),
        ("word_space", False, 10),  # a generator that draws word by word
        ("word_space", True, 12),
        ("word", False, 10),
        ("glyph", False, 10),
    ],
)
def test_a_sentence_in_several_text_objects_is_never_delivered_split_across_two_cells(
    text_unit: str, column_major: bool, font_size: int
) -> None:
    """The rule is swept through the sentence in 3-point steps: refused, or the sentence whole.

    (From a first column of 117 points the sentence fits its cell and is delivered whole.)

    Before the fix, the word-by-word and the bold-word forms were handed back split ("Paid in" in
    one cell, "full on delivery" in the next) where the rule fell in a word space.
    """
    wrong = []
    refused = 0
    for first_column in range(30, 120, 3):
        pdf = fixtures.hand_table_pdf(
            NOTE_ROWS,
            cell_width=first_column,
            text_unit=text_unit,
            column_major=column_major,
            font_size=font_size,
        )
        rows = _delivered(pdf)
        if rows is None:
            refused += 1
        elif rows != NOTE_ROWS:
            wrong.append((first_column, rows[1]))
    assert wrong == []
    assert refused > 15, "the sweep never reached the narrow columns, so it proved nothing"


def _space_intervals(text: str, text_unit: str, font_size: int) -> list[tuple[float, float]]:
    """Where the word spaces of ``text`` fall, from the start of the cell's text, in points."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    fonts = {"F1": "Helvetica", "F2": "Helvetica-Bold"}
    objects = fixtures._text_objects(text, text_unit, font_size)
    intervals = []
    for index, (offset, font, piece) in enumerate(objects):
        face = fonts[font]
        for position, character in enumerate(piece):
            if character == " ":
                start = offset + stringWidth(piece[:position], face, font_size)
                intervals.append((start, start + stringWidth(" ", face, font_size)))
        if index + 1 < len(objects):  # a space the PDF leaves to the position of the next object
            end = offset + stringWidth(piece, face, font_size)
            if objects[index + 1][0] > end + 0.01:
                intervals.append((end, objects[index + 1][0]))
    return intervals


@pytestmark_worker
@pytest.mark.parametrize("text_unit", ["rich", "word_space", "word"])
@pytest.mark.parametrize("sentence", ["Needs manager approval", SENTENCE])
def test_a_rule_that_falls_in_a_word_space_never_leaves_the_sentence_split(
    text_unit: str, sentence: str
) -> None:
    """The case the review found: the rule exactly in a word space, at its start, middle and end.

    A coarse sweep can step over a 3-point space; this puts the rule in every one of them.
    """
    rows = [["Name", "Note", "Qty"], [sentence, "", "10"], ["Bob", "short", "3"]]
    intervals = _space_intervals(sentence, text_unit, 10)
    assert len(intervals) == sentence.count(" ")
    wrong, refused, tried = [], 0, 0
    for start, end in intervals:
        for where in (start, (start + end) / 2, end):
            tried += 1
            pdf = fixtures.hand_table_pdf(rows, cell_width=4 + where, text_unit=text_unit)
            if where == (start + end) / 2:  # nothing crosses the rule: the line check decides
                assert sandbox.run("verify", pdf).reason == "text_overflows"
            delivered = _delivered(pdf)
            if delivered is None:
                refused += 1
            elif delivered != rows:
                wrong.append((round(where, 2), delivered[1]))
    assert wrong == []
    assert refused == tried, "a rule in a word space of a sentence must never be delivered"


@pytestmark_worker
@pytest.mark.parametrize(
    "text",
    [
        "Mon - Fri",
        "Mon – Fri",
        "Mon — Fri",
        "Smith - one",
        "low - high",
        "x ~ y",
        "9:00 - 17:00",
        "A  B",
        "Inc., Ltd",
        "re: memo",
    ],
)
def test_a_cell_with_a_dash_or_a_double_space_in_a_ruled_grid_is_delivered_whole(
    text: str,
) -> None:
    """Round 3, R3-M1: the table was refused as "columns not ruled" for these."""
    rows = [["Shop", "Open"], ["North", text], ["South", "ok"]]
    pdf = fixtures.hand_table_pdf(rows, cell_width=100)
    assert _delivered(pdf) == [list(map(_squashed, row)) for row in rows]


def _squashed(text: str) -> str:
    return " ".join(text.split())  # a cell's normal form: runs of spaces are one space


@pytestmark_worker
def test_the_refusal_for_text_close_to_a_border_names_the_real_reason() -> None:
    pdf = fixtures.hand_table_pdf(NOTE_ROWS, cell_width=100, text_unit="word_space")
    assert sandbox.run("verify", pdf).reason == "text_overflows"
    sentence = REFUSALS["text_overflows"]
    assert "too close to a cell's border" in sentence and "which cell it belongs to" in sentence
    assert "ruled" not in sentence  # it must never say the columns aren't ruled
    assert "ruled" in REFUSALS["columns_not_ruled"]
