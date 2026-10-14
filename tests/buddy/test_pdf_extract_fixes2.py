"""Tests for the findings of review round 2 of feat/pdf-extract.

The findings are in ``docs/reviews/feat-pdf-extract-review.md``. Same setting as
``test_pdf_extract``: the real pipeline and the real sandboxed worker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl
import pytest

from buddy import planner
from buddy.models import Understanding
from skills.base import SkillTask
from skills.excel import ExcelDeduplicateSkill, ExcelRemoveEmptyRowsSkill
from skills.pdf import sandbox
from skills.pdf.extract_table import REFUSALS
from tests import pdf_fixtures as fixtures
from tests.buddy.test_engine_factory import _chain_replies
from tests.buddy.test_pdf_extract import _run

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox is Linux-only (it fails closed elsewhere)",
)


@pytest.fixture(autouse=True)
def _work_in_a_temporary_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


# -- R2-H1: a sentence that runs into the empty cell beside it -------------------------------


def _sentence_into_an_empty_cell(sentence: str, first_column: int) -> bytes:
    """A 3-column hand-drawn grid; row 1's first cell holds ``sentence``, the next cell is empty."""
    return fixtures.hand_table_pdf(
        [["Name", "Note", "Qty"], [sentence, "", "10"], ["Bob", "short", "3"]],
        cell_width=first_column,
    )


@pytest.mark.parametrize(
    "sentence", ["Ships next week", "Left at the front desk", "Paid in full on delivery"]
)
def test_a_sentence_is_never_delivered_split_across_two_cells_wherever_the_rule_falls(
    sentence: str,
) -> None:
    """The rule is swept through the sentence in 2-point steps: never delivered split."""
    wrong = []
    for first_column in range(30, 170, 2):
        pdf = _sentence_into_an_empty_cell(sentence, first_column)
        verified = sandbox.run("verify", pdf)
        if not verified.ok:
            continue  # refused: the table is not delivered
        row = verified.data["rows"][1]
        if row != [sentence, "", "10"]:  # delivered, so it has to be the sentence whole
            wrong.append((first_column, row))
    assert wrong == []


def test_one_string_drawn_across_two_cells_is_refused_even_between_two_letters() -> None:
    pdf = _sentence_into_an_empty_cell("Paid in full on delivery", 100)
    # whatever the verifier says about this width, no split row may be handed back
    verified = sandbox.run("verify", pdf)
    assert not verified.ok or verified.data["rows"][1] == ["Paid in full on delivery", "", "10"]


# -- R2-M1: columns that are only partly ruled -----------------------------------------------


def test_a_table_with_one_ruled_column_and_the_others_not_is_refused(tmp_path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    table = Table([list(row) for row in fixtures.FRUIT], colWidths=[100, 60, 60])
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, colors.black),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.black),
                ("LINEAFTER", (0, 0), (0, -1), 0.5, colors.black),
            ]
        )
    )
    pdf = tmp_path / "partly-ruled.pdf"
    SimpleDocTemplate(str(pdf)).build([table])
    assert sandbox.run("verify", pdf.read_bytes()).reason == "columns_not_ruled"
    state, _, _ = _run(tmp_path, lambda p: p.write_bytes(pdf.read_bytes()))
    assert state.execution_result is not None and state.execution_result.outputs == ()
    assert REFUSALS["columns_not_ruled"] in (state.reply or "")


def test_words_with_ordinary_spaces_in_a_cell_are_not_two_columns() -> None:
    rows = [["Name", "Note"], ["Alice Smith", "paid in full on delivery"], ["B", "ok"]]
    assert sandbox.run("verify", fixtures.hand_table_pdf(rows, cell_width=200)).ok


# -- R2-M2: "extract the table and give me a report" on a PDF -------------------------------


@pytest.mark.parametrize("speaker", ["template", "friend", "focus"])
def test_a_report_asked_with_the_extraction_hands_the_table_back_and_says_no_report(
    tmp_path: Path, speaker: str
) -> None:
    replies = _chain_replies(
        ("pdf.extract_table", "extract the table"), ("spreadsheet.analyze", "give me a report")
    )
    replies[0] = json.dumps(  # Brain's reading: both requests
        {
            "intent": "multi_step_request",
            "confidence": 0.9,
            "requested_actions": [
                {"action": "extract_pdf_table", "source_phrase": "extract the table"},
                {"action": "report_on_file", "source_phrase": "give me a report"},
            ],
        }
    )
    state, _, _ = _run(
        tmp_path,
        lambda p: fixtures.write_table_pdf(p),
        message="extract the table and give me a report",
        replies=replies,
        speaker=speaker,
    )
    assert state.execution_result is not None and len(state.execution_result.outputs) == 1
    reply = state.reply or ""
    assert "Partly done — extracted the table" in reply
    assert "I couldn't produce the report you asked for" in reply
    for leak in ("No skill supporting", "-task-", "analyze couldn't be run"):
        assert leak not in reply.replace(
            state.execution_result.outputs[0].split("/")[-1], ""
        ), reply


def test_no_analysis_step_is_planned_on_a_pdf_and_the_rest_of_the_plan_stays() -> None:
    from buddy.models import PlanningStep

    steps = (
        PlanningStep("step-1", "extract", "pdf.extract_table", 0),
        PlanningStep("step-2", "report", "spreadsheet.analyze", 1, ("step-1",)),
    )

    class Inner:
        def __call__(self, request):  # type: ignore[no-untyped-def]
            return steps

    def plan(active: str):  # type: ignore[no-untyped-def]
        request = planner.PlanningRequest(
            Understanding(),
            planner.Reasoning(),
            (active,),
            active,
            "extract and report",
            unanalyzed_spreadsheet_files=(active,) if active.endswith(".xlsx") else (),
        )
        return planner.ProactiveAnalysisPlanner(Inner())(request)

    assert [s.capability for s in plan("uploads/a.pdf")] == ["pdf.extract_table"]
    assert plan("uploads/a.pdf")[0].dependencies == ()
    # On a workbook nothing changes: the analysis is still planned (first, once).
    assert "spreadsheet.analyze" in [s.capability for s in plan("uploads/a.xlsx")]


# -- R2-M3: no handle on the server ---------------------------------------------------------


def test_the_worker_cannot_signal_get_a_handle_on_or_take_a_socket_from_the_server() -> None:
    data = sandbox.run("selftest", b"").data
    for key in (
        "signal_to_parent_refused",
        "pidfd_open_refused",
        "pidfd_getfd_refused",
        "process_vm_readv_refused",
    ):
        assert data[key] is True, key
    assert data["signal_to_self_works"] is True


# -- R2-L1: guards the survivors of round 2's mutations -----------------------------------------


def _ids_workbook(path: Path) -> Path:
    book = openpyxl.Workbook()
    for row in (["id", "n"], ["007", "a"], ["008", "b"], ["008", "b"], [None, None], ["009", "c"]):
        book.active.append(row)
    for row in book.active.iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    book.save(path)
    return path


@pytest.mark.parametrize("skill_class", [ExcelDeduplicateSkill, ExcelRemoveEmptyRowsSkill])
def test_the_real_cleaning_skills_keep_a_text_id_as_text(tmp_path: Path, skill_class: type) -> None:
    path = _ids_workbook(tmp_path / "ids.xlsx")
    result = skill_class().execute(
        SkillTask("t", "s", "w", "d", input_references=[str(path)], output_dir=str(tmp_path / "o"))
    )
    book = openpyxl.load_workbook(result.outputs[0])
    ids = [row[0].value for row in book.active.iter_rows(min_row=2)]
    assert "007" in ids and 7 not in ids
