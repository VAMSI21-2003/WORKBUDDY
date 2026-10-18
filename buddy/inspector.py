"""Inspector — checks executed work against the user's original intent."""

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import NamedTuple, Protocol

import pandas as pd

from buddy.actions import ACTIONS_BY_NAME, requested_ledger
from buddy.models import (
    DUPLICATE_ROWS_TARGET,
    EMPTY_ROWS_TARGET,
    SKIPPED_STATUS,
    ExecutionResult,
    OriginalFacts,
    StepMeasurement,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.state import BuddyState
from buddy.text import count_noun
from skills.excel.shown_text import shown
from skills.excel.workbook import (
    WorkbookRefused,
    check_workbook_limits,
    formula_cell_counts,
    plain_table_problem,
    sheet_names,
)

logger = logging.getLogger(__name__)

# What a reply says when the Inspector could not read a file again. Fixed sentences: the detail
# (the exception and the path) goes to the log, never to a finding, because findings reach replies.
COULD_NOT_READ_OUTPUT = "could not independently read the file it produced"
COULD_NOT_READ_INPUT = "could not independently read the file it started from"
COULD_NOT_READ_ANALYSED = "could not independently read the file that was analysed"


@dataclass(frozen=True)
class VerificationRequest:
    """The inputs available for checking executed work against user intent.

    A plain, self-contained bundle of everything a ``Verifier`` needs to
    work from — assembled mechanically from ``BuddyState`` by the
    Inspector stage, so a verifier never has to depend on ``BuddyState``
    directly.

    Attributes:
        understanding: The structured understanding produced by
            Brain — the original intent to check the outcome
            against.
        execution_result: The structured result produced by Hands, if
            it has run yet.
        active_file: Reference to the file the user was last focused on,
            if any, from the working context — the same field
            ``Delegate``/``Planner`` already pull straight from
            ``state.context``. Existing Verifiers ignore this entirely;
            ``SpreadsheetAnalysisVerifier`` is the first to read it, since
            verifying an analysis means re-deriving facts about the
            *original* upload, not a Skill-produced output file (see
            ``docs/design/proactive-analysis-design.md`` Section 5.1).
    """

    understanding: Understanding
    execution_result: ExecutionResult | None
    active_file: str | None = None


# What the analysis reports when none of its checks found anything.
NO_ANALYSIS_FINDINGS = "none of the issues I check for were found"


class Verifier(Protocol):
    """Produces a ``VerificationResult`` from a ``VerificationRequest``.

    This is the seam where real, independent verification is plugged in.
    The Inspector stage itself never judges whether work succeeded; it
    only calls whichever ``Verifier`` it was given.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify ``request`` and return the resulting judgment."""
        ...


class NullVerifier:
    """A ``Verifier`` that performs no verification.

    Never claims success it hasn't checked. Serves as a safe default so
    ``Inspector`` can be constructed and exercised — in tests, or wired
    into the Engine — before a real verifier is plugged in.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Return an unverified, unpassed result for any request.

        Args:
            request: The request that would otherwise be verified.

        Returns:
            A ``VerificationResult`` with ``passed=False`` and zero
            confidence, noting that no verifier has been configured.
        """
        return VerificationResult(
            passed=False,
            confidence=0.0,
            findings=("no verifier has been configured",),
        )


class SpreadsheetDedupeVerifier:
    """Independently confirms an output spreadsheet has no duplicate rows.

    Deliberately blind to everything the Skill *said* about itself: it
    never reads a ``SkillResult``'s ``status``, ``summary``, or
    ``metadata`` (none of which even reach this stage — ``ExecutionResult``
    carries none of them). It takes only the output path Hands recorded,
    opens *that specific file* fresh via ``pandas.read_excel``, and
    recomputes ``duplicated().sum()`` itself. See
    ``docs/design/slice-1-dedupe-design.md`` Section D for the full design.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify ``request`` by independently re-reading its output file.

        Args:
            request: The request to verify.

        Returns:
            A ``VerificationResult`` reflecting what was actually found in
            the output file — not what Hands or a Skill reported.
        """
        execution_result = request.execution_result
        if execution_result is None or not execution_result.success or not execution_result.outputs:
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=("execution did not produce an output file",),
            )

        output_path = execution_result.outputs[0]
        try:
            written = _read_cells(output_path)  # fresh, independent read of the artifact
        except Exception:  # noqa: BLE001 — surfaced as a finding, not raised
            logger.exception("SpreadsheetDedupeVerifier: failed to read %r", output_path)
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=(COULD_NOT_READ_OUTPUT,),
            )

        duplicate_count = _duplicate_rows(written)  # recomputed from scratch
        findings = [
            f"output has {count_noun(len(written), 'row')}, "
            f"{count_noun(duplicate_count, 'exact duplicate row')} remaining"
        ]
        if duplicate_count:
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=tuple(findings),
                recommendations=("re-run deduplication; duplicates remain",),
            )

        check = _check_against_input(
            written,
            _step_input(execution_result),
            lambda original: len(original) - _duplicate_rows(original),
            # Every row dedupe must drop, identical blank rows included.
            _duplicate_rows,
        )
        if check.problem is not None:
            findings.append(check.problem)
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=tuple(findings),
                recommendations=("re-run deduplication on the original",),
            )
        return VerificationResult(
            passed=True,
            confidence=1.0,
            findings=tuple(findings),
            measurements=(check.measurement(
                    DUPLICATE_ROWS_TARGET, len(written), duplicate_count, repeats_blank_rows=True
                ),
            ),
        )


