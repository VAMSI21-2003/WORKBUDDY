# Hands / Verifier Review — Confirmation Gating, Skill.verify(), StepResult, CompositeVerifier

**Purpose:** A documentation snapshot of five specific artifacts from the
Hands permission-enforcement batch and the CompositeVerifier/multi-step
batch, requested verbatim rather than summarized. No code was changed to
produce this document.

---

## 1. The `confirm()`-gating block and `Skill.verify()` precedence logic (`buddy/hands.py`)

```python
skill = execution_request.skill
# The task's permission is not known until now: it belongs to
# whichever Skill the Dispatcher actually selected, not to the
# capability request itself. Overwriting it here (rather than
# guessing a tier in ``_build_task``) is what makes this the
# Skill's own declared tier, not a hardcoded one.
task.permission = skill.permission_tier

if task.permission in _CONFIRMATION_REQUIRED_TIERS and not (
    self._confirmation_provider.confirm(task, task.permission)
):
    logger.info(
        "Hands: confirmation declined for task %r (skill=%r, permission=%s); "
        "skipping this step",
        task.task_id,
        skill.name,
        task.permission.value,
    )
    errors.append(
        f"{skill.name} was not confirmed for a {task.permission.value}-tier "
        f"action; skipped"
    )
    continue

result = skill.execute(task)

# Skill.verify() is the Skill's own well-formedness self-check
# (docs/BUDDY_SKILLS.md Section 9) — distinct from Inspector's
# independent judgment against the user's original intent.
# Called unconditionally right after execute() so a cheap,
# in-process check (e.g. "does the output file actually
# exist?") can catch a Skill that mis-reports its own success
# before paying for Inspector's heavier, independent read of
# whatever the Skill claims it produced. When the Skill has
# already reported FAILURE, its own structured errors are the
# more specific signal, so a (redundant) False from verify()
# in that case is not treated as a *second*, less specific
# failure — it's folded into the ordinary failure path below.
well_formed = skill.verify(task, result)
if result.status is not SkillStatus.FAILURE and not well_formed:
    logger.error(
        "Hands: %r reported %s for task %r but failed its own well-formedness "
        "check; discarding its result rather than trusting it",
        skill.name,
        result.status.value,
        task.task_id,
    )
    raw_outputs.append(
        {
            "task_id": task.task_id,
            "skill": skill.name,
            "status": SkillStatus.FAILURE.value,
            "outputs": [],
            "summary": result.summary,
        }
    )
    step_results.append(
        StepResult(
            capability=request.capability,
            skill=skill.name,
            status=SkillStatus.FAILURE.value,
            summary=result.summary,
        )
    )
    errors.append(
        f"{skill.name} reported {result.status.value} but failed its own "
        f"well-formedness check; result discarded"
    )
    continue
```

`_CONFIRMATION_REQUIRED_TIERS = (PermissionTier.WRITE, PermissionTier.EXTERNAL_EFFECT)`,
a module-level constant. Note: the `step_results.append(...)` block was
added in the *following* turn's `CompositeVerifier` batch — it wasn't
present when this code was first written in the permission-enforcement
batch.

---

## 2. The stop-and-flag exchange, verbatim

**What was proposed, in full:**

