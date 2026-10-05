"""BuddyState — the shared representation of Buddy's current understanding
of the user's work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from buddy.context import Context
from buddy.models import (
    CapabilityRequest,
    ExecutionResult,
    PlanningStep,
    Understanding,
    VerificationResult,
)

if TYPE_CHECKING:
    # Imported only for type checking: buddy.think imports BuddyState from
    # this module, so importing Reasoning at runtime here would create a
    # circular import. The string-based annotations enabled by
    # ``from __future__ import annotations`` let the field below reference
    # it safely without ever executing this import.
    from buddy.think import Reasoning


@dataclass(frozen=True)
class StageFailure:
    """Which stage of the cognitive cycle raised during a turn.

    Recorded by the Engine when a stage raises, so Voice can still give an
    honest reply. Deliberately carries no exception message and no
    traceback: an LLM or pandas error text can contain file contents or
    request fragments, and nothing here may reach the user. The traceback
    is logged server side only (``core.engine.Engine.run``).

    Attributes:
        stage: The ``CognitiveCycle`` slot that raised, e.g. ``"inspector"``.
        error_type: The exception's class name, e.g. ``"ValueError"``.
    """

    stage: str
    error_type: str


@dataclass
class BuddyState:
    """Buddy's complete working memory for a unit of work.

    A single ``BuddyState`` instance flows through every stage of Buddy's
    cognitive cycle (Observe, Understand, Think, Plan, Delegate, Dispatcher,
    Execute, Verify, Speak). Each stage reads from and writes to this
    object rather than passing its own private data around, so it is the
    single source of truth for what Buddy currently knows and is doing
    *during this cycle*. ``BuddyState`` is a passive container: it holds
    data and offers small, mechanical helpers for updating it, but it
    never itself decides what that data means — that judgment always
    belongs to whichever stage produced it.

    ``BuddyState`` owns a ``Context`` instance (``buddy.context.Context``)
    rather than duplicating what it tracks. Conversational and workspace
    context — which files are available, which task or file is currently
    active, recent conversation and execution history — lives entirely in
    ``context``. ``session_id``, ``workspace_id``, ``uploaded_files``, and
    ``metadata`` remain readable directly on ``BuddyState`` as thin,
    read-only pass-throughs to ``context`` — kept for every stage's
    convenience — but are no longer stored twice.

    Each cognitive stage that produces a strongly typed result has a
    dedicated, optional field for it: ``understanding`` (Understand),
    ``reasoning`` (Think), ``planning`` (Plan), ``delegation`` (Delegate),
    ``execution_result`` (Execute), and ``verification_result`` (Verify).
    Each is ``None``
    until its stage has actually run. The older, generic fields
    (``intent``, ``confidence``, ``reasoning_trace``, ``plan``, ``tasks``,
    ``active_task``, ``outputs``, ``reply``) remain in place unchanged —
    stages currently write to them directly, and are expected to adopt
    the corresponding typed field over time, the way Understand's
    ``intent``/``confidence`` already coexist with the richer
    ``Understanding`` model. ``metadata`` is not a substitute for any of
    these: it remains available only for information that is genuinely
    auxiliary to the cognitive pipeline, not as an overflow bucket for
    structured stage output that belongs in one of these dedicated
    fields.

    Attributes:
        context: The owned working context for this state's session and
            workspace. See ``buddy.context.Context``.
        user_message: The most recent raw message received from the user.
        observations: Raw observations gathered during the Observe stage.
        understanding: The structured understanding produced by the
            Understand stage, if it has run yet.
        intent: The user intent identified during the Understand stage, if
            one has been determined yet.
        confidence: Buddy's confidence in the current ``intent``, from 0.0
            (no confidence) to 1.0 (full confidence).
        reasoning: The structured reasoning result produced by the Think
            stage, if it has run yet.
        reasoning_trace: Ordered, plain-language trace of reasoning steps
            recorded during the Think stage.
        planning: The ordered sequence of ``PlanningStep`` records
            produced by the Plan stage, if it has run yet.
        plan: Ordered sequence of plain-language steps produced during the
            Plan stage.
        delegation: The structured sequence of ``CapabilityRequest``
            records produced by the Delegate stage, if it has run yet.
        tasks: Task records derived from ``plan`` and handed to skills
            during the Delegate stage.
        active_task: The full record of the task currently being executed,
            if any. Distinct from ``context.active_task``, which is only a
            lightweight identifier kept for conversational continuity
            across turns, not the task's execution details.
        execution_result: The structured result produced by the Execute
            stage, if it has run yet.
        outputs: Results produced by skills during the Execute stage.
        verification_result: The structured result produced by the Verify
            stage, if it has run yet.
        reply: The message Buddy will send back to the user, produced
            during the Speak stage.
        failure: Set by the Engine when a stage raised this turn. Voice
            then gives a fixed failure reply, and nothing from the turn
            counts as a verified delivery, whatever ``execution_result``
            and ``verification_result`` hold.
        turn_output_dir: The folder this turn's skills write their output
            files into, copied onto every ``SkillTask`` by Hands. ``None``
            keeps each skill's legacy path. A caller that runs several
            turns in one session sets a fresh folder per turn, so a later
            turn can't overwrite a file handed back earlier.
        earlier_deliveries: How many files the session had handed back
            before this turn, set by a caller that runs several turns in
            one session (the backend API). Every turn works from the
            original upload, so when this is above 0 a delivered reply says
            the copy was made from the original
            (``buddy.voice_catalog.FROM_ORIGINAL_SENTENCE``).
    """

    context: Context
    user_message: str = ""
    observations: list[str] = field(default_factory=list)
    understanding: Understanding | None = None
    intent: str | None = None
    confidence: float = 0.0
    reasoning: Reasoning | None = None
    reasoning_trace: list[str] = field(default_factory=list)
    planning: tuple[PlanningStep, ...] | None = None
    plan: list[str] = field(default_factory=list)
    delegation: tuple[CapabilityRequest, ...] | None = None
    tasks: list[dict[str, Any]] = field(default_factory=list)
    active_task: dict[str, Any] | None = None
    execution_result: ExecutionResult | None = None
    outputs: list[dict[str, Any]] = field(default_factory=list)
    verification_result: VerificationResult | None = None
    reply: str | None = None
    failure: StageFailure | None = None
    turn_output_dir: str | None = None
    earlier_deliveries: int = 0

    @classmethod
    def new(cls, session_id: str, workspace_id: str) -> "BuddyState":
        """Construct a fresh ``BuddyState`` with a new ``Context``.

        Convenience factory for the common case of starting a state from
        plain session and workspace identifiers, without having to
        construct a ``Context`` separately.

        Args:
            session_id: Identifier of the session this state belongs to.
            workspace_id: Identifier of the workspace this state is
                scoped to.

        Returns:
            A new ``BuddyState`` backed by a new, empty ``Context`` for
            the given session and workspace.
        """
        return cls(context=Context(active_workspace=workspace_id, current_session=session_id))

    @property
    def session_id(self) -> str:
        """Identifier of the session this state belongs to."""
        return self.context.current_session

    @property
    def workspace_id(self) -> str:
        """Identifier of the workspace this state is scoped to."""
        return self.context.active_workspace

    @property
    def uploaded_files(self) -> list[str]:
        """Files currently available in this state's working context."""
        return self.context.uploaded_files

    @property
    def metadata(self) -> dict[str, Any]:
        """Auxiliary information attached to this state's working context.

        Reserved for information that is genuinely auxiliary to the
        cognitive pipeline. Structured stage output has a dedicated field
        (``understanding``, ``reasoning``, ``planning``, ``delegation``,
        ``execution_result``, ``verification_result``) and should be
        stored there instead.
        """
        return self.context.metadata

    def add_observation(self, observation: str) -> None:
        """Append an observation gathered during the Observe stage.

        Args:
            observation: The observation to record.
        """
        self.observations.append(observation)

    def add_reasoning_step(self, step: str) -> None:
        """Append a step to the reasoning trace recorded during Think.

        Args:
            step: Description of the reasoning step to record.
        """
        self.reasoning_trace.append(step)

    def reset_transient(self) -> None:
        """Reset the state produced by a single work cycle.

        Clears everything derived from processing one user message —
        including both the legacy generic fields and each stage's
        dedicated typed result — while leaving ``context`` untouched, so
        the same ``BuddyState`` instance can be reused for the next cycle
        within the session without losing working context accumulated so
        far.
        """
        self.user_message = ""
        self.observations = []
        self.understanding = None
        self.intent = None
        self.confidence = 0.0
        self.reasoning = None
        self.reasoning_trace = []
        self.planning = None
        self.plan = []
        self.delegation = None
        self.tasks = []
        self.active_task = None
        self.execution_result = None
        self.outputs = []
        self.verification_result = None
        self.reply = None
        self.failure = None
        self.turn_output_dir = None
        self.earlier_deliveries = 0