class SpreadsheetEmptyRowsVerifier:
    """Independently confirms an output spreadsheet has no fully-empty rows.

    Same shape as ``SpreadsheetDedupeVerifier`` — deliberately blind to
    what the Skill (``skills.excel.remove_empty_rows.ExcelRemoveEmptyRowsSkill``)
    reported about itself. Opens the output path Hands recorded fresh via
    ``pandas.read_excel`` and recomputes the check itself, rather than
    trusting anything the Skill claimed.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify ``request`` by independently re-reading its output file.

        Args:
            request: The request to verify.

        Returns:
            A ``VerificationResult`` reflecting what was actually found in
            the output file — not what Hands or a Skill reported.
        """
        execution_result = request.execution_result
        if execution_result is None or not execution_result.success or not execution_result.outputs:
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=("execution did not produce an output file",),
            )

        output_path = execution_result.outputs[0]
        try:
            written = _read_cells(output_path)  # fresh, independent read of the artifact
        except Exception:  # noqa: BLE001 — surfaced as a finding, not raised
            logger.exception("SpreadsheetEmptyRowsVerifier: failed to read %r", output_path)
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=(COULD_NOT_READ_OUTPUT,),
            )

        empty_row_count = _empty_rows(written)  # recomputed from scratch
        findings = [
            f"output has {count_noun(len(written), 'row')}, "
            f"{count_noun(empty_row_count, 'fully-empty row')} remaining"
        ]
        if empty_row_count:
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=tuple(findings),
                recommendations=("re-run empty-row removal; empty rows remain",),
            )

        check = _check_against_input(
            written,
            _step_input(execution_result),
            lambda original: len(original) - _empty_rows(original),
            _empty_rows,
        )
        if check.problem is not None:
            findings.append(check.problem)
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=tuple(findings),
                recommendations=("re-run empty-row removal on the original",),
            )
        return VerificationResult(
            passed=True,
            confidence=1.0,
            findings=tuple(findings),
            measurements=(check.measurement(EMPTY_ROWS_TARGET, len(written), empty_row_count),),
        )


def _read_cells(path: str) -> pd.DataFrame:
    """A workbook's first sheet as its cells hold it: no text cell converted to a number.

    ``dtype=object`` keeps a text cell text and a number a number. pandas' default would turn a
    column of text that looks like numbers into numbers (``"007"`` into ``7``, a long account
    number into a float that has lost digits), so a check built on it could not tell a skill that
    did exactly that from one that kept the cells. (A boolean is still read as 0 or 1: backlog,
    "TRUE/FALSE in a column of numbers".)
    """
    check_workbook_limits(path)
    return pd.read_excel(path, dtype=object)


# How each measured target is counted in a file, as read fresh. The step
# verifiers and ``ResidualMeasurer`` use the same definitions.
def _duplicate_rows(df: pd.DataFrame) -> int:
    """Rows that repeat an earlier row exactly (identical blank rows included)."""
    return int(df.duplicated().sum())


def _empty_rows(df: pd.DataFrame) -> int:
    """Rows with every cell empty."""
    return int(df.isna().all(axis=1).sum())


_TARGET_COUNTERS: dict[str, Callable[[pd.DataFrame], int]] = {
    DUPLICATE_ROWS_TARGET: _duplicate_rows,
    EMPTY_ROWS_TARGET: _empty_rows,
}


def _step_input(execution_result: ExecutionResult) -> str:
    """Return the file the (single, scoped) step actually read, or ``""``."""
    return execution_result.steps[0].input_reference if execution_result.steps else ""


def _without_empty_rows(df: pd.DataFrame) -> pd.DataFrame:
    """``df`` without its fully-empty rows."""
    return df[~df.isna().all(axis=1)]


def _row_set(df: pd.DataFrame) -> set[tuple[object, ...]]:
    """Every row of ``df`` as a hashable tuple, with missing values as ``None``."""
    return set(df.astype(object).where(df.notna(), None).itertuples(index=False, name=None))


class _InputCheck(NamedTuple):
    """What ``_check_against_input`` found: a problem, or what it measured."""

    problem: str | None = None
    # Rows in the step's input, and the target rows it had to remove, both
    # read fresh from the input. ``None`` when there was no input to read.
    input_rows: int | None = None
    removed: int | None = None
    # Rows and fully-empty rows in the output as read (before the check
    # drops empty rows), and fully-empty rows in the input.
    output_rows: int | None = None
    input_blank: int | None = None
    output_blank: int | None = None

    def measurement(
        self, target: str, output_rows: int, remaining: int, repeats_blank_rows: bool = False
    ) -> StepMeasurement:
        """The ``StepMeasurement`` for a check that passed.

        ``removed`` is kept only when the files confirm it: it must equal the
        rows the input had beyond the output. Otherwise it's ``None`` and the
        reply states no number. The row-count check leaves empty rows out, so
        this is where an .xlsx round trip that dropped or kept a blank row is
        caught.

        Args:
            target: What the step counted.
            output_rows: Rows in the output, as read.
            remaining: Target rows still in the output.
            repeats_blank_rows: ``True`` for deduplication, which removes all
                but one of any identical blank rows. Their number is then
                confirmed as well, and reported as ``removed_blank``.
        """
        removed, removed_blank = self.removed, 0
        if removed is not None:
            confirmed = self.input_rows - self.output_rows == removed
            if repeats_blank_rows:
                removed_blank = self.input_blank - self.output_blank
                # Identical blank rows keep exactly one, so the step removed
                # all but one of them (none if there were none).
                confirmed = confirmed and removed_blank == max(self.input_blank - 1, 0)
            if not confirmed:
                removed, removed_blank = None, 0
        return StepMeasurement(
            target=target,
            output_rows=output_rows,
            remaining=remaining,
            input_rows=self.input_rows,
            removed=removed,
            removed_blank=removed_blank,
        )


