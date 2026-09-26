"""Hands — carries out delegated work and produces results."""

import logging
from typing import Protocol

from buddy.models import CapabilityRequest, ExecutionResult, StepResult
from buddy.state import BuddyState
from core.dispatcher import Dispatcher, DispatchError
from skills.base import PermissionTier, SkillStatus, SkillTask

logger = logging.getLogger(__name__)

_CONFIRMATION_REQUIRED_TIERS = (PermissionTier.WRITE, PermissionTier.EXTERNAL_EFFECT)


class ConfirmationProvider(Protocol):
    """Decides whether a permission-gated task may proceed.

    This is the seam where real user confirmation is plugged in — asking
    the user before Buddy takes a Write- or External-Effect-tier action,
    per ``docs/BUDDY_SKILLS.md`` Section 13. Hands itself never decides
    what "yes" means or how to ask; it only calls whichever
    ``ConfirmationProvider`` it was given, exactly the same seam pattern
    ``Interpreter``/``Thinker``/``PlanningStrategy``/``Delegator``/
    ``Verifier``/``Speaker`` already follow for their own stages.
    """

    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        """Return whether ``task`` may proceed at permission tier ``tier``."""
        ...


class NullConfirmationProvider:
    """A ``ConfirmationProvider`` that approves every request unconditionally.

    This is not a real confirmation flow — there is no user-facing surface
    to ask through yet (no frontend/backend; see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 3). It exists so ``Hands``
    has a safe, honestly-named default in the meantime. Auto-approving
    everything is exactly what happened before this class existed, when
    no confirmation gate was wired in at all — so wiring this in as the
    default is not a behavior change, only a way of making that previously
    implicit "always yes" explicit and swappable, so a real
    "ask the user" implementation can replace it later without touching
    ``Hands``.
    """

    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        """Approve any task unconditionally.

        Args:
            task: The task under consideration. Unused — approval is
                unconditional.
            tier: The permission tier being requested. Unused — approval
                is unconditional.

        Returns:
            True, always.
        """
        return True


class Hands:
    """The Hands stage of Buddy's cognitive cycle.

    Hands is the only stage that crosses out of Buddy's core into a
    Skill (see ``docs/WORKBUDDY_ARCHITECTURE.md`` Sections 4 and 11). For
    each capability request Delegate recorded, it builds a ``SkillTask``,
    asks the injected ``Dispatcher`` to select a capable Skill, checks
    that Skill's own declared ``permission_tier`` against an injected
    ``ConfirmationProvider`` when that tier requires confirmation, and —
    only then — invokes that Skill's ``execute()`` itself. Hands never
    looks up a Skill on its own; every invocation goes through a
    Dispatcher-prepared ``ExecutionRequest``.

    Unlike Brain/Think/Planner/Delegate, Hands takes the ``Dispatcher`` as
    a required, non-swappable collaborator, not a strategy with a
    ``Null*`` default: the architecture doc frames it as Hands's one
    required routing collaborator (Section 11 — "Execute is the
    Dispatcher's only caller and its only consumer"). Constructing Hands
    with a ``Dispatcher`` backed by an empty ``SkillRegistry`` already
    produces correct "nothing configured" behavior for free — it raises
    ``NoCapableSkillError`` for any real request, which Hands turns into a
    failed ``ExecutionResult`` rather than a crash — so no separate null
    ``Dispatcher`` implementation is needed.

    Confirmation, unlike routing, *is* a policy decision Hands doesn't own
    — whether a Write/External-Effect action may proceed is a product
    question, not a fixed architectural rule — so it follows the same
    swappable-protocol-plus-``Null*``-default pattern every other stage's
    collaborator does: ``ConfirmationProvider`` / ``NullConfirmationProvider``.
    """

    def __init__(
        self, dispatcher: Dispatcher, confirmation_provider: ConfirmationProvider | None = None
    ) -> None:
        """Initialize the stage.

        Args:
            dispatcher: The Dispatcher used to resolve each capability
                request to a capable Skill.
            confirmation_provider: Consulted before executing any task
                whose selected Skill declares a Write or External-Effect
                permission tier. Defaults to ``NullConfirmationProvider``,
                which approves everything unconditionally — see its
                docstring for why that's an honest default today, not a
                real confirmation flow.
        """
        self._dispatcher = dispatcher
        if confirmation_provider is None:
            logger.debug(
                "No confirmation provider given; falling back to NullConfirmationProvider"
            )
        self._confirmation_provider = (
            confirmation_provider
            if confirmation_provider is not None
            else NullConfirmationProvider()
        )

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
            logger.debug("Hands: nothing to execute for session %r", state.session_id)
            self._apply(state, ExecutionResult(success=True, warnings=("nothing to execute",)), [])
            return state

        outputs: list[str] = []
        warnings: list[str] = []
        errors: list[str] = []
        raw_outputs: list[dict[str, object]] = []
        step_results: list[StepResult] = []

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
                    "Hands: could not dispatch capability %r for session %r: %s",
                    request.capability,
                    state.session_id,
                    exc,
                )
                errors.append(str(exc))
                continue

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

            raw_outputs.append(
                {
                    "task_id": task.task_id,
                    "skill": skill.name,
                    "status": result.status.value,
                    "outputs": list(result.outputs),
                    "summary": result.summary,
                }
            )
            step_results.append(
                StepResult(
                    capability=request.capability,
                    skill=skill.name,
                    status=result.status.value,
                    outputs=tuple(result.outputs),
                    summary=result.summary,
                    self_reported_findings=tuple(result.self_reported_findings),
                )
            )
            outputs.extend(result.outputs)
            warnings.extend(result.warnings)

            task_errors = list(result.errors)
            if result.status is SkillStatus.FAILURE and not task_errors:
                task_errors.append(f"{skill.name} reported failure")
            errors.extend(task_errors)

        state.active_task = None
        execution_result = ExecutionResult(
            success=not errors,
            outputs=tuple(outputs),
            warnings=tuple(warnings),
            errors=tuple(errors),
            steps=tuple(step_results),
        )
        self._apply(state, execution_result, raw_outputs)
        logger.debug(
            "Hands recorded success=%s outputs=%d for session %r",
            execution_result.success,
            len(execution_result.outputs),
            state.session_id,
        )
        return state

    @staticmethod
    def _build_task(state: BuddyState, request: CapabilityRequest, index: int) -> SkillTask:
        """Build the ``SkillTask`` to hand to the Dispatcher for one request.

        The task's ``permission`` is left at ``SkillTask``'s own default
        (``PermissionTier.READ``) here — it is not yet known which Skill
        will actually run this request, so there is nothing real to set it
        to. ``__call__`` overwrites it with the selected Skill's own
        declared ``permission_tier`` once the Dispatcher has resolved one,
        which is what makes the eventual permission check reflect the
        Skill actually running, not a guess made before dispatch.

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
