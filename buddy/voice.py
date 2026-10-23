"""Voice — communicates results and status back to the user."""

import dataclasses
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from buddy.actions import INTENT_CAPABILITIES, asks_for_report, is_conversation
from buddy.context import ConversationTurn
from buddy.inspector import FINAL_OUTPUT_CAPABILITY, NO_ANALYSIS_FINDINGS, SKIPPED_FINDING
from buddy.llm import LLMProvider
from buddy.models import (
    DECLINED_STATUS,
    FILE_KIND_COPY,
    SKIPPED_STATUS,
    ExecutionResult,
    FileUsed,
    StepResult,
    Understanding,
    VerificationResult,
)
from buddy.outcome import (
    FILE_STYLE_GENERATED,
    FILE_STYLE_PLAIN,
    REPORT_NOT_RUN_SENTENCE,
    OutcomeReport,
    _describe_capability,
    _is_action_step,
    _step_passed,
    already_satisfied,
    build_outcome_report,
    not_done_sentences,
    render_outcome,
    report_unavailable_sentence,
)
from buddy.state import BuddyState, StageFailure
from buddy.think import CLARIFY_FAILED, CLARIFY_WHICH_FILE, Reasoning
from buddy.voice_catalog import (
    CAPABILITIES,
    NO_FILE_SENTENCE,
    NOT_UNDERSTOOD_SENTENCE,
    NOTHING_LEFT_TEMPLATE,
    REFUSED_TEMPLATE,
    REPORT_LEAD_COPY,
    REPORT_LEAD_ORIGINAL,
    REPORT_LEAD_USED,
    REPORT_NOT_PRODUCED_SENTENCE,
    RESUMED_SENTENCE,
    STYLE_PLAIN,
    UNDERSTANDING_FAILED_SENTENCE,
    WORKED_ON_COPY_TEMPLATE,
    WORKED_ON_ORIGINAL_SKIPPING_COPY_TEMPLATE,
    WORKED_ON_ORIGINAL_TEMPLATE,
    clarification_question,
    effective_mode,
    failure_reply,
    greeting_reply,
    select_phrases,
    style_for,
)

logger = logging.getLogger(__name__)

_ANALYZE_CAPABILITY = "spreadsheet.analyze"
_ANALYSIS_FINDING_PATTERN = re.compile(
    r"^\[" + re.escape(_ANALYZE_CAPABILITY) + r"\] \[(?P<category>\w+)\] (?P<text>.*)$"
)

# No spreadsheet.analyze sub-check has a capability that actually resolves
# it yet -- see docs/design/proactive-analysis-design.md Section 5.3. A
# finding's category is only ever folded into "already handled" when a
# real, matching capability is added here AND a step for it ran and passed
# in the same turn. Deliberately empty today: near-duplicate findings, for
# instance, are never resolved by spreadsheet.deduplicate (exact-match
# only) even when it ran and passed in the same turn.
_ANALYSIS_FINDING_FIX_CAPABILITY: dict[str, str] = {}

# Inspector prefixes each finding with its capability (and, for analysis
# findings, a category) -- "[spreadsheet.deduplicate] ..." or
# "[spreadsheet.analyze] [outliers] ...". Those tags are internal routing
# labels and must never reach the user, so they're stripped before a
# finding is rendered into a reply.
_FINDING_TAG_PREFIX = re.compile(r"^(?:\[[\w.]+\]\s*)+")

# A raw capability id anywhere in a text, e.g. the "spreadsheet.report" in
# "No registered skill declares support for 'spreadsheet.report'." Matches
# any id in a known capability domain, so an id with no registered skill is
# caught too — but not a file name such as "spreadsheet.xlsx".
_CAPABILITY_DOMAINS = sorted(
    {c.split(".", 1)[0] for c in (*INTENT_CAPABILITIES.values(), "spreadsheet.analyze")}
)
_CAPABILITY_ID = re.compile(
    r"\b(?:" + "|".join(map(re.escape, _CAPABILITY_DOMAINS)) + r")\.(?!(?:xlsx|xlsm|xls|csv)\b)"
    r"[a-z_]+\b"
)


def _plain_words(text: str) -> str:
    """Replace every raw capability id in ``text`` with plain words."""
    return _CAPABILITY_ID.sub(lambda match: _describe_capability(match.group(0)), text)


def _strip_finding_tags(finding: str) -> str:
    """Return ``finding`` without its leading ``[capability]``/``[category]``
    tags, and with any capability id inside it in plain words."""
    return _plain_words(_FINDING_TAG_PREFIX.sub("", finding))


def _is_analysis_finding(finding: str) -> bool:
    """Return whether ``finding`` came from the proactive ``spreadsheet.analyze`` step."""
    return finding.startswith(f"[{_ANALYZE_CAPABILITY}]")


def _find_step(execution_result: ExecutionResult | None, capability: str) -> StepResult | None:
    """Return the first step in ``execution_result.steps`` for ``capability``.

    Args:
        execution_result: The execution result to search, if any.
        capability: The capability to look for.

    Returns:
        The matching ``StepResult``, or ``None`` if there isn't one.
    """
    if execution_result is None:
        return None
    for step in execution_result.steps:
        if step.capability == capability:
            return step
    return None


NO_CHANGES_SENTENCE = "No changes were made to any file this turn."

# Wording that claims work was carried out. No reply for a turn that
# delivered nothing may contain any of it, and no phrase-catalog entry may
# either (``buddy.voice_catalog``): on a delivered turn the outcome block's
# lead word is the only "Done". Tests check replies against it; it is a
# lint, not the safeguard. Deliberately broad.
_COMPLETION_CLAIM = re.compile(
    r"\b(done|complet(e|ed|ely|ion)|finish(ed)?|succeed(ed)?|success(ful|fully)?|"
    r"removed|deleted|clean(ed)?|cleared|dedup(ed|licated)?|saved|fixed|ready|"
    r"handled|taken care)\b",
    re.IGNORECASE,
)


