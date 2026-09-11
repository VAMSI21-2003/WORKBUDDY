"""Models — shared data models used throughout Buddy's cognitive pipeline."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Understanding:
    """A structured understanding of what the user wants.

    Produced by Understand from a single turn's observations, and read by
    Think when it reasons about what needs to happen next. ``Understanding``
    is deliberately implementation-agnostic: it says nothing about how the
    intent was inferred, only what was concluded.

    Attributes:
        intent: The inferred user intent, or ``None`` if it could not be
            determined.
        confidence: Confidence in ``intent``, from 0.0 (no confidence) to
            1.0 (full confidence).
        referenced_files: Files the request appears to refer to.
        referenced_work_items: Tasks or work items the request appears to
            refer to.
        missing_information: Plain-language descriptions of information
            the request is missing in order to proceed.
        ambiguity_flags: Plain-language descriptions of points in the
            request that are ambiguous.
        notes: Free-form annotation that doesn't fit any other field.
    """

    intent: str | None = None
    confidence: float = 0.0
    referenced_files: tuple[str, ...] = ()
    referenced_work_items: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    ambiguity_flags: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class PlanningStep:
    """A single step in a plan produced by Plan.

    Plan decomposes an ``Understanding`` into an ordered sequence of
    ``PlanningStep`` records, which Delegate then turns into
    ``CapabilityRequest`` records for the Dispatcher to route.

    Attributes:
        id: Identifier for this step, unique within its plan. Referenced
            by other steps' ``dependencies``.
        description: Plain-language description of what this step
            accomplishes.
        capability: The capability this step requires (for example, a
            file type or a named skill capability), used by Delegate and
            the Dispatcher to find a Skill able to perform it.
        priority: Ordering weight among steps with no dependency relation
            to each other. Lower values run first; ``0`` is the default.
        dependencies: IDs of other steps in the same plan that must
            complete before this one can start.
    """

    id: str
    description: str
    capability: str
    priority: int = 0
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityRequest:
    """A request for a specific capability to be applied to a target.

    Produced by Delegate from a ``PlanningStep`` and handed to the
    Dispatcher, which resolves it to a specific, capable Skill.

    Attributes:
        capability: The capability being requested.
        target: What the capability should be applied to — typically a
            reference to a file or work item.
        parameters: Additional parameters needed to carry out the
            request.
    """

    capability: str
    target: str
    parameters: dict[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of carrying out one or more capability requests.

    Produced by Execute after invoking the Skill(s) selected by the
    Dispatcher, and read by Verify to judge whether the work actually
    satisfies the original ``Understanding``.

    Attributes:
        success: Whether execution completed without a fatal error.
        outputs: References to files or data produced during execution,
            across every step, in no particular per-step grouping. Kept
            as the flat, source-of-truth view existing callers already
            depend on.
        warnings: Non-fatal issues encountered during execution.
        errors: Fatal issues that prevented execution from succeeding.
        duration: How long execution took, in seconds.
        steps: Per-step breakdown of what happened, one ``StepResult``
            per capability request that reached a Skill. This is what a
            capability-aware ``Verifier`` (see
            ``buddy.inspector.CompositeVerifier``) or a multi-step reply
            (see ``buddy.voice.TemplateSpeaker``) should iterate over —
            ``outputs`` alone cannot say which output belongs to which
            capability.
    """

    success: bool
    outputs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    duration: float = 0.0
    steps: tuple[StepResult, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of checking an execution result against user intent.

    Produced by Verify and read by Speak, which uses it to decide what to
    tell the user.

    Attributes:
        passed: Whether the result satisfies the original intent.
        confidence: Confidence in this verdict, from 0.0 (no confidence)
            to 1.0 (full confidence).
        findings: Plain-language observations made while verifying.
        recommendations: Plain-language suggestions for what to do next,
            particularly when ``passed`` is ``False``.
    """

    passed: bool
    confidence: float = 0.0
    findings: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
