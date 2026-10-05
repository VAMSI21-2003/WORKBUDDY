"""Confirmation — the once-per-turn plan approval.

Design Section 1.5, "Decisions (2026-09-30)" item 2, and "Decisions
(2026-10-01)" items 4 and 5. Before the first file-changing step of a turn,
the user sees the whole plan (every step that will change a file, in
order) and approves or rejects it once. Approval covers every step of the
plan. A rejection, a timeout or a server shutdown means no step runs; none
of them is ever an approval.

The plan comes from the pipeline, not from the user's message: ``Hands``
resolves every delegated step through its Dispatcher before running any
(``buddy.hands.Hands.confirmation_plan``) and hands the steps that will ask
for confirmation to the turn's ``WebConfirmationProvider``. The provider
publishes them on the first ``confirm`` call, waits for the answer, and
then answers that call and every later one in the turn from the one
decision. A ``confirm`` call for anything but the next step of the
published plan is refused, so nothing runs that the user wasn't shown.

The text shown is built here by code, from fixed templates per capability
and the upload's (sanitized) name. It describes proposed steps and states
no result.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from backend.errors import ApiError, ErrorCode
from buddy.hands import ConfirmationStep
from skills.base import PermissionTier, SkillTask

logger = logging.getLogger(__name__)

APPROVE = "approve"
REJECT = "reject"
DECISIONS = (APPROVE, REJECT)

# Who or what decided. Only the user ever approves.
BY_USER = "user"
BY_TIMEOUT = "timeout"
BY_SHUTDOWN = "shutdown"
# The turn finished (its deadline passed) before its plan could be asked.
BY_TURN_END = "turn_finished"
DECISION_SOURCES = (BY_USER, BY_TIMEOUT, BY_SHUTDOWN, BY_TURN_END)

# The plan-level line for two or more steps ("Decisions (2026-10-01)",
# item 4). A constant, never built from request data. It leaves out
# "nothing runs if you reject" (the analysis runs either way) and anything
# about the original (a promise the card can't verify).
PLAN_NOTE_CHAIN = (
    "The steps run in this order, each on the previous step's result. "
    "Only the final file is handed back."
)

# The first step's template, then a later step's, per capability. A later
# step reads the previous step's result (every multi-step plan is a chain),
# so only the first names the upload. No template says "save", "new file"
# or anything about the original, and none states a result.
_TEMPLATES: dict[str, tuple[str, str]] = {
    "spreadsheet.deduplicate": (
        "Remove duplicate rows from {input_name}.",
        "Then remove duplicate rows from the result of step {previous}.",
    ),
    "spreadsheet.remove_empty_rows": (
        "Remove empty rows from {input_name}.",
        "Then remove empty rows from the result of step {previous}.",
    ),
}
_FALLBACK_TEMPLATES = (
    "Run {capability} on {input_name}.",
    "Then run {capability} on the result of step {previous}.",
)

_CONFIRMED_TIERS = (PermissionTier.WRITE, PermissionTier.EXTERNAL_EFFECT)


def has_templates(capability: str) -> bool:
    """Whether ``capability`` has its own templates (not the fallback)."""
    return capability in _TEMPLATES


def step_prompt(capability: str, position: int, input_name: str) -> str:
    """The text shown for one step of the plan.

    Args:
        capability: The step's capability. Plan steps are only steps the
            Dispatcher could route, so this is a registered capability.
        position: The step's position in the plan, counting from 1.
        input_name: The name of the file the first step reads.
    """
    first, later = _TEMPLATES.get(capability, _FALLBACK_TEMPLATES)
    if position == 1:
        return first.format(capability=capability, input_name=input_name)
    return later.format(capability=capability, previous=position - 1)


def plan_note(step_count: int) -> str | None:
    """``PLAN_NOTE_CHAIN`` for two or more steps, else ``None``."""
    return PLAN_NOTE_CHAIN if step_count >= 2 else None


@dataclass(frozen=True)
class PlanStep:
    """One step as the user sees it: its capability and its prompt."""

    capability: str
    prompt: str


@dataclass(frozen=True)
class PendingPlan:
    """A published plan waiting for its answer (the public ``pending_confirmation``)."""

    confirmation_id: str
    steps: tuple[PlanStep, ...]
    plan_note: str | None
    expires_at: datetime


@dataclass(frozen=True)
class ConfirmationOutcome:
    """How a turn's plan was decided: ``decision`` by ``source``."""

    decision: str
    source: str


