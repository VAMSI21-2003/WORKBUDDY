# Review record — Step 3, items #6–9

**Full test suite: `60 passed in 3.37s`** (run via `python -m pytest tests/`
from the repo root — includes everything from items #1–5 plus this batch's
new tests for `plan.py`, `delegate.py`, and `skills/excel/dedupe.py`).

One test bug was found and fixed during this batch — not a bug in the
skill itself. `test_execute_output_path_is_under_workspace_outputs_for_workspace_id`
compared a relative `Path` (`workspace/outputs/...`, as the skill
correctly returns) against an absolute `tmp_path` using `is_relative_to`,
which doesn't resolve paths before comparing. Fixed by resolving the
output path against the (monkeypatched) cwd before the comparison. The two
tests immediately before it (`test_execute_removes_duplicate_rows_from_real_file`,
`test_execute_never_writes_to_input_path`) already independently confirm
the file lands on disk in the right place, so this was purely an
assertion-construction bug in the test, not a sign anything was wrong with
`_build_output_path`.

No stray files were left in the real `E:\WorkBuddy\workspace\outputs\`
directory after the run — confirmed by listing it post-suite; only the
original `.gitkeep` files are present. Every test that actually writes an
output file does so via `monkeypatch.chdir(tmp_path)` first.

Per the approved design (`docs/design/slice-1-dedupe-design.md` Section C),
`skills/excel/dedupe.py` is reproduced in full below with an explicit
callout of the read/write behavior. `buddy/execute.py`, `buddy/verify.py`,
and `buddy/speak.py` were **not** touched in this batch, as instructed.

---

## `buddy/plan.py` (new content)

```python
"""Plan — turns a reasoned understanding into a concrete sequence of steps."""

import logging
from dataclasses import dataclass
from typing import Protocol

from buddy.models import PlanningStep, Understanding
from buddy.state import BuddyState
from buddy.think import Reasoning

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanningRequest:
    """The inputs available for turning a reasoned understanding into steps.

    A plain, self-contained bundle of everything a ``Planner`` needs to
    work from — assembled mechanically from ``BuddyState`` by the Plan
    stage, so a planner never has to depend on ``BuddyState`` directly.

    Attributes:
        understanding: The structured understanding produced by
            Understand.
        reasoning: The structured reasoning produced by Think.
        uploaded_files: Files currently available in the workspace.
        active_file: Reference to the file the user was last focused on,
            if any, from the working context.
    """

    understanding: Understanding
    reasoning: Reasoning
    uploaded_files: tuple[str, ...]
    active_file: str | None


class Planner(Protocol):
    """Produces an ordered sequence of ``PlanningStep``s from a ``PlanningRequest``.

    This is the seam where real planning is plugged in — a rule-based
    planner, or one backed by an LLM. The Plan stage itself never decides
    what to do; it only calls whichever ``Planner`` it was given.
    """

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Plan ``request`` and return the resulting steps."""
        ...


class NullPlanner:
    """A ``Planner`` that produces no steps.

    Never invents a plan. Serves as a safe default so ``Plan`` can be
    constructed and exercised — in tests, or wired into the Engine —
    before a real planner is plugged in.
    """

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Return no steps for any request.

        Args:
            request: The request that would otherwise be planned.

        Returns:
            An empty tuple — no planner has been configured.
        """
        return ()


_INTENT_CAPABILITIES: dict[str, str] = {
    "remove_duplicate_rows": "spreadsheet.deduplicate",
}


class RuleBasedPlanner:
    """A ``Planner`` that mechanically maps known intents to one step.

    Turns a small, closed set of known intents into a single
    ``PlanningStep`` requesting the matching capability. This is
    deliberately not an LLM-backed planner: translating one already-known
    intent into one step is mechanical, not a judgment call, so no
    additional reasoning is needed here. As more intents are added, a
    richer ``Planner`` can be substituted behind this same protocol
    without changing the Plan stage.

    Produces no steps when: the intent isn't in the known mapping, Think
    flagged ``clarification_required``, or no file is available to act on
    — in all three cases, proceeding would mean guessing.
    """

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        """Plan ``request`` and return the resulting steps.

        Args:
            request: The request to plan for.

        Returns:
            A single ``PlanningStep`` if the intent is known, the request
            isn't flagged as needing clarification, and a target file is
            available; otherwise an empty tuple.
        """
        intent = request.understanding.intent
        capability = _INTENT_CAPABILITIES.get(intent) if intent else None
        target_file = request.active_file or (
            request.uploaded_files[-1] if request.uploaded_files else None
        )

        if capability is None or request.reasoning.clarification_required or target_file is None:
            return ()

        return (
            PlanningStep(
                id="step-1",
                description=f"Apply {capability} to {target_file}",
                capability=capability,
                priority=0,
            ),
        )