> Stopping here — iterating over all outputs surfaces a real conflict, not just a mechanical change, and it directly determines the shape of the rest of this work.
>
> **The conflict:** `ExecutionResult` (`buddy/models.py`) is a flat aggregate:
>
> ```python
> @dataclass(frozen=True)
> class ExecutionResult:
>     success: bool
>     outputs: tuple[str, ...] = ()
>     warnings: tuple[str, ...] = ()
>     errors: tuple[str, ...] = ()
>     duration: float = 0.0
> ```
>
> `Hands.__call__` builds `outputs` by concatenating every step's `result.outputs` in a loop (`buddy/hands.py`: `outputs.extend(result.outputs)`), then throws away everything about *which step produced which output* — the `request.capability` string that drove the Dispatcher lookup exists only as a local loop variable and never reaches `ExecutionResult`. The only place per-step detail survives at all is the legacy dict mirror in `state.outputs` (`{"task_id", "skill", "status", "outputs", "summary"}`), and even that records `skill` name, not `capability` — and a `Skill.supported_inputs` can in principle declare more than one capability, so `skill.name` isn't reliably a stand-in for capability either.
>
> A `CompositeVerifier` keyed by `capability -> Verifier` (as specified) needs exactly the thing that gets dropped: given `execution_result.outputs[i]`, there's currently no way to know which capability produced it, so there's nothing to key the dispatch on. This isn't a matter of writing a `for` loop instead of indexing `[0]` — the data the loop would need doesn't exist yet.
>
> **Proposed fix**, following the pattern already established for `BuddyState` (typed field added alongside a legacy one it doesn't replace — see `understanding` next to `intent`):
>
> ```python
> @dataclass(frozen=True)
> class StepResult:
>     """The outcome of executing one delegated capability request."""
>     capability: str
>     skill: str
>     status: str          # SkillStatus.value — buddy/models.py doesn't import skills.base
>     outputs: tuple[str, ...] = ()
>     summary: str = ""
>
> @dataclass(frozen=True)
> class ExecutionResult:
>     success: bool
>     outputs: tuple[str, ...] = ()      # unchanged — flat view, kept for existing consumers
>     warnings: tuple[str, ...] = ()
>     errors: tuple[str, ...] = ()
>     duration: float = 0.0
>     steps: tuple[StepResult, ...] = () # NEW — source of truth for per-capability detail
> ```
>
> `Hands` populates `steps` from data it already computes per iteration — no new information needed, just not discarding it. `CompositeVerifier` iterates `execution_result.steps`, dispatches per `step.capability`, and Inspector *aggregates* the per-step `VerificationResult`s into the one it already returns today (`passed = all(...)`, `findings` = each step's findings, prefixed by capability so they stay traceable, `confidence` = min across steps) — so `VerificationResult`, `BuddyState.verification_result`, and `SpeakingRequest` need **no shape change at all**. `TemplateSpeaker` and `CompositeVerifier` both then iterate `execution_result.steps` instead of `outputs[0]`.
>
> Net effect: one new frozen dataclass + one new field on `ExecutionResult`, `outputs`/`warnings`/`errors` untouched for backward compatibility, no `BuddyState` field changes, no `VerificationResult`/`SpeakingRequest` shape changes.

Followed immediately by an `AskUserQuestion` call:

**Question:** "Proceed with adding ExecutionResult.steps (a new StepResult per capability request) as the fix for the missing capability↔output association, so CompositeVerifier and the multi-output Inspector/Voice changes have something real to dispatch on?"

**Options offered:**
- "Yes, add ExecutionResult.steps (Recommended)" — New StepResult(capability, skill, status, outputs, summary) dataclass; Hands populates it from data it already computes. outputs/warnings/errors stay as-is for backward compat. VerificationResult/BuddyState/SpeakingRequest need no shape change — Inspector aggregates per-step verdicts into the single VerificationResult it already returns.
- "Different design — I'll specify"
- "Narrow the scope for now"

**The user's response, verbatim as returned by the tool:**

> Your questions have been answered: "Proceed with adding ExecutionResult.steps (a new StepResult per capability request) as the fix for the missing capability↔output association, so CompositeVerifier and the multi-output Inspector/Voice changes have something real to dispatch on?"="Yes, add ExecutionResult.steps (Recommended)". You can now continue with these answers in mind.

---

## 3. `StepResult` dataclass, in full (`buddy/models.py`)

```python
@dataclass(frozen=True)
class StepResult:
    """The outcome of executing a single delegated capability request.

    Produced by Hands, one per ``CapabilityRequest`` it actually got far
    enough to dispatch to a Skill (a request that never reached a Skill —
    routing failure, declined confirmation — has nothing to report here
    and is represented purely by an ``ExecutionResult.errors`` entry
    instead; see ``buddy/hands.py``). This is what lets a Verifier
    resolve *which* strategy applies to *which* output — something
    ``ExecutionResult.outputs``, a single flat tuple with every step's
    outputs concatenated together, cannot express on its own.

    Attributes:
        capability: The capability this step requested — the same string
            Delegate produced and the Dispatcher used to select a Skill.
        skill: The name of the Skill that actually ran this step.
        status: The Skill's own reported outcome for this step, as the
            string value of ``skills.base.SkillStatus`` (e.g.
            ``"success"``, ``"failure"``). Kept as a plain string rather
            than importing ``SkillStatus`` here so ``buddy/models.py``
            stays free of a dependency on ``skills/`` — the same
            domain-agnostic boundary ``docs/BUDDY_SKILLS.md`` Section 2
            asks Buddy's core to keep.
        outputs: References to files or data this step produced.
        summary: Plain-language description of what this step did.
    """

    capability: str
    skill: str
    status: str
    outputs: tuple[str, ...] = ()
    summary: str = ""
```

---

## 4. `CompositeVerifier`, in full (`buddy/inspector.py`)

Including its two supporting module-level functions, since the dispatch
logic in `__call__` calls both directly.

```python
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
            )
            per_step.append((step.capability, verifier(scoped_request)))

        return _aggregate_verdicts(per_step)
```

---

## 5. `test_full_slice_runs_two_real_skills_and_aggregates_verification_and_reply`, in full (`tests/buddy/test_engine_factory.py`)

Including its fixture helper, since the test depends on it directly.

```python
def _write_fixture_with_duplicates_and_an_empty_row(path: Path) -> None:
    """Write a real .xlsx file with both an exact duplicate row and a
    fully-empty row, so both registered skills each have real work to do
    on the very same uploaded file (MechanicalDelegator resolves one
    shared target for the whole plan — see
    docs/design/multi-step-planner-design.md — so both steps read this
    same file independently, not chained).

    The empty row sits before the final row, not after it: a trailing
    all-empty row has no following row to anchor it, so openpyxl's
    write/read round trip silently drops it rather than preserving it as
    a blank row (the same reason tests/skills/excel/test_remove_empty_rows.py's
    own fixture places its empty row mid-file rather than last).
    """
    pd.DataFrame(
        {
            "name": ["Alice", "Bob", None, "Alice"],
            "amount": [10, 20, None, 10],
        }
    ).to_excel(path, index=False)


def test_full_slice_runs_two_real_skills_and_aggregates_verification_and_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof that CompositeVerifier's per-capability dispatch
    and TemplateSpeaker's multi-step reply actually work together, not
    just in isolation: two *real* registered skills
    (ExcelDeduplicateSkill, ExcelRemoveEmptyRowsSkill) both run against
    the same real file, each is independently verified by its own real
    Verifier, and the final reply covers both outcomes.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    _write_fixture_with_duplicates_and_an_empty_row(tmp_path / input_path)

    understand_reply = json.dumps(
        {
            "intent": "multi_step_request",
            "confidence": 0.9,
            "missing_information": [],
            "ambiguity_flags": [],
            "notes": "",
        }
    )
    think_reply = json.dumps(
        {
            "objective": "Clean up the spreadsheet.",
            "summary": "The request names two distinct cleanup actions but is clear.",
            "clarification_required": False,
            "confidence": 0.85,
        }
    )
    plan_reply = json.dumps(
        {
            "steps": [
                {
                    "description": "Remove duplicate rows",
                    "capability": "spreadsheet.deduplicate",
                    "depends_on_step_numbers": [],
                },
                {
                    "description": "Remove empty rows",
                    "capability": "spreadsheet.remove_empty_rows",
                    "depends_on_step_numbers": [],
                },
            ]
        }
    )
    provider = _QueuedLLMProvider([understand_reply, think_reply, plan_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove duplicate rows and remove empty rows from this file"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    # Both steps reached a real, registered skill and both succeeded.
    assert state.execution_result is not None
    assert state.execution_result.success is True
    assert len(state.execution_result.steps) == 2
    capabilities = {step.capability for step in state.execution_result.steps}
    assert capabilities == {"spreadsheet.deduplicate", "spreadsheet.remove_empty_rows"}
    assert len(state.execution_result.outputs) == 2

    # CompositeVerifier dispatched each output to its own real Verifier and
    # aggregated both into a single passed verdict.
    assert state.verification_result is not None
    assert state.verification_result.passed is True
    assert any(
        f.startswith("[spreadsheet.deduplicate]") for f in state.verification_result.findings
    )
    assert any(
        f.startswith("[spreadsheet.remove_empty_rows]")
        for f in state.verification_result.findings
    )

    # Independent re-check, in the test itself, of both output files —
    # not trusting the app's own Inspector stage or either skill's
    # self-report. Each output is checked against the specific claim its
    # own capability makes, not an either-or guess.
    for step in state.execution_result.steps:
        assert len(step.outputs) == 1
        written = pd.read_excel(step.outputs[0])
        if step.capability == "spreadsheet.deduplicate":
            assert int(written.duplicated().sum()) == 0
        elif step.capability == "spreadsheet.remove_empty_rows":
            assert int(written.isna().all(axis=1).sum()) == 0
        else:
            raise AssertionError(f"unexpected capability in step results: {step.capability!r}")

    # Voice's reply mentions both output files, not just the first.
    assert state.reply is not None
    output_names = {Path(p).name for p in state.execution_result.outputs}
    for name in output_names:
        assert name in state.reply

    # The original upload was never modified.
    original = pd.read_excel(tmp_path / input_path)
    assert len(original) == 4
```
