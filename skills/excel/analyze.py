"""Analyze — proactively looks over an uploaded spreadsheet for missing
values, outliers, inconsistent date formats, grouped-key conflicts, and
near-duplicate, exact-duplicate and trim/case-duplicate rows, independent of whatever the user
asked for.

See ``docs/design/proactive-analysis-design.md`` for the full design this
implements, and ``scripts/spike_tools_test.py`` for the real-tool spike
its choices are grounded in.
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask
from skills.excel.workbook import WorkbookRefused, check_workbook_limits

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = (".xlsx", ".xls")

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
_NAME_COLUMN_PATTERN = re.compile(r"name", re.IGNORECASE)
_DISCRIMINATING_COLUMN_PATTERN = re.compile(r"phone|email|tel", re.IGNORECASE)
_KEY_COLUMN_PATTERN = _DISCRIMINATING_COLUMN_PATTERN
_NEAR_DUPLICATE_ROW_LIMIT = 10_000
_NO_ANALYTICS_ENV = "YDATA_PROFILING_NO_ANALYTICS"


class Analyzer(Protocol):
    """Produces plain-language findings for one check, from a DataFrame.

    Internal to ``SpreadsheetAnalysisSkill`` — not a Buddy-core protocol,
    so it needs no Registry/Dispatcher awareness. See
    ``docs/design/proactive-analysis-design.md`` Section 6. Deliberately
    independent, separately-coded logic from any equivalent check in
    ``buddy.inspector`` — a Skill must never depend on ``buddy/`` (see
    ``docs/BUDDY_SKILLS.md`` Section 2), and duplicating rather than
    sharing code is what makes the Verifier's later recomputation a
    genuine second opinion rather than the same bug affecting both.
    """

    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
        """Return this analyzer's plain-language findings for ``df``."""
        ...


class MissingValueAndDistributionAnalyzer:
    """Flags missing values via ydata-profiling.

    See ``docs/design/proactive-analysis-design.md`` Section 2. Run with
    ``minimal=True`` and no HTML report generated — this needs to stay
    cheap enough to run on every upload, not produce a report for a human
    to read later. Only the ``MISSING`` alert is surfaced as a finding;
    per-column distribution stats are available on
    ``report.get_description().variables`` but aren't surfaced as
    separate findings today, since this design doesn't specify a
    materiality threshold for them.
    """

    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
        # ydata-profiling reports the shape of every analysed sheet to its
        # vendor unless this is set, and it only tests that the value is
        # non-empty, so it is assigned, not defaulted. Set before the import.
        os.environ[_NO_ANALYTICS_ENV] = "1"
        from ydata_profiling import ProfileReport
        from ydata_profiling.model.alerts import AlertType

        report = ProfileReport(df, minimal=True, progress_bar=False)
        description = report.get_description()
        return tuple(
            str(alert) for alert in description.alerts if alert.alert_type is AlertType.MISSING
        )


class ExactDuplicateRowAnalyzer:
    """Flags rows that repeat an earlier row exactly, with a count.

    The same definition the cleaning skill removes by (``DataFrame.duplicated``),
    so a "still has N duplicate rows" after cleaning and this finding agree.
    Backlog item 3 (L-4).
    """

    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
        count = int(df.duplicated().sum())
        if count == 0:
            return ()
        verb = "repeats" if count == 1 else "repeat"
        return (f"{count} {'row' if count == 1 else 'rows'} {verb} an earlier row exactly",)


class LooseDuplicateRowAnalyzer:
    """Flags rows that match an earlier row once spaces and capital letters are ignored.

    Only rows that are *not* exact repeats: those are ``ExactDuplicateRowAnalyzer``'s, and the
    cleaning skill removes them. These it keeps, because it removes only rows identical in every
    cell; a finding says so, so a person isn't left to think "remove duplicates" got them.
    Text cells are compared with the spaces at both ends trimmed and the case folded; every other
    cell as it is.
    """

    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
        loosened = df.apply(
            lambda column: column.map(
                lambda value: value.strip().casefold() if isinstance(value, str) else value
            )
        )
        count = int((loosened.duplicated() & ~df.duplicated()).sum())
        if count == 0:
            return ()
        if count == 1:
            return (
                "1 row matches an earlier row once spaces around the text and capital letters "
                "are ignored, but isn't an exact repeat",
            )
        return (
            f"{count} rows match an earlier row once spaces around the text and capital "
            "letters are ignored, but aren't exact repeats",
        )