def _check_against_input(
    written: pd.DataFrame,
    input_path: str,
    expected_row_count: Callable[[pd.DataFrame], int],
    removed_count: Callable[[pd.DataFrame], int],
) -> _InputCheck:
    """Check an output against the step's own input, not just on its own.

    An output-only check ("no duplicates left") would still pass for a step
    that removed rows it shouldn't have. This re-reads the input fresh and
    confirms the output has exactly the rows the capability should have
    kept: the expected row count, and no row that wasn't in the input.

    When the check passes, it also returns what it measured on the input:
    its row count and how many target rows it had. The pass condition
    proves the output kept exactly the rest, so the removed count is
    confirmed, not predicted.

    Args:
        written: The step's output, already read fresh.
        input_path: The file the step read, or ``""`` if unknown — in which
            case only the output-only postcondition applies (the check a
            final file gets, since it isn't any single step's direct output).
        expected_row_count: Recomputes, from the input with its fully-empty
            rows left out, how many rows the output should have.
        removed_count: Counts, from the input as read (empty rows
            included), the rows the step had to remove.

    Returns:
        An ``_InputCheck`` with no problem and the input's counts if the
        output is consistent with its input; with no problem and no counts
        if there is no input to check against; otherwise with a
        plain-language problem.
    """
    if not input_path:
        return _InputCheck()
    try:
        original = _read_cells(input_path)  # fresh, independent read of the step's input
    except Exception:  # noqa: BLE001 — surfaced as a finding, not raised
        logger.exception("Inspector: failed to read step input %r", input_path)
        return _InputCheck(problem=COULD_NOT_READ_INPUT)

    input_rows = len(original)
    removed = removed_count(original)
    input_blank = int(original.isna().all(axis=1).sum())
    output_rows, output_blank = len(written), int(written.isna().all(axis=1).sum())
    # Fully-empty rows are left out on both sides: an .xlsx can't hold a
    # trailing empty row (openpyxl drops it on the write/read round trip),
    # so whether one survives depends on where it ended up, not on what the
    # step did. Every other row must match exactly.
    original = _without_empty_rows(original)
    written = _without_empty_rows(written)
    expected = expected_row_count(original)
    if len(written) != expected:
        return _InputCheck(
            problem=(
                f"expected {count_noun(expected, 'non-empty row')} from its "
                f"{len(original)}-row input, "
                f"found {len(written)}"
            )
        )
    unexpected = _row_set(written) - _row_set(original)
    if unexpected:
        return _InputCheck(problem=(
            f"{count_noun(len(unexpected), 'output row')} "
            f"{'doesn' if len(unexpected) == 1 else 'don'}'t appear in its input"
        ))
    return _InputCheck(
        input_rows=input_rows,
        removed=removed,
        output_rows=output_rows,
        input_blank=input_blank,
        output_blank=output_blank,
    )


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%b %d %Y",
)
_KEY_COLUMN_PATTERN = re.compile(r"email|phone|tel", re.IGNORECASE)


def _matched_date_format(value: str) -> str | None:
    """Return the first of ``_DATE_FORMATS`` that parses ``value`` exactly.

    Args:
        value: The raw string value to test.

    Returns:
        The matching format string, or ``None`` if none of the known
        formats parse ``value``.
    """
    for date_format in _DATE_FORMATS:
        try:
            datetime.strptime(value, date_format)
            return date_format
        except ValueError:
            continue
    return None


def _independent_duplicate_row_findings(df: pd.DataFrame) -> tuple[str, ...]:
    """Recompute how many rows repeat an earlier row exactly, directly from ``df``.

    The same definition the step verifiers and ``ResidualMeasurer`` count
    (``_duplicate_rows``), so "it still has 2 duplicate rows" and this
    finding can never disagree. Backlog item 3 (L-4).
    """
    count = _duplicate_rows(df)
    if count == 0:
        return ()
    verb = "repeats" if count == 1 else "repeat"
    return (f"{count_noun(count, 'row')} {verb} an earlier row exactly",)


def _independent_loose_duplicate_row_findings(df: pd.DataFrame) -> tuple[str, ...]:
    """Recompute how many rows match an earlier row once spaces and case are ignored.

    Only rows that are not exact repeats (those are ``_independent_duplicate_row_findings``'s,
    and the cleaning skill removes them). A text cell is compared with the spaces at both ends
    trimmed and its case folded; any other cell as it is. Written separately from the skill's own
    check, so the two can disagree and the verifier's count is the one that counts.
    """
    folded = df.copy()
    for column in folded.columns:
        folded[column] = [
            cell.strip().casefold() if isinstance(cell, str) else cell for cell in folded[column]
        ]
    count = int((folded.duplicated() & ~df.duplicated()).sum())
    if count == 0:
        return ()
    if count == 1:
        return (
            "1 row matches an earlier row once spaces around the text and capital letters are "
            "ignored, but isn't an exact repeat",
        )
    return (
        f"{count} rows match an earlier row once spaces around the text and capital letters "
        "are ignored, but aren't exact repeats",
    )


def _independent_missing_value_findings(df: pd.DataFrame) -> tuple[str, ...]:
    """Recompute missing-value counts directly from ``df``.

    See ``docs/design/proactive-analysis-design.md`` Section 2.
    """
    findings: list[str] = []
    for column in df.columns:
        missing = int(df[column].isna().sum())
        if missing == 0:
            continue
        percentage = 100 * missing / len(df) if len(df) else 0.0
        findings.append(
            f"column {shown(column)}: {count_noun(missing, 'missing value')} ({percentage:.1f}%)"
        )
    return tuple(findings)