class Plan:
    """The Plan stage of Buddy's cognitive cycle.

    Plan converts Think's reasoning into a concrete, ordered sequence of
    steps — what Delegate will turn into capability requests for the
    Dispatcher to route (see ``docs/WORKBUDDY_ARCHITECTURE.md`` Section
    4). It is not responsible for resolving a step to a specific Skill,
    for executing anything, or for verifying results — those belong to
    Delegate, Execute, and Verify respectively.

    Plan contains no planning logic of its own. All of the actual
    decision-making — which capability a step requires, how many steps,
    their ordering — is delegated to an injected ``Planner``. Plan's own
    responsibility is purely mechanical: assemble the planner's input from
    ``BuddyState``, call the planner, and record what it returns. This
    keeps Plan a stable, swappable framework — a rule-based planner, an
    LLM-backed planner, or anything else conforming to ``Planner`` can be
    substituted without ever changing this stage.
    """

    def __init__(self, planner: Planner | None = None) -> None:
        """Initialize the stage.

        Args:
            planner: The planner to delegate planning to. Defaults to
                ``NullPlanner``, which produces no steps rather than
                guessing.
        """
        if planner is None:
            logger.debug("No planner provided; falling back to NullPlanner")
        self._planner = planner if planner is not None else NullPlanner()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Plan for ``state`` and record the resulting steps.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the full sequence of ``PlanningStep``s
            recorded in ``state.planning`` and mirrored as plain-language
            descriptions in the legacy ``state.plan``.
        """
        request = self._build_request(state)
        steps = self._planner(request)
        self._apply(state, steps)
        logger.debug("Plan recorded %d step(s) for session %r", len(steps), state.session_id)
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> PlanningRequest:
        """Assemble the planner's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` and ``state.context`` into a plain,
        self-contained bundle. It performs no planning of its own. If
        Understand or Think have not run yet, empty defaults are used
        rather than guessing at what they might have contained.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured planner.
        """
        return PlanningRequest(
            understanding=state.understanding or Understanding(),
            reasoning=state.reasoning or Reasoning(),
            uploaded_files=tuple(state.uploaded_files),
            active_file=state.context.active_file,
        )

    @staticmethod
    def _apply(state: BuddyState, steps: tuple[PlanningStep, ...]) -> None:
        """Record the planner's output onto ``state``.

        ``state.planning`` is the source of truth. Plain-language
        descriptions are additionally mirrored into the legacy
        ``state.plan`` list, consistent with how Understand/Think keep
        their legacy fields populated alongside the typed ones.

        Args:
            state: The state to update.
            steps: The planned steps to record.
        """
        state.planning = steps
        state.plan = [step.description for step in steps]
```

---

## `buddy/delegate.py` (new content)

```python
"""Delegate — assigns planned steps to the appropriate skills."""

import logging
from dataclasses import dataclass
from typing import Protocol

from buddy.models import CapabilityRequest, PlanningStep
from buddy.state import BuddyState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DelegationRequest:
    """The inputs available for turning planned steps into capability requests.

    A plain, self-contained bundle of everything a ``Delegator`` needs to
    work from — assembled mechanically from ``BuddyState`` by the Delegate
    stage, so a delegator never has to depend on ``BuddyState`` directly.

    Attributes:
        planning: The ordered sequence of ``PlanningStep``s produced by
            Plan.
        uploaded_files: Files currently available in the workspace.
        active_file: Reference to the file the user was last focused on,
            if any, from the working context.
    """

    planning: tuple[PlanningStep, ...]
    uploaded_files: tuple[str, ...]
    active_file: str | None


class Delegator(Protocol):
    """Produces ``CapabilityRequest``s from a ``DelegationRequest``.

    This is the seam where the mapping from planned steps to capability
    requests is plugged in. The Delegate stage itself never decides how a
    step's target is resolved; it only calls whichever ``Delegator`` it
    was given.
    """

    def __call__(self, request: DelegationRequest) -> tuple[CapabilityRequest, ...]:
        """Delegate ``request`` and return the resulting capability requests."""
        ...


class NullDelegator:
    """A ``Delegator`` that produces no capability requests.

    Never invents a target to act on. Serves as a safe default so
    ``Delegate`` can be constructed and exercised — in tests, or wired
    into the Engine — before a real delegator is plugged in.
    """

    def __call__(self, request: DelegationRequest) -> tuple[CapabilityRequest, ...]:
        """Return no capability requests for any request.

        Args:
            request: The request that would otherwise be delegated.

        Returns:
            An empty tuple — no delegator has been configured.
        """
        return ()


class MechanicalDelegator:
    """A ``Delegator`` that mechanically turns each step into one request.

    Produces one ``CapabilityRequest`` per ``PlanningStep``, using the
    step's own ``capability`` and resolving ``target`` from the working
    context (the active file, or the most recent upload) rather than from
    the step itself — ``PlanningStep`` carries no target field, the same
    way Understand and Think already pull file references straight from
    state rather than threading them through every model.

    Never references a skill by name: ``capability`` is always the
    domain-language string a ``PlanningStep`` already carries (for
    example ``"spreadsheet.deduplicate"``), so which registered Skill
    actually satisfies it is left entirely to the Dispatcher.
    """

    def __call__(self, request: DelegationRequest) -> tuple[CapabilityRequest, ...]:
        """Delegate ``request`` and return the resulting capability requests.

        Args:
            request: The request to delegate.

        Returns:
            One ``CapabilityRequest`` per planned step, using a resolved
            target file. If no target file can be resolved, returns an
            empty tuple rather than guessing.
        """
        target_file = request.active_file or (
            request.uploaded_files[-1] if request.uploaded_files else None
        )
        if target_file is None:
            return ()

        return tuple(
            CapabilityRequest(
                capability=step.capability,
                target=target_file,
                parameters={},
            )
            for step in request.planning
        )


class Delegate:
    """The Delegate stage of Buddy's cognitive cycle.

    Delegate converts Plan's ordered steps into capability requests — what
    capability is needed and what it should be applied to — for the
    Dispatcher to resolve to a concrete Skill (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Sections 4 and 11). As documented
    there, Delegate does not itself resolve a request to a specific Skill
    instance; that resolution belongs entirely to the Dispatcher, called
    by Execute.

    Delegate contains no delegation logic of its own. All of the actual
    decision-making — how a step's target is resolved — is delegated to
    an injected ``Delegator``. Delegate's own responsibility is purely
    mechanical: assemble the delegator's input from ``BuddyState``, call
    the delegator, and record what it returns. This keeps Delegate a
    stable, swappable framework — any implementation conforming to
    ``Delegator`` can be substituted without ever changing this stage.
    """

    def __init__(self, delegator: Delegator | None = None) -> None:
        """Initialize the stage.

        Args:
            delegator: The delegator to delegate to. Defaults to
                ``NullDelegator``, which produces no capability requests
                rather than guessing.
        """
        if delegator is None:
            logger.debug("No delegator provided; falling back to NullDelegator")
        self._delegator = delegator if delegator is not None else NullDelegator()

    def __call__(self, state: BuddyState) -> BuddyState:
        """Delegate for ``state`` and record the resulting capability requests.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with the full sequence of
            ``CapabilityRequest``s recorded in ``state.delegation`` and
            mirrored as plain task records in the legacy ``state.tasks``.
        """
        request = self._build_request(state)
        capability_requests = self._delegator(request)
        self._apply(state, capability_requests)
        logger.debug(
            "Delegate recorded %d capability request(s) for session %r",
            len(capability_requests),
            state.session_id,
        )
        return state

    @staticmethod
    def _build_request(state: BuddyState) -> DelegationRequest:
        """Assemble the delegator's input from the current state.

        This step is purely mechanical: it gathers what is already
        present on ``state`` and ``state.context`` into a plain,
        self-contained bundle. It performs no delegation of its own. If
        Plan has not recorded any steps yet, an empty sequence is used
        rather than guessing at what it might have contained.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The request to hand to the configured delegator.
        """
        return DelegationRequest(
            planning=state.planning or (),
            uploaded_files=tuple(state.uploaded_files),
            active_file=state.context.active_file,
        )

    @staticmethod
    def _apply(state: BuddyState, capability_requests: tuple[CapabilityRequest, ...]) -> None:
        """Record the delegator's output onto ``state``.

        ``state.delegation`` is the source of truth. Plain task records
        are additionally mirrored into the legacy ``state.tasks`` list,
        consistent with how the earlier stages keep their legacy fields
        populated alongside the typed ones.

        Args:
            state: The state to update.
            capability_requests: The capability requests to record.
        """
        state.delegation = capability_requests
        state.tasks = [
            {
                "capability": request.capability,
                "target": request.target,
                "parameters": dict(request.parameters),
            }
            for request in capability_requests
        ]
```

---

## `skills/excel/dedupe.py` (new)

**Explicit read/write confirmation** (per the approved design, Section C):
the only read call is `pd.read_excel(input_path)` — inherently read-only,
no write mode exists on that call — and the only write call is
`deduped.to_excel(output_path, index=False)`, where `output_path` is always
freshly constructed by `_build_output_path` from `task.workspace_id` and
`task.task_id`, never derived from or equal to `input_path`. There is no
code path in this file that opens the upload with write intent, writes
back to `input_path`, or deletes/renames the original.

```python
"""Dedupe — removes duplicate rows from an uploaded spreadsheet."""

import logging
from pathlib import Path

import pandas as pd

from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = (".xlsx", ".xls")


class ExcelDeduplicateSkill(Skill):
    """Removes duplicate rows from an uploaded spreadsheet.

    Reads the uploaded ``.xlsx``/``.xls`` file read-only, drops exact
    full-row duplicates, and writes the result to a freshly constructed
    path under ``workspace/outputs/{workspace_id}/`` — never in place, and
    never back to the original upload. See
    ``docs/design/slice-1-dedupe-design.md`` Section C for the full design
    and the explicit read/write confirmation this implementation follows.
    """

    @property
    def name(self) -> str:
        """The skill's unique, human-readable identifier."""
        return "excel.deduplicate"

    @property
    def description(self) -> str:
        """A plain-language description of what this skill does."""
        return "Removes duplicate rows from an uploaded spreadsheet."

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        """The capabilities this skill declares support for.

        A capability string, not a file extension — see
        ``docs/design/slice-1-dedupe-design.md`` Section C for why
        ``supported_inputs`` is used this way here.
        """
        return ("spreadsheet.deduplicate",)

    @property
    def permission_tier(self) -> PermissionTier:
        """The permission tier required to invoke this skill."""
        return PermissionTier.WRITE

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
        """Remove duplicate rows from the uploaded spreadsheet.

        Reads ``task.input_references[0]`` read-only via
        ``pandas.read_excel`` and writes the deduplicated result to a new
        path under ``workspace/outputs/{workspace_id}/``. The original
        upload is never opened with write intent, and the output path is
        always freshly constructed — never derived from, or equal to, the
        input path.

        Args:
            task: The task to execute.

        Returns:
            A ``SkillResult`` describing the outcome. Any error (unreadable
            input, unwritable output) is captured in the result rather
            than raised.
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
            original = pd.read_excel(input_path)  # READ-ONLY open of the upload
        except Exception as exc:  # noqa: BLE001 — surfaced as a SkillResult, not raised
            logger.exception("ExcelDeduplicateSkill: failed to read %r", input_path)
            return SkillResult(
                status=SkillStatus.FAILURE, errors=[f"could not read {input_path!r}: {exc}"]
            )

        deduped = original.drop_duplicates()
        output_path = self._build_output_path(task)

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            deduped.to_excel(output_path, index=False)  # WRITE only ever targets output_path
        except Exception as exc:  # noqa: BLE001
            logger.exception("ExcelDeduplicateSkill: failed to write %r", output_path)
            return SkillResult(
                status=SkillStatus.FAILURE, errors=[f"could not write {output_path!r}: {exc}"]
            )

        removed_count = len(original) - len(deduped)
        return SkillResult(
            status=SkillStatus.SUCCESS,
            outputs=[str(output_path)],
            summary=f"Removed {removed_count} duplicate row(s) from {Path(input_path).name}.",
            metadata={
                "removed_count": removed_count,
                "original_row_count": len(original),
                "output_row_count": len(deduped),
            },
        )

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        """Check that ``result`` is well-formed and consistent with ``task``.

        This is the skill's own self-check — it confirms the result is
        well-formed, not that no duplicates remain. That claim belongs
        entirely to Buddy's independent Verify stage
        (``buddy.verify.SpreadsheetDedupeVerifier``), precisely so the
        skill's self-report is never the thing that decides success.

        Args:
            task: The task the result was produced for.
            result: The result to verify.

        Returns:
            True if ``result`` reports success, has exactly one output
            path, and that path exists on disk with nonzero size.
        """
        if result.status is not SkillStatus.SUCCESS:
            return False
        if len(result.outputs) != 1:
            return False
        output_path = Path(result.outputs[0])
        return output_path.exists() and output_path.stat().st_size > 0

    @staticmethod
    def _build_output_path(task: SkillTask) -> Path:
        """Construct a fresh output path under ``workspace/outputs/``.

        Always a new path, scoped to the task's workspace and unique to
        this task, so it can never collide with — or be mistaken for —
        the original upload. See ``docs/design/slice-1-dedupe-design.md``
        Section C for the naming convention and the alternatives
        considered.

        Args:
            task: The task to build an output path for.

        Returns:
            The output path. Its parent directory is not yet created.
        """
        input_stem = Path(task.input_references[0]).stem
        return (
            Path("workspace")
            / "outputs"
            / task.workspace_id
            / f"{input_stem}_deduplicated_{task.task_id}.xlsx"
        )
```

---

## `skills/excel/__init__.py` (modified — thin re-export)

```python
"""Excel — skills for working with spreadsheets."""

from skills.excel.dedupe import ExcelDeduplicateSkill

__all__ = ["ExcelDeduplicateSkill"]
```

---

## `tests/buddy/test_plan.py` (new)

```python
"""Unit tests for buddy.plan."""

from __future__ import annotations

from buddy.models import Understanding
from buddy.plan import NullPlanner, Plan, PlanningRequest, RuleBasedPlanner
from buddy.state import BuddyState
from buddy.think import Reasoning


def _make_state() -> BuddyState:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.add_uploaded_file("report.xlsx")
    return state


def _request(**overrides: object) -> PlanningRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(),
        "reasoning": Reasoning(),
        "uploaded_files": (),
        "active_file": None,
    }
    fields.update(overrides)
    return PlanningRequest(**fields)  # type: ignore[arg-type]


# -- NullPlanner ----------------------------------------------------------------


def test_null_planner_produces_no_steps() -> None:
    assert NullPlanner()(_request()) == ()


# -- RuleBasedPlanner -------------------------------------------------------------


def test_rule_based_planner_produces_step_for_known_intent() -> None:
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=False, confidence=0.9),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    steps = RuleBasedPlanner()(request)

    assert len(steps) == 1
    assert steps[0].capability == "spreadsheet.deduplicate"
    assert "report.xlsx" in steps[0].description


def test_rule_based_planner_produces_nothing_for_unknown_intent() -> None:
    request = _request(
        understanding=Understanding(intent="unclear", confidence=0.1),
        reasoning=Reasoning(clarification_required=False),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    assert RuleBasedPlanner()(request) == ()


def test_rule_based_planner_produces_nothing_when_clarification_required() -> None:
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=True),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    assert RuleBasedPlanner()(request) == ()


def test_rule_based_planner_produces_nothing_without_a_file() -> None:
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=False),
        uploaded_files=(),
        active_file=None,
    )

    assert RuleBasedPlanner()(request) == ()


def test_rule_based_planner_falls_back_to_most_recent_upload() -> None:
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=False),
        uploaded_files=("first.xlsx", "second.xlsx"),
        active_file=None,
    )

    steps = RuleBasedPlanner()(request)

    assert len(steps) == 1
    assert "second.xlsx" in steps[0].description


# -- Plan stage -----------------------------------------------------------------


def test_plan_sets_typed_planning_field_and_mirrors_legacy_plan() -> None:
    state = _make_state()
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    state.reasoning = Reasoning(clarification_required=False, confidence=0.9)
    state.context.switch_active_file("report.xlsx")
    stage = Plan(planner=RuleBasedPlanner())

    stage(state)

    assert state.planning is not None
    assert len(state.planning) == 1
    assert state.planning[0].capability == "spreadsheet.deduplicate"
    assert state.plan == [state.planning[0].description]


def test_plan_defaults_to_null_planner_when_none_configured() -> None:
    state = _make_state()
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    stage = Plan()

    stage(state)

    assert state.planning == ()
    assert state.plan == []
```

---

## `tests/buddy/test_delegate.py` (new)

```python
"""Unit tests for buddy.delegate."""

from __future__ import annotations

from buddy.delegate import Delegate, DelegationRequest, MechanicalDelegator, NullDelegator
from buddy.models import CapabilityRequest, PlanningStep
from buddy.state import BuddyState


def _make_state() -> BuddyState:
    return BuddyState.new(session_id="session-1", workspace_id="workspace-1")


def _request(**overrides: object) -> DelegationRequest:
    fields: dict[str, object] = {
        "planning": (),
        "uploaded_files": (),
        "active_file": None,
    }
    fields.update(overrides)
    return DelegationRequest(**fields)  # type: ignore[arg-type]


def _step(capability: str = "spreadsheet.deduplicate") -> PlanningStep:
    return PlanningStep(id="step-1", description="dedupe", capability=capability)


# -- NullDelegator ----------------------------------------------------------------


def test_null_delegator_produces_no_capability_requests() -> None:
    assert NullDelegator()(_request()) == ()


# -- MechanicalDelegator -----------------------------------------------------------


def test_mechanical_delegator_produces_one_request_per_step() -> None:
    request = _request(planning=(_step(),), active_file="report.xlsx")

    requests = MechanicalDelegator()(request)

    assert requests == (
        CapabilityRequest(
            capability="spreadsheet.deduplicate", target="report.xlsx", parameters={}
        ),
    )


def test_mechanical_delegator_falls_back_to_most_recent_upload() -> None:
    request = _request(
        planning=(_step(),), uploaded_files=("a.xlsx", "b.xlsx"), active_file=None
    )

    requests = MechanicalDelegator()(request)

    assert requests[0].target == "b.xlsx"


def test_mechanical_delegator_produces_nothing_without_a_target_file() -> None:
    request = _request(planning=(_step(),), uploaded_files=(), active_file=None)

    assert MechanicalDelegator()(request) == ()


def test_mechanical_delegator_never_references_a_skill_by_name() -> None:
    """The capability string passes through verbatim from the
    ``PlanningStep`` — ``MechanicalDelegator`` has no knowledge of
    ``skills.excel`` or any other concrete Skill.
    """
    request = _request(planning=(_step("some.other.capability"),), active_file="report.xlsx")

    requests = MechanicalDelegator()(request)

    assert requests[0].capability == "some.other.capability"


# -- Delegate stage -----------------------------------------------------------------


def test_delegate_sets_typed_delegation_field_and_mirrors_legacy_tasks() -> None:
    state = _make_state()
    state.planning = (_step(),)
    state.context.switch_active_file("report.xlsx")
    stage = Delegate(delegator=MechanicalDelegator())

    stage(state)

    assert state.delegation == (
        CapabilityRequest(
            capability="spreadsheet.deduplicate", target="report.xlsx", parameters={}
        ),
    )
    assert state.tasks == [
        {"capability": "spreadsheet.deduplicate", "target": "report.xlsx", "parameters": {}}
    ]


def test_delegate_defaults_to_null_delegator_when_none_configured() -> None:
    state = _make_state()
    stage = Delegate()

    stage(state)

    assert state.delegation == ()
    assert state.tasks == []
```

---

## `tests/skills/excel/test_dedupe.py` (new)

```python
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
```
