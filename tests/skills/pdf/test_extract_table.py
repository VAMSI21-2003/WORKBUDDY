"""``PdfExtractTableSkill`` on real PDFs: what it writes, what it refuses, what it never touches."""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import openpyxl
import pytest

from skills.base import PermissionTier, SkillResult, SkillStatus, SkillTask
from skills.pdf import PdfExtractTableSkill, sandbox
from skills.pdf.extract_table import REFUSALS
from tests import pdf_fixtures as fixtures

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox is Linux-only (it fails closed elsewhere)",
)


def _task(pdf: Path, tmp_path: Path, task_id: str = "t-0") -> SkillTask:
    return SkillTask(
        task_id=task_id,
        session_id="s-1",
        workspace_id="w-1",
        description="extract the table",
        input_references=[str(pdf)],
        permission=PermissionTier.WRITE,
        output_dir=str(tmp_path / "out"),
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cells(path: str) -> list[list[object]]:
    book = openpyxl.load_workbook(path)  # not read-only: the cell types are what is asked
    try:
        return [[c.value for c in row] for row in book.worksheets[0].iter_rows()]
    finally:
        book.close()


def _run(pdf: Path, tmp_path: Path) -> tuple[SkillResult, str]:
    before = _sha(pdf)
    result = PdfExtractTableSkill().execute(_task(pdf, tmp_path))
    assert _sha(pdf) == before  # rule 2: the PDF is byte-identical afterwards
    return result, before


def test_the_skill_is_write_tier_and_declares_its_capability() -> None:
    skill = PdfExtractTableSkill()
    assert skill.permission_tier is PermissionTier.WRITE
    assert skill.supported_inputs == ("pdf.extract_table",)
    assert skill.name == "pdf.extract_table"


def test_can_handle_only_a_single_pdf(tmp_path: Path) -> None:
    skill = PdfExtractTableSkill()
    pdf = tmp_path / "a.pdf"
    assert skill.can_handle(_task(pdf, tmp_path))
    assert skill.can_handle(_task(tmp_path / "A.PDF", tmp_path))
    assert not skill.can_handle(_task(tmp_path / "a.xlsx", tmp_path))
    two = _task(pdf, tmp_path)
    two.input_references.append(str(tmp_path / "b.pdf"))
    assert not skill.can_handle(two)
    assert not skill.can_handle(SkillTask("t", "s", "w", "d", input_references=[]))


def test_a_plain_table_becomes_a_new_text_only_workbook(tmp_path: Path) -> None:
    pdf = fixtures.write_table_pdf(tmp_path / "fruit.pdf")
    result, _ = _run(pdf, tmp_path)

    assert result.status is SkillStatus.SUCCESS
    (output,) = result.outputs
    assert Path(output) != pdf and Path(output).suffix == ".xlsx"
    assert Path(output).parent == tmp_path / "out"
    assert Path(output).name == "fruit_table_t-0.xlsx"
    assert _cells(output) == fixtures.FRUIT  # every value a string, "2.50" not 2.5
    assert PdfExtractTableSkill().verify(_task(pdf, tmp_path), result)
    book = openpyxl.load_workbook(output, read_only=True)
    assert book.sheetnames == ["Table"]
    book.close()


def test_the_title_above_the_table_is_not_part_of_it(tmp_path: Path) -> None:
    result, _ = _run(
        fixtures.write_table_pdf(tmp_path / "a.pdf", title="Quarterly report"), tmp_path
    )
    assert all("Quarterly" not in str(v) for row in _cells(result.outputs[0]) for v in row)


def test_a_table_on_page_one_of_twenty_is_read(tmp_path: Path) -> None:
    pdf = fixtures.write_table_pdf(tmp_path / "long.pdf", text_pages_after=19)
    result, _ = _run(pdf, tmp_path)
    assert result.status is SkillStatus.SUCCESS
    assert _cells(result.outputs[0]) == fixtures.FRUIT


@pytest.mark.parametrize(
    "rows",
    [
        # empty cells, including a whole empty row in the middle and an empty first column
        [["", "B", "C"], ["", "", ""], ["x", "", "z"], ["y", "2", ""]],
        # leading zeros, thousands separators, signs, a percentage, a date: all stay text
        [
            ["Code", "Amount", "Date"],
            ["007", "1,234.50", "2026-10-12"],
            ["-5", "12%", "01/02/2026"],
        ],
        # text that spreadsheets treat as a formula or a command
        [["A", "B"], ["=SUM(A1:A9)", "+1+1"], ["@SUM(1)", "-2"]],
        # accents and non-Latin text
        [["Name", "City"], ["Zoë", "Kraków"], ["Łukasz", "Αθήνα"]],
    ],
    ids=["empty-cells", "numbers-and-dates", "formula-like", "unicode"],
)
def test_every_cell_is_the_text_the_pdf_has(tmp_path: Path, rows: list[list[str]]) -> None:
    # Text with characters Helvetica can't draw is skipped by the hand-built writer; use
    # reportlab (which embeds a font) for the Unicode case and the hand-built one otherwise.
    if any(not cell.isascii() for row in rows for cell in row):
        pytest.skip("covered by test_non_latin_text_comes_through below")
    pdf = tmp_path / "cells.pdf"
    pdf.write_bytes(fixtures.hand_table_pdf(rows))
    result, _ = _run(pdf, tmp_path)
    assert result.status is SkillStatus.SUCCESS, result.errors
    expected = [[cell if cell != "" else None for cell in row] for row in rows]
    assert _cells(result.outputs[0]) == expected


def test_a_cell_that_reads_as_a_formula_is_stored_as_text_not_a_formula(tmp_path: Path) -> None:
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(fixtures.hand_table_pdf([["A", "B"], ["=1+1", '=HYPERLINK("http://x")']]))
    result, _ = _run(pdf, tmp_path)
    (output,) = result.outputs
    with zipfile.ZipFile(output) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet and "<f " not in sheet  # no formula element anywhere
    book = openpyxl.load_workbook(output)
    cell = book.worksheets[0]["A2"]
    assert (cell.value, cell.data_type) == ("=1+1", "s")


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (fixtures.write_scanned_pdf, "no_text"),
        (fixtures.write_text_only_pdf, "no_table"),
        (fixtures.write_two_tables_pdf, "many_tables"),
        (lambda p: fixtures.write_two_tables_pdf(p, same_page=False), "many_tables"),
        (fixtures.write_table_across_pages_pdf, "many_tables"),
        (lambda p: fixtures.write_table_pdf(p, spans=[((0, 1), (1, 1))]), "not_plain"),
        (lambda p: fixtures.write_table_pdf(p, spans=[((1, 1), (1, 2))]), "not_plain"),
        (lambda p: fixtures.write_table_pdf(p, password=("secret", "owner")), "encrypted"),
        (lambda p: fixtures.write_table_pdf(p, password=("", "owner")), "encrypted"),
    ],
    ids=[
        "scanned",
        "no-table",
        "two-tables-one-page",
        "two-tables-two-pages",
        "table-per-page",
        "merged-across",
        "merged-down",
        "encrypted-with-password",
        "encrypted-no-user-password",
    ],
)
def test_a_pdf_the_skill_cannot_take_is_refused_with_a_fixed_sentence_and_no_file(
    tmp_path: Path, make, reason: str  # type: ignore[no-untyped-def]
) -> None:
    pdf = make(tmp_path / "x.pdf")
    result, _ = _run(pdf, tmp_path)

    assert result.status is SkillStatus.FAILURE
    assert result.outputs == []
    assert result.errors == [REFUSALS[reason]]
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())
    assert not PdfExtractTableSkill().verify(_task(pdf, tmp_path), result)


