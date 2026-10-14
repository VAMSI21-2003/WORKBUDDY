"""Tests for the findings of review round 3 of feat/pdf-extract, through the real pipeline.

The findings are in ``docs/reviews/feat-pdf-extract-review.md``. The layout check itself is tested
in ``tests/skills/pdf/test_text_layout.py`` and the sandbox's new refusals in
``tests/skills/pdf/test_sandbox.py``; here is what a person sees: the table, or the real reason it
wasn't handed back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from buddy.voice import _COMPLETION_CLAIM
from skills.pdf import sandbox
from skills.pdf.extract_table import REFUSALS
from tests import pdf_fixtures as fixtures
from tests.buddy.test_pdf_extract import SPEAKERS, _cells, _run

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox is Linux-only (it fails closed elsewhere)",
)

SHOPS = [["Shop", "Open", "Notes"], ["North", "Mon - Fri", "low - high"], ["South", "A  B", "ok"]]


@pytest.fixture(autouse=True)
def _work_in_a_temporary_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_ruled_table_with_dashes_and_double_spaces_in_its_cells_is_delivered(
    tmp_path: Path, speaker: str
) -> None:
    """R3-M1: this table was refused, with a reason that was not true ("columns aren't ruled")."""
    state, _, _ = _run(
        tmp_path,
        lambda p: p.write_bytes(fixtures.hand_table_pdf(SHOPS, cell_width=100)),
        speaker=speaker,
    )
    result = state.execution_result
    assert result is not None and result.success and len(result.outputs) == 1
    assert _cells(result.outputs[0]) == [
        ["Shop", "Open", "Notes"],
        ["North", "Mon - Fri", "low - high"],
        ["South", "A B", "ok"],  # runs of spaces are one space in a cell's normal form
    ]
    assert "ruled" not in (state.reply or "")


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_sentence_the_rule_cuts_in_two_is_refused_for_its_real_reason(
    tmp_path: Path, speaker: str
) -> None:
    """R3-H1: a note with one bold word, split across two cells, was delivered as if it were whole.

    Now no file is handed back, and the reply says what is the matter: text so close to a border
    that the cell it belongs to can't be told. It must not say the columns aren't ruled.
    """
    rows = [["Name", "Note", "Qty"], ["Needs manager approval", "", "10"], ["Bob", "short", "3"]]
    pdf = fixtures.hand_table_pdf(rows, cell_width=70, text_unit="rich")
    assert sandbox.run("verify", pdf).reason == "text_overflows"
    state, _, _ = _run(tmp_path, lambda p: p.write_bytes(pdf), speaker=speaker)
    assert state.execution_result is not None and state.execution_result.outputs == ()
    reply = state.reply or ""
    assert REFUSALS["text_overflows"] in reply
    assert "which cell it belongs to" in reply and "too close" in reply
    assert "ruled" not in reply and "Needs" not in reply  # nothing from the file reaches the reply
    assert "no file was handed back" in reply
    assert _COMPLETION_CLAIM.search(reply) is None, reply
