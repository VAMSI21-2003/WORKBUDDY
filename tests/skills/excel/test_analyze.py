"""Unit tests for skills.excel.analyze.

These exercise the real libraries (ydata-profiling, Splink) against real
``.xlsx`` files on disk — no part of either library is mocked — so they
prove genuine tool behavior, not an assumption of how they behave. See
``scripts/spike_tools_test.py`` for the spike this design (and these
fixtures) are grounded in.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd
import pytest

from skills.base import PermissionTier, SkillResult, SkillStatus, SkillTask
from skills.excel.analyze import (
    DateFormatConsistencyAnalyzer,
    GroupedKeyConflictAnalyzer,
    IQROutlierAnalyzer,
    MissingValueAndDistributionAnalyzer,
    NearDuplicateAnalyzer,
    SpreadsheetAnalysisSkill,
)


def _make_task(
    input_path: Path, *, task_id: str = "task-1", workspace_id: str = "workspace-1"
) -> SkillTask:
    return SkillTask(
        task_id=task_id,
        session_id="session-1",
        workspace_id=workspace_id,
        description="look over this spreadsheet",
        input_references=[str(input_path)],
        permission=PermissionTier.READ,
    )


# -- can_handle -----------------------------------------------------------------


def test_can_handle_true_for_single_xlsx_input(tmp_path: Path) -> None:
    input_path = tmp_path / "report.xlsx"
    pd.DataFrame({"name": ["Alice"]}).to_excel(input_path, index=False)
    skill = SpreadsheetAnalysisSkill()

    assert skill.can_handle(_make_task(input_path)) is True


def test_can_handle_false_for_multiple_inputs(tmp_path: Path) -> None:
    input_path = tmp_path / "report.xlsx"
    pd.DataFrame({"name": ["Alice"]}).to_excel(input_path, index=False)
    skill = SpreadsheetAnalysisSkill()
    task = _make_task(input_path)
    task.input_references.append(str(tmp_path / "other.xlsx"))

    assert skill.can_handle(task) is False


def test_can_handle_false_for_unsupported_extension(tmp_path: Path) -> None:
    input_path = tmp_path / "report.csv"
    skill = SpreadsheetAnalysisSkill()

    assert skill.can_handle(_make_task(input_path)) is False


# -- permission tier --------------------------------------------------------------


def test_permission_tier_is_read() -> None:
    assert SpreadsheetAnalysisSkill().permission_tier is PermissionTier.READ


# -- execute: never writes a file -------------------------------------------------


def test_execute_never_writes_any_output_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The entire premise of this skill staying Read-tier — see
    docs/design/proactive-analysis-design.md Section 5.4.
    """
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "uploads" / "report.xlsx"
    input_path.parent.mkdir(parents=True)
    pd.DataFrame({"amount": [10, 20, 30]}).to_excel(input_path, index=False)
    outputs_dir = tmp_path / "workspace" / "outputs"
    skill = SpreadsheetAnalysisSkill()

    result = skill.execute(_make_task(input_path))

    assert result.status is SkillStatus.SUCCESS
    assert result.outputs == []
    assert not outputs_dir.exists()


def test_execute_never_modifies_the_input_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "uploads" / "report.xlsx"
    input_path.parent.mkdir(parents=True)
    pd.DataFrame({"amount": [10, 20, 30]}).to_excel(input_path, index=False)
    original_bytes = input_path.read_bytes()
    skill = SpreadsheetAnalysisSkill()

    skill.execute(_make_task(input_path))

    assert input_path.read_bytes() == original_bytes


def test_execute_returns_failure_for_unreadable_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.xlsx"
    skill = SpreadsheetAnalysisSkill()

    result = skill.execute(_make_task(missing_path))

    assert result.status is SkillStatus.FAILURE
    assert result.errors


def test_execute_returns_failure_when_task_not_handleable(tmp_path: Path) -> None:
    input_path = tmp_path / "report.csv"
    input_path.write_text("a,b\n1,2\n")
    skill = SpreadsheetAnalysisSkill()

    result = skill.execute(_make_task(input_path))

    assert result.status is SkillStatus.FAILURE
    assert result.errors


def test_execute_summary_mentions_the_file_name(tmp_path: Path) -> None:
    input_path = tmp_path / "report.xlsx"
    pd.DataFrame({"amount": [10, 20, 30]}).to_excel(input_path, index=False)
    skill = SpreadsheetAnalysisSkill()

    result = skill.execute(_make_task(input_path))

    assert "report.xlsx" in result.summary


