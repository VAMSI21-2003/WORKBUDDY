"""Hands — carries out delegated work and produces results."""

import logging
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from buddy.inspector import VerificationRequest, Verifier
from buddy.models import (
    DECLINED_STATUS,
    SKIPPED_STATUS,
    CapabilityRequest,
    ExecutionResult,
    StepResult,
    Understanding,
)
from buddy.state import BuddyState
from core.dispatcher import Dispatcher, DispatchError
from skills.base import PermissionTier, SkillStatus, SkillTask

logger = logging.getLogger(__name__)

_CONFIRMATION_REQUIRED_TIERS = (PermissionTier.WRITE, PermissionTier.EXTERNAL_EFFECT)

# The file types a chain step's output may have to carry the chain forward.
_SPREADSHEET_SUFFIXES = (".xlsx", ".xls")


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

    **Chaining.** Steps whose Skill changes files (any tier above READ)
    form a chain: each reads the previous chain step's output, so the user
    gets one file with every change applied, in plan order. A READ-tier
    step (e.g. proactive ``spreadsheet.analyze``) is outside the chain: it
    always reads the original upload, never feeds the next step, and its
    failure doesn't stop the chain. A chain step's output feeds the next
    step only when it is exactly one file with the same extension as its
    input. If any chain step fails — it can't be dispatched, isn't
    confirmed, reports failure, fails its own well-formedness check,
    fails the injected ``step_verifier``, or its output can't feed the
    chain — the chain stops: the failing step is recorded with a failing
    status (so Inspector sees it), every later step is recorded as
    skipped, and nothing is handed back as a result.
    Skills never write to their input, so the original upload is untouched
    either way. ``ExecutionResult.outputs`` holds only the final file;
    intermediate files stay on disk, listed on each ``StepResult``.
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        confirmation_provider: ConfirmationProvider | None = None,
        step_verifier: Verifier | None = None,
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
            step_verifier: Checks each chain step's result before the next
                step may read it, so a step that fails verification stops
                the chain instead of feeding bad data forward. The same
                ``Verifier`` Inspector uses; Inspector still re-verifies
                everything afterwards and remains the final word. ``None``
                (the default) skips this in-loop check.
        """
        self._dispatcher = dispatcher
        self._step_verifier = step_verifier
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

        warnings: list[str] = []
        errors: list[str] = []
        raw_outputs: list[dict[str, object]] = []
        step_results: list[StepResult] = []
        # The file the next chain step reads: None until a chain step
        # produces one, meaning "read the delegated target (the original)".
        chain_input: str | None = None
        # Outputs handed back besides the chain's final file: any a
        # read-only step produced (a chain step's output always feeds the
        # chain, or the chain stops).
        other_outputs: list[str] = []
        # READ-tier requests that ran on the original, re-run on the final
        # file once the chain completes.
        read_requests: list[CapabilityRequest] = []
        # Why the chain stopped, e.g. "spreadsheet.deduplicate failed
        # verification" — set on the first chain-step failure.
        stopped_because: str | None = None
        stopped_at: str | None = None

        for index, request in enumerate(requests):
            if stopped_because is not None:
                step_results.append(
                    StepResult(
                        capability=request.capability,
                        skill="",
                        status=SKIPPED_STATUS,
                        summary=f"skipped because {stopped_because}",
                    )
                )
                continue

            input_reference = chain_input or request.target
            task = self._build_task(state, request, index, input_reference)
            state.active_task = {
                "task_id": task.task_id,
                "capability": request.capability,
                "target": input_reference,
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
                step_results.append(
                    StepResult(
                        capability=request.capability,
                        skill="",
                        status=SkillStatus.FAILURE.value,
                        summary=str(exc),
                        input_reference=input_reference,
                        failure_reason="couldn't be run because no available skill can do it",
                    )
                )
                stopped_because = f"{request.capability} could not be run"
                stopped_at = request.capability
                continue

            skill = execution_request.skill
            # The task's permission is not known until now: it belongs to
            # whichever Skill the Dispatcher actually selected, not to the
            # capability request itself. Overwriting it here (rather than
            # guessing a tier in ``_build_task``) is what makes this the
            # Skill's own declared tier, not a hardcoded one.
            task.permission = skill.permission_tier

            # A READ-tier step changes no file, so it isn't part of the
            # chain: it always reads the original upload, and its failure
            # doesn't stop the steps that do change the file.
            in_chain = task.permission is not PermissionTier.READ
            if not in_chain:
                input_reference = request.target
                task.input_references = [input_reference]

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
                step_results.append(
                    StepResult(
                        capability=request.capability,
                        skill=skill.name,
                        status=DECLINED_STATUS,
                        summary="confirmation was declined",
                        input_reference=input_reference,
                        permission_tier=task.permission.value,
                        failure_reason="wasn't confirmed, so it didn't run",
                    )
                )
                stopped_because = f"{request.capability} was not confirmed"
                stopped_at = request.capability
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
                        input_reference=input_reference,
                        permission_tier=task.permission.value,
                    )
                )
                errors.append(
                    f"{skill.name} reported {result.status.value} but failed its own "
                    f"well-formedness check; result discarded"
                )
                if in_chain:
                    stopped_because = f"{request.capability} produced a malformed result"
                    stopped_at = request.capability
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
            step = StepResult(
                capability=request.capability,
                skill=skill.name,
                status=result.status.value,
                outputs=tuple(result.outputs),
                summary=result.summary,
                self_reported_findings=tuple(result.self_reported_findings),
                input_reference=input_reference,
                permission_tier=task.permission.value,
            )
            step_results.append(step)
            warnings.extend(result.warnings)

            task_errors = list(result.errors)
            if result.status is SkillStatus.FAILURE and not task_errors:
                task_errors.append(f"{skill.name} reported failure")
            errors.extend(task_errors)

            if not in_chain:
                other_outputs.extend(result.outputs)
                read_requests.append(request)
                continue
            if result.status is not SkillStatus.SUCCESS:
                stopped_because = f"{request.capability} did not complete"
                stopped_at = request.capability
                continue

            verification_failure = self._verify_step(state, step)
            if verification_failure is not None:
                errors.append(f"{request.capability} failed verification: {verification_failure}")
                stopped_because = f"{request.capability} failed verification"
                stopped_at = request.capability
                continue

            reason = self._why_output_cant_feed_the_chain(state, requests, index, step.outputs)
            if reason is not None:
                # Continuing from the old input would silently leave this
                # step's change out of the final file, so stop instead.
                step_results[-1] = replace(
                    step, status=SkillStatus.FAILURE.value, failure_reason=reason
                )
                errors.append(f"{request.capability} {reason}")
                stopped_because = f"{request.capability}'s output couldn't be carried forward"
                stopped_at = request.capability
                continue
            chain_input = step.outputs[0]

        final_outputs: list[str] = []
        if stopped_because is not None:
            skipped = sum(1 for s in step_results if s.status == SKIPPED_STATUS)
            if skipped:
                errors.append(
                    f"stopped: {stopped_because}; {skipped} later step(s) were skipped "
                    "and no file was handed back"
                )
        else:
            if chain_input is not None:
                final_outputs.append(chain_input)
                # Findings about the original no longer describe the file
                # the user receives, so re-run each read-only check on it.
                step_results.extend(
                    self._recheck_final_file(
                        state, read_requests, chain_input, len(requests), warnings
                    )
                )
            final_outputs.extend(other_outputs)

        state.active_task = None
        execution_result = ExecutionResult(
            success=not errors,
            outputs=tuple(final_outputs),
            warnings=tuple(warnings),
            errors=tuple(errors),
            steps=tuple(step_results),
            stopped_at=stopped_at,
        )
        self._apply(state, execution_result, raw_outputs)
        logger.debug(
            "Hands recorded success=%s outputs=%d for session %r",
            execution_result.success,
            len(execution_result.outputs),
            state.session_id,
        )
        return state

    def _recheck_final_file(
        self,
        state: BuddyState,
        read_requests: list[CapabilityRequest],
        final_file: str,
        first_index: int,
        warnings: list[str],
    ) -> list[StepResult]:
        """Re-run each read-only step of this turn against the final file.

        The read-only steps (proactive analysis) ran on the original upload;
        once the chain has changed the file, their findings describe a file
        the user no longer receives. Re-running them here gives Voice
        findings about the file actually handed back. These re-checks stay
        outside the chain: they never feed or stop it, and a failure is
        recorded as a warning, not an error.

        Args:
            state: The current ``BuddyState`` for this turn.
            read_requests: The READ-tier requests that ran this turn.
            final_file: The chain's final output.
            first_index: Task index to number the re-checks from, so their
                task ids never collide with the chain's.
            warnings: This turn's warnings, appended to on failure.

        Returns:
            One ``StepResult`` per distinct capability re-checked, with
            ``input_reference`` set to ``final_file``.
        """
        rechecked: list[StepResult] = []
        distinct = {request.capability: request for request in read_requests}.values()
        for offset, request in enumerate(distinct):
            task = self._build_task(state, request, first_index + offset, final_file)
            try:
                skill = self._dispatcher.dispatch(task, input_type=request.capability).skill
            except DispatchError as exc:
                warnings.append(f"could not re-check the final file with {request.capability}: {exc}")
                continue
            task.permission = skill.permission_tier
            if task.permission is not PermissionTier.READ:
                continue  # never let a re-check change a file
            result = skill.execute(task)
            ok = result.status is SkillStatus.SUCCESS and skill.verify(task, result)
            if not ok:
                warnings.append(f"could not re-check the final file with {request.capability}")
            rechecked.append(
                StepResult(
                    capability=request.capability,
                    skill=skill.name,
                    status=result.status.value if ok else SkillStatus.FAILURE.value,
                    outputs=tuple(result.outputs) if ok else (),
                    summary=result.summary,
                    self_reported_findings=tuple(result.self_reported_findings) if ok else (),
                    input_reference=final_file,
                    permission_tier=task.permission.value,
                    recheck=True,
                )
            )
        return rechecked

    def _verify_step(self, state: BuddyState, step: StepResult) -> str | None:
        """Check one chain step's result before the next step may read it.

        Args:
            state: The current ``BuddyState`` for this turn.
            step: The step that just ran successfully.

        Returns:
            ``None`` if the step passed (or no ``step_verifier`` is
            configured); otherwise a plain-language reason it failed.
        """
        if self._step_verifier is None:
            return None
        verdict = self._step_verifier(
            VerificationRequest(
                understanding=state.understanding or Understanding(),
                execution_result=ExecutionResult(success=True, outputs=step.outputs, steps=(step,)),
                active_file=state.context.active_file,
            )
        )
        if verdict.passed:
            return None
        return "; ".join(verdict.findings) or "no reason given"

    def _why_output_cant_feed_the_chain(
        self,
        state: BuddyState,
        requests: tuple[CapabilityRequest, ...],
        index: int,
        outputs: tuple[str, ...],
    ) -> str | None:
        """Say why a chain step's outputs can't carry the chain forward, if they can't.

        They can when there is exactly one output, it's a spreadsheet, and
        the next file-changing step has a skill that accepts it. The
        output's type needn't match the step's input: a skill that reads
        an ``.xls`` and writes an ``.xlsx`` is fine. A report or chart a
        future skill writes alongside (or instead of) the spreadsheet must
        never be handed on or back.

        Args:
            state: The current ``BuddyState`` for this turn.
            requests: Every request delegated this turn.
            index: The position of the step that produced ``outputs``.
            outputs: What that step produced.

        Returns:
            The reason, for the step's ``failure_reason``, or ``None``.
        """
        if len(outputs) != 1 or not outputs[0].lower().endswith(_SPREADSHEET_SUFFIXES):
            return (
                f"produced {len(outputs)} file(s) instead of one spreadsheet, so its "
                "result couldn't be carried forward"
            )
        for later_index in range(index + 1, len(requests)):
            later = requests[later_index]
            candidates = self._dispatcher.locate_by_capability(later.capability)
            if not candidates:
                # That step can't run at all; it stops the chain itself.
                return None
            changers = [s for s in candidates if s.permission_tier is not PermissionTier.READ]
            if not changers:
                continue  # read-only: outside the chain, it reads the original
            probe = self._build_task(state, later, later_index, outputs[0])
            if any(skill.can_handle(probe) for skill in changers):
                return None
            return (
                f"produced a {Path(outputs[0]).suffix} file the next step can't read, so its "
                "result couldn't be carried forward"
            )
        return None

    @staticmethod
    def _build_task(
        state: BuddyState, request: CapabilityRequest, index: int, input_reference: str
    ) -> SkillTask:
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
            input_reference: The file this step reads — the delegated
                target, or the previous chain step's output.

        Returns:
            The ``SkillTask`` to dispatch.
        """
        return SkillTask(
            task_id=f"{state.session_id}-task-{index}",
            session_id=state.session_id,
            workspace_id=state.workspace_id,
            description=f"Apply {request.capability} to {input_reference}",
            input_references=[input_reference],
            parameters=dict(request.parameters),
            output_dir=state.turn_output_dir,
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