def new_confirmation_id() -> str:
    return "c_" + secrets.token_urlsafe(16)


def build_plan(
    steps: tuple[ConfirmationStep, ...], *, confirmation_id: str, expires_at: datetime
) -> PendingPlan:
    """The plan as published: one ``PlanStep`` per step, in order, and the note.

    Raises:
        ValueError: ``steps`` is empty. A plan is never published without a
            step to approve.
    """
    if not steps:
        raise ValueError("a plan needs at least one step")
    input_name = Path(steps[0].input_reference).name
    return PendingPlan(
        confirmation_id=confirmation_id,
        steps=tuple(
            PlanStep(step.capability, step_prompt(step.capability, position, input_name))
            for position, step in enumerate(steps, start=1)
        ),
        plan_note=plan_note(len(steps)),
        expires_at=expires_at,
    )


class StaticConfirmationProvider:
    """Answers every confirmation the same way, without asking. A test setting only.

    ``Settings.auto_approve`` selects it with ``approve=True``; nothing in
    the default configuration does (design Section 8.3, PR-3).
    """

    def __init__(self, approve: bool) -> None:
        self._approve = approve

    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        return self._approve


class WebConfirmationProvider:
    """One turn's plan approval: published once, answered once, by the user.

    Built per turn by the turn runner, so it has no cross-turn state. Its
    lock is the session's lock, so the plan, the turn record and the
    decision change together. Hands calls ``expect_plan`` (before its first
    step) and ``confirm`` (before each file-changing step) on the turn's
    worker thread; the confirmation endpoint calls ``answer``; finishing the
    turn or shutting the server down calls ``close``.

    - The first ``confirm`` for the plan's first step publishes the plan
      (``on_publish``) and waits, without the lock, for ``answer`` or the
      timeout. A timeout is a rejection.
    - Every ``confirm`` returns the one decision: ``True`` only after an
      approval by the user, and never once the provider is closed.
    - A ``confirm`` that isn't for the next step of the plan Hands
      announced (another task, another tier, out of order, no plan, a step
      past its end) gets ``False``, and so does every later one.
    - ``close`` turns a waiting or future question into a rejection.

    Args:
        lock: The session's lock.
        timeout_s: How long a published plan waits for its answer.
        clock: The time now (for ``expires_at``).
        on_publish: Called under the lock with the plan about to be
            published. It returns ``False`` if the turn is already
            finishing, and then nothing is published and the step is
            refused.
        on_settle: Called under the lock once the plan is decided (or
            refused before it could be asked), with the outcome.
    """

    def __init__(
        self,
        *,
        lock: threading.RLock,
        timeout_s: float,
        clock: Callable[[], datetime],
        on_publish: Callable[[PendingPlan], bool],
        on_settle: Callable[[ConfirmationOutcome], None],
    ) -> None:
        self._lock = lock
        self._timeout_s = timeout_s
        self._clock = clock
        self._on_publish = on_publish
        self._on_settle = on_settle
        self._answered = threading.Event()
        self._plan: tuple[ConfirmationStep, ...] | None = None
        self._next = 0
        self._pending: PendingPlan | None = None
        self._outcome: ConfirmationOutcome | None = None
        self._closed_by: str | None = None
        self._refusing = False

    @property
    def pending(self) -> PendingPlan | None:
        """The published plan waiting for its answer, if any."""
        with self._lock:
            return self._pending

    @property
    def outcome(self) -> ConfirmationOutcome | None:
        """How the plan was decided, once it was."""
        with self._lock:
            return self._outcome

    def expect_plan(self, steps: tuple[ConfirmationStep, ...]) -> None:
        """Hands' plan: every step that will ask for confirmation, in order."""
        with self._lock:
            if self._plan is not None:
                # Hands announces once per run; a second plan means the
                # first can't be trusted to be what runs.
                logger.error("A second plan was announced for one turn; refusing every step")
                self._refusing = True
                return
            self._plan = tuple(steps)

    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        """Whether ``task`` may run: only if it's the plan's next step and the user approved."""
        with self._lock:
            if not self._is_next_step(task, tier):
                if not self._refusing:
                    logger.error(
                        "A step outside the published plan asked for confirmation "
                        "(%s, tier %s); refusing it and every later step",
                        task.task_id,
                        tier.value,
                    )
                self._refusing = True
                return False
            self._next += 1
            if self._closed_by is not None:
                # The turn finished or the server is stopping: no step starts
                # now, even one the user approved (review round 1, L2).
                self._settle(ConfirmationOutcome(REJECT, self._closed_by))
                return False
            if self._outcome is not None:
                return self._outcome.decision == APPROVE
            assert self._plan is not None
            plan = build_plan(
                self._plan,
                confirmation_id=new_confirmation_id(),
                expires_at=self._clock() + timedelta(seconds=self._timeout_s),
            )
            if not self._on_publish(plan):
                self._settle(ConfirmationOutcome(REJECT, BY_TURN_END))
                return False
            self._pending = plan
        self._answered.wait(self._timeout_s)
        with self._lock:
            if self._outcome is None:
                self._settle(ConfirmationOutcome(REJECT, BY_TIMEOUT))
            assert self._outcome is not None
            if self._closed_by is not None:
                # The turn finished, or the server is stopping, while this
                # step waited for the lock: not even the step the user just
                # approved starts now (review round 2, R2-L1).
                return False
            return self._outcome.decision == APPROVE

    def answer(self, confirmation_id: str, decision: str) -> None:
        """Record the user's answer to the published plan.

        Raises:
            ApiError: ``no_pending_confirmation`` when no plan is waiting
                (none was published, or it was already answered, timed out
                or closed); ``stale_confirmation`` when ``confirmation_id``
                isn't the waiting plan's.
        """
        if decision not in DECISIONS:
            raise ApiError(ErrorCode.INVALID_REQUEST)
        with self._lock:
            pending = self._pending
            if pending is None:
                raise ApiError(ErrorCode.NO_PENDING_CONFIRMATION)
            if confirmation_id != pending.confirmation_id:
                raise ApiError(ErrorCode.STALE_CONFIRMATION)
            try:
                self._settle(ConfirmationOutcome(decision, BY_USER))
            finally:
                self._answered.set()

    def close(self, source: str) -> None:
        """No answer can come any more (``source``): a waiting or later question is a rejection."""
        with self._lock:
            if self._closed_by is None:
                self._closed_by = source
            try:
                if self._pending is not None:
                    self._settle(ConfirmationOutcome(REJECT, source))
            finally:
                self._answered.set()

    def _is_next_step(self, task: SkillTask, tier: PermissionTier) -> bool:
        plan = self._plan
        if self._refusing or plan is None or self._next >= len(plan):
            return False
        expected = plan[self._next]
        return (
            tier in _CONFIRMED_TIERS
            and tier is expected.tier
            and task.permission is expected.tier
            and task.task_id == expected.task_id
        )

    def _settle(self, outcome: ConfirmationOutcome) -> None:
        """Record ``outcome`` (the caller holds the lock). The first outcome stands."""
        if self._outcome is not None:
            return
        self._outcome = outcome
        self._pending = None
        self._on_settle(outcome)
