# Review record — Step 3, items #10–15 (final batch)

**Full test suite: `85 passed in 7.20s`** (run via `python -m pytest tests/`
from the repo root — includes everything from items #1–9 plus this
batch's new tests for `execute.py`, `verify.py`, `speak.py`, `brain.py`,
and `core/engine.py`, all on the first run with no fixes needed).

This batch wires the entire "remove duplicate rows" vertical slice
together end to end. `tests/buddy/test_brain.py` is the capstone: it runs
the real `Engine` — real `Observe`, real Claude-backed `Understand`/
`Think` (fed canned JSON through a fake `LLMProvider`, so no real network
call or API key is ever needed), real `RuleBasedPlanner`/
`MechanicalDelegator`, the real `Dispatcher` routing to a really-registered
`ExcelDeduplicateSkill`, the real `SpreadsheetDedupeVerifier`, and the real
`TemplateSpeaker` — against a real temporary `.xlsx` file with known
duplicate rows, then re-checks the output file's duplicate count itself,
independent of the app's own Verify stage.

No stray files were left in the real `workspace/outputs/` directory after
the run — confirmed by listing it post-suite; only the original
`.gitkeep` files are present.

`core/router.py` has been deleted (item #15), per the decision from
earlier in this project: dead, unreferenced, and duplicating
`core/dispatcher.py`'s stated responsibility.

---

## `buddy/execute.py` (new content)

```python
"""Execute — carries out delegated work and produces results."""

import logging

from buddy.models import CapabilityRequest, ExecutionResult
from buddy.state import BuddyState
from core.dispatcher import Dispatcher, DispatchError
from skills.base import PermissionTier, SkillStatus, SkillTask

logger = logging.getLogger(__name__)


class Execute:
    """The Execute stage of Buddy's cognitive cycle.

    Execute is the only stage that crosses out of Buddy's core into a
    Skill (see ``docs/WORKBUDDY_ARCHITECTURE.md`` Sections 4 and 11). For
    each capability request Delegate recorded, it builds a ``SkillTask``,
    asks the injected ``Dispatcher`` to select a capable Skill, and — only
    then — invokes that Skill's ``execute()`` itself. Execute never looks
    up a Skill on its own; every invocation goes through a
    Dispatcher-prepared ``ExecutionRequest``.

    Unlike Understand/Think/Plan/Delegate, Execute has no ``Null*``
    default and no swappable protocol of its own: the architecture doc
    frames the Dispatcher as Execute's one required collaborator, not a
    strategy to be substituted (Section 11 — "Execute is the Dispatcher's
    only caller and its only consumer"). Constructing Execute with a
    ``Dispatcher`` backed by an empty ``SkillRegistry`` already produces
    correct "nothing configured" behavior for free — it raises
    ``NoCapableSkillError`` for any real request, which Execute turns into
    a failed ``ExecutionResult`` rather than a crash — so no separate null
    implementation is needed.
    """

    def __init__(self, dispatcher: Dispatcher) -> None:
        """Initialize the stage.

        Args:
            dispatcher: The Dispatcher used to resolve each capability
                request to a capable Skill.
        """
        self._dispatcher = dispatcher

    def __call__(self, state: BuddyState) -> BuddyState:
        """Execute the capability requests Delegate recorded on ``state``.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the outcome recorded in
            ``state.execution_result`` and a plain-record mirror of each
            Skill's raw result in the legacy ``state.outputs``.
        """
        requests = state.delegation or ()
        if not requests:
            logger.debug("Execute: nothing to execute for session %r", state.session_id)
            self._apply(state, ExecutionResult(success=True, warnings=("nothing to execute",)), [])
            return state

        outputs: list[str] = []
        warnings: list[str] = []
        errors: list[str] = []
        raw_outputs: list[dict[str, object]] = []

        for index, request in enumerate(requests):
            task = self._build_task(state, request, index)
            state.active_task = {
                "task_id": task.task_id,
                "capability": request.capability,
                "target": request.target,
            }

            try:
                execution_request = self._dispatcher.dispatch(task, input_type=request.capability)
            except DispatchError as exc:
                logger.error(
                    "Execute: could not dispatch capability %r for session %r: %s",
                    request.capability,
                    state.session_id,
                    exc,
                )
                errors.append(str(exc))
                continue

            result = execution_request.skill.execute(task)
            raw_outputs.append(
                {
                    "task_id": task.task_id,
                    "skill": execution_request.skill.name,
                    "status": result.status.value,
                    "outputs": list(result.outputs),
                    "summary": result.summary,
                }
            )
            outputs.extend(result.outputs)
            warnings.extend(result.warnings)

            task_errors = list(result.errors)
            if result.status is SkillStatus.FAILURE and not task_errors:
                task_errors.append(f"{execution_request.skill.name} reported failure")
            errors.extend(task_errors)

        state.active_task = None
        execution_result = ExecutionResult(
            success=not errors,
            outputs=tuple(outputs),
            warnings=tuple(warnings),
            errors=tuple(errors),
        )
        self._apply(state, execution_result, raw_outputs)
        logger.debug(
            "Execute recorded success=%s outputs=%d for session %r",
            execution_result.success,
            len(execution_result.outputs),
            state.session_id,
        )
        return state

    @staticmethod
    def _build_task(state: BuddyState, request: CapabilityRequest, index: int) -> SkillTask:
        """Build the ``SkillTask`` to hand to the Dispatcher for one request.

        Args:
            state: The current ``BuddyState`` for this turn.
            request: The capability request to build a task for.
            index: This request's position among this turn's delegated
                requests, used to give each task a unique, deterministic
                id within the turn.

        Returns:
            The ``SkillTask`` to dispatch.
        """
        return SkillTask(
            task_id=f"{state.session_id}-task-{index}",
            session_id=state.session_id,
            workspace_id=state.workspace_id,
            description=f"Apply {request.capability} to {request.target}",
            input_references=[request.target],
            parameters=dict(request.parameters),
            permission=PermissionTier.WRITE,
        )

    @staticmethod
    def _apply(
        state: BuddyState,
        execution_result: ExecutionResult,
        raw_outputs: list[dict[str, object]],
    ) -> None:
        """Record the execution outcome onto ``state``.

        ``state.execution_result`` is the source of truth. A plain-record
        mirror of each Skill's raw result is additionally stored in the
        legacy ``state.outputs`` list, consistent with how the earlier
        stages keep their legacy fields populated alongside the typed
        ones.

        Args:
            state: The state to update.
            execution_result: The execution result to record.
            raw_outputs: Plain-record mirrors of each Skill's raw result.
        """
        state.execution_result = execution_result
        state.outputs = raw_outputs
```

---

## `buddy/verify.py` (new content)

```python
"""Verify — checks executed work against the user's original intent."""

import logging
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from buddy.models import ExecutionResult, Understanding, VerificationResult
from buddy.state import BuddyState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationRequest:
    """The inputs available for checking executed work against user intent.

    A plain, self-contained bundle of everything a ``Verifier`` needs to
    work from — assembled mechanically from ``BuddyState`` by the Verify
    stage, so a verifier never has to depend on ``BuddyState`` directly.

    Attributes:
        understanding: The structured understanding produced by
            Understand — the original intent to check the outcome
            against.
        execution_result: The structured result produced by Execute, if
            it has run yet.
    """

    understanding: Understanding
    execution_result: ExecutionResult | None


class Verifier(Protocol):
    """Produces a ``VerificationResult`` from a ``VerificationRequest``.

    This is the seam where real, independent verification is plugged in.
    The Verify stage itself never judges whether work succeeded; it only
    calls whichever ``Verifier`` it was given.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        """Verify ``request`` and return the resulting judgment."""
        ...


class NullVerifier:
    """A ``Verifier`` that performs no verification.

    Never claims success it hasn't checked. Serves as a safe default so
    ``Verify`` can be constructed and exercised — in tests, or wired into
    the Engine — before a real verifier is plugged in.
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
    carries none of them). It takes only the output path Execute recorded,
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
            the output file — not what Execute or a Skill reported.
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


class Verify:
    """The Verify stage of Buddy's cognitive cycle.

    Verify independently checks the outcome collected during Execute
    against the *original* intent captured back in Understand — not
    against what a Skill merely reported (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4; ``BUDDY_SKILLS.md``
    Section 9). A Skill's own self-check (``Skill.verify()``) confirms its
    result is well-formed; it is necessary but never sufficient — Verify's
    judgment is the one that actually decides whether the work succeeded.

    Verify contains no verification logic of its own. All of the actual
    checking is delegated to an injected ``Verifier``. Verify's own
    responsibility is purely mechanical: assemble the verifier's input
    from ``BuddyState``, call the verifier, and record what it returns.
    This keeps Verify a stable, swappable framework — any implementation
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
        logger.debug(
            "Verify recorded passed=%s confidence=%.2f for session %r",
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
        performs no verification of its own. If Understand has not
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
        )

    @staticmethod
    def _apply(state: BuddyState, result: VerificationResult) -> None:
        """Record the verifier's output onto ``state``.

        Args:
            state: The state to update.
            result: The verification result to record.
        """
        state.verification_result = result
```

---

## `buddy/speak.py` (new content)

```python
"""Speak — communicates results and status back to the user."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from buddy.models import ExecutionResult, Understanding, VerificationResult
from buddy.state import BuddyState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeakingRequest:
    """The inputs available for composing Buddy's reply to the user.

    A plain, self-contained bundle of everything a ``Speaker`` needs to
    work from — assembled mechanically from ``BuddyState`` by the Speak
    stage, so a speaker never has to depend on ``BuddyState`` directly.

    Attributes:
        understanding: The structured understanding produced by
            Understand.
        execution_result: The structured result produced by Execute, if
            it has run yet.
        verification_result: The structured result produced by Verify, if
            it has run yet.
    """

    understanding: Understanding
    execution_result: ExecutionResult | None
    verification_result: VerificationResult | None


class Speaker(Protocol):
    """Produces the reply text from a ``SpeakingRequest``.

    This is the seam where real reply composition is plugged in — a
    templated speaker, or one backed by an LLM. The Speak stage itself
    never composes a reply; it only calls whichever ``Speaker`` it was
    given.
    """

    def __call__(self, request: SpeakingRequest) -> str:
        """Compose a reply for ``request`` and return the reply text."""
        ...


class NullSpeaker:
    """A ``Speaker`` that never actually composes a reply.

    Honestly reports that no reply could be produced, rather than
    guessing. Serves as a safe default so ``Speak`` can be constructed and
    exercised — in tests, or wired into the Engine — before a real speaker
    is plugged in.
    """

    def __call__(self, request: SpeakingRequest) -> str:
        """Return a fixed, honest fallback reply for any request.

        Args:
            request: The request that would otherwise be spoken for.

        Returns:
            A fixed message noting that no speaker has been configured.
        """
        return "I wasn't able to generate a response — no speaker has been configured."


class TemplateSpeaker:
    """A ``Speaker`` that deterministically templates the reply.

    Builds the reply entirely from ``execution_result`` and
    ``verification_result`` — in particular, from Verify's own
    independently-computed ``findings``, never from a Skill's
    self-reported summary or metadata (``ExecutionResult`` doesn't even
    carry those). No LLM call is made here: templating a known-shape
    success/fail message from already-structured data is mechanical, not
    a judgment call — the same reasoning ``RuleBasedPlanner`` follows for
    this slice.
    """

    def __call__(self, request: SpeakingRequest) -> str:
        """Compose a reply for ``request``.

        Args:
            request: The request to compose a reply for.

        Returns:
            The reply text.
        """
        if request.verification_result is None:
            return self._describe_without_verification(request.execution_result)

        verification_result = request.verification_result
        finding = verification_result.findings[0] if verification_result.findings else ""

        if verification_result.passed:
            return self._success_message(request.execution_result, finding)
        return self._failure_message(verification_result, finding)

    @staticmethod
    def _success_message(execution_result: ExecutionResult | None, finding: str) -> str:
        """Compose the reply for a passed verification.

        Args:
            execution_result: The execution result, if any, to pull the
                output file name from.
            finding: Verify's own plain-language finding, if any.

        Returns:
            The reply text.
        """
        parts = ["Done"]
        if finding:
            parts.append(f"— {finding}.")
        else:
            parts[0] = "Done."
        output_name = TemplateSpeaker._output_file_name(execution_result)
        if output_name:
            parts.append(f"Saved as `{output_name}`.")
        return " ".join(parts)

    @staticmethod
    def _failure_message(verification_result: VerificationResult, finding: str) -> str:
        """Compose the reply for a failed (or inconclusive) verification.

        Args:
            verification_result: The verification result.
            finding: Verify's own plain-language finding, if any.

        Returns:
            The reply text.
        """
        parts = ["I wasn't able to confirm that worked."]
        if finding:
            parts.append(finding[0].upper() + finding[1:] + ".")
        if verification_result.recommendations:
            parts.append(" ".join(verification_result.recommendations))
        return " ".join(parts)

    @staticmethod
    def _describe_without_verification(execution_result: ExecutionResult | None) -> str:
        """Compose a reply when Verify hasn't run at all.

        Args:
            execution_result: The execution result, if any.

        Returns:
            The reply text.
        """
        if execution_result is None:
            return "I wasn't able to find anything to do with that request."
        if not execution_result.success:
            errors = "; ".join(execution_result.errors) or "an unknown error occurred"
            return f"I ran into a problem: {errors}"
        return "I did the work, but wasn't able to confirm the result."

    @staticmethod
    def _output_file_name(execution_result: ExecutionResult | None) -> str | None:
        """Extract the first output file's name, if any.

        Args:
            execution_result: The execution result, if any.

        Returns:
            The output file's base name, or ``None`` if there isn't one.
        """
        if execution_result is None or not execution_result.outputs:
            return None
        return Path(execution_result.outputs[0]).name


class Speak:
    """The Speak stage of Buddy's cognitive cycle.

    Speak converts the verified outcome into the message the user
    actually sees, written to ``BuddyState.reply`` (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4). Speak is the only stage
    that produces user-facing language.

    Speak contains no reply-composition logic of its own. All of the
    actual wording is delegated to an injected ``Speaker``. Speak's own
    responsibility is purely mechanical: assemble the speaker's input from
    ``BuddyState``, call the speaker, and record what it returns. This
    keeps Speak a stable, swappable framework — a templated speaker, an
    LLM-backed speaker, or anything else conforming to ``Speaker`` can be
    substituted without ever changing this stage.
    """

    def __init__(self, speaker: Speaker | None = None) -> None:
        """Initialize the stage.

        Args:
            speaker: The speaker to delegate reply composition to.
                Defaults to ``NullSpeaker``, which honestly reports that
                no reply could be produced rather than guessing.
        """
        if speaker is None:
            logger.debug("No speaker provided; falling back to NullSpeaker")
        self._speaker = speaker if speaker is not None else NullSpeaker()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Compose a reply for ``state`` and record it.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the reply text recorded in
            ``state.reply``.
        """
        request = self._build_request(state)
        reply = self._speaker(request)
        self._apply(state, reply)
        logger.debug("Speak recorded a reply for session %r", state.session_id)
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> SpeakingRequest:
        """Assemble the speaker's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` into a plain, self-contained bundle. It
        performs no reply composition of its own.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured speaker.
        """
        return SpeakingRequest(
            understanding=state.understanding or Understanding(),
            execution_result=state.execution_result,
            verification_result=state.verification_result,
        )

    @staticmethod
    def _apply(state: BuddyState, reply: str) -> None:
        """Record the speaker's output onto ``state``.

        Args:
            state: The state to update.
            reply: The reply text to record.
        """
        state.reply = reply
```

---

## `buddy/brain.py` (new content)

```python
"""Buddy's brain — orchestrates the Observe -> Understand -> Think -> Plan ->
Delegate -> Execute -> Verify -> Speak cycle over a shared BuddyState.
"""

import logging

from buddy.delegate import Delegate, MechanicalDelegator
from buddy.execute import Execute
from buddy.llm import ClaudeProvider, LLMProvider
from buddy.observe import Observe
from buddy.plan import Plan, RuleBasedPlanner
from buddy.speak import Speak, TemplateSpeaker
from buddy.think import ClaudeThinker, Think
from buddy.understand import ClaudeInterpreter, Understand
from buddy.verify import SpreadsheetDedupeVerifier, Verify
from core.dispatcher import Dispatcher
from core.engine import CognitiveCycle, Engine
from skills.excel import ExcelDeduplicateSkill
from skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


def build_engine(*, llm_provider: LLMProvider | None = None) -> Engine:
    """Build the real, end-to-end Engine for the "remove duplicate rows" slice.

    Wires the real Claude-backed Understand/Think stages, the rule-based
    Plan/Delegate stages, a Dispatcher backed by a registry containing
    ``ExcelDeduplicateSkill``, and the independent
    ``SpreadsheetDedupeVerifier``/``TemplateSpeaker`` — the concrete
    implementations this vertical slice needs (see
    ``docs/design/slice-1-dedupe-design.md``). All wiring happens here,
    once; every stage above continues to depend only on the protocol it
    was already built against, and none of them import this module.

    Args:
        llm_provider: The ``LLMProvider`` to back Understand/Think with.
            Defaults to a real ``ClaudeProvider`` (which reads
            ``ANTHROPIC_API_KEY`` from the environment). Tests should pass
            a fake provider here instead of relying on the default, so
            they never make a real network call or need a real key.

    Returns:
        An ``Engine`` ready to run the full, real cognitive cycle.
    """
    provider = llm_provider if llm_provider is not None else ClaudeProvider()

    registry = SkillRegistry()
    registry.register(ExcelDeduplicateSkill())
    dispatcher = Dispatcher(registry)

    cycle = CognitiveCycle(
        observe=Observe(),
        understand=Understand(interpreter=ClaudeInterpreter(provider)),
        think=Think(thinker=ClaudeThinker(provider)),
        plan=Plan(planner=RuleBasedPlanner()),
        delegate=Delegate(delegator=MechanicalDelegator()),
        execute=Execute(dispatcher=dispatcher),
        verify=Verify(verifier=SpreadsheetDedupeVerifier()),
        speak=Speak(speaker=TemplateSpeaker()),
    )
    logger.debug("build_engine: assembled the CognitiveCycle for the dedupe slice")
    return Engine(cycle)
```

---

## `core/engine.py` (modified — `CognitiveCycle.dispatch` field removed)

```python
"""Engine — drives the Observe -> Understand -> Think -> Plan -> Delegate ->
Execute -> Verify -> Speak cycle end to end.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from buddy.state import BuddyState

logger = logging.getLogger(__name__)


class Stage(Protocol):
    """A single step in Buddy's cognitive cycle.

    A stage receives the current ``BuddyState``, does its work against it,
    and returns the resulting state. A stage may mutate the state it is
    given and return the same instance, or return a new one — the Engine
    only cares about the value a stage returns, never how a stage produces
    it.
    """

    def __call__(self, state: BuddyState) -> BuddyState:
        """Run this stage against ``state`` and return the resulting state."""
        ...


@dataclass(frozen=True)
class CognitiveCycle:
    """The ordered set of stage dependencies the Engine runs.

    Each attribute is an injected ``Stage`` implementation. The Engine
    holds no knowledge of what any stage actually does — only the order in
    which they run. See ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4 for
    what each stage is responsible for, and Section 11 for the
    Dispatcher's role. The Dispatcher (``core/dispatcher.py``) is
    deliberately **not** one of these stages — Section 11 is explicit that
    it is not a cognitive stage and never reads or writes ``BuddyState``.
    It is a Core Engine component that ``execute`` calls internally,
    exactly once it's ready to act on a delegated task.

    Attributes:
        observe: Perceives the raw input for the current turn.
        understand: Interprets observations into user intent.
        think: Reasons about intent to determine what needs to happen.
        plan: Converts reasoning into an ordered sequence of steps.
        delegate: Converts plan steps into capability requests.
        execute: Asks the Dispatcher to select a capable Skill for each
            delegated request, invokes it, and collects the results.
        verify: Checks results against the original intent.
        speak: Produces the reply sent back to the user.
    """

    observe: Stage
    understand: Stage
    think: Stage
    plan: Stage
    delegate: Stage
    execute: Stage
    verify: Stage
    speak: Stage

    def stages(self) -> tuple[Stage, ...]:
        """Return the stages in the order the Engine runs them."""
        return (
            self.observe,
            self.understand,
            self.think,
            self.plan,
            self.delegate,
            self.execute,
            self.verify,
            self.speak,
        )


class Engine:
    """Orchestrates Buddy's cognitive cycle over a ``BuddyState``.

    The Engine contains no reasoning, routing, or domain logic of its own.
    Its only responsibility is to run each stage of the cognitive cycle, in
    order, against a shared ``BuddyState``, threading the state returned by
    one stage into the next. Every stage is supplied to the Engine as a
    dependency (see ``CognitiveCycle``), so the Engine can be exercised in
    tests with stub or fake stages, and real stage implementations can be
    built, changed, or replaced without ever changing the Engine.
    """

    def __init__(self, cycle: CognitiveCycle) -> None:
        """Initialize the engine.

        Args:
            cycle: The stage dependencies to run, in order.
        """
        self._cycle = cycle

    def run(self, state: BuddyState) -> BuddyState:
        """Run one full pass of the cognitive cycle over ``state``.

        Args:
            state: The ``BuddyState`` to run the cycle against.

        Returns:
            The state after every stage has run.

        Raises:
            Exception: Whatever the failing stage itself raises. The
                Engine does not interpret or suppress stage failures — it
                only logs which stage failed before propagating the error.
        """
        logger.info("Starting cognitive cycle for session %r", state.session_id)
        for stage in self._cycle.stages():
            stage_name = getattr(stage, "__name__", type(stage).__name__)
            logger.debug("Running stage %r for session %r", stage_name, state.session_id)
            try:
                state = stage(state)
            except Exception:
                logger.exception(
                    "Stage %r failed for session %r", stage_name, state.session_id
                )
                raise
        logger.info("Completed cognitive cycle for session %r", state.session_id)
        return state
```

---

## `core/router.py` — deleted (item #15)

Dead, unreferenced, undocumented, and duplicated `core/dispatcher.py`'s
stated responsibility (its one-line docstring was "Router — routes work
items to the correct skill or handler," near-identical to Dispatcher's own
purpose). No file anywhere imported or referenced it.

---

## `tests/buddy/test_execute.py` (new)

```python
"""Unit tests for buddy.execute."""

from __future__ import annotations

from buddy.execute import Execute
from buddy.models import CapabilityRequest, ExecutionResult
from buddy.state import BuddyState
from core.dispatcher import Dispatcher
from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask
from skills.registry import SkillRegistry

_DEFAULT_OUTPUTS = ["workspace/outputs/workspace-1/report_deduplicated.xlsx"]


class _FakeSkill(Skill):
    """A minimal Skill stand-in that records the tasks it was given."""

    def __init__(
        self,
        *,
        capability: str = "spreadsheet.deduplicate",
        status: SkillStatus = SkillStatus.SUCCESS,
        outputs: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self._capability = capability
        self._status = status
        self.outputs = outputs if outputs is not None else list(_DEFAULT_OUTPUTS)
        self._errors = errors if errors is not None else []
        self.received_tasks: list[SkillTask] = []

    @property
    def name(self) -> str:
        return "fake.skill"

    @property
    def description(self) -> str:
        return "A fake skill for testing Execute."

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        return (self._capability,)

    @property
    def permission_tier(self) -> PermissionTier:
        return PermissionTier.WRITE

    def can_handle(self, task: SkillTask) -> bool:
        return True

    def execute(self, task: SkillTask) -> SkillResult:
        self.received_tasks.append(task)
        return SkillResult(
            status=self._status,
            outputs=list(self.outputs),
            summary="did the fake thing",
            errors=list(self._errors),
        )

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        return result.status is SkillStatus.SUCCESS


def _make_state_with_delegation(*capability_requests: CapabilityRequest) -> BuddyState:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.delegation = tuple(capability_requests)
    return state


def _dispatcher_with(skill: Skill) -> Dispatcher:
    registry = SkillRegistry()
    registry.register(skill)
    return Dispatcher(registry)


# -- no-op path ---------------------------------------------------------------


def test_execute_no_ops_when_nothing_delegated() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    stage = Execute(dispatcher=_dispatcher_with(_FakeSkill()))

    stage(state)

    assert state.execution_result == ExecutionResult(success=True, warnings=("nothing to execute",))
    assert state.outputs == []
    assert state.active_task is None


# -- successful dispatch + execution -------------------------------------------


def test_execute_dispatches_and_invokes_the_matching_skill() -> None:
    skill = _FakeSkill()
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Execute(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert len(skill.received_tasks) == 1
    task = skill.received_tasks[0]
    assert task.input_references == ["uploads/report.xlsx"]
    assert task.permission is PermissionTier.WRITE
    assert task.session_id == "session-1"
    assert task.workspace_id == "workspace-1"

    assert state.execution_result is not None
    assert state.execution_result.success is True
    assert state.execution_result.outputs == tuple(_DEFAULT_OUTPUTS)
    assert state.active_task is None  # cleared once execution finishes


def test_execute_mirrors_raw_results_into_legacy_outputs() -> None:
    skill = _FakeSkill()
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Execute(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert len(state.outputs) == 1
    assert state.outputs[0]["skill"] == "fake.skill"
    assert state.outputs[0]["status"] == "success"


# -- failure paths ----------------------------------------------------------------


def test_execute_records_failure_when_no_skill_can_handle_the_capability() -> None:
    state = _make_state_with_delegation(
        CapabilityRequest(capability="some.other.capability", target="uploads/report.xlsx")
    )
    stage = Execute(dispatcher=_dispatcher_with(_FakeSkill(capability="spreadsheet.deduplicate")))

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.success is False
    assert state.execution_result.errors


def test_execute_records_failure_when_skill_reports_failure() -> None:
    skill = _FakeSkill(status=SkillStatus.FAILURE, outputs=[], errors=["could not read file"])
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Execute(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.success is False
    assert "could not read file" in state.execution_result.errors
```

---

## `tests/buddy/test_verify.py` (new)

```python
"""Unit tests for buddy.verify."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from buddy.models import ExecutionResult, Understanding
from buddy.state import BuddyState
from buddy.verify import NullVerifier, SpreadsheetDedupeVerifier, Verify, VerificationRequest


def _request(**overrides: object) -> VerificationRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(),
        "execution_result": None,
    }
    fields.update(overrides)
    return VerificationRequest(**fields)  # type: ignore[arg-type]


# -- NullVerifier ---------------------------------------------------------------


def test_null_verifier_never_claims_success() -> None:
    result = NullVerifier()(_request())

    assert result.passed is False
    assert result.confidence == 0.0


# -- SpreadsheetDedupeVerifier: independent re-read ------------------------------


def test_verifier_passes_when_output_file_has_no_duplicates(tmp_path: Path) -> None:
    output_path = tmp_path / "report_deduplicated.xlsx"
    pd.DataFrame({"name": ["Alice", "Bob", "Carol"]}).to_excel(output_path, index=False)
    request = _request(execution_result=ExecutionResult(success=True, outputs=(str(output_path),)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is True
    assert "0 duplicate row(s)" in result.findings[0]


def test_verifier_fails_when_output_file_still_has_duplicates(tmp_path: Path) -> None:
    output_path = tmp_path / "still_has_dupes.xlsx"
    pd.DataFrame({"name": ["Alice", "Alice", "Bob"]}).to_excel(output_path, index=False)
    request = _request(execution_result=ExecutionResult(success=True, outputs=(str(output_path),)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is False
    assert "1 duplicate row(s)" in result.findings[0]
    assert result.recommendations


def test_verifier_recomputes_independently_rather_than_trusting_anything(tmp_path: Path) -> None:
    """``ExecutionResult`` carries no skill metadata at all — only a
    success flag and output paths — so there is structurally nothing for
    the verifier to "trust" about what the skill claimed to have done. It
    must derive its answer purely from re-reading the file.
    """
    output_path = tmp_path / "actually_has_dupes.xlsx"
    pd.DataFrame({"name": ["Alice", "Alice"]}).to_excel(output_path, index=False)
    request = _request(execution_result=ExecutionResult(success=True, outputs=(str(output_path),)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is False


def test_verifier_fails_when_execution_did_not_succeed() -> None:
    request = _request(execution_result=ExecutionResult(success=False, errors=("boom",)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is False
    assert result.findings


def test_verifier_fails_when_no_execution_result_at_all() -> None:
    result = SpreadsheetDedupeVerifier()(_request(execution_result=None))

    assert result.passed is False


def test_verifier_fails_when_output_file_is_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.xlsx"
    request = _request(execution_result=ExecutionResult(success=True, outputs=(str(missing_path),)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is False
    assert result.findings


# -- Verify stage -----------------------------------------------------------------


def test_verify_stage_sets_typed_verification_result_field() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    stage = Verify(verifier=NullVerifier())

    stage(state)

    assert state.verification_result is not None
    assert state.verification_result.passed is False
```

---

## `tests/buddy/test_speak.py` (new)

```python
"""Unit tests for buddy.speak."""

from __future__ import annotations

from buddy.models import ExecutionResult, Understanding, VerificationResult
from buddy.speak import NullSpeaker, Speak, SpeakingRequest, TemplateSpeaker
from buddy.state import BuddyState


def _request(**overrides: object) -> SpeakingRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(),
        "execution_result": None,
        "verification_result": None,
    }
    fields.update(overrides)
    return SpeakingRequest(**fields)  # type: ignore[arg-type]


# -- NullSpeaker ------------------------------------------------------------------


def test_null_speaker_returns_honest_fallback() -> None:
    reply = NullSpeaker()(_request())

    assert "no speaker has been configured" in reply.lower()


# -- TemplateSpeaker ----------------------------------------------------------------


def test_template_speaker_success_message_mentions_output_file_and_finding() -> None:
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("workspace/outputs/workspace-1/report_deduplicated.xlsx",)
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=("output has 3 row(s), 0 duplicate row(s) remaining",),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "Done" in reply
    assert "report_deduplicated.xlsx" in reply
    assert "0 duplicate row(s)" in reply


def test_template_speaker_failure_message_includes_findings_and_recommendations() -> None:
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",)),
        verification_result=VerificationResult(
            passed=False,
            confidence=1.0,
            findings=("output has 3 row(s), 1 duplicate row(s) remaining",),
            recommendations=("re-run deduplication; duplicates remain",),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "wasn't able to confirm" in reply.lower()
    assert "1 duplicate row(s)" in reply
    assert "re-run deduplication" in reply


def test_template_speaker_describes_execution_failure_when_verification_never_ran() -> None:
    request = _request(
        execution_result=ExecutionResult(success=False, errors=("could not read file",)),
        verification_result=None,
    )

    reply = TemplateSpeaker()(request)

    assert "could not read file" in reply


def test_template_speaker_handles_nothing_executed() -> None:
    request = _request(execution_result=None, verification_result=None)

    reply = TemplateSpeaker()(request)

    assert reply


# -- Speak stage --------------------------------------------------------------------


def test_speak_stage_sets_reply() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    stage = Speak(speaker=NullSpeaker())

    stage(state)

    assert state.reply is not None
    assert "no speaker has been configured" in state.reply.lower()
```

---

## `tests/core/test_engine.py` (new)

```python
"""Unit tests for core.engine."""

from __future__ import annotations

import pytest

from buddy.state import BuddyState
from core.engine import CognitiveCycle, Engine


def _make_stage(name: str, calls: list[str]):
    def stage(state: BuddyState) -> BuddyState:
        calls.append(name)
        return state

    return stage


def test_cognitive_cycle_has_exactly_the_eight_documented_stage_slots() -> None:
    fields = {f.name for f in CognitiveCycle.__dataclass_fields__.values()}

    assert fields == {
        "observe",
        "understand",
        "think",
        "plan",
        "delegate",
        "execute",
        "verify",
        "speak",
    }


def test_cognitive_cycle_has_no_separate_dispatch_stage() -> None:
    """Per docs/WORKBUDDY_ARCHITECTURE.md Sections 4/5/11, the Dispatcher
    is not a pipeline stage — it never reads or writes BuddyState, and
    Execute is its only caller. There must be no ``dispatch`` slot here.
    """
    assert "dispatch" not in CognitiveCycle.__dataclass_fields__


def test_engine_runs_all_eight_stages_in_documented_order() -> None:
    calls: list[str] = []
    cycle = CognitiveCycle(
        observe=_make_stage("observe", calls),
        understand=_make_stage("understand", calls),
        think=_make_stage("think", calls),
        plan=_make_stage("plan", calls),
        delegate=_make_stage("delegate", calls),
        execute=_make_stage("execute", calls),
        verify=_make_stage("verify", calls),
        speak=_make_stage("speak", calls),
    )
    engine = Engine(cycle)
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    engine.run(state)

    assert calls == [
        "observe",
        "understand",
        "think",
        "plan",
        "delegate",
        "execute",
        "verify",
        "speak",
    ]


def test_engine_propagates_stage_exceptions() -> None:
    def failing_stage(state: BuddyState) -> BuddyState:
        raise RuntimeError("boom")

    def passthrough(state: BuddyState) -> BuddyState:
        return state

    cycle = CognitiveCycle(
        observe=failing_stage,
        understand=passthrough,
        think=passthrough,
        plan=passthrough,
        delegate=passthrough,
        execute=passthrough,
        verify=passthrough,
        speak=passthrough,
    )
    engine = Engine(cycle)
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    with pytest.raises(RuntimeError, match="boom"):
        engine.run(state)
```

---

## `tests/buddy/test_brain.py` (new) — the end-to-end capstone

```python
"""End-to-end test for the "remove duplicate rows" vertical slice.

Runs the full cognitive cycle built by ``build_engine`` against a real
temporary ``.xlsx`` file with known duplicate rows, using a fake
``LLMProvider`` so the test never makes a real network call or needs a
real API key. This is the test that proves the whole slice works
together — Observe through Speak — not just each stage in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from buddy.brain import build_engine
from buddy.llm import LLMMessage
from buddy.state import BuddyState


class _QueuedLLMProvider:
    """An ``LLMProvider`` stand-in that returns canned replies in call order.

    In this pipeline, Understand always calls the provider once and then
    Think calls it once — in that fixed order — so a simple queue is
    sufficient to fake both without needing to inspect which stage is
    calling.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, tuple[LLMMessage, ...]]] = []

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.calls.append((system, messages))
        if not self._replies:
            raise AssertionError("no more canned LLM replies configured for this test")
        return self._replies.pop(0)


def _write_fixture_with_duplicates(path: Path) -> None:
    """Write a real .xlsx file with 5 rows, 2 of which are exact
    duplicates, leaving 3 distinct rows after deduplication.
    """
    pd.DataFrame(
        {
            "name": ["Alice", "Bob", "Alice", "Carol", "Bob"],
            "amount": [10, 20, 10, 30, 20],
        }
    ).to_excel(path, index=False)


def test_full_slice_removes_duplicate_rows_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    _write_fixture_with_duplicates(tmp_path / input_path)

    understand_reply = json.dumps(
        {
            "intent": "remove_duplicate_rows",
            "confidence": 0.95,
            "referenced_files": [str(input_path)],
            "referenced_work_items": [],
            "missing_information": [],
            "ambiguity_flags": [],
            "notes": "",
        }
    )
    think_reply = json.dumps(
        {
            "objective": "Remove duplicate rows from the uploaded spreadsheet.",
            "summary": "The request is clear and a file is available.",
            "assumptions": [],
            "constraints": [],
            "risks": [],
            "recommended_strategy": "Use the Excel skill to deduplicate rows.",
            "clarification_required": False,
            "confidence": 0.9,
        }
    )
    provider = _QueuedLLMProvider([understand_reply, think_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove duplicate rows"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    # Both LLM calls actually happened, in the expected order — Understand
    # then Think — proving the real Understand/Think stages ran, not stubs.
    assert len(provider.calls) == 2

    # Execute actually ran the real ExcelDeduplicateSkill and produced a
    # real output file.
    assert state.execution_result is not None
    assert state.execution_result.success is True
    assert len(state.execution_result.outputs) == 1
    output_path = Path(state.execution_result.outputs[0])
    assert output_path.exists()

    # Verify's own independent judgment passed.
    assert state.verification_result is not None
    assert state.verification_result.passed is True

    # Independent re-check in the TEST itself — not trusting the app's own
    # Verify stage or the skill's self-report — that the output file
    # genuinely has zero duplicate rows.
    written = pd.read_excel(output_path)
    assert len(written) == 3
    assert int(written.duplicated().sum()) == 0

    # Speak produced a sensible reply describing what happened.
    assert state.reply
    assert output_path.name in state.reply

    # The original upload was never modified.
    original = pd.read_excel(tmp_path / input_path)
    assert len(original) == 5


def test_full_slice_reports_clarification_needed_for_an_unclear_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Claude classifies the request as unclear, nothing should be
    executed, and the reply should say so rather than silently doing
    nothing or crashing.
    """
    monkeypatch.chdir(tmp_path)

    understand_reply = json.dumps(
        {
            "intent": "unclear",
            "confidence": 0.2,
            "missing_information": ["no file was clearly referenced"],
        }
    )
    think_reply = json.dumps(
        {
            "objective": "",
            "summary": "The request is too ambiguous to act on.",
            "clarification_required": True,
            "confidence": 0.2,
            "risks": ["intent is unclear"],
        }
    )
    provider = _QueuedLLMProvider([understand_reply, think_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "do something with my stuff"

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    assert state.execution_result is not None
    assert state.execution_result.success is True  # nothing to execute is not a failure
    assert state.execution_result.outputs == ()
    assert state.verification_result is not None
    assert state.verification_result.passed is False
    assert state.reply
```