def test_a_merged_cell_drawn_by_hand_is_not_plain(tmp_path: Path) -> None:
    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(fixtures.hand_table_pdf(fixtures.FRUIT, skip_lines=["v:1:2"]))
    result, _ = _run(pdf, tmp_path)
    assert result.errors == [REFUSALS["not_plain"]]


def test_more_than_twenty_pages_is_refused(tmp_path: Path) -> None:
    pdf = fixtures.write_table_pdf(tmp_path / "long.pdf", text_pages_after=20)  # 21 pages
    result, _ = _run(pdf, tmp_path)
    assert result.errors == [REFUSALS["too_many_pages"]]
    assert str(sandbox.MAX_PAGES) in REFUSALS["too_many_pages"]


def test_exactly_twenty_pages_is_read(tmp_path: Path) -> None:
    pdf = fixtures.write_table_pdf(tmp_path / "long.pdf", text_pages_after=19)  # 20 pages
    assert _run(pdf, tmp_path)[0].status is SkillStatus.SUCCESS


def test_a_refusal_never_carries_the_files_words(tmp_path: Path) -> None:
    pdf = tmp_path / "secret.pdf"
    pdf.write_bytes(
        fixtures.hand_table_pdf([["TOPSECRETWORD", "b"], ["c", "d"]], skip_lines=["v:1:1"])
    )
    result, _ = _run(pdf, tmp_path)
    assert result.status is SkillStatus.FAILURE
    assert "TOPSECRETWORD" not in " ".join(result.errors + [result.summary])
    assert all(error in REFUSALS.values() for error in result.errors)


