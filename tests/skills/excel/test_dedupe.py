"""Unit tests for skills.excel.dedupe.

These tests exercise real ``.xlsx`` files on disk via pandas — no part of
pandas is mocked — so they prove the skill's read/write behavior against
an actual spreadsheet with known duplicate rows, not against an assumption
of how pandas behaves.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from skills.base import PermissionTier, SkillResult, SkillStatus, SkillTask
from skills.excel.dedupe import ExcelDeduplicateSkill


def _write_fixture_with_duplicates(path: Path) -> pd.DataFrame:
    """Write a real ``.xlsx`` file to ``path`` with known duplicate rows.

    5 rows, 2 of which are exact duplicates of earlier rows, leaving 3
    distinct rows after deduplication.
    """
    df = pd.DataFrame(
        {
            "name": ["Alice", "Bob", "Alice", "Carol", "Bob"],
            "amount": [10, 20, 10, 30, 20],
        }
    )
    df.to_excel(path, index=False)
    return df


def _make_task(
    input_path: Path, *, task_id: str = "task-1", workspace_id: str = "workspace-1"
) -> SkillTask:
    return SkillTask(
        task_id=task_id,
        session_id="session-1",
        workspace_id=workspace_id,
        description="remove duplicate rows",
        input_references=[str(input_path)],
        permission=PermissionTier.WRITE,
    )


# -- can_handle ---------------------------------------------------------------


def test_can_handle_true_for_single_xlsx_input(tmp_path: Path) -> None:
    input_path = tmp_path / "report.xlsx"
    _write_fixture_with_duplicates(input_path)
    skill = ExcelDeduplicateSkill()

    assert skill.can_handle(_make_task(input_path)) is True


def test_can_handle_false_for_multiple_inputs(tmp_path: Path) -> None:
    input_path = tmp_path / "report.xlsx"
    _write_fixture_with_duplicates(input_path)
    skill = ExcelDeduplicateSkill()
    task = _make_task(input_path)
    task.input_references.append(str(tmp_path / "other.xlsx"))

    assert skill.can_handle(task) is False


def test_can_handle_false_for_unsupported_extension(tmp_path: Path) -> None:
    input_path = tmp_path / "report.csv"
    skill = ExcelDeduplicateSkill()

    assert skill.can_handle(_make_task(input_path)) is False


# -- execute --------------------------------------------------------------------


def test_execute_removes_duplicate_rows_from_real_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "uploads" / "report.xlsx"
    input_path.parent.mkdir(parents=True)
    original = _write_fixture_with_duplicates(input_path)
    skill = ExcelDeduplicateSkill()
    task = _make_task(input_path)

    result = skill.execute(task)

    assert result.status is SkillStatus.SUCCESS
    assert len(result.outputs) == 1
    output_path = Path(result.outputs[0])
    assert output_path.exists()

    # Independent re-read of the real output file on disk — not trusting
    # the skill's own metadata about what it did.
    written = pd.read_excel(output_path)
    assert len(written) == 3
    assert int(written.duplicated().sum()) == 0
    assert result.metadata["removed_count"] == len(original) - 3
    assert result.metadata["original_row_count"] == len(original)
    assert result.metadata["output_row_count"] == 3


def test_execute_never_writes_to_input_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "uploads" / "report.xlsx"
    input_path.parent.mkdir(parents=True)
    _write_fixture_with_duplicates(input_path)
    original_bytes = input_path.read_bytes()
    skill = ExcelDeduplicateSkill()
    task = _make_task(input_path)

    result = skill.execute(task)

    # The upload is byte-for-byte unchanged, and the output landed at a
    # different path entirely.
    assert input_path.read_bytes() == original_bytes
    assert Path(result.outputs[0]) != input_path
    assert len(pd.read_excel(input_path)) == 5  # still has its original duplicates


def test_execute_output_path_is_under_workspace_outputs_for_workspace_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "uploads" / "report.xlsx"
    input_path.parent.mkdir(parents=True)
    _write_fixture_with_duplicates(input_path)
    skill = ExcelDeduplicateSkill()
    task = _make_task(input_path, workspace_id="workspace-42")

    result = skill.execute(task)

    # The skill returns a relative path (resolved against cwd for actual
    # I/O); resolve it here before comparing against the absolute tmp_path.
    output_path = Path(result.outputs[0]).resolve()
    assert output_path.is_relative_to((tmp_path / "workspace" / "outputs" / "workspace-42").resolve())


def test_execute_returns_failure_for_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    missing_path = tmp_path / "uploads" / "does-not-exist.xlsx"
    skill = ExcelDeduplicateSkill()
    task = _make_task(missing_path)

    result = skill.execute(task)

    assert result.status is SkillStatus.FAILURE
    assert result.errors


def test_execute_returns_failure_when_task_not_handleable(tmp_path: Path) -> None:
    input_path = tmp_path / "report.csv"
    input_path.write_text("a,b\n1,2\n")
    skill = ExcelDeduplicateSkill()
    task = _make_task(input_path)

    result = skill.execute(task)

    assert result.status is SkillStatus.FAILURE
    assert result.errors


# -- verify -----------------------------------------------------------------------


def test_verify_true_for_well_formed_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "uploads" / "report.xlsx"
    input_path.parent.mkdir(parents=True)
    _write_fixture_with_duplicates(input_path)
    skill = ExcelDeduplicateSkill()
    task = _make_task(input_path)
    result = skill.execute(task)

    assert skill.verify(task, result) is True


def test_verify_false_for_failure_status(tmp_path: Path) -> None:
    skill = ExcelDeduplicateSkill()
    task = _make_task(tmp_path / "report.xlsx")  # file doesn't exist
    failed_result = skill.execute(task)

    assert failed_result.status is SkillStatus.FAILURE
    assert skill.verify(task, failed_result) is False


def test_verify_false_when_output_file_missing(tmp_path: Path) -> None:
    skill = ExcelDeduplicateSkill()
    task = _make_task(tmp_path / "report.xlsx")
    fabricated_result = SkillResult(
        status=SkillStatus.SUCCESS, outputs=[str(tmp_path / "nonexistent_output.xlsx")]
    )

    assert skill.verify(task, fabricated_result) is False
