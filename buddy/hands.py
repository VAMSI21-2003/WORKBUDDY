"""Hands — carries out delegated work and produces results."""

import logging

from buddy.models import CapabilityRequest, ExecutionResult
from buddy.state import BuddyState
from core.dispatcher import Dispatcher, DispatchError
from skills.base import PermissionTier, SkillStatus, SkillTask

logger = logging.getLogger(__name__)


class Hands:
    """The Hands stage of Buddy's cognitive cycle.

    Hands is the only stage that crosses out of Buddy's core into a
    Skill (see ``docs/WORKBUDDY_ARCHITECTURE.md`` Sections 4 and 11). For
    each capability request Delegate recorded, it builds a ``SkillTask``,
    asks the injected ``Dispatcher`` to select a capable Skill, and — only
    then — invokes that Skill's ``execute()`` itself. Hands never looks
    up a Skill on its own; every invocation goes through a
    Dispatcher-prepared ``ExecutionRequest``.

    Unlike Brain/Think/Planner/Delegate, Hands has no ``Null*``
    default and no swappable protocol of its own: the architecture doc
    frames the Dispatcher as Hands's one required collaborator, not a
    strategy to be substituted (Section 11 — "Execute is the Dispatcher's
    only caller and its only consumer"). Constructing Hands with a
    ``Dispatcher`` backed by an empty ``SkillRegistry`` already produces
    correct "nothing configured" behavior for free — it raises
    ``NoCapableSkillError`` for any real request, which Hands turns into
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
            logger.debug("Hands: nothing to execute for session %r", state.session_id)
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
                    "Hands: could not dispatch capability %r for session %r: %s",
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
            "Hands recorded success=%s outputs=%d for session %r",
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
