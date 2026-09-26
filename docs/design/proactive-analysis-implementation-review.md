# Proactive Analysis Implementation — Focused Safety Review

**Scope:** This is not a full-file review. Per the instruction that
requested it, this covers only the safety/correctness-critical surface of
implementing `docs/design/proactive-analysis-design.md`: the Verifier
that runs unsupervised against a real uploaded file, the aggregation
change that decides what a Verifier actually sees and what survives into
`VerificationResult`, and the two tests that prove the two behavioral
guarantees this design exists to provide. Everything else the
implementation touched (the Skill itself, the Planner wrapper, Context,
Voice's prompt text) is mechanical by comparison and is not reproduced
here — see the design doc and the diff for those.

---

## 1. `SpreadsheetAnalysisVerifier`, in full (`buddy/inspector.py`)

This is the one new piece of code in this batch that opens an arbitrary
user-uploaded file and runs unsupervised logic against it. It never
receives user input directly (it reads a path, not file content, from
`VerificationRequest.active_file`), but it's still the most
security/correctness-sensitive new surface: a bug here means Buddy tells
the user something false about their own data with high apparent
confidence.

```python
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
    for date_format in _DATE_FORMATS:
        try:
            datetime.strptime(value, date_format)
            return date_format
        except ValueError:
            continue
    return None


def _independent_missing_value_findings(df: pd.DataFrame) -> tuple[str, ...]:
    findings: list[str] = []
    for column in df.columns:
        missing = int(df[column].isna().sum())
        if missing == 0:
            continue
        percentage = 100 * missing / len(df) if len(df) else 0.0
        findings.append(f"{column}: {missing} missing value(s) ({percentage:.1f}%)")
    return tuple(findings)


def _independent_outlier_findings(df: pd.DataFrame) -> tuple[str, ...]:
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
    """Independently confirms SpreadsheetAnalysisSkill's proactive findings.

    Two different treatments for two different kinds of claim, per
    docs/design/proactive-analysis-design.md Section 5.2:

    - Fully re-derived, independently (missing values, outliers,
      date-format consistency, grouped-key conflicts): recomputed from
      scratch directly against request.active_file — the ORIGINAL
      upload, opened fresh, exactly the same "deliberately blind to what
      the Skill said" discipline SpreadsheetDedupeVerifier already
      applies.
    - Self-reported, not independently re-run (near-duplicates): passed
      through from StepResult.self_reported_findings unmodified,
      explicitly labeled as self-reported, at a capped confidence.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
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
        except Exception as exc:
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

        confidence = 0.7 if self_reported else 1.0
        return VerificationResult(passed=True, confidence=confidence, findings=tuple(findings))
```

**What to check this against:**

- **Never trusts the Skill.** `step.status` is read only to detect an
  outright reported failure; `step.summary`/`step.outputs` are never
  read at all. Every fully-re-derived finding comes from `df`, freshly
  read from `request.active_file` in this same call. Confirmed by test:
  `test_spreadsheet_analysis_verifier_independently_recomputes_missing_values`
  passes a `_analysis_step(summary="I found nothing at all, trust me.")`
  — a deliberately wrong self-report — and asserts the Verifier's own
  finding is correct anyway.
- **Read is exception-safe.** `pd.read_excel` failure (missing file,
  corrupt file) is caught and surfaced as a finding, `passed=False` —
  never an unhandled exception propagating out of the cognitive cycle.
- **No arbitrary code execution risk.** `pd.read_excel` on an `.xlsx`
  parses a Zip/XML container via `openpyxl`; nothing here evaluates
  formulas, macros, or embedded content. The four independent-check
  functions are pure pandas/`datetime` operations over already-parsed
  cell values — no `eval`, no dynamic attribute access driven by file
  content, no path built from a value read out of the spreadsheet
  (`request.active_file` is a workspace-scoped reference Buddy's own
  core resolved earlier in the turn, not something taken from the file's
  contents).
- **Confidence is honest.** `1.0` only when every finding was
  independently recomputed; `0.7` the moment any self-reported (Splink)
  content is included — never silently presented at the same confidence
  as a fully-checked claim. Confirmed by
  `test_spreadsheet_analysis_verifier_passes_near_duplicates_through_as_self_reported`.
- **`passed` has a narrow, honest meaning.** `True` means "the check ran
  and held together," not "the file has no issues" — there is no
  user-intent being judged here (see the design doc Section 5.2's own
  reasoning for why this capability doesn't have a pass/fail intent in
  the first place). The only paths to `False` are: nothing to verify, the
  Skill itself failed, no file to check against, or the file couldn't be
  read.

---

## 2. `CompositeVerifier` aggregation change (`buddy/inspector.py`)

Two changes to existing, already-relied-upon code, both additive:

```python
def _aggregate_verdicts(per_step: Sequence[tuple[str, VerificationResult]]) -> VerificationResult:
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
        steps=tuple(                                    # <-- NEW
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
```

```python
class CompositeVerifier:
    def __call__(self, request: VerificationRequest) -> VerificationResult:
        execution_result = request.execution_result
        if execution_result is None or not execution_result.steps:
            return self._fallback(request)

        per_step: list[tuple[str, VerificationResult]] = []
        for step in execution_result.steps:
            verifier = self._verifiers_by_capability.get(step.capability, self._fallback)
            scoped_request = VerificationRequest(
                understanding=request.understanding,
                execution_result=_scoped_execution_result(step),
                active_file=request.active_file,             # <-- NEW
            )
            per_step.append((step.capability, verifier(scoped_request)))

        return _aggregate_verdicts(per_step)
```

**Why each change matters, and what to check it against:**

- **`active_file` passthrough.** Without this line, every Verifier
  dispatched through `CompositeVerifier` — which is all of them, in the
  real wiring — would see `active_file=None` regardless of what the
  Inspector stage resolved, silently breaking
  `SpreadsheetAnalysisVerifier` (it would always take the "no active
  file available" branch). This is exactly the kind of gap that's easy
  to miss because nothing raises or fails loudly — it would just quietly
  make every analysis run's confirmation weaker than intended.
  `test_composite_verifier_passes_active_file_through_to_each_scoped_request`
  guards this directly with a recording fake Verifier.
- **`steps` on the aggregate.** This is purely additive — every existing
  field (`passed`, `confidence`, `findings`, `recommendations`) is
  computed exactly as before, unchanged; `steps` is a new field appended
  on the end, default `()` for anything that doesn't set it. No existing
  consumer of `VerificationResult` needed to change, and none did.
  `test_composite_verifier_builds_a_per_step_verification_breakdown`
  checks the new field's exact contents against two independently
  configured fake Verifiers.
- **Why this one matters more than it looks:** `VerificationResult.steps`
  is what makes it possible to tell "did capability X's own step
  specifically pass" apart from "did the whole turn pass" — which is
  exactly the distinction both requested tests below depend on. Get this
  aggregation wrong (e.g. accidentally reuse the same `VerificationResult`
  object instance across `StepVerification` entries, or transpose
  `capability`/`result` in the tuple unpacking) and both downstream
  guarantees — the fold-in rule and `mark_analyzed`'s per-step gating —
  would silently read the wrong step's verdict instead of failing loudly.

---

## 3. The two requested tests, in full

### 3.1 Fold-in rule: a near-duplicate finding must not be folded into an exact-dedupe step

Two versions were written: a full end-to-end integration test (real
Splink, real `ExcelDeduplicateSkill`, real `TemplateSpeaker`) and a
narrower `TemplateSpeaker`-only unit test proving the same rule in
isolation. Both are included below since each guards a different layer.

**Integration** (`tests/buddy/test_engine_factory.py`):

```python
def test_full_slice_does_not_fold_a_near_duplicate_finding_into_an_exact_dedupe_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    pd.DataFrame(_NEAR_DUPLICATE_AND_COLLISION_ROWS, columns=["name", "phone"]).to_excel(
        tmp_path / input_path, index=False
    )

    understand_reply = json.dumps({
        "intent": "remove_duplicate_rows",
        "confidence": 0.95,
        "referenced_files": [str(input_path)],
        "referenced_work_items": [],
        "missing_information": [],
        "ambiguity_flags": [],
        "notes": "",
    })
    think_reply = json.dumps({
        "objective": "Remove duplicate rows from the uploaded spreadsheet.",
        "summary": "The request is clear and a file is available.",
        "assumptions": [], "constraints": [], "risks": [],
        "recommended_strategy": "Use the Excel skill to deduplicate rows.",
        "clarification_required": False,
        "confidence": 0.9,
    })
    provider = _QueuedLLMProvider([understand_reply, think_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove duplicate rows"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    assert state.execution_result is not None
    assert state.execution_result.success is True
    capabilities = {step.capability for step in state.execution_result.steps}
    assert capabilities == {"spreadsheet.analyze", "spreadsheet.deduplicate"}

    assert state.verification_result is not None
    dedupe_verdict = next(
        sv for sv in state.verification_result.steps if sv.capability == "spreadsheet.deduplicate"
    )
    assert dedupe_verdict.passed is True

    near_duplicate_findings = [
        f for f in state.verification_result.findings
        if f.startswith("[spreadsheet.analyze] [near_duplicates]")
    ]
    assert len(near_duplicate_findings) > 0

    assert state.reply is not None
    assert "Before anything else, here's what I noticed" in state.reply
    assert "Want me to tackle any of these?" in state.reply
```

The fixture (`_NEAR_DUPLICATE_AND_COLLISION_ROWS`) reuses the exact
(name, phone) rows validated against real Splink in
`scripts/spike_tools_test.py` — true duplicates (spelling variant, same
phone) score real `match_probability` ~0.21–0.28; name collisions (same
or near-identical name, different phone) score ~0.0000 — plus one exact
duplicate row so `spreadsheet.deduplicate` has genuine work to do and
genuinely passes. This proves the rule holds with a real Splink call
feeding a real finding into a real aggregated `VerificationResult`, not
a hand-constructed one.

**Unit** (`tests/buddy/test_voice.py`):

```python
def test_template_speaker_does_not_fold_a_near_duplicate_finding_into_an_exact_dedupe_step() -> (
    None
):
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("report_deduplicated.xlsx",), steps=(_analyze_step(),)
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.analyze] [near_duplicates] Alice Johnson and Alice Johnston "
                "might be the same (self-reported, not independently re-checked)",
                "[spreadsheet.deduplicate] output has 3 row(s), 0 duplicate row(s) remaining",
            ),
            steps=(
                StepVerification(capability="spreadsheet.analyze", passed=True),
                # spreadsheet.deduplicate ran AND passed in this same turn --
                # the exact condition that must NOT be enough to fold the
                # near-duplicate finding away.
                StepVerification(capability="spreadsheet.deduplicate", passed=True),
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "Alice Johnson and Alice Johnston" in reply
    assert "Before anything else, here's what I noticed" in reply
    assert "Want me to tackle any of these?" in reply
```

A companion test,
`test_template_speaker_does_fold_a_finding_once_a_real_matching_capability_passes`,
proves the mechanism *can* fold a finding — it monkeypatches
`_ANALYSIS_FINDING_FIX_CAPABILITY` (empty in production; see
`buddy/voice.py`) to add a hypothetical `"outliers" -> "spreadsheet.fix_outliers"`
entry and confirms that finding *does* get dropped once a step for that
capability passes. Together, the two tests show the near-duplicate
finding stays open specifically because no capability resolves it today
— not because folding is broken in general.

### 3.2 `mark_analyzed` reads the per-step verdict, never the aggregate

Two complementary tests (`tests/buddy/test_inspector.py`), proving both
directions of the same claim:

```python
def test_inspector_marks_analyzed_from_the_steps_own_verdict_even_when_the_aggregate_failed() -> (
    None
):
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.switch_active_file("report.xlsx")
    result = VerificationResult(
        passed=False,  # the turn overall failed -- some OTHER step failed
        confidence=0.5,
        steps=(
            StepVerification(capability="spreadsheet.analyze", passed=True, confidence=1.0),
            StepVerification(capability="spreadsheet.deduplicate", passed=False, confidence=1.0),
        ),
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert "report.xlsx" in state.context.analyzed_files


def test_inspector_does_not_mark_analyzed_from_an_aggregate_pass_when_the_step_itself_failed() -> (
    None
):
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.switch_active_file("report.xlsx")
    result = VerificationResult(
        passed=True,
        confidence=1.0,
        steps=(StepVerification(capability="spreadsheet.analyze", passed=False, confidence=1.0),),
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert "report.xlsx" not in state.context.analyzed_files
```

Both construct a `VerificationResult` whose top-level `passed` is
deliberately inconsistent with the `spreadsheet.analyze` entry in
`.steps` — a shape real `_aggregate_verdicts` output would never produce
on its own, but which conclusively proves `Inspector._mark_analyzed_files`
(`buddy/inspector.py`) reads the right field:

```python
@staticmethod
def _mark_analyzed_files(state: BuddyState, result: VerificationResult) -> None:
    target_file = state.context.active_file
    if target_file is None:
        return
    analyze_passed = any(
        step.capability == "spreadsheet.analyze" and step.passed for step in result.steps
    )
    if analyze_passed:
        state.context.mark_analyzed(target_file)
```

Two further edge-case tests
(`test_inspector_does_not_mark_analyzed_when_there_is_no_active_file`,
`test_inspector_does_not_mark_analyzed_when_no_analyze_step_ran_at_all`)
confirm the method degrades safely — no active file, or no analyze step
in `.steps` at all — rather than raising or marking something incorrect.

**Why this matters:** if marking were gated on the aggregate `passed`
instead, a single unrelated capability failing in an otherwise-successful
analysis turn would cause `spreadsheet.analyze` to silently never be
marked done — and `ProactiveAnalysisPlanner` would re-queue (and
re-run, at real cost: a real ydata-profiling call and, when applicable, a
real Splink training run) the exact same analysis on the exact same
unchanged file, forever, every subsequent turn. The per-step gating is
what makes a genuinely-completed analysis "stick," independent of
whatever else happens to go wrong in the same turn.

---

## 4. What this review does not cover

Per its stated scope: `SpreadsheetAnalysisSkill` itself (real ydata-
profiling/Splink calls, five analyzers), `ProactiveAnalysisPlanner`,
`Context.mark_analyzed`, and the `TemplateSpeaker`/prompt changes are all
implemented and tested (`tests/skills/excel/test_analyze.py`,
`tests/buddy/test_planner.py`, `tests/buddy/test_context.py`,
`tests/buddy/test_voice.py`) but not reproduced here, since none of them
open a file unsupervised the way `SpreadsheetAnalysisVerifier` does, and
none of them decide what a Verifier sees or what survives aggregation the
way the `CompositeVerifier` change does.
