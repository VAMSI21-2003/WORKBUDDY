"""Base — the abstract interface that every skill must implement."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionTier(Enum):
    """Authorization tier required before a skill may be invoked.

    Mirrors the permission model in ``docs/BUDDY_SKILLS.md``: read-only
    work requires no confirmation, work that writes within the workspace
    requires confirmation before overwriting an original, and work with an
    effect outside the workspace always requires explicit confirmation.
    """

    READ = "read"
    WRITE = "write"
    EXTERNAL_EFFECT = "external_effect"


class SkillStatus(Enum):
    """Outcome status reported by a skill after execution."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


@dataclass
class SkillTask:
    """The input contract handed to a skill for a single unit of work.

    Attributes:
        task_id: Unique identifier for this task.
        session_id: Identifier of the session this task belongs to.
        workspace_id: Identifier of the workspace this task is scoped to.
        description: Plain-language description of the outcome requested.
        input_references: Workspace-scoped references to the files or
            resources the skill is authorized to read.
        parameters: Task-specific parameters needed to perform the work.
        permission: The permission tier granted for this invocation.
        output_dir: The folder to write output files into. ``None`` keeps
            the skill's legacy path, ``workspace/outputs/{workspace_id}/``.
            Set per turn by Hands from ``BuddyState.turn_output_dir``, so
            two turns in one session never write to the same path.
    """

    task_id: str
    session_id: str
    workspace_id: str
    description: str
    input_references: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    permission: PermissionTier = PermissionTier.READ
    output_dir: str | None = None


@dataclass
class SkillResult:
    """The output contract a skill returns after attempting a task.

    Attributes:
        status: The outcome of the execution.
        outputs: References to files or data produced by the skill.
        summary: Plain-language description of what was done, suitable
            for Buddy's Speak stage to use without translation.
        warnings: Non-fatal issues encountered during execution.
        errors: Fatal issues that prevented the task from succeeding.
        metadata: Auxiliary execution information, such as timing.
        self_reported_findings: Plain-language findings this skill is
            reporting as-is, with no independent re-check expected —
            distinct from ``summary`` in that a Verifier may relay these
            directly into ``VerificationResult.findings`` (see
            ``buddy.inspector.SpreadsheetAnalysisVerifier`` and
            ``docs/design/proactive-analysis-design.md`` Section 5.2 for
            why some findings get this lighter treatment instead of full
            independent recomputation). A tuple, unlike this dataclass's
            other list-typed fields, to mirror the corresponding field on
            ``buddy.models.StepResult`` it gets copied onto.
    """

    status: SkillStatus
    outputs: list[str] = field(default_factory=list)
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    self_reported_findings: tuple[str, ...] = ()


class Skill(ABC):
    """The minimum contract every Buddy skill must satisfy.

    A skill is a self-contained specialist capable of performing one
    category of domain-specific work on Buddy's behalf (see
    ``docs/BUDDY_SKILLS.md``). Buddy orchestrates skills; it never
    performs their work directly, and a skill never reasons about user
    intent or communicates with the user directly. A skill receives a
    ``SkillTask`` and returns a ``SkillResult`` — it never reads from or
    writes to ``BuddyState`` directly.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The skill's unique, human-readable identifier."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """A plain-language description of what this skill does."""
        ...

    @property
    @abstractmethod
    def supported_inputs(self) -> tuple[str, ...]:
        """The input types (e.g. file extensions) this skill can handle."""
        ...

    @property
    @abstractmethod
    def permission_tier(self) -> PermissionTier:
        """The permission tier required to invoke this skill."""
        ...

    @abstractmethod
    def can_handle(self, task: SkillTask) -> bool:
        """Determine whether this skill is capable of performing a task.

        Args:
            task: The task under consideration.

        Returns:
            True if this skill can perform the task, False otherwise.
        """
        ...

    @abstractmethod
    def execute(self, task: SkillTask) -> SkillResult:
        """Perform the work described by a task.

        Args:
            task: The task to execute.

        Returns:
            The result of attempting the task.
        """
        ...

    @abstractmethod
    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        """Check that a result is well-formed and consistent with its task.

        This is the skill's own self-check (see ``docs/BUDDY_SKILLS.md``,
        Section 9) — it confirms the result is well-formed, not that it
        satisfies the user's original intent, which is judged
        independently by Buddy's Verify stage.

        Args:
            task: The task the result was produced for.
            result: The result to verify.

        Returns:
            True if the result is well-formed and consistent with the
            task, False otherwise.
        """
        ...