def _action_passed_verification(
    execution_result: ExecutionResult | None, verification_result: VerificationResult | None
) -> bool:
    """Whether any action step passed this turn, by ``_step_passed``'s rule."""
    if execution_result is None or verification_result is None:
        return False
    if not execution_result.steps:
        # A result with no per-step breakdown: judge it by its output alone.
        return bool(execution_result.outputs) and verification_result.passed
    return any(
        _is_action_step(step) and _step_passed(step, verification_result)
        for step in execution_result.steps
    )


def _no_changes_made(
    execution_result: ExecutionResult | None, verification_result: VerificationResult | None
) -> bool:
    """Whether to tell the user no file was changed this turn.

    True when no action step passed verification — e.g. Think asked for
    clarification, so only read-only analysis ran. When Inspector didn't
    run at all but an action step did, this is ``False``: the file may have
    changed, just unverified, and the reply says that instead.
    """
    if verification_result is None:
        steps = execution_result.steps if execution_result is not None else ()
        return not any(_is_action_step(s) and s.status == "success" for s in steps) and not (
            execution_result is not None and execution_result.outputs
        )
    return not _action_passed_verification(execution_result, verification_result)


def _nothing_left(request: "SpeakingRequest") -> bool:
    """Whether the copy this turn worked on already satisfied the request.

    The turn worked on a copy Buddy handed back earlier, and every file-changing step passed its
    check with the verifier's own count of 0 rows to remove (``buddy.outcome.already_satisfied``).
    No new file is handed back then: it would be the same as the file the person already has.
    Never judged from the user's words.
    """
    used = request.file_used
    return (
        request.failure is None
        and used is not None
        and used.kind == FILE_KIND_COPY
        and bool(already_satisfied(request.execution_result, request.verification_result))
    )


def _nothing_delivered(request: "SpeakingRequest") -> bool:
    """Whether this turn handed the user no verified result at all."""
    execution_result = request.execution_result
    return (
        _nothing_left(request)
        or _no_changes_made(execution_result, request.verification_result)
        or execution_result is None
        or not execution_result.outputs
        or execution_result.stopped_at is not None
    )


def _delivered_file_name(request: "SpeakingRequest") -> str | None:
    """The name of the user's original file, if a changed copy was handed back.

    ``PersonalityVoice`` never shows the generated output name; its reply
    refers to the file as a cleaned copy of the original.
    """
    if _nothing_delivered(request):
        return None
    assert request.execution_result is not None
    used = request.file_used
    if used is not None and used.kind == FILE_KIND_COPY and _name_in_reply(used.copy_name):
        # The name the person sees for the copy the turn worked on, the same one the reply's
        # "I worked on ..." sentence gives (not the turn's private working name for it).
        return _name_in_reply(used.copy_name)
    for step in request.execution_result.steps:
        if _is_action_step(step) and step.input_reference:
            return Path(step.input_reference).name
    return Path(request.execution_result.outputs[0]).name


def _findings_for_reply(verification_result: VerificationResult) -> tuple[str, ...]:
    """The findings a reply should report, with analysis of the right file.

    When analysis was re-run on the final file after the chain changed it
    (a ``recheck`` verdict), the original's analysis findings no longer
    describe the file the user receives, so they're dropped. The re-check's
    own findings replace them if it passed; if it failed there's nothing
    current to report, and the reply says the file couldn't be re-checked
    instead (``_recheck_failed``).
    """
    rechecks = [
        s
        for s in verification_result.steps
        if s.capability == _ANALYZE_CAPABILITY and s.recheck
    ]
    if not rechecks:
        return verification_result.findings
    others = tuple(f for f in verification_result.findings if not _is_analysis_finding(f))
    return others + tuple(
        f"[{_ANALYZE_CAPABILITY}] {f}" for s in rechecks if s.passed for f in s.findings
    )


def _analysis_describes_final_file(verification_result: VerificationResult | None) -> bool:
    if verification_result is None:
        return False
    return any(
        s.capability == _ANALYZE_CAPABILITY and s.recheck and s.passed
        for s in verification_result.steps
    )


NO_CHANGES_FINDINGS_LEAD = "Here's what I noticed when I looked it over: "
RECHECK_FAILED_SENTENCE = (
    "I couldn't re-check the final file for anything else worth flagging after the changes."
)


def _no_file_changing_step_ran(execution_result: ExecutionResult | None) -> bool:
    """Whether the turn ran no file-changing step, and nothing in it went wrong.

    True for no execution at all, for read-only steps only (the analysis), and for an empty
    plan. False as soon as a file-changing step was tried or an error was recorded: those
    turns have their own replies.
    """
    if execution_result is None:
        return True
    if execution_result.errors or execution_result.stopped_at is not None:
        return False
    if execution_result.outputs:
        # A file came back: even with no per-step breakdown, something may have changed one.
        return False
    return not any(_is_action_step(step) for step in execution_result.steps)


