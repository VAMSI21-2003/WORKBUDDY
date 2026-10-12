"""Models — shared data models used throughout Buddy's cognitive pipeline."""

from dataclasses import dataclass, field
from typing import Any

# ``RequestedAction.source`` values: where a ledger entry came from.
LEDGER_FROM_BRAIN = "brain"  # Brain's ``requested_actions``
LEDGER_FROM_INTENT = "intent"  # Brain's single-action intent, not in ``requested_actions``
LEDGER_FROM_MENTION = "mention"  # only the deterministic mention detector found it

# ``Understanding.file_to_use`` values: which file Brain reads the request to be about, when a copy
# Buddy handed back earlier is on offer. A closed vocabulary (``buddy.actions.FILE_VOCABULARY``).
FILE_LATEST = "latest"  # build on the latest copy handed back (the default)
FILE_ORIGINAL = "original"  # start over from the upload: "start over", "use my original file"

# ``FileUsed.kind`` values: which file the turn worked on.
FILE_KIND_ORIGINAL = "original"  # the person's upload, as uploaded
FILE_KIND_COPY = "copy"  # a copy Buddy handed back in an earlier turn


@dataclass(frozen=True)
class RequestedAction:
    """One action the user's message asks for: an entry in the requested-actions ledger.

    See ``docs/design/verified-outcome-design.md`` Section 3. The ledger is
    read only by Voice and the residual measurement; it never changes routing.

    Attributes:
        action: A name from ``buddy.actions.ACTION_VOCABULARY``, e.g.
            ``"remove_duplicate_rows"``, or ``"unsupported"``.
        source_phrase: The words asking for it. When ``phrase_found`` is
            true these are the user's own words, copied from the message in
            the user's casing; otherwise they're the model's and are never
            shown to the user.
        source: ``LEDGER_FROM_BRAIN``, ``LEDGER_FROM_INTENT`` or
            ``LEDGER_FROM_MENTION``.
        phrase_found: Whether ``source_phrase`` occurs in the user's message.
    """

    action: str
    source_phrase: str
    source: str = LEDGER_FROM_BRAIN
    phrase_found: bool = False


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
        file_to_use: Which file the request is about, when a copy handed back earlier is on
            offer: ``FILE_ORIGINAL`` only when Brain reads the person asking to start over or to
            use their original file; otherwise ``FILE_LATEST``. Never read from the words of the
            message by code: it is Brain's field, in a closed vocabulary. Anything else in
            Brain's reply becomes ``FILE_LATEST``.
        requested_actions: The ledger of actions the message asks for, in
            the user's order: Brain's ``requested_actions`` and, after
            ``buddy.brain.MentionCrossCheck``, what only the mention
            detector found. Read with ``buddy.actions.requested_ledger``,
            which also adds Brain's single-action intent. Doesn't affect
            routing: the planner follows ``intent``.
        answers_pending: Brain reads this message as only the answer to "which file?" about the
            earlier request that was waiting (``BuddyState.pending_request``), and everything
            else in this understanding (intent, requested actions) is about that earlier
            request. ``False`` when nothing was waiting, and when the message is a request of
            its own. Brain's field, never read from the words by code.
    """

    intent: str | None = None
    confidence: float = 0.0
    referenced_files: tuple[str, ...] = ()
    referenced_work_items: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    ambiguity_flags: tuple[str, ...] = ()
    notes: str = ""
    requested_actions: tuple[RequestedAction, ...] = ()
    file_to_use: str = FILE_LATEST
    answers_pending: bool = False


@dataclass(frozen=True)
class FileChoice:
    """The files a caller offers a turn to work on, set before the run (``BuddyState.file_choice``).

    A caller that runs several turns in one session (the backend API) says which upload the
    turn is about and which copy of it, if any, Buddy handed back last. Which of the two the
    turn works on is decided after Brain has read the request (``buddy.source.choose_file``).

    Attributes:
        original: The upload's path.
        copy: The path of the latest copy handed back from that upload, or ``None``.
        copy_name: The name the person sees for the copy (``copy`` is set).
        copy_unavailable: A copy was handed back from this upload, but it can't be read now, so
            ``copy`` is ``None`` and the turn works on the upload; the reply says so.
        pinned: The person picked this upload for the turn: it is used as itself.
        unclear: The message names a spreadsheet file that isn't one of the session's files, or
            as close to two of them (``buddy.source.match_file_name``), so which file is meant
            isn't settled and Buddy asks (``buddy.think.settle_file_question``).
    """

    original: str
    copy: str | None = None
    copy_name: str | None = None
    copy_unavailable: bool = False
    pinned: bool = False
    unclear: bool = False


@dataclass(frozen=True)
class FileUsed:
    """Which file a turn worked on, set by ``buddy.source.choose_file``.

    Attributes:
        path: The file the pipeline reads (``Context.active_file`` for this turn).
        kind: ``FILE_KIND_ORIGINAL`` or ``FILE_KIND_COPY``.
        copy_name: The copy's name as the person sees it (``FILE_KIND_COPY`` only).
        copy_skipped: ``FILE_KIND_ORIGINAL`` only: a copy had been handed back from this upload
            and the turn worked on the upload instead.
        start_over: Brain read the person asking to start over (``FILE_ORIGINAL``).
    """

    path: str
    kind: str
    copy_name: str | None = None
    copy_skipped: bool = False
    start_over: bool = False


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
        order_confirmed: ``False`` when this step belongs to a multi-step
            plan whose order couldn't be checked against the order the
            user stated, so the model's own order was used.
    """

    id: str
    description: str
    capability: str
    priority: int = 0
    dependencies: tuple[str, ...] = ()
    order_confirmed: bool = True


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

    Produced by Hands, one per ``CapabilityRequest`` it handled — including
    one that never reached a Skill: a request no Skill could be dispatched
    for is recorded with status ``"failure"`` and no skill, and one whose
    confirmation was declined with status ``DECLINED_STATUS``, each with a
    ``failure_reason``, so Inspector sees the step that stopped the chain.
    A step Hands never started because an earlier step in the chain failed
    gets status ``SKIPPED_STATUS`` and no skill, so the reply can say which
    steps didn't run. This is what lets a Verifier
    resolve *which* strategy applies to *which* output — something
    ``ExecutionResult.outputs`` (only the final file) cannot express on
    its own.

    Attributes:
        capability: The capability this step requested — the same string
            Delegate produced and the Dispatcher used to select a Skill.
        skill: The name of the Skill that actually ran this step, or
            ``""`` for a skipped step.
        status: The Skill's own reported outcome for this step, as the
            string value of ``skills.base.SkillStatus`` (e.g.
            ``"success"``, ``"failure"``), ``SKIPPED_STATUS`` for a step
            never started, or ``DECLINED_STATUS`` for a step whose
            confirmation was declined. Kept as a plain string rather than
            importing ``SkillStatus`` here so ``buddy/models.py`` stays
            free of a dependency on ``skills/`` — the same domain-agnostic
            boundary ``docs/BUDDY_SKILLS.md`` Section 2 asks Buddy's core
            to keep.
        outputs: References to files or data this step produced —
            including an intermediate file a later step went on to read.
        summary: Plain-language description of what this step did.
        self_reported_findings: Mirrors ``skills.base.SkillResult
            .self_reported_findings`` — copied across unchanged by
            ``Hands``, the same 1:1 field copy already applied to
            ``status``/``outputs``/``summary``. Findings a Verifier may
            relay as-is rather than independently recompute (see
            ``buddy.inspector.SpreadsheetAnalysisVerifier``).
        input_reference: The file this step actually read — the original
            upload for the first step of a chain, the previous step's
            output after that — or ``""`` if unknown. Lets a Verifier
            check a step against its own input, not just its output.
        permission_tier: The permission tier of the Skill that ran this
            step, as the string value of ``skills.base.PermissionTier``
            (``"read"``, ``"write"``, ``"external_effect"``), or ``""`` if
            unknown (e.g. a skipped step). Anything above ``"read"`` is an
            action step — one that changes a file.
        failure_reason: Why Hands itself failed this step, in plain words
            with no capability id — set when there is no Skill result for
            a Verifier to check (no Skill could run it, confirmation was
            declined) or when the result couldn't be used (its output
            couldn't feed the chain), or, for a chain step whose Skill
            reported failure, the Skill's own errors (a reason it failed,
            never a claim of success). ``""`` otherwise. Inspector relays it
            as the step's finding instead of running a Verifier.
        recheck: ``True`` for a read-only step Hands re-ran on the chain's
            final file after the chain completed (see
            ``Hands._recheck_final_file``). Its failure is a non-blocking
            finding: it never fails the turn.
    """

    capability: str
    skill: str
    status: str
    outputs: tuple[str, ...] = ()
    summary: str = ""
    self_reported_findings: tuple[str, ...] = ()
    input_reference: str = ""
    permission_tier: str = ""
    failure_reason: str = ""
    recheck: bool = False


# The ``StepResult.status`` of a step Hands never started because an
# earlier step in the same chain failed.
SKIPPED_STATUS = "skipped"

# The ``StepResult.status`` of a step whose confirmation was declined.
DECLINED_STATUS = "declined"


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of carrying out one or more capability requests.

    Produced by Execute after invoking the Skill(s) selected by the
    Dispatcher, and read by Verify to judge whether the work actually
    satisfies the original ``Understanding``.

    Attributes:
        success: Whether execution completed without a fatal error.
        outputs: The result handed back to the user: the final file of
            the chain (each step reads the previous step's output — see
            ``buddy.hands.Hands``), or ``()`` if the chain stopped on a
            failure or no step produced a file. Intermediate files are
            not listed here; they stay on each ``StepResult.outputs``.
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
        stopped_at: The capability of the chain step whose failure
            stopped the chain (later steps are ``SKIPPED_STATUS``), or
            ``None`` if the chain didn't stop. Lets the reply say which
            step failed without parsing error strings.
    """

    success: bool
    outputs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    duration: float = 0.0
    steps: tuple[StepResult, ...] = ()
    stopped_at: str | None = None