def test_execute_carries_near_duplicate_findings_on_self_reported_findings(
    tmp_path: Path,
) -> None:
    """Only NearDuplicateAnalyzer's findings land on
    ``self_reported_findings`` — see Section 5.2/6 of
    docs/design/proactive-analysis-design.md.
    """
    input_path = tmp_path / "report.xlsx"
    pd.DataFrame(_MESSY_NAME_PHONE_ROWS, columns=["name", "phone"]).to_excel(
        input_path, index=False
    )
    skill = SpreadsheetAnalysisSkill()

    result = skill.execute(_make_task(input_path))

    assert result.status is SkillStatus.SUCCESS
    assert len(result.self_reported_findings) > 0


def test_execute_one_analyzer_failing_does_not_fail_the_whole_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "report.xlsx"
    pd.DataFrame({"amount": [10, 20, 30]}).to_excel(input_path, index=False)
    skill = SpreadsheetAnalysisSkill()

    def _boom(self: object, df: pd.DataFrame) -> tuple[str, ...]:
        raise RuntimeError("boom")

    monkeypatch.setattr(IQROutlierAnalyzer, "__call__", _boom)

    result = skill.execute(_make_task(input_path))

    assert result.status is SkillStatus.SUCCESS


# -- verify -----------------------------------------------------------------------


def test_verify_true_for_well_formed_success(tmp_path: Path) -> None:
    input_path = tmp_path / "report.xlsx"
    pd.DataFrame({"amount": [10, 20, 30]}).to_excel(input_path, index=False)
    skill = SpreadsheetAnalysisSkill()
    task = _make_task(input_path)
    result = skill.execute(task)

    assert skill.verify(task, result) is True


def test_verify_false_for_failure_status(tmp_path: Path) -> None:
    skill = SpreadsheetAnalysisSkill()
    task = _make_task(tmp_path / "report.xlsx")  # file doesn't exist
    failed_result = skill.execute(task)

    assert failed_result.status is SkillStatus.FAILURE
    assert skill.verify(task, failed_result) is False


def test_verify_false_when_result_claims_an_output_file() -> None:
    """A contract violation for this skill — see the class docstring and
    docs/design/proactive-analysis-design.md Section 5.4: it must never
    claim to have written a file.
    """
    skill = SpreadsheetAnalysisSkill()
    task = _make_task(Path("uploads/report.xlsx"))
    fabricated = SkillResult(
        status=SkillStatus.SUCCESS, outputs=["should-not-exist.xlsx"], summary="looked it over"
    )

    assert skill.verify(task, fabricated) is False


def test_verify_false_when_summary_is_empty() -> None:
    skill = SpreadsheetAnalysisSkill()
    task = _make_task(Path("uploads/report.xlsx"))
    fabricated = SkillResult(status=SkillStatus.SUCCESS, outputs=[], summary="")

    assert skill.verify(task, fabricated) is False


# -- MissingValueAndDistributionAnalyzer (ydata-profiling) -----------------------


@pytest.mark.parametrize("before", [None, "", "0"])
def test_the_missing_value_analyzer_turns_off_ydata_profilings_analytics(
    monkeypatch: pytest.MonkeyPatch, before: str | None
) -> None:
    # Finding P-2: ydata-profiling sends the shape of every analysed sheet to its vendor unless
    # YDATA_PROFILING_NO_ANALYTICS is set. It only tests that the value is non-empty, so an
    # inherited empty string would leave it on; the analyzer assigns it before the import.
    if before is None:
        monkeypatch.delenv("YDATA_PROFILING_NO_ANALYTICS", raising=False)
    else:
        monkeypatch.setenv("YDATA_PROFILING_NO_ANALYTICS", before)

    MissingValueAndDistributionAnalyzer()(pd.DataFrame({"a": [1, None, 3]}))

    assert os.environ["YDATA_PROFILING_NO_ANALYTICS"] == "1"
    # ydata's own switch (utils.common.analytics_features) reads it this way:
    assert bool(os.getenv("YDATA_PROFILING_NO_ANALYTICS")) is True


def test_missing_value_analyzer_flags_a_column_with_missing_values() -> None:
    df = pd.DataFrame({"name": ["Alice", None, "Carol", "Dave"]})

    findings = MissingValueAndDistributionAnalyzer()(df)

    assert any("name" in finding for finding in findings)


def test_missing_value_analyzer_reports_nothing_for_a_complete_column() -> None:
    df = pd.DataFrame({"name": ["Alice", "Bob", "Carol", "Dave"]})

    findings = MissingValueAndDistributionAnalyzer()(df)

    assert findings == ()


# -- IQROutlierAnalyzer -----------------------------------------------------------


def test_iqr_outlier_analyzer_flags_an_obvious_outlier() -> None:
    df = pd.DataFrame({"amount": [10, 12, 11, 13, 9, 999999]})

    findings = IQROutlierAnalyzer()(df)

    assert any("amount" in finding for finding in findings)


