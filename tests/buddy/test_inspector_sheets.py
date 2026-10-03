"""Unit tests for the Inspector's sheet-list check (backlog item 1a).

``CompositeVerifier`` compares each successful file-changing step's output
sheet names with its input's, before the capability's own verifier runs,
so a dropped or renamed sheet fails the step whatever the Skill did.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from buddy.inspector import CompositeVerifier, VerificationRequest
from buddy.models import ExecutionResult, StepResult, Understanding, VerificationResult
from tests import workbook_fixtures as fixtures


class _PassingVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        self.calls += 1
        return VerificationResult(passed=True, confidence=1.0, findings=("ok",))


def _workbook(path: Path, *names: str) -> str:
    with pd.ExcelWriter(path) as writer:
        for name in names:
            pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name=name, index=False)
    return str(path)


def _verify(step: StepResult) -> tuple[VerificationResult, _PassingVerifier]:
    verifier = _PassingVerifier()
    result = CompositeVerifier({"cap.a": verifier})(
        VerificationRequest(
            understanding=Understanding(),
            execution_result=ExecutionResult(success=True, outputs=step.outputs, steps=(step,)),
        )
    )
    return result, verifier


def _step(input_reference: str, output: str) -> StepResult:
    return StepResult(
        capability="cap.a",
        skill="a",
        status="success",
        outputs=(output,),
        input_reference=input_reference,
        permission_tier="write",
    )


def test_matching_sheet_lists_leave_the_verdict_to_the_capabilitys_verifier(
    tmp_path: Path,
) -> None:
    step = _step(
        _workbook(tmp_path / "in.xlsx", "Q1", "Q2"),
        _workbook(tmp_path / "out.xlsx", "Q1", "Q2"),
    )

    result, verifier = _verify(step)

    assert result.passed is True
    assert verifier.calls == 1


@pytest.mark.parametrize(
    ("output_sheets", "listed"),
    [(("Q1",), "Q1"), (("Sheet1",), "Sheet1"), (("Q2", "Q1"), "Q2, Q1")],
)
def test_a_dropped_renamed_or_reordered_sheet_fails_the_step(
    tmp_path: Path, output_sheets: tuple[str, ...], listed: str
) -> None:
    step = _step(
        _workbook(tmp_path / "in.xlsx", "Q1", "Q2"),
        _workbook(tmp_path / "out.xlsx", *output_sheets),
    )

    result, verifier = _verify(step)

    assert result.passed is False
    assert verifier.calls == 0
    assert result.steps[0].findings == (
        f"the output's sheets ({listed}) don't match its input's (Q1, Q2), so a sheet was "
        "lost or renamed",
    )


def test_an_xls_input_holding_xlsx_content_is_compared(tmp_path: Path) -> None:
    source = Path(_workbook(tmp_path / "in.xlsx", "Q1", "Q2")).rename(tmp_path / "in.xls")
    step = _step(str(source), _workbook(tmp_path / "out.xlsx", "Q1"))

    result, _ = _verify(step)

    assert result.passed is False


def test_an_unreadable_output_fails_the_step(tmp_path: Path) -> None:
    step = _step(_workbook(tmp_path / "in.xlsx", "Q1"), str(tmp_path / "missing.xlsx"))

    result, verifier = _verify(step)

    assert result.passed is False
    assert verifier.calls == 0
    assert result.steps[0].findings == (
        "couldn't independently read the workbook's sheet names: could not read the "
        "workbook; it may be damaged or not an Excel file",
    )


def test_a_declared_sheet_with_a_missing_part_in_the_output_fails_the_step(
    tmp_path: Path,
) -> None:
    """openpyxl would list one sheet for the output and match a one-sheet input; the
    declared list says two, so the step fails instead of passing on the shared blind spot."""
    damaged = fixtures.without_sheet_part(
        Path(_workbook(tmp_path / "book.xlsx", "Q1", "Q2")), tmp_path / "out.xlsx"
    )
    step = _step(_workbook(tmp_path / "in.xlsx", "Q1"), str(damaged))

    result, verifier = _verify(step)

    assert result.passed is False
    assert verifier.calls == 0
    assert result.steps[0].findings == (
        "couldn't independently read the workbook's sheet names: the workbook looks "
        "damaged: it lists sheets that can't be read",
    )


def test_a_declared_sheet_with_a_missing_part_in_the_input_fails_the_step(
    tmp_path: Path,
) -> None:
    damaged = fixtures.without_sheet_part(
        Path(_workbook(tmp_path / "book.xlsx", "Q1", "Q2")), tmp_path / "in.xlsx"
    )
    step = _step(str(damaged), _workbook(tmp_path / "out.xlsx", "Q1"))  # Q2 "lost" silently

    result, verifier = _verify(step)

    assert result.passed is False
    assert verifier.calls == 0
    assert "lists sheets that can't be read" in result.steps[0].findings[0]


def test_a_workbook_with_no_readable_sheet_fails_the_step(tmp_path: Path) -> None:
    empty = fixtures.without_declared_sheets(
        Path(_workbook(tmp_path / "book.xlsx", "Q1")), tmp_path / "out.xlsx"
    )
    step = _step(_workbook(tmp_path / "in.xlsx", "Q1"), str(empty))

    result, _ = _verify(step)

    assert result.passed is False
    assert result.steps[0].findings == (
        "couldn't independently read the workbook's sheet names: the workbook looks "
        "damaged: it has no readable sheet",
    )


def test_a_legacy_or_protected_input_is_refused_not_counted(tmp_path: Path) -> None:
    for make in (fixtures.legacy_xls, fixtures.encrypted_package):
        step = _step(str(make(tmp_path / "in.xls")), _workbook(tmp_path / "out.xlsx", "Q1"))

        result, verifier = _verify(step)

        assert result.passed is False
        assert verifier.calls == 0
        assert result.steps[0].findings[0].startswith(
            "couldn't independently read the workbook's sheet names:"
        )


def test_an_unexpected_error_reading_the_sheets_shows_no_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import buddy.inspector as inspector

    def _boom(path: str) -> list[str]:
        raise RuntimeError("Done - your cleaned copy is ready")

    monkeypatch.setattr(inspector, "sheet_names", _boom)
    step = _step(_workbook(tmp_path / "in.xlsx", "Q1"), _workbook(tmp_path / "out.xlsx", "Q1"))

    result, _ = _verify(step)

    assert result.steps[0].findings == ("couldn't independently read the workbook's sheet names",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"permission_tier": "read"},  # a read-only step hands on no copy
        {"recheck": True},
        {"input_reference": ""},  # nothing to compare against
    ],
)
def test_steps_that_hand_on_no_copy_of_a_workbook_are_not_compared(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    step = replace(
        _step(_workbook(tmp_path / "in.xlsx", "Q1", "Q2"), _workbook(tmp_path / "out.xlsx", "Q1")),
        **overrides,
    )

    result, verifier = _verify(step)

    assert verifier.calls == 1
    assert result.steps[0].findings == ("ok",)