# ``StepMeasurement.target`` values: what a verifier counted.
DUPLICATE_ROWS_TARGET = "duplicate_rows"
EMPTY_ROWS_TARGET = "empty_rows"


@dataclass(frozen=True)
class StepMeasurement:
    """Counts a verifier measured itself, by reading files fresh.

    Never copied from a Skill: nothing here comes from ``StepResult
    .summary``, ``self_reported_findings`` or Skill metadata. A verifier
    records a measurement only when its check passed, so every count is
    one the check confirmed (see ``docs/design/verified-outcome-design.md``
    Section 2.1). The reply's counts come from here and nowhere else.

    Attributes:
        target: What was counted: ``DUPLICATE_ROWS_TARGET`` or
            ``EMPTY_ROWS_TARGET``.
        output_rows: Rows in the output, as read by the verifier.
        remaining: Target rows still in the output.
        input_rows: Rows in the step's input, as read by the verifier;
            ``None`` when there was no input to read (the final-file check).
        removed: Rows the step removed, as confirmed by the verifier:
            ``input_rows`` minus the rows in the output, and equal to what
            the capability was required to remove. ``None`` when there was
            no input to read, or when the verifier couldn't confirm the
            exact count (the reply then states no number).
        removed_blank: Of ``removed``, how many were repeated fully-empty
            rows. Only deduplication removes these (it keeps the first of
            identical blank rows); ``0`` for every other capability.
    """

    target: str
    output_rows: int
    remaining: int
    input_rows: int | None = None
    removed: int | None = None
    removed_blank: int = 0

    @property
    def removed_data(self) -> int | None:
        """``removed`` without the repeated blank rows, or ``None`` if unconfirmed."""
        return None if self.removed is None else self.removed - self.removed_blank