class IQROutlierAnalyzer:
    """Flags numeric outliers via a simple, dependency-free IQR check.

    Deliberately not a ydata-profiling extension — see
    ``docs/design/proactive-analysis-design.md`` Section 3 for why.
    """

    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
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
                f"{column}: {len(outliers)} unusually high/low "
                f"{'value' if len(outliers) == 1 else 'values'} (e.g. {examples})"
            )
        return tuple(findings)


def _matched_date_format(value: str) -> str | None:
    """Return the first of ``_DATE_FORMATS`` that parses ``value`` exactly."""
    for date_format in _DATE_FORMATS:
        try:
            datetime.strptime(value, date_format)
            return date_format
        except ValueError:
            continue
    return None


class DateFormatConsistencyAnalyzer:
    """Flags a column whose date-like values use more than one format.

    See ``docs/design/proactive-analysis-design.md`` Section 4.1. A
    column only qualifies as "date-like" if at least half its non-null
    values match one of a small, fixed list of explicit formats — this
    excludes ordinary text columns from being dragged into this check.
    """

    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
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
                continue
            distinct_formats = sorted({fmt for fmt in matched_formats.dropna().unique()})
            unparseable = int(matched_formats.isna().sum())
            if len(distinct_formats) <= 1 and unparseable == 0:
                continue
            parts = []
            if distinct_formats:
                parts.append(
                    f"{len(distinct_formats)} different "
                    f"{'format' if len(distinct_formats) == 1 else 'formats'}: "
                    f"{', '.join(distinct_formats)}"
                )
            if unparseable:
                parts.append(
                    f"{unparseable} {'value that doesn' if unparseable == 1 else 'values that don'}"
                    "'t match a recognized date format"
                )
            findings.append(f"{column}: " + "; ".join(parts))
        return tuple(findings)


class GroupedKeyConflictAnalyzer:
    """Flags rows that share an identity column but disagree on another.

    Deliberately limited to exact-match grouping on an email/phone-like
    column — never guessing at arithmetic/derived-column relationships.
    See ``docs/design/proactive-analysis-design.md`` Section 4.2.
    """

    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
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
                    f"{len(conflicts)} {'value' if len(conflicts) == 1 else 'values'} of "
                    f"{key_column!r} {'is' if len(conflicts) == 1 else 'are'} linked to more "
                    f"than one {other_column!r} (e.g. {key_column}={example_key!r})"
                )
        return tuple(findings)