def _independent_outlier_findings(df: pd.DataFrame) -> tuple[str, ...]:
    """Recompute IQR-based numeric outliers directly from ``df``.

    See ``docs/design/proactive-analysis-design.md`` Section 3.
    """
    findings: list[str] = []
    for column in df.select_dtypes(include="number").columns:
        series = df[column].dropna()
        if len(series) < 4:
            continue
        first_quartile, third_quartile = series.quantile(0.25), series.quantile(0.75)
        iqr = third_quartile - first_quartile
        if iqr == 0:
            continue
        low, high = first_quartile - 1.5 * iqr, third_quartile + 1.5 * iqr
        outliers = series[(series < low) | (series > high)]
        if len(outliers) == 0:
            continue
        examples = ", ".join(f"{value:g}" for value in outliers.head(3).tolist())
        findings.append(
            f"column {shown(column)}: {count_noun(len(outliers), 'unusually high/low value')} "
            f"(e.g. {examples})"
        )
    return tuple(findings)


def _independent_date_format_findings(df: pd.DataFrame) -> tuple[str, ...]:
    """Recompute date-format-consistency findings directly from ``df``.

    See ``docs/design/proactive-analysis-design.md`` Section 4.1.
    """
    findings: list[str] = []
    for column in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[column]):
            continue
        values = df[column].dropna().astype(str)
        if values.empty:
            continue
        matched_formats = values.map(_matched_date_format)
        match_ratio = matched_formats.notna().mean()
        if match_ratio < 0.5:
            continue  # not date-like enough to be a date column at all
        distinct_formats = sorted({fmt for fmt in matched_formats.dropna().unique()})
        unparseable = int(matched_formats.isna().sum())
        if len(distinct_formats) <= 1 and unparseable == 0:
            continue
        parts = []
        if distinct_formats:
            parts.append(
                f"{count_noun(len(distinct_formats), 'different format')}: "
                f"{', '.join(distinct_formats)}"
            )
        if unparseable:
            parts.append(
                f"{count_noun(unparseable, 'value')} that "
                f"{'doesn' if unparseable == 1 else 'don'}'t match a recognized date format"
            )
        findings.append(f"column {shown(column)}: " + "; ".join(parts))
    return tuple(findings)


def _independent_grouped_key_conflict_findings(df: pd.DataFrame) -> tuple[str, ...]:
    """Recompute grouped-key-conflict findings directly from ``df``.

    Deliberately limited to exact-match grouping on an email/phone-like
    column — never guessing at arithmetic/derived-column relationships.
    See ``docs/design/proactive-analysis-design.md`` Section 4.2. Grouped
    just as cheaply as the other three checks above, so it gets the same
    full-recomputation treatment even though the design doc's Section 5.2
    enumeration didn't name it explicitly alongside them.
    """
    findings: list[str] = []
    key_columns = [column for column in df.columns if _KEY_COLUMN_PATTERN.search(column)]
    for key_column in key_columns:
        grouped = df.dropna(subset=[key_column]).groupby(key_column)
        for other_column in df.columns:
            if other_column == key_column:
                continue
            conflict_counts = grouped[other_column].nunique(dropna=True)
            conflicts = conflict_counts[conflict_counts > 1]
            if len(conflicts) == 0:
                continue
            example_key = conflicts.index[0]
            findings.append(
                f"{count_noun(len(conflicts), 'value')} of column {shown(key_column)} "
                f"{'is' if len(conflicts) == 1 else 'are'} linked to more than one value in "
                f"column {shown(other_column)} "
                f"(e.g. {shown(key_column)} = {shown(example_key)})"
            )
    return tuple(findings)


class SpreadsheetAnalysisVerifier:
    """Independently confirms ``SpreadsheetAnalysisSkill``'s proactive findings.

    Two different treatments for two different kinds of claim, per
    ``docs/design/proactive-analysis-design.md`` Section 5.2:

    - **Fully re-derived, independently** (missing values, outliers,
      date-format consistency, grouped-key conflicts): recomputed from
      scratch directly against ``request.active_file`` — the *original*
      upload, opened fresh, exactly the same "deliberately blind to what
      the Skill said" discipline ``SpreadsheetDedupeVerifier`` already
      applies. Cheap, dependency-free pandas/``datetime`` logic, so full
      independent recomputation costs almost nothing.
    - **Self-reported, not independently re-run** (near-duplicates):
      Splink's near-duplicate detection is expensive and re-running the
      identical algorithm a second time would not be independent
      verification at all. These are passed through from
      ``StepResult.self_reported_findings`` unmodified, explicitly
      labeled as self-reported, at a capped confidence.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify ``request`` by independently re-reading the original upload.

        Args:
            request: The request to verify.

        Returns:
            A ``VerificationResult`` combining fully-independent findings
            with the Skill's own self-reported near-duplicate findings,
            explicitly labeled as such.
        """
        execution_result = request.execution_result
        if execution_result is None or not execution_result.steps:
            return VerificationResult(
                passed=False, confidence=1.0, findings=("no analysis step to verify",)
            )

        step = execution_result.steps[0]
        if step.status == "failure":
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=("the analysis reported failure",),
            )

        # The exact file the Skill analyzed; the active file (the original
        # upload) only for results recorded before steps carried their input.
        analyzed_file = step.input_reference or request.active_file
        if not analyzed_file:
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=("no active file available to independently re-check",),
            )

        try:
            check_workbook_limits(analyzed_file)
            df = pd.read_excel(analyzed_file)  # fresh, independent read of the ORIGINAL
            cells = _read_cells(analyzed_file)  # the same cells, none converted
        except Exception:  # noqa: BLE001 — surfaced as a finding, not raised
            logger.exception("SpreadsheetAnalysisVerifier: failed to read %r", analyzed_file)
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=(COULD_NOT_READ_ANALYSED,),
            )

        findings: list[str] = []
        for category, category_findings in (
            ("duplicate_rows", _independent_duplicate_row_findings(cells)),
            ("loose_duplicate_rows", _independent_loose_duplicate_row_findings(cells)),
            ("missing_values", _independent_missing_value_findings(df)),
            ("outliers", _independent_outlier_findings(df)),
            ("date_format", _independent_date_format_findings(df)),
            ("grouped_key_conflict", _independent_grouped_key_conflict_findings(df)),
        ):
            findings.extend(f"[{category}] {finding}" for finding in category_findings)

        self_reported = step.self_reported_findings
        findings.extend(
            f"[near_duplicates] {finding} (self-reported, not independently re-checked)"
            for finding in self_reported
        )

        if not findings:
            # Not "nothing notable": the analysis doesn't check everything,
            # so it must not read as "clean".
            findings.append(NO_ANALYSIS_FINDINGS)

        # A run with any self-reported (unverified) content is never as
        # confident as a run whose every finding was independently
        # recomputed from scratch.
        confidence = 0.7 if self_reported else 1.0
        return VerificationResult(passed=True, confidence=confidence, findings=tuple(findings))