@dataclass(frozen=True)
class OriginalFacts:
    """What the Inspector read in the user's original file that the copy can't keep.

    Measured fresh with openpyxl by ``buddy.inspector.OriginalMeasurer``; never
    taken from a Skill. The cleaning skills write values only (see
    ``docs/design/values-only-copy.md``), so a formula in the original is, in
    the copy, its last saved value, or an empty cell when the file saved none.

    Attributes:
        formula_cells: Cells of the original that hold a formula, or ``None``
            when the original couldn't be read for this count (the reply then
            says it couldn't check, and states no number).
        formula_cells_without_value: Of those, the cells with no saved result,
            which are empty in the copy. ``0`` when ``formula_cells`` is ``None``.
    """

    formula_cells: int | None
    formula_cells_without_value: int = 0


@dataclass(frozen=True)
class StepVerification:
    """One step's own, unaggregated verification verdict.

    ``CompositeVerifier._aggregate_verdicts`` collapses every step's
    verdict into a single ``passed``/``confidence`` pair on
    ``VerificationResult`` — necessary for a simple overall answer, but it
    means no code can ask "did capability X's own step specifically
    pass?" without parsing ``[capability]``-prefixed finding strings.
    ``VerificationResult.steps`` exists to answer exactly that question,
    the same way ``StepResult`` was added to ``ExecutionResult`` to answer
    "which output belongs to which capability?" (see
    ``docs/design/hands-verifier-review.md``). Consumed by
    ``buddy.voice.TemplateSpeaker`` to decide whether a proactive-analysis
    finding should be presented as already handled by another step in the
    same turn — see ``docs/design/proactive-analysis-design.md`` Section
    5.5.

    Attributes:
        capability: The capability this verdict belongs to.
        passed: Whether this step, specifically, satisfied its own check.
        confidence: Confidence in this step's own verdict.
        findings: This step's own plain-language findings, unprefixed.
        recommendations: This step's own suggestions for what to do next.
        input_reference: The file the verified step read (its
            ``StepResult.input_reference``), or ``""`` if unknown or not a
            step (e.g. the final-file check). Lets a consumer tell which
            file a verdict is about when one capability ran more than once.
        recheck: Mirrors ``StepResult.recheck``: this verdict is for a
            re-check of the final file, and its failure didn't count
            against ``VerificationResult.passed``.
        read_only: The verified step ran at READ tier (e.g. proactive
            analysis of the original). Outside the chain, so its failure
            doesn't count against ``VerificationResult.passed`` either —
            unless every step this turn was read-only.
        measurements: What this step's verifier measured, copied from its
            ``VerificationResult.measurements``. One entry for a step; one
            per chained step's target for the final-file check. Empty when
            the check failed or its verifier measures nothing.
    """

    capability: str
    passed: bool
    confidence: float = 0.0
    findings: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    input_reference: str = ""
    recheck: bool = False
    read_only: bool = False
    measurements: tuple[StepMeasurement, ...] = ()


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
        steps: Per-step, unaggregated verdicts — one ``StepVerification``
            per step ``CompositeVerifier`` dispatched, alongside (not
            instead of) the aggregated ``passed``/``confidence`` above.
            Empty when verification didn't go through ``CompositeVerifier``
            at all (e.g. ``NullVerifier``, or a single non-composite
            ``Verifier`` used directly).
        measurements: Counts this verifier measured itself (see
            ``StepMeasurement``), set only when its check passed. A
            composite verdict leaves this empty and carries each step's
            measurements on ``steps`` instead.
        residuals: Counts measured on the delivered file, by a fresh read,
            for each requested action's target (``buddy.inspector
            .ResidualMeasurer``): ``output_rows`` and ``remaining`` only.
            They let the reply say what's still in the file for a requested
            action that wasn't done. Empty when nothing was delivered or the
            read failed; they never affect ``passed``.
        original_facts: What the Inspector read in the user's original file
            that a values-only copy can't keep (``OriginalFacts``), set by
            ``buddy.inspector.OriginalMeasurer`` on a turn that delivered a
            file. ``None`` when nothing was measured; it never affects ``passed``.
    """

    passed: bool
    confidence: float = 0.0
    findings: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    steps: tuple[StepVerification, ...] = ()
    measurements: tuple[StepMeasurement, ...] = ()
    residuals: tuple[StepMeasurement, ...] = ()
    original_facts: OriginalFacts | None = None