class NearDuplicateAnalyzer:
    """Flags likely-duplicate rows via Splink, discriminated by phone/email.

    See ``docs/design/proactive-analysis-design.md`` Section 4.3. Never
    guesses which columns to compare: contributes no findings at all if
    it can't identify both a name-like column and a discriminating
    (phone/email/tel-like) column. Skips entirely above
    ``_NEAR_DUPLICATE_ROW_LIMIT`` rows without a smarter blocking strategy
    than this design covers — a named, honest scale limit, not a promise
    this scales unconditionally.
    """

    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
        if len(df) > _NEAR_DUPLICATE_ROW_LIMIT:
            return ()
        name_column = next((c for c in df.columns if _NAME_COLUMN_PATTERN.search(c)), None)
        discriminating_column = next(
            (c for c in df.columns if _DISCRIMINATING_COLUMN_PATTERN.search(c)), None
        )
        if name_column is None or discriminating_column is None:
            return ()
        if len(df) < 2:
            return ()

        import splink.comparison_library as cl
        from splink import DuckDBAPI, Linker, SettingsCreator, block_on

        working = df[[name_column, discriminating_column]].copy()
        working.insert(0, "unique_id", range(len(working)))
        working[name_column] = working[name_column].astype(str)
        working[discriminating_column] = working[discriminating_column].astype(str)

        settings = SettingsCreator(
            link_type="dedupe_only",
            comparisons=[cl.NameComparison(name_column), cl.ExactMatch(discriminating_column)],
            blocking_rules_to_generate_predictions=[
                block_on(f"substr({name_column}, 1, 3)"),
                block_on(discriminating_column),
            ],
        )
        try:
            linker = Linker(working, settings, db_api=DuckDBAPI(), set_up_basic_logging=False)
            linker.training.estimate_u_using_random_sampling(max_pairs=1e5)
            linker.training.estimate_parameters_using_expectation_maximisation(
                block_on(f"substr({name_column}, 1, 3)")
            )
            linker.training.estimate_parameters_using_expectation_maximisation(
                block_on(discriminating_column)
            )
            predictions = linker.inference.predict().as_pandas_dataframe()
        except Exception:  # noqa: BLE001 — degrade gracefully, never crash the whole analysis
            logger.exception("NearDuplicateAnalyzer: Splink training/prediction failed")
            return ()

        findings: list[str] = []
        name_left, name_right = f"{name_column}_l", f"{name_column}_r"
        # Threshold set from real observed behavior (scripts/spike_tools_test.py),
        # not a textbook round number: with unsupervised EM training on a
        # realistically small upload, genuine spelling-variant duplicates
        # scored 0.21-0.28 match_probability, while true name collisions
        # (different people, different phone) scored ~0.0000. 0.15 sits
        # comfortably between the two, matched to what this exact model
        # actually produces rather than an arbitrary "0.5" cutoff.
        likely = predictions[predictions["match_probability"] >= 0.15]
        for _, row in likely.iterrows():
            findings.append(
                f"rows with {name_column} {row[name_left]!r} and {row[name_right]!r} look "
                f"like they might be the same (match probability "
                f"{row['match_probability']:.2f})"
            )
        return tuple(findings)


# The checks that read the cells as they are (no text-to-number conversion), because they count
# what the cleaning skills count.
_CELL_CHECKS = frozenset({"duplicate_rows", "loose_duplicate_rows"})

_ANALYZERS: tuple[tuple[str, Analyzer], ...] = (
    ("missing_and_distribution", MissingValueAndDistributionAnalyzer()),
    ("duplicate_rows", ExactDuplicateRowAnalyzer()),
    ("loose_duplicate_rows", LooseDuplicateRowAnalyzer()),
    ("outliers", IQROutlierAnalyzer()),
    ("date_format_consistency", DateFormatConsistencyAnalyzer()),
    ("grouped_key_conflicts", GroupedKeyConflictAnalyzer()),
    ("near_duplicates", NearDuplicateAnalyzer()),
)