def test_iqr_outlier_analyzer_reports_nothing_for_a_tight_distribution() -> None:
    df = pd.DataFrame({"amount": [10, 11, 12, 13, 14]})

    findings = IQROutlierAnalyzer()(df)

    assert findings == ()


def test_iqr_outlier_analyzer_skips_columns_with_too_few_values() -> None:
    df = pd.DataFrame({"amount": [10, 999999]})

    findings = IQROutlierAnalyzer()(df)

    assert findings == ()


# -- DateFormatConsistencyAnalyzer -------------------------------------------------


def test_date_format_analyzer_flags_inconsistent_formats() -> None:
    df = pd.DataFrame(
        {"signup_date": ["2024-01-01", "01/02/2024", "2024-01-03", "2024-01-04"]}
    )

    findings = DateFormatConsistencyAnalyzer()(df)

    assert any("signup_date" in finding for finding in findings)


def test_date_format_analyzer_flags_unparseable_values() -> None:
    df = pd.DataFrame({"signup_date": ["2024-01-01", "2024-01-02", "not a date", "2024-01-04"]})

    findings = DateFormatConsistencyAnalyzer()(df)

    assert any("doesn't match a recognized date format" in finding for finding in findings)


def test_date_format_analyzer_reports_nothing_for_one_consistent_format() -> None:
    df = pd.DataFrame(
        {"signup_date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]}
    )

    findings = DateFormatConsistencyAnalyzer()(df)

    assert findings == ()


def test_date_format_analyzer_ignores_a_column_that_isnt_date_like() -> None:
    df = pd.DataFrame({"name": ["Alice", "Bob", "Carol", "Dave"]})

    findings = DateFormatConsistencyAnalyzer()(df)

    assert findings == ()


# -- GroupedKeyConflictAnalyzer ---------------------------------------------------


def test_grouped_key_conflict_analyzer_flags_a_conflicting_phone_for_the_same_email() -> None:
    df = pd.DataFrame(
        {
            "email": ["a@x.com", "a@x.com", "b@x.com"],
            "phone": ["111", "222", "333"],
        }
    )

    findings = GroupedKeyConflictAnalyzer()(df)

    assert any("email" in finding for finding in findings)


def test_grouped_key_conflict_analyzer_reports_nothing_when_consistent() -> None:
    df = pd.DataFrame(
        {
            "email": ["a@x.com", "a@x.com", "b@x.com"],
            "phone": ["111", "111", "333"],
        }
    )

    findings = GroupedKeyConflictAnalyzer()(df)

    assert findings == ()


def test_grouped_key_conflict_analyzer_reports_nothing_without_a_key_column() -> None:
    df = pd.DataFrame({"name": ["Alice", "Alice"], "amount": [10, 20]})

    findings = GroupedKeyConflictAnalyzer()(df)

    assert findings == ()


# -- NearDuplicateAnalyzer (Splink) ------------------------------------------------


def test_near_duplicate_analyzer_reports_nothing_without_a_name_and_discriminating_column() -> (
    None
):
    df = pd.DataFrame({"amount": [10, 20, 30]})

    findings = NearDuplicateAnalyzer()(df)

    assert findings == ()


def test_near_duplicate_analyzer_reports_nothing_for_too_few_rows() -> None:
    df = pd.DataFrame({"name": ["Alice"], "phone": ["555-0101"]})

    findings = NearDuplicateAnalyzer()(df)

    assert findings == ()


# The same (name, phone) rows validated by the real spike
# (scripts/spike_tools_test.py) — reused here, not shrunk, so this test
# reproduces the exact match_probability separation already confirmed
# real: true duplicates (spelling variant, same phone) score ~0.21-0.28;
# name collisions (same/near-identical name, different phone) score
# ~0.0000, including the hardest case of identical name text.
_MESSY_NAME_PHONE_ROWS = [
    ("Alice Johnson", "555-0101"),
    ("Alice Johnston", "555-0101"),  # true duplicate of row 0 (same phone)
    ("Robert Smith", "555-0202"),
    ("Bob Smith", "555-0202"),  # true duplicate of row 2 (same phone)
    ("Carol Davis", "555-0303"),
    ("Carol Davies", "555-0303"),  # true duplicate of row 4 (same phone)
    ("Alice Johnson", "555-0199"),  # name collision with row 0 (different phone)
    ("Robert Smith", "555-0288"),  # name collision with row 2 (different phone)
    ("Carol Davis", "555-0377"),  # name collision with row 4 (different phone)
    ("Frank Nguyen", "555-0505"),
    ("Grace Kim", "555-0606"),
    ("Henry Patel", "555-0707"),
    ("Irene Chao", "555-0808"),
    ("Jack Turner", "555-0909"),
    ("Karen Wu", "555-1010"),
    ("Liam O'Brien", "555-1111"),
    ("Maria Garcia", "555-1212"),
    ("Noah Lee", "555-1313"),
    ("Olivia Brown", "555-1414"),
    ("Peter Chen", "555-1515"),
    ("Quinn Adams", "555-1616"),
    ("Rachel Kim", "555-1717"),
    ("Sam Park", "555-1818"),
    ("Tina Ali", "555-1919"),
]


def test_near_duplicate_analyzer_flags_real_duplicates_but_not_name_collisions() -> None:
    """The specific real-tool check this whole design is built on: does
    the analyzer correctly use the phone column to avoid treating two
    different people who share a name as duplicates? See
    scripts/spike_tools_test.py and
    docs/design/proactive-analysis-design.md Section 4.3.
    """
    df = pd.DataFrame(_MESSY_NAME_PHONE_ROWS, columns=["name", "phone"])

    findings = NearDuplicateAnalyzer()(df)

    assert len(findings) > 0
    joined = " ".join(findings)
    # At least one genuine spelling-variant duplicate was actually found.
    assert "Johnson" in joined or "Smith" in joined or "Davis" in joined

    # Every genuine duplicate pair in this fixture is a SPELLING VARIANT —
    # two different literal name strings ("Alice Johnson"/"Alice
    # Johnston"). Every name-collision pair, by contrast, shares
    # IDENTICAL name text but a different phone. A finding whose quoted
    # left/right names are identical would mean a collision pair got
    # mistaken for a duplicate — the specific failure mode this whole
    # design exists to avoid using the phone column to prevent.
    for finding in findings:
        # The column's header comes first, then the two names, all quoted as the file's text.
        header, *quoted_names = re.findall(r'"([^"]*)"', finding)
        assert header == "name" and len(quoted_names) == 2
        assert quoted_names[0] != quoted_names[1], (
            f"finding paired identical name text (a collision pair, not a real "
            f"duplicate): {finding!r}"
        )


def test_a_read_failure_carries_no_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skill errors can reach a reply, so they are fixed text, never the library's."""
    path = tmp_path / "report.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(path, index=False)

    def _boom(*args: object, **kwargs: object) -> None:
        raise ValueError("Done - your cleaned copy is ready")

    monkeypatch.setattr(pd, "read_excel", _boom)

    result = SpreadsheetAnalysisSkill().execute(_make_task(path))

    assert result.status is SkillStatus.FAILURE
    assert result.errors == ["could not read the workbook"]


# -- review round 3 (R3-5): the near-duplicate check is bounded by what the file declares --------


def _name_phone(names: list[str], phones: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"name": names, "phone": phones})