def _scoped_execution_result(step: StepResult) -> ExecutionResult:
    """Build a single-step ``ExecutionResult`` so an existing ``Verifier``
    (e.g. ``SpreadsheetDedupeVerifier``, which only ever reads
    ``execution_result.success``/``.outputs``) can be reused unchanged for
    one step at a time, without ever needing to know ``CompositeVerifier``
    exists.

    ``success`` mirrors whether the Skill itself reported ``"failure"`` for
    this step — the literal string, not ``skills.base.SkillStatus``,
    matching the same domain-agnostic boundary ``StepResult.status``
    already documents. Everything else about the turn (``warnings``,
    ``errors``, other steps) is deliberately left out: a per-step Verifier
    should judge this step's own output, not the rest of the turn.
    """
    return ExecutionResult(
        success=step.status != "failure",
        outputs=step.outputs,
        steps=(step,),
    )


class _Verdict(NamedTuple):
    """One entry for ``_aggregate_verdicts``: a step's own verdict, and
    which file it was about."""

    capability: str
    result: VerificationResult
    input_reference: str = ""
    # A re-check of the final file: its failure doesn't fail the turn.
    recheck: bool = False
    # A READ-tier step (e.g. analysis of the original): outside the chain,
    # so its failure doesn't fail the turn either.
    read_only: bool = False


# The finding recorded for a step that never ran because an earlier one failed.
SKIPPED_FINDING = "skipped because an earlier step failed"

# The finding recorded when the re-check of the final file fails. Plain
# words, no capability id: it reaches the user as-is.
RECHECK_FAILED_FINDING = "the final file couldn't be re-checked after the changes"


def _aggregate_verdicts(per_step: Sequence[_Verdict]) -> VerificationResult:
    """Combine one ``VerificationResult`` per step into a single verdict.

    ``passed`` iff every step passed — one unverified or failed step means
    the turn as a whole isn't verified, the same all-or-nothing standard
    ``ExecutionResult.success`` already applies via ``not errors``. The
    exceptions are read-only steps outside the chain: a re-check of the
    final file (``recheck``) and a READ-tier step on the original
    (``read_only``, e.g. proactive analysis). They only add findings, so
    their failure is recorded on their own ``StepVerification`` but never
    fails the turn — unless nothing else was verified, as in a turn whose
    only step is an analysis the user asked for.
    ``confidence`` is the minimum across steps, since an aggregate claim is
    only ever as confident as its least confident component. ``findings``
    and ``recommendations`` are the concatenation of every step's own,
    each finding prefixed with the capability it came from, so the
    aggregate stays traceable back to which step said what — rather than
    collapsing multiple steps' evidence into one anonymous list.

    Also builds ``VerificationResult.steps`` — one ``StepVerification``
    per entry in ``per_step``, alongside (not instead of) the aggregated
    fields above — so a consumer can ask "did capability X's own step
    specifically pass?" without parsing prefixed finding strings. See
    ``docs/design/proactive-analysis-design.md`` Section 5.5.

    Args:
        per_step: One ``_Verdict`` per step that was actually verified, in
            step order.

    Returns:
        The aggregated ``VerificationResult``, or an honest "nothing was
        verified" result if ``per_step`` is empty.
    """
    if not per_step:
        return VerificationResult(
            passed=False, confidence=0.0, findings=("no steps were verified",)
        )
    blocking = [v.result for v in per_step if not (v.recheck or v.read_only)] or [
        v.result for v in per_step
    ]
    return VerificationResult(
        passed=all(result.passed for result in blocking),
        confidence=min(result.confidence for result in blocking),
        findings=tuple(
            f"[{v.capability}] {finding}" for v in per_step for finding in v.result.findings
        ),
        recommendations=tuple(
            recommendation for v in per_step for recommendation in v.result.recommendations
        ),
        steps=tuple(
            StepVerification(
                capability=v.capability,
                passed=v.result.passed,
                confidence=v.result.confidence,
                findings=v.result.findings,
                recommendations=v.result.recommendations,
                input_reference=v.input_reference,
                recheck=v.recheck,
                read_only=v.read_only,
                measurements=v.result.measurements,
            )
            for v in per_step
        ),
    )


