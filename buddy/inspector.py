"""Inspector — checks executed work against the user's original intent."""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd

from buddy.models import (
    ExecutionResult,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.state import BuddyState

logger = logging.getLogger(__name__)


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
            written = pd.read_excel(output_path)  # fresh, independent read of the artifact
        except Exception as exc:  # noqa: BLE001 — surfaced as a finding, not raised
            logger.exception("SpreadsheetDedupeVerifier: failed to read %r", output_path)
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=(f"could not independently read {output_path!r}: {exc}",),
            )

        duplicate_count = int(written.duplicated().sum())  # recomputed from scratch
        passed = duplicate_count == 0
        return VerificationResult(
            passed=passed,
            confidence=1.0,
            findings=(
                f"output has {len(written)} row(s), {duplicate_count} duplicate row(s) remaining",
            ),
            recommendations=() if passed else ("re-run deduplication; duplicates remain",),
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
            written = pd.read_excel(output_path)  # fresh, independent read of the artifact
        except Exception as exc:  # noqa: BLE001 — surfaced as a finding, not raised
            logger.exception("SpreadsheetEmptyRowsVerifier: failed to read %r", output_path)
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=(f"could not independently read {output_path!r}: {exc}",),
            )

        empty_row_count = int(written.isna().all(axis=1).sum())  # recomputed from scratch
        passed = empty_row_count == 0
        return VerificationResult(
            passed=passed,
            confidence=1.0,
            findings=(
                f"output has {len(written)} row(s), "
                f"{empty_row_count} fully-empty row(s) remaining",
            ),
            recommendations=() if passed else ("re-run empty-row removal; empty rows remain",),
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
        findings.append(f"{column}: {missing} missing value(s) ({percentage:.1f}%)")
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
            f"{column}: {len(outliers)} unusually high/low value(s) (e.g. {examples})"
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
            parts.append(f"{len(distinct_formats)} different format(s): {', '.join(distinct_formats)}")
        if unparseable:
            parts.append(f"{unparseable} value(s) that don't match a recognized date format")
        findings.append(f"{column}: " + "; ".join(parts))
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
                f"{len(conflicts)} value(s) of {key_column!r} are linked to more than one "
                f"{other_column!r} (e.g. {key_column}={example_key!r})"
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
                findings=("spreadsheet.analyze reported failure",),
            )

        if request.active_file is None:
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=("no active file available to independently re-check",),
            )

        try:
            df = pd.read_excel(request.active_file)  # fresh, independent read of the ORIGINAL
        except Exception as exc:  # noqa: BLE001 — surfaced as a finding, not raised
            logger.exception(
                "SpreadsheetAnalysisVerifier: failed to read %r", request.active_file
            )
            return VerificationResult(
                passed=False,
                confidence=1.0,
                findings=(f"could not independently read {request.active_file!r}: {exc}",),
            )

        findings: list[str] = []
        for category, category_findings in (
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
            findings.append("nothing notable found")

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


def _aggregate_verdicts(per_step: Sequence[tuple[str, VerificationResult]]) -> VerificationResult:
    """Combine one ``VerificationResult`` per step into a single verdict.

    ``passed`` iff every step passed — one unverified or failed step means
    the turn as a whole isn't verified, the same all-or-nothing standard
    ``ExecutionResult.success`` already applies via ``not errors``.
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
        per_step: ``(capability, VerificationResult)`` pairs, one per step
            that was actually verified, in step order.

    Returns:
        The aggregated ``VerificationResult``, or an honest "nothing was
        verified" result if ``per_step`` is empty.
    """
    if not per_step:
        return VerificationResult(
            passed=False, confidence=0.0, findings=("no steps were verified",)
        )
    return VerificationResult(
        passed=all(result.passed for _, result in per_step),
        confidence=min(result.confidence for _, result in per_step),
        findings=tuple(
            f"[{capability}] {finding}"
            for capability, result in per_step
            for finding in result.findings
        ),
        recommendations=tuple(
            recommendation for _, result in per_step for recommendation in result.recommendations
        ),
        steps=tuple(
            StepVerification(
                capability=capability,
                passed=result.passed,
                confidence=result.confidence,
                findings=result.findings,
                recommendations=result.recommendations,
            )
            for capability, result in per_step
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

        per_step: list[tuple[str, VerificationResult]] = []
        for step in execution_result.steps:
            verifier = self._verifiers_by_capability.get(step.capability, self._fallback)
            scoped_request = VerificationRequest(
                understanding=request.understanding,
                execution_result=_scoped_execution_result(step),
                active_file=request.active_file,
            )
            per_step.append((step.capability, verifier(scoped_request)))

        return _aggregate_verdicts(per_step)


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
            step.capability == "spreadsheet.analyze" and step.passed for step in result.steps
        )
        if analyze_passed:
            state.context.mark_analyzed(target_file)