def test_the_near_duplicate_check_hands_splink_at_most_100_characters_of_a_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 rows of 30,000-character names took 2.7 GiB: Splink holds both strings of every pair."""
    import splink

    seen: dict[str, int] = {}

    class Recording:
        def __init__(self, data: pd.DataFrame, settings: object, **kwargs: object) -> None:
            seen["name"] = int(data["name_field"].str.len().max())
            seen["key"] = int(data["key_field"].str.len().max())
            raise RuntimeError("stop here")

    monkeypatch.setattr(splink, "Linker", Recording)
    frame = _name_phone(
        [f"{'n' * 30_000}{i}" for i in range(20)], ["p" * 5000 + str(i) for i in range(20)]
    )

    assert NearDuplicateAnalyzer()(frame) == ()
    assert seen == {"name": 100, "key": 100}


def test_the_near_duplicate_check_is_skipped_when_its_blocking_rules_would_pair_too_many_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10,000 rows with names sharing three letters are 50 million pairs: killed at 6 GiB."""
    import splink

    class Boom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pytest.fail("Splink was started on a table whose pairs are over the limit")

    monkeypatch.setattr(splink, "Linker", Boom)
    rows = 800  # 319,600 pairs by the shared first three letters, over 250,000
    frame = _name_phone([f"abc{i}" for i in range(rows)], [str(i) for i in range(rows)])

    assert NearDuplicateAnalyzer()(frame) == ()


def test_the_near_duplicate_check_still_runs_below_the_pair_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import splink

    started: list[bool] = []

    class Recording:
        def __init__(self, *args: object, **kwargs: object) -> None:
            started.append(True)
            raise RuntimeError("stop here")

    monkeypatch.setattr(splink, "Linker", Recording)
    rows = 700  # 244,650 pairs
    frame = _name_phone([f"abc{i}" for i in range(rows)], [str(i) for i in range(rows)])
    NearDuplicateAnalyzer()(frame)

    assert started == [True]