def test_a_missing_or_unreadable_input_is_a_refusal_not_an_exception(tmp_path: Path) -> None:
    result = PdfExtractTableSkill().execute(_task(tmp_path / "nope.pdf", tmp_path))
    assert result.status is SkillStatus.FAILURE
    assert result.errors == [REFUSALS["unreadable"]]


def test_a_file_that_is_not_a_pdf_is_a_refusal(tmp_path: Path) -> None:
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"this is not a pdf at all" * 100)
    result, _ = _run(junk, tmp_path)
    assert result.errors == [REFUSALS["unreadable"]]


def test_a_pdf_with_active_content_is_read_and_none_of_it_is_followed(tmp_path: Path) -> None:
    pdf = tmp_path / "active.pdf"
    pdf.write_bytes(fixtures.hand_table_pdf(fixtures.FRUIT, active_content=True))
    before = sorted(p.name for p in tmp_path.iterdir())
    result, _ = _run(pdf, tmp_path)
    assert result.status is SkillStatus.SUCCESS
    assert _cells(result.outputs[0]) == fixtures.FRUIT
    # The embedded file was not written anywhere, and nothing else appeared beside the PDF.
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "out") == before


def test_a_failed_write_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import skills.pdf.extract_table as module

    def broken(rows, output_path):  # type: ignore[no-untyped-def]
        output_path.write_bytes(b"half a workbook")
        raise OSError("disk full")

    monkeypatch.setattr(module, "_write_text_cells", broken)
    pdf = fixtures.write_table_pdf(tmp_path / "a.pdf")
    result = PdfExtractTableSkill().execute(_task(pdf, tmp_path))
    assert result.status is SkillStatus.FAILURE and result.outputs == []
    assert list((tmp_path / "out").iterdir()) == []


def test_a_worker_answer_that_is_not_a_rectangle_of_text_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = fixtures.write_table_pdf(tmp_path / "a.pdf")
    for rows in ([["a", "b"], ["c"]], [["a", 1]], [], [[]], "text", [["a\nb"]], [[" a"]]):
        monkeypatch.setattr(
            sandbox,
            "run",
            lambda mode, data, _rows=rows, **kw: sandbox.WorkerOutcome(True, data={"rows": _rows}),
        )
        result = PdfExtractTableSkill().execute(_task(pdf, tmp_path))
        assert result.status is SkillStatus.FAILURE and result.outputs == [], rows


def test_the_sandbox_being_unavailable_is_said_plainly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    pdf = fixtures.write_table_pdf(tmp_path / "a.pdf")
    result = PdfExtractTableSkill().execute(_task(pdf, tmp_path))
    assert result.errors == [REFUSALS["sandbox_unavailable"]]


def test_every_reason_the_sandbox_can_give_has_a_sentence() -> None:
    for reason in sandbox.REASONS:
        assert reason in REFUSALS, reason
    assert set(REFUSALS) == set(sandbox.REASONS)


def test_non_latin_text_comes_through(tmp_path: Path) -> None:
    rows = [["Name", "City"], ["Zoë", "Kraków"], ["Łukasz", "Zürich"]]
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    font = next(
        (p for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",) if Path(p).exists()), None
    )
    if font is None:
        pytest.skip("no TrueType font on this machine")
    pdfmetrics.registerFont(TTFont("DejaVu", font))
    table = Table(rows)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
            ]
        )
    )
    pdf = tmp_path / "u.pdf"
    SimpleDocTemplate(str(pdf), pagesize=A4).build([table])
    result, _ = _run(pdf, tmp_path)
    assert result.status is SkillStatus.SUCCESS, result.errors
    assert _cells(result.outputs[0]) == rows