def _analysis_findings(
    request: "SpeakingRequest",
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """The original file's analysis as ``(open findings, all-clear notes)``, or ``None``.

    ``None`` unless the analysis ran this turn and passed, so a reply never reports findings
    of an analysis that failed or didn't run.
    """
    verification_result = request.verification_result
    analysis_ran = _find_step(request.execution_result, _ANALYZE_CAPABILITY) is not None
    if verification_result is None or not analysis_ran:
        return None
    if not any(
        v.capability == _ANALYZE_CAPABILITY and v.passed and not v.recheck
        for v in verification_result.steps
    ):
        return None
    open_findings, other_findings = TemplateSpeaker._partition_analysis_findings(
        verification_result
    )
    notes = tuple(_strip_finding_tags(f) for f in other_findings if _is_analysis_finding(f))
    return open_findings, notes


def _verified_delivery(request: "SpeakingRequest") -> bool:
    """Whether this turn delivered a verified file.

    Something was handed back and Inspector ran and passed the turn. Only
    such a turn gets the rendered outcome (``_verified_delivery_reply``),
    and, from ``PersonalityVoice``, catalog phrases around it. Every other
    turn keeps ``TemplateSpeaker``'s factual reply.

    This deliberately doesn't depend on the classified intent. Brain has
    no conversational intent, so a turn classified ``unclear`` or with no
    intent (an LLM failure, a non-JSON reply) may well be an action
    request that nothing carried out.

    A turn in which a stage raised (``request.failure``) is never a
    verified delivery, whatever its results hold: Inspector may have
    raised after Hands produced a real file.
    """
    verification_result = request.verification_result
    return (
        request.failure is None
        and not _nothing_delivered(request)
        and verification_result is not None
        and verification_result.passed
    )


INITIAL_ANALYSIS_FAILED_SENTENCE = "The initial analysis of your original file couldn't run."
# The same, for a turn that worked on a copy handed back earlier: the analysis was of that copy.
INITIAL_ANALYSIS_FAILED_COPY_SENTENCE = (
    "The initial analysis of the file I worked on couldn't run."
)


def _initial_analysis_failed_sentence(request: "SpeakingRequest") -> str:
    """Which file the failed initial analysis was of: the upload, or the copy the turn used."""
    used = request.file_used
    if used is not None and used.kind == FILE_KIND_COPY:
        return INITIAL_ANALYSIS_FAILED_COPY_SENTENCE
    return INITIAL_ANALYSIS_FAILED_SENTENCE


def _order_used_sentence(request: "SpeakingRequest") -> str | None:
    """Say the order the steps ran in, when it couldn't be checked.

    Only when the planner couldn't confirm the order the user stated and
    two or more file-changing steps ran: the user can then see whether it
    matches what they meant.
    """
    if request.order_confirmed or request.execution_result is None:
        return None
    ran = [
        _describe_capability(step.capability)
        for step in request.execution_result.steps
        if _is_action_step(step) and step.status != DECLINED_STATUS
    ]
    if len(ran) < 2:
        return None
    return (
        "I couldn't tell for sure from your message what order you wanted, so the steps ran "
        "in this order: " + ", then ".join(ran) + "."
    )


def _initial_analysis_failed(verification_result: VerificationResult | None) -> bool:
    """Whether a read-only step on the original (not a re-check) failed.

    Non-blocking, like a failed re-check: it never fails the turn, but the
    reply says so in plain words.
    """
    if verification_result is None:
        return False
    return any(
        s.read_only and not s.recheck and not s.passed for s in verification_result.steps
    )


def _recheck_failed(verification_result: VerificationResult | None) -> bool:
    """Whether a re-check of the delivered file failed (non-blocking)."""
    if verification_result is None:
        return False
    return any(s.recheck and not s.passed for s in verification_result.steps)


def _declined_before_any_change(execution_result: ExecutionResult) -> bool:
    """Whether the first file-changing step was declined, so no step changed a file.

    The user said no to the plan (or didn't answer it): nothing went wrong,
    so the reply doesn't say "I ran into a problem"; the step's own reason
    ("wasn't confirmed, so it didn't run") says what happened. A step
    declined after an earlier one ran is still a problem.
    """
    for step in execution_result.steps:
        if _is_action_step(step):
            return step.status == DECLINED_STATUS
    return False


def _an_action_step_succeeded(execution_result: ExecutionResult) -> bool:
    """Whether a file-changing step other than the one that stopped the chain succeeded.

    "Partway through" is only true if one did. A chain that stopped at its first
    step (even a step whose skill succeeded but failed verification), or that never
    started because a later step can't run, got nowhere.
    """
    return any(
        _is_action_step(step)
        and step.status == "success"
        and step.capability != execution_result.stopped_at
        for step in execution_result.steps
    )


STOPPED_BEFORE_ANY_CHANGE = "I ran into a problem before I could change anything."


def _chain_stop_sentence(execution_result: ExecutionResult | None) -> str:
    """Say, in plain words, which step stopped the chain and what didn't run.

    Returns:
        ``""`` if the chain didn't stop; otherwise a sentence naming the
        failed step (never its raw capability id) and every skipped step.
    """
    if execution_result is None or execution_result.stopped_at is None:
        return ""
    skipped = [
        _describe_capability(step.capability)
        for step in execution_result.steps
        if step.status == SKIPPED_STATUS
    ]
    sentence = f"I stopped at the {_describe_capability(execution_result.stopped_at)} step"
    if skipped:
        sentence += f", so {', '.join(skipped)} never ran"
    return sentence + " and no file was handed back; your original file is unchanged."


@dataclass(frozen=True)
class SpeakingRequest:
    """The inputs available for composing Buddy's reply to the user.

    A plain, self-contained bundle of everything a ``Speaker`` needs to
    work from — assembled mechanically from ``BuddyState`` by the Voice
    stage, so a speaker never has to depend on ``BuddyState`` directly.

    Attributes:
        understanding: The structured understanding produced by
            Brain.
        execution_result: The structured result produced by Hands, if
            it has run yet.
        verification_result: The structured result produced by Inspector,
            if it has run yet.
        mode: Buddy's conversational personality mode for this session —
            ``"friend"`` or ``"focus"`` — sourced from
            ``Context.mode``. ``NullSpeaker``/``TemplateSpeaker`` ignore
            it; ``PersonalityVoice`` uses it to select catalog phrases.
        order_confirmed: ``False`` when the plan's order couldn't be
            checked against the order the user stated (see
            ``PlanningStep.order_confirmed``).
        session_id: The session this turn belongs to.
        turn_index: How many replies Buddy had already given in this
            session. With ``session_id``, it seeds the deterministic
            choice of catalog phrases (``buddy.voice_catalog``).
        failure: The stage that raised this turn, if one did. The reply
            is then ``buddy.voice_catalog.failure_reply``, nothing else.
        file_used: ``BuddyState.file_used``: which file the turn worked on (the person's
            upload, or a copy handed back earlier). A delivered reply says which in one fixed
            sentence; ``None`` for a caller that doesn't say, which then gets no such sentence.
        user_message: The user's own message. Only deterministic detectors read
            it (a greeting, a report request); it is never put in a reply.
        reasoning: Think's result: whether it asked for clarification and why
            (a closed code; Think's own words are never shown).
        has_file: Whether the session has a file to work on. Defaults to ``True``: a
            caller that doesn't say never makes the reply claim there is none.
        file_refusal: The row-cleaning skills' own refusal text for this turn's
            file, when the Planner found one (``BuddyState.file_refusal``).
        recent_conversation: The session's recent conversation, oldest first (this turn's
            message last). Only the reply writer reads it (``buddy.writer``), as quoted text.
        report_requested: Brain's reading of the message includes a report on the file
            (``buddy.actions.asks_for_report``). Nothing in the message decides it. The reply
            then gives the report or says why it can't (``buddy.outcome``).
    """

    understanding: Understanding
    execution_result: ExecutionResult | None
    verification_result: VerificationResult | None
    mode: str
    order_confirmed: bool = True
    session_id: str = ""
    turn_index: int = 0
    failure: StageFailure | None = None
    file_used: FileUsed | None = None
    user_message: str = ""
    reasoning: Reasoning | None = None
    has_file: bool = True
    file_refusal: str | None = None
    report_requested: bool = False
    resumed: bool = False
    recent_conversation: tuple[ConversationTurn, ...] = ()


# Leads the analysis findings on a delivered turn, after the outcome.
DELIVERED_ANALYSIS_LEAD = "I also noticed: "
ANALYSIS_QUESTION = "Want me to tackle any of these?"
# The analyzer's all-clear, when a residual count says the file still has something.
_OTHER_ISSUES_NOTE = "none of the other issues I check for were found"


def delivery_parts(
    request: SpeakingRequest,
) -> tuple[OutcomeReport, tuple[str, ...], list[str]]:
    """The facts of a verified delivery: the outcome report, the open analysis findings and the
    analysis notes (the all-clear), exactly as the rendered reply states them.

    Shared by ``_verified_delivery_reply`` and the writer's fact sheet (``buddy.facts``), so the
    two can never disagree about what a delivered turn did.
    """
    execution_result = request.execution_result
    verification_result = request.verification_result
    assert verification_result is not None
    open_findings: tuple[str, ...] = ()
    analysis_notes: list[str] = []
    if _find_step(execution_result, _ANALYZE_CAPABILITY) is not None:
        open_findings, other_findings = TemplateSpeaker._partition_analysis_findings(
            verification_result
        )
        # An analysis finding with no category (e.g. "none of the issues I
        # check for were found") is a note, not a question. A failed
        # analysis is said by INITIAL_ANALYSIS_FAILED_SENTENCE instead. Every
        # other finding is a step verifier's own wording, replaced by the
        # measured counts.
        failed = {
            finding
            for verdict in verification_result.steps
            if verdict.capability == _ANALYZE_CAPABILITY and not verdict.passed
            for finding in verdict.findings
        }
        analysis_notes = [
            _strip_finding_tags(f)
            for f in other_findings
            if _is_analysis_finding(f) and _FINDING_TAG_PREFIX.sub("", f) not in failed
        ]
    report = build_outcome_report(
        execution_result,
        verification_result,
        request.understanding,
        order_used=_order_used_sentence(request),
        delivered_file=_delivered_file_name(request),
        open_findings=len(open_findings),
    )
    if any(m.remaining for m in report.residuals):
        # "It still has 2 duplicate row(s)." can't be followed by an
        # unqualified all-clear: the analysis doesn't check for that.
        analysis_notes = [
            _OTHER_ISSUES_NOTE if note == NO_ANALYSIS_FINDINGS else note
            for note in analysis_notes
        ]
    return report, open_findings, analysis_notes


def _verified_delivery_reply(
    request: SpeakingRequest, *, file_style: str, mode: str | None = None
) -> str:
    """The reply for a turn that delivered a verified file, rendered by code.

    In order (design decision D3): the opener, the outcome block, the
    analysis findings, the closer. The outcome block comes from
    ``buddy.outcome`` (execution and verifier data, checked against the
    requested-actions ledger on ``request.understanding``); the opener and
    closer from ``buddy.voice_catalog``, chosen by code, and only when a
    ``mode`` is given. No model writes any of it.

    Args:
        request: The turn; ``_verified_delivery(request)`` must be true.
        file_style: How the outcome block names the file.
        mode: The personality mode for the catalog phrases, or ``None`` for
            none (``TemplateSpeaker``).

    Returns:
        The reply text.
    """
    verification_result = request.verification_result
    assert verification_result is not None
    report, open_findings, analysis_notes = delivery_parts(request)
    opener = closer = ""
    if mode is not None:
        opener_phrase, closer_phrase = select_phrases(
            report, mode, seed=f"{request.session_id}:{request.turn_index}"
        )
        opener, closer = opener_phrase.text, closer_phrase.text

    parts = [opener, render_outcome(report, file_style)]
    parts.append(_file_used_sentence(request.file_used))
    if request.resumed:
        parts.append(RESUMED_SENTENCE)
    if report.report_asked:
        # A requested report is the analysis findings on the final file, labelled as the
        # report. When it can't be given, the outcome block already says why (a not-done
        # action), so it is never left out and never silent.
        if report.report_given:
            parts.append(_report_text(open_findings, analysis_notes, REPORT_LEAD_COPY))
    else:
        if open_findings:
            parts.append(
                DELIVERED_ANALYSIS_LEAD + "; ".join(open_findings) + ". " + ANALYSIS_QUESTION
            )
        if analysis_notes:
            joined = "; ".join(analysis_notes)
            parts.append(joined[0].upper() + joined[1:] + ".")
    if not report.report_asked or report.report_given:
        if _initial_analysis_failed(verification_result):
            parts.append(_initial_analysis_failed_sentence(request))
        if _recheck_failed(verification_result):
            parts.append(RECHECK_FAILED_SENTENCE)
    parts.append(closer)
    return _plain_words(" ".join(part for part in parts if part))


def _name_in_reply(name: str | None) -> str:
    """A file name safe to put between backticks in a reply: one line, no backticks, quotes or
    control characters, at most 120 characters."""
    cleaned = " ".join(re.sub(r"[`\"“”\x00-\x1f\x7f]", " ", name or "").split())
    return cleaned[:119].rstrip() + "…" if len(cleaned) > 120 else cleaned


def _file_label(name: str | None, fallback: str = "the copy") -> str:
    """A file's name between backticks, or ``fallback`` if it has none to show."""
    cleaned = _name_in_reply(name)
    return f"`{cleaned}`" if cleaned else fallback


def _file_used_sentence(used: FileUsed | None) -> str:
    """Which file the turn worked on, in one fixed sentence chosen by code; ``""`` if unknown.

    The sentence follows ``FileUsed`` and nothing else, and names the file: the person's upload,
    the upload when a copy had been handed back and wasn't used, or the latest copy. It claims
    nothing about what the work did.
    """
    if used is None:
        return ""
    if used.kind == FILE_KIND_COPY:
        return WORKED_ON_COPY_TEMPLATE.format(file=_file_label(used.copy_name))
    template = (
        WORKED_ON_ORIGINAL_SKIPPING_COPY_TEMPLATE
        if used.copy_skipped
        else WORKED_ON_ORIGINAL_TEMPLATE
    )
    return template.format(file=_file_label(Path(used.path).name, fallback="your file"))


def _report_text(open_findings: Sequence[str], notes: Sequence[str], lead: str) -> str:
    """A delivered report: the analysis findings (or its all-clear), labelled.

    No "want me to tackle these?": the user asked for a report, and Buddy can't act on most
    findings.

    Args:
        open_findings: The analysis findings Buddy could act on or ask about.
        notes: Findings with no category (the analyzer's all-clear).
        lead: ``REPORT_LEAD_COPY``, ``REPORT_LEAD_ORIGINAL`` or ``REPORT_LEAD_USED``: which file
            they describe.
    """
    return lead + "; ".join((*open_findings, *notes)) + "."


class Speaker(Protocol):
    """Produces the reply text from a ``SpeakingRequest``.

    This is the seam where real reply composition is plugged in — a
    templated speaker, or one backed by an LLM. The Voice stage itself
    never composes a reply; it only calls whichever ``Speaker`` it was
    given.
    """

    def __call__(self, request: SpeakingRequest) -> str | Sequence[str]:
        """Compose a reply for ``request``: one text, or the separate messages it is sent as."""
        ...


class NullSpeaker:
    """A ``Speaker`` that never actually composes a reply.

    Honestly reports that no reply could be produced, rather than
    guessing. Serves as a safe default so ``Voice`` can be constructed and
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
    ``verification_result`` — in particular, from Inspector's own
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
        return self.compose(request, style=STYLE_PLAIN)

    def compose(self, request: SpeakingRequest, *, style: str) -> str:
        """Compose the factual reply for a turn that delivered no verified file.

        ``style`` (a ``buddy.voice_catalog`` style) only picks the wording of the greeting and
        of Think's clarification question; every fact in a reply is the same in every style.
        A verified delivery is rendered by ``_verified_delivery_reply`` instead.

        Args:
            request: The request to compose a reply for.
            style: ``STYLE_PLAIN``, ``"friend"`` or ``"focus"``.

        Returns:
            The reply text.
        """
        # A stage raised: a fixed reply that claims nothing, whatever the
        # results hold. Voice handles this before calling any speaker; this
        # covers a speaker called directly.
        if request.failure is not None:
            return failure_reply(request.failure.stage)
        execution_result = request.execution_result
        verification_result = request.verification_result
        # A verified delivery: the outcome is rendered from execution and
        # verifier data, with the generated file name and no phrases.
        if _verified_delivery(request):
            return _verified_delivery_reply(request, file_style=FILE_STYLE_GENERATED)
        # Routed on "was anything delivered?" first: when the chain stopped
        # or no verified file came back, no later branch may run — they
        # can say "Done" because an earlier step passed.
        if _nothing_delivered(request):
            if _nothing_left(request):
                return self._nothing_left_reply(request)
            if execution_result is not None and execution_result.stopped_at is not None:
                reply = self._stopped_message(
                    execution_result, verification_result, request.report_requested
                )
            elif _no_file_changing_step_ran(execution_result):
                # No file-changing step ran, and nothing went wrong: the reply says why
                # (a greeting, a question, no file, a file that can't be cleaned, a request
                # that isn't something Buddy does). No order sentence: no steps ran.
                return self._no_change_reply(request, style)
            elif verification_result is None:
                reply = self._nothing_confirmed_message(execution_result)
            else:
                reply = self._no_changes_message(
                    execution_result,
                    verification_result,
                    report_unavailable_sentence(
                        build_outcome_report(
                            execution_result, verification_result, request.understanding
                        )
                    )
                    if request.report_requested
                    else None,
                )
        elif verification_result is None:
            reply = "I made the changes, but wasn't able to confirm the result."
        else:
            # Delivered, but Inspector didn't pass it (a verified delivery
            # returned above).
            if _find_step(execution_result, _ANALYZE_CAPABILITY) is not None:
                reply = self._compose_with_analysis_preface(verification_result)
            else:
                reply = self._failure_message(verification_result)
            if _initial_analysis_failed(verification_result):
                reply = f"{reply} {_initial_analysis_failed_sentence(request)}"
            if _recheck_failed(verification_result):
                reply = f"{reply} {RECHECK_FAILED_SENTENCE}"
        order_sentence = _order_used_sentence(request)
        if order_sentence is not None:
            reply = f"{reply} {order_sentence}"
        return _plain_words(reply)

    @staticmethod
    def _nothing_left_reply(request: SpeakingRequest) -> str:
        """The reply when the latest copy already satisfies the request: no new file.

        Fixed text from the verifier's own count (``buddy.outcome.already_satisfied``) and the
        copy's name. A requested report is the analysis of that same file, or why it can't be
        given; anything else the message asked for that Buddy didn't run is stated as in any
        other reply. No count, and nothing about the file's contents, is put in it.
        """
        used = request.file_used
        assert used is not None
        actions = already_satisfied(request.execution_result, request.verification_result)
        rows = " or ".join(dict.fromkeys(action.rows for action in actions))
        parts = [NOTHING_LEFT_TEMPLATE.format(file=_file_label(used.copy_name), rows=rows)]
        outcome = build_outcome_report(
            request.execution_result, request.verification_result, request.understanding
        )
        parts.extend(not_done_sentences(outcome, include_report=False))
        if outcome.report_asked:
            findings = _analysis_findings(request)
            if outcome.report_given and findings is not None:
                parts.append(_report_text(findings[0], findings[1], REPORT_LEAD_USED))
            else:
                parts.append(report_unavailable_sentence(outcome) or REPORT_NOT_RUN_SENTENCE)
        if request.resumed:
            parts.append(RESUMED_SENTENCE)
        parts.append(NO_CHANGES_SENTENCE)
        return _plain_words(" ".join(parts))

    @staticmethod
    def _no_change_reply(request: SpeakingRequest, style: str) -> str:
        """The reply for a turn in which no file-changing step ran: the real reason, in fixed text.

        In this order, the first that holds decides the main sentence:

        1. Brain read the message as conversation and nothing in it contradicts that
           (``buddy.actions.is_conversation``): a greeting, the capabilities, what to do next.
        2. There is no file: say so.
        3. The Planner found the file can't be cleaned: the skills' own refusal text.
        4. Think asked for clarification: the fixed question for its reason code, or, when Think
           itself failed, that plainly.
        5. Brain gave no reading at all (its call failed): say so.
        6. Otherwise the requested actions that didn't run, each by the outcome renderer's own
           sentence for its reason, and what Buddy can do; or, when nothing was asked that
           Buddy knows, that the message wasn't read as something it can do.

        Then a requested report (delivered or not available), the analysis findings if the
        analysis ran and no report was asked for, and, unless the reply is a greeting or a
        question, that no file was changed. Nothing here claims work.
        """
        seed = f"{request.session_id}:{request.turn_index}"
        understanding = request.understanding
        if is_conversation(understanding, request.user_message):
            return greeting_reply(style, has_file=request.has_file, seed=seed)
        if not request.has_file:
            return f"{NO_FILE_SENTENCE} {NO_CHANGES_SENTENCE}"
        if request.file_refusal:
            reason = REFUSED_TEMPLATE.format(reason=request.file_refusal)
            return f"{reason} {NO_CHANGES_SENTENCE}"
        reasoning = request.reasoning
        outcome = build_outcome_report(
            request.execution_result, request.verification_result, understanding
        )
        parts: list[str] = []
        asked = False
        if reasoning is not None and reasoning.clarification_required:
            if reasoning.clarification_reason == CLARIFY_FAILED:
                parts.append(UNDERSTANDING_FAILED_SENTENCE)
            else:
                parts.append(clarification_question(reasoning.clarification_reason, style, seed))
                asked = True
        elif understanding.intent is None:
            parts.append(UNDERSTANDING_FAILED_SENTENCE)
        else:
            stated = not_done_sentences(outcome, nothing_done=True, include_report=False)
            parts.extend(stated)
            if stated or not outcome.report_asked:
                if not stated:
                    parts.append(NOT_UNDERSTOOD_SENTENCE)
                parts.append(CAPABILITIES[style])
        if outcome.report_asked:
            # Brain's reading asked for a report: the findings of the analysis of the original,
            # labelled as the report, or why there isn't one. Never silent, never a "can't do".
            findings = _analysis_findings(request)
            unavailable = report_unavailable_sentence(outcome)
            if outcome.report_given and findings is not None:
                on_copy = request.file_used is not None and request.file_used.kind == FILE_KIND_COPY
                lead = REPORT_LEAD_USED if on_copy else REPORT_LEAD_ORIGINAL
                parts.append(_report_text(findings[0], findings[1], lead))
                if request.resumed:
                    # The one no-change reply in which the earlier request was carried out. On
                    # every other (a question, a read that failed, a request Buddy can't do)
                    # nothing went on, so the sentence would be false (review round 2, F5).
                    parts.append(RESUMED_SENTENCE)
            else:
                parts.append(unavailable or REPORT_NOT_RUN_SENTENCE)
        else:
            findings = _analysis_findings(request)
            if findings is not None and (findings[0] or findings[1]):
                open_findings, notes = findings
                texts = "; ".join((*open_findings, *notes))
                parts.append(f"{NO_CHANGES_FINDINGS_LEAD}{texts}.")
                if open_findings:
                    parts.append(ANALYSIS_QUESTION)
        if _initial_analysis_failed(request.verification_result) and not outcome.report_asked:
            parts.append(_initial_analysis_failed_sentence(request))
        if not asked:
            parts.append(NO_CHANGES_SENTENCE)
        return _plain_words(" ".join(parts))

    @staticmethod
    def _stopped_message(
        execution_result: ExecutionResult,
        verification_result: VerificationResult | None,
        report_requested: bool = False,
    ) -> str:
        """Compose the reply for a chain that stopped: nothing was delivered.

        Never says "Done" or reports an earlier step's success as if it
        counted: the file that step produced was never handed back. Says
        what was noticed on the (unchanged) original, why the work stopped,
        and which step stopped it.
        """
        parts = []
        reasons: list[str] = []
        if verification_result is not None:
            open_findings, _ = TemplateSpeaker._partition_analysis_findings(verification_result)
            if open_findings:
                parts.append(
                    "Before anything else, here's what I noticed: "
                    + "; ".join(open_findings)
                    + ". Want me to tackle any of these?"
                )
            for verdict in verification_result.steps:
                findings = [f for f in verdict.findings if f != SKIPPED_FINDING]
                if (
                    verdict.passed
                    or verdict.capability in (_ANALYZE_CAPABILITY, FINAL_OUTPUT_CAPABILITY)
                    or not findings
                ):
                    continue
                reasons.append(
                    f"The {_describe_capability(verdict.capability)} step: "
                    + "; ".join(_strip_finding_tags(f) for f in findings)
                )
        if not reasons:
            reasons = [
                _plain_words(error).rstrip(".")
                for error in execution_result.errors
                if not error.startswith("stopped:")
            ]
        sentence = []
        if not _declined_before_any_change(execution_result):
            sentence.append(
                STOPPED_BEFORE_ANY_CHANGE
                if not _an_action_step_succeeded(execution_result)
                else "I ran into a problem partway through."
            )
        if reasons:
            sentence.append(". ".join(reasons) + ".")
        if sentence:
            parts.append(" ".join(sentence))
        if report_requested:
            parts.append(REPORT_NOT_PRODUCED_SENTENCE)
        parts.append(_chain_stop_sentence(execution_result))
        return " ".join(parts)

    @staticmethod
    def _nothing_confirmed_message(execution_result: ExecutionResult | None) -> str:
        """Compose the reply when nothing was delivered and Inspector didn't run.

        Never says the work was done: with no verification and no file
        handed back — e.g. only read-only steps ran — nothing the user
        asked for can be claimed.
        """
        if execution_result is None:
            return "I wasn't able to find anything to do with that request."
        if execution_result.errors:
            errors = "; ".join(_plain_words(e).rstrip(".") for e in execution_result.errors)
            return f"I ran into a problem: {errors}. {NO_CHANGES_SENTENCE}"
        return f"I wasn't able to confirm any result. {NO_CHANGES_SENTENCE}"

    @staticmethod
    def _no_changes_message(
        execution_result: ExecutionResult | None,
        verification_result: VerificationResult,
        report_unavailable: str | None = None,
    ) -> str:
        """Compose the reply for a turn in which no file was changed.

        Never says "Done": nothing the user asked for was carried out (e.g.
        Think asked for clarification, so only read-only analysis ran).
        Still reports what was noticed, and why things failed if they did.
        A chain that stopped is ``_stopped_message``'s job, not this one's.

        Args:
            execution_result: The execution result, if any.
            verification_result: Inspector's verdict for this turn.
            report_unavailable: Why the report Brain read can't be given
                (``report_unavailable_sentence``), or ``None``. It states the failed analysis in
                place of the generic "I wasn't able to confirm that worked".

        Returns:
            The reply text.
        """
        open_findings, other_findings = TemplateSpeaker._partition_analysis_findings(
            verification_result
        )
        parts = []
        if open_findings:
            parts.append(
                "Before anything else, here's what I noticed: "
                + "; ".join(open_findings)
                + ". Want me to tackle any of these?"
            )
        remaining = dataclasses.replace(verification_result, findings=other_findings)
        only_analysis_failed = all(
            v.passed or v.capability == _ANALYZE_CAPABILITY for v in verification_result.steps
        )
        if report_unavailable is not None and only_analysis_failed:
            parts.append(report_unavailable)
        elif not remaining.passed:
            parts.append(TemplateSpeaker._failure_message(remaining))
            if report_unavailable is not None:
                parts.append(report_unavailable)
        elif other_findings:
            joined = "; ".join(_strip_finding_tags(f) for f in other_findings)
            parts.append(joined[0].upper() + joined[1:] + ".")
            if report_unavailable is not None:
                parts.append(report_unavailable)
        parts.append(NO_CHANGES_SENTENCE)
        return " ".join(parts)

    @staticmethod
    def _compose_with_analysis_preface(verification_result: VerificationResult) -> str:
        """Compose a reply that leads with proactive-analysis findings.

        Per ``docs/design/proactive-analysis-design.md`` Section 5.3: this
        is an unprompted observation, not a pass/fail outcome, so it's
        never folded into the ordinary success/failure framing. Findings
        already resolved by another capability's step that ran and passed
        in this same turn (per ``_ANALYSIS_FINDING_FIX_CAPABILITY`` and
        ``verification_result.steps``) are silently dropped, not asked
        about again.

        Only used for a delivered turn Inspector didn't pass: a verified
        delivery is ``_verified_delivery_reply``'s job.

        Args:
            verification_result: Inspector's own verdict, covering every
                step this turn ran, including ``spreadsheet.analyze``.

        Returns:
            The reply text: a leading sentence about open findings (if
            any), followed by the failure message built from every other
            step's own findings.
        """
        open_findings, other_findings = TemplateSpeaker._partition_analysis_findings(
            verification_result
        )
        parts = []
        if open_findings:
            parts.append(
                "Before anything else, here's what I noticed: "
                + "; ".join(open_findings)
                + ". Want me to tackle any of these?"
            )
        remaining_result = dataclasses.replace(verification_result, findings=other_findings)
        parts.append(TemplateSpeaker._failure_message(remaining_result))
        return " ".join(parts)

    @staticmethod
    def _partition_analysis_findings(
        verification_result: VerificationResult,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Split ``verification_result.findings`` into open vs. everything else.

        Args:
            verification_result: The verification result to partition.

        Returns:
            ``(open_findings, other_findings)`` — ``open_findings`` are
            this turn's ``spreadsheet.analyze`` findings not already
            resolved by another passed step this turn, with their
            ``[spreadsheet.analyze] [category]`` prefixes stripped for
            display; ``other_findings`` are every finding that isn't a
            ``spreadsheet.analyze`` finding at all, unchanged.
        """
        resolved_capabilities = {
            step.capability for step in verification_result.steps if step.passed
        }
        open_findings: list[str] = []
        other_findings: list[str] = []
        for finding in _findings_for_reply(verification_result):
            match = _ANALYSIS_FINDING_PATTERN.match(finding)
            if match is None:
                other_findings.append(finding)
                continue
            fix_capability = _ANALYSIS_FINDING_FIX_CAPABILITY.get(match.group("category"))
            if fix_capability is not None and fix_capability in resolved_capabilities:
                continue  # already handled this turn -- don't ask about it again
            open_findings.append(match.group("text"))
        return tuple(open_findings), tuple(other_findings)

    @staticmethod
    def _failure_message(verification_result: VerificationResult) -> str:
        """Compose the reply for a failed (or inconclusive) verification.

        Covers every step's findings, not just the first: one turn may
        cover several delegated steps, each independently verified (see
        ``buddy.inspector.CompositeVerifier``).

        Args:
            verification_result: The verification result, covering every
                step this turn ran.

        Returns:
            The reply text.
        """
        parts = ["I wasn't able to confirm that worked."]
        if verification_result.findings:
            joined = "; ".join(_strip_finding_tags(f) for f in verification_result.findings)
            parts.append(joined[0].upper() + joined[1:] + ".")
        if verification_result.recommendations:
            parts.append(" ".join(verification_result.recommendations))
        return " ".join(parts)

class Voice:
    """The Voice stage of Buddy's cognitive cycle.

    Voice converts the verified outcome into the message the user
    actually sees, written to ``BuddyState.reply`` (see
    ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4). Voice is the only stage
    that produces user-facing language.

    Voice contains no reply-composition logic of its own. All of the
    actual wording is delegated to an injected ``Speaker``. Voice's own
    responsibility is purely mechanical: assemble the speaker's input from
    ``BuddyState``, call the speaker, and record what it returns. This
    keeps Voice a stable, swappable framework — a templated speaker, an
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
        if state.failure is not None:
            # A stage raised (``core.engine.Engine.run``). The reply is fixed
            # text chosen by code; no speaker, and so no model, is called.
            self._apply(state, failure_reply(state.failure.stage))
            logger.info(
                "Voice recorded the failure reply for session %r (stage %r raised %s)",
                state.session_id,
                state.failure.stage,
                state.failure.error_type,
            )
            return state
        request = self._build_request(state)
        reply = self._speaker(request)
        self._apply(state, reply)
        logger.debug("Voice recorded a reply for session %r", state.session_id)
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
            mode=state.context.mode,
            order_confirmed=all(step.order_confirmed for step in state.planning or ()),
            session_id=state.session_id,
            turn_index=state.context.buddy_reply_count(),
            failure=state.failure,
            file_used=state.file_used,
            user_message=state.user_message or "",
            reasoning=state.reasoning,
            has_file=bool(state.uploaded_files or state.context.active_file),
            file_refusal=state.file_refusal,
            report_requested=asks_for_report(state.understanding or Understanding()),
            resumed=state.resumed_request,
            recent_conversation=tuple(state.context.recent_conversation()),
        )

    @staticmethod
    def _apply(state: BuddyState, reply: str | Sequence[str]) -> None:
        """Record the speaker's output onto ``state``.

        A single text is one message. Separate messages are kept as they are in
        ``state.reply_messages``, and ``state.reply`` holds them one per line.

        Args:
            state: The state to update.
            reply: The reply text, or its messages.
        """
        messages = (reply,) if isinstance(reply, str) else tuple(reply)
        state.reply_messages = messages
        state.reply = "\n".join(messages)


def is_verified_delivery(state: BuddyState) -> bool:
    """Whether the reply for ``state``'s turn says a verified file was handed back.

    The same predicate the speakers use (``_verified_delivery``), on the
    request Voice builds from ``state``. A caller that hands files to the
    user (the backend API) checks it, so what it hands back can never
    disagree with what the reply says. False whenever a stage raised.
    """
    return _verified_delivery(Voice._build_request(state))


def asked_which_file(state: BuddyState) -> bool:
    """Whether the reply for ``state``'s turn is the question "which file?".

    The same order as ``TemplateSpeaker._no_change_reply``: a greeting, no file, or a file the
    skills refuse each come before the question, and a failed stage gives no question. A caller
    that remembers the request the question was about (the backend API) uses this, so it never
    remembers one the reply didn't ask about.
    """
    reasoning = state.reasoning
    if (
        state.failure is not None
        or reasoning is None
        or not reasoning.clarification_required
        or reasoning.clarification_reason != CLARIFY_WHICH_FILE
    ):
        return False
    request = Voice._build_request(state)
    return (
        request.has_file
        and request.file_refusal is None
        and not is_conversation(request.understanding, request.user_message)
    )


class PersonalityVoice:
    """A ``Speaker`` with a selectable personality: Friend or Focus mode.

    The template reply: what the app shows when the reply writer (``buddy.writer.NaturalVoice``,
    the app's speaker since 2026-10-23) can't word a turn, or its drafts fail the claim check.
    No model writes any of it (``docs/design/verified-outcome-design.md``, decision D1):

    - On a turn that delivered a verified file, the reply is rendered by
      code: the outcome block from execution and verifier data, with the
      file named as a cleaned copy of the original, and an opener and
      closer for ``request.mode`` chosen by code from the reviewed phrase
      catalog (``buddy.voice_catalog``).
    - Every other turn gets ``TemplateSpeaker``'s factual reply, with the greeting and
      the clarification question worded for the mode from the same catalog.

    No turn calls the model: a greeting is catalog lines chosen by code too. The provider
    is kept only so callers that construct this speaker keep working.

    When Brain read the person as urgent or frustrated (``Understanding.user_state``), every
    reply is worded in the brief mode (``buddy.voice_catalog.effective_mode``) whatever mode they
    picked. That swaps only the opener, the closer and the fixed greeting and question wording:
    every fact, count, disclosure and "I worked on ..." sentence is the calm reply's.
    """

    def __init__(self, provider: LLMProvider | None = None) -> None:
        """Initialize the speaker.

        Args:
            provider: Not called by any turn.
        """
        self._provider = provider

    def __call__(self, request: SpeakingRequest) -> str:
        """Compose a reply for ``request`` in the requested personality mode.

        Args:
            request: The request to compose a reply for.

        Returns:
            The rendered reply for a verified delivery; otherwise
            ``TemplateSpeaker``'s factual reply.
        """
        # An urgent or frustrated person gets the brief wording whatever mode they picked: tone
        # only, so every fact below is the one the calm reply gives.
        mode = effective_mode(request.mode, request.understanding.user_state)
        if not _verified_delivery(request):
            logger.info(
                "PersonalityVoice: no verified file was delivered this turn; "
                "using the factual reply"
            )
            return TemplateSpeaker().compose(request, style=style_for(mode))
        return _verified_delivery_reply(request, file_style=FILE_STYLE_PLAIN, mode=mode)