class CompositeVerifier:
    """A ``Verifier`` that dispatches to a per-capability strategy.

    Mirrors the compositional pattern ``CompositePlanner`` uses for
    Planner (``buddy/planner.py``): a small registry mapping capability ->
    ``Verifier``, wrapped behind the same ``Verifier`` protocol every
    other implementation already satisfies. Adding it requires zero
    changes to the ``Inspector`` stage class itself — the same "stage
    needs no changes" property ``CompositePlanner`` already demonstrated
    for ``Planner``.

    Unlike ``SpreadsheetDedupeVerifier``, which only ever looks at
    ``execution_result.outputs[0]``, ``CompositeVerifier`` iterates every
    entry in ``execution_result.steps`` — Buddy's per-capability record of
    what each delegated request actually produced (see
    ``buddy.models.StepResult``) — selecting whichever registered
    ``Verifier`` declares that step's capability, or the shared fallback
    when none does. Each selected ``Verifier`` is still called with a
    request scoped to just its own step (see ``_scoped_execution_result``),
    so an existing single-output ``Verifier`` like
    ``SpreadsheetDedupeVerifier`` needs no changes to be reused here.
    """

    def __init__(
        self,
        verifiers_by_capability: Mapping[str, Verifier],
        *,
        fallback: Verifier | None = None,
    ) -> None:
        """Initialize the composed verifier.

        Args:
            verifiers_by_capability: Maps a capability string to the
                ``Verifier`` that knows how to independently check it.
            fallback: Used for any capability with no entry in
                ``verifiers_by_capability``, and when ``execution_result``
                has no steps to dispatch on at all. Defaults to
                ``NullVerifier`` — an honest "not independently checked"
                result, never a guessed pass.
        """
        self._verifiers_by_capability = dict(verifiers_by_capability)
        self._fallback = fallback if fallback is not None else NullVerifier()

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify ``request`` by dispatching each step to its own strategy.

        Args:
            request: The request to verify.

        Returns:
            The aggregated verdict across every step (see
            ``_aggregate_verdicts``), or the fallback's own verdict if
            there is nothing to dispatch on.
        """
        execution_result = request.execution_result
        if execution_result is None or not execution_result.steps:
            return self._fallback(request)

        per_step = [
            _Verdict(
                step.capability,
                self._verify_step(request, step),
                step.input_reference,
                step.recheck,
                step.permission_tier == "read",
            )
            for step in execution_result.steps
        ]

        final_check = self._verify_final_file(request)
        if final_check is not None:
            per_step.append(_Verdict(FINAL_OUTPUT_CAPABILITY, final_check))

        return _aggregate_verdicts(per_step)

    def _verify_step(self, request: VerificationRequest, step: StepResult) -> VerificationResult:
        """Verify one step with its own strategy, or say why it can't be.

        A skipped step never ran, and a step Hands failed itself
        (``failure_reason``: no Skill could run it, it wasn't confirmed, or
        its output couldn't be used) has no result worth checking — running
        a verifier would only report a confusing "no output file". Those
        get a failing verdict with the reason instead. A failed re-check of
        the final file gets a plain-words finding.
        """
        if step.status == SKIPPED_STATUS:
            return VerificationResult(passed=False, findings=(SKIPPED_FINDING,))
        if step.failure_reason:
            return VerificationResult(passed=False, confidence=1.0, findings=(step.failure_reason,))
        sheet_problem = _sheet_list_problem(step) or _plain_table_finding(step)
        if sheet_problem is not None:
            return VerificationResult(passed=False, confidence=1.0, findings=(sheet_problem,))
        verifier = self._verifiers_by_capability.get(step.capability, self._fallback)
        result = verifier(self._scoped_request(request, step))
        if step.recheck and not result.passed:
            return VerificationResult(
                passed=False, confidence=result.confidence, findings=(RECHECK_FAILED_FINDING,)
            )
        return result

    @staticmethod
    def _scoped_request(request: VerificationRequest, step: StepResult) -> VerificationRequest:
        """A request covering just ``step``, for a single-step ``Verifier``."""
        return VerificationRequest(
            understanding=request.understanding,
            execution_result=_scoped_execution_result(step),
            active_file=request.active_file,
        )

    def _verify_final_file(self, request: VerificationRequest) -> VerificationResult | None:
        """Check the file the user receives against every chained step's claim.

        Each step is verified against its own input, but only the final
        file is handed back — so it must satisfy *every* file-changing
        step's postcondition at once (e.g. no duplicates *and* no empty
        rows). Re-runs each such step's verifier against the final file,
        with no input reference, so only the output postcondition applies.

        Returns:
            ``None`` when there's no chain to check — fewer than two
            file-changing steps, steps that didn't actually read each
            other's output, or no final file because the chain stopped;
            otherwise the combined verdict for the final file.
        """
        execution_result = request.execution_result
        if execution_result is None or not execution_result.outputs:
            return None
        final_file = execution_result.outputs[0]
        chained = [
            step
            for step in execution_result.steps
            if step.status == "success" and step.outputs and not step.recheck
        ]
        if len(chained) < 2 or chained[-1].outputs[0] != final_file:
            return None
        if any(
            later.input_reference != earlier.outputs[0]
            for earlier, later in zip(chained, chained[1:])
        ):
            return None

        verdicts = []
        for step in chained:
            verifier = self._verifiers_by_capability.get(step.capability, self._fallback)
            final_step = replace(step, outputs=(final_file,), input_reference="")
            verdicts.append(verifier(self._scoped_request(request, final_step)))
        passed = all(v.passed for v in verdicts)
        return VerificationResult(
            passed=passed,
            confidence=min(v.confidence for v in verdicts),
            findings=tuple(f"final file: {f}" for v in verdicts for f in v.findings),
            recommendations=tuple(r for v in verdicts for r in v.recommendations),
            # With no input reference, each verifier measured only the final
            # file: its rows and the target rows remaining, never "removed".
            measurements=tuple(m for v in verdicts for m in v.measurements) if passed else (),
        )


_WORKBOOK_SUFFIXES = (".xlsx", ".xlsm", ".xls")


def _sheet_names(path: str) -> list[str]:
    """Every sheet's name in the workbook at ``path``, read fresh and read-only.

    The same declared-sheets reader the skills use, so a sheet a library
    would silently drop still counts here.
    """
    return sheet_names(path)


def _reads_a_workbook(step: StepResult) -> bool:
    """Whether ``step`` is a successful file-changing step that read a workbook."""
    return not (
        step.status != "success"
        or step.permission_tier == "read"
        or step.recheck
        or not step.outputs
        or not step.input_reference.lower().endswith(_WORKBOOK_SUFFIXES)
    )


def _sheet_list_problem(step: StepResult) -> str | None:
    """Say how a file-changing step's output lost or renamed a sheet, if it did.

    A structural check, independent of the capability and of what the Skill
    reported: a step that read a workbook must hand on a workbook with the
    same sheet names, in the same order. Each chain step is checked against
    the file it read (the previous step's output), so the final file's
    sheets match the original's.

    Returns:
        ``None`` if the sheet lists match, or the step isn't a successful
        file-changing step that read a workbook; otherwise the problem.
    """
    if not _reads_a_workbook(step):
        return None
    try:
        before, after = _sheet_names(step.input_reference), _sheet_names(step.outputs[0])
    except WorkbookRefused as exc:  # its text is fixed, so it can be shown
        return f"couldn't independently read the workbook's sheet names: {exc}"
    except Exception:  # noqa: BLE001 — surfaced as a finding, not raised
        logger.exception("Inspector: failed to read the sheet names of %r", step.outputs[0])
        return "couldn't independently read the workbook's sheet names"
    if before == after:
        return None
    return "the output's sheets don't match its input's, so a sheet was lost or renamed"


def _plain_table_finding(step: StepResult) -> str | None:
    """Say why the sheet a file-changing step read isn't a plain table, if it isn't.

    The Skills write values only, so a sheet with merged cells, a header that
    isn't on the first row, a column with no header or two equal headers would
    come back changed. This reads the step's input fresh with openpyxl (the
    same ``plain_table_problem`` the Skills refuse on, never a Skill's own
    DataFrame), so a Skill that didn't refuse still fails verification and
    nothing is handed back. Each chain step is checked against the file it
    read, so the user's original is checked at the first step.

    Returns:
        ``None`` if the input is a plain table, or the step isn't a successful
        file-changing step that read a workbook; otherwise the problem.
    """
    if not _reads_a_workbook(step):
        return None
    try:
        problem = plain_table_problem(step.input_reference)
    except WorkbookRefused as exc:  # its text is fixed, so it can be shown
        return f"couldn't independently check the sheet's layout: {exc}"
    if problem is None:
        return None
    return f"the sheet it read isn't a plain table: {problem}"


# The ``StepVerification.capability`` recorded for the final-file check —
# not a real capability, just the label for "the file the user receives".
FINAL_OUTPUT_CAPABILITY = "final_output"


class ResidualMeasurer:
    """A ``Verifier`` wrapper that measures what's left in the delivered file.

    Turn level only: wired around Inspector's verifier, never into Hands'
    per-step verifier. After the inner verifier passes a turn that handed a
    file back, it reads that file fresh and counts, for each action in the
    requested-actions ledger (``buddy.actions.requested_ledger``) that has a
    measured target, the rows and the target rows still in it. The reply
    uses these only for a requested action that wasn't done ("It still has
    1 fully-empty row(s)."), per ``docs/design/verified-outcome-design.md``
    Section 2.5.

    It never changes the verdict: ``passed``, ``findings`` and every other
    field are the inner verifier's. It only reads the delivered file, never
    the original, so rule 2 isn't touched. If the file can't be read, there
    are no residuals, and the reply claims nothing about what's left.
    """

    def __init__(self, inner: Verifier) -> None:
        """Initialize the wrapper.

        Args:
            inner: The verifier whose verdict is kept (``CompositeVerifier``).
        """
        self._inner = inner

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify with the inner verifier, then measure the delivered file.

        Args:
            request: The request to verify.

        Returns:
            The inner verdict, with ``residuals`` set when it passed a turn
            that delivered a file and the file could be read.
        """
        result = self._inner(request)
        execution_result = request.execution_result
        if (
            not result.passed
            or execution_result is None
            or not execution_result.outputs
            or execution_result.stopped_at is not None
        ):
            return result
        targets: list[str] = []
        for entry in requested_ledger(request.understanding):
            action = ACTIONS_BY_NAME.get(entry.action)
            if action is not None and action.target not in targets:
                targets.append(action.target)
        if not targets:
            return result
        delivered = execution_result.outputs[0]
        try:
            written = _read_cells(delivered)  # fresh, independent read of the delivered file
        except Exception:  # noqa: BLE001 — no residuals, and nothing is claimed
            logger.exception("ResidualMeasurer: failed to read %r", delivered)
            return result
        residuals = tuple(
            StepMeasurement(
                target=target, output_rows=len(written), remaining=_TARGET_COUNTERS[target](written)
            )
            for target in targets
            if target in _TARGET_COUNTERS
        )
        return replace(result, residuals=residuals)


