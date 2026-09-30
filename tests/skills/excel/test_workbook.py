"""Unit tests for the single-sheet check (``skills.excel.workbook``) and the
two row-cleaning skills' refusal of a multi-sheet workbook (backlog item 1a).

Real workbooks on disk; only ``xlrd`` is faked, since it isn't installed.
"""

from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from openpyxl.chart import BarChart, Reference

from skills.base import PermissionTier, SkillStatus, SkillTask
from skills.excel.dedupe import ExcelDeduplicateSkill
from skills.excel.remove_empty_rows import ExcelRemoveEmptyRowsSkill
from skills.excel.workbook import WorkbookRefused, single_sheet_name

_ROWS = {"name": ["Alice", "Bob", None, "Alice"], "amount": [1, 2, None, 1]}
_OLE2_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504


def _write_workbook(path: Path, *sheet_names: str) -> Path:
    with pd.ExcelWriter(path) as writer:
        for name in sheet_names:
            pd.DataFrame(_ROWS).to_excel(writer, sheet_name=name, index=False)
    return path


def _refusal(count: int) -> str:
    return (
        f"the workbook has {count} sheets, and only single-sheet workbooks are supported "
        "for now"
    )


# -- single_sheet_name -------------------------------------------------------------


def test_single_sheet_name_returns_the_only_sheets_name(tmp_path: Path) -> None:
    assert single_sheet_name(str(_write_workbook(tmp_path / "r.xlsx", "Data"))) == "Data"


@pytest.mark.parametrize("names", [("Q1", "Q2"), ("Q1", "Q2", "Q3")])
def test_single_sheet_name_refuses_a_multi_sheet_workbook(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    path = _write_workbook(tmp_path / "r.xlsx", *names)
    before = path.read_bytes()

    with pytest.raises(WorkbookRefused) as refused:
        single_sheet_name(str(path))

    assert str(refused.value) == _refusal(len(names))
    assert path.read_bytes() == before  # opened read-only


def test_single_sheet_name_counts_a_chart_sheet(tmp_path: Path) -> None:
    """A chart sheet would be lost as well, so it counts as a sheet."""
    book = openpyxl.Workbook()
    book.active.append([1])
    book.active.append([2])
    chart = BarChart()
    chart.add_data(Reference(book.active, min_col=1, min_row=1, max_row=2))
    book.create_chartsheet("Chart").add_chart(chart)
    book.save(tmp_path / "r.xlsx")

    with pytest.raises(WorkbookRefused, match="has 2 sheets"):
        single_sheet_name(str(tmp_path / "r.xlsx"))


def test_single_sheet_name_reads_an_xls_name_holding_xlsx_content(tmp_path: Path) -> None:
    """The format comes from the content: openpyxl counts these sheets."""
    path = _write_workbook(tmp_path / "book.xlsx", "Q1", "Q2").rename(tmp_path / "r.xls")

    with pytest.raises(WorkbookRefused, match="has 2 sheets"):
        single_sheet_name(str(path))


def test_single_sheet_name_fails_honestly_on_a_legacy_xls_without_xlrd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "xlrd", None)  # "import xlrd" raises ImportError
    path = tmp_path / "r.xls"
    path.write_bytes(_OLE2_HEADER)

    with pytest.raises(WorkbookRefused) as refused:
        single_sheet_name(str(path))

    assert str(refused.value) == (
        "the sheets in an .xls workbook can't be counted because the xlrd library isn't "
        "installed"
    )


@pytest.mark.parametrize(("names", "refused"), [(["Only"], False), (["A", "B"], True)])
def test_single_sheet_name_counts_a_legacy_xls_with_xlrd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str], refused: bool
) -> None:
    released: list[bool] = []
    book = types.SimpleNamespace(
        sheet_names=lambda: names, release_resources=lambda: released.append(True)
    )
    fake_xlrd = types.SimpleNamespace(open_workbook=lambda path, on_demand: book)
    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)
    path = tmp_path / "r.xls"
    path.write_bytes(_OLE2_HEADER)

    if refused:
        with pytest.raises(WorkbookRefused, match="has 2 sheets"):
            single_sheet_name(str(path))
    else:
        assert single_sheet_name(str(path)) == "Only"
    assert released == [True]


def test_single_sheet_name_refuses_a_file_that_isnt_a_workbook(tmp_path: Path) -> None:
    path = tmp_path / "r.xlsx"
    path.write_text("name,amount\n")

    with pytest.raises(WorkbookRefused, match="isn't an Excel workbook"):
        single_sheet_name(str(path))


def test_single_sheet_name_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WorkbookRefused, match="could not read"):
        single_sheet_name(str(tmp_path / "missing.xlsx"))


# -- The skills --------------------------------------------------------------------

_SKILLS = [ExcelDeduplicateSkill, ExcelRemoveEmptyRowsSkill]


def _task(path: Path) -> SkillTask:
    return SkillTask(
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        description="clean it",
        input_references=[str(path)],
        permission=PermissionTier.WRITE,
    )


@pytest.mark.parametrize("skill_class", _SKILLS)
@pytest.mark.parametrize("names", [("Q1", "Q2"), ("Q1", "Q2", "Q3")])
def test_skill_refuses_a_multi_sheet_workbook_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skill_class: type, names: tuple[str, ...]
) -> None:
    monkeypatch.chdir(tmp_path)
    path = _write_workbook(tmp_path / "report.xlsx", *names)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    skill = skill_class()

    result = skill.execute(_task(path))

    assert result.status is SkillStatus.FAILURE
    assert result.errors == [_refusal(len(names))]
    assert result.outputs == []
    assert not (tmp_path / "workspace").exists()
    assert skill.verify(_task(path), result) is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("skill_class", _SKILLS)
def test_skill_fails_honestly_on_a_legacy_xls_without_xlrd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skill_class: type
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "xlrd", None)
    path = tmp_path / "report.xls"
    path.write_bytes(_OLE2_HEADER)

    result = skill_class().execute(_task(path))

    assert result.status is SkillStatus.FAILURE
    assert result.errors == [
        "the sheets in an .xls workbook can't be counted because the xlrd library isn't "
        "installed"
    ]
    assert not (tmp_path / "workspace").exists()


@pytest.mark.parametrize("skill_class", _SKILLS)
def test_skill_keeps_the_only_sheets_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skill_class: type
) -> None:
    monkeypatch.chdir(tmp_path)
    path = _write_workbook(tmp_path / "report.xlsx", "Data")

    result = skill_class().execute(_task(path))

    assert result.status is SkillStatus.SUCCESS
    assert pd.ExcelFile(result.outputs[0]).sheet_names == ["Data"]
    assert result.metadata["removed_count"] == 1