class SpreadsheetAnalysisSkill(Skill):
    """Proactively analyzes an uploaded spreadsheet for data-quality issues.

    Reads the uploaded ``.xlsx``/``.xls`` file read-only and runs five
    independent analyzers (see ``Analyzer``) over it. Writes no file at
    all — every finding is communicated through ``SkillResult.summary``
    and ``SkillResult.self_reported_findings``, never a new path under
    ``workspace/outputs/``. This is deliberate, not an oversight: see
    ``docs/design/proactive-analysis-design.md`` Section 5.4 for why
    writing a file here would have forced this Skill onto the Write tier,
    directly undermining "runs automatically, independent of the user's
    stated request."

    Only ``NearDuplicateAnalyzer``'s findings are carried on
    ``self_reported_findings`` — the one category ``SpreadsheetAnalysisVerifier``
    (``buddy/inspector.py``) treats as self-reported rather than
    independently re-derived. The other analyzers' findings feed only
    this Skill's own ``summary`` and its own well-formedness self-check;
    ``SpreadsheetAnalysisVerifier`` fully recomputes those independently
    from the original upload and never reads this Skill's copy of them.
    """

    @property
    def name(self) -> str:
        """The skill's unique, human-readable identifier."""
        return "excel.analyze"

    @property
    def description(self) -> str:
        """A plain-language description of what this skill does."""
        return "Proactively looks over an uploaded spreadsheet for data-quality issues."

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        """The capabilities this skill declares support for."""
        return ("spreadsheet.analyze",)

    @property
    def permission_tier(self) -> PermissionTier:
        """The permission tier required to invoke this skill.

        Genuinely Read tier: this skill never writes a file and never
        modifies the upload (see the class docstring and
        ``docs/design/proactive-analysis-design.md`` Section 5.4).
        """
        return PermissionTier.READ

    def can_handle(self, task: SkillTask) -> bool:
        """Determine whether this skill is capable of performing a task.

        Args:
            task: The task under consideration.

        Returns:
            True if ``task`` has exactly one input reference ending in a
            supported spreadsheet extension, False otherwise.
        """
        if len(task.input_references) != 1:
            return False
        return task.input_references[0].lower().endswith(_SUPPORTED_EXTENSIONS)

    def execute(self, task: SkillTask) -> SkillResult:
        """Run every analyzer over the uploaded spreadsheet.

        Args:
            task: The task to execute.

        Returns:
            A ``SkillResult`` describing what each analyzer found. Any
            single analyzer's failure is logged and treated as "no
            findings from that analyzer" rather than failing the whole
            task — a partial analysis is more useful than none. A failure
            reading the input file at all is the only path to
            ``SkillStatus.FAILURE``.
        """
        if not self.can_handle(task):
            return SkillResult(
                status=SkillStatus.FAILURE,
                errors=[
                    f"cannot handle task {task.task_id!r}: expected exactly one input "
                    f"reference ending in {_SUPPORTED_EXTENSIONS}"
                ],
            )

        input_path = task.input_references[0]
        try:
            check_workbook_limits(input_path)
        except WorkbookRefused as refused:  # its text is fixed, so it can be shown
            logger.info("SpreadsheetAnalysisSkill: refused %r: %s", input_path, refused)
            return SkillResult(status=SkillStatus.FAILURE, errors=[str(refused)])
        try:
            df = pd.read_excel(input_path)  # READ-ONLY open of the upload
            # The cells as they are, for the duplicate checks: the cleaning skills compare rows
            # without converting a text cell to a number, so "exact duplicate" means that here too.
            cells = pd.read_excel(input_path, dtype=object)  # READ-ONLY open of the upload
        except Exception:  # noqa: BLE001 — surfaced as a SkillResult, not raised
            logger.exception("SpreadsheetAnalysisSkill: failed to read %r", input_path)
            return SkillResult(status=SkillStatus.FAILURE, errors=["could not read the workbook"])

        near_duplicate_findings: tuple[str, ...] = ()
        total_finding_count = 0
        for check_name, analyzer in _ANALYZERS:
            try:
                findings = analyzer(cells if check_name in _CELL_CHECKS else df)
            except Exception:  # noqa: BLE001 — one analyzer's failure isn't the whole task's
                logger.exception("SpreadsheetAnalysisSkill: %s analyzer failed", check_name)
                findings = ()
            total_finding_count += len(findings)
            if check_name == "near_duplicates":
                near_duplicate_findings = findings

        file_name = Path(input_path).name
        summary = (
            f"Looked over {file_name}: found {total_finding_count} "
            f"{'thing' if total_finding_count == 1 else 'things'} worth a look."
            if total_finding_count
            else f"Looked over {file_name}: nothing stood out."
        )
        return SkillResult(
            status=SkillStatus.SUCCESS,
            outputs=[],
            summary=summary,
            self_reported_findings=near_duplicate_findings,
            metadata={"finding_count": total_finding_count},
        )

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        """Check that ``result`` is well-formed and consistent with ``task``.

        This skill never produces an output file, so well-formedness here
        means: the result reports success, claims no output file (one
        would be a contract violation for this skill — see the class
        docstring), and has a non-empty summary.

        Args:
            task: The task the result was produced for.
            result: The result to verify.

        Returns:
            True if ``result`` is well-formed for this skill.
        """
        if result.status is not SkillStatus.SUCCESS:
            return False
        if result.outputs:
            return False
        return bool(result.summary)