class OriginalMeasurer:
    """A ``Verifier`` wrapper that measures what the user's original has that the copy can't keep.

    Turn level only, like ``ResidualMeasurer``. After the inner verifier
    passes a turn that handed a file back, it reads the file the first
    file-changing step read (the user's own file, also in a chain) fresh with
    openpyxl and counts its formula cells, and how many of them have no saved
    value. The reply says so in a fixed sentence
    (``buddy.outcome``), because the Skills write values only. If the
    original can't be read, ``formula_cells`` is ``None`` and the reply says it
    couldn't check; it is never silently left out.

    It never changes the verdict: ``passed``, ``findings`` and every other
    field are the inner verifier's, and it never writes anything, so rule 2
    isn't touched. See ``docs/design/values-only-copy.md`` Section 2.
    """

    def __init__(self, inner: Verifier) -> None:
        """Initialize the wrapper.

        Args:
            inner: The verifier whose verdict is kept.
        """
        self._inner = inner

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify with the inner verifier, then measure the original.

        Args:
            request: The request to verify.

        Returns:
            The inner verdict, with ``original_facts`` set when it passed a
            turn that delivered a file.
        """
        result = self._inner(request)
        execution_result = request.execution_result
        if (
            not result.passed
            or execution_result is None
            or not execution_result.outputs
            or execution_result.stopped_at is not None
        ):
            return result
        original = next(
            (step.input_reference for step in execution_result.steps if _reads_a_workbook(step)),
            None,
        )
        if original is None:
            return result
        try:
            counts = formula_cell_counts(original)  # fresh, independent read of the original
        except Exception:  # noqa: BLE001 — "couldn't check" is said, never a count
            logger.exception("OriginalMeasurer: failed to read %r", original)
            return replace(result, original_facts=OriginalFacts(formula_cells=None))
        return replace(
            result,
            original_facts=OriginalFacts(
                formula_cells=counts.formulas, formula_cells_without_value=counts.without_value
            ),
        )


class Inspector:
    """The Inspector stage of Buddy's cognitive cycle.

    Inspector independently checks the outcome collected during Hands
    against the *original* intent captured back in Brain — not
    against what a Skill merely reported (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4; ``BUDDY_SKILLS.md``
    Section 9). A Skill's own self-check (``Skill.verify()``) confirms its
    result is well-formed; it is necessary but never sufficient —
    Inspector's judgment is the one that actually decides whether the
    work succeeded.

    Inspector contains no verification logic of its own. All of the actual
    checking is delegated to an injected ``Verifier``. Inspector's own
    responsibility is purely mechanical: assemble the verifier's input
    from ``BuddyState``, call the verifier, and record what it returns.
    This keeps Inspector a stable, swappable framework — any implementation
    conforming to ``Verifier`` can be substituted without ever changing
    this stage.
    """

    def __init__(self, verifier: Verifier | None = None) -> None:
        """Initialize the stage.

        Args:
            verifier: The verifier to delegate checking to. Defaults to
                ``NullVerifier``, which never claims success it hasn't
                checked.
        """
        if verifier is None:
            logger.debug("No verifier provided; falling back to NullVerifier")
        self._verifier = verifier if verifier is not None else NullVerifier()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Verify ``state`` and record the resulting judgment.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the ``VerificationResult`` recorded in
            ``state.verification_result``.
        """
        request = self._build_request(state)
        result = self._verifier(request)
        self._apply(state, result)
        self._mark_analyzed_files(state, result)
        logger.debug(
            "Inspector recorded passed=%s confidence=%.2f for session %r",
            result.passed,
            result.confidence,
            state.session_id,
        )
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> VerificationRequest:
        """Assemble the verifier's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` into a plain, self-contained bundle. It
        performs no verification of its own. If Brain has not
        recorded an ``Understanding`` yet, an empty one is used rather
        than guessing at what it might have contained.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured verifier.
        """
        return VerificationRequest(
            understanding=state.understanding or Understanding(),
            execution_result=state.execution_result,
            active_file=state.context.active_file,
        )

    @staticmethod
    def _apply(state: BuddyState, result: VerificationResult) -> None:
        """Record the verifier's output onto ``state``.

        Args:
            state: The state to update.
            result: The verification result to record.
        """
        state.verification_result = result

    @staticmethod
    def _mark_analyzed_files(state: BuddyState, result: VerificationResult) -> None:
        """Mark this turn's target file as analyzed, once genuinely earned.

        Only an analysis of *this* file counts: the verdict's
        ``input_reference`` must be the active file, so a passing re-check
        of the chain's final file never marks the original as analyzed.

        Reads ``result.steps`` — the per-step, unaggregated verdict — not
        the aggregate ``result.passed``: one other step failing in the
        same turn must never stop a genuinely-passed ``spreadsheet
        .analyze`` step from being marked done, and an aggregate pass must
        never be misread as this specific step having passed. Marking
        happens only once the analysis has been *independently confirmed*
        by ``SpreadsheetAnalysisVerifier`` — not merely because the Skill
        self-reported success — so a check that fails in a way the
        Skill's own ``verify()`` didn't catch is retried next turn instead
        of silently never being looked at again. See
        ``docs/design/proactive-analysis-design.md`` Section 1.2.

        Args:
            state: The state to update.
            result: The verification result just computed for this turn.
        """
        target_file = state.context.active_file
        if target_file is None:
            return
        analyze_passed = any(
            step.capability == "spreadsheet.analyze"
            and step.passed
            and step.input_reference == target_file
            for step in result.steps
        )
        if analyze_passed:
            state.context.mark_analyzed(target_file)
